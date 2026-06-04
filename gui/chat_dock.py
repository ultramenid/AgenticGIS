"""The in-QGIS chat dock: minimal, refined chat interface.

Inspired by Dribbble's "Minimal Chat Box UI" — clean off-white surface,
soft pill input, restrained type, generous spacing. Works in light
mode (default) and respects QGIS's own theme via neutral grays.
"""

import html
import time
from datetime import datetime

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QEvent, QThread, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QFont, QTextCursor
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QFrame,
    QHBoxLayout,
    QApplication,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..backends.base import AgentEvent, EventType
from ..core.session_store import DEFAULT_SESSION_NAME, SessionStore
from .agent_turn_bubble import AgentTurnBubble, _SPINNER_FRAMES
from .chart_widget import ChartWidget
from .message_bubble import MessageContainer
from .stats_widget import StatsWidget
from .typing_indicator import TypingIndicator
from .ask_user_card import AskUserCard

# ── Design Tokens (Mono + Signal palette) ──────────────────────────────────
_CANVAS      = "#141414"   # transcript / app background (true neutral black)
_SURFACE     = "#1c1c1c"   # card surface
_SURFACE_2   = "#232323"   # elevated: chips, inline-code background
_BORDER      = "#2b2b2b"   # hairline border
_BORDER_SOFT = "#222222"   # fainter border
_TEXT        = "#e8e8e8"   # primary text
_TEXT_2      = "#9a9a9a"   # secondary text
_TEXT_3      = "#6f6f6f"   # muted meta
_TEXT_4      = "#4a4a4a"   # faint
# NO decorative accent. These alias to neutrals so existing refs don't break:
_ACCENT      = "#e8e8e8"   # primary white (send arrow, etc.)
_ACCENT_DIM  = "#9a9a9a"
_ACCENT_HOV  = "#ffffff"
_PURPLE      = "#6f6f6f"   # thinking -> grayscale dim (NO purple)
# SIGNAL colors — appear ONLY on tool/message STATE, never on plain text or chrome:
_WARN        = "#d99a3c"   # amber  — tool running
_SUCCESS     = "#5aa86f"   # green  — tool done / ready dot
_DANGER      = "#d05a5a"   # red    — error
_CODE_GREEN  = "#e8e8e8"   # inline-code text -> grayscale (NO green/teal)
_STREAM_COALESCE_INTERVAL_S = 0.030
_STREAM_COALESCE_MAX_CHARS = 8192
_STREAM_RENDER_INTERVAL_S = 0.050


class ChatWorker(QThread):
    event = pyqtSignal(object)
    finished_history = pyqtSignal(object)

    def __init__(self, backend, message, history, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._message = message
        self._history = history
        self._stop = False
        self._coalesce_type = None
        self._coalesce_text = ""
        self._last_coalesce_flush = time.monotonic()

    def stop(self):
        self._stop = True

    def run(self):
        try:
            history = self._backend.send(
                self._message, self._history, self._emit_event, lambda: self._stop
            )
            self._flush_coalesced_event()
            self.finished_history.emit(history)
        except Exception:
            import traceback
            try:
                self._flush_coalesced_event()
                self.event.emit(AgentEvent(EventType.ERROR, {"error": traceback.format_exc()}))
            except RuntimeError:
                # Widget already deleted (QGIS shutting down)
                pass
            try:
                self.finished_history.emit(None)
            except RuntimeError:
                # Widget already deleted (QGIS shutting down)
                pass
        finally:
            # Ensure we clean up even on unexpected errors
            try:
                self._stop = False  # Reset for potential reuse
            except Exception:
                pass

    def _emit_event(self, ev):
        """Emit backend events with source-side backpressure for text floods.

        Qt queues cross-thread signals. If a backend emits thousands of token
        deltas, queueing each token can make QGIS feel stuck even if each paint
        is cheap. Coalescing adjacent TEXT/THINKING deltas preserves content and
        ordering while sharply reducing queued signal count. Non-stream events
        are flushed through immediately.
        """
        if ev.type in (EventType.TEXT, EventType.THINKING):
            delta = ev.data.get("text", "")
            if delta:
                if self._coalesce_type is not None and self._coalesce_type != ev.type:
                    self._flush_coalesced_event()
                self._coalesce_type = ev.type
                self._coalesce_text += delta
                now = time.monotonic()
                if (
                    len(self._coalesce_text) >= _STREAM_COALESCE_MAX_CHARS
                    or now - self._last_coalesce_flush >= _STREAM_COALESCE_INTERVAL_S
                ):
                    self._flush_coalesced_event(now)
                return

        self._flush_coalesced_event()
        self.event.emit(ev)

    def _flush_coalesced_event(self, now=None):
        if not self._coalesce_text or self._coalesce_type is None:
            return
        self.event.emit(AgentEvent(self._coalesce_type, {"text": self._coalesce_text}))
        self._coalesce_type = None
        self._coalesce_text = ""
        self._last_coalesce_flush = now if now is not None else time.monotonic()


class ChatDock(QgsDockWidget):
    _ask_user_signal = pyqtSignal(str, object, bool)

    def __init__(
        self,
        get_backend,
        open_settings,
        request_cancel,
        toolkit=None,
        parent=None,
        session_store=None,
        show_startup_picker=True,
    ):
        super().__init__("AgenticGIS", parent)
        self.setObjectName("AgenticGisDock")
        self._get_backend = get_backend
        self._open_settings = open_settings
        self._request_cancel = request_cancel
        self._toolkit = toolkit
        self._session_store = session_store or SessionStore()
        self._active_session_id = self._session_store.active_session()["id"]
        self._show_startup_picker = bool(show_startup_picker)
        self._startup_picker_shown = False
        self._transcript_events = []
        self._restoring_transcript = False
        self._current_turn_event = None
        self._history = []
        self._prompt_history = []
        self._prompt_history_index = None
        self._prompt_history_draft = ""
        self._worker = None
        self._stop_requested = False
        self._streaming = False
        self._pending_tool = None
        self._typing_widget = None
        self._current_agent_turn = None   # AgentTurnBubble for the active turn
        self._current_tool_row = None      # ToolRowWidget awaiting its result
        self._current_text = ""            # accumulated final-answer text
        self._tool_progress_text = ""      # temporary visible text while a tool runs
        self._showing_tool_progress = False
        self._pending_stream_render = False
        self._pending_stream_kind = None
        self._pending_stream_scroll = False
        self._last_stream_render_at = 0.0
        self._thinking_text = ""           # accumulated thinking/progress text
        self._thinking_started = False     # whether add_thinking_block was called this turn
        self._ask_user_card = None
        self._ask_user_payload = None
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._status_phase = 0
        self._status_text = "Ready"
        self._status_color = _TEXT_3
        self._status_icon = "✓"
        self._status_spinning = False
        self._last_escape_press_at = 0.0
        self._build_ui()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(120)
        self._status_timer.timeout.connect(self._tick_status)
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.timeout.connect(self._flush_stream_render)
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._ask_user_signal.connect(self._show_ask_user, Qt.QueuedConnection)
        if self._toolkit is not None:
            self._toolkit.set_ask_user_emitter(
                self._emit_ask_user_threadsafe
            )
        self._restore_active_session()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.setStyleSheet(f"""
            QgsDockWidget {{
                background-color: {_CANVAS};
                border: none;
            }}
        """)

        container = QWidget()
        container.setStyleSheet(f"background-color: {_CANVAS};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Top bar (slim, no chrome) ----------------------------------- #
        top = QHBoxLayout()
        top.setContentsMargins(20, 14, 16, 14)
        top.setSpacing(8)

        self.status = QLabel(
            f"<span style='color:{_SUCCESS};font-size:11px;'>✓</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self.status.setTextFormat(Qt.RichText)
        self.status.setStyleSheet("background: transparent; padding-right: 4px;")
        top.addWidget(self.status)

        top.addStretch(1)

        for label, tip in (("Setting", "Settings"), ("Session", "Chat sessions")):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedSize(58, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 10px;
                    font-weight: 500;
                    letter-spacing: 0;
                    border: none;
                    border-radius: 6px;
                    background: transparent;
                    color: {_TEXT_3};
                }}
                QPushButton:hover {{
                    background-color: {_SURFACE_2};
                    color: {_TEXT};
                }}
            """)
            top.addWidget(btn)

        self._settings_btn = top.itemAt(top.count() - 2).widget()
        self._session_btn = top.itemAt(top.count() - 1).widget()
        self._settings_btn.clicked.connect(self._open_settings)
        self._session_menu = QMenu(self)
        self._session_menu.setStyleSheet(f"""
            QMenu {{
                background-color: {_SURFACE};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
            }}
            QMenu::item {{
                padding: 6px 18px 6px 10px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {_SURFACE_2};
            }}
        """)
        for text, handler in (
            ("New session", self._new_session_from_menu),
            ("Session list", self._show_session_list),
            ("Rename current", self._rename_current_session),
            ("Delete current", self._delete_current_session),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            self._session_menu.addAction(action)
        self._session_btn.setMenu(self._session_menu)
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
                background-color: {_CANVAS};
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
        self.transcript_widget.setStyleSheet(f"background-color: {_CANVAS};")
        self.transcript_layout = QVBoxLayout(self.transcript_widget)
        self.transcript_layout.setContentsMargins(0, 12, 0, 12)
        self.transcript_layout.setSpacing(12)
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

        # -- Input bar --------------------------------------------------- #
        input_wrap = QWidget()
        self._input_wrap = input_wrap  # kept for layout lookups in _show_ask_user
        input_wrap.setStyleSheet(f"background-color: {_CANVAS};")
        input_bar = QVBoxLayout(input_wrap)
        input_bar.setContentsMargins(8, 8, 8, 8)
        input_bar.setSpacing(0)

        # Input field — action button lives inside the same frame
        input_frame = QFrame()
        self._input_frame = input_frame
        self._input_min_h = 28
        self._input_max_h = 104
        self._input_frame_min_h = 38
        self._input_frame_max_h = 118
        input_frame.setMinimumHeight(self._input_frame_min_h)
        input_frame.setMaximumHeight(self._input_frame_max_h)
        input_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
        """)
        field_row = QHBoxLayout(input_frame)
        field_row.setContentsMargins(10, 0, 4, 0)
        field_row.setSpacing(0)

        self.input = QTextEdit()
        self.input.setPlaceholderText("Message AgenticGIS…")
        self.input.setAcceptRichText(False)
        self.input.setTabChangesFocus(True)
        self.input.setLineWrapMode(QTextEdit.WidgetWidth)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input.setFixedHeight(self._input_min_h)
        self.input.document().setDocumentMargin(0)
        mono_font = QFont("JetBrains Mono")
        mono_font.setStyleHint(QFont.Monospace)
        mono_font.setPointSize(10)
        self.input.setFont(mono_font)
        self.input.setStyleSheet(f"""
            QTextEdit {{
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 12px;
                border: none;
                background: transparent;
                color: {_TEXT};
                padding: 0px;
                selection-background-color: {_BORDER};
            }}
            QTextEdit QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                margin: 2px 0 2px 0;
            }}
            QTextEdit QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius: 2px;
                min-height: 18px;
            }}
        """)
        self.input.textChanged.connect(self._resize_input)
        self._update_input_vertical_inset(self._input_min_h)
        field_row.addWidget(self.input, 1, Qt.AlignVCenter)

        # Send button — inside the field frame, right edge
        self.send_btn = QPushButton("→")
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 14px; font-weight: 600;
                border: none; border-radius: 4px;
                background: transparent; color: {_ACCENT};
            }}
            QPushButton:hover {{ background-color: {_BORDER}; color: {_ACCENT_HOV}; }}
            QPushButton:pressed {{ color: {_ACCENT_DIM}; }}
            QPushButton:disabled {{ color: {_TEXT_3}; }}
        """)
        self.send_btn.clicked.connect(self._on_send)
        field_row.addWidget(self.send_btn, 0, Qt.AlignVCenter)

        # Stop button — inside the field frame, replaces send when running
        self.stop_btn = QPushButton("■")
        self.stop_btn.setToolTip("Stop (Esc)")
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 10px;
                border: none; border-radius: 4px;
                background: transparent; color: {_DANGER};
            }}
            QPushButton:hover {{ background-color: {_BORDER}; }}
            QPushButton:disabled {{ color: {_TEXT_3}; }}
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        field_row.addWidget(self.stop_btn, 0, Qt.AlignVCenter)

        input_bar.addWidget(input_frame)
        layout.addWidget(input_wrap)

        self.setWidget(container)

        # Install event filter on the input widget so Enter-to-send works
        self.input.installEventFilter(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)
        if (
            self._show_startup_picker
            and not self._startup_picker_shown
            and self._session_store.had_existing_sessions
        ):
            self._startup_picker_shown = True
            QTimer.singleShot(0, self._show_startup_session_picker)

    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            # Keep transcript widget exactly as wide as the viewport so no
            # child widget can cause horizontal overflow or sideways scrolling.
            self.transcript_widget.setFixedWidth(event.size().width())
            return False
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                return self._handle_input_escape()
            if event.key() in (Qt.Key_Up, Qt.Key_Down) and event.modifiers() == Qt.NoModifier:
                if self._handle_prompt_history_key(event.key()):
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self._newline_modifier(event.modifiers()):
                    self.input.insertPlainText("\n")
                    self._resize_input()
                    return True
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _newline_modifier(self, modifiers):
        return bool(
            modifiers & (
                Qt.ShiftModifier
                | Qt.AltModifier
                | Qt.MetaModifier
                | Qt.ControlModifier
            )
        )

    def _remember_prompt(self, message):
        message = message.strip() if isinstance(message, str) else ""
        if not message:
            return
        if self._prompt_history and self._prompt_history[-1] == message:
            self._prompt_history_index = None
            self._prompt_history_draft = ""
            return
        self._prompt_history.append(message)
        if len(self._prompt_history) > 100:
            self._prompt_history = self._prompt_history[-100:]
        self._prompt_history_index = None
        self._prompt_history_draft = ""

    def _handle_prompt_history_key(self, key):
        if not self._prompt_history:
            return False

        cursor = self.input.textCursor()
        block_number = cursor.blockNumber()
        last_block = max(0, self.input.document().blockCount() - 1)
        if key == Qt.Key_Up and block_number > 0:
            return False
        if key == Qt.Key_Down and block_number < last_block:
            return False

        if key == Qt.Key_Up:
            if self._prompt_history_index is None:
                self._prompt_history_draft = self.input.toPlainText()
                self._prompt_history_index = len(self._prompt_history) - 1
            else:
                self._prompt_history_index = max(0, self._prompt_history_index - 1)
            self._set_input_text_from_history(self._prompt_history[self._prompt_history_index])
            return True

        if key == Qt.Key_Down:
            if self._prompt_history_index is None:
                return False
            if self._prompt_history_index >= len(self._prompt_history) - 1:
                self._prompt_history_index = None
                self._set_input_text_from_history(self._prompt_history_draft)
                self._prompt_history_draft = ""
            else:
                self._prompt_history_index += 1
                self._set_input_text_from_history(self._prompt_history[self._prompt_history_index])
            return True

        return False

    def _set_input_text_from_history(self, text):
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.End)
        self._resize_input()

    def _handle_input_escape(self):
        if self._worker is None:
            self._last_escape_press_at = 0.0
            return False
        now = time.monotonic()
        if self._last_escape_press_at > 0.0 and now - self._last_escape_press_at <= 1.2:
            self._last_escape_press_at = 0.0
            self._on_stop()
        else:
            self._last_escape_press_at = now
            self._set_status("Esc again to stop", _DANGER, spinning=True)
        return True

    def _resize_input(self):
        if not hasattr(self, "input") or not hasattr(self, "_input_frame"):
            return
        doc_h = int(self.input.document().size().height()) + 2
        input_h = max(self._input_min_h, min(self._input_max_h, doc_h))
        frame_h = max(self._input_frame_min_h, min(self._input_frame_max_h, input_h + 10))
        self._update_input_vertical_inset(input_h)
        self.input.setFixedHeight(input_h)
        self._input_frame.setFixedHeight(frame_h)

    def _update_input_vertical_inset(self, input_h):
        line_h = self.input.fontMetrics().lineSpacing()
        top = max(0, int((input_h - line_h) / 2) - 1)
        self.input.setViewportMargins(0, top, 0, 0)

    def _scroll_to_bottom(self):
        try:
            self._programmatic_scroll = True
            vs = self.scroll.verticalScrollBar()
            vs.setValue(vs.maximum())
            self._programmatic_scroll = False
        except RuntimeError:
            self._programmatic_scroll = False

    def _scroll_to_bottom_after_layout(self):
        """Scroll to bottom once the scroll range expands to include new content.

        Hooks rangeChanged so we scroll at the exact moment Qt has finished
        laying out the new widget (not one tick too early). A 150 ms fallback
        covers the case where content fits the viewport with no range change.
        """
        vs = self.scroll.verticalScrollBar()
        fired = [False]

        def _go(*_):
            if fired[0]:
                return
            fired[0] = True
            try:
                vs.rangeChanged.disconnect(_go)
            except (RuntimeError, TypeError):
                pass
            self._scroll_to_bottom()

        vs.rangeChanged.connect(_go)
        QTimer.singleShot(150, _go)

    def _on_scroll_changed(self, value):
        """Detect user-initiated scroll during streaming and lock auto-scroll.
        Only lock when the user scrolls *up* by a meaningful amount, and
        only while streaming. Outside streaming, never lock — the user
        should be free to scroll wherever they want."""
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
        """Insert widget above the trailing stretch, then scroll to bottom
        once the layout has expanded the scroll range to include it."""
        self.transcript_layout.insertWidget(self.transcript_layout.count() - 1, widget)
        self._scroll_to_bottom_after_layout()

    def _record_transcript_event(self, event):
        if self._restoring_transcript:
            return
        self._transcript_events.append(dict(event))
        self._save_current_session()

    def _ensure_current_turn_event(self):
        if self._restoring_transcript:
            return None
        if self._current_turn_event is None:
            self._current_turn_event = {
                "type": "agent_turn",
                "thinking": "",
                "tools": [],
                "text": "",
            }
        return self._current_turn_event

    def _finalize_current_turn_event(self):
        if self._restoring_transcript or self._current_turn_event is None:
            self._current_turn_event = None
            return
        event = self._current_turn_event
        has_text = bool(event.get("text"))
        has_thinking = bool(event.get("thinking"))
        has_tools = bool(event.get("tools"))
        if has_text or has_thinking or has_tools:
            self._transcript_events.append(dict(event))
            self._save_current_session()
        self._current_turn_event = None

    # -- High-level adders ---------------------------------------------- #
    def _add_user_message(self, text: str):
        self._add_widget(MessageContainer(text, sender_name="You", is_user=True))
        self._record_transcript_event({"type": "user", "text": text})

    def _get_or_create_agent_turn(self) -> AgentTurnBubble:
        """Return the active AgentTurnBubble, creating and adding one if needed."""
        if self._current_agent_turn is None:
            self._hide_typing()
            self._current_agent_turn = self._add_agent_turn_widget()
            self._thinking_started = False
            self._ensure_current_turn_event()
        return self._current_agent_turn

    def _add_agent_turn_widget(self):
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
        return turn

    def _add_chart(self, chart_data):
        self._add_widget(ChartWidget(chart_data))
        self._record_transcript_event({"type": "chart", "data": chart_data})

    def _add_stats(self, stats_data):
        self._add_widget(StatsWidget(stats_data))
        self._record_transcript_event({"type": "stats", "data": stats_data})

    def _add_compaction_notice(self):
        w = QLabel("── history compacted ──")
        w.setAlignment(Qt.AlignCenter)
        w.setStyleSheet(
            f"color:{_TEXT_4}; font-size:10px; font-family:'JetBrains Mono',monospace;"
            f" padding:4px 0; background:transparent;"
        )
        self._add_widget(w)
        self._record_transcript_event({"type": "compaction"})

    def _add_error_message(self, text):
        msg = html.escape(str(text or ""))
        self._add_widget(MessageContainer(msg, is_user=False, is_error=True))
        self._record_transcript_event({"type": "error", "text": str(text or "")})

    def _restore_transcript(self, events):
        self._restoring_transcript = True
        try:
            self._clear_live_ui()
            for event in events or []:
                etype = event.get("type") if isinstance(event, dict) else None
                if etype == "user":
                    self._add_widget(MessageContainer(event.get("text", ""), sender_name="You", is_user=True))
                elif etype == "agent_turn":
                    self._restore_agent_turn(event)
                elif etype == "chart":
                    self._add_widget(ChartWidget(event.get("data") or {}))
                elif etype == "stats":
                    self._add_widget(StatsWidget(event.get("data") or {}))
                elif etype == "error":
                    self._add_widget(MessageContainer(html.escape(str(event.get("text", ""))), is_user=False, is_error=True))
                elif etype == "compaction":
                    self._add_compaction_notice()
        finally:
            self._restoring_transcript = False
            self._typing_widget = None
            self._current_agent_turn = None
            self._current_tool_row = None
            self._current_turn_event = None
            self._pending_tool = None
            self._streaming = False

    def _restore_agent_turn(self, event):
        turn = self._add_agent_turn_widget()
        thinking = event.get("thinking", "")
        if thinking:
            turn.add_thinking_block()
            turn.set_thinking_text(thinking)
        for tool in event.get("tools") or []:
            item = turn.add_tool(tool.get("name", "tool"), tool.get("input") or {})
            if "result" in tool:
                item.set_result(str(tool.get("result", "")), bool(tool.get("is_error", False)))
        text = event.get("text", "")
        if text:
            turn.finalize_text(text)
        turn.finalize()

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

    def _set_status(self, text: str, color: str = _TEXT_3, *, spinning: bool = False, icon: str = ""):
        """Render top-left status with the same CLI spinner language as the turn."""
        self._status_text = text or ""
        self._status_color = color
        self._status_icon = icon
        self._status_spinning = spinning
        if spinning:
            if not self._status_timer.isActive():
                self._status_timer.start()
        else:
            self._status_timer.stop()
        self._render_status()

    def _tick_status(self):
        self._status_phase = (self._status_phase + 1) % len(_SPINNER_FRAMES)
        self._render_status()

    def _render_status(self):
        if self._status_spinning:
            mark = _SPINNER_FRAMES[self._status_phase]
        else:
            mark = self._status_icon or "·"
        self.status.setText(
            f"<span style='color:{self._status_color};font-size:11px;'>{html.escape(mark)}</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>{html.escape(self._status_text)}</span>"
        )

    # ------------------------------------------------------------------ #
    def _emit_ask_user_threadsafe(self, question, options, allow_free_text):
        """Called by the toolkit from worker threads to surface a question."""
        self._ask_user_signal.emit(question, options, allow_free_text)

    def _show_ask_user(self, question, options, allow_free_text):
        """Build and show the AskUserCard as a modal-style popover.

        Renders a translucent backdrop over the entire chat body, with
        a centered card on top. The user cannot miss it — it lives in
        the middle of their attention, not in some strip near the
        input they have to hunt for.

        The overlay is a child widget of the dock's main container so
        it floats above the transcript. It is created once and shown /
        hidden as needed; the card inside is rebuilt per question.
        """
        if self._ask_user_card is not None:
            return
        self._hide_typing()
        self._ask_user_payload = None

        # ── Backdrop + card overlay ──────────────────────────────────────
        if not hasattr(self, "_ask_overlay") or self._ask_overlay is None:
            from qgis.PyQt.QtWidgets import QWidget as _QW
            self._ask_overlay = _QW(self)
            # Translucent dark backdrop — dims the chat behind the
            # card. The card itself is a separate child widget we
            # center on top.
            self._ask_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 140);")
            self._ask_overlay.setAttribute(Qt.WA_StyledBackground, True)
            # Make sure the overlay never grabs focus away from the
            # card's buttons — the card's child buttons must be
            # clickable.
            self._ask_overlay.setFocusPolicy(Qt.NoFocus)

            self._ask_card_frame = _QW(self._ask_overlay)
            # Transparent positioning frame. AskUserCard owns the actual
            # surface so we do not render a card inside another card.
            self._ask_card_frame.setObjectName("AskUserOverlayCard")
            self._ask_card_frame.setStyleSheet(f"""
                QWidget#AskUserOverlayCard {{
                    background-color: transparent;
                    border: none;
                }}
            """)
            from qgis.PyQt.QtWidgets import QVBoxLayout as _QV
            card_layout = _QV(self._ask_card_frame)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)
            # The actual AskUserCard (with question + buttons) goes here
            self._ask_card_layout = card_layout
        # Build the actual interactive card
        card = AskUserCard(question, options, allow_free_text=allow_free_text, parent=self._ask_card_frame)
        card.submitted.connect(self._resolve_ask_user)
        self._ask_card_layout.addWidget(card)
        self._ask_card = card

        # Position the overlay to cover the dock's viewport area. We
        # use the dock widget's rect, accounting for the top-bar height
        # so the overlay doesn't sit on top of the status bar.
        self._ask_overlay.setGeometry(self._overlay_rect())
        self._position_ask_card()
        self._ask_overlay.show()
        self._ask_overlay.raise_()
        # Hand keyboard focus to the first option button so the user
        # can press Enter to confirm.
        try:
            first_option = card.findChild(QFrame, "AskUserOptionRow")
            if first_option is not None:
                first_option.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass
        self._ask_user_card = card
        self._set_status("Awaiting input", _WARN, spinning=True)

    def _resolve_ask_user(self, payload):
        """User picked an option or typed a reply; close the card and unblock."""
        self._ask_user_payload = payload
        # Tear down the overlay + card so the chat becomes interactive
        # again. We keep the overlay widget itself (cheap) but hide it
        # and clear the inner card so the next question rebuilds clean.
        if self._ask_user_card is not None:
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        if hasattr(self, "_ask_overlay") and self._ask_overlay is not None:
            self._ask_overlay.hide()
        if hasattr(self, "_ask_card_layout") and self._ask_card_layout is not None:
            # Remove all children so the next ask starts fresh
            while self._ask_card_layout.count():
                item = self._ask_card_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
        label = payload.get("choice")
        text = payload.get("free_text")
        chosen_text = label or text
        if chosen_text:
            try:
                self._get_or_create_agent_turn().set_user_decision(chosen_text)
            except Exception:
                pass
        self._set_status("Ready", _SUCCESS, icon="✓")
        if self._toolkit is not None:
            cancelled = bool(payload.get("cancelled", False))
            self._toolkit._resolve_ask_user({
                "choice": payload.get("choice"),
                "free_text": payload.get("free_text"),
                "cancelled": cancelled,
            })

    def _overlay_rect(self):
        """Return the rect for the ask_user overlay.

        Covers the chat body and input area, but skips the top status
        bar. Coordinates are in the parent (dock widget) frame.
        """
        # The dock widget's body rect, in dock-local coordinates.
        return self.rect()

    def _position_ask_card(self):
        if not hasattr(self, "_ask_overlay") or self._ask_overlay is None:
            return
        if not hasattr(self, "_ask_card_frame") or self._ask_card_frame is None:
            return
        ov = self._ask_overlay.geometry()
        available_w = max(280, ov.width() - 32)
        card_w = min(560, available_w)
        self._ask_card_frame.setFixedWidth(card_w)
        card = getattr(self, "_ask_card", None)
        if card is not None:
            card.setFixedWidth(card_w)
            card_size = card.sizeHint()
        else:
            card_size = self._ask_card_frame.sizeHint()
        available_h = max(240, ov.height() - 32)
        card_h = min(max(card_size.height(), 240), available_h)
        cx = ov.x() + (ov.width() - card_w) // 2
        cy = ov.y() + (ov.height() - card_h) // 2
        self._ask_card_frame.setGeometry(cx, cy, card_w, card_h)

    def resizeEvent(self, event):
        # Keep the ask_user overlay sized to the dock if it's visible.
        if hasattr(self, "_ask_overlay") and self._ask_overlay is not None and self._ask_overlay.isVisible():
            self._ask_overlay.setGeometry(self.rect())
            self._position_ask_card()
        super().resizeEvent(event)

    # ------------------------------------------------------------------ #
    def _show_startup_session_picker(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Chat sessions")
        dialog.setModal(True)
        dialog.setStyleSheet(f"QDialog {{ background: {_CANVAS}; color: {_TEXT}; }}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Choose a chat session")
        title.setStyleSheet(f"color:{_TEXT}; font-size:13px; font-weight:600;")
        layout.addWidget(title)

        previous = QPushButton("Continue previous")
        sessions = QPushButton("Session list")
        new_session = QPushButton("New session")
        for button in (previous, sessions, new_session):
            button.setMinimumHeight(30)
            button.setStyleSheet(self._session_dialog_button_style())
            layout.addWidget(button)

        previous.clicked.connect(lambda: (dialog.accept(), self._switch_to_session(self._session_store.active_session()["id"])))
        sessions.clicked.connect(lambda: (dialog.accept(), self._show_session_list()))
        new_session.clicked.connect(lambda: (dialog.accept(), self._new_session_from_menu()))
        dialog.exec_()

    def _session_dialog_button_style(self):
        return f"""
            QPushButton {{
                background: {_SURFACE};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {_SURFACE_2};
            }}
        """

    def _prompt_session_name(self, title, current=""):
        text, accepted = QInputDialog.getText(self, title, "Name:", text=current or "")
        if not accepted:
            return None
        return (text or DEFAULT_SESSION_NAME).strip() or DEFAULT_SESSION_NAME

    def _new_session_from_menu(self):
        name = self._prompt_session_name("New session", "")
        if name is None:
            return
        self._save_current_session()
        session = self._session_store.create_session(name)
        self._switch_to_session(session["id"], save_current=False)

    def _rename_current_session(self):
        session = self._session_store.get_session(self._active_session_id)
        if session is None:
            return
        name = self._prompt_session_name("Rename session", session.get("name", ""))
        if name is None:
            return
        self._session_store.rename_session(self._active_session_id, name)

    def _delete_current_session(self):
        self._delete_session(self._active_session_id)

    def _show_session_list(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sessions")
        dialog.setModal(True)
        dialog.setStyleSheet(f"QDialog {{ background: {_CANVAS}; color: {_TEXT}; }}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        for session in self._session_store.list_sessions():
            row = QWidget(dialog)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            label = QLabel(f"{session.get('name', DEFAULT_SESSION_NAME)}\n{self._format_session_time(session.get('updated_at'))}")
            label.setStyleSheet(f"color:{_TEXT}; font-size:11px; background:transparent;")
            row_layout.addWidget(label, 1)
            for text, handler in (
                ("Open", lambda _checked=False, sid=session["id"]: (dialog.accept(), self._switch_to_session(sid))),
                ("Rename", lambda _checked=False, sid=session["id"]: self._rename_session_from_list(sid, dialog)),
                ("Delete", lambda _checked=False, sid=session["id"]: self._delete_session_from_list(sid, dialog)),
            ):
                button = QPushButton(text)
                button.setFixedHeight(28)
                button.setStyleSheet(self._session_dialog_button_style())
                button.clicked.connect(handler)
                row_layout.addWidget(button)
            layout.addWidget(row)

        close = QPushButton("Close")
        close.setStyleSheet(self._session_dialog_button_style())
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec_()

    def _rename_session_from_list(self, session_id, dialog):
        session = self._session_store.get_session(session_id)
        if session is None:
            return
        name = self._prompt_session_name("Rename session", session.get("name", ""))
        if name is None:
            return
        self._session_store.rename_session(session_id, name)
        dialog.accept()
        self._show_session_list()

    def _delete_session_from_list(self, session_id, dialog):
        if self._delete_session(session_id):
            dialog.accept()
            self._show_session_list()

    def _delete_session(self, session_id):
        session = self._session_store.get_session(session_id)
        if session is None:
            return False
        answer = QMessageBox.question(
            self,
            "Delete session",
            f"Delete '{session.get('name', DEFAULT_SESSION_NAME)}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
        if session_id == self._active_session_id:
            self._stop_active_worker()
        fallback = self._session_store.delete_session(session_id)
        if session_id == self._active_session_id and fallback is not None:
            self._switch_to_session(fallback["id"], save_current=False)
        return True

    @staticmethod
    def _format_session_time(value):
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return str(value)

    def _stop_active_worker(self):
        if self._worker is None:
            return False
        self._stop_requested = True
        self._worker.stop()
        if self._request_cancel is not None:
            try:
                self._request_cancel()
            except Exception:
                pass
        if self._ask_user_card is not None and self._toolkit is not None:
            self._toolkit._resolve_ask_user({
                "choice": None, "free_text": None, "cancelled": True,
            })
        return True

    def _save_current_session(self):
        if not self._active_session_id:
            return
        self._session_store.save_session(
            self._active_session_id,
            backend_history=list(self._history),
            transcript_events=list(self._transcript_events),
            backend_state=self._export_backend_state(),
        )

    def _export_backend_state(self):
        try:
            backend = self._get_backend()
        except Exception:
            backend = None
        if backend is None or not hasattr(backend, "export_session_state"):
            return {}
        try:
            return backend.export_session_state() or {}
        except Exception:
            return {}

    def _import_backend_state(self, state):
        try:
            backend = self._get_backend()
        except Exception:
            backend = None
        if backend is None or not hasattr(backend, "import_session_state"):
            return
        try:
            backend.import_session_state(state or {})
        except Exception:
            pass

    def _restore_active_session(self):
        session = self._session_store.active_session()
        self._active_session_id = session["id"]
        self._history = list(session.get("backend_history") or [])
        self._transcript_events = list(session.get("transcript_events") or [])
        self._import_backend_state(session.get("backend_state") or {})
        self._restore_transcript(self._transcript_events)

    def _switch_to_session(self, session_id, save_current=True):
        if save_current and session_id != self._active_session_id:
            self._save_current_session()
        self._stop_active_worker()
        if not self._session_store.set_active_session(session_id):
            return False
        session = self._session_store.get_session(session_id)
        if session is None:
            return False
        self._active_session_id = session_id
        self._history = list(session.get("backend_history") or [])
        self._transcript_events = list(session.get("transcript_events") or [])
        self._import_backend_state(session.get("backend_state") or {})
        self._restore_transcript(self._transcript_events)
        self._set_status("Ready", _SUCCESS, icon="✓")
        return True

    def _clear_transcript_widgets(self):
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _clear(self):
        self._stop_active_worker()
        self._history = []
        self._transcript_events = []
        self._current_turn_event = None
        self._clear_live_ui()
        self._save_current_session()

    def _clear_live_ui(self):
        worker_was_active = self._worker is not None
        # Stop any active worker BEFORE clearing widgets to prevent crashes
        if worker_was_active:
            self._stop_requested = True
            self._worker.stop()
            # The worker's QThread.finished signal releases self._worker.
        if self._ask_user_card is not None:
            if self._toolkit is not None:
                self._toolkit._resolve_ask_user({
                    "choice": None, "free_text": None, "cancelled": True,
                })
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        # Hide the overlay (modal dialog state) on clear
        if hasattr(self, "_ask_overlay") and self._ask_overlay is not None:
            self._ask_overlay.hide()
        self._clear_transcript_widgets()
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._tool_progress_text = ""
        self._showing_tool_progress = False
        self._pending_stream_render = False
        self._pending_stream_kind = None
        self._pending_stream_scroll = False
        self._stream_render_timer.stop()
        self._last_stream_render_at = 0.0
        self._thinking_text = ""
        self._thinking_started = False
        self._scroll_locked = False
        if not worker_was_active:
            self._stop_requested = False

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

        self._remember_prompt(message)
        self.input.clear()
        self._resize_input()
        self._add_user_message(message)
        # Reset scroll lock and force-scroll to bottom. The user just hit
        # send, so they want to see the response that follows. We use
        # the deferred-scroll path so the new message widget has been
        # laid out into the scroll range first.
        self._scroll_locked = False
        self._scroll_to_bottom_after_layout()
        self._stop_requested = False
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._tool_progress_text = ""
        self._showing_tool_progress = False
        self._pending_stream_render = False
        self._pending_stream_kind = None
        self._pending_stream_scroll = False
        self._stream_render_timer.stop()
        self._last_stream_render_at = 0.0
        self._thinking_text = ""
        self._current_agent_turn = None
        self._current_tool_row = None
        self._thinking_started = False

        self.send_btn.setEnabled(False)
        self.send_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self._set_status("Thinking", _TEXT_3, spinning=True)
        self._set_tool_progress("Thinking...")

        self._worker = ChatWorker(backend, message, self._history)
        self._worker.event.connect(self._on_event)
        self._worker.finished_history.connect(
            lambda history, worker=self._worker: self._on_finished(history, worker)
        )
        self._worker.finished.connect(
            lambda *_, worker=self._worker: self._on_worker_thread_finished(worker)
        )
        self._worker.start()

    def _on_stop(self):
        if self._worker is not None:
            self._stop_requested = True
            self._worker.stop()
            # Cooperatively cancel any main-thread operation (run_pyqgis,
            # processing.run, create_chart, get_layer_statistics). The worker
            # also stops the agent loop, but main-thread work needs a separate
            # signal because it can be stuck inside exec/processing.run.
            if self._request_cancel is not None:
                try:
                    self._request_cancel()
                except Exception:
                    pass
            if self._ask_user_card is not None and self._toolkit is not None:
                self._toolkit._resolve_ask_user({
                    "choice": None, "free_text": None, "cancelled": True,
                })
            self.stop_btn.setEnabled(False)
            self._set_status("Stopping", _DANGER, spinning=True)

    # ------------------------------------------------------------------ #
    def _on_event(self, ev):
        if self._stop_requested:
            return
        if ev.type == EventType.TEXT:
            self._hide_typing()
            delta = ev.data.get("text", "")
            if delta:
                turn = self._get_or_create_agent_turn()
                if self._current_tool_row is not None:
                    self._current_tool_row.append_reasoning(delta)
                    if self._tool_progress_text and not self._tool_progress_text.endswith(("\n", " ")):
                        self._tool_progress_text += "\n"
                    self._tool_progress_text += delta
                    self._showing_tool_progress = True
                    self._schedule_stream_render("tool")
                    return

                # Final-answer text begins after thinking/progress. Collapse
                # the thinking block so the turn stays compact.
                if self._showing_tool_progress:
                    turn.clear_streaming_text()
                    self._tool_progress_text = ""
                    self._showing_tool_progress = False
                    self._last_stream_render_at = 0.0
                if self._thinking_started and self._current_agent_turn is not None:
                    try:
                        self._current_agent_turn.finalize_thinking()
                    except Exception:
                        pass
                    self._thinking_started = False

                if not self._streaming:
                    self._streaming = True
                self._current_text += delta
                self._schedule_stream_render("final")

        elif ev.type == EventType.TOOL_USE:
            self._flush_stream_render()
            self._hide_typing()
            turn = self._get_or_create_agent_turn()
            turn_event = self._ensure_current_turn_event()
            if self._current_text:
                # In a tool turn, any prose emitted before the tool call is
                # progress/reasoning. Keep it visible in the thinking block
                # and remove it from the final-answer buffer.
                self._append_thinking_text(self._current_text)
                turn.clear_streaming_text()
                self._current_text = ""
                self._streaming = False
            self._finish_streaming()
            tool_name = ev.data.get("name", "tool")
            tool_input = ev.data.get("input", {})
            if self._showing_tool_progress:
                turn.clear_streaming_text()
                self._tool_progress_text = ""
                self._showing_tool_progress = False
            self._pending_tool = (tool_name, tool_input)
            self._current_tool_row = turn.add_tool(tool_name, tool_input)
            if turn_event is not None:
                turn_event.setdefault("tools", []).append({
                    "name": tool_name,
                    "input": tool_input,
                })
            self._set_tool_progress(f"Processing `{tool_name}`...\n")
            self._maybe_scroll_to_bottom()

        elif ev.type == EventType.TOOL_RESULT:
            self._flush_stream_render()
            result = ev.data.get("result", "")
            tool_name = ev.data.get("name", "tool")
            # F11: prefer the structured is_error / cancelled flags that
            # the backends compute. Fall back to the old string-prefix
            # heuristic for forward-compat with any third-party backend.
            is_err = ev.data.get("is_error")
            if is_err is None:
                is_err = (str(result).startswith("Error")
                          or str(result).startswith("error"))
            is_cancelled = bool(ev.data.get("cancelled"))
            if self._current_tool_row is not None:
                self._current_tool_row.set_result(str(result), is_err)
                self._current_tool_row = None
            turn_event = self._ensure_current_turn_event()
            if turn_event is not None:
                tools = turn_event.setdefault("tools", [])
                if tools:
                    tools[-1].update({
                        "name": tool_name,
                        "result": str(result),
                        "is_error": bool(is_err),
                        "cancelled": is_cancelled,
                    })
            self._pending_tool = None
            # Surface cancellation as a clear status update so the user
            # knows the tool didn't return a real error.
            if is_cancelled:
                self._set_tool_progress(f"Cancelled `{tool_name}`.")
                self._set_status("Cancelled", _DANGER, icon="!")
            else:
                if is_err:
                    self._set_tool_progress(f"`{tool_name}` returned an error. Preparing next step...")
                else:
                    self._set_tool_progress(f"Finished `{tool_name}`. Preparing answer...")
                self._set_status("Thinking", _TEXT_3, spinning=True)
            self._maybe_scroll_to_bottom()

        elif ev.type == EventType.ASK_USER:
            self._flush_stream_render()
            self._show_ask_user(
                ev.data.get("question", ""),
                ev.data.get("options", []),
                ev.data.get("allow_free_text", True),
            )

        elif ev.type == EventType.VISUALIZATION:
            self._flush_stream_render()
            viz = ev.data.get("type")
            d = ev.data.get("data", {})
            if viz == "chart":
                self._add_chart(d)
            elif viz == "stats":
                self._add_stats(d)

        elif ev.type == EventType.THINKING:
            self._flush_stream_render()
            self._hide_typing()
            thinking_text = ev.data.get("text", "")
            if thinking_text:
                try:
                    self._append_thinking_text(thinking_text)
                except Exception:
                    pass
            self._set_status("Thinking", _TEXT_3, spinning=True)

        elif ev.type == EventType.COMPACTION:
            self._flush_stream_render()
            self._add_compaction_notice()

        elif ev.type == EventType.ERROR:
            self._flush_stream_render()
            self._hide_typing()
            self._finish_streaming()
            self._add_error_message(str(ev.data.get("error", "")))

        elif ev.type == EventType.DONE:
            self._flush_stream_render()
            self._hide_typing()
            self._finish_streaming()
            self._finalize_current_turn_event()
            self._streaming = False
            self._thinking_started = False
            self._thinking_text = ""
            self._tool_progress_text = ""
            self._showing_tool_progress = False
            self._current_agent_turn = None
            self._set_status("Ready", _SUCCESS, icon="✓")
            self._scroll_to_bottom()
            self._pending_tool = None

    def _finish_streaming(self):
        """Finalize streaming text in current agent turn — applies full markdown.

        The bubble already received every final-answer token via
        set_streaming_text, so we just call finalize_text to apply full
        markdown (code blocks, tables, headings) and drop the cursor.
        Tool/progress prose lives in thinking/tool-row buffers instead.
        """
        self._flush_stream_render()
        if self._current_text:
            turn = self._get_or_create_agent_turn()
            turn.finalize_text(self._current_text)
            turn.finalize()
            turn_event = self._ensure_current_turn_event()
            if turn_event is not None:
                turn_event["text"] = self._current_text
        elif self._current_agent_turn is None:
            # No text and no turn yet — create an empty turn so the tool row
            # has somewhere to live.
            self._get_or_create_agent_turn()

    def _set_tool_progress(self, text: str):
        """Show temporary tool progress in the response area.

        This is UI-owned progress, not final answer content. It is cleared as
        soon as the backend starts streaming the actual answer.
        """
        if not text:
            return
        self._flush_stream_render()
        turn = self._get_or_create_agent_turn()
        self._tool_progress_text = text
        self._showing_tool_progress = True
        turn.set_progress_text(self._tool_progress_text)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _schedule_stream_render(self, kind: str):
        """Throttle expensive rich-text rendering during stream floods."""
        self._pending_stream_render = True
        self._pending_stream_kind = kind
        self._pending_stream_scroll = self._pending_stream_scroll or not self._scroll_locked

        now = time.monotonic()
        elapsed = now - self._last_stream_render_at
        if self._last_stream_render_at <= 0.0 or elapsed >= _STREAM_RENDER_INTERVAL_S:
            self._flush_stream_render(now)
            return

        delay_ms = max(1, int((_STREAM_RENDER_INTERVAL_S - elapsed) * 1000))
        if not self._stream_render_timer.isActive():
            self._stream_render_timer.start(delay_ms)

    def _flush_stream_render(self, now=None):
        """Render the latest accumulated stream state once."""
        if not self._pending_stream_render:
            return
        self._stream_render_timer.stop()
        kind = self._pending_stream_kind
        should_scroll = self._pending_stream_scroll and not self._scroll_locked
        self._pending_stream_render = False
        self._pending_stream_kind = None
        self._pending_stream_scroll = False

        turn = self._get_or_create_agent_turn()
        if kind == "tool":
            turn.set_streaming_text(self._tool_progress_text)
        elif kind == "final":
            turn.set_streaming_text(self._current_text)

        self._last_stream_render_at = now if now is not None else time.monotonic()
        if should_scroll:
            self._scroll_to_bottom()

    def _append_thinking_text(self, text: str):
        """Append progress/reasoning text into the turn's thinking block."""
        if not text:
            return
        turn = self._get_or_create_agent_turn()
        if not self._thinking_started:
            try:
                turn.add_thinking_block()
            except Exception:
                pass
            self._thinking_started = True

        if self._thinking_text and not self._thinking_text.endswith((" ", "\n")):
            if not text.startswith((" ", "\n", ".", ",", ":", ";", ")")):
                self._thinking_text += " "
        self._thinking_text += text
        turn.set_thinking_text(self._thinking_text)
        turn_event = self._ensure_current_turn_event()
        if turn_event is not None:
            turn_event["thinking"] = self._thinking_text

    def _on_finished(self, history, worker=None):
        if worker is not None and self._worker is not None and worker is not self._worker:
            return
        self._history = history if history is not None else self._history
        self._save_current_session()
        # NOTE: do NOT call _finish_streaming here — the DONE event handler
        # already finalized the turn and reset _current_agent_turn to None.
        # Calling it again would re-create an empty turn and call
        # finalize_text(self._current_text) on it, producing a second bubble
        # with the same text (the "double response" bug).
        self._hide_typing()
        self.send_btn.setEnabled(True)
        self.send_btn.setVisible(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self._scroll_locked = False
        self._thinking_started = False
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._stop_requested = False

    def _on_worker_thread_finished(self, worker):
        for signal_name in ("event", "finished_history", "finished"):
            try:
                getattr(worker, signal_name).disconnect()
            except Exception:
                pass
        if worker is self._worker:
            self._worker = None
