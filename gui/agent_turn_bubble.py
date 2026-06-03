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
