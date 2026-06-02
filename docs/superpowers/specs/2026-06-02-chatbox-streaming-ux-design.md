# Chatbox Streaming UX — Total Improvement Design
**Date:** 2026-06-02  
**Scope:** message_bubble.py, chat_dock.py

## Problem Statement

1. **Bubble overlap** — `QLabel.setText()` during streaming doesn't force parent `QFrame` to recompute height; bubbles visually overlap the next widget.
2. **Raw text flash** — streaming uses `html.escape()`, finalize applies `_md_to_html()`. The jump at stream end is jarring.
3. **No fluid feel** — no streaming cursor, no progressive formatting, auto-scroll snaps even when user is reading above.

## Design: Approach C — Incremental Markdown + Full Polish

### 1. Streaming markdown split

Split `_md_to_html` into two functions:

- **`_md_inline(text)`** — applied during streaming. Handles: bold `**`, italic `*`, inline code `` ` ``, bullet points `- `. Does NOT process fenced code blocks (avoids mid-fence flicker when ` ``` ` hasn't closed yet).
- **`_md_to_html(text)`** — applied at finalize. Full processing: code blocks, headings, + all inline.

During streaming: `_md_inline(text) + "▋"` (cursor appended to raw HTML).  
At finalize: `_md_to_html(text)` (no cursor, full render).

### 2. Geometry fix

After every `setText` call in `set_streaming_text`:
```
self.text_label.updateGeometry()
self.adjustSize()
self.updateGeometry()
```
Forces parent VBoxLayout to recompute bubble height and eliminate overlap.

### 3. Smart auto-scroll

Track whether user has scrolled up during streaming via `_scroll_locked` flag.

- `_on_event(TEXT)` — scroll only if `not self._scroll_locked`
- `scroll.verticalScrollBar().valueChanged` → if user manually scrolled up → set `_scroll_locked = True`
- New bubble added (tool result, new turn) → reset `_scroll_locked = False`, scroll to bottom

### 4. Streaming cursor

`▋` appended to HTML during `set_streaming_text`, removed in `finalize_text`. Gives the "typing" feel without a separate widget.

## Files Changed

| File | Changes |
|---|---|
| `gui/message_bubble.py` | Add `_md_inline()`, update `set_streaming_text`, `finalize_text`, geometry calls |
| `gui/chat_dock.py` | Smart scroll logic, `_scroll_locked` flag, `_maybe_scroll_to_bottom()` |

## Success Criteria

- No bubble overlap during or after streaming
- Formatting appears progressively (bold/italic/code visible as tokens arrive)
- Code blocks render cleanly at stream end (no ` ``` ` artifact)
- Streaming cursor visible while tokens arrive, gone when done
- Manual scroll up during streaming is respected; auto-scroll resumes on next turn
