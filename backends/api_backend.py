"""In-process agent loop talking to the Anthropic Messages API over HTTP.

Uses the dependency-free ``AnthropicHttpClient`` (stdlib only), so it runs on a
stock QGIS Python with nothing to install. Used by both API-key and
subscription modes (they differ only in how credentials are supplied). The
model's tool calls map onto ``QgisToolkit`` via ``core.tools.dispatch``; every
QGIS operation is marshaled to the main thread by the executor. The system
prompt and tool list are prompt-cached to keep multi-turn sessions cheap.
"""

import json
import os

from ..core import tools as tools_mod
from .anthropic_http import AnthropicHttpClient, AnthropicHttpError
from .base import AgentBackend, AgentEvent, EventType

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


class ApiBackend(AgentBackend):
    label = "API (Anthropic)"

    def __init__(self, config, toolkit, executor):
        self.config = config
        self.toolkit = toolkit
        self.executor = executor
        # Cache static tool specs and system prompt
        self._cached_system_key = None
        self._cached_system_blocks = None
        self._cached_tool_list = None

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
            # Fallback for unknown/historical provider
            api_key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        return AnthropicHttpClient(
            api_key=api_key or None, auth_token=None, base_url=base_url
        )

    def validate(self):
        p = self._provider()
        if p:
            key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            label = p["label"]
        else:
            key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            label = "Anthropic"
        if not key:
            return (
                f"No API key set for {label}. "
                f"Add one in Settings (or set {p['key_env'] if p else 'ANTHROPIC_API_KEY'})."
            )
        return None

    def _system_blocks(self):
        text = self.config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        if text != self._cached_system_key:
            self._cached_system_key = text
            self._cached_system_blocks = [
                {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
            ]
        return self._cached_system_blocks

    def _tool_list(self):
        if self._cached_tool_list is None:
            tool_list = tools_mod.anthropic_tool_list()
            if tool_list:
                tool_list[-1] = {**tool_list[-1], "cache_control": {"type": "ephemeral"}}
            self._cached_tool_list = tool_list
        return self._cached_tool_list

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
                content, stop_reason = client.stream_message(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=self._system_blocks(),
                    tools=self._tool_list(),
                    messages=messages,
                    on_text=lambda t: emit(AgentEvent(EventType.TEXT, {"text": t})),
                    should_stop=should_stop,
                )
            except AnthropicHttpError as exc:
                emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
                return messages
            except Exception as exc:  # noqa: BLE001
                emit(AgentEvent(EventType.ERROR,
                                {"error": f"{type(exc).__name__}: {exc}"}))
                return messages

            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                emit(AgentEvent(EventType.DONE))
                return messages

            tool_results = []
            for tu in tool_uses:
                emit(AgentEvent(EventType.TOOL_USE,
                                {"name": tu["name"], "input": tu["input"]}))
                try:
                    result = tools_mod.dispatch(
                        self.toolkit, self.executor, tu["name"], tu["input"]
                    )
                    payload = json.dumps(result, default=str)
                    if len(payload) > 200_000:
                        payload = payload[:200_000] + "\n... [output truncated]"
                    is_error = isinstance(result, dict) and result.get("ok") is False
                except Exception as exc:  # noqa: BLE001
                    payload = f"Tool error: {type(exc).__name__}: {exc}"
                    is_error = True
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": tu["name"], "result": payload}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": payload,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        else:
            emit(AgentEvent(EventType.THINKING,
                            {"text": f"Reached max {max_iters} iterations."}))
            emit(AgentEvent(EventType.DONE))
        return messages
