"""In-process agent loop talking to any OpenAI-compatible Chat Completions endpoint.

Uses the stdlib ``OpenAIHttpClient`` (backends/openai_http.py) so the plugin
runs on a stock QGIS with no packages. The model's tool_calls map onto
``QgisToolkit`` via ``core.tools.dispatch``.
"""

import json
import os

from ..core import tools as tools_mod
from .base import AgentBackend, AgentEvent, EventType
from .openai_http import OpenAIHttpClient, OpenAIHttpError

DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a GIS assistant embedded in a running QGIS session. \
Operate QGIS by calling tools. Be direct — act first, explain briefly after.

Tools:
- run_pyqgis: use for almost everything. Full QGIS + plugin access. Call it \
directly without preamble.
- get_project_state / list_layers: call only when you genuinely need layer \
IDs or project context. Do NOT call on every turn.
- run_processing: use for standard algorithms (buffer, clip, dissolve, etc.).

Rules:
- Auto-run is ON — code executes immediately. Never delete files or layers \
unless explicitly asked.
- Always reference layers by id, not name.
- Do not explain what you are about to do. Just do it.
- After acting: one short sentence on what changed. No narration.
- For questions that need no tools: answer directly without calling anything."""

MAX_TOKENS = 4096


class OpenAIBackend(AgentBackend):
    label = "API (OpenAI-compatible)"

    def __init__(self, config, toolkit, executor):
        self.config = config
        self.toolkit = toolkit
        self.executor = executor

    # ------------------------------------------------------------------ #
    def _provider(self):
        from . import providers
        pid = self.config.get("provider")
        if pid == "custom":
            return None
        return providers.get_provider(pid)

    def _client(self):
        p = self._provider()
        if p:
            api_key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            base_url = p["base_url"]
        else:
            api_key = self.config.get("api_key") or ""
            base_url = self.config.get("custom_base_url")
        return OpenAIHttpClient(api_key=api_key or None, base_url=base_url)

    def validate(self):
        p = self._provider()
        if p:
            key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            label = p["label"]
        else:
            key = self.config.get("api_key")
            label = "Custom endpoint"
        if not key:
            return (
                f"No API key set for {label}. "
                f"Add one in Settings (or set {p['key_env'] if p else 'the provider key env'})."
            )
        return None

    def _system_text(self):
        return self.config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    def _tool_list(self):
        return OpenAIHttpClient.build_tool_list(tools_mod.TOOL_SPECS)

    # ------------------------------------------------------------------ #
    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        client = self._client()
        model = self.config.get("model")
        max_iters = self.config.get("max_iterations")

        messages = list(history)
        messages.append({"role": "user", "content": message})

        for _ in range(max_iters):
            if should_stop():
                emit(AgentEvent(EventType.THINKING, {"text": "Stopped."}))
                break

            try:
                content, finish_reason = client.stream_message(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=self._system_text(),
                    tools=self._tool_list(),
                    messages=messages,
                    on_text=lambda t: emit(AgentEvent(EventType.TEXT, {"text": t})),
                    should_stop=should_stop,
                )
            except OpenAIHttpError as exc:
                emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
                return messages
            except Exception as exc:  # noqa: BLE001
                emit(AgentEvent(EventType.ERROR,
                                {"error": f"{type(exc).__name__}: {exc}"}))
                return messages

            # Build the assistant message for the next turn
            text = ""
            tool_calls = []
            for b in content:
                if b.get("type") == "text":
                    text = b.get("text", "")
                elif b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    })
            assistant_msg = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if finish_reason != "tool_calls" or not tool_calls:
                emit(AgentEvent(EventType.DONE))
                return messages

            # Dispatch tools and build tool result messages
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                emit(AgentEvent(EventType.TOOL_USE, {"name": name, "input": args}))
                try:
                    result = tools_mod.dispatch(
                        self.toolkit, self.executor, name, args
                    )
                    payload = json.dumps(result, default=str)
                except Exception as exc:  # noqa: BLE001
                    payload = f"Tool error: {type(exc).__name__}: {exc}"
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": name, "result": payload}))
                messages.append(
                    OpenAIHttpClient.build_tool_result_message(tc["id"], payload)
                )
        else:
            emit(AgentEvent(EventType.THINKING,
                            {"text": f"Reached max {max_iters} iterations."}))
            emit(AgentEvent(EventType.DONE))
        return messages
