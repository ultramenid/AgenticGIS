"""AgentTurnBubble — one widget per complete agent response turn.

All tool calls for the turn are grouped in a compact collapsible section
(max 3 rows visible). The streaming text response appears below.
Replaces the old approach of separate ToolCallBubble + MessageContainer widgets.
"""

import html as _html
import json
import re

from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.PyQt.QtGui import QFont, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .message_bubble import _md_to_html

_SURFACE     = "#131316"
_INPUT_BG    = "#1c1c20"
_BORDER      = "#27272a"
_BORDER_SOFT = "#1f1f23"
_TEXT        = "#fafafa"
_TEXT_2      = "#a1a1aa"
_TEXT_3      = "#71717a"
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"

_DOTS = ["·  ", "·· ", "···", " ··", "  ·"]
MAX_VISIBLE = 3


class ToolRowWidget(QWidget):
    """Compact single-line tool call row with expandable details."""

    def __init__(self, tool_name: str, tool_input: dict, parent=None):
        super().__init__(parent)
        self._tool_input = tool_input
        self._expanded = False
        self._dots_idx = 0

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(f"background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header row (always visible) ──────────────────────────────────
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(10, 3, 10, 3)
        hbox.setSpacing(6)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{_TEXT_3}; font-size:8px; min-width:10px;")
        self.dot.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        hbox.addWidget(self.dot)

        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        name_lbl = QLabel(_html.escape(tool_name))
        name_lbl.setFont(mono)
        name_lbl.setStyleSheet(f"color:{_TEXT_2};")
        hbox.addWidget(name_lbl)
        hbox.addStretch()

        self.state_lbl = QLabel("Running")
        self.state_lbl.setStyleSheet(f"color:{_TEXT_3}; font-size:10px;")
        hbox.addWidget(self.state_lbl)

        self.dots_lbl = QLabel("···")
        self.dots_lbl.setStyleSheet(f"color:{_TEXT_3}; font-size:10px; min-width:22px;")
        hbox.addWidget(self.dots_lbl)

        self.expand_btn = QPushButton("▶")
        self.expand_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_TEXT_3};
                border:none; font-size:9px; padding:0 2px;
            }}
            QPushButton:hover {{ color:{_TEXT_2}; }}
        """)
        self.expand_btn.setFixedSize(18, 18)
        self.expand_btn.setVisible(False)
        self.expand_btn.clicked.connect(self._toggle)
        hbox.addWidget(self.expand_btn)

        outer.addWidget(header)

        # ── Collapsible details ──────────────────────────────────────────
        self.details = QWidget()
        self.details.setVisible(False)
        self.details.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(10, 4, 10, 6)
        dl.setSpacing(4)

        mono_ss = (
            f"background:{_SURFACE}; color:{_TEXT_2}; border-radius:4px;"
            f" padding:6px; font-family:Consolas,monospace; font-size:9px;"
        )

        if tool_input:
            inp_str = json.dumps(tool_input, indent=2)[:400]
            inp_lbl = QLabel(_html.escape(inp_str))
            inp_lbl.setStyleSheet(mono_ss)
            inp_lbl.setWordWrap(True)
            inp_lbl.setMinimumWidth(0)
            inp_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            dl.addWidget(inp_lbl)

        self.result_lbl = QLabel("")
        self.result_lbl.setStyleSheet(mono_ss)
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setMinimumWidth(0)
        self.result_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.result_lbl)

        # Copy button
        copy_row = QHBoxLayout()
        copy_row.setContentsMargins(0, 0, 0, 0)
        copy_row.addStretch()
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_TEXT_3};
                border:1px solid {_BORDER}; border-radius:3px;
                font-size:9px; padding:1px 6px;
            }}
            QPushButton:hover {{ color:{_TEXT_2}; }}
        """)
        self._copy_btn.setVisible(False)
        copy_row.addWidget(self._copy_btn)
        dl.addLayout(copy_row)

        outer.addWidget(self.details)

        # ── Dots animation ───────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    # ── Public API ───────────────────────────────────────────────────────

    def set_result(self, result_str: str, is_error: bool = False) -> None:
        try:
            self._timer.stop()
            self.dots_lbl.setVisible(False)
            if is_error:
                self.state_lbl.setText("Error")
                self.state_lbl.setStyleSheet(f"color:{_DANGER}; font-size:10px;")
                self.dot.setStyleSheet(f"color:{_DANGER}; font-size:8px;")
            else:
                self.state_lbl.setText("Done")
                self.dot.setStyleSheet(f"color:{_SUCCESS}; font-size:8px;")
            truncated = result_str[:600] + ("…" if len(result_str) > 600 else "")
            self.result_lbl.setText(_html.escape(truncated))
            self._copy_btn.setVisible(True)
            self._copy_btn.clicked.connect(
                lambda: QGuiApplication.clipboard().setText(result_str)
            )
            self.expand_btn.setVisible(True)
        except RuntimeError:
            pass

    # ── Private ──────────────────────────────────────────────────────────

    def _toggle(self):
        self._expanded = not self._expanded
        self.details.setVisible(self._expanded)
        self.expand_btn.setText("▼" if self._expanded else "▶")
        self.updateGeometry()

    def _tick(self):
        try:
            self._dots_idx = (self._dots_idx + 1) % len(_DOTS)
            self.dots_lbl.setText(_DOTS[self._dots_idx])
        except RuntimeError:
            self._timer.stop()

    def _on_destroyed(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass


class AgentTurnBubble(QFrame):
    """Single widget for one complete agent turn: tools + streaming text.

    Tool rows are grouped in a compact collapsible section (max 3 visible).
    The text response streams in below the tools.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._tools_expanded = False
        self._stream_text = ""
        self._stream_html = ""

        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(f"""
            AgentTurnBubble {{
                background: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 6, 0, 8)
        self._outer.setSpacing(0)

        # ── Tools section (hidden until first tool) ──────────────────────
        self.tools_frame = QFrame()
        self.tools_frame.setVisible(False)
        self.tools_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.tools_frame.setStyleSheet(f"""
            QFrame {{
                background: {_SURFACE};
                border:none;
                border-bottom: 1px solid {_BORDER_SOFT};
            }}
        """)
        tl = QVBoxLayout(self.tools_frame)
        tl.setContentsMargins(0, 2, 0, 2)
        tl.setSpacing(0)

        self._rows_container = QWidget()
        self._rows_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        tl.addWidget(self._rows_container)

        self._more_btn = QPushButton()
        self._more_btn.setVisible(False)
        self._more_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_TEXT_3};
                border:none; font-size:10px; padding:3px 10px;
                text-align:left;
            }}
            QPushButton:hover {{ color:{_TEXT_2}; }}
        """)
        self._more_btn.clicked.connect(self._toggle_expand)
        tl.addWidget(self._more_btn)

        self._outer.addWidget(self.tools_frame)

        # ── Streaming text ───────────────────────────────────────────────
        self.text_lbl = QLabel("")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setMinimumWidth(0)
        self.text_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.text_lbl.setTextFormat(Qt.RichText)
        self.text_lbl.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
        )
        self.text_lbl.setOpenExternalLinks(True)
        font = QFont("Inter", 13)
        font.setStyleHint(QFont.SansSerif)
        self.text_lbl.setFont(font)
        self.text_lbl.setStyleSheet(f"""
            color:{_TEXT}; background:transparent; border:none;
            font-family:'Inter','Segoe UI',sans-serif;
        """)
        self.text_lbl.setContentsMargins(12, 6, 12, 0)
        self._outer.addWidget(self.text_lbl)

    # ── Qt overrides for correct height-for-width layout ─────────────────

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        if self._outer:
            m = self._outer.contentsMargins()
            inner_w = width - m.left() - m.right()
            if inner_w > 0:
                lh = self.text_lbl.heightForWidth(inner_w)
                tools_h = self.tools_frame.sizeHint().height() if self.tools_frame.isVisible() else 0
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

    # ── Tool management ──────────────────────────────────────────────────

    def add_tool(self, tool_name: str, tool_input: dict) -> ToolRowWidget:
        """Add a running tool row. Returns it for later set_result()."""
        row = ToolRowWidget(tool_name, tool_input, self._rows_container)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        self._refresh_visibility()
        self.tools_frame.setVisible(True)
        return row

    def _refresh_visibility(self):
        n = len(self._rows)
        if n == 0:
            self._more_btn.setVisible(False)
            return
        if self._tools_expanded or n <= MAX_VISIBLE:
            for row in self._rows:
                row.setVisible(True)
            if n > MAX_VISIBLE and self._tools_expanded:
                self._more_btn.setText("▲ Collapse tools")
                self._more_btn.setVisible(True)
            else:
                self._more_btn.setVisible(False)
        else:
            for i, row in enumerate(self._rows):
                row.setVisible(i < MAX_VISIBLE)
            hidden = n - MAX_VISIBLE
            self._more_btn.setText(f"▾  {hidden} more tool{'s' if hidden > 1 else ''}…")
            self._more_btn.setVisible(True)

    def _toggle_expand(self):
        self._tools_expanded = not self._tools_expanded
        self._refresh_visibility()
        self.updateGeometry()

    # ── Text streaming ───────────────────────────────────────────────────

    def set_streaming_text(self, text: str) -> None:
        """Append delta, apply inline markdown, show cursor ▋."""
        if text == self._stream_text:
            return
        delta = _html.escape(text[len(self._stream_text):])
        self._stream_text = text
        # Inline transforms on delta only
        delta = re.sub(r"(?m)^- (.+)$",
            lambda m: f'<div style="padding-left:12px;">• {m.group(1)}</div>', delta)
        delta = re.sub(r"`([^`\n]+)`",
            lambda m: (f'<code style="background:{_SURFACE};color:{_SUCCESS};'
                       f'border-radius:3px;padding:1px 4px;font-family:monospace;'
                       f'font-size:12px;">{m.group(1)}</code>'), delta)
        delta = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", delta)
        delta = re.sub(r"\*(.+?)\*", r"<i>\1</i>", delta)
        delta = delta.replace("\n", "<br>")
        self._stream_html += delta
        cursor = f'<span style="color:{_TEXT_2};">▋</span>'
        self.text_lbl.setText(self._stream_html + cursor)

    def finalize_text(self, text: str) -> None:
        """Apply full markdown at stream end, remove cursor."""
        self._stream_text = ""
        self._stream_html = ""
        if text:
            self.text_lbl.setText(_md_to_html(text))
        else:
            self.text_lbl.clear()

    def has_content(self) -> bool:
        return bool(self._rows) or bool(self._stream_text)
