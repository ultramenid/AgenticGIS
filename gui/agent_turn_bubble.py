"""AgentTurnBubble — one widget per complete agent response turn.

All tool calls for the turn are grouped in a compact collapsible section
(max 3 rows visible). The streaming text response appears below.
Replaces the old approach of separate ToolCallBubble + MessageContainer widgets.

Anti-AI-SLOP: no emoji, no heavy icons, pure typography.
"""

import html as _html
import json
import re
import time

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
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"
_WARN        = "#f0a500"
_CODE_GREEN  = "#7ee787"

_DOTS = [".", "..", "..."]
MAX_VISIBLE = 3


class ThinkingBlock(QWidget):
    """Collapsible thinking block shown above tools/text in an agent turn."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_collapsed = False
        self._dots_idx = 0
        self._thinking_text = ""
        self._start_time = time.monotonic()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header row ───────────────────────────────────────────────────
        self._header = QWidget()
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.mousePressEvent = lambda _ev: self.toggle_collapse()
        hbox = QHBoxLayout(self._header)
        hbox.setContentsMargins(12, 4, 12, 4)
        hbox.setSpacing(6)

        mono = QFont("SF Mono", 10)
        mono.setStyleHint(QFont.Monospace)
        mono.setItalic(True)

        self._summary_lbl = QLabel("▸ thinking")
        self._summary_lbl.setFont(mono)
        self._summary_lbl.setStyleSheet(
            f"color:{_TEXT_3}; font-size:11px; font-style:italic;"
        )
        hbox.addWidget(self._summary_lbl)

        self._dots_lbl = QLabel(".")
        self._dots_lbl.setFont(mono)
        self._dots_lbl.setStyleSheet(
            f"color:{_TEXT_3}; font-size:11px; font-style:italic; min-width:18px;"
        )
        hbox.addWidget(self._dots_lbl)
        hbox.addStretch()

        outer.addWidget(self._header)

        # ── Content area ─────────────────────────────────────────────────
        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        cl = QVBoxLayout(self._content)
        cl.setContentsMargins(12, 2, 12, 6)
        cl.setSpacing(0)

        mono_content = QFont("SF Mono", 10)
        mono_content.setStyleHint(QFont.Monospace)
        mono_content.setItalic(True)

        self._text_lbl = QLabel("")
        self._text_lbl.setFont(mono_content)
        self._text_lbl.setStyleSheet(
            f"color:{_TEXT_3}; font-size:11px; font-style:italic; background:transparent;"
        )
        self._text_lbl.setWordWrap(True)
        self._text_lbl.setMinimumWidth(0)
        self._text_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self._text_lbl)

        outer.addWidget(self._content)

        # ── Dots animation ───────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.destroyed.connect(self._on_destroyed)

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def is_collapsed(self):
        return self._is_collapsed

    def toggle_collapse(self):
        self._is_collapsed = not self._is_collapsed
        self._content.setVisible(not self._is_collapsed)
        self.updateGeometry()

    def auto_collapse(self, elapsed_seconds=None):
        """Collapse block and show summary. Called when text begins or on finalize."""
        self._timer.stop()
        self._dots_lbl.setVisible(False)
        self._is_collapsed = True
        self._content.setVisible(False)
        if elapsed_seconds is not None:
            summary = f"▸ Thought for {elapsed_seconds:.0f}s"
        else:
            summary = "▸ Thought"
        self._summary_lbl.setText(summary)
        self.updateGeometry()

    def set_thinking_text(self, text: str) -> None:
        """Stream thinking text into content label."""
        self._thinking_text = text
        self._text_lbl.setText(_html.escape(text))

    def start_animation(self) -> None:
        self._dots_lbl.setVisible(True)
        self._timer.start()

    def stop_animation(self) -> None:
        try:
            self._timer.stop()
            self._dots_lbl.setVisible(False)
        except RuntimeError:
            pass

    # ── Private ──────────────────────────────────────────────────────────

    def _tick(self):
        try:
            self._dots_idx = (self._dots_idx + 1) % len(_DOTS)
            self._dots_lbl.setText(_DOTS[self._dots_idx])
        except RuntimeError:
            self._timer.stop()

    def _on_destroyed(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass


class ToolRowWidget(QWidget):
    """Compact single-line tool call row with expandable details.

    Left-border accent: amber=running, green=done, red=error.
    """

    def __init__(self, tool_name: str, tool_input: dict, parent=None):
        super().__init__(parent)
        self._tool_input = tool_input
        self._expanded = False
        self._dots_idx = 0
        self._start_time = time.monotonic()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Card frame with left-border accent ───────────────────────────
        self._card = QFrame()
        self._card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._set_card_style(_WARN)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # ── Header row (always visible) ──────────────────────────────────
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 4, 12, 4)
        hbox.setSpacing(8)

        mono = QFont("SF Mono", 9)
        mono.setStyleHint(QFont.Monospace)

        name_lbl = QLabel(_html.escape(tool_name) + "()")
        name_lbl.setFont(mono)
        name_lbl.setStyleSheet(f"color:{_TEXT_2};")
        hbox.addWidget(name_lbl)
        hbox.addStretch()

        self.state_lbl = QLabel("running...")
        self.state_lbl.setStyleSheet(
            f"color:{_WARN}; font-size:10px; letter-spacing:0.03em;"
        )
        hbox.addWidget(self.state_lbl)

        # Expand toggle
        self.expand_lbl = QLabel("")
        self.expand_lbl.setStyleSheet(
            f"color:{_TEXT_3}; font-size:10px; cursor:pointer;"
        )
        self.expand_lbl.setVisible(False)
        self.expand_lbl.setCursor(Qt.PointingHandCursor)
        self.expand_lbl.mousePressEvent = lambda _ev: self._toggle()
        hbox.addWidget(self.expand_lbl)

        card_layout.addWidget(header)

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

        # Copy button
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

        card_layout.addWidget(self.details)
        outer.addWidget(self._card)

        # ── Dots animation ───────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    # ── Public API ───────────────────────────────────────────────────────

    def set_result(self, result_str: str, is_error: bool = False) -> None:
        """Alias kept for backward compat; delegates to mark_done."""
        self.mark_done(result_str, is_error=is_error)

    def mark_done(self, result_str: str, is_error: bool = False) -> None:
        try:
            self._timer.stop()
            elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
            if is_error:
                self._set_card_style(_DANGER)
                self.state_lbl.setText("error")
                self.state_lbl.setStyleSheet(
                    f"color:{_DANGER}; font-size:10px; letter-spacing:0.03em;"
                )
            else:
                self._set_card_style(_SUCCESS)
                self.state_lbl.setText(f"done ({elapsed_ms}ms)")
                self.state_lbl.setStyleSheet(
                    f"color:{_SUCCESS}; font-size:10px; letter-spacing:0.03em;"
                )
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

    def _set_card_style(self, accent_color: str) -> None:
        self._card.setStyleSheet(f"""
            QFrame {{
                background: {_INPUT_BG};
                border: none;
                border-left: 2px solid {accent_color};
                border-radius: 4px;
            }}
        """)

    def _toggle(self):
        self._expanded = not self._expanded
        self.details.setVisible(self._expanded)
        self.expand_lbl.setText("hide" if self._expanded else "details")
        self.updateGeometry()

    def _tick(self):
        try:
            self._dots_idx = (self._dots_idx + 1) % len(_DOTS)
            dots = _DOTS[self._dots_idx]
            self.state_lbl.setText(f"running{dots}")
        except RuntimeError:
            self._timer.stop()

    def _on_destroyed(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass


class AgentTurnBubble(QFrame):
    """Single widget for one complete agent turn: thinking + tools + streaming text.

    ThinkingBlock (if any) appears first, then tool rows grouped in a compact
    collapsible section (max 3 visible), then the text response streaming below.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._tools_expanded = False
        self._stream_text = ""
        self._stream_html = ""
        self._thinking_block = None
        self._thinking_start = None
        self._tool_count = 0
        self._tool_start_time = None
        self._tool_summary_added = False

        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(f"""
            AgentTurnBubble {{
                background: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 4px;
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

        # ── Tool summary label (shown above text after tools complete) ────
        self._tool_summary_lbl = QLabel("")
        mono_font = QFont("SF Mono", 9)
        mono_font.setStyleHint(QFont.Monospace)
        self._tool_summary_lbl.setFont(mono_font)
        self._tool_summary_lbl.setStyleSheet(
            f"color:{_TEXT_3}; font-size:10px; background:transparent; border:none;"
        )
        self._tool_summary_lbl.setContentsMargins(12, 4, 12, 0)
        self._tool_summary_lbl.setVisible(False)
        self._outer.addWidget(self._tool_summary_lbl)

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
                tools_h = (
                    self.tools_frame.sizeHint().height()
                    if self.tools_frame.isVisible()
                    else 0
                )
                thinking_h = (
                    self._thinking_block.sizeHint().height()
                    if self._thinking_block is not None
                    else 0
                )
                if lh >= 0:
                    return lh + tools_h + thinking_h + m.top() + m.bottom() + 8
        return -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._outer:
            m = self._outer.contentsMargins()
            w = event.size().width() - m.left() - m.right()
            if w > 0:
                self.text_lbl.setFixedWidth(w)

    # ── Thinking block management ────────────────────────────────────────

    def add_thinking_block(self) -> "ThinkingBlock":
        """Create and insert ThinkingBlock before the tools section."""
        if self._thinking_block is not None:
            return self._thinking_block
        self._thinking_block = ThinkingBlock(self)
        self._thinking_start = time.monotonic()
        # Insert at index 0 (before tools_frame)
        self._outer.insertWidget(0, self._thinking_block)
        self._thinking_block.start_animation()
        return self._thinking_block

    def set_thinking_text(self, text: str) -> None:
        """Create block if needed and stream thinking text."""
        if self._thinking_block is None:
            self.add_thinking_block()
        self._thinking_block.set_thinking_text(text)

    def finalize_thinking(self) -> None:
        """Collapse the thinking block and record elapsed time."""
        if self._thinking_block is None:
            return
        elapsed = None
        if self._thinking_start is not None:
            elapsed = time.monotonic() - self._thinking_start
        self._thinking_block.auto_collapse(elapsed_seconds=elapsed)

    # ── Tool management ──────────────────────────────────────────────────

    def add_tool(self, tool_name: str, tool_input: dict) -> ToolRowWidget:
        """Add a running tool row. Returns it for later mark_done() / set_result()."""
        if self._tool_start_time is None:
            self._tool_start_time = time.monotonic()
        self._tool_count += 1
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
            self._more_btn.setText(
                f"+ {hidden} more tool{'s' if hidden > 1 else ''}"
            )
            self._more_btn.setVisible(True)

    def _toggle_expand(self):
        self._tools_expanded = not self._tools_expanded
        self._refresh_visibility()
        self.updateGeometry()

    def show_tool_summary(self) -> None:
        """Show dim summary line 'N tools · Xms total' above text area."""
        if self._tool_summary_added or self._tool_count == 0:
            return
        self._tool_summary_added = True
        elapsed_ms = 0
        if self._tool_start_time is not None:
            elapsed_ms = int((time.monotonic() - self._tool_start_time) * 1000)
        label = (
            f"{self._tool_count} tool{'s' if self._tool_count > 1 else ''}"
            f" · {elapsed_ms}ms total"
        )
        self._tool_summary_lbl.setText(label)
        self._tool_summary_lbl.setVisible(True)

    # ── Text streaming ───────────────────────────────────────────────────

    def set_streaming_text(self, text: str) -> None:
        """Append delta, apply inline markdown, show minimal cursor."""
        if text == self._stream_text:
            return
        # Auto-collapse thinking block when text begins
        if self._thinking_block is not None and not self._thinking_block.is_collapsed:
            elapsed = None
            if self._thinking_start is not None:
                elapsed = time.monotonic() - self._thinking_start
            self._thinking_block.auto_collapse(elapsed_seconds=elapsed)
        delta = _html.escape(text[len(self._stream_text):])
        self._stream_text = text
        # Inline transforms on delta only — tight, en-dash bullets
        delta = re.sub(
            r"(?m)^- (.+)$",
            lambda m: (
                f'<div style="padding-left:12px; color:{_TEXT}; '
                f'font-size:13px; line-height:1.35; margin:0 0 1px 0;">'
                f'<span style="color:{_TEXT_3};margin-right:6px;">—</span>'
                f'{m.group(1)}</div>'
            ),
            delta,
        )
        delta = re.sub(
            r"`([^`]+)`",
            lambda m: (
                f'<code style="background:{_SURFACE};color:{_CODE_GREEN};'
                f'border-radius:3px;padding:1px 4px;font-family:monospace;'
                f'font-size:12px;letter-spacing:-0.01em;">{m.group(1)}</code>'
            ),
            delta,
        )
        delta = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", delta)
        delta = re.sub(r"\*(.+?)\*", r"<i>\1</i>", delta)
        delta = delta.replace("\n", "<br>")
        self._stream_html += delta
        # Minimal cursor — thin bar
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>'
        self.text_lbl.setText(self._stream_html + cursor)

    def finalize_text(self, text: str) -> None:
        """Apply full markdown at stream end, remove cursor.

        Collapses thinking block and shows tool summary if tools were used.
        We keep _stream_text / _stream_html so that if the LLM resumes text
        streaming after a tool call we can compute the correct delta and append
        to the existing HTML instead of replacing it.
        """
        # Collapse thinking if still open
        if self._thinking_block is not None and not self._thinking_block.is_collapsed:
            elapsed = None
            if self._thinking_start is not None:
                elapsed = time.monotonic() - self._thinking_start
            self._thinking_block.auto_collapse(elapsed_seconds=elapsed)

        # Show tool summary if applicable
        if self._tool_count > 0:
            self.show_tool_summary()

        self.text = text
        self._stream_text = text
        self._stream_html = _md_to_html(text) if text else ""
        self.text_lbl.setText(self._stream_html)

    def has_content(self) -> bool:
        return (
            bool(self._rows)
            or bool(self._stream_text)
            or (self._thinking_block is not None)
        )
