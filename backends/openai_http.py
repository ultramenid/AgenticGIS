"""Minimal OpenAI-compatible Chat Completions client on stdlib urllib.

Supports streaming (SSE) and tool-calling so the same tool-use loop in
``api_backend.py`` works whether the wire format is Anthropic or OpenAI.
"""

import json
import urllib.error
import urllib.request


class OpenAIHttpError(Exception):
    pass


class OpenAIHttpClient:
    def __init__(self, api_key=None, base_url=None, extra_headers=None, org=None):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        self.extra_headers = extra_headers or {}
        self.org = org

    def _headers(self):
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        if self.org:
            headers["openai-organization"] = self.org
        headers.update(self.extra_headers)
        return headers

    def stream_message(self, model, max_tokens, system, tools, messages,
                       on_text, should_stop, timeout=600):
        """POST /v1/chat/completions with stream=True.

        Calls ``on_text(str)`` for each text delta. Returns
        ``(content_blocks, finish_reason)`` where blocks are a clean list
        suitable for replaying as an assistant message (text + tool_use).
        """
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

        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data, headers=self._headers(), method="POST",
        )

        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise OpenAIHttpError(f"HTTP {exc.code}: {detail[:600]}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIHttpError(f"Connection error: {exc.reason}") from exc

        text_parts = []
        tool_calls = {}        # index -> {id, type, function: {name, arguments}}
        finish_reason = None

        with response:
            for raw in response:
                if should_stop():
                    break
                line = raw.decode("utf-8", "replace").strip()
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
                choice = event.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                # text delta
                token = delta.get("content")
                if token:
                    text_parts.append(token)
                    on_text(token)

                # tool_call deltas
                for tcd in delta.get("tool_calls", []):
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

        return blocks, finish_reason

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
                tool_use_id = m.get("tool_use_id", "")
                out.append({
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": content,
                })
        return out

    @staticmethod
    def build_tool_list(tool_specs):
        """Convert our Anthropic-shaped tool specs to OpenAI function-calling format.

        Each input_schema dict becomes a JSON Schema ``parameters`` object.
        """
        tools = []
        for spec in tool_specs:
            tools.append({
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec.get("input_schema", {"type": "object"}),
                },
            })
        return tools

    @staticmethod
    def build_tool_result_message(tool_use_id, content):
        """Return a message dict for sending a tool result back to the model."""
        return {
            "role": "tool",
            "tool_call_id": tool_use_id,
            "content": content if isinstance(content, str) else json.dumps(content, default=str),
        }
