"""Own safe Observation facts and the concrete web/workspace read-only executor boundaries.

ObservationLedger keeps append-only in-memory started/completed/failed facts and folds them by stable
observation identity.  TavilySearchExecutor registers provider URLs in SourceRegistry; SafeWebReader
then performs pinned-address, manually redirected public-web reads.  WorkspaceReader walks only a
configured WorkspaceRegistry through no-follow descriptors, and its tiny executor wrappers connect
these bounded reads to ToolHarness without changing scheduling or ResponseGrant semantics.
"""

from __future__ import annotations

import asyncio
import codecs
import concurrent.futures
import errno
import hashlib
import html
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from typing import Any

from .settings import WorkspaceRegistry, WorkspaceRegistryError
from .tool_runtime.definitions import ToolCall


_MAX_WEB_BYTES = 1024 * 1024
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_WORKSPACE_ITEMS = 200
_MAX_WORKSPACE_LINES = 200
_MAX_WORKSPACE_SCAN = 1_000
_MAX_WORKSPACE_FILE_BYTES = 1024 * 1024
_MAX_START_LINE = 10_000
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SECRET_NAMES = frozenset({
    ".git", ".ssh", ".aws", ".azure", ".docker", ".kube", ".gnupg",
    ".netrc", ".npmrc", ".pypirc", ".terraform.d", "gcloud", "keyrings", "keychains",
    "credentials", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "secrets", "secrets.json",
    "auth.json", "service-account.json", "service-account-key.json",
})
_SECRET_SUFFIXES = (
    ".key", ".pem", ".p12", ".pfx", ".kdbx", ".jks", ".keystore",
)
_BLOCKING_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="aurabot-observation",
)
_WORKER_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=3, thread_name_prefix="aurabot-observation-job",
)


class _ExpiredBeforeStart(Exception):
    """Private worker marker proving the guarded operation body was never invoked."""


def _discard_worker_result(
    wrapped: asyncio.Future[Any], cleanup: Callable[[Any], None],
) -> None:
    """Consume and close a rejected completed result without surfacing worker exceptions."""

    if not wrapped.done() or wrapped.cancelled():
        return
    try:
        result = wrapped.result()
    except BaseException:
        return
    try:
        cleanup(result)
    except BaseException:
        pass


async def _run_deadline_worker(
    operation: Callable[..., Any], *args: Any, timeout: float,
    executor: concurrent.futures.Executor = _WORKER_POOL,
    late_cleanup: Callable[[Any], None] | None = None,
) -> Any:
    """Cancel queued work at deadline, but retain its caller until any started worker really exits."""

    deadline = time.monotonic() + timeout

    def guarded() -> Any:
        if time.monotonic() >= deadline:
            raise _ExpiredBeforeStart
        return operation(*args, deadline)

    worker = executor.submit(guarded)
    wrapped = asyncio.wrap_future(worker)
    loop = asyncio.get_running_loop()
    cleanup = late_cleanup or _close_quietly
    expired = loop.create_future()
    timer = loop.call_later(
        max(0.0, deadline - time.monotonic()), expired.set_result, None,
    )
    try:
        done, _ = await asyncio.wait((wrapped, expired), return_when=asyncio.FIRST_COMPLETED)
        caller = asyncio.current_task()
        if caller is not None and caller.cancelling():
            raise asyncio.CancelledError
        if time.monotonic() < deadline and wrapped in done:
            try:
                result = wrapped.result()
            except _ExpiredBeforeStart:
                raise TimeoutError("Observation worker timed out") from None
            if time.monotonic() < deadline:
                return result
        if worker.cancel():
            raise TimeoutError("Observation worker timed out")
        cancelled = False
        while not wrapped.done():
            try:
                await asyncio.shield(wrapped)
            except asyncio.CancelledError:
                cancelled = True
                continue
            except BaseException:
                break
        if cancelled:
            raise asyncio.CancelledError
        _discard_worker_result(wrapped, cleanup)
        raise TimeoutError("Observation worker timed out")
    except asyncio.CancelledError:
        if worker.cancel():
            raise
        while not wrapped.done():
            try:
                await asyncio.shield(wrapped)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        _discard_worker_result(wrapped, cleanup)
        raise
    finally:
        timer.cancel()
        if not expired.done():
            expired.cancel()


def _remaining(deadline: float, message: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(message)
    return remaining


def _deadline_call(
    submitter: Callable[..., concurrent.futures.Future[Any]],
    deadline: float,
    message: str,
    operation: Callable[..., Any],
    *args: Any,
    late_cleanup: Callable[[Any], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Bound one blocking phase to a shared monotonic deadline and ignore late completion."""

    timeout = _remaining(deadline, message)
    future = submitter(operation, *args, **kwargs)
    try:
        result = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        callback = getattr(future, "add_done_callback", None)
        if late_cleanup is not None and callback is not None:
            def clean_late(done: concurrent.futures.Future[Any]) -> None:
                try:
                    late_cleanup(done.result())
                except BaseException:
                    pass

            try:
                callback(clean_late)
            except BaseException:
                pass
        cancel = getattr(future, "cancel", None)
        if cancel is not None:
            try:
                cancel()
            except BaseException:
                pass
        raise TimeoutError(message) from None
    try:
        _remaining(deadline, message)
    except TimeoutError:
        if late_cleanup is not None:
            try:
                late_cleanup(result)
            except BaseException:
                pass
        raise
    return result


def _read_bounded(
    response: Any,
    limit: int,
    *,
    deadline: float,
    timeout_message: str,
    submitter: Callable[..., concurrent.futures.Future[Any]],
) -> bytes:
    """Read bounded chunks while refreshing transport and worker timeouts from one deadline."""

    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        remaining = _remaining(deadline, timeout_message)
        setter = getattr(response, "set_timeout", None)
        if setter is not None:
            setter(remaining)
        chunk = _deadline_call(
            submitter, deadline, timeout_message, response.read,
            min(64 * 1024, limit + 1 - total),
        )
        if not isinstance(chunk, bytes):
            raise ValueError("response body must be bytes")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class ObservationSecurityError(ValueError):
    """A stable, secret-free rejection at a web or workspace trust boundary."""


class ObservationExecutorError(RuntimeError):
    """A stable, secret-free failure of an external Observation provider."""


def _json_value(value: Any) -> Any:
    """Copy JSON-safe executor facts without retaining caller-owned mutable objects."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Observation facts must be JSON-safe") from exc


def _canonical_arguments(arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    copied = _json_value(dict(arguments))
    if not isinstance(copied, dict):
        raise ValueError("Observation arguments must be an object")
    return json.dumps(
        copied, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ), copied


class ObservationLedger:
    """Thread-safe append-only facts with same-input request reuse and terminal idempotency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._latest: dict[str, dict[str, Any]] = {}
        self._dedupe: dict[tuple[str, str, str], str] = {}

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """Return defensive copies in append order for diagnostics and shutdown reporting."""

        with self._lock:
            return tuple(_json_value(event) for event in self._events)

    def start(
        self,
        observation_id: str,
        *,
        input_id: str,
        revision: int,
        kind: str,
        arguments: Mapping[str, Any],
    ) -> tuple[str, bool]:
        """Append one running fact or return the same-input canonical request already accepted."""

        if not all(isinstance(value, str) and value for value in (observation_id, input_id, kind)):
            raise ValueError("Observation identity, input, and kind are required")
        if type(revision) is not int or revision < 0:
            raise ValueError("Observation revision must be a non-negative integer")
        canonical, copied_arguments = _canonical_arguments(arguments)
        key = (input_id, kind, canonical)
        with self._lock:
            if existing := self._dedupe.get(key):
                return existing, False
            if observation_id in self._latest:
                raise ValueError("Observation identity is already in use")
            event = {
                "id": observation_id,
                "kind": kind,
                "request": copied_arguments,
                "status": "running",
                "origin_input_id": input_id,
                "origin_revision": revision,
            }
            self._events.append(event)
            self._latest[observation_id] = event
            self._dedupe[key] = observation_id
            return observation_id, True

    def complete(self, observation_id: str, result: Any) -> bool:
        """Append the first completed fact; duplicate or conflicting terminal delivery is ignored."""

        _, owned = self.settle(observation_id, "completed", result)
        return owned

    def fail(self, observation_id: str, error_type: str) -> bool:
        """Append the first failed fact while retaining only a class name, never exception text."""

        _, owned = self.settle(observation_id, "failed", error_type)
        return owned

    def settle(
        self, observation_id: str, status: str, payload: Any,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Atomically append or recover one canonical terminal fact and transition ownership."""

        if status not in {"completed", "failed"}:
            raise ValueError("Observation terminal status must be completed or failed")
        with self._lock:
            current = self._latest.get(observation_id)
            if current is None:
                return None, False
            if current["status"] != "running":
                return _json_value(current), False
            if status == "completed":
                terminal = {"result": _json_value(payload)}
            else:
                safe_type = (
                    payload
                    if isinstance(payload, str) and payload.isidentifier()
                    else "ExecutorError"
                )
                terminal = {"error_type": safe_type[:128]}
            event = {**current, "status": status, **terminal}
            returned = _json_value(event)
            self._events.append(event)
            self._latest[observation_id] = event
            return returned, True

    def terminal_fact(self, observation_id: str) -> dict[str, Any] | None:
        """Return the canonical terminal fact, or None while unknown/running, for fault recovery."""

        with self._lock:
            current = self._latest.get(observation_id)
            if current is None or current["status"] == "running":
                return None
            return _json_value(current)

    def fold(self) -> tuple[dict[str, Any], ...]:
        """Return one deterministic latest state per accepted observation."""

        with self._lock:
            return tuple(_json_value(self._latest[key]) for key in sorted(self._latest))


def _validated_web_url(raw_url: object) -> str:
    if not isinstance(raw_url, str) or not raw_url:
        raise ObservationSecurityError("web source URL is required")
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise ObservationSecurityError("web source URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ObservationSecurityError("only HTTP and HTTPS web sources are allowed")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ObservationSecurityError("web source URL authority is invalid")
    if port is not None and not 1 <= port <= 65535:
        raise ObservationSecurityError("web source URL port is invalid")
    host = parsed.hostname.lower()
    if ":" in host:
        authority = f"[{host}]"
    else:
        authority = host
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    if port is not None and port != default_port:
        authority += f":{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), authority, path, parsed.query, ""))


class SourceRegistry:
    """Map provider-discovered public-web candidates to opaque stable ids used by web_read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, str] = {}
        self._by_url: dict[str, str] = {}

    def register(self, url: object) -> str:
        normalized = _validated_web_url(url)
        with self._lock:
            if existing := self._by_url.get(normalized):
                return existing
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            length = 16
            source_id = f"source-{digest[:length]}"
            while source_id in self._by_id and self._by_id[source_id] != normalized:
                length += 4
                source_id = f"source-{digest[:length]}"
            self._by_id[source_id] = normalized
            self._by_url[normalized] = source_id
            return source_id

    def require(self, source_id: object) -> str:
        if not isinstance(source_id, str) or not source_id:
            raise ObservationSecurityError("source_id is required")
        with self._lock:
            try:
                return self._by_id[source_id]
            except KeyError as exc:
                raise ObservationSecurityError("source_id is unknown") from exc


class TavilySearchExecutor:
    """Issue exactly one fixed-shape Tavily REST request and register its bounded result URLs."""

    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        sources: SourceRegistry,
        *,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        connection_factory: Callable[..., Any] | None = None,
        submitter: Callable[..., concurrent.futures.Future[Any]] = _BLOCKING_POOL.submit,
        worker_executor: concurrent.futures.Executor = _WORKER_POOL,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Tavily API key is required")
        self._api_key = api_key
        self._sources = sources
        self._resolver = resolver
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._submitter = submitter
        self._worker_executor = worker_executor

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        query = call.arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("search query is required")
        try:
            return await _run_deadline_worker(
                self._search, query, timeout=10.0, executor=self._worker_executor,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ObservationExecutorError("web search provider failed") from None

    def _search(self, query: str, deadline: float | None = None) -> dict[str, Any]:
        if deadline is None:
            deadline = time.monotonic() + 10.0
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        connection = None
        response = None
        try:
            records = _deadline_call(
                self._submitter,
                deadline,
                "web search provider timed out",
                self._resolver,
                "api.tavily.com",
                443,
                type=socket.SOCK_STREAM,
            )
            addresses = _validated_public_addresses(records)
            timeout = _remaining(deadline, "web search provider timed out")
            connection = self._connection_factory(
                "api.tavily.com", addresses[0], 443, timeout,
            )
            _deadline_call(
                self._submitter, deadline, "web search provider timed out",
                connection.connect,
                late_cleanup=lambda _result: _close_quietly(connection),
            )
            _set_connection_timeout(connection, deadline, "web search provider timed out")
            _deadline_call(
                self._submitter, deadline, "web search provider timed out",
                connection.request, "POST", "/search", body=body, headers={
                    "Host": "api.tavily.com",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
                late_cleanup=lambda _result: _close_quietly(connection),
            )
            _set_connection_timeout(connection, deadline, "web search provider timed out")
            response = _deadline_call(
                self._submitter, deadline, "web search provider timed out",
                connection.getresponse,
                late_cleanup=lambda late: (
                    _close_quietly(late), _close_quietly(connection),
                ),
            )
            if not 200 <= response.status < 300:
                raise ValueError("provider response status is unsuccessful")
            wrapped = _ConnectionResponse(connection, response)
            try:
                raw = _read_bounded(
                    wrapped, _MAX_WEB_BYTES,
                    deadline=deadline,
                    timeout_message="web search provider timed out",
                    submitter=self._submitter,
                )
            finally:
                wrapped.close()
                connection = None
                response = None
            if len(raw) > _MAX_WEB_BYTES:
                raise ValueError("provider response too large")
            document = json.loads(raw)
            raw_results = document.get("results", [])
            if not isinstance(raw_results, list):
                raise ValueError("provider results are invalid")
            results = []
            for item in raw_results[:5]:
                if not isinstance(item, Mapping):
                    continue
                try:
                    source_id = self._sources.register(item.get("url"))
                except ObservationSecurityError:
                    continue
                result = {
                    "source_id": source_id,
                    "title": str(item.get("title", ""))[:500],
                    "url": self._sources.require(source_id),
                    "snippet": str(item.get("content", ""))[:4_000],
                }
                score = item.get("score")
                if isinstance(score, (int, float)) and not isinstance(score, bool):
                    result["score"] = float(score)
                results.append(result)
            return {"query": query, "results": results}
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ObservationExecutorError("web search provider failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except BaseException:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    pass


class _HTMLText(HTMLParser):
    """Extract visible HTML text while dropping script/style/noscript bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._suppressed += 1
        elif not self._suppressed and tag.lower() in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._suppressed:
            self._suppressed -= 1
        elif not self._suppressed and tag.lower() in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(data)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Use a validated address for transport while preserving hostname SNI/certificate checks."""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._validated_address, self.port), self.timeout, self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _validated_public_addresses(records: Any) -> tuple[str, ...]:
    """Extract unique addresses and reject a DNS answer containing any non-public target."""

    try:
        addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ObservationExecutorError("DNS resolution failed") from None
    if not addresses:
        raise ObservationExecutorError("DNS resolution failed")
    try:
        parsed = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError as exc:
        raise ObservationSecurityError("DNS returned an invalid address") from exc
    if any(not address.is_global for address in parsed):
        raise ObservationSecurityError("DNS must resolve only to public addresses")
    return addresses


def _set_connection_timeout(connection: Any, deadline: float, message: str) -> None:
    """Refresh a live transport socket from the remaining end-to-end deadline when available."""

    remaining = _remaining(deadline, message)
    sock = getattr(connection, "sock", None)
    if sock is not None:
        sock.settimeout(remaining)


def _close_quietly(resource: Any) -> None:
    """Close a late transport result without allowing cleanup errors across the boundary."""

    close = getattr(resource, "close", None)
    if close is not None:
        try:
            close()
        except BaseException:
            pass


class _ConnectionResponse:
    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse) -> None:
        self._connection = connection
        self._response = response
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def set_timeout(self, timeout: float) -> None:
        sock = getattr(self._connection, "sock", None)
        if sock is not None:
            sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def _header(headers: Any, name: str) -> str | None:
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is not None:
        return str(value)
    getter = getattr(headers, "get_all", None)
    if getter is not None:
        values = getter(name)
        if values:
            return str(values[0])
    if isinstance(headers, Mapping):
        wanted = name.casefold()
        for key, item in headers.items():
            if str(key).casefold() == wanted:
                return str(item)
    return None


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    """Collect every physical/repeated header occurrence without comma-folding omissions."""

    getter = getattr(headers, "get_all", None)
    if getter is not None:
        values = getter(name)
        if values is not None:
            return tuple(str(value) for value in values)
    value = _header(headers, name)
    return () if value is None else (value,)


def _truncate_utf8(text: str, maximum: int = _MAX_CONTEXT_BYTES) -> str:
    encoded = text.encode("utf-8")
    return text if len(encoded) <= maximum else encoded[:maximum].decode("utf-8", "ignore")


class SafeWebReader:
    """Read one registered source through validated/pinned DNS and manual redirect handling."""

    def __init__(
        self,
        sources: SourceRegistry,
        *,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        submitter: Callable[..., concurrent.futures.Future[Any]] = _BLOCKING_POOL.submit,
        worker_executor: concurrent.futures.Executor = _WORKER_POOL,
    ) -> None:
        self._sources = sources
        self._resolver = resolver
        self._submitter = submitter
        self._worker_executor = worker_executor

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        return await _run_deadline_worker(
            self.read, call.arguments.get("source_id"), timeout=15.0,
            executor=self._worker_executor,
        )

    def read(self, source_id: object, deadline: float | None = None) -> dict[str, Any]:
        url = self._sources.require(source_id)
        if deadline is None:
            deadline = time.monotonic() + 15.0
        redirects = 0
        while True:
            url = _validated_web_url(url)
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname
            assert host is not None
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            address = self._public_addresses(host, port, deadline)[0]
            _remaining(deadline, "web read timed out")
            try:
                response = _deadline_call(
                    self._submitter, deadline, "web read timed out",
                    self._request, url, address, deadline,
                    late_cleanup=_close_quietly,
                )
                try:
                    if response.status in _REDIRECT_STATUSES:
                        location = _header(response.headers, "Location")
                        if not location:
                            raise ObservationSecurityError("web redirect has no location")
                        if redirects >= 3:
                            raise ObservationSecurityError("web redirect limit exceeded")
                        redirects += 1
                        url = urllib.parse.urljoin(url, location)
                        continue
                    if not 200 <= response.status < 300:
                        raise ObservationExecutorError("web source returned an unsuccessful status")
                    mime = (_header(response.headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
                    if not mime.startswith("text/"):
                        raise ObservationSecurityError("web source must use a text MIME type")
                    encodings = tuple(
                        token.strip().casefold()
                        for value in _header_values(response.headers, "Content-Encoding")
                        for token in value.split(",")
                    )
                    if any(encoding not in {"", "identity"} for encoding in encodings):
                        raise ObservationSecurityError(
                            "web source Content-Encoding must be empty or identity"
                        )
                    length = _header(response.headers, "Content-Length")
                    if length is not None:
                        try:
                            if int(length) > _MAX_WEB_BYTES:
                                raise ObservationSecurityError("web source exceeds 1 MiB")
                        except ValueError as exc:
                            raise ObservationSecurityError("web source length is invalid") from exc
                    body = _read_bounded(
                        response, _MAX_WEB_BYTES,
                        deadline=deadline,
                        timeout_message="web read timed out",
                        submitter=self._submitter,
                    )
                    if len(body) > _MAX_WEB_BYTES:
                        raise ObservationSecurityError("web source exceeds 1 MiB")
                finally:
                    response.close()
            except (ObservationSecurityError, ObservationExecutorError, TimeoutError):
                raise
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ObservationExecutorError("web source read failed") from None
            content = self._normalize(body, _header(response.headers, "Content-Type") or "", mime)
            return {"source_id": source_id, "url": url, "content": content}

    def _public_addresses(self, host: str, port: int, deadline: float) -> tuple[str, ...]:
        try:
            records = _deadline_call(
                self._submitter, deadline, "web read timed out",
                self._resolver, host, port, type=socket.SOCK_STREAM,
            )
            addresses = _validated_public_addresses(records)
        except TimeoutError:
            raise
        except ObservationSecurityError:
            raise
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ObservationExecutorError("web source DNS resolution failed") from None
        return addresses

    def _request(self, url: str, address: str, deadline: float) -> _ConnectionResponse:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        assert host is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        host_header = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            host_header += f":{parsed.port}"
        timeout = _remaining(deadline, "web read timed out")
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(host, address, port, timeout)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=timeout)
        try:
            connection.connect()
            timeout = _remaining(deadline, "web read timed out")
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            connection.request("GET", target, headers={
                "Host": host_header,
                "Accept": "text/*",
                "Accept-Encoding": "identity",
                "User-Agent": "AuraBot/1",
                "Connection": "close",
            })
            timeout = _remaining(deadline, "web read timed out")
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            response = connection.getresponse()
            _remaining(deadline, "web read timed out")
            return _ConnectionResponse(connection, response)
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _normalize(body: bytes, content_type: str, mime: str) -> str:
        charset = "utf-8"
        match = re.search(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"']+)", content_type, re.I)
        if match:
            charset = match.group(1).strip()
        try:
            codecs.lookup(charset)
        except LookupError as exc:
            raise ObservationSecurityError("web source charset is unsupported") from exc
        text = body.decode(charset, "replace")
        if mime in {"text/html", "text/xhtml"}:
            parser = _HTMLText()
            parser.feed(text)
            parser.close()
            text = "".join(parser.parts)
        else:
            text = html.unescape(text)
        text = re.sub(r"[\t\r\f\v ]+", " ", text)
        text = re.sub(r" *\n+ *", "\n", text).strip()
        return _truncate_utf8(text)


def _relative_parts(raw_path: object, *, allow_root: bool) -> tuple[str, ...]:
    if not isinstance(raw_path, str):
        raise ObservationSecurityError("workspace path must be text")
    if raw_path == "" and allow_root:
        return ()
    if not raw_path or raw_path.startswith(("/", "\\")) or "\\" in raw_path:
        raise ObservationSecurityError("workspace path must be canonical and relative")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ObservationSecurityError("workspace path must be canonical and relative")
    if any(_secret_name(part) for part in parts):
        raise ObservationSecurityError("workspace secret paths are not readable")
    return tuple(parts)


def _secret_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in _SECRET_NAMES
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith(_SECRET_SUFFIXES)
        or "credential" in lowered
        or "secret" in lowered
        or "service-account" in lowered
        or "private-key" in lowered
    )


def _binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        codecs.getincrementaldecoder("utf-8")("strict").decode(data, final=False)
    except UnicodeDecodeError:
        return True
    controls = sum(byte < 32 and byte not in {9, 10, 13} for byte in data)
    return bool(data) and controls / len(data) > 0.02


class WorkspaceReader:
    """Perform bounded list/read operations beneath registered roots using no-follow descriptors."""

    def __init__(self, registry: WorkspaceRegistry) -> None:
        self._registry = registry

    def list(self, workspace_id: object, path: object = "") -> dict[str, Any]:
        parts = _relative_parts(path, allow_root=True)
        descriptor = self._open(workspace_id, parts, directory=True)
        try:
            safe_items = []
            scanned = 0
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > _MAX_WORKSPACE_SCAN:
                        raise ObservationSecurityError("workspace directory exceeds scan limit")
                    name = entry.name
                    if _secret_name(name):
                        continue
                    try:
                        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        safe_items.append({"name": name, "type": "directory", "size": 0})
                    elif stat.S_ISREG(info.st_mode) and not self._binary_entry(descriptor, name):
                        safe_items.append({"name": name, "type": "file", "size": info.st_size})
            safe_items.sort(key=lambda item: item["name"])
            return {
                "workspace_id": workspace_id,
                "path": str(path),
                "items": safe_items[:_MAX_WORKSPACE_ITEMS],
                "truncated": len(safe_items) > _MAX_WORKSPACE_ITEMS,
            }
        finally:
            os.close(descriptor)

    def read(
        self,
        workspace_id: object,
        path: object,
        *,
        start_line: int = 1,
        max_lines: int = _MAX_WORKSPACE_LINES,
    ) -> dict[str, Any]:
        if type(start_line) is not int or not 1 <= start_line <= _MAX_START_LINE:
            raise ValueError("start_line must be between 1 and 10000")
        if type(max_lines) is not int or not 1 <= max_lines <= _MAX_WORKSPACE_LINES:
            raise ValueError("max_lines must be between 1 and 200")
        parts = _relative_parts(path, allow_root=False)
        descriptor = self._open(workspace_id, parts, directory=False)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ObservationSecurityError("workspace path is not a regular file")
            if info.st_size > _MAX_WORKSPACE_FILE_BYTES:
                raise ObservationSecurityError("workspace file exceeds 1 MiB")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                sample = stream.read(8192)
                if _binary(sample):
                    raise ObservationSecurityError("workspace binary files are not readable")
                stream.seek(0)
                current = 1
                while current < start_line:
                    raw = stream.readline(_MAX_CONTEXT_BYTES + 1)
                    if not raw:
                        break
                    if len(raw) > _MAX_CONTEXT_BYTES:
                        raise ObservationSecurityError("workspace logical line exceeds 64 KiB")
                    current += 1
                output = bytearray()
                lines = 0
                truncated = False
                while current >= start_line and lines < max_lines:
                    raw = stream.readline(_MAX_CONTEXT_BYTES + 1)
                    if not raw:
                        break
                    if len(raw) > _MAX_CONTEXT_BYTES:
                        raise ObservationSecurityError("workspace logical line exceeds 64 KiB")
                    if _binary(raw):
                        raise ObservationSecurityError("workspace binary files are not readable")
                    remaining = _MAX_CONTEXT_BYTES - len(output)
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(raw) > remaining:
                        truncated = True
                        break
                    output.extend(raw)
                    lines += 1
                    current += 1
                if not truncated:
                    truncated = bool(stream.peek(1))
                content = output.decode("utf-8", "ignore")
            return {
                "workspace_id": workspace_id,
                "path": str(path),
                "start_line": start_line,
                "content": content,
                "next_start_line": current if truncated else None,
            }
        finally:
            os.close(descriptor)

    def _open(self, workspace_id: object, parts: tuple[str, ...], *, directory: bool) -> int:
        try:
            root = self._registry.require(workspace_id)
        except WorkspaceRegistryError as exc:
            raise ObservationSecurityError("workspace is not registered") from exc
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(root, flags | directory_flag | no_follow)
            for index, part in enumerate(parts):
                child_flags = flags | no_follow
                if index < len(parts) - 1 or directory:
                    child_flags |= directory_flag
                try:
                    child = os.open(part, child_flags, dir_fd=descriptor)
                finally:
                    os.close(descriptor)
                    descriptor = -1
                descriptor = child
            return descriptor
        except OSError as exc:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EACCES, errno.EPERM}:
                raise ObservationSecurityError("workspace path is unavailable or unsafe") from exc
            raise ObservationSecurityError("workspace path could not be opened safely") from exc

    @staticmethod
    def _binary_entry(directory: int, name: str) -> bool:
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(name, flags, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    return True
                return _binary(os.read(descriptor, 8192))
            finally:
                os.close(descriptor)
        except OSError:
            return True


class WorkspaceListExecutor:
    """Adapt a validated workspace_list ToolCall to the descriptor-based reader."""

    def __init__(self, reader: WorkspaceReader) -> None:
        self._reader = reader

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._reader.list, call.arguments.get("workspace_id"), call.arguments.get("path", ""),
        )


class WorkspaceReadExecutor:
    """Adapt a validated workspace_read ToolCall to bounded line paging."""

    def __init__(self, reader: WorkspaceReader) -> None:
        self._reader = reader

    async def __call__(self, call: ToolCall) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._reader.read,
            call.arguments.get("workspace_id"),
            call.arguments.get("path"),
            start_line=call.arguments.get("start_line", 1),
            max_lines=call.arguments.get("max_lines", _MAX_WORKSPACE_LINES),
        )


__all__ = [
    "ObservationExecutorError",
    "ObservationLedger",
    "ObservationSecurityError",
    "SafeWebReader",
    "SourceRegistry",
    "TavilySearchExecutor",
    "WorkspaceListExecutor",
    "WorkspaceReadExecutor",
    "WorkspaceReader",
]
