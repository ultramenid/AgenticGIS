"""Backend interface and the streaming event model the chat dock consumes.

A backend's ``send`` runs on a worker thread and reports progress by calling
``emit(AgentEvent(...))``. It must poll ``should_stop()`` between steps so the
Stop button can interrupt a long agent loop.
"""

import concurrent.futures
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
    CONNECTING = "connecting"  # HTTP transport establishing new TCP+TLS (data: {})
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

    def precompact_history(
        self,
        messages: List[Dict[str, Any]],
        should_stop: ShouldStopFn,
    ) -> List[Dict[str, Any]]:
        """Run compaction in the background (no UI events) and return the result.

        Called from a daemon thread after a turn completes so the next send
        starts with an already-compacted history, eliminating the inline
        compaction latency stall.

        Returns the input ``messages`` unchanged when:
        * the history is already below the compaction threshold,
        * ``should_stop()`` fires before completion,
        * this backend does not support HTTP-based compaction (e.g. CLI
          backends that lack ``_system_arg`` / ``_client`` / ``_tool_list``),
        * any exception occurs (failures are swallowed silently).

        The existing inline compaction path inside ``send()`` is the fallback
        and is never modified.
        """
        # Guard: CLI backends do not implement _system_arg / _tool_list /
        # _client, so compaction is not available for them.
        if not callable(getattr(self, "_system_arg", None)):
            return messages
        if not callable(getattr(self, "_tool_list", None)):
            return messages
        if not callable(getattr(self, "_client", None)):
            return messages

        if should_stop():
            return messages

        try:
            return self._compact_history(
                messages,
                emit=lambda _event: None,  # no UI events from background pass
                should_stop=should_stop,
            )
        except Exception:  # nosec B110
            return messages

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
            # Fix A2: use the cheaper compaction_model when set; fall back to chat model.
            model = self.config.get("compaction_model") or self.config.get("model")
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
# Entries are matched via substring search in insertion order, so longer /
# more-specific keys must come BEFORE shorter ones that share a prefix (e.g.
# "gpt-4o" before "gpt-4", "o4" before "o1"/"o3").
_CONTEXT_WINDOWS = {
    # Claude 3.x / 4.x families
    "claude": 200_000,
    # GPT-4o / GPT-4-turbo / GPT-4.1 — 128k or more; list before plain gpt-4
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_047_576,
    # o4 before o1/o3 so "o4-mini" doesn't fall through to o1
    "o4": 200_000,
    "o1": 200_000,
    "o3": 200_000,
    # Gemini context is 1 M tokens (capped to _MAX_EFFECTIVE_CONTEXT_WINDOW anyway)
    "gemini": 1_000_000,
    # Legacy / smaller models — intentionally after all modern variants
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
    "llama": 128_000,
    "mistral": 32_000,
    "deepseek": 128_000,
    "qwen": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000
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
# Maximum characters stored in conversation history for a single tool result.
# Keeping this small reduces per-turn token cost: this payload is re-sent on
# every subsequent request for the remainder of the session.
MAX_TOOL_RESULT_CHARS = 30_000

# Fix A3: tools that are safe to run concurrently (no QGIS-canvas side-effects,
# no shared mutable state). Do NOT import from core.toolkit — keeping this list
# here avoids a gui-importing dependency chain.
BACKGROUND_SAFE_TOOLS = {
    "analyze_layer",
    "create_chart",
    "get_layer_statistics",
    "web_fetch",
    "gee_status",
    "gee_dataset_info",
    "gee_add_layer",
    "gee_animation",
}

_VISUALIZATION_TOOLS = {
    "create_chart": "chart",
    "get_layer_statistics": "stats",
    "gee_animation": "gif",
}


# ── Fix A1: Elide stale tool results from history ─────────────────────────────

_ELIDE_PREVIEW_CHARS = 200  # characters of original payload kept as a preview


def _count_real_user_turns(messages):
    """Count user-role messages that are plain text (not tool-result carriers).

    Anthropic format: user messages whose ``content`` is a *list* of blocks
    containing only ``tool_result`` type entries are tool-result carriers and
    are NOT counted.  OpenAI format: ``{"role": "tool", ...}`` messages are
    never user turns.
    """
    count_turns = 0
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            # OpenAI tool-result message — never a real user turn
            continue
        if role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                # Anthropic: all blocks are tool_result → this is a result carrier
                if content and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                ):
                    continue
            # Plain text user message or mixed content → real turn
            count_turns += 1
    return count_turns


def _build_tool_use_id_to_name(messages):
    """Build a mapping of tool_use_id → tool name from assistant messages.

    Used to resolve the tool name when eliding Anthropic tool-result blocks
    (the name lives on the paired assistant ``tool_use`` block, not on the
    result block itself).
    """
    mapping = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                mapping[block["id"]] = block.get("name", "")
    return mapping


def elide_stale_tool_results(messages, keep_recent_user_turns=2):
    """Return a new messages list with old tool-result payloads replaced by stubs.

    Handles both Anthropic format (user messages whose content list contains
    ``{"type": "tool_result", ...}`` blocks) and OpenAI format
    (``{"role": "tool", ...}`` messages).

    Keeps tool results from the *most recent* ``keep_recent_user_turns`` real
    user turns intact.  Older results are replaced with a short stub that
    preserves the tool name, original payload length, and the first
    ``_ELIDE_PREVIEW_CHARS`` characters of the payload as a preview.

    The input list and its dicts are never mutated; the function is idempotent
    (stubs that are already elided pass through unchanged).
    """
    total_real_turns = _count_real_user_turns(messages)
    # Index boundary: real user turns beyond this threshold are "recent"
    keep_from_turn = total_real_turns - keep_recent_user_turns

    id_to_name = _build_tool_use_id_to_name(messages)

    result = []
    real_turn_index = 0  # counts real user turns seen so far

    for msg in messages:
        role = msg.get("role")

        # ── OpenAI tool-result message ────────────────────────────────────
        if role == "tool":
            payload = msg.get("content", "")
            # Already elided?
            if isinstance(payload, str) and payload.startswith("[tool result elided"):
                result.append(msg)
                continue
            if real_turn_index > keep_from_turn:
                # Recent — keep verbatim
                result.append(msg)
            else:
                # Stale — replace with stub
                name = msg.get("name", "")
                n = len(payload) if isinstance(payload, str) else len(str(payload))
                preview = (payload[:_ELIDE_PREVIEW_CHARS] if isinstance(payload, str) else "")
                stub = (
                    f"[tool result elided to save context — {name} returned {n} chars."
                    f" Re-run the tool if the data is needed again."
                    + (f" Preview: {preview}" if preview else "")
                    + "]"
                )
                new_msg = {**msg, "content": stub}
                result.append(new_msg)
            continue

        # ── Real user turn (plain text or mixed) ─────────────────────────
        if role == "user":
            content = msg.get("content")

            # Anthropic tool-result carrier: list of tool_result blocks
            if isinstance(content, list) and content and all(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            ):
                if real_turn_index > keep_from_turn:
                    # Recent — keep verbatim
                    result.append(msg)
                else:
                    # Stale — elide each block's content
                    new_blocks = []
                    for block in content:
                        block_content = block.get("content", "")
                        # Already elided?
                        if isinstance(block_content, str) and block_content.startswith(
                            "[tool result elided"
                        ):
                            new_blocks.append(block)
                            continue
                        tool_use_id = block.get("tool_use_id", "")
                        name = id_to_name.get(tool_use_id, "")
                        n = (
                            len(block_content)
                            if isinstance(block_content, str)
                            else len(str(block_content))
                        )
                        preview = (
                            block_content[:_ELIDE_PREVIEW_CHARS]
                            if isinstance(block_content, str)
                            else ""
                        )
                        stub = (
                            f"[tool result elided to save context"
                            + (f" — {name}" if name else "")
                            + f" returned {n} chars."
                            f" Re-run the tool if the data is needed again."
                            + (f" Preview: {preview}" if preview else "")
                            + "]"
                        )
                        new_blocks.append({**block, "content": stub})
                    result.append({**msg, "content": new_blocks})
                continue

            # Plain text (or mixed-content) real user turn — count it
            real_turn_index += 1
            result.append(msg)
            continue

        # ── All other messages (assistant, system, …) ─────────────────────
        result.append(msg)

    return result


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
        if len(payload) > MAX_TOOL_RESULT_CHARS:
            original_len = len(payload)
            payload = (
                payload[:MAX_TOOL_RESULT_CHARS]
                + f"\n... [tool output truncated at {MAX_TOOL_RESULT_CHARS} chars"
                f" — full result was {original_len} chars."
                " Re-run with a narrower query, a limit parameter, or a more"
                " specific tool if you need the missing data.]"
            )
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
    # Generic file download card: any tool returning a successful dict with a
    # file_path or download_path gets a download widget.
    if (
        isinstance(result, dict)
        and result.get("ok")
        and name not in _VISUALIZATION_TOOLS
        and (result.get("file_path") or result.get("download_path"))
    ):
        emit(AgentEvent(EventType.VISUALIZATION, {
            "type": "file",
            "data": result,
        }))
    return payload, is_error, is_cancelled, result


# ── Fix A3: parallel dispatch for background-safe tool batches ────────────────

class _ToolCall:
    """Lightweight wrapper to give both Anthropic and OpenAI tool entries a
    uniform ``name`` and ``input`` attribute for ``_dispatch_tools_maybe_parallel``."""

    __slots__ = ("name", "input", "_raw")

    def __init__(self, name, tool_input, raw):
        self.name = name
        self.input = tool_input
        self._raw = raw  # original dict, kept for build_result_entry


def _dispatch_tools_maybe_parallel(toolkit, executor, tool_calls, emit, should_stop,
                                   build_result_entry):
    """Dispatch a batch of tool calls, running them in parallel when safe.

    Parameters
    ----------
    tool_calls:
        List of ``_ToolCall`` instances.
    emit:
        ``AgentEvent`` callback.  NOT thread-safe, so a ``threading.Lock``
        wrapper is used for all calls inside worker threads.
    build_result_entry:
        Callable(tool_call, payload, is_error) → history dict; differs between
        Anthropic and OpenAI callers.

    Returns
    -------
    (tool_results, stopped, cancelled)
        ``tool_results`` is in original call order.
        ``stopped``/``cancelled`` are True when execution was interrupted.
    """
    if should_stop():
        return [], True, False

    n = len(tool_calls)

    # Run in parallel only if EVERY tool in the batch is background-safe.
    use_parallel = n >= 2 and all(tc.name in BACKGROUND_SAFE_TOOLS for tc in tool_calls)

    if not use_parallel:
        # Sequential path — unchanged semantics
        tool_results = []
        for tc in tool_calls:
            if should_stop():
                return tool_results, True, False
            payload, is_error, is_cancelled, _result = _dispatch_one_tool(
                toolkit, executor, tc.name, tc.input, emit, should_stop
            )
            if should_stop() or is_cancelled:
                return tool_results, True, is_cancelled
            tool_results.append(build_result_entry(tc, payload, is_error))
        return tool_results, False, False

    # Parallel path — wrap emit with a lock so concurrent threads don't
    # interleave event delivery.
    emit_lock = threading.Lock()

    def locked_emit(event):
        with emit_lock:
            emit(event)

    # Pre-allocate result slots so ordering matches the original call order.
    slots = [None] * n
    any_cancelled = threading.Event()

    max_workers = min(4, n)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for idx, tc in enumerate(tool_calls):
            if should_stop():
                # Do not submit further work; already-submitted futures run to
                # completion (they also check should_stop internally).
                break
            fut = pool.submit(
                _dispatch_one_tool,
                toolkit, executor, tc.name, tc.input, locked_emit, should_stop,
            )
            futures.append((idx, tc, fut))

        for idx, tc, fut in futures:
            try:
                payload, is_error, is_cancelled, _result = fut.result()
            except Exception as exc:
                payload = f"Tool error: {type(exc).__name__}: {exc}"
                is_error = True
                is_cancelled = False
            if is_cancelled:
                any_cancelled.set()
            slots[idx] = build_result_entry(tc, payload, is_error)

    if should_stop() or any_cancelled.is_set():
        return [s for s in slots if s is not None], True, any_cancelled.is_set()

    return slots, False, False
