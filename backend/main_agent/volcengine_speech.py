"""Connect Pipeline speech to Volcengine ASR WebSocket and streaming HTTP TTS.

Speech's existing worker adapters own these synchronous transports and cancellation. ASR batches
16 kHz PCM into 100 ms packets, normalizes binary responses and waits for the final response before
stopping; TTS decodes 24 kHz PCM into the bounded speech spool. Credentials are request-local.
Only request identities, byte counts, timings and fixed failure codes enter speech diagnostics.
No provider response text, credentials or audio is logged. Realtime uses its own SeedDuplex adapter.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import struct
import time
import uuid
from threading import Event, Thread
from typing import Any

import httpx
from websockets.sync.client import connect

from .metrics import speech_logger

ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
_MAX_RESPONSE_BYTES = 1_048_576


def _headers(api_key: str, resource: str, request_id: str) -> dict[str, str]:
    return {"X-Api-Key": api_key, "X-Api-Resource-Id": resource,
            "X-Api-Request-Id": request_id, "X-Api-Connect-Id": request_id}


def _asr_packet(payload: bytes, *, audio: bool = False, final: bool = False) -> bytes:
    payload = gzip.compress(payload)
    return bytes((0x11, (0x20 if audio else 0x10) | (2 if final else 0),
                  0x01 if audio else 0x11, 0)) + struct.pack(">I", len(payload)) + payload


def _asr_response(packet: bytes) -> dict[str, Any]:
    """Validate framing and bounded gzip data before exposing one full transcript snapshot."""
    if not isinstance(packet, bytes) or len(packet) < 8 or packet[0] >> 4 != 1:
        raise RuntimeError("volcengine_stt_protocol_error")
    offset = (packet[0] & 15) * 4
    kind, flags = packet[1] >> 4, packet[1] & 15
    if offset < 4 or kind not in (9, 15) or flags not in (0, 1, 2, 3):
        raise RuntimeError("volcengine_stt_protocol_error")
    if kind == 15:
        raise RuntimeError("volcengine_stt_provider_error")
    if flags & 1:
        offset += 4
    if len(packet) < offset + 4 or packet[2] >> 4 != 1 or packet[2] & 15 not in (0, 1):
        raise RuntimeError("volcengine_stt_protocol_error")
    size = struct.unpack_from(">I", packet, offset)[0]
    payload = packet[offset + 4:]
    if size != len(payload) or size > _MAX_RESPONSE_BYTES:
        raise RuntimeError("volcengine_stt_protocol_error")
    if packet[2] & 15:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            payload = compressed.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("volcengine_stt_protocol_error")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise RuntimeError("volcengine_stt_protocol_error")
    if result.get("code", 0) not in (0, 1000, 20000000):
        raise RuntimeError("volcengine_stt_provider_error")
    return {**result, "final": bool(flags & 2)}


class VolcengineSttTransport:
    def __init__(self, *, model_name: str, sample_rate_hz: int, on_event: Any, api_key: str = ""):
        self.resource, self.rate, self.on_event = model_name, sample_rate_hz, on_event
        self.headers = _headers(api_key, model_name, str(uuid.uuid4()))
        self.socket: Any = None
        self.reader: Thread | None = None
        self.done = Event()
        self.stopping = Event()
        self.failure = False
        self.buffer = bytearray()
        self.sent_bytes = 0

    def start(self) -> None:
        self.socket = connect(ASR_URL, additional_headers=self.headers, open_timeout=10,
                              close_timeout=.2, max_size=_MAX_RESPONSE_BYTES)
        # A cancelled to_thread caller cannot interrupt connect; close a late connection here.
        if self.stopping.is_set():
            self.socket.close()
            raise RuntimeError("volcengine_stt_cancelled")
        request = {"user": {"uid": "aurabot"},
                   "audio": {"format": "pcm", "codec": "raw", "rate": self.rate, "bits": 16, "channel": 1},
                   "request": {"model_name": "bigmodel", "result_type": "full", "show_utterances": True}}
        self.socket.send(_asr_packet(json.dumps(request).encode()))
        self.reader = Thread(target=self._receive, name="volcengine-asr", daemon=True)
        self.reader.start()
        speech_logger.info("volcengine_stt_open request_id=%s", self.headers["X-Api-Request-Id"])

    def _receive(self) -> None:
        try:
            while True:
                event = _asr_response(self.socket.recv(timeout=30))
                self.on_event(event)
                if event["final"]:
                    return
        except Exception:
            self.failure = True
            speech_logger.warning("volcengine_stt_receive_failed")
        finally:
            self.done.set()

    def send_audio_frame(self, pcm: bytes) -> None:
        if self.done.is_set():
            raise RuntimeError("volcengine_stt_closed")
        self.buffer.extend(pcm)
        while len(self.buffer) >= 3200:
            self.socket.send(_asr_packet(bytes(self.buffer[:3200]), audio=True))
            del self.buffer[:3200]
            self.sent_bytes += 3200

    def stop(self) -> None:
        self.stopping.set()
        if self.socket is None:
            return
        started = time.monotonic()
        try:
            if not self.done.is_set():
                self.socket.send(_asr_packet(bytes(self.buffer), audio=True, final=True))
                self.sent_bytes += len(self.buffer)
                self.buffer.clear()
            if not self.done.wait(.8) or self.failure:
                raise RuntimeError("volcengine_stt_finalization_failed")
        finally:
            self.socket.close()
            if self.reader is not None:
                self.reader.join(.3)
            speech_logger.info("volcengine_stt_closed bytes=%d elapsed_ms=%.1f",
                               self.sent_bytes, (time.monotonic() - started) * 1000)


class VolcengineTtsTransport:
    def __init__(self, *, model_name: str, voice: str, api_key: str = ""):
        self.headers = _headers(api_key, model_name, str(uuid.uuid4()))
        self.voice = voice
        self.cancelled = Event()
        self.response: httpx.Response | None = None

    def stream(self, text: str, on_data: Any, on_complete: Any, on_error: Any) -> None:
        if self.cancelled.is_set():
            return
        started, total = time.monotonic(), 0
        request = {"user": {"uid": "aurabot"}, "req_params": {"text": text, "speaker": self.voice,
                   "audio_params": {"format": "pcm", "sample_rate": 24000}}}
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=10)) as client:
                with client.stream("POST", TTS_URL, headers=self.headers, json=request) as response:
                    self.response = response
                    if self.cancelled.is_set():
                        return
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if self.cancelled.is_set():
                            return
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        code = event.get("code")
                        if code == 20000000:
                            if not total:
                                raise RuntimeError("volcengine_tts_audio_missing")
                            on_complete()
                            return
                        if code != 0:
                            raise RuntimeError("volcengine_tts_provider_error")
                        if event.get("data"):
                            pcm = base64.b64decode(event["data"], validate=True)
                            if len(pcm) % 2:
                                raise RuntimeError("volcengine_tts_invalid_pcm")
                            if not total:
                                speech_logger.info("volcengine_tts_first_audio elapsed_ms=%.1f", (time.monotonic() - started) * 1000)
                            total += len(pcm)
                            on_data(pcm)
                    raise RuntimeError("volcengine_tts_incomplete")
        except Exception:
            if not self.cancelled.is_set():
                speech_logger.warning("volcengine_tts_failed")
                on_error(RuntimeError("volcengine_tts_failed"))
        finally:
            self.response = None
            speech_logger.info("volcengine_tts_closed bytes=%d cancelled=%s elapsed_ms=%.1f",
                               total, self.cancelled.is_set(), (time.monotonic() - started) * 1000)

    def cancel(self) -> None:
        self.cancelled.set()
        response = self.response
        if response is not None:
            response.close()
