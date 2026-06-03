# Brainstorming Protocol + ask_user Tool — Design

**Status:** Draft
**Date:** 2026-06-03
**Scope:** Add a first-class "brainstorm with the user" capability to AgenticGIS so the agent pauses and asks a structured clarifying question (with clickable options) when input is ambiguous or a tool result looks suspicious, instead of guessing and running the wrong analysis.

---

## 1. Problem

The current `DEFAULT_SYSTEM_PROMPT` (duplicated in `backends/api_backend.py:18-48` and `backends/openai_backend.py:15-45`) instructs the model to "act, then explain the result briefly". When the user is vague (e.g. *"analyse the data"*) or a precondition fails (no spatial index on a layer they're about to buffer, an analysis field with all-null values, a clip that returns zero features), the model has no way to surface a question back to the user. The tool surface is read/write-only and there is no UI for back-and-forth clarification.

Result: the agent silently picks a default, runs an analysis the user did not intend, and the user has to re-prompt. For a "spatial data analyst" persona this is the wrong default.

## 2. Goal

Give the agent a structured way to ask the user a clarifying question and wait for an answer before continuing. The user should be able to answer with one click (pick an option) or type a free-text reply.

**Non-goals:**
- No persistent "clarification state" beyond the active turn — when the user picks an option, the answer is sent back as a normal user turn and the agent loop continues.
- No new backend wiring for the CLI subprocess in this iteration (it streams whatever the local CLI emits, so the local CLI would need its own `ask_user` tool implementation; tracked as a follow-up).
- No new dependency on QGIS APIs that aren't already used.

## 3. Design summary

Three coordinated changes:

1. **New tool `ask_user`** declared in `core/tools.py` (auto-surfaces to the Anthropic + OpenAI-compatible API backends, plus the MCP bridge).
2. **New event type `ASK_USER`** in `backends/base.py:EventType` plus a dock-rendered card in `gui/chat_dock.py` — a popover above the input containing the question, the options as buttons in a row, and a free-text field below. When the user responds, the dock sends a normal user turn carrying the chosen label (or the typed text).
3. **A "Brainstorming Protocol" section** in both `DEFAULT_SYSTEM_PROMPT` copies: a one-line rule ("When in doubt, call `ask_user`") + a short trigger list naming the conditions that should always trigger a question. The trigger list is short (4-6 items) on purpose so the model has unambiguous cues without the prompt becoming a wall of text.

## 4. Tool spec: `ask_user`

Added to `core/tools.py:TOOL_SPECS`:

```python
{
    "name": "ask_user",
    "method": "ask_user",  # dispatched through QgisToolkit
    "description": (
        "Pause and ask the user a clarifying question. Use proactively when "
        "the request is ambiguous (e.g. no analysis field named, no CRS "
        "target, no comparison layer) and reactively when a tool result "
        "looks suspicious (no spatial index, empty result, schema mismatch, "
        "out-of-range value). Wait for the user's reply before continuing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The clarifying question, in the user's working language.",
            },
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label"],
                },
                "description": "2-4 options. The first is recommended.",
            },
            "allow_free_text": {
                "type": "boolean",
                "default": True,
                "description": "If true, the user can type a reply instead of picking an option.",
            },
        },
        "required": ["question", "options"],
    },
}
```

### 4.1 Toolkit implementation

`core/toolkit.py` gets a new method `ask_user(question, options, allow_free_text=True)` whose body:

1. Marshals to the main thread (every other toolkit method assumes main thread; `core.tools.dispatch` already wraps in `executor.run_sync`).
2. Emits an `AgentEvent(EventType.ASK_USER, {"question": ..., "options": ..., "allow_free_text": ...})` via a callback the dock registers on the toolkit. The callback is set on the toolkit by the dock after construction (same pattern as `_request_cancel`).
3. Blocks on a `threading.Event` until the dock fires the reply.
4. Returns `{"choice": "<label>", "free_text": "<text or null>"}`. If the user picked option 1, `choice` is the label and `free_text` is null. If they typed, `choice` is null and `free_text` is the typed string.

The blocking event has a cooperative cancel hook into the existing `_CancellationRegistry` so the Stop button can interrupt a long wait without freezing the dock.

### 4.2 Why a new method on the toolkit (vs. a backend-side shim)

The dock already owns the user-facing UI and the worker thread that drives the backend. Putting the wait on the toolkit means:
- The event is fired from the main thread, so the dock can build the popover synchronously without cross-thread Qt rules.
- The blocking `threading.Event` is straightforward to make cancellable.
- The same method serves all three backends (Anthropic, OpenAI, MCP) because they all dispatch through `core.tools.dispatch`.

## 5. Event + dock UI

### 5.1 Event

`backends/base.py:EventType` gets one new constant:

```python
ASK_USER = "ask_user"   # agent asks the user    (data: {"question", "options", "allow_free_text"})
```

Backends do not need to change — they forward the toolkit's emitted events through their existing `emit(AgentEvent(...))` path. Anthropic and OpenAI HTTP clients don't need to know about it; they just stream the tool result and the dock renders the popover.

### 5.2 Dock popover

A new `AskUserCard` widget in `gui/ask_user_card.py`:

- Header: "Agent needs input" label.
- Question rendered as bold text.
- Options rendered as a horizontal row of `QToolButton` instances. Clicking a button calls back into the dock with `{"choice": label, "free_text": None}`.
- A small `QLineEdit` below with a "Send" button — only shown when `allow_free_text` is true. Submitting sends `{"choice": None, "free_text": text}`.
- The card lives in a `QWidget` floated just above the input bar (the dock's `_input_row`), positioned with a `QLayout` on the bottom of the transcript container, so it appears in-context but does not push transcript content up.

### 5.3 Dock wiring

In `gui/chat_dock.py`:

- New method `_show_ask_user(question, options, allow_free_text)`:
  1. Creates an `AskUserCard`.
  2. Connects the card's `submitted = lambda payload: ...` signal to a new internal method `_resolve_ask_user(payload)`.
  3. Inserts the card into the dock above the input row.
  4. Records the pending `threading.Event` and the `payload_slot` on the dock so `_resolve_ask_user` can find them.
- New method `_resolve_ask_user(payload)`:
  1. Stops the typing indicator (if visible) and removes the popover.
  2. Sets the wait event with the payload, so the blocked `ask_user` call on the toolkit thread unblocks.
  3. Echoes the chosen label (or the typed text) as a user message in the transcript so the user has a record of what they answered.
- The popover and the typing indicator are mutually exclusive (the popover IS the "agent is waiting" state, so typing is hidden when the popover is up).
- Cancellable: if the user clicks Stop while the popover is up, the cancellation registry fires the event with `{"choice": None, "free_text": null, "cancelled": True}` and the popover closes with a status line "Cancelled".

## 6. System prompt section

Added to both `backends/api_backend.py:DEFAULT_SYSTEM_PROMPT` and `backends/openai_backend.py:DEFAULT_SYSTEM_PROMPT`, appended after the existing "Rules" block:

```
Brainstorming protocol:
- When in doubt, call `ask_user(question, options, allow_free_text=True)`
  instead of guessing. Always include 2-4 options; mark the first as recommended
  in its description.

Triggers — brainstorm when you see any of these:
- A vector layer has no spatial index (you noticed it in get_layer_summary or
  a slow operation) and the user asked for a spatial operation.
- The user did not name a field, CRS, comparison layer, or time window that
  the analysis needs.
- A tool result is empty / all zeros / zero features where the user clearly
  expected non-empty data.
- Schema or CRS mismatch between two layers you're about to join, clip, or
  overlay.
- A value is out of the range you expected (e.g. population count > 1e9,
  density = 0, year = 1900).

When the user picks an option, treat their choice as the new instruction and
proceed. Do not re-ask the same question.
```

The trigger list is short on purpose. The model already has tools to detect each condition; the list is the *cue to use `ask_user` rather than improvise*.

## 7. Files touched

- `core/tools.py` — add the `ask_user` spec to `TOOL_SPECS`. No other change; `anthropic_tool_list()` and `TOOL_BY_NAME` rebuild automatically.
- `core/toolkit.py` — add `ask_user(question, options, allow_free_text)` method + a setter for the emit callback.
- `backends/base.py` — add `EventType.ASK_USER`.
- `gui/ask_user_card.py` — new file, the popover widget.
- `gui/chat_dock.py` — store the toolkit emit callback on construction, add `_show_ask_user` / `_resolve_ask_user`, insert the popover above the input row, hide typing when the popover is up.
- `backends/api_backend.py` and `backends/openai_backend.py` — add the prompt section to `DEFAULT_SYSTEM_PROMPT`. (The `OPENAI_HTTP` and `ANTHROPIC_HTTP` clients do not need changes.)
- `plugin.py` — set the toolkit's `set_ask_user_emitter(...)` from the dock's `_ensure_dock` method (the dock already knows the toolkit via the `iface` reference and the plugin's `toolkit` attribute). The emitter routes the toolkit's `ASK_USER` events into `dock._show_ask_user` and the dock's user-input slot routes back into the toolkit's wait event.

## 8. Error handling

- The toolkit method runs on the main thread. If the dock is shutting down while the event is pending, the event fires with `{"cancelled": True}` and the agent loop sees a cancel marker in the tool result.
- The dock stores the pending event in `self._pending_ask_user_event`. If `_clear()` is called (the user hits the dock's "Clear" button), any pending event fires with `cancelled=True` so the agent loop unblocks.
- If the model calls `ask_user` with fewer than 2 options or more than 4, the toolkit returns a tool error `"options must have 2-4 items"` and the agent loop continues. The dock does not render anything.
- If `ask_user` is called recursively (shouldn't happen, but the model can do anything), the second call returns a tool error `"already waiting for user input"`.

## 9. Testing

- `tests/test_tools.py` — extend to assert `ask_user` is in `TOOL_BY_NAME` and `anthropic_tool_list()`.
- `tests/test_ask_user.py` (new) — unit-test the toolkit's `ask_user` method:
  1. Registers a fake emit callback, calls the method, fires the wait event with a payload, asserts the return value matches.
  2. Tests cancellation: the stop callback fires the event with `cancelled=True`; the method returns the cancellation marker.
  3. Tests option-count validation: 0, 1, 5 options all return a tool error string.
  4. Tests recursive guard: a second concurrent call returns the "already waiting" error.
- Manual / integration: open the dock in QGIS, send a prompt like *"analyse the data"*, confirm the popover appears with 2-4 options, click one, confirm the transcript shows the choice and the agent continues. Repeat with a known-broken case (a layer with no spatial index + a buffer request) and confirm the agent offers to create the index.

## 10. Open questions / follow-ups

- The CLI subprocess backend (`backends/cli_backend.py`) does not consume `TOOL_SPECS` — it streams whatever the local CLI emits. If we want `ask_user` parity there, the CLI sub-tool would need its own implementation that talks to the same dock handler over the MCP bridge. Out of scope for this iteration; documented as a follow-up.
- Long-term: the `Brainstorming Protocol` section could move to its own module (e.g. `core/persona.py`) and be shared by all backends via a constant, removing the duplicate `DEFAULT_SYSTEM_PROMPT` strings. Defer until a third backend needs the same persona.
