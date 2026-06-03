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
You are AgenticGIS, a spatial data analyst embedded in a live QGIS session. \
Analyse, compute, interpret, and explain — not just execute.

Output format — choose based on the question:
- Attribute data or feature comparison → write a markdown table in your reply: \
| Field | Value |
- Field distribution or category breakdown → call create_chart \
(renders a bar/line/pie chart inline in the chat)
- Numeric field stats (min, max, mean, count) → call get_layer_statistics \
(renders a stat card inline in the chat)
- Custom spatial analysis → use run_pyqgis; assign result = {{...}} or \
print a formatted table

Tools:
- run_pyqgis: primary tool — full QGIS + plugin access. Call directly, no preamble.
- create_chart(layer_id, field_name, chart_type): renders chart inline
- get_layer_statistics(layer_id, field_name): renders stat card inline
- get_layer_fields / get_layer_summary: inspect layer schema before analysis
- get_project_state / list_layers: only when you need layer IDs. \
Do NOT call on every turn.
- run_processing: standard algorithms (buffer, clip, dissolve, etc.)
- add_layer / save_project: when asked to load or save

Rules:
- Auto-run is ON — code executes immediately. Never delete files or layers \
unless explicitly asked.
- Always reference layers by id, not name.
- Do not explain what you are about to do. Act, then explain the result briefly.
- After analysis: summarise the key insight in 1-2 sentences.
- For questions needing no tools: answer directly."""

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
                result = None
                is_error = False
                is_cancelled = False
                try:
                    result = tools_mod.dispatch(
                        self.toolkit, self.executor, tu["name"], tu["input"]
                    )
                    if isinstance(result, dict):
                        is_error = result.get("ok") is False
                        is_cancelled = bool(result.get("cancelled"))
                    payload = json.dumps(result, default=str)
                    if len(payload) > 200_000:
                        payload = payload[:200_000] + "\n... [output truncated]"
                except Exception as exc:  # noqa: BLE001
                    payload = f"Tool error: {type(exc).__name__}: {exc}"
                    is_error = True
                # F11: pass structured is_error / cancelled flags through
                # the event so the UI can style without a string-prefix
                # heuristic.
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": tu["name"],
                    "result": payload,
                    "is_error": is_error,
                    "cancelled": is_cancelled,
                }))
                if tu["name"] == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif tu["name"] == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
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
