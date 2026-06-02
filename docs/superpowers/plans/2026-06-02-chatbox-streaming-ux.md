# Chatbox Streaming UX — Total Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AgenticGIS chatbox fluid like Claude — progressive markdown during streaming, no raw-text flash, no bubble overlap, and smart auto-scroll that respects manual scrolling.

**Architecture:** Split markdown rendering into a fast inline-only path (`_md_inline`) for streaming and the full `_md_to_html` for finalization. Add a streaming cursor `▋` appended during streaming, removed at finalize. Fix geometry propagation in `MessageBubble` so the parent VBoxLayout recomputes height on every token. Add smart scroll in `ChatDock` that detects user-initiated scroll and stops auto-scrolling until the next turn.

**Tech Stack:** PyQt5 (via `qgis.PyQt`), Python 3.9+, stdlib `re` + `html`

---

## File Map

| File | What changes |
|---|---|
| `gui/message_bubble.py` | Add `_md_inline()`, update `set_streaming_text` + `finalize_text` + geometry calls |
| `gui/chat_dock.py` | Add `_scroll_locked`, `_programmatic_scroll`, `_on_scroll_changed`, `_maybe_scroll_to_bottom`; update `_on_send` and `_on_event` |

---

### Task 1: Add `_md_inline()` to message_bubble.py

**Files:**
- Modify: `gui/message_bubble.py` (add new function after `_md_to_html`)

`_md_inline` applies only inline patterns — no fenced code blocks. This prevents the ` ``` ` artifact that appears when a code fence hasn't closed yet during streaming.

- [ ] **Step 1: Add `_md_inline` function**

Open `gui/message_bubble.py`. After the closing `return safe` of `_md_to_html` (around line 117), add:

```python
def _md_inline(text: str) -> str:
    """Streaming path — inline markdown only, no fenced code blocks.

    Applies bold, italic, inline code, and bullet points.
    Fenced code blocks (``` ... ```) are intentionally skipped to avoid
    showing a half-rendered fence while the closing ``` hasn't arrived yet.
    """
    safe = html.escape(text)

    # Bullet list items
    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: f'<div style="padding-left:12px; color:{_TEXT};">• {m.group(1)}</div>',
        safe,
    )

    # Inline code (backtick)
    safe = re.sub(
        r"`([^`\n]+)`",
        lambda m: (
            f'<code style="background:{_SURFACE}; color:{_SUCCESS}; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:12px;">{m.group(1)}</code>'
        ),
        safe,
    )

    # Bold then italic (order matters)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    safe = safe.replace("\n", "<br>")
    return safe
```

- [ ] **Step 2: Manual verify — function exists**

In QGIS Python console:
```python
import importlib, agenticgis.gui.message_bubble as m
importlib.reload(m)
print(m._md_inline("**bold** and `code` and *italic*"))
# Expected: HTML with <b>, <code>, <i> tags, no raw asterisks
```

---

### Task 2: Update `set_streaming_text` with inline markdown + cursor + geometry fix

**Files:**
- Modify: `gui/message_bubble.py` — `MessageBubble.set_streaming_text`

- [ ] **Step 1: Replace `set_streaming_text` in `MessageBubble`**

Find (around line 223):
```python
    def set_streaming_text(self, text: str):
        """Fast path during streaming — plain escaped text, no markdown."""
        self.text = text
        self.text_label.setText(html.escape(text))
```

Replace with:
```python
    def set_streaming_text(self, text: str):
        """Streaming path — inline markdown + cursor, geometry updated each token."""
        self.text = text
        cursor = f'<span style="color:{_TEXT_2};">▋</span>'
        self.text_label.setText(_md_inline(text) + cursor)
        # Force the parent VBoxLayout to recompute this bubble's height so it
        # never visually overlaps the widget below it.
        self.text_label.updateGeometry()
        self.adjustSize()
        self.updateGeometry()
```

- [ ] **Step 2: Manual verify — cursor visible during streaming**

Send a message in QGIS chat. While the agent is streaming, the bubble should show:
- Formatted bold/italic/inline-code as tokens arrive
- A `▋` cursor blinking at the end of the text
- No bubble overlapping the widget below it

---

### Task 3: Update `finalize_text` to remove cursor and apply full markdown

**Files:**
- Modify: `gui/message_bubble.py` — `MessageBubble.finalize_text`

- [ ] **Step 1: Replace `finalize_text` in `MessageBubble`**

Find (around line 228):
```python
    def finalize_text(self, text: str):
        """Called when stream ends — applies markdown. No re-animation (avoids flicker)."""
        self.text = text
        if not self.is_user and not self.is_tool and not self.is_error:
            self.text_label.setText(_md_to_html(text))
        else:
            self.text_label.setText(html.escape(text))
```

Replace with:
```python
    def finalize_text(self, text: str):
        """Stream end — full markdown render, cursor removed, geometry updated."""
        self.text = text
        if not self.is_user and not self.is_tool and not self.is_error:
            self.text_label.setText(_md_to_html(text))
        else:
            self.text_label.setText(html.escape(text))
        self.text_label.updateGeometry()
        self.adjustSize()
        self.updateGeometry()
```

- [ ] **Step 2: Manual verify — cursor gone, code blocks render**

After streaming completes:
- `▋` cursor must be gone
- Fenced code blocks must render as styled `<pre>` blocks
- Headings (`#`, `##`, `###`) must render with larger/bold text
- Bubble height must match content exactly (no clipping, no overlap below)

---

### Task 4: Add smart auto-scroll to ChatDock

**Files:**
- Modify: `gui/chat_dock.py` — `__init__`, `_build_ui`, new methods `_on_scroll_changed` + `_maybe_scroll_to_bottom`, update `_on_send` + `_on_event`

This task adds:
- `_scroll_locked` — True when user has scrolled up during active streaming
- `_programmatic_scroll` — guard flag so our own `_scroll_to_bottom` calls don't trigger the lock
- `_on_scroll_changed` — connected to `verticalScrollBar().valueChanged`
- `_maybe_scroll_to_bottom` — scrolls only if not locked

- [ ] **Step 1: Add flags to `__init__`**

Find in `__init__` (around line 84):
```python
        self._current_bubble_container = None
        self._current_text = ""
        self._build_ui()
```

Replace with:
```python
        self._current_bubble_container = None
        self._current_text = ""
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._build_ui()
```

- [ ] **Step 2: Connect scroll signal in `_build_ui`**

Find (around line 194):
```python
        self.scroll.setWidget(self.transcript_widget)
        layout.addWidget(self.scroll, 1)
```

Replace with:
```python
        self.scroll.setWidget(self.transcript_widget)
        layout.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
```

- [ ] **Step 3: Add `_on_scroll_changed` and `_maybe_scroll_to_bottom` methods**

After `_scroll_to_bottom` (around line 307), add:

```python
    def _on_scroll_changed(self, value):
        """Detect user-initiated scroll during streaming and lock auto-scroll."""
        if self._programmatic_scroll or not self._streaming:
            return
        vs = self.scroll.verticalScrollBar()
        if vs.maximum() > 0 and value < vs.maximum() - 60:
            self._scroll_locked = True
        else:
            # Scrolled back to bottom — re-enable auto-scroll
            self._scroll_locked = False

    def _maybe_scroll_to_bottom(self):
        """Scroll to bottom only if the user hasn't manually scrolled up."""
        if not self._scroll_locked:
            self._scroll_to_bottom()
```

- [ ] **Step 4: Guard `_scroll_to_bottom` with programmatic flag**

Find (around line 305):
```python
    def _scroll_to_bottom(self):
        vs = self.scroll.verticalScrollBar()
        vs.setValue(vs.maximum())
```

Replace with:
```python
    def _scroll_to_bottom(self):
        self._programmatic_scroll = True
        vs = self.scroll.verticalScrollBar()
        vs.setValue(vs.maximum())
        self._programmatic_scroll = False
```

- [ ] **Step 5: Reset lock on new send**

Find in `_on_send` (around line 384):
```python
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._current_bubble_container = None
```

Replace with:
```python
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._current_bubble_container = None
        self._scroll_locked = False
```

- [ ] **Step 6: Use `_maybe_scroll_to_bottom` during streaming**

Find in `_on_event` TEXT branch (around line 426):
```python
                self._current_bubble_container.set_streaming_text(self._current_text)
                self._scroll_to_bottom()
```

Replace with:
```python
                self._current_bubble_container.set_streaming_text(self._current_text)
                self._maybe_scroll_to_bottom()
```

- [ ] **Step 7: Manual verify — smart scroll**

1. Send a long-form request (e.g. "list all QGIS processing algorithms").
2. While the agent streams a long response, scroll up manually.
3. Auto-scroll must stop — text continues streaming but viewport stays where you scrolled.
4. Scroll back to bottom — auto-scroll resumes.
5. Send a new message — scroll snaps to bottom again.

---

### Task 5: Final integration verify

- [ ] **Step 1: Full streaming flow**

Send: "Write a Python code example and explain it with bold headers"

Expected end state:
- While streaming: bold text appears progressively, inline code formatted, `▋` cursor at end
- After streaming: fenced code block renders as styled `<pre>`, cursor gone, no overlap

- [ ] **Step 2: Tool use flow**

Send: "List my layers"

Expected:
- Typing indicator → tool result widget → typing indicator → agent text bubble
- No bubbles overlap each other
- Smart scroll works across tool result widgets too

- [ ] **Step 3: Error and edge cases**

- Empty response: bubble should not show cursor (no text → no bubble created)
- Stop mid-stream: `_finish_streaming()` is called → cursor removed, markdown applied
- Clear chat: `_scroll_locked` resets to False (confirmed in `_clear` — already resets `_current_bubble_container`)

- [ ] **Step 4: Add `_scroll_locked = False` reset to `_clear`**

Find in `_clear` (around line 364):
```python
        self._typing_widget = None
        self._current_bubble_container = None
        self._current_text = ""
```

Replace with:
```python
        self._typing_widget = None
        self._current_bubble_container = None
        self._current_text = ""
        self._scroll_locked = False
```
