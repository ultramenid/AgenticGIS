# AgentTurnBubble Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `gui/agent_turn_bubble.py` with a Claude Code terminal-style tool UI — same-type tool calls grouped under one `● ToolName (N)` header with braille spinners, tree connectors, and a single-line reasoning ticker replacing `ThinkingBlock`.

**Architecture:** Four new classes replace the old internals. `ReasoningTicker` streams LLM thinking as a single dim italic line. `ToolSubItem` is one tool call row with a braille spinner → ✓/!. `ToolGroupRow` groups same-name calls under a header dot. The rewritten `AgentTurnBubble` orchestrates them. All existing call sites in `chat_dock.py` work unchanged via backward-compat shims.

**Tech Stack:** PyQt5 (QWidget, QLabel, QTimer, QHBoxLayout, QVBoxLayout, QFrame), existing `_md_to_html` helper from `message_bubble.py`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `gui/agent_turn_bubble.py` | Full rewrite | `ReasoningTicker`, `ToolSubItem`, `ToolGroupRow`, `AgentTurnBubble` |
| `gui/tool_result_widget.py` | Delete | Unused — no importers |
| `dev/test_reasoning_ticker.py` | Create | Visual test for ReasoningTicker |
| `dev/test_tool_sub_item.py` | Create | Visual test for ToolSubItem |
| `dev/test_tool_group_row.py` | Create | Visual test for ToolGroupRow |
| `dev/test_agent_turn_bubble_rewrite.py` | Create | Integration test for full bubble |

`chat_dock.py` — **no changes required**. Backward-compat shims handle all existing call patterns.

---

## Task 1: Module header + `ReasoningTicker`

**Files:**
- Modify: `gui/agent_turn_bubble.py`
- Create: `dev/test_reasoning_ticker.py`

- [ ] **Step 1: Write visual test**

Create `dev/test_reasoning_ticker.py`:

```python
import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ReasoningTicker

win = QWidget()
win.setWindowTitle("ReasoningTicker test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 60)
lay = QVBoxLayout(win)
ticker = ReasoningTicker()
lay.addWidget(ticker)

chunks = ["considering ", "layer ", "boundaries ", "to filter ", "by spatial ", "extent…"]
idx = [0]

def send():
    if idx[0] < len(chunks):
        ticker.append(chunks[idx[0]])
        idx[0] += 1

t = QTimer()
t.setInterval(400)
t.timeout.connect(send)
t.start()

win.show()
sys.exit(app.exec_())
```

- [ ] **Step 2: Run — expect ImportError**

```bash
cd /Users/muhammadalichamdan/Documents/Development/AgenticGis
python dev/test_reasoning_ticker.py
```

Expected: `ImportError: cannot import name 'ReasoningTicker'`

- [ ] **Step 3: Replace module header and add `ReasoningTicker`**

Replace the entire file content of `gui/agent_turn_bubble.py` with:

```python
"""AgentTurnBubble — one widget per complete agent response turn.

Reasoning ticker streams LLM thinking in one line above grouped tool calls.
Tool calls group by name with braille spinners → ✓/! on completion.
"""

import html as _html
import json

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .message_bubble import _md_to_html

# Design tokens
_CANVAS      = "#141414"
_SURFACE     = "#1c1c1c"
_SURFACE_2   = "#232323"
_BORDER      = "#2b2b2b"
_BORDER_SOFT = "#222222"
_TEXT        = "#e8e8e8"
_TEXT_2      = "#9a9a9a"
_TEXT_3      = "#6f6f6f"
_TEXT_4      = "#4a4a4a"
_ACCENT      = "#e8e8e8"
_ACCENT_DIM  = "#9a9a9a"
_ACCENT_HOV  = "#ffffff"
_WARN        = "#d99a3c"
_SUCCESS     = "#5aa86f"
_DANGER      = "#d05a5a"

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
QWIDGETSIZE_MAX = 16777215


class ReasoningTicker(QWidget):
    """Single-line streaming reasoning display. Shows last 100 chars of LLM thinking."""

    _MAX_CHARS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.Monospace)
        mono.setItalic(True)

        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(12, 2, 12, 2)
        hbox.setSpacing(4)

        prefix = QLabel("▸")
        prefix.setFont(mono)
        prefix.setStyleSheet(f"color:{_TEXT_3}; background:transparent;")
        hbox.addWidget(prefix)

        self._lbl = QLabel("")
        self._lbl.setFont(mono)
        self._lbl.setStyleSheet(
            f"color:{_TEXT_3}; background:transparent; font-style:italic;"
        )
        self._lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hbox.addWidget(self._lbl)

    def append(self, text_chunk: str) -> None:
        """Append a streaming delta and update the single-line display."""
        if not text_chunk:
            return
        self._buffer += text_chunk
        self._render()
        if not self.isVisible():
            self.setVisible(True)

    def set_full(self, text: str) -> None:
        """Replace buffer entirely (for cumulative set_thinking_text calls)."""
        self._buffer = text or ""
        self._render()
        if self._buffer and not self.isVisible():
            self.setVisible(True)

    def hide_ticker(self) -> None:
        self.setVisible(False)

    def _render(self) -> None:
        display = self._buffer
        if len(display) > self._MAX_CHARS:
            display = "…" + display[-self._MAX_CHARS:]
        display = display.replace("\n", " ").replace("\r", "")
        self._lbl.setText(_html.escape(display))
```

At this point the file ends after `ReasoningTicker`. The remaining classes (ToolSubItem, ToolGroupRow, AgentTurnBubble) will be added in subsequent tasks.

- [ ] **Step 4: Run test — expect NameError on missing classes (not ImportError)**

```bash
python dev/test_reasoning_ticker.py
```

Expected: window opens, ticker text appears and scrolls left with each chunk arrival, showing `▸ considering layer boundaries…` updating in place.

- [ ] **Step 5: Commit**

```bash
git add gui/agent_turn_bubble.py dev/test_reasoning_ticker.py
git commit -m "feat: add ReasoningTicker — single-line streaming LLM reasoning display"
```

---

## Task 2: `ToolSubItem`

**Files:**
- Modify: `gui/agent_turn_bubble.py` (append class after `ReasoningTicker`)
- Create: `dev/test_tool_sub_item.py`

- [ ] **Step 1: Write visual test**

Create `dev/test_tool_sub_item.py`:

```python
import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ToolSubItem

win = QWidget()
win.setWindowTitle("ToolSubItem test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 100)
lay = QVBoxLayout(win)

item1 = ToolSubItem({"layer": "roads_2024"}, group=None, is_last=False)
item2 = ToolSubItem({"layer": "buildings"}, group=None, is_last=True)
lay.addWidget(item1)
lay.addWidget(item2)

QTimer.singleShot(1500, lambda: item1.mark_done(is_error=False))
QTimer.singleShot(2500, lambda: item2.mark_done(is_error=True))

win.show()
sys.exit(app.exec_())
```

- [ ] **Step 2: Run — expect ImportError**

```bash
python dev/test_tool_sub_item.py
```

Expected: `ImportError: cannot import name 'ToolSubItem'`

- [ ] **Step 3: Append `ToolSubItem` to `agent_turn_bubble.py`**

Add after `ReasoningTicker`:

```python
class ToolSubItem(QWidget):
    """One tool call line: [connector]  [icon]  [key_label]  [json_suffix]"""

    def __init__(self, tool_input: dict, group, is_last: bool = False, parent=None):
        super().__init__(parent)
        self._group = group   # ToolGroupRow | None
        self._done = False
        self._spin_idx = 0
        self._bubble = None   # set by AgentTurnBubble.add_tool()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background:transparent;")

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.Monospace)

        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(20, 1, 12, 1)
        hbox.setSpacing(4)

        self._conn_lbl = QLabel("└─" if is_last else "├─")
        self._conn_lbl.setFont(mono)
        self._conn_lbl.setStyleSheet(f"color:{_BORDER}; background:transparent;")
        hbox.addWidget(self._conn_lbl)

        self._icon_lbl = QLabel(_BRAILLE[0])
        self._icon_lbl.setFont(mono)
        self._icon_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        self._icon_lbl.setFixedWidth(14)
        hbox.addWidget(self._icon_lbl)

        key_lbl = QLabel(self._extract_key(tool_input))
        key_lbl.setFont(mono)
        key_lbl.setStyleSheet(f"color:{_TEXT}; background:transparent; font-size:10px;")
        key_lbl.setTextFormat(Qt.PlainText)
        hbox.addWidget(key_lbl)

        json_str = json.dumps(tool_input, default=str)
        if len(json_str) > 60:
            json_str = json_str[:60] + "…"
        json_lbl = QLabel(json_str)
        json_lbl.setFont(mono)
        json_lbl.setStyleSheet(f"color:{_TEXT_3}; background:transparent; font-size:10px;")
        json_lbl.setTextFormat(Qt.PlainText)
        hbox.addWidget(json_lbl)
        hbox.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    # ── Public API ────────────────────────────────────────────────────────

    def set_last(self, is_last: bool) -> None:
        """Recalculate connector prefix when a new sibling is added."""
        self._conn_lbl.setText("└─" if is_last else "├─")

    def mark_done(self, is_error: bool = False) -> None:
        """Stop spinner and show ✓ or !. Internal — call set_result() from chat_dock."""
        try:
            self._timer.stop()
            self._done = True
            if is_error:
                self._icon_lbl.setText("!")
                self._icon_lbl.setStyleSheet(f"color:{_DANGER}; background:transparent;")
            else:
                self._icon_lbl.setText("✓")
                self._icon_lbl.setStyleSheet(f"color:{_SUCCESS}; background:transparent;")
        except RuntimeError:
            pass

    def set_result(self, result_str: str, is_error: bool = False) -> None:
        """Called by chat_dock.py. Marks done and notifies parent group."""
        if self._done:
            return
        self.mark_done(is_error=is_error)
        if self._group is not None:
            self._group.on_item_done(self, is_error=is_error)

    def append_reasoning(self, delta: str) -> None:
        """Called by chat_dock.py. Delegates to parent bubble's ReasoningTicker."""
        if self._bubble is not None:
            self._bubble.stream_reasoning(delta)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_key(tool_input: dict) -> str:
        """Return the most meaningful short label from tool_input dict."""
        if not isinstance(tool_input, dict):
            s = str(tool_input)
            return s[:40] if len(s) > 40 else s
        for k in ("path", "file_path", "filename",
                  "layer", "layer_name", "layer_id",
                  "query", "sql", "name", "id"):
            if k in tool_input:
                s = str(tool_input[k])
                return s[:40] if len(s) > 40 else s
        for v in tool_input.values():
            s = str(v)
            return s[:40] if len(s) > 40 else s
        return ""

    def _tick(self):
        try:
            self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE)
            self._icon_lbl.setText(_BRAILLE[self._spin_idx])
        except RuntimeError:
            self._timer.stop()

    def _on_destroyed(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass
```

- [ ] **Step 4: Run test — expect pass**

```bash
python dev/test_tool_sub_item.py
```

Expected: two rows appear. `├─ ⠋ roads_2024 {…}` and `└─ ⠋ buildings {…}` with spinning braille. After 1.5 s item1 shows `✓` green. After 2.5 s item2 shows `!` red.

- [ ] **Step 5: Commit**

```bash
git add gui/agent_turn_bubble.py dev/test_tool_sub_item.py
git commit -m "feat: add ToolSubItem — tree-row widget with braille spinner and ✓/! completion"
```

---

## Task 3: `ToolGroupRow`

**Files:**
- Modify: `gui/agent_turn_bubble.py` (append class after `ToolSubItem`)
- Create: `dev/test_tool_group_row.py`

- [ ] **Step 1: Write visual test**

Create `dev/test_tool_group_row.py`:

```python
import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ToolGroupRow

win = QWidget()
win.setWindowTitle("ToolGroupRow test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 160)
lay = QVBoxLayout(win)

group = ToolGroupRow("read_layer")
lay.addWidget(group)

item1 = group.add_item({"layer": "roads_2024"})
item2 = group.add_item({"layer": "buildings"})
item3 = group.add_item({"layer": "parks"})

QTimer.singleShot(800,  lambda: item1.set_result("ok", False))
QTimer.singleShot(1400, lambda: item2.set_result("ok", False))
QTimer.singleShot(2000, lambda: item3.set_result("ok", False))

win.show()
sys.exit(app.exec_())
```

- [ ] **Step 2: Run — expect ImportError**

```bash
python dev/test_tool_group_row.py
```

Expected: `ImportError: cannot import name 'ToolGroupRow'`

- [ ] **Step 3: Append `ToolGroupRow` to `agent_turn_bubble.py`**

Add after `ToolSubItem`:

```python
class ToolGroupRow(QWidget):
    """Groups all ToolSubItems for one tool_name under a ● header with spinner."""

    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._spin_idx = 0
        self._running_count = 0
        self._had_error = False

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet("background:transparent;")

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.Monospace)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(0)

        # Header row
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 2, 12, 2)
        hbox.setSpacing(6)

        self._dot_lbl = QLabel("●")
        self._dot_lbl.setFont(mono)
        self._dot_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        hbox.addWidget(self._dot_lbl)

        name_lbl = QLabel(_html.escape(tool_name))
        name_lbl.setFont(mono)
        name_lbl.setStyleSheet(
            f"color:{_TEXT}; background:transparent; font-size:10px;"
        )
        name_lbl.setTextFormat(Qt.RichText)
        hbox.addWidget(name_lbl)

        self._count_lbl = QLabel("(0)")
        self._count_lbl.setFont(mono)
        self._count_lbl.setStyleSheet(
            f"color:{_TEXT_3}; background:transparent; font-size:10px;"
        )
        hbox.addWidget(self._count_lbl)
        hbox.addStretch()

        self._state_lbl = QLabel(_BRAILLE[0])
        self._state_lbl.setFont(mono)
        self._state_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        hbox.addWidget(self._state_lbl)

        self._layout.addWidget(header)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    def add_item(self, tool_input: dict) -> ToolSubItem:
        """Append a sub-item; recalculate connectors so only the last shows └─."""
        if self._items:
            self._items[-1].set_last(False)
        item = ToolSubItem(tool_input, group=self, is_last=True, parent=self)
        self._items.append(item)
        self._running_count += 1
        self._layout.addWidget(item)
        self._count_lbl.setText(f"({len(self._items)})")
        return item

    def on_item_done(self, item: ToolSubItem, is_error: bool = False) -> None:
        """Called by ToolSubItem.set_result(). Finalizes header when all done."""
        self._running_count = max(0, self._running_count - 1)
        if is_error:
            self._had_error = True
        if self._running_count == 0:
            self._finalize_header()

    def force_finalize(self) -> None:
        """Mark all still-running items as timed out. Called by AgentTurnBubble.finalize()."""
        for item in self._items:
            if not item._done:
                item.mark_done(is_error=True)
        if self._running_count > 0:
            self._running_count = 0
            self._had_error = True
            self._finalize_header()

    def _finalize_header(self) -> None:
        try:
            self._timer.stop()
            if self._had_error:
                self._dot_lbl.setStyleSheet(f"color:{_DANGER}; background:transparent;")
                self._state_lbl.setText("!")
                self._state_lbl.setStyleSheet(f"color:{_DANGER}; background:transparent;")
            else:
                self._dot_lbl.setStyleSheet(f"color:{_SUCCESS}; background:transparent;")
                self._state_lbl.setText("✓")
                self._state_lbl.setStyleSheet(f"color:{_SUCCESS}; background:transparent;")
        except RuntimeError:
            pass

    def _tick(self):
        try:
            self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE)
            self._state_lbl.setText(_BRAILLE[self._spin_idx])
        except RuntimeError:
            self._timer.stop()

    def _on_destroyed(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass
```

- [ ] **Step 4: Run test — expect pass**

```bash
python dev/test_tool_group_row.py
```

Expected: `● read_layer  (3)  ⠋` header with amber spinner. Three sub-items with tree connectors. Each transitions to ✓ as its result arrives. After item3 resolves, header dot and state turn green: `● read_layer  (3)  ✓`.

- [ ] **Step 5: Commit**

```bash
git add gui/agent_turn_bubble.py dev/test_tool_group_row.py
git commit -m "feat: add ToolGroupRow — grouped tool header with count and spinner → ✓"
```

---

## Task 4: Rewrite `AgentTurnBubble`

**Files:**
- Modify: `gui/agent_turn_bubble.py` (append rewritten `AgentTurnBubble`; remove old `ThinkingBlock`, `ToolRowWidget`, old `AgentTurnBubble`)
- Create: `dev/test_agent_turn_bubble_rewrite.py`

- [ ] **Step 1: Write integration test**

Create `dev/test_agent_turn_bubble_rewrite.py`:

```python
import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import AgentTurnBubble

win = QWidget()
win.setWindowTitle("AgentTurnBubble rewrite")
win.setStyleSheet("background:#141414;")
win.resize(700, 420)
lay = QVBoxLayout(win)
bubble = AgentTurnBubble()
lay.addWidget(bubble)

t = [0]

def step():
    n = t[0]
    t[0] += 1
    if n == 0:
        bubble.set_thinking_text("considering layer boundaries to filter by extent")
    elif n == 1:
        bubble.set_thinking_text(
            "considering layer boundaries to filter by extent of the selected region…"
        )
    elif n == 2:
        r1 = bubble.add_tool("read_layer", {"layer": "roads_2024"})
        r2 = bubble.add_tool("read_layer", {"layer": "buildings"})
        r3 = bubble.add_tool("read_layer", {"layer": "parks"})
        QTimer.singleShot(700,  lambda: r1.set_result("ok", False))
        QTimer.singleShot(1200, lambda: r2.set_result("ok", False))
        QTimer.singleShot(1800, lambda: r3.set_result("ok", False))
    elif n == 4:
        q = bubble.add_tool("run_query", {"query": "SELECT * FROM roads WHERE speed > 80"})
        QTimer.singleShot(900, lambda: q.set_result("142 rows", False))
    elif n == 7:
        bubble.set_streaming_text("The analysis found ")
    elif n == 8:
        bubble.set_streaming_text("The analysis found 142 road segments ")
    elif n == 9:
        bubble.finalize_text(
            "The analysis found **142 road segments** intersecting the selected area."
        )

timer = QTimer()
timer.setInterval(700)
timer.timeout.connect(step)
timer.start()

win.show()
sys.exit(app.exec_())
```

- [ ] **Step 2: Run — note current (old) UI as baseline**

```bash
python dev/test_agent_turn_bubble_rewrite.py
```

Note what you see — the old card-style UI. Close the window.

- [ ] **Step 3: Append new `AgentTurnBubble` to `agent_turn_bubble.py`**

The old `ThinkingBlock`, `ToolRowWidget`, and `AgentTurnBubble` were already removed when Task 1 replaced the whole file. The file currently ends after `ToolGroupRow`. Append the new `AgentTurnBubble` class:

```python
class AgentTurnBubble(QFrame):
    """One agent turn: reasoning ticker + grouped tool rows + streaming text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: dict = {}   # tool_name → ToolGroupRow
        self._stream_text = ""
        self._stream_html = ""
        self._user_decision_lbl = None

        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(f"""
            AgentTurnBubble {{
                background: {_SURFACE};
                border: 1px solid {_BORDER};
                border-left: 2px solid {_TEXT_2};
                border-radius: 0px;
            }}
        """)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 6, 0, 8)
        self._outer.setSpacing(0)

        self._ticker = ReasoningTicker(self)
        self._outer.addWidget(self._ticker)

        self._tools_area = QWidget(self)
        self._tools_area.setVisible(False)
        self._tools_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._tools_area.setStyleSheet("background:transparent;")
        self._tools_layout = QVBoxLayout(self._tools_area)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(0)
        self._outer.addWidget(self._tools_area)

        self.text_lbl = QLabel("")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setMinimumWidth(0)
        self.text_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.text_lbl.setTextFormat(Qt.RichText)
        self.text_lbl.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
        )
        self.text_lbl.setOpenExternalLinks(True)
        font = QFont("JetBrains Mono", 12)
        font.setStyleHint(QFont.Monospace)
        self.text_lbl.setFont(font)
        self.text_lbl.setStyleSheet(f"""
            color:{_TEXT}; background:transparent; border:none;
            font-family:'JetBrains Mono',monospace;
            font-size:12px; line-height:1.5;
        """)
        self.text_lbl.setContentsMargins(12, 6, 12, 0)
        self._outer.addWidget(self.text_lbl)

    # ── Core public API ───────────────────────────────────────────────────

    def add_tool(self, tool_name: str, tool_input: dict) -> ToolSubItem:
        """Add a tool call; creates group if tool_name is new. Returns ToolSubItem."""
        if tool_name not in self._groups:
            group = ToolGroupRow(tool_name, self._tools_area)
            self._groups[tool_name] = group
            self._tools_layout.addWidget(group)
            self._tools_area.setVisible(True)
        item = self._groups[tool_name].add_item(tool_input)
        item._bubble = self
        return item

    def stream_reasoning(self, text_chunk: str) -> None:
        self._ticker.append(text_chunk)

    def set_streaming_text(self, text: str) -> None:
        if text == self._stream_text:
            return
        if self._ticker.isVisible():
            self._ticker.hide_ticker()
        self._stream_text = text
        self._stream_html = _md_to_html(text) if text else ""
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>'
        self.text_lbl.setText(self._stream_html + cursor)

    def finalize_text(self, text: str) -> None:
        self._ticker.hide_ticker()
        self._stream_text = text
        self._stream_html = _md_to_html(text) if text else ""
        self.text_lbl.setText(self._stream_html)
        self.setStyleSheet(f"""
            AgentTurnBubble {{
                background: {_SURFACE};
                border: 1px solid {_BORDER};
                border-left: 2px solid {_BORDER};
                border-radius: 0px;
            }}
        """)

    def finalize(self) -> None:
        """Stop all spinners; mark any still-running tools as timed out."""
        self._ticker.hide_ticker()
        for group in self._groups.values():
            group.force_finalize()

    # ── Backward-compat shims for chat_dock.py ────────────────────────────

    def add_thinking_block(self) -> None:
        pass  # reasoning now routes to ReasoningTicker via set_thinking_text

    def set_thinking_text(self, text: str) -> None:
        self._ticker.set_full(text)

    def finalize_thinking(self) -> None:
        pass  # ticker hides automatically when set_streaming_text() is called

    def clear_streaming_text(self) -> None:
        self._stream_text = ""
        self._stream_html = ""
        self.text_lbl.setText("")
        self.updateGeometry()

    def has_content(self) -> bool:
        return bool(self._groups) or bool(self._stream_text)

    def set_user_decision(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        if self._user_decision_lbl is None:
            self._user_decision_lbl = QLabel("")
            self._user_decision_lbl.setWordWrap(True)
            self._user_decision_lbl.setMinimumWidth(0)
            self._user_decision_lbl.setTextFormat(Qt.PlainText)
            self._user_decision_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._user_decision_lbl.setStyleSheet(
                f"color:{_TEXT_2}; background:{_SURFACE_2}; border:1px solid {_BORDER_SOFT};"
                f" border-radius:5px; padding:5px 9px; margin:6px 12px 0 12px;"
                f" font-size:10.5px; font-family:'JetBrains Mono',monospace;"
            )
            self._outer.insertWidget(max(0, self._outer.count() - 1), self._user_decision_lbl)
        self._user_decision_lbl.setText(f"User chose: {clean}")
        self.updateGeometry()

    # ── Layout ────────────────────────────────────────────────────────────

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        if self._outer:
            m = self._outer.contentsMargins()
            inner_w = width - m.left() - m.right()
            if inner_w > 0:
                lh = self.text_lbl.heightForWidth(inner_w)
                tools_h = (
                    self._tools_area.sizeHint().height()
                    if self._tools_area.isVisible()
                    else 0
                )
                if lh >= 0:
                    return lh + tools_h + m.top() + m.bottom() + 8
        return -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._outer:
            m = self._outer.contentsMargins()
            w = event.size().width() - m.left() - m.right()
            if w > 0:
                self.text_lbl.setFixedWidth(w)
```

- [ ] **Step 4: Run integration test — verify new UI**

```bash
python dev/test_agent_turn_bubble_rewrite.py
```

Expected sequence:
1. `▸ considering layer boundaries…` appears (dim italic, updates in place)
2. `● read_layer  (3)  ⠋` appears with 3 sub-items, connectors `├─ ├─ └─`, spinning
3. Items transition to `✓` one by one; after item3 the group header goes `✓` green
4. `● run_query  (1)  ⠋` appears, its single item spins then resolves
5. Ticker hides when response text starts
6. Response text streams in with cursor `|`, finalizes with bold markdown

- [ ] **Step 5: Commit**

```bash
git add gui/agent_turn_bubble.py dev/test_agent_turn_bubble_rewrite.py
git commit -m "feat: rewrite AgentTurnBubble — terminal-style grouped tools + reasoning ticker"
```

---

## Task 5: Delete `tool_result_widget.py`

**Files:**
- Delete: `gui/tool_result_widget.py`

- [ ] **Step 1: Confirm no imports (already verified — zero importers)**

```bash
grep -rn "tool_result_widget\|ToolResultWidget" /Users/muhammadalichamdan/Documents/Development/AgenticGis/ --include="*.py"
```

Expected: only the class definition line inside `tool_result_widget.py` itself. If any other file appears, remove its import before proceeding.

- [ ] **Step 2: Delete the file**

```bash
git rm /Users/muhammadalichamdan/Documents/Development/AgenticGis/gui/tool_result_widget.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove tool_result_widget.py — superseded by ToolGroupRow/ToolSubItem"
```

---

## Task 6: Smoke-test in QGIS

**Files:** none (runtime verification)

- [ ] **Step 1: Load plugin in QGIS and open chat panel**

Start QGIS, reload the AgenticGIS plugin, open the chat dock.

- [ ] **Step 2: Send a prompt that causes one tool call**

Example: "how many features does the first loaded layer have?"

Expected:
- Reasoning ticker appears with streaming dim text
- One `● tool_name  (1)  ⠋` row appears
- Sub-item shows `└─ ⠋ <key_label>  {…}`
- On result: sub-item → `✓`, group header → `✓`
- Ticker hides when response text starts
- Response text appears below

- [ ] **Step 3: Send a prompt that causes multiple calls of the same tool**

Example: "summarize all 3 loaded layers"

Expected: one group row `● tool_name  (3)` with three sub-items and `├─ ├─ └─` connectors.

- [ ] **Step 4: Verify ask_user flow still works**

Send a prompt that triggers an `ask_user` card. Confirm `set_user_decision()` display is intact.

- [ ] **Step 5: Commit any runtime fixes**

```bash
git add gui/agent_turn_bubble.py
git commit -m "fix: address smoke-test issues from QGIS runtime"
```
