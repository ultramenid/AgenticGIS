# Chatbox Revamp Design

**Date:** 2026-06-03  
**Scope:** `gui/` — chat_dock.py, agent_turn_bubble.py, message_bubble.py, tool_call_bubble.py, tool_result_widget.py, typing_indicator.py

---

## Goal

Revamp the in-QGIS chatbox from "minimal chat app" to **developer terminal panel** aesthetic. Surface LLM internals (thinking, tool calls, summaries) in the transcript rather than hiding them in a status bar.

---

## 1. Visual Language

**Direction:** Hard-edged terminal card style.

- Keep dark palette (`#161616` surface, `#1e1e1e` input bg, `#2e2e2e` border)
- Reduce border-radius from 12px → 4px on agent cards and tool rows
- Left-border accent strips (2px) encode state: `#f0a500` running, `#22c55e` success, `#ef4444` error
- Monospace font (`SF Mono` / `Consolas`) for all metadata: tool names, timestamps, stats
- User bubble: right-aligned pill, radius stays rounded (12px), no change in shape
- Agent turn: full-width card, hard edges, left-border accent

---

## 2. Thinking State Block (new widget)

**Trigger:** `EventType.THINKING`  
**Widget:** `ThinkingBlock` (new class in `agent_turn_bubble.py`)

Behavior:
- Appears at the top of the `AgentTurnBubble` before any text or tools
- Shows dim italic text: `thinking…` with animated dots
- If `EventType.THINKING` data includes `{"text": str}`, stream that text into the block
- When `EventType.TEXT` begins, the block collapses automatically (height animates to a 1-line summary: `▸ Thought for Xs`)
- Collapsed block is clickable to re-expand
- Color: `_TEXT_3` (`#707070`) — visually subordinate to main response

---

## 3. Tool Call Rows (revamp `ToolRowWidget`)

Current: compact single-line, animated dots, monospace name, collapsible details.

Changes:
- Add 2px left-border accent (amber → green/red on completion)
- Tool name shown as `function_name()` syntax in monospace
- Status label: `running` → `done (Xms)` or `error` on completion
- Expanded details: args as formatted JSON, result as truncated pre-formatted block
- After all tools in a turn complete: a summary chip is appended above the text area: `N tools · Xms total`

---

## 4. Agent Turn Bubble (revamp `AgentTurnBubble`)

- Container: `QFrame` with `border-left: 2px solid #2e2e2e`, border-radius 4px, `background: #1a1a1a`
- Header row: left-aligned `AgenticGIS` label (monospace, dim) + right-aligned turn counter / timestamp chip
- ThinkingBlock section (if thinking events received)
- Tool section (existing ToolRowWidget rows, max 3 visible, expand toggle)
- Tool summary chip after tools complete
- Text area: streaming with blinking `|` cursor; cursor removed on DONE
- No avatar, no speech bubble tail

---

## 5. Input Area Polish

- Input: monospace font, 1px border, square-ish corners (radius 6px)
- Send button: `→` arrow, 28×28px, border 1px
- Stop button: `■` square
- Model name chip: left side of input row, dim monospace text (e.g. `claude-3-5-sonnet`)
- Placeholder text: `Ask AgenticGIS...`

---

## 6. Typing Indicator (minor revamp)

- Replace wave dots with a single blinking `_` cursor character (terminal style)
- Keep the `AgenticGIS` prefix label

---

## Architecture

All changes stay within `gui/`. No backend changes required.

| File | Change |
|------|--------|
| `agent_turn_bubble.py` | Add `ThinkingBlock`, revamp `ToolRowWidget` borders + summary chip, revamp container style |
| `chat_dock.py` | Feed `THINKING` event text to `ThinkingBlock`; add model chip to input row |
| `message_bubble.py` | Minor: adjust user bubble border-radius, keep core logic |
| `typing_indicator.py` | Replace wave dots with blinking cursor |
| `tool_result_widget.py` | Align color tokens, add left-border accent |

---

## Out of Scope

- Backend changes (EventType, executor, backends)
- New event types
- Settings dialog changes
- Chart / stats widget changes
