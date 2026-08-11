"""Minimal Anthropic Messages API client built on the Python standard library.

No third-party packages — uses ``urllib`` + ``json`` so the plugin runs on a
stock QGIS Python with nothing to install. Supports streaming (SSE) so the
chat dock can render tokens as they arrive, and reconstructs the final content
blocks (text + tool_use) needed to continue a tool-use loop.

Reliability hardening
---------------------
* A ``threading.Lock`` guards the connection slot so two concurrent
  ``send()`` calls (possible if a future change caches the client) cannot
  race on the connection state.
* The socket is created with a ``timeout`` matching the request timeout,
  so a half-closed SSE stream cannot hang ``readline()`` forever.
* Fully consumed responses leave the connection reusable; cancellation and
  transport errors close it so the next request reconnects cleanly.
"""

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..core.dev_logging import log_event


# Default per-stream inactivity timeout (seconds). Bounded so a stalled SSE
# cannot hold the worker thread forever; long enough to absorb legitimate
# long-tail completion requests. Mirrors the OpenAI client's DEFAULT_TIMEOUT.
DEFAULT_INACTIVITY_TIMEOUT = 120.0


def _safe_urlopen(request, **kwargs):
    """Wrap ``urllib.request.urlopen`` and reject non-HTTP(S) schemes.

    This prevents accidental ``file:/`` or custom-scheme access when
    user-provided URLs reach the HTTP layer (Bandit B310).
    """
    url = request.full_url if hasattr(request, "full_url") else str(request)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise urllib.error.URLError(
            f"Refusing to open non-HTTP(S) URL: {parsed.scheme}://{parsed.netloc}"
        )
    return urllib.request.urlopen(request, **kwargs)  # nosec B310


DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicHttpError(Exception):
    pass


class AnthropicHttpClient:
    def __init__(self, api_key=None, auth_token=None, base_url=None,
                 version=ANTHROPIC_VERSION, config=None):
        self.api_key = api_key
        self.auth_token = auth_token
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.version = version
        self.config = config
        self._base_path = urllib.parse.urlparse(self.base_url).path.rstrip("/")
        self._conn = None          # http.client.HTTPSConnection
        self._conn_host = None
        # serialise access to the connection slot. Cheap uncontended.
        self._conn_lock = threading.Lock()
        self._cancel_event = threading.Event()

    def _headers(self):
        headers = {
            "content-type": "application/json",
            "anthropic-version": self.version,
        }
        # Send both conventions: real Anthropic reads x-api-key and ignores
        # Authorization; custom/local servers (e.g. LM Studio) often only
        # check Authorization: Bearer and ignore x-api-key. Sending both
        # keeps every "Anthropic-compatible" server happy regardless of
        # which convention it implements.
        if self.api_key:
            headers["x-api-key"] = self.api_key
            headers["authorization"] = f"Bearer {self.api_key}"
        elif self.auth_token:
            headers["authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _messages_path(self):
        """Append ``/messages`` to the base URL's path as given, no ``/v1`` guessing."""
        if self._base_path.endswith("/messages"):
            return self._base_path
        return f"{self._base_path}/messages"

    def list_models(self, timeout=15):
        """GET ``{base_url}/models``. Doubles as a connection test.

        Returns ``(sorted_model_ids, None)`` on success or
        ``([], error_message)`` on failure. No ``/v1`` is guessed or
        inserted — the base URL must already include any version segment
        the server needs (e.g. ``https://api.anthropic.com/v1``).
        """
        request = urllib.request.Request(
            f"{self.base_url}/models", headers=self._headers(), method="GET"
        )
        try:
            response = _safe_urlopen(request, timeout=timeout)  # nosec B310
            data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            return [], (f"HTTP {exc.code}: {detail[:300]}" if detail else f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return [], f"Connection error: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return [], f"{type(exc).__name__}: {exc}"
        items = data.get("data") if isinstance(data, dict) else data
        models = [
            it["id"] for it in (items or [])
            if isinstance(it, dict) and it.get("id")
        ]
        return sorted(set(models)), None

    def _ensure_conn(self, timeout):
        """Return a live HTTPSConnection with a bounded socket timeout.

        Recreates if host changed or the socket is dead (peer reset).
        Returns ``(connection, is_new)`` where *is_new* is ``True`` when a
        fresh TCP+TLS handshake was just performed.
        """
        with self._conn_lock:
            if self._conn is not None:
                if self._conn_host != self.base_url:
                    try:
                        self._conn.close()
                    except Exception:  # nosec B110
                        pass
                    self._conn = None
                else:
                    try:
                        self._conn.sock.getpeername()
                    except Exception:
                        try:
                            self._conn.close()
                        except Exception:  # nosec B110
                            pass
                        self._conn = None

            is_new = self._conn is None
            if is_new:
                parsed = urllib.parse.urlparse(self.base_url)
                host = parsed.hostname or ""
                port = parsed.port
                if parsed.scheme == "https":
                    self._conn = http.client.HTTPSConnection(
                        host, port=port, timeout=timeout
                    )
                else:
                    self._conn = http.client.HTTPConnection(
                        host, port=port, timeout=timeout
                    )
                self._conn_host = self.base_url
        return self._conn, is_new

    def _close_conn(self):
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # nosec B110
                    pass
                self._conn = None

    def cancel_current_request(self):
        """Best-effort cancellation of the active HTTP stream."""
        self._cancel_event.set()
        self._close_conn()

    def stream_message(self, model, max_tokens, system, tools, messages,
                       on_text, should_stop, timeout=120,
                       on_connecting=None, temperature=None, top_p=None,
                       inactivity_timeout=DEFAULT_INACTIVITY_TIMEOUT):
        self._cancel_event.clear()
        thinking_config = self.config.get("anthropic_thinking") if self.config else None
        base_payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            "stream": True,
        }
        # Omit "thinking" entirely unless the user opted in — that's already
        # Anthropic's own default (non-extended-thinking) behavior, and
        # third-party Anthropic-compatible servers (LM Studio, etc.) may not
        # implement the field and can hang rather than error on it.
        if thinking_config:
            base_payload["thinking"] = thinking_config
        if temperature is not None:
            base_payload["temperature"] = temperature
        payload = json.dumps(base_payload).encode("utf-8")
        log_event(
            "transport.request_serialized",
            transport="anthropic",
            bytes=len(payload),
            model=model,
        )

        headers = self._headers()
        headers["Content-Length"] = str(len(payload))

        # --- HTTP request with retry/backoff for transient errors -------------
        # Retry policy mirrors the OpenAI client's resilience layer:
        #   * Connection errors (OSError / HTTPException / socket.timeout):
        #     retry once on a fresh connection (handles stale-connection TCP
        #     resets, the original single-retry case).
        #   * Transient HTTP statuses 429 / 500 / 502 / 503 / 504: retry up to 3
        #     total attempts with exponential backoff (1s, 2s, 4s). For 429,
        #     honor the server's ``Retry-After`` header when present.
        #   * Permanent client errors (400 / 401 / 403 / 404 / 422): raise
        #     immediately — retrying cannot help.
        # The retry wraps ONLY the request -> getresponse -> status check.
        # Once status 200 is returned and streaming begins, there is no retry;
        # the read loop has its own inactivity guard.
        _TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
        _PERMANENT_STATUSES = {400, 401, 403, 404, 422}
        max_attempts = 3
        response = None
        last_exc = None
        for attempt in range(max_attempts):
            conn, is_new = self._ensure_conn(timeout)
            if is_new and on_connecting:
                try:
                    on_connecting()
                except Exception:  # nosec B110
                    pass
            try:
                conn.request("POST", self._messages_path(), body=payload, headers=headers)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException, socket.timeout) as exc:  # noqa: F821
                # Stale/reset connection — close and retry on a fresh one.
                last_exc = exc
                self._close_conn()
                if self._cancel_event.is_set():
                    return [], "stop"
                log_event(
                    "transport.http_retry",
                    transport="anthropic",
                    attempt=attempt + 1,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if attempt == 0:
                    # First connect-error: single retry with backoff.
                    time.sleep(1)
                    continue
                raise AnthropicHttpError(f"Connection error: {exc}") from exc

            log_event(
                "transport.headers",
                transport="anthropic",
                status=response.status,
            )
            if response.status in _PERMANENT_STATUSES or (
                response.status >= 400 and response.status not in _TRANSIENT_STATUSES
            ):
                try:
                    body = response.read(600).decode("utf-8", "replace")
                except Exception:
                    body = ""
                self._close_conn()
                raise AnthropicHttpError(f"HTTP {response.status}: {body}")
            if response.status in _TRANSIENT_STATUSES:
                status = response.status
                # Honor Retry-After for 429 before consuming the body.
                retry_after_raw = None
                if status == 429:
                    try:
                        retry_after_raw = response.getheader("Retry-After")
                    except Exception:  # nosec B110
                        retry_after_raw = None
                try:
                    body = response.read(600).decode("utf-8", "replace")
                except Exception:
                    body = ""
                self._close_conn()
                response = None
                if self._cancel_event.is_set():
                    return [], "stop"
                # Backoff: server-requested delay for 429, else exponential 1s/2s/4s.
                delay = 2 ** attempt  # 1, 2, 4
                if retry_after_raw:
                    try:
                        delay = max(1.0, float(retry_after_raw))
                    except (TypeError, ValueError):
                        pass
                log_event(
                    "transport.http_retry",
                    transport="anthropic",
                    attempt=attempt + 1,
                    status=status,
                )
                if attempt + 1 >= max_attempts:
                    raise AnthropicHttpError(f"HTTP {status}: {body}")
                time.sleep(delay)
                continue
            # status 2xx — streaming will begin; break out of the retry loop.
            break

        if response is None:
            # All attempts exhausted on transient errors / connection errors.
            raise AnthropicHttpError(f"HTTP retry failed: {last_exc}")

        # Fix 1: re-bind the socket timeout after getresponse() so the
        # inactivity guard's ``readline()`` cannot block longer than
        # ``inactivity_timeout``. The connect-timeout (``timeout``) governs
        # the handshake; once streaming, the per-read bound must be the
        # inactivity window or a stalled peer blocks up to ``timeout`` (600s)
        # before the 120s guard can act. Mirrors openai_http.py:530-535.
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.settimeout(inactivity_timeout)
        except Exception:  # nosec B110
            pass

        blocks = {}
        json_buffers = {}
        stop_reason = None
        premature_exit = False
        stream_error = False
        stalled = False
        first_event_logged = False
        first_text_logged = False
        last_cache_logged = None
        # Inactivity guard: a stream that keeps the socket alive (e.g. SSE
        # heartbeats/comment lines) but sends no real data would otherwise
        # hang on ``readline()`` until the socket timeout (up to 600s). Bail
        # out once no new server data has arrived for ``inactivity_timeout``.
        last_activity = time.monotonic()

        try:
            while True:
                if should_stop():
                    premature_exit = True
                    break
                # Inactivity guard: checked before the blocking read so a
                # silent socket cannot hold the worker for the full socket
                # timeout. Comment/heartbeat lines (``:``) do NOT reset the
                # timer — only real ``data:`` events do (reset below).
                if time.monotonic() - last_activity > inactivity_timeout:
                    stalled = True
                    stream_error = True
                    log_event(
                        "transport.stream_stalled",
                        transport="anthropic",
                        idle_s=round(time.monotonic() - last_activity, 1),
                    )
                    break
                try:
                    raw = response.readline()
                except (http.client.HTTPException, OSError, TimeoutError):
                    stream_error = True
                    break
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # Real server data arrived — reset the inactivity timer.
                last_activity = time.monotonic()
                if not first_event_logged:
                    log_event(
                        "transport.first_stream_event",
                        transport="anthropic",
                    )
                    first_event_logged = True
                last_cache_logged = self._log_cache_usage(event, last_cache_logged)
                etype = event.get("type")

                # Fix 4: defensive SSE parsing. Malformed events (missing
                # ``index``, ``content_block``, ``text``, etc.) used to raise
                # KeyError/TypeError and abort the whole stream. Wrap the
                # dispatch so one bad event is logged and skipped instead.
                try:
                    if etype == "content_block_start":
                        idx = event.get("index")
                        if idx is None:
                            log_event(
                                "transport.sse_malformed",
                                transport="anthropic",
                                etype=etype,
                                reason="missing index",
                            )
                            continue
                        content_block = event.get("content_block")
                        if not isinstance(content_block, dict):
                            log_event(
                                "transport.sse_malformed",
                                transport="anthropic",
                                etype=etype,
                                reason="missing content_block",
                            )
                            continue
                        block = dict(content_block)
                        blocks[idx] = block
                        if block.get("type") == "tool_use":
                            json_buffers[idx] = ""
                            block.setdefault("input", {})
                    elif etype == "content_block_delta":
                        idx = event.get("index")
                        if idx is None:
                            log_event(
                                "transport.sse_malformed",
                                transport="anthropic",
                                etype=etype,
                                reason="missing index",
                            )
                            continue
                        delta = event.get("delta", {})
                        if not isinstance(delta, dict):
                            delta = {}
                        if delta.get("type") == "text_delta":
                            if not first_text_logged:
                                log_event(
                                    "transport.first_text",
                                    transport="anthropic",
                                )
                                first_text_logged = True
                            text_chunk = delta.get("text", "")
                            blocks.setdefault(idx, {})["text"] = (
                                blocks.get(idx, {}).get("text", "") + text_chunk
                            )
                            try:
                                on_text(text_chunk)
                            except Exception:  # nosec B110
                                # An exception in the on_text callback (e.g. a Qt
                                # signal dispatch error) should not crash the
                                # streaming loop — drop the delta and continue.
                                pass
                        elif delta.get("type") == "input_json_delta":
                            json_buffers[idx] = (
                                json_buffers.get(idx, "") + delta.get("partial_json", "")
                            )
                    elif etype == "content_block_stop":
                        idx = event.get("index")
                        if idx is None:
                            continue
                        if idx in json_buffers:
                            buf = json_buffers[idx]
                            try:
                                blocks.setdefault(idx, {})["input"] = (
                                    json.loads(buf) if buf else {}
                                )
                            except json.JSONDecodeError:
                                blocks.setdefault(idx, {})["input"] = {}
                    elif etype == "message_delta":
                        stop_reason = event.get("delta", {}).get("stop_reason", stop_reason)
                    elif etype == "error":
                        error = event.get("error")
                        if isinstance(error, dict):
                            msg = error.get("message", str(error))
                        elif isinstance(error, str):
                            msg = error
                        else:
                            msg = str(error)
                        raise AnthropicHttpError(msg)
                except (KeyError, TypeError, ValueError) as sse_exc:
                    # One malformed event must not crash the whole stream.
                    log_event(
                        "transport.sse_malformed",
                        transport="anthropic",
                        etype=etype,
                        error=f"{type(sse_exc).__name__}: {sse_exc}",
                    )
                    continue
        finally:
            # stop trying to drain the socket. The connect-then-close
            # cost is negligible at our call rate, and the drain branch was
            # the only path that could hang on a half-closed peer.
            if premature_exit or stream_error:
                self._close_conn()

        # A stall that produced nothing usable would otherwise end the turn
        # silently (the dock keeps showing "Preparing answer…"). Surface it so
        # the agent loop emits a visible error the user can act on. Matches
        # the OpenAI client's behaviour; cancellation is handled separately.
        if (
            stalled
            and not premature_exit
            and not self._cancel_event.is_set()
            and not blocks
        ):
            raise AnthropicHttpError(
                "The model stopped sending data mid-stream (no response for "
                f"~{int(inactivity_timeout)}s). The endpoint may be slow or "
                "overloaded — try again, or switch model in Settings."
            )

        if self._cancel_event.is_set():
            stop_reason = "stop"
        return self._clean_blocks(blocks), stop_reason

    @staticmethod
    def _log_cache_usage(event, last_logged=None):
        """Log cache usage, deduped per stream.

        Some events repeat the same ``usage`` block; logging each one writes
        a file line per chunk, which is pure overhead. Returns the value to
        pass back in as ``last_logged`` on the next event.
        """
        usage = event.get("usage")
        if not isinstance(usage, dict):
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            return last_logged
        cache_fields = {
            key: usage.get(key)
            for key in (
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
            if key in usage
        }
        if cache_fields:
            if cache_fields != last_logged:
                log_event(
                    "transport.cache_usage",
                    transport="anthropic",
                    **cache_fields,
                )
            return cache_fields
        return last_logged

    def close(self):
        """Close the reusable connection; safe to call more than once."""
        self._close_conn()

    def prewarm(self, timeout=10):
        """Eagerly perform the TCP+TLS handshake without sending a request.

        Calling this before the first ``stream_message`` hides the handshake
        latency from the user's perceived time-to-first-token. Establishes the
        socket only when no live connection exists, so it never clobbers or
        duplicates an in-flight connection. Never raises — the network may be
        down; a failed prewarm just means the first send pays the handshake.
        """
        with self._conn_lock:
            if self._conn is not None and getattr(self._conn, "sock", None) is not None:
                return
            parsed = urllib.parse.urlparse(self.base_url)
            host = parsed.hostname or ""
            port = parsed.port
            if parsed.scheme == "https":
                self._conn = http.client.HTTPSConnection(host, port=port, timeout=timeout)
            else:
                self._conn = http.client.HTTPConnection(host, port=port, timeout=timeout)
            self._conn_host = self.base_url
            try:
                self._conn.connect()
            except Exception:  # nosec B110
                try:
                    self._conn.close()
                except Exception:  # nosec B110
                    pass
                self._conn = None

    @staticmethod
    def _clean_blocks(blocks):
        cleaned = []
        for idx in sorted(blocks):
            block = blocks[idx]
            if block.get("type") == "text":
                cleaned.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "tool_use":
                cleaned.append({
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input", {}),
                })
        return cleaned
