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
        self._result = ""

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
        try:
            self._conn_lbl.setText("└─" if is_last else "├─")
        except RuntimeError:
            pass

    def mark_done(self, is_error: bool = False) -> None:
        """Stop spinner and show ✓ or !. Internal — call set_result() from chat_dock."""
        self._done = True
        try:
            self._timer.stop()
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
        self._result = result_str
        self.mark_done(is_error=is_error)
        if self._group is not None:
            self._group.on_item_done(self, is_error=is_error)

    def append_reasoning(self, delta: str) -> None:
        """Called by chat_dock.py. Delegates to parent bubble's ReasoningTicker.

        _bubble is set by AgentTurnBubble.add_tool() before any deltas arrive.
        Deltas received before _bubble is set are silently dropped (safe: the
        ReasoningTicker belongs to the turn, not the individual tool item).
        """
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


class ToolGroupRow(QWidget):
    """Groups all ToolSubItems for one tool_name under a ● header with spinner."""

    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._spin_idx = 0
        self._running_count = 0
        self._had_error = False
        self._finalized = False

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
        name_lbl.setTextFormat(Qt.PlainText)
        hbox.addWidget(name_lbl)

        self._count_lbl = QLabel("")
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
        self.destroyed.connect(self._on_destroyed)

    def add_item(self, tool_input: dict) -> ToolSubItem:
        """Append a sub-item; recalculate connectors so only the last shows └─."""
        if not self._items:
            self._timer.start()
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
        any_forced = False
        for item in self._items:
            if not item._done:
                item.mark_done(is_error=True)
                any_forced = True
        if any_forced or self._running_count > 0:
            self._running_count = 0
            self._had_error = True
            self._finalize_header()

    def _finalize_header(self) -> None:
        if self._finalized:
            return
        self._finalized = True
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
        self.text_lbl.setText(self._stream_html)
        for group in self._groups.values():
            group.force_finalize()

    # ── Backward-compat shims for chat_dock.py ────────────────────────────

    def add_thinking_block(self) -> None:
        pass  # reasoning now routes to ReasoningTicker via set_thinking_text

    def set_thinking_text(self, text: str) -> None:
        self._ticker.set_full(text)

    def finalize_thinking(self) -> None:
        pass  # no-op; ticker accumulates via append() and hides on set_streaming_text()

    def clear_streaming_text(self) -> None:
        self._stream_text = ""
        self._stream_html = ""
        self.text_lbl.setText("")
        self.updateGeometry()

    def has_content(self) -> bool:
        return bool(self._groups) or bool(self._stream_text) or self._ticker.isVisible()

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
            self._outer.addWidget(self._user_decision_lbl)
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
                ticker_h = (
                    self._ticker.sizeHint().height()
                    if self._ticker.isVisible()
                    else 0
                )
                if lh >= 0:
                    return lh + tools_h + ticker_h + m.top() + m.bottom() + 8
        return -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._outer:
            m = self._outer.contentsMargins()
            w = event.size().width() - m.left() - m.right()
            if w > 0:
                self.text_lbl.setFixedWidth(w)
