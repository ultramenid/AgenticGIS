"""The in-QGIS chat dock: minimal, refined chat interface.

Inspired by Dribbble's "Minimal Chat Box UI" — clean off-white surface,
soft pill input, restrained type, generous spacing. Works in light
mode (default) and respects QGIS's own theme via neutral grays.
"""

import html

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QEvent, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..backends.base import AgentEvent, EventType
from .agent_turn_bubble import AgentTurnBubble
from .chart_widget import ChartWidget
from .message_bubble import MessageContainer
from .stats_widget import StatsWidget
from .typing_indicator import TypingIndicator

# ── Design Tokens (dark-minimal palette) ─────────────────────────────
_SURFACE     = "#131316"
_CANVAS      = "#0a0a0b"
_INPUT_BG    = "#1c1c20"
_BORDER      = "#27272a"
_BORDER_SOFT = "#1f1f23"
_TEXT        = "#fafafa"
_TEXT_2      = "#a1a1aa"
_TEXT_3      = "#71717a"
_ACCENT      = "#fafafa"
_ACCENT_HOV  = "#e4e4e7"
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"


class ChatWorker(QThread):
    event = pyqtSignal(object)
    finished_history = pyqtSignal(object)

    def __init__(self, backend, message, history, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._message = message
        self._history = history
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            history = self._backend.send(
                self._message, self._history, self.event.emit, lambda: self._stop
            )
            self.finished_history.emit(history)
        except Exception:
            import traceback
            self.event.emit(AgentEvent(EventType.ERROR, {"error": traceback.format_exc()}))
            self.finished_history.emit(None)


class ChatDock(QgsDockWidget):
    def __init__(self, get_backend, open_settings, parent=None):
        super().__init__("AgenticGIS", parent)
        self.setObjectName("AgenticGisDock")
        self._get_backend = get_backend
        self._open_settings = open_settings
        self._history = []
        self._worker = None
        self._streaming = False
        self._pending_tool = None
        self._typing_widget = None
        self._current_agent_turn = None   # AgentTurnBubble for the active turn
        self._current_tool_row = None      # ToolRowWidget awaiting its result
        self._current_text = ""            # accumulated streaming text
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.setStyleSheet(f"""
            QgsDockWidget {{
                background-color: {_SURFACE};
                border: none;
            }}
        """)

        container = QWidget()
        container.setStyleSheet(f"background-color: {_SURFACE};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Top bar (slim, no chrome) ----------------------------------- #
        top = QHBoxLayout()
        top.setContentsMargins(20, 14, 16, 14)
        top.setSpacing(8)

        brand = QLabel("AgenticGIS")
        brand.setStyleSheet(f"""
            color: {_TEXT};
            font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: -0.01em;
            background: transparent;
        """)
        top.addWidget(brand)
        top.addStretch(1)

        self.status = QLabel(
            f"<span style='color:{_SUCCESS};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self.status.setTextFormat(Qt.RichText)
        self.status.setStyleSheet("background: transparent; padding-right: 4px;")
        top.addWidget(self.status)

        for icon, tip in (("⚙", "Settings"), ("⌫", "Clear chat")):
            btn = QPushButton(icon)
            btn.setToolTip(tip)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 14px;
                    border: none;
                    border-radius: 8px;
                    background: transparent;
                    color: {_TEXT_3};
                }}
                QPushButton:hover {{
                    background-color: {_INPUT_BG};
                    color: {_TEXT};
                }}
            """)
            top.addWidget(btn)

        self._settings_btn = top.itemAt(top.count() - 2).widget()
        self._clear_btn = top.itemAt(top.count() - 1).widget()
        self._settings_btn.clicked.connect(self._open_settings)
        self._clear_btn.clicked.connect(self._clear)
        layout.addLayout(top)

        # -- Hairline divider -------------------------------------------- #
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {_BORDER}; border: none;")
        layout.addWidget(divider)

        # -- Scrollable transcript --------------------------------------- #
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background-color: {_SURFACE};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 4px 2px 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_TEXT_3};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self.transcript_widget = QWidget()
        self.transcript_widget.setStyleSheet(f"background-color: {_SURFACE};")
        self.transcript_layout = QVBoxLayout(self.transcript_widget)
        self.transcript_layout.setContentsMargins(0, 16, 0, 16)
        self.transcript_layout.setSpacing(18)
        self.transcript_layout.addStretch(1)

        self.scroll.setWidget(self.transcript_widget)
        layout.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        # Clamp transcript width to viewport — prevents any child widget from
        # forcing horizontal overflow and unwanted sideways scrolling.
        self.scroll.viewport().installEventFilter(self)

        # -- Hairline divider above input -------------------------------- #
        divider2 = QFrame()
        divider2.setFrameShape(QFrame.HLine)
        divider2.setFixedHeight(1)
        divider2.setStyleSheet(f"background-color: {_BORDER}; border: none;")
        layout.addWidget(divider2)

        # -- Input bar (pill) -------------------------------------------- #
        input_wrap = QWidget()
        input_wrap.setStyleSheet(f"background-color: {_SURFACE};")
        input_bar = QHBoxLayout(input_wrap)
        input_bar.setContentsMargins(16, 12, 16, 16)
        input_bar.setSpacing(8)

        pill = QFrame()
        pill.setObjectName("chatInputPill")
        pill.setStyleSheet(f"""
            QFrame#chatInputPill {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 22px;
            }}
        """)
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(6, 4, 6, 4)
        pill_layout.setSpacing(6)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message AgenticGIS…")
        self.input.setFixedHeight(36)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input.setStyleSheet(f"""
            QPlainTextEdit {{
                background: transparent;
                color: {_TEXT};
                border: none;
                padding: 6px 10px 6px 12px;
                font-size: 13px;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                selection-background-color: {_TEXT};
                selection-color: {_CANVAS};
            }}
        """)
        pill_layout.addWidget(self.input, 1)

        # Send button (circular, accent)
        self.send_btn = QPushButton("↑")
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_ACCENT};
                color: {_CANVAS};
                border: none;
                border-radius: 16px;
                font-size: 15px;
                font-weight: 700;
                padding-bottom: 2px;
            }}
            QPushButton:hover {{ background-color: {_ACCENT_HOV}; }}
            QPushButton:pressed {{ background-color: {_TEXT_2}; }}
            QPushButton:disabled {{ background-color: {_BORDER}; color: {_TEXT_3}; }}
        """)
        self.send_btn.clicked.connect(self._on_send)
        pill_layout.addWidget(self.send_btn, 0, Qt.AlignVCenter)

        # Stop button (circular, danger)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setFixedSize(32, 32)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {_DANGER};
                border: 1px solid {_DANGER};
                border-radius: 16px;
                font-size: 10px;
            }}
            QPushButton:hover {{ background-color: {_DANGER}; color: {_SURFACE}; }}
            QPushButton:disabled {{
                border: 1px solid {_BORDER};
                color: {_TEXT_3};
                background-color: transparent;
            }}
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        pill_layout.addWidget(self.stop_btn, 0, Qt.AlignVCenter)

        input_bar.addWidget(pill, 1)
        layout.addWidget(input_wrap)

        self.setWidget(container)

        # Install event filter on the input widget so Enter-to-send works
        self.input.installEventFilter(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)

    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            # Keep transcript widget exactly as wide as the viewport so no
            # child widget can cause horizontal overflow or sideways scrolling.
            self.transcript_widget.setFixedWidth(event.size().width())
            return False
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False  # Shift+Enter inserts newline
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _scroll_to_bottom(self):
        try:
            self._programmatic_scroll = True
            vs = self.scroll.verticalScrollBar()
            vs.setValue(vs.maximum())
            self._programmatic_scroll = False
        except RuntimeError:
            self._programmatic_scroll = False

    def _on_scroll_changed(self, value):
        """Detect user-initiated scroll during streaming and lock auto-scroll."""
        if self._programmatic_scroll or not self._streaming:
            return
        vs = self.scroll.verticalScrollBar()
        if vs.maximum() > 0 and value < vs.maximum() - 60:
            self._scroll_locked = True
        else:
            self._scroll_locked = False

    def _maybe_scroll_to_bottom(self):
        """Scroll to bottom only if the user has not manually scrolled up."""
        if not self._scroll_locked:
            self._scroll_to_bottom()

    def _add_widget(self, widget):
        """Insert widget above the trailing stretch."""
        self.transcript_layout.insertWidget(self.transcript_layout.count() - 1, widget)
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, self._scroll_to_bottom)

    # -- High-level adders ---------------------------------------------- #
    def _add_user_message(self, text: str):
        self._add_widget(MessageContainer(text, sender_name="You", is_user=True))

    def _get_or_create_agent_turn(self) -> AgentTurnBubble:
        """Return the active AgentTurnBubble, creating and adding one if needed."""
        if self._current_agent_turn is None:
            self._hide_typing()
            container = QWidget()
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            vl = QVBoxLayout(container)
            vl.setContentsMargins(16, 0, 16, 0)
            vl.setSpacing(3)
            sender = QLabel("AgenticGIS")
            sender.setStyleSheet(
                f"color:{_TEXT_3}; font-size:10px; background:transparent; border:none;"
            )
            vl.addWidget(sender)
            turn = AgentTurnBubble()
            vl.addWidget(turn)
            self._add_widget(container)
            self._current_agent_turn = turn
        return self._current_agent_turn

    def _add_chart(self, chart_data):
        self._add_widget(ChartWidget(chart_data))

    def _add_stats(self, stats_data):
        self._add_widget(StatsWidget(stats_data))

    # -- Typing indicator ----------------------------------------------- #
    def _show_typing(self):
        if self._typing_widget is None:
            self._typing_widget = TypingIndicator("AgenticGIS")
            self._add_widget(self._typing_widget)

    def _hide_typing(self):
        if self._typing_widget is not None:
            self._typing_widget.stop()
            self._typing_widget.deleteLater()
            self._typing_widget = None

    # ------------------------------------------------------------------ #
    def _clear(self):
        self._history = []
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.status.setText(
            f"<span style='color:{_SUCCESS};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._scroll_locked = False

    def _on_send(self):
        if self._worker is not None:
            return
        message = self.input.toPlainText().strip()
        if not message:
            return

        backend = self._get_backend()
        if backend is None:
            self._add_user_message("No backend configured. Open Settings.")
            return
        err = backend.validate()
        if err:
            self._add_user_message(err)
            return

        self.input.clear()
        self._add_user_message(message)
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._current_agent_turn = None
        self._current_tool_row = None
        self._scroll_locked = False

        self.send_btn.setEnabled(False)
        self.send_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self.status.setText(
            f"<span style='color:{_TEXT_3};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Thinking</span>"
        )
        self._show_typing()

        self._worker = ChatWorker(backend, message, self._history)
        self._worker.event.connect(self._on_event)
        self._worker.finished_history.connect(self._on_finished)
        self._worker.start()

    def _on_stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.status.setText(
                f"<span style='color:{_DANGER};'>&#9679;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Stopping</span>"
            )

    # ------------------------------------------------------------------ #
    def _on_event(self, ev):
        if ev.type == EventType.TEXT:
            if not self._streaming:
                self._streaming = True
                self._current_text = ""
            delta = ev.data.get("text", "")
            if delta:
                self._current_text += delta
                turn = self._get_or_create_agent_turn()
                turn.set_streaming_text(self._current_text)
                self._maybe_scroll_to_bottom()

        elif ev.type == EventType.TOOL_USE:
            self._finish_streaming()
            tool_name = ev.data.get("name", "tool")
            tool_input = ev.data.get("input", {})
            self._pending_tool = (tool_name, tool_input)
            turn = self._get_or_create_agent_turn()
            self._current_tool_row = turn.add_tool(tool_name, tool_input)
            self._maybe_scroll_to_bottom()

        elif ev.type == EventType.TOOL_RESULT:
            result = ev.data.get("result", "")
            is_err = str(result).startswith("Error") or str(result).startswith("error")
            if self._current_tool_row is not None:
                self._current_tool_row.set_result(str(result), is_err)
                self._current_tool_row = None
            self._pending_tool = None
            self._maybe_scroll_to_bottom()

        elif ev.type == EventType.VISUALIZATION:
            viz = ev.data.get("type")
            d = ev.data.get("data", {})
            if viz == "chart":
                self._add_chart(d)
            elif viz == "stats":
                self._add_stats(d)

        elif ev.type == EventType.THINKING:
            self.status.setText(
                f"<span style='color:{_TEXT_3};'>&#9679;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Thinking</span>"
            )

        elif ev.type == EventType.ERROR:
            self._hide_typing()
            self._finish_streaming()
            msg = html.escape(str(ev.data.get("error", "")))
            self._add_widget(MessageContainer(msg, is_user=False, is_error=True))

        elif ev.type == EventType.DONE:
            self._hide_typing()
            self._finish_streaming()
            self._streaming = False
            self._current_agent_turn = None
            self.status.setText(
                f"<span style='color:{_SUCCESS};'>&#9679;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
            )
            self._scroll_to_bottom()
            self._pending_tool = None

    def _finish_streaming(self):
        """Finalize streaming text in current agent turn — applies full markdown."""
        if self._current_agent_turn is not None and self._current_text:
            self._current_agent_turn.finalize_text(self._current_text)
            self._current_text = ""

    def _on_finished(self, history):
        self._history = history if history is not None else self._history
        self._finish_streaming()
        self._hide_typing()
        self.send_btn.setEnabled(True)
        self.send_btn.setVisible(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.status.setText(
            f"<span style='color:{_SUCCESS};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self._worker = None
