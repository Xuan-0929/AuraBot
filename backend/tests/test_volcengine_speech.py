"""Exercise Volcengine wire contracts through production speech providers without cloud accounts.

Fake WebSocket frames and httpx streams verify authentication, audio framing, partial/final delivery,
failure handling and cleanup. The real adapters, callback workers and bounded PCM spool remain active.
"""
import asyncio
import base64
import gzip
import json
import queue
import struct
import unittest
from threading import Event
from unittest.mock import patch

import httpx

from backend.main_agent import volcengine_speech as wire
from backend.main_agent.speech import (
    VolcengineSpeechOutputProvider, VolcengineStreamingSttProvider, _normalize_volcengine_event,
)


def response(body, flags=1, compressed=True):
    payload = json.dumps(body).encode()
    if compressed:
        payload = gzip.compress(payload)
    return bytes((0x11, 0x90 | flags, 0x11 if compressed else 0x10, 0)) + (
        struct.pack('>i', -1 if flags & 2 else 1) if flags & 1 else b''
    ) + struct.pack('>I', len(payload)) + payload


class Socket:
    def __init__(self):
        self.sent, self.incoming, self.closed = [], queue.Queue(), False

    def send(self, packet):
        self.sent.append(packet)
        if packet[1] & 2:
            self.incoming.put(response({'result': {'text': '最终结果'}}, flags=3))

    def recv(self, timeout):
        packet = self.incoming.get(timeout=timeout)
        if packet is None:
            raise ConnectionError('private server detail')
        return packet

    def close(self):
        self.closed = True
        self.incoming.put(None)


class VolcengineProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stt_cancel_during_connect_closes_late_socket(self):
        entered, release, socket = Event(), Event(), Socket()

        def connect(*_, **__):
            entered.set()
            release.wait(2)
            return socket

        with patch.object(wire, 'connect', side_effect=connect):
            starting = asyncio.create_task(VolcengineStreamingSttProvider('resource', api_key='key').start(
                lambda _: asyncio.sleep(0)))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                starting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await starting
                release.set()
                for _ in range(100):
                    if socket.closed:
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(socket.closed)
                self.assertEqual(socket.sent, [])
            finally:
                release.set()

    async def test_real_stt_adapter_batches_pcm_and_drains_final_text(self):
        socket, updates, got_partial = Socket(), [], asyncio.Event()

        async def sink(update):
            updates.append(update)
            got_partial.set()

        with patch.object(wire, 'connect', return_value=socket) as connect:
            session = await VolcengineStreamingSttProvider('volc.seedasr.sauc.duration', api_key='private-key').start(sink)
            try:
                self.assertEqual(connect.call_args.args, (wire.ASR_URL,))
                headers = connect.call_args.kwargs['additional_headers']
                self.assertEqual(headers['X-Api-Key'], 'private-key')
                self.assertEqual(headers['X-Api-Resource-Id'], 'volc.seedasr.sauc.duration')
                request = json.loads(gzip.decompress(socket.sent[0][8:]))
                self.assertEqual(request['audio']['rate'], 16000)
                self.assertEqual(request['request']['result_type'], 'full')
                for _ in range(6):
                    await session.send_audio(b'\x01\x02' * 320)
                self.assertEqual(len(socket.sent), 2)
                self.assertEqual(gzip.decompress(socket.sent[1][8:]), b'\x01\x02' * 1600)
                socket.incoming.put(response({'result': {'text': '临时'}}))
                await asyncio.wait_for(got_partial.wait(), 1)
                self.assertEqual(updates[0].mutable_suffix, '临时')
                await session.finish()
                self.assertEqual(updates[-1].stable_prefix, '最终结果')
                self.assertEqual(socket.sent[-1][1], 0x22)
                self.assertEqual(gzip.decompress(socket.sent[-1][8:]), b'\x01\x02' * 320)
                self.assertTrue(socket.closed)
            finally:
                await session.close()

    async def test_stt_provider_error_fails_finish_instead_of_accepting_partial(self):
        socket = Socket()
        with patch.object(wire, 'connect', return_value=socket):
            session = await VolcengineStreamingSttProvider('resource', api_key='key').start(lambda _: asyncio.sleep(0))
            socket.incoming.put(bytes.fromhex('11f01000') + struct.pack('>II', 45000000, 0))
            with self.assertRaises(RuntimeError):
                await session.finish()
            self.assertTrue(socket.closed)

    def test_final_flags_lengths_compression_and_definite_prefix(self):
        for flags in (0, 1, 2, 3):
            for compressed in (False, True):
                self.assertEqual(wire._asr_response(response({'result': {'text': '你好'}}, flags, compressed))['final'], bool(flags & 2))
        update = _normalize_volcengine_event({'result': {'text': '你好世界', 'utterances': [
            {'text': '你好', 'definite': True}, {'text': '世界', 'definite': False}]}})
        self.assertEqual((update.stable_prefix, update.mutable_suffix), ('你好', '世界'))
        for packet in (b'', response({})[:-1], response({}) + b'x', bytes.fromhex('1100100000000000')):
            with self.assertRaises(RuntimeError):
                wire._asr_response(packet)
        with patch.object(wire, '_MAX_RESPONSE_BYTES', 200), self.assertRaises(RuntimeError):
            wire._asr_response(response({'result': {'text': 'a' * 1000}}))

    async def test_tts_stream_uses_request_local_key_and_pcm_and_rejects_truncation(self):
        real_client = httpx.Client
        requests = []

        def run(events):
            def handle(request):
                requests.append(request)
                return httpx.Response(200, content='\n'.join(json.dumps(event) for event in events))
            return real_client(transport=httpx.MockTransport(handle))

        audio = {'code': 0, 'data': base64.b64encode(b'\x01\x02' * 40).decode()}
        for events, succeeds in (([audio, {'code': 20000000}], True), ([audio], False),
                                 ([{'code': 45000000, 'message': 'private-detail'}], False),
                                 ([{'code': 20000000}], False)):
            with patch.object(wire.httpx, 'Client', side_effect=lambda **_: run(events)):
                stream = VolcengineSpeechOutputProvider('seed-tts-2.0', voice='zh_female_vv_uranus_bigtts', api_key='speech-key').synthesize('测试')
                if succeeds:
                    self.assertEqual(b''.join([chunk async for chunk in stream]), b'\x01\x02' * 40)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Volcengine TTS streaming failed'):
                        _ = [chunk async for chunk in stream]
        request = requests[0]
        self.assertEqual(str(request.url), wire.TTS_URL)
        self.assertEqual(request.headers['X-Api-Key'], 'speech-key')
        self.assertEqual(request.headers['X-Api-Resource-Id'], 'seed-tts-2.0')
        self.assertEqual(json.loads(request.content)['req_params']['audio_params'], {'format': 'pcm', 'sample_rate': 24000})

    async def test_tts_cancellation_closes_active_http_stream(self):
        released = Event()

        class AudioStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"code":0,"data":"AQI="}\n'
                released.wait(2)
                yield b'{"code":20000000}\n'

            def close(self):
                released.set()

        client = httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=AudioStream())))
        with patch.object(wire.httpx, 'Client', return_value=client):
            stream = VolcengineSpeechOutputProvider('seed-tts-2.0', voice='voice', api_key='key').synthesize('测试')
            try:
                self.assertEqual(await asyncio.wait_for(anext(stream), 1), b'\x01\x02')
                self.assertFalse(released.is_set())
                await asyncio.wait_for(stream.aclose(), 1)
                self.assertTrue(released.is_set())
            finally:
                released.set()
                await stream.aclose()
