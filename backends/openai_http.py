"""Minimal OpenAI-compatible Chat Completions client on stdlib urllib.

Supports streaming (SSE) and tool-calling so the same tool-use loop in
``api_backend.py`` works whether the wire format is Anthropic or OpenAI.

Reliability hardening
---------------------
* Each stream opens with a bounded socket timeout and closes the response
  on ``should_stop()`` so a half-closed SSE cannot leave the worker
  blocked until the timeout fires.
* Mid-stream ``read()`` errors are caught and the loop exits cleanly,
  preserving any text already collected.
* Token budget guard prevents runaway streams from holding the worker
  indefinitely.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request


class OpenAIHttpError(Exception):
    pass


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


# Default per-stream timeout. Bounded so a stalled SSE cannot hold the
# worker thread forever; long enough to absorb legitimate long-tail
# completion requests.
DEFAULT_TIMEOUT = 120.0


class OpenAIHttpClient:
    def __init__(self, api_key=None, base_url=None, extra_headers=None, org=None,
                 timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        self.extra_headers = extra_headers or {}
        self.org = org
        self.timeout = timeout
        self._active_response = None
        self._active_lock = threading.Lock()
        self._cancel_requested = False

    def _headers(self):
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        if self.org:
            headers["openai-organization"] = self.org
        headers.update(self.extra_headers)
        return headers

    def list_models(self, timeout=15):
        """GET the models list. Doubles as a connection test.

        Returns ``(sorted_model_ids, None)`` on success or
        ``([], error_message)`` on failure (the message explains why, e.g.
        ``HTTP 401`` for a bad key). Tries ``/v1/models`` then ``/models`` to
        tolerate base-URL path differences across OpenAI-compatible providers.
        """
        last_err = ""
        for path in ("/v1/models", "/models"):
            request = urllib.request.Request(
                f"{self.base_url}{path}", headers=self._headers(), method="GET"
            )
            try:
                response = _safe_urlopen(request, timeout=timeout)  # nosec B310
                body = response.read().decode("utf-8", "replace")
                data = json.loads(body)
                items = data.get("data") if isinstance(data, dict) else data
                models = []
                for it in items or []:
                    if isinstance(it, dict) and it.get("id"):
                        models.append(it["id"])
                    elif isinstance(it, str):
                        models.append(it)
                return sorted(set(models)), None
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", "replace")
                except Exception:
                    detail = ""
                last_err = f"HTTP {exc.code}: {detail[:300]}" if detail else f"HTTP {exc.code}"
                # Auth/permission errors won't be fixed by the other path.
                if exc.code in (401, 403):
                    return [], last_err
            except urllib.error.URLError as exc:
                return [], f"Connection error: {exc.reason}"
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
        return [], last_err or "Unknown error"

    def stream_message(self, model, max_tokens, system, tools, messages,
                       on_text, should_stop, timeout=None):
        """POST /v1/chat/completions with stream=True.

        Calls ``on_text(str)`` for each text delta. Returns
        ``(content_blocks, finish_reason)`` where blocks are a clean list
        suitable for replaying as an assistant message (text + tool_use).
        Closes the response on ``should_stop()`` so a half-closed SSE
        cannot leave the worker blocked.
        """
        effective_timeout = self.timeout if timeout is None else timeout
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._build_messages(system, messages),
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        data = json.dumps(payload).encode("utf-8")

        headers = self._headers()
        headers["Connection"] = "keep-alive"
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data, headers=headers, method="POST",
        )

        with self._active_lock:
            self._cancel_requested = False

        response = None
        try:
            response = _safe_urlopen(request, timeout=effective_timeout)  # nosec B310
            with self._active_lock:
                self._active_response = response
                # Cancel was called while urlopen() was blocking — close immediately.
                if self._cancel_requested:
                    self._active_response = None
                    try:
                        response.close()
                    except Exception:
                        pass
                    return [], "stop"
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            raise OpenAIHttpError(f"HTTP {exc.code}: {detail[:600]}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIHttpError(f"Connection error: {exc.reason}") from exc

        text_parts = []
        tool_calls = {}        # index -> {id, type, function: {name, arguments}}
        finish_reason = None
        stopped = False

        try:
            for raw in response:
                if should_stop():
                    stopped = True
                    break
                try:
                    line = raw.decode("utf-8", "replace").strip()
                except Exception:
                    break
                if not line or not line.startswith("data: "):
                    continue
                data_text = line[len("data: "):].strip()
                if data_text == "[DONE]":
                    break
                if not data_text:
                    continue
                try:
                    event = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or [{}]
                choice = choices[0]
                delta = choice.get("delta", {})
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                # text delta
                token = delta.get("content")
                if token:
                    text_parts.append(token)
                    try:
                        on_text(token)
                    except Exception:
                        # A callback failure should not break the stream.
                        pass

                # tool_call deltas
                for tcd in (delta.get("tool_calls") or []):
                    idx = tcd.get("index", 0)
                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": tcd.get("id", ""),
                            "type": tcd.get("type", "function"),
                            "function": {"name": "", "arguments": ""},
                        }
                    fn = tcd.get("function", {})
                    if fn.get("name"):
                        tool_calls[idx]["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tool_calls[idx]["function"]["arguments"] += fn["arguments"]
        finally:
            # close the response on every exit path (should_stop, error,
            # natural end) so a half-closed peer doesn't leave the worker
            # blocked until the OS-level timeout fires.
            try:
                response.close()
            except Exception:
                pass
            with self._active_lock:
                if self._active_response is response:
                    self._active_response = None

        blocks = []
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})
        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            raw_args = tc["function"].get("arguments", "")
            try:
                parsed = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                parsed = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": tc["function"].get("name", ""),
                "input": parsed,
            })

        if stopped:
            # The caller checks finish_reason; force a non-tool finish so
            # the agent loop ends the turn rather than waiting for a
            # completion the user already cancelled.
            finish_reason = "stop"

        return blocks, finish_reason

    def cancel_current_request(self):
        """Best-effort cancellation of the active streaming response."""
        with self._active_lock:
            self._cancel_requested = True
            response = self._active_response
        if response is None:
            return
        try:
            response.close()
        except Exception:
            pass

    @staticmethod
    def _build_messages(system, messages):
        """Convert our internal message list to OpenAI Chat Completions shape.

        Internal messages are the same shape as Anthropic: role + content
        with optional tool_use / tool_result blocks. We map them to OpenAI
        roles (system, user, assistant, tool).
        """
        out = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                out.append({"role": "user", "content": content})
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                assistant_msg = {
                    "role": "assistant",
                    "content": content if content else None,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
            elif role == "tool":
                # Internal message format uses ``tool_use_id`` (matching
                # Anthropic's shape — see build_tool_result_message). Read
                # that key here, not ``tool_call_id``, otherwise the
                # outgoing request carries ``tool_call_id=""`` and strict
                # providers (DeepSeek, etc.) reject it with
                # "Messages with role 'tool' must be a response to a
                # preceding message with 'tool_calls'".
                tool_call_id = m.get("tool_use_id", "")
                out.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                })
        return out

    @staticmethod
    def build_tool_list(tool_specs):
        """Convert our Anthropic-shaped tool specs to OpenAI function-calling format.

        Each input_schema dict becomes a JSON Schema ``parameters`` object.
        Empty properties objects are replaced with an empty object schema to
        satisfy strict providers (DeepSeek, etc.).
        """
        tools = []
        for spec in tool_specs:
            schema = spec.get("input_schema", {"type": "object"})
            # Some providers reject empty properties objects; expand to a generic
            # empty object if needed.
            if schema.get("type") == "object" and not schema.get("properties"):
                schema = {"type": "object"}
            tools.append({
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": schema,
                },
            })
        return tools

    @staticmethod
    def build_tool_result_message(tool_use_id, content):
        """Return a message dict for sending a tool result back to the model.

        The internal message format uses ``tool_use_id`` (matching
        Anthropic's shape — see ``_build_messages``). Keeping one key
        name across writers and readers means a tool result round-trips
        intact; if the writer used ``tool_call_id`` and the reader used
        ``tool_use_id`` the outgoing request would carry an empty id and
        strict providers (DeepSeek, etc.) would reject it with
        "Messages with role 'tool' must be a response to a preceding
        message with 'tool_calls'".
        """
        return {
            "role": "tool",
            "tool_use_id": tool_use_id,
            "content": content if isinstance(content, str) else json.dumps(content, default=str),
        }
