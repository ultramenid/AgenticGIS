"""Backend interface and the streaming event model the chat dock consumes.

A backend's ``send`` runs on a worker thread and reports progress by calling
``emit(AgentEvent(...))``. It must poll ``should_stop()`` between steps so the
Stop button can interrupt a long agent loop.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class EventType:
    TEXT = "text"            # assistant text delta (data: {"text": str})
    THINKING = "thinking"    # status / reasoning note  (data: {"text": str})
    TOOL_USE = "tool_use"    # agent invoked a tool      (data: {"name", "input"})
    TOOL_RESULT = "tool_result"  # tool returned          (data: {"name", "result"})
    DONE = "done"            # turn finished
    VISUALIZATION = "visualization"  # data visualization (charts, stats) (data: {"type": str, "data": dict})
    ERROR = "error"          # something failed          (data: {"error": str})


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
