"""Backend interface and the streaming event model the chat dock consumes.

A backend's ``send`` runs on a worker thread and reports progress by calling
``emit(AgentEvent(...))``. It must poll ``should_stop()`` between steps so the
Stop button can interrupt a long agent loop.
"""

import json
import os
import threading

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable, Dict, List, Optional


class EventType:
    TEXT = "text"            # assistant text delta (data: {"text": str})
    THINKING = "thinking"    # status / reasoning note  (data: {"text": str})
    TOOL_USE = "tool_use"    # agent invoked a tool      (data: {"name", "input"})
    TOOL_RESULT = "tool_result"  # tool returned          (data: {"name", "result"})
    DONE = "done"            # turn finished
    VISUALIZATION = "visualization"  # data visualization (charts, stats) (data: {"type": str, "data": dict})
    ASK_USER = "ask_user"    # agent asks the user      (data: {"question", "options", "allow_free_text"})
    ERROR = "error"          # something failed          (data: {"error": str})
    COMPACTION = "compaction"  # history summarized to fit context window


@dataclass
class AgentEvent:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


# A backend receives these from the dock.
EmitFn = Callable[[AgentEvent], None]
ShouldStopFn = Callable[[], bool]


class AgentBackend(ABC):
    """Common contract so the dock widget is backend-agnostic."""

    #: Human-readable label shown in the dock status area.
    label = "agent"

    def __init__(self, config, toolkit, executor):
        self.config = config
        self.toolkit = toolkit
        self.executor = executor
        self._cached_tool_list = None
        self._active_client = None
        self._active_client_lock = threading.Lock()

    @abstractmethod
    def send(
        self,
        message: str,
        history: List[Dict[str, Any]],
        emit: EmitFn,
        should_stop: ShouldStopFn,
    ) -> List[Dict[str, Any]]:
        """Process one user turn.

        ``history`` is the prior conversation in a backend-defined shape;
        ``send`` returns the updated history to persist for the next turn.
        Streaming output is delivered through ``emit``.
        """

    def _provider(self):
        from . import providers
        pid = self.config.get("provider")
        return None if pid == "custom" else providers.get_provider(pid)

    def validate(self) -> Optional[str]:
        p = self._provider()
        key = (self.config.get("api_key") or os.environ.get(
            p["key_env"], "")) if p else self.config.get("custom_api_key")
        label = p["label"] if p else "Custom endpoint"
        if not key:
            return (
                f"No API key set for {label}. "
                f"Add one in Settings (or set {p['key_env'] if p else 'the provider key env'})."
            )
        return None

    def export_session_state(self) -> Dict[str, Any]:
        """Return backend-owned continuation state for the active chat session."""
        return {}

    def import_session_state(self, state: Dict[str, Any]) -> None:
        """Restore backend-owned continuation state for the active chat session."""
        return None

    def cancel_current_request(self) -> None:
        with self._active_client_lock:
            client = self._active_client
        if client is None:
            return
        try:
            client.cancel_current_request()
        except Exception:  # nosec B110
            pass

    def prewarm(self) -> None:
        """Optionally open network connections ahead of the first send to cut
        time-to-first-token. Default is a no-op (e.g. CLI backends have nothing
        to warm). HTTP-transport backends override this."""
        return None

    # Hook for shared compaction — must be overridden.
    def _system_arg(self):
        """Return the ``system`` value the LLM client expects.

        Anthropic backends return a list of blocks; OpenAI backends return a
        plain string.  Called by the shared ``_compact_history`` helper.
        """
        raise NotImplementedError

    def _compact_history(self, messages, emit, should_stop=None):
        """Summarize old messages and return a trimmed list.

        Keeps the KEEP_TAIL most recent messages verbatim. Everything older is
        replaced by a summary user/assistant pair so the model retains context
        without consuming the full window.
        """
        if len(messages) <= _COMPACTION_KEEP_TAIL:
            return messages
        head = messages[:-_COMPACTION_KEEP_TAIL]
        tail = messages[-_COMPACTION_KEEP_TAIL:]
        sum_messages = list(head) + [
            {
                "role": "user",
                "content": (
                    "Summarize the conversation above in bullet points. "
                    "Keep: layer IDs, key findings, tool results, decisions made. "
                    "Be concise — max 300 words."
                ),
            }
        ]
        try:
            client = self._active_client or self._client()
            model = self.config.get("model")
            content, _ = client.stream_message(
                model=model,
                max_tokens=1024,
                system=self._system_arg(),
                tools=self._tool_list(),
                messages=sum_messages,
                on_text=lambda _t: None,
                should_stop=should_stop or (lambda: False),
            )
            summary = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            ).strip()
        except Exception:
            return messages
        if not summary:
            return messages
        compacted = [
            {"role": "user", "content": f"[Earlier conversation summary]\n\n{summary}"},
            {
                "role": "assistant",
                "content": "Understood. Continuing with full context.",
            },
        ] + list(tail)
        emit(AgentEvent(EventType.COMPACTION, {}))
        return compacted


# ── Context compaction helpers ─────────────────────────────────────────────
_CONTEXT_WINDOWS = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
    "o1": 200_000,
    "o3": 200_000,
    "gemini": 200_000,
    "llama": 128_000,
    "mistral": 32_000,
    "qwen": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 128_000
_MAX_EFFECTIVE_CONTEXT_WINDOW = 100_000
_COMPACTION_THRESHOLD = 0.90
_COMPACTION_KEEP_TAIL = 6   # keep this many recent messages verbatim
_COMPACTION_FIXED_OVERHEAD = 30_000  # reserved for system prompt + tool schemas


def context_window_for(model: str) -> int:
    """Return the context window (tokens) for the given model name."""
    m = (model or "").lower()
    for key, size in _CONTEXT_WINDOWS.items():
        if key in m:
            return min(size, _MAX_EFFECTIVE_CONTEXT_WINDOW)
    return min(_DEFAULT_CONTEXT_WINDOW, _MAX_EFFECTIVE_CONTEXT_WINDOW)


def estimate_message_tokens(messages) -> int:
    """Rough token estimate for a messages list: total characters / 4."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    for key in ("text", "content"):
                        v = block.get(key, "")
                        total += len(v) if isinstance(v, str) else len(str(v))
    return total // 4


def should_compact(messages, model: str) -> bool:
    """Return True when estimated token usage exceeds the compaction threshold."""
    limit = context_window_for(model)
    estimated = estimate_message_tokens(messages) + _COMPACTION_FIXED_OVERHEAD
    return estimated >= int(limit * _COMPACTION_THRESHOLD)


def unlimited_iterations(max_iterations: Any) -> bool:
    """Return True when the agent tool loop should not have a numeric cap."""
    try:
        return int(max_iterations) <= 0
    except (TypeError, ValueError):
        return False


def agent_iteration_steps(max_iterations: Any):
    """Yield loop steps for the agent tool loop.

    Positive values are finite. Zero or negative values are intentionally
    unlimited; callers must still check their stop/cancel callback inside the
    loop.
    """
    if unlimited_iterations(max_iterations):
        return count()
    try:
        return range(int(max_iterations))
    except (TypeError, ValueError):
        return range(0)


MAX_TOKENS = 4096

_VISUALIZATION_TOOLS = {"create_chart": "chart", "get_layer_statistics": "stats"}


def _dispatch_one_tool(toolkit, executor, name, tool_input, emit, should_stop):
    from ..core import tools as tools_mod

    emit(AgentEvent(EventType.TOOL_USE, {"name": name, "input": tool_input}))
    result = None
    is_error = False
    is_cancelled = False
    try:
        result = tools_mod.dispatch(toolkit, executor, name, tool_input, should_stop=should_stop)
        if isinstance(result, dict):
            is_error = result.get("ok") is False
            is_cancelled = bool(result.get("cancelled"))
        else:
            is_error = True
        payload = json.dumps(result, default=str)
        if len(payload) > 200_000:
            payload = payload[:200_000] + "\n... [output truncated]"
    except Exception as exc:
        payload = f"Tool error: {type(exc).__name__}: {exc}"
        is_error = True
    emit(AgentEvent(EventType.TOOL_RESULT, {
        "name": name,
        "result": payload,
        "is_error": is_error,
        "cancelled": is_cancelled,
    }))
    if should_stop() or is_cancelled:
        return payload, is_error, is_cancelled, result
    if name in _VISUALIZATION_TOOLS and isinstance(result, dict) and result.get("ok"):
        emit(AgentEvent(EventType.VISUALIZATION, {
            "type": _VISUALIZATION_TOOLS[name],
            "data": result,
        }))
    return payload, is_error, is_cancelled, result
