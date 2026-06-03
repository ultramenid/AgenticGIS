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
        elif not self._buffer:
            self.setVisible(False)

    def hide_ticker(self) -> None:
        self.setVisible(False)

    def _render(self) -> None:
        display = self._buffer
        if len(display) > self._MAX_CHARS:
            display = "…" + display[-self._MAX_CHARS:]
        display = display.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        self._lbl.setText(_html.escape(display))


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
