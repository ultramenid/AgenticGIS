"""Backend interface and the streaming event model the chat dock consumes.

A backend's ``send`` runs on a worker thread and reports progress by calling
``emit(AgentEvent(...))``. It must poll ``should_stop()`` between steps so the
Stop button can interrupt a long agent loop.
"""

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

    def validate(self) -> Optional[str]:
        """Return an error string if the backend is not usable yet
        (missing key, missing binary, ...), else ``None``."""
        return None

    def export_session_state(self) -> Dict[str, Any]:
        """Return backend-owned continuation state for the active chat session."""
        return {}

    def import_session_state(self, state: Dict[str, Any]) -> None:
        """Restore backend-owned continuation state for the active chat session."""
        return None


# ── Context compaction helpers ─────────────────────────────────────────────
_CONTEXT_WINDOWS = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
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
_COMPACTION_THRESHOLD = 0.90
_COMPACTION_KEEP_TAIL = 6   # keep this many recent messages verbatim
_COMPACTION_FIXED_OVERHEAD = 30_000  # reserved for system prompt + tool schemas


def context_window_for(model: str) -> int:
    """Return the context window (tokens) for the given model name."""
    m = (model or "").lower()
    for key, size in _CONTEXT_WINDOWS.items():
        if key in m:
            return size
    return _DEFAULT_CONTEXT_WINDOW


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
