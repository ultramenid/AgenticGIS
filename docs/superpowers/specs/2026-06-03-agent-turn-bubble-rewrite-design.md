# AgentTurnBubble Rewrite — Design Spec
**Date:** 2026-06-03
**Status:** Approved

---

## Goal

Replace the current `AgentTurnBubble` internals with a Claude Code terminal-style tool UI:
- Same-type tool calls grouped under one header with a live count
- Braille spinner per tool while running → ✓/! on completion
- Tree connectors (├─/└─) for sub-items
- Single-line streaming reasoning ticker above the tool groups
- Plain markdown prose response below — no cards, no borders

---

## Components

### 1. `ReasoningTicker`
A single fixed-height label rendered above the tool groups.

- Prefix: `▸`
- Font: JetBrains Mono 10px, italic
- Color: `#6f6f6f` (dim)
- Behaviour: appends reasoning chunks to an internal buffer; displays the last 100 characters on one line (left-truncated with `…` if overflowing)
- Visibility: shown as soon as the first reasoning chunk arrives; hidden (not deleted) when the first response text chunk arrives

### 2. `ToolGroupRow`
One widget per unique `tool_name` within a turn.

**Header line:** `● ToolName  (N)   [spinner or ✓/!]`
- `●` dot + spinner: amber `#d99a3c` while any sub-item is still running
- All done without error: dot + `✓` green `#5aa86f`
- Any error: dot + `!` red `#d05a5a`
- `(N)` count: dim `#6f6f6f`, updates as sub-items are added
- Spinner: braille cycle `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` at 80 ms via `QTimer`; stops on all-done

**Sub-items:** a `ToolSubItem` appended below the header for each individual call. Tree connectors recalculated on every append — all items get `├─` except the last which gets `└─`.

### 3. `ToolSubItem`
One line per individual tool call, identified by `tool_use_id`.

Layout (left to right):
```
  [connector]  [icon]  [key_label]    [json_suffix]
```
- `connector`: `├─` or `└─`, color `#2b2b2b`
- `icon`: braille spinner while running → `✓` (`#5aa86f`) or `!` (`#d05a5a`) on done
- `key_label`: brightest meaningful input field (see Key Label Logic below), color `#e8e8e8`, JetBrains Mono 10px
- `json_suffix`: full input as compact JSON, truncated to 60 chars, color `#6f6f6f`

**Key Label Logic** — extract the most meaningful field from `tool_input` dict, in priority order:
1. `path`, `file_path`, `filename`
2. `layer`, `layer_name`, `layer_id`
3. `query`, `sql`
4. `name`, `id`
5. Fallback: first value in the dict, stringified and truncated to 40 chars

### 4. `AgentTurnBubble` (rewritten)
The top-level container widget for one complete agent response turn.

**Internal layout (top to bottom):**
1. `ReasoningTicker` (hidden until first reasoning chunk)
2. Tool groups area: a `QVBoxLayout` holding `ToolGroupRow` widgets in insertion order
3. Response text area: `QLabel` with `setWordWrap(True)`, markdown rendered via existing `_md_to_html()` helper

**No cards, no border-left accents, no `QFrame` surfaces** — everything renders on the same `#1c1c1c` background as the chat panel.

---

## Public API (unchanged call sites)

```python
bubble.add_tool_call(tool_use_id: str, tool_name: str, tool_input: dict)
# Creates ToolGroupRow if tool_name is new; appends ToolSubItem; recalculates connectors.

bubble.tool_done(tool_use_id: str, result_text: str, is_error: bool = False)
# Finds ToolSubItem by id; stops its spinner; shows ✓ or !.
# Checks if all sub-items in the parent group are done → updates group header.

bubble.stream_reasoning(text_chunk: str)
# Appends to reasoning buffer; updates ReasoningTicker display.

bubble.stream_text(text_chunk: str)
# On first call: hides ReasoningTicker.
# Appends to response buffer; re-renders markdown label.

bubble.finalize()
# Stops all running spinners; marks any still-running tools as timed-out (! icon).
# Hides ReasoningTicker if still visible (covers tool-only turns with no response text).
# Renders final markdown for response area.
```

---

## Visual Layout

```
┌─────────────────────────────────────────────────────────────┐
│  ▸ considering layer boundaries to filter by extent...      │  ReasoningTicker
│                                                             │
│  ● read_layer  (3)                              ✓           │  ToolGroupRow
│  ├─  ✓  roads_2024    {"layer": "roads_2024"}               │
│  ├─  ✓  buildings     {"layer": "buildings"}                │
│  └─  ✓  parks         {"layer": "parks"}                    │
│                                                             │
│  ● run_query  (1)                               ⠿           │  ToolGroupRow (running)
│  └─  ⠿  SELECT * FROM roads...  {"query": "SE…"}            │
│                                                             │
│  The analysis found 142 road segments intersecting…         │  Response text
└─────────────────────────────────────────────────────────────┘
```

---

## Design Tokens (match existing palette)

| Token       | Hex       | Usage                              |
|-------------|-----------|------------------------------------|
| `_SURFACE`  | `#1c1c1c` | background (no card chrome)        |
| `_TEXT`     | `#e8e8e8` | key input label                    |
| `_TEXT_3`   | `#6f6f6f` | connectors, JSON suffix, count     |
| `_SUCCESS`  | `#5aa86f` | ✓ icon, done group dot             |
| `_WARN`     | `#d99a3c` | spinner dot, running group dot     |
| `_DANGER`   | `#d05a5a` | ! icon, error group dot            |
| `_BORDER`   | `#2b2b2b` | connector characters               |

Font: JetBrains Mono 10px for all tool rows. Response text uses the existing prose font.

---

## Files Changed

| File | Change |
|------|--------|
| `gui/agent_turn_bubble.py` | Full rewrite — `ThinkingBlock`, `ToolGroupRow`, `ToolSubItem`, `ReasoningTicker`, `AgentTurnBubble` |
| `gui/tool_result_widget.py` | Deleted — no longer used |

Call sites (`chat_dock.py`, `executor.py`) require no changes if the public API above is preserved.

---

## Out of Scope

- Collapsible tool groups (expand/collapse toggle) — not in reference design
- Tool result content display (the actual output text) — hidden by default, not shown in tree
- Changes to `message_bubble.py`, `ask_user_card.py`, or any other widget
