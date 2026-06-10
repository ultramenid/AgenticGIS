"""In-process agent loop talking to the Anthropic Messages API over HTTP.

Uses the dependency-free ``AnthropicHttpClient`` (stdlib only), so it runs on a
stock QGIS Python with nothing to install. Used by both API-key and
subscription modes (they differ only in how credentials are supplied). The
model's tool calls map onto ``QgisToolkit`` via ``core.tools.dispatch``; every
QGIS operation is marshaled to the main thread by the executor. The system
prompt and tool list are prompt-cached to keep multi-turn sessions cheap.
"""

import os

from ..core import tools as tools_mod
from .anthropic_http import AnthropicHttpClient, AnthropicHttpError
from .base import (
    MAX_TOKENS,
    AgentBackend,
    AgentEvent,
    EventType,
    _ToolCall,
    _dispatch_tools_maybe_parallel,
    agent_iteration_steps,
    elide_stale_tool_results,
    should_compact,
    unlimited_iterations,
)
from .openai_backend import build_system_prompt


def _messages_with_cache_breakpoint(messages):
    """Return a shallow copy of ``messages`` with an ephemeral cache_control
    breakpoint on the last block of the last message.

    Lets Anthropic cache the growing conversation prefix across turns (system
    blocks and the last tool definition already carry their own breakpoints),
    so turn 2+ reads the history from cache instead of re-prefilling it. The
    input list and its message dicts are left untouched. Anthropic ignores
    breakpoints below the minimum cacheable size, so short chats are simply
    unaffected.
    """
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = blocks
    elif isinstance(content, str) and content:
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    else:
        return messages
    out[-1] = last
    return out


class ApiBackend(AgentBackend):
    label = "API (Anthropic)"

    def __init__(self, config, toolkit, executor):
        super().__init__(config, toolkit, executor)
        self._cached_system_key = None
        self._cached_system_blocks = None

    # ------------------------------------------------------------------ #
    def _client(self):
        with self._active_client_lock:
            if self._active_client is None:
                p = self._provider()
                if p:
                    api_key = self.config.get("api_key") or os.environ.get(
                        p["key_env"], ""
                    )
                    configured_url = (
                        self.config.get("api_base_url") or ""
                    ).strip()
                    base_url = configured_url or p["base_url"]
                else:
                    api_key = self.config.get("custom_api_key") or ""
                    base_url = self.config.get("custom_base_url") or None
                self._active_client = AnthropicHttpClient(
                    api_key=api_key or None,
                    auth_token=None,
                    base_url=base_url,
                )
            return self._active_client

    def close(self):
        with self._active_client_lock:
            client = self._active_client
            self._active_client = None
        if client is not None:
            client.close()

    def prewarm(self):
        err = self.validate()
        if err:
            return
        try:
            self._client().prewarm()
        except Exception:  # nosec B110
            pass

    def _gee_available(self):
        """Return True when the GEE plugin is available (or toolkit is absent)."""
        toolkit = getattr(self, "toolkit", None)
        if toolkit is None:
            return True  # fail-open: no toolkit means tests / unknown context
        try:
            return toolkit.gee_available()
        except Exception:  # nosec B110
            return True

    def _system_blocks(self):
        user_override = self.config.get("system_prompt")
        if user_override:
            text = user_override
        else:
            # Include the GEE suffix only when the plugin is available.
            # Cache key encodes the include_gee outcome so a session that
            # gains or loses the GEE plugin gets a fresh block.
            include_gee = self._gee_available()
            text = build_system_prompt(include_gee=include_gee)
        cache_key = text
        if cache_key != self._cached_system_key:
            self._cached_system_key = cache_key
            self._cached_system_blocks = [
                {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
            ]
        return self._cached_system_blocks

    def _system_arg(self):
        return self._system_blocks()

    def _tool_list(self):
        include_gee = self._gee_available()
        cache_valid = (
            self._cached_tool_list is not None
            and getattr(self, "_cached_tool_list_gee", None) == include_gee
        )
        if not cache_valid:
            tool_list = tools_mod.anthropic_tool_list(include_gee=include_gee)
            if tool_list:
                tool_list[-1] = {
                    **tool_list[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            self._cached_tool_list = tool_list
            self._cached_tool_list_gee = include_gee
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
        # Fix A1: elide stale tool-result payloads before sending.
        messages = elide_stale_tool_results(messages)
        messages.append({"role": "user", "content": message})

        is_unlimited = unlimited_iterations(max_iters)
        for _ in agent_iteration_steps(max_iters):
            if should_stop():
                emit(AgentEvent(EventType.THINKING, {"text": "Stopped."}))
                emit(AgentEvent(EventType.DONE))
                return messages

            if should_compact(messages, model or ""):
                messages = self._compact_history(messages, emit, should_stop)

            try:
                content, stop_reason = client.stream_message(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=self._system_blocks(),
                    tools=self._tool_list(),
                    messages=_messages_with_cache_breakpoint(messages),
                    on_text=lambda t: emit(AgentEvent(EventType.TEXT, {"text": t})),
                    should_stop=should_stop,
                    on_connecting=lambda: emit(AgentEvent(EventType.CONNECTING)),
                )
            except AnthropicHttpError as exc:
                emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
                return messages
            except Exception as exc:  # noqa: BLE001
                emit(
                    AgentEvent(
                        EventType.ERROR, {"error": f"{type(exc).__name__}: {exc}"}
                    )
                )
                return messages

            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                emit(AgentEvent(EventType.DONE))
                return messages

            # Fix A3: run background-safe tool batches in parallel.
            def _build_anthropic_result(tc, payload, is_error):
                return {
                    "type": "tool_result",
                    "tool_use_id": tc._raw["id"],
                    "content": payload,
                    "is_error": is_error,
                }

            wrapped = [_ToolCall(tu["name"], tu["input"], tu) for tu in tool_uses]
            tool_results, stopped, _cancelled = _dispatch_tools_maybe_parallel(
                self.toolkit, self.executor, wrapped, emit, should_stop,
                _build_anthropic_result,
            )
            if stopped:
                emit(AgentEvent(EventType.DONE))
                return messages
            messages.append({"role": "user", "content": tool_results})

        else:
            if is_unlimited:
                return messages
            emit(
                AgentEvent(
                    EventType.THINKING, {"text": f"Reached max {max_iters} iterations."}
                )
            )
            emit(AgentEvent(EventType.DONE))
        return messages
