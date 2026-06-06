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
* The drain-to-EOF branch was removed: it added no value for an interactive
  chat and was the only path that could hang the worker on a peer reset.
  After every response we just close the connection — the next call opens
  a fresh one.
"""

import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request


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


DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicHttpError(Exception):
    pass


class AnthropicHttpClient:
    def __init__(self, api_key=None, auth_token=None, base_url=None,
                 version=ANTHROPIC_VERSION):
        self.api_key = api_key
        self.auth_token = auth_token
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.version = version
        self._conn = None          # http.client.HTTPSConnection
        self._conn_host = None
        # serialise access to the connection slot. Cheap uncontended.
        self._conn_lock = threading.Lock()

    def _headers(self):
        headers = {
            "content-type": "application/json",
            "anthropic-version": self.version,
        }
        # Prefer a raw API key; fall back to a bearer token (subscription/OAuth).
        if self.api_key:
            headers["x-api-key"] = self.api_key
        elif self.auth_token:
            headers["authorization"] = f"Bearer {self.auth_token}"
        return headers

    def list_models(self, timeout=15):
        """GET /v1/models. Doubles as a connection test.

        Returns ``(sorted_model_ids, None)`` on success or
        ``([], error_message)`` on failure.
        """
        request = urllib.request.Request(
            f"{self.base_url}/v1/models", headers=self._headers(), method="GET"
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
        """
        with self._conn_lock:
            if self._conn is not None:
                if self._conn_host != self.base_url:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                else:
                    try:
                        self._conn.sock.getpeername()
                    except Exception:
                        try:
                            self._conn.close()
                        except Exception:
                            pass
                        self._conn = None

            if self._conn is None:
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
        return self._conn

    def _close_conn(self):
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def cancel_current_request(self):
        """Best-effort cancellation of the active HTTP stream."""
        self._close_conn()

    def stream_message(self, model, max_tokens, system, tools, messages,
                       on_text, should_stop, timeout=600):
        payload = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            "stream": True,
            "thinking": {"type": "disabled"},
        }).encode("utf-8")

        headers = self._headers()
        headers["Content-Length"] = str(len(payload))

        conn = self._ensure_conn(timeout)
        try:
            conn.request("POST", "/v1/messages", body=payload, headers=headers)
            response = conn.getresponse()
        except (OSError, http.client.HTTPException, socket.timeout):  # noqa: F821
            # Stale connection; retry once with a fresh one
            self._close_conn()
            conn = self._ensure_conn(timeout)
            try:
                conn.request("POST", "/v1/messages", body=payload, headers=headers)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException, socket.timeout):  # noqa: F821
                self._close_conn()
                raise

        if response.status >= 400:
            try:
                body = response.read(600).decode("utf-8", "replace")
            except Exception:
                body = ""
            self._close_conn()
            raise AnthropicHttpError(f"HTTP {response.status}: {body}")

        blocks = {}
        json_buffers = {}
        stop_reason = None
        premature_exit = False

        try:
            while True:
                if should_stop():
                    premature_exit = True
                    break
                try:
                    raw = response.readline()
                except (http.client.HTTPException, OSError, TimeoutError):
                    # Network went away; treat as a normal end-of-stream.
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
                etype = event.get("type")

                if etype == "content_block_start":
                    idx = event["index"]
                    block = dict(event["content_block"])
                    blocks[idx] = block
                    if block.get("type") == "tool_use":
                        json_buffers[idx] = ""
                        block.setdefault("input", {})
                elif etype == "content_block_delta":
                    idx = event["index"]
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        blocks[idx]["text"] = blocks[idx].get("text", "") + delta["text"]
                        try:
                            on_text(delta["text"])
                        except Exception:
                            # An exception in the on_text callback (e.g. a Qt
                            # signal dispatch error) should not crash the
                            # streaming loop — drop the delta and continue.
                            pass
                    elif delta.get("type") == "input_json_delta":
                        json_buffers[idx] = json_buffers.get(idx, "") + delta.get("partial_json", "")
                elif etype == "content_block_stop":
                    idx = event["index"]
                    if idx in json_buffers:
                        buf = json_buffers[idx]
                        try:
                            blocks[idx]["input"] = json.loads(buf) if buf else {}
                        except json.JSONDecodeError:
                            blocks[idx]["input"] = {}
                elif etype == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason", stop_reason)
                elif etype == "error":
                    raise AnthropicHttpError(str(event.get("error")))
        finally:
            # stop trying to drain the socket. The connect-then-close
            # cost is negligible at our call rate, and the drain branch was
            # the only path that could hang on a half-closed peer.
            if premature_exit:
                self._close_conn()

        return self._clean_blocks(blocks), stop_reason

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
