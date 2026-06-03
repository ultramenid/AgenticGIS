"""AgentTurnBubble — one widget per complete agent response turn.

All tool calls for the turn are grouped in a compact collapsible section
(max 3 rows visible). The streaming text response appears below.
Replaces the old approach of separate ToolCallBubble + MessageContainer widgets.

Anti-AI-SLOP: no emoji, no heavy icons, pure typography.
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

# Design tokens — darker, softer
_SURFACE     = "#161616"
_INPUT_BG    = "#1e1e1e"
_BORDER      = "#2e2e2e"
_BORDER_SOFT = "#242424"
_TEXT        = "#ececec"
_TEXT_2      = "#a0a0a0"
_TEXT_3      = "#707070"
_DANGER      = "#e57373"
_SUCCESS     = "#81c784"
_CODE_GREEN  = "#7ee787"

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
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header row (always visible) ──────────────────────────────────
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 4, 12, 4)
        hbox.setSpacing(8)

        # Minimal status dot — tiny square, no emoji
        self.dot = QLabel("")
        self.dot.setFixedSize(4, 4)
        self.dot.setStyleSheet(f"background-color:{_TEXT_3}; border-radius:2px;")
        hbox.addWidget(self.dot)

        mono = QFont("SF Mono", 9)
        mono.setStyleHint(QFont.Monospace)
        name_lbl = QLabel(_html.escape(tool_name))
        name_lbl.setFont(mono)
        name_lbl.setStyleSheet(f"color:{_TEXT_2};")
        hbox.addWidget(name_lbl)
        hbox.addStretch()

        self.state_lbl = QLabel("running")
        self.state_lbl.setStyleSheet(f"color:{_TEXT_3}; font-size:10px; letter-spacing:0.03em;")
        hbox.addWidget(self.state_lbl)

        self.dots_lbl = QLabel("···")
        self.dots_lbl.setStyleSheet(f"color:{_TEXT_3}; font-size:10px; min-width:22px; letter-spacing:1px;")
        hbox.addWidget(self.dots_lbl)

        # Expand toggle — text label instead of triangle icon
        self.expand_lbl = QLabel("")
        self.expand_lbl.setStyleSheet(f"color:{_TEXT_3}; font-size:10px; cursor:pointer;")
        self.expand_lbl.setVisible(False)
        self.expand_lbl.setCursor(Qt.PointingHandCursor)
        self.expand_lbl.mousePressEvent = lambda _ev: self._toggle()
        hbox.addWidget(self.expand_lbl)

        outer.addWidget(header)

        # ── Collapsible details ──────────────────────────────────────────
        self.details = QWidget()
        self.details.setVisible(False)
        self.details.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(12, 4, 12, 6)
        dl.setSpacing(4)

        mono_ss = (
            f"background:{_SURFACE}; color:{_TEXT_2}; border-radius:4px;"
            f" padding:8px; font-family:'SF Mono',monospace; font-size:9.5px;"
            f" line-height:1.4;"
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

        # Copy button — minimal
        copy_row = QHBoxLayout()
        copy_row.setContentsMargins(0, 0, 0, 0)
        copy_row.addStretch()
        self._copy_btn = QPushButton("copy")
        self._copy_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_TEXT_3};
                border:1px solid {_BORDER}; border-radius:3px;
                font-size:9px; padding:1px 8px;
            }}
            QPushButton:hover {{ color:{_TEXT_2}; border-color:{_TEXT_3}; }}
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
                self.state_lbl.setText("error")
                self.state_lbl.setStyleSheet(f"color:{_DANGER}; font-size:10px; letter-spacing:0.03em;")
                self.dot.setStyleSheet(f"background-color:{_DANGER}; border-radius:2px;")
            else:
                self.state_lbl.setText("done")
                self.state_lbl.setStyleSheet(f"color:{_SUCCESS}; font-size:10px; letter-spacing:0.03em;")
                self.dot.setStyleSheet(f"background-color:{_SUCCESS}; border-radius:2px;")
            truncated = result_str[:600] + ("…" if len(result_str) > 600 else "")
            self.result_lbl.setText(_html.escape(truncated))
            self._copy_btn.setVisible(True)
            self._copy_btn.clicked.connect(
                lambda: QGuiApplication.clipboard().setText(result_str)
            )
            self.expand_lbl.setVisible(True)
            self.expand_lbl.setText("details")
        except RuntimeError:
            pass

    # ── Private ──────────────────────────────────────────────────────────

    def _toggle(self):
        self._expanded = not self._expanded
        self.details.setVisible(self._expanded)
        self.expand_lbl.setText("hide" if self._expanded else "details")
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
                border:none; font-size:10px; padding:3px 12px;
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
                self._more_btn.setText("show less")
                self._more_btn.setVisible(True)
            else:
                self._more_btn.setVisible(False)
        else:
            for i, row in enumerate(self._rows):
                row.setVisible(i < MAX_VISIBLE)
            hidden = n - MAX_VISIBLE
            self._more_btn.setText(f"+ {hidden} more tool{'s' if hidden > 1 else ''}")
            self._more_btn.setVisible(True)

    def _toggle_expand(self):
        self._tools_expanded = not self._tools_expanded
        self._refresh_visibility()
        self.updateGeometry()

    # ── Text streaming ───────────────────────────────────────────────────

    def set_streaming_text(self, text: str) -> None:
        """Append delta, apply inline markdown, show minimal cursor."""
        if text == self._stream_text:
            return
        delta = _html.escape(text[len(self._stream_text):])
        self._stream_text = text
        # Inline transforms on delta only — tight, en-dash bullets
        delta = re.sub(r"(?m)^- (.+)$",
            lambda m: (
                f'<div style="padding-left:12px; color:{_TEXT}; '
                f'font-size:13px; line-height:1.35; margin:0 0 1px 0;">'
                f'<span style="color:{_TEXT_3};margin-right:6px;">—</span>{m.group(1)}</div>'
            ), delta)
        delta = re.sub(r"`([^`]+)`",
            lambda m: (f'<code style="background:{_SURFACE};color:{_CODE_GREEN};'
                       f'border-radius:3px;padding:1px 4px;font-family:monospace;'
                       f'font-size:12px;letter-spacing:-0.01em;">{m.group(1)}</code>'), delta)
        delta = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", delta)
        delta = re.sub(r"\*(.+?)\*", r"<i>\1</i>", delta)
        delta = delta.replace("\n", "<br>")
        self._stream_html += delta
        # Minimal cursor — thin bar
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>'
        self.text_lbl.setText(self._stream_html + cursor)

    def finalize_text(self, text: str) -> None:
        """Apply full markdown at stream end, remove cursor.

        We keep ``_stream_text`` / ``_stream_html`` so that if the LLM
        resumes text streaming after a tool call we can compute the correct
        delta and append to the existing HTML instead of replacing it."""
        self.text = text
        self._stream_text = text
        self._stream_html = _md_to_html(text) if text else ""
        self.text_lbl.setText(self._stream_html)

    def has_content(self) -> bool:
        return bool(self._rows) or bool(self._stream_text)
