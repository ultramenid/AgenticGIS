"""The in-QGIS chat dock: minimal, refined chat interface.

Inspired by Dribbble's "Minimal Chat Box UI" — clean off-white surface,
soft pill input, restrained type, generous spacing. Works in light
mode (default) and respects QGIS's own theme via neutral grays.
"""

import html
import threading
import time
from collections import deque
from datetime import datetime

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QEvent, QThread, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QFont, QTextCursor
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..backends.base import AgentEvent, EventType, should_compact
from ..core.dev_logging import log_ttft_event, new_trace_id
from ..core.qt_compat import QUEUED_CONNECTION
from ..core.session_store import DEFAULT_SESSION_NAME, SessionStore
from .agent_turn_bubble import AgentTurnBubble, _SPINNER_FRAMES
from .chart_widget import ChartWidget
from .download_card import DownloadWidget
from .gif_widget import GifWidget
from .message_bubble import MessageContainer
from .stats_widget import StatsWidget
from .typing_indicator import TypingIndicator
from .ask_user_card import AskUserCard
from .theme import (
    DOCK_CANVAS as _CANVAS,
    DOCK_SURFACE as _SURFACE,
    DOCK_SURFACE_2 as _SURFACE_2,
    DOCK_BORDER as _BORDER,
    DOCK_BORDER_SOFT as _BORDER_SOFT,
    DOCK_TEXT as _TEXT,
    DOCK_TEXT_2 as _TEXT_2,
    DOCK_TEXT_3 as _TEXT_3,
    DOCK_TEXT_4 as _TEXT_4,
    DOCK_ACCENT as _ACCENT,
    DOCK_ACCENT_DIM as _ACCENT_DIM,
    DOCK_ACCENT_HOV as _ACCENT_HOV,
    DOCK_WARN as _WARN,
    DOCK_SUCCESS as _SUCCESS,
    DOCK_DANGER as _DANGER,
)
_STREAM_COALESCE_INTERVAL_S = 0.030
_STREAM_COALESCE_MAX_CHARS = 8192
_STREAM_RENDER_INTERVAL_S = 0.050

_MAX_EVENTS_BUFFERED = 1000
_BUFFER_DROP_COUNT = 500

_MAX_TURN_TEXT_CHARS = 200_000
_MAX_THINKING_TEXT_CHARS = 50_000


class ChatWorker(QThread):
    event = pyqtSignal(object)
    finished_history = pyqtSignal(object)

    def __init__(
        self,
        backend,
        message,
        history,
        parent=None,
        trace_id=None,
        trace_started_at=None,
    ):
        super().__init__(parent)
        self._backend = backend
        self._message = message
        self._history = history
        self._trace_id = trace_id
        self._trace_started_at = trace_started_at
        self._first_text_received = False
        self._stop = False
        self._coalesce_type = None
        self._coalesce_text = ""
        self._last_coalesce_flush = time.monotonic()
        self._event_buffer = deque()
        self._last_buffer_flush = time.monotonic()

    def stop(self):
        self._stop = True
        try:
            cancel = getattr(self._backend, "cancel_current_request", None)
            if callable(cancel):
                cancel()
        except Exception:  # nosec B110
            pass

    def run(self):
        log_ttft_event(
            "worker_started",
            trace_id=self._trace_id,
            started_at=self._trace_started_at,
        )
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
            except Exception:  # nosec B110
                pass

    def _emit_event(self, ev):
        """Emit backend events with source-side backpressure for text floods.

        Qt queues cross-thread signals. If a backend emits thousands of token
        deltas, queueing each token can make QGIS feel stuck even if each paint
        is cheap. Coalescing adjacent TEXT/THINKING deltas preserves content and
        ordering while sharply reducing queued signal count. Non-stream events
        are buffered and flushed so the signal queue never grows unbounded.
        """
        if ev.type in (EventType.TEXT, EventType.THINKING):
            delta = ev.data.get("text", "")
            if delta:
                if ev.type == EventType.TEXT and not self._first_text_received:
                    self._first_text_received = True
                    log_ttft_event(
                        "first_text_received",
                        trace_id=self._trace_id,
                        started_at=self._trace_started_at,
                    )
                    self._flush_coalesced_event()
                    try:
                        self.event.emit(ev)
                    except RuntimeError:
                        return
                    log_ttft_event(
                        "first_text_emitted",
                        trace_id=self._trace_id,
                        started_at=self._trace_started_at,
                    )
                    return
                if self._coalesce_type is not None and self._coalesce_type != ev.type:
                    self._flush_coalesced_event()
                self._coalesce_type = ev.type
                self._coalesce_text += delta
                now = time.monotonic()
                has_coalesce_max = len(self._coalesce_text) >= _STREAM_COALESCE_MAX_CHARS
                is_coalesce_stale = now - self._last_coalesce_flush >= _STREAM_COALESCE_INTERVAL_S
                if has_coalesce_max or is_coalesce_stale:
                    self._flush_coalesced_event(now)
                return

        self._flush_coalesced_event()
        self._event_buffer.append(ev)
        if len(self._event_buffer) > _MAX_EVENTS_BUFFERED:
            for _ in range(_BUFFER_DROP_COUNT):
                if self._event_buffer:
                    self._event_buffer.popleft()
        self._flush_event_buffer()

    def _flush_event_buffer(self):
        while self._event_buffer:
            ev = self._event_buffer.popleft()
            try:
                self.event.emit(ev)
            except RuntimeError:
                pass

    def _flush_coalesced_event(self, now=None):
        if self._coalesce_text and self._coalesce_type is not None:
            try:
                self._event_buffer.append(AgentEvent(self._coalesce_type, {"text": self._coalesce_text}))
            except RuntimeError:
                pass
            self._coalesce_type = None
            self._coalesce_text = ""
            self._last_coalesce_flush = now if now is not None else time.monotonic()
        self._flush_event_buffer()


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
        self._history_generation = 0  # incremented on every send + session switch
        self._prompt_history = []
        self._prompt_history_index = None
        self._prompt_history_draft = ""
        self._worker = None
        self._stop_requested = False
        self._streaming = False
        self._last_prewarm_at = 0.0
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
        self._ttft_trace_id = None
        self._ttft_started_at = None
        self._ttft_first_ui_render_logged = False
        self._thinking_text = ""           # accumulated thinking/progress text
        self._thinking_started = False     # whether add_thinking_block was called this turn
        self._current_text_truncated = False
        self._ask_user_card = None
        self._ask_user_payload = None
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._status_phase = 0
        self._status_text = "Ready"
        self._status_color = _TEXT_3
        self._status_icon = "✓"
        self._status_spinning = False
        self._status_session_name = DEFAULT_SESSION_NAME
        self._last_escape_press_at = 0.0
        self._build_ui()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(120)
        self._status_timer.timeout.connect(self._tick_status)
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.timeout.connect(self._flush_stream_render)
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._ask_user_signal.connect(self._show_ask_user, QUEUED_CONNECTION)
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
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setStyleSheet("background: transparent; padding-right: 4px;")
        top.addWidget(self.status)

        top.addStretch(1)

        for label, tip, width in (
            ("Setting", "Settings", 58),
            ("▦ Session", "Chat sessions", 76),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedSize(width, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 10px;
                    font-weight: 500;
                    letter-spacing: 0;
                    border: none;
                    border-radius: 6px;
                    background: transparent;
                    color: {_TEXT_3};
                    padding: 0px;
                    text-align: center;
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
        self._session_btn.clicked.connect(self._show_session_menu)
        layout.addLayout(top)

        # -- Hairline divider -------------------------------------------- #
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {_BORDER}; border: none;")
        layout.addWidget(divider)

        # -- Scrollable transcript --------------------------------------- #
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        divider2.setFrameShape(QFrame.Shape.HLine)
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
        self.input.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setFixedHeight(self._input_min_h)
        self.input.document().setDocumentMargin(0)
        mono_font = QFont("JetBrains Mono")
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
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
        field_row.addWidget(self.input, 1, Qt.AlignmentFlag.AlignVCenter)

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
        field_row.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignVCenter)

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
        field_row.addWidget(self.stop_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        input_bar.addWidget(input_frame)
        layout.addWidget(input_wrap)

        self.setWidget(container)

        # Install event filter on the input widget so Enter-to-send works
        self.input.installEventFilter(self)

    def _maybe_prewarm(self):
        """Fire prewarm in a daemon thread if the connection may have gone stale.

        Throttled to at most once every 120 s and never while streaming, so
        repeated calls (e.g. from key-press events) are essentially free.
        """
        if self._streaming:
            return
        now = time.monotonic()
        if now - self._last_prewarm_at <= 120.0:
            return
        self._last_prewarm_at = now

        def _prewarm():
            try:
                backend = self._get_backend()
                if backend is not None:
                    backend.prewarm()
            except Exception:  # nosec B110
                pass

        try:
            threading.Thread(
                target=_prewarm, name="agenticgis-prewarm", daemon=True
            ).start()
        except Exception:  # nosec B110
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)
        self._maybe_prewarm()
        if self._show_startup_picker and not self._startup_picker_shown and self._session_store.had_existing_sessions:
            self._startup_picker_shown = True
            QTimer.singleShot(0, self._show_startup_session_picker)

    def closeEvent(self, event):
        self._stop_active_worker()
        self._status_timer.stop()
        self._stream_render_timer.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            # Keep transcript widget exactly as wide as the viewport so no
            # child widget can cause horizontal overflow or sideways scrolling.
            self.transcript_widget.setFixedWidth(event.size().width())
            return False
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            self._maybe_prewarm()
            if event.key() == Qt.Key.Key_Escape:
                return self._handle_input_escape()
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                if self._handle_prompt_history_key(event.key()):
                    return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._newline_modifier(event.modifiers()):
                    self.input.insertPlainText("\n")
                    self._resize_input()
                    return True
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _newline_modifier(self, modifiers):
        return bool(modifiers & (
            Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
            | Qt.KeyboardModifier.ControlModifier
        ))

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
        if key == Qt.Key.Key_Up and block_number > 0:
            return False
        if key == Qt.Key.Key_Down and block_number < last_block:
            return False

        if key == Qt.Key.Key_Up:
            if self._prompt_history_index is None:
                self._prompt_history_draft = self.input.toPlainText()
                self._prompt_history_index = len(self._prompt_history) - 1
            else:
                self._prompt_history_index = max(0, self._prompt_history_index - 1)
            self._set_input_text_from_history(self._prompt_history[self._prompt_history_index])
            return True

        if key == Qt.Key.Key_Down:
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
        self.input.moveCursor(QTextCursor.MoveOperation.End)
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
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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

    def _add_gif(self, data):
        self._add_widget(GifWidget(data))
        self._record_transcript_event({"type": "gif", "data": data})

    def _add_file(self, data):
        self._add_widget(DownloadWidget(data))
        self._record_transcript_event({"type": "file", "data": data})

    def _add_compaction_notice(self):
        w = QLabel("── history compacted ──")
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self._clear_live_ui()
        self._pending_restore_events = list(events or [])
        self._restore_index = 0
        self._restore_transcript_batch()

    def _restore_transcript_batch(self):
        batch_size = 20
        self.transcript_widget.setUpdatesEnabled(False)
        try:
            for _ in range(batch_size):
                if self._restore_index >= len(self._pending_restore_events):
                    self._finish_restore()
                    return
                event = self._pending_restore_events[self._restore_index]
                self._restore_index += 1
                etype = event.get("type") if isinstance(event, dict) else None
                if etype == "user":
                    self._add_widget(MessageContainer(event.get("text", ""), sender_name="You", is_user=True))
                elif etype == "agent_turn":
                    self._restore_agent_turn(event)
                elif etype == "chart":
                    self._add_widget(ChartWidget(event.get("data") or {}))
                elif etype == "stats":
                    self._add_widget(StatsWidget(event.get("data") or {}))
                elif etype == "gif":
                    self._add_widget(GifWidget(event.get("data") or {}))
                elif etype == "file":
                    self._add_widget(DownloadWidget(event.get("data") or {}))
                elif etype == "error":
                    self._add_widget(MessageContainer(html.escape(
                        str(event.get("text", ""))), is_user=False, is_error=True))
                elif etype == "compaction":
                    self._add_compaction_notice()
        finally:
            self.transcript_widget.setUpdatesEnabled(True)
        QTimer.singleShot(0, self._restore_transcript_batch)

    def _finish_restore(self):
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
        session_name = self._status_session_name or DEFAULT_SESSION_NAME
        try:
            self.status.setText(
                f"<span style='color:{self._status_color};font-size:11px;'>{html.escape(mark)}</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>{html.escape(self._status_text)}</span>"
                f"<span style='color:{_TEXT_4}; font-size:11px;'> - </span>"
                f"<span style='color:{_TEXT_2}; font-size:11px;'>{html.escape(session_name)}</span>"
            )
        except RuntimeError:
            pass

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
            self._ask_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            # Make sure the overlay never grabs focus away from the
            # card's buttons — the card's child buttons must be
            # clickable.
            self._ask_overlay.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            self._ask_card_frame = _QW(self._ask_overlay)
            # Transparent positioning frame. AskUserCard owns the actual
            # surface so we do not render a card inside another card.
            self._ask_card_frame.setObjectName("AskUserOverlayCard")
            self._ask_card_frame.setStyleSheet("""
                QWidget#AskUserOverlayCard {
                    background-color: transparent;
                    border: none;
                }
            """)
            from qgis.PyQt.QtWidgets import QVBoxLayout as _QV
            card_layout = _QV(self._ask_card_frame)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)
            # The actual AskUserCard (with question + buttons) goes here
            self._ask_card_layout = card_layout
            # A scroll area wraps the card so a long question scrolls instead
            # of overflowing and clipping the header/options.
            from qgis.PyQt.QtWidgets import QScrollArea as _QSA
            scroll = _QSA(self._ask_card_frame)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setStyleSheet("background: transparent; border: none;")
            scroll.viewport().setStyleSheet("background: transparent;")
            card_layout.addWidget(scroll)
            self._ask_scroll = scroll
        # Build the actual interactive card
        card = AskUserCard(question, options, allow_free_text=allow_free_text, parent=self._ask_card_frame)
        card.submitted.connect(self._resolve_ask_user)
        self._ask_scroll.setWidget(card)
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
                first_option.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:  # nosec B110
            pass
        self._ask_user_card = card
        self._set_status("Awaiting input", _WARN, spinning=True)

    def _resolve_ask_user(self, payload):
        """User picked an option or typed a reply; close the card and unblock."""
        self._ask_user_payload = payload
        # Tear down the overlay + card so the chat becomes interactive
        # again. We keep the overlay widget itself (cheap) but hide it
        # and clear the inner card so the next question rebuilds clean.
        # Detach the card from the scroll area first so the next ask starts
        # fresh, but keep the reusable scroll area in the layout.
        if hasattr(self, "_ask_scroll") and self._ask_scroll is not None:
            old = self._ask_scroll.takeWidget()
            if old is not None:
                old.deleteLater()
        if self._ask_user_card is not None:
            self._ask_user_card = None
        self._ask_card = None
        if hasattr(self, "_ask_overlay") and self._ask_overlay is not None:
            self._ask_overlay.hide()
        label = payload.get("choice")
        text = payload.get("free_text")
        chosen_text = label or text
        if chosen_text:
            try:
                self._get_or_create_agent_turn().set_user_decision(chosen_text)
            except Exception:  # nosec B110
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
        # Content height at this width; the scroll area shows a viewport of
        # this height (capped to the overlay) and scrolls any overflow.
        card = getattr(self, "_ask_card", None)
        if card is not None:
            content_h = card.heightForWidth(card_w)
            if content_h <= 0:
                content_h = card.sizeHint().height()
        else:
            content_h = self._ask_card_frame.sizeHint().height()
        available_h = max(240, ov.height() - 32)
        card_h = min(max(content_h, 240), available_h)
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
    def _show_session_menu(self):
        pos = self._session_btn.mapToGlobal(self._session_btn.rect().bottomRight())
        pos.setX(pos.x() - self._session_menu.sizeHint().width())
        self._session_menu.exec(pos)

    def _show_startup_session_picker(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Chat sessions")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(self._session_dialog_style())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        card, card_layout = self._session_dialog_card("Choose a chat session", "Continue or start fresh")
        layout.addWidget(card)

        previous = QPushButton("Continue previous")
        sessions = QPushButton("Session list")
        new_session = QPushButton("New session")
        for button in (previous, sessions, new_session):
            button.setMinimumHeight(36)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(self._session_dialog_button_style("wide"))
            card_layout.addWidget(button)

        previous.clicked.connect(lambda: (dialog.accept(), self._switch_to_session(
            self._session_store.active_session()["id"])))
        sessions.clicked.connect(lambda: (dialog.accept(), self._show_session_list()))
        new_session.clicked.connect(lambda: (dialog.accept(), self._new_session_from_menu()))
        dialog.exec()

    def _session_dialog_style(self):
        return f"""
            QDialog {{
                background-color: {_CANVAS};
                color: {_TEXT};
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """

    def _session_dialog_card(self, title, subtitle=""):
        card = QFrame()
        card.setObjectName("SessionDialogCard")
        card.setStyleSheet(f"""
            QFrame#SessionDialogCard {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        marker = QLabel("")
        marker.setFixedSize(9, 9)
        marker.setStyleSheet(f"background:{_WARN}; border:1px solid {_WARN}; border-radius:4px;")
        header_row.addWidget(marker, 0, Qt.AlignmentFlag.AlignVCenter)
        header = QLabel(title)
        header.setFont(QFont("JetBrains Mono", 10, QFont.Weight.DemiBold))
        header.setStyleSheet(f"color:{_TEXT_2}; font-size:11px;")
        header_row.addWidget(header)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        if subtitle:
            desc = QLabel(subtitle)
            desc.setWordWrap(True)
            desc.setFont(QFont("JetBrains Mono", 10))
            desc.setStyleSheet(f"color:{_TEXT_3}; font-size:11px; line-height:1.35;")
            layout.addWidget(desc)
        return card, layout

    def _build_session_name_dialog(self, title, current=""):
        dialog = QDialog(self)
        dialog.setObjectName("SessionNameDialog")
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(self._session_dialog_style())

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        card, card_layout = self._session_dialog_card(title, "Name this chat session")
        layout.addWidget(card)

        field = QLineEdit(current or "")
        field.setObjectName("SessionNameField")
        field.setMinimumHeight(36)
        field.setFont(QFont("JetBrains Mono", 10))
        field.setPlaceholderText(DEFAULT_SESSION_NAME)
        field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_SURFACE_2};
                color: {_TEXT};
                border: 1px solid {_BORDER_SOFT};
                border-radius: 7px;
                padding: 7px 10px;
                font-size: 12px;
                selection-background-color: {_TEXT};
                selection-color: {_SURFACE};
            }}
            QLineEdit:focus {{
                border-color: {_WARN};
            }}
        """)
        card_layout.addWidget(field)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.setMinimumWidth(72)
        cancel.setStyleSheet(self._session_dialog_button_style("secondary"))
        confirm = QPushButton("Save")
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm.setFixedHeight(32)
        confirm.setMinimumWidth(72)
        confirm.setStyleSheet(self._session_dialog_button_style("wide"))
        cancel.clicked.connect(dialog.reject)
        confirm.clicked.connect(dialog.accept)
        field.returnPressed.connect(dialog.accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        card_layout.addLayout(actions)
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        field.selectAll()
        return dialog, field

    def _build_delete_session_dialog(self, session):
        name = (session or {}).get("name") or DEFAULT_SESSION_NAME
        dialog = QDialog(self)
        dialog.setObjectName("SessionDeleteDialog")
        dialog.setWindowTitle("Delete session")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(self._session_dialog_style())

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        card, card_layout = self._session_dialog_card(
            "Delete session",
            "This removes the saved transcript and backend continuation for this chat.",
        )
        layout.addWidget(card)

        row = QFrame(dialog)
        row.setObjectName("SessionListRow")
        row.setStyleSheet(self._session_row_style(active=True))
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(12, 9, 12, 9)
        row_layout.setSpacing(3)
        title = QLabel(name)
        title.setWordWrap(True)
        title.setFont(QFont("JetBrains Mono", 11, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
        meta = QLabel("Deletion cannot be undone")
        meta.setFont(QFont("JetBrains Mono", 10))
        meta.setStyleSheet(f"color:{_DANGER}; font-size:10px;")
        row_layout.addWidget(title)
        row_layout.addWidget(meta)
        card_layout.addWidget(row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.setMinimumWidth(72)
        cancel.setStyleSheet(self._session_dialog_button_style("secondary"))
        delete = QPushButton("Delete")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setFixedHeight(32)
        delete.setMinimumWidth(72)
        delete.setStyleSheet(self._session_dialog_button_style("danger"))
        cancel.clicked.connect(dialog.reject)
        delete.clicked.connect(dialog.accept)
        actions.addWidget(cancel)
        actions.addWidget(delete)
        card_layout.addLayout(actions)
        return dialog

    def _confirm_delete_session(self, session):
        dialog = self._build_delete_session_dialog(session)
        try:
            return dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()

    def _session_dialog_button_style(self, role="secondary"):
        if role == "danger":
            bg = _SURFACE_2
            hover = "#3a2424"
            color = _DANGER
            border = _BORDER_SOFT
        elif role == "wide":
            bg = _SURFACE_2
            hover = _BORDER
            color = _TEXT
            border = _BORDER_SOFT
        else:
            bg = _SURFACE_2
            hover = _BORDER
            color = _TEXT_2
            border = _BORDER_SOFT
        return f"""
            QPushButton {{
                background: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                text-align: center;
            }}
            QPushButton:hover {{
                background: {hover};
                color: {_TEXT};
            }}
            QPushButton:pressed {{
                background: {_ACCENT};
                color: {_SURFACE};
            }}
        """

    def _session_row_style(self, active=False):
        border = _WARN if active else _BORDER_SOFT
        bg = _SURFACE_2 if active else "#202020"
        return f"""
            QFrame#SessionListRow {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 7px;
            }}
        """

    def _prompt_session_name(self, title, current=""):
        dialog, field = self._build_session_name_dialog(title, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        text = field.text()
        return (text or DEFAULT_SESSION_NAME).strip() or DEFAULT_SESSION_NAME

    def _update_session_name_label(self):
        session = self._session_store.get_session(self._active_session_id)
        name = (session or {}).get("name") or DEFAULT_SESSION_NAME
        self._status_session_name = name
        if hasattr(self, "status"):
            self.status.setToolTip(f"Active session: {name}")
            self._render_status()

    def _new_session_from_menu(self):
        name = self._prompt_session_name("New session", "")
        if name is None:
            return
        self._save_current_session(immediate=True)
        session = self._session_store.create_session(name)
        if self._toolkit is not None:
            self._toolkit.clear_session_approvals()
        self._switch_to_session(session["id"], save_current=False)

    def _rename_current_session(self):
        session = self._session_store.get_session(self._active_session_id)
        if session is None:
            return
        name = self._prompt_session_name("Rename session", session.get("name", ""))
        if name is None:
            return
        self._session_store.rename_session(self._active_session_id, name)
        self._update_session_name_label()

    def _delete_current_session(self):
        self._delete_session(self._active_session_id)

    def _show_session_list(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sessions")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)
        dialog.setStyleSheet(self._session_dialog_style())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        sessions = self._session_store.list_sessions()
        card, card_layout = self._session_dialog_card(
            "Sessions",
            f"{len(sessions)} saved chats. Latest 20 are kept. Large sessions are highlighted.",
        )
        layout.addWidget(card)

        for session in sessions:
            active = session["id"] == self._active_session_id
            row = QFrame(dialog)
            row.setObjectName("SessionListRow")
            row.setStyleSheet(self._session_row_style(active))
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 10, 9)
            row_layout.setSpacing(8)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(3)
            name = QLabel(session.get("name", DEFAULT_SESSION_NAME))
            name.setWordWrap(True)
            name.setFont(QFont("JetBrains Mono", 11, QFont.Weight.DemiBold))
            name.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
            size_text = self._format_session_size(session.get("size_bytes", 0))
            meta = QLabel(
                f"{'Current' if active else 'Updated'} - "
                f"{self._format_session_time(session.get('updated_at'))} - {size_text}"
            )
            meta.setFont(QFont("JetBrains Mono", 10))
            meta_color = _WARN if (active or session.get("size_warning")) else _TEXT_3
            meta.setStyleSheet(f"color:{meta_color}; font-size:10px;")
            text_col.addWidget(name)
            text_col.addWidget(meta)
            row_layout.addLayout(text_col, 1)

            for text, handler in (
                ("Open", lambda _checked=False, sid=session["id"]: (dialog.accept(), self._switch_to_session(sid))),
                ("Rename", lambda _checked=False, sid=session["id"]: self._rename_session_from_list(sid, dialog)),
                ("Delete", lambda _checked=False, sid=session["id"]: self._delete_session_from_list(sid, dialog)),
            ):
                button = QPushButton(text)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setFixedHeight(28)
                button.setMinimumWidth(58)
                role = "danger" if text == "Delete" else "secondary"
                button.setStyleSheet(self._session_dialog_button_style(role))
                button.clicked.connect(handler)
                row_layout.addWidget(button)
            card_layout.addWidget(row)

        close = QPushButton("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setMinimumHeight(34)
        close.setStyleSheet(self._session_dialog_button_style("wide"))
        close.clicked.connect(dialog.reject)
        card_layout.addWidget(close)
        dialog.exec()

    def _rename_session_from_list(self, session_id, dialog):
        session = self._session_store.get_session(session_id)
        if session is None:
            return
        name = self._prompt_session_name("Rename session", session.get("name", ""))
        if name is None:
            return
        self._session_store.rename_session(session_id, name)
        if session_id == self._active_session_id:
            self._update_session_name_label()
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
        if not self._confirm_delete_session(session):
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

    @staticmethod
    def _format_session_size(size_bytes):
        try:
            size = max(0, int(size_bytes or 0))
        except (TypeError, ValueError):
            size = 0
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        unit = units[0]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                break
            value /= 1024.0
        if unit == "B":
            return f"{int(value)} {unit}"
        if value >= 100:
            return f"{value:.0f} {unit}"
        if value >= 10:
            return f"{value:.1f} {unit}"
        return f"{value:.2f} {unit}"

    def _stop_active_worker(self):
        if self._worker is None:
            return False
        self._stop_requested = True
        self._worker.stop()
        if self._request_cancel is not None:
            try:
                self._request_cancel()
            except Exception:  # nosec B110
                pass
        if self._ask_user_card is not None and self._toolkit is not None:
            self._toolkit._resolve_ask_user({
                "choice": None, "free_text": None, "cancelled": True,
            })
        return True

    def _save_current_session(self, immediate=False):
        if not self._active_session_id:
            return
        if immediate:
            self._session_store.flush_save()
        else:
            self._session_store.schedule_save(
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
        except Exception:  # nosec B110
            pass

    def _restore_active_session(self):
        session = self._session_store.active_session()
        self._active_session_id = session["id"]
        self._history = list(session.get("backend_history") or [])
        self._transcript_events = list(session.get("transcript_events") or [])
        self._import_backend_state(session.get("backend_state") or {})
        self._restore_transcript(self._transcript_events)
        self._update_session_name_label()

    def _switch_to_session(self, session_id, save_current=True):
        if save_current and session_id != self._active_session_id:
            self._save_current_session(immediate=True)
        self._stop_active_worker()
        if not self._session_store.set_active_session(session_id):
            return False
        session = self._session_store.get_session(session_id)
        if session is None:
            return False
        self._active_session_id = session_id
        self._history = list(session.get("backend_history") or [])
        self._history_generation += 1  # invalidate any pending pre-compaction
        self._transcript_events = list(session.get("transcript_events") or [])
        self._import_backend_state(session.get("backend_state") or {})
        self._restore_transcript(self._transcript_events)
        self._update_session_name_label()
        self._set_status("Ready", _SUCCESS, icon="✓")
        if self._toolkit is not None:
            self._toolkit.clear_session_approvals()
        return True

    def _clear_transcript_widgets(self):
        self.transcript_widget.setUpdatesEnabled(False)
        try:
            self.transcript_widget.hide()
            try:
                while self.transcript_layout.count() > 1:
                    item = self.transcript_layout.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.deleteLater()
            finally:
                self.transcript_widget.show()
        finally:
            self.transcript_widget.setUpdatesEnabled(True)

    def _clear(self):
        self._stop_active_worker()
        self._history = []
        self._history_generation += 1  # invalidate any pending pre-compaction
        self._transcript_events = []
        self._current_turn_event = None
        self._clear_live_ui()
        self._save_current_session(immediate=True)

    def _clear_live_ui(self):
        worker_was_active = self._worker is not None
        # Stop any active worker BEFORE clearing widgets to prevent crashes
        if worker_was_active:
            self._stop_active_worker()
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
        self._current_text_truncated = False
        self._status_timer.stop()
        self._scroll_locked = False
        if not worker_was_active:
            self._stop_requested = False

    def _on_send(self):
        if self._worker is not None:
            return
        message = self.input.toPlainText().strip()
        if not message:
            return

        trace_id = new_trace_id()
        trace_started_at = time.monotonic()
        backend = self._get_backend()
        if backend is None:
            self._add_user_message("No backend configured. Open Settings.")
            return
        err = backend.validate()
        if err:
            self._add_user_message(err)
            return

        self._ttft_trace_id = trace_id
        self._ttft_started_at = trace_started_at
        self._ttft_first_ui_render_logged = False
        log_ttft_event(
            "send_accepted",
            trace_id=trace_id,
            started_at=trace_started_at,
        )
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
        self._current_text_truncated = False

        self.send_btn.setEnabled(False)
        self.send_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self._set_status("Thinking", _TEXT_3, spinning=True)
        self._set_tool_progress("Thinking...")

        # Bump the generation counter so any in-flight background pre-compaction
        # detects that a new send has started and discards its stale result.
        self._history_generation += 1

        self._worker = ChatWorker(
            backend,
            message,
            self._history,
            trace_id=trace_id,
            trace_started_at=trace_started_at,
        )
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
            self._remove_current_agent_turn()
            self._current_tool_row = None
            self._pending_tool = None
            self._current_text = ""
            self._tool_progress_text = ""
            self._showing_tool_progress = False
            self._pending_stream_render = False
            self._pending_stream_kind = None
            self._pending_stream_scroll = False
            self._stream_render_timer.stop()
            # Cooperatively cancel any main-thread operation (run_pyqgis,
            # processing.run, create_chart, get_layer_statistics). The worker
            # also stops the agent loop, but main-thread work needs a separate
            # signal because it can be stuck inside exec/processing.run.
            if self._request_cancel is not None:
                try:
                    self._request_cancel()
                except Exception:  # nosec B110
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
                    except Exception:  # nosec B110
                        pass
                    self._thinking_started = False

                if not self._streaming:
                    self._streaming = True
                self._current_text += delta
                if len(self._current_text) > _MAX_TURN_TEXT_CHARS:
                    if not self._current_text_truncated:
                        self._current_text = self._current_text[:_MAX_TURN_TEXT_CHARS] + "\n\n… (truncated)"
                        self._current_text_truncated = True
                        self._schedule_stream_render("final")
                    return
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
            # prefer the structured is_error / cancelled flags that
            # the backends compute. Fall back to the old string-prefix
            # heuristic for forward-compat with any third-party backend.
            is_err = ev.data.get("is_error")
            if is_err is None:
                is_err = str(result).startswith("Error") or str(result).startswith("error")
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
            elif viz == "gif":
                self._add_gif(d)
            elif viz == "file":
                self._add_file(d)

        elif ev.type == EventType.CONNECTING:
            # HTTP transport is establishing a fresh TCP+TLS connection.
            # Shown briefly before THINKING so the user knows the delay is
            # network-level, not the model being slow.
            self._set_status("Connecting", _TEXT_3, spinning=True)

        elif ev.type == EventType.THINKING:
            self._flush_stream_render()
            self._hide_typing()
            thinking_text = ev.data.get("text", "")
            if thinking_text:
                try:
                    self._append_thinking_text(thinking_text)
                except Exception:  # nosec B110
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
            self._save_current_session()

        elif ev.type == EventType.DONE:
            self._flush_stream_render()
            self._hide_typing()
            self._finish_streaming()
            self._finalize_current_turn_event()
            self._save_current_session()
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

    def _remove_current_agent_turn(self):
        turn = self._current_agent_turn
        if turn is None:
            return
        container = turn.parentWidget()
        if container is not None:
            self.transcript_layout.removeWidget(container)
            container.deleteLater()
        self._current_agent_turn = None
        self._current_turn_event = None

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

        is_emit_kind = kind in ("tool", "final")
        if is_emit_kind and not self._ttft_first_ui_render_logged:
            self._ttft_first_ui_render_logged = True
            log_ttft_event(
                "first_ui_render",
                trace_id=self._ttft_trace_id,
                started_at=self._ttft_started_at,
            )
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
            except Exception:  # nosec B110
                pass
            self._thinking_started = True

        if self._thinking_text and not self._thinking_text.endswith((" ", "\n")):
            if not text.startswith((" ", "\n", ".", ",", ":", ";", ")")):
                self._thinking_text += " "
        self._thinking_text += text
        if len(self._thinking_text) > _MAX_THINKING_TEXT_CHARS:
            self._thinking_text = self._thinking_text[:_MAX_THINKING_TEXT_CHARS]
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
        try:
            self.send_btn.setEnabled(True)
            self.send_btn.setVisible(True)
            self.stop_btn.setEnabled(False)
            self.stop_btn.setVisible(False)
            self._set_status("Ready", _SUCCESS, icon="✓")
        except RuntimeError:
            pass
        self._scroll_locked = False
        self._thinking_started = False
        self._stop_requested = False

    def _maybe_precompact(self):
        """Start a background pre-compaction pass after a turn fully finishes.

        Runs only when history is above the compaction threshold and no worker
        is active.  The daemon thread snapshots the current history, calls
        ``backend.precompact_history()``, then posts the result back to the
        main thread via ``QTimer.singleShot(0, ...)``.  The result is applied
        only when the generation counter is unchanged (i.e. no new send or
        session switch happened) and the dock is still idle — otherwise it is
        silently discarded.  The inline compaction path inside ``send()``
        remains the fallback and is never touched.
        """
        if self._worker is not None:
            return
        backend = None
        try:
            backend = self._get_backend()
        except Exception:  # nosec B110
            pass
        if backend is None:
            return
        # Use the chat model string to decide whether compaction is needed.
        try:
            model = backend.config.get("model") or ""
        except Exception:  # nosec B110
            return
        if not should_compact(self._history, model):
            return

        # Take a snapshot so the thread works on an immutable copy.
        snapshot = list(self._history)
        generation_at_start = self._history_generation

        def _should_stop():
            # Abort if a new send started (generation bumped) or dock is closing.
            return self._history_generation != generation_at_start

        def _run():
            try:
                result = backend.precompact_history(snapshot, _should_stop)
            except Exception:  # nosec B110
                return

            def _apply():
                # Re-check on the main thread before mutating any state.
                try:
                    if (
                        self._history_generation != generation_at_start
                        or self._worker is not None
                    ):
                        return
                    self._history = result
                    self._save_current_session()
                except Exception:  # nosec B110
                    pass

            try:
                QTimer.singleShot(0, _apply)
            except Exception:  # nosec B110
                pass

        try:
            threading.Thread(
                target=_run, name="agenticgis-precompact", daemon=True
            ).start()
        except Exception:  # nosec B110
            pass

    def _on_worker_thread_finished(self, worker):
        for signal_name in ("event", "finished_history", "finished"):
            try:
                getattr(worker, signal_name).disconnect()
            except Exception:  # nosec B110
                pass
        if worker is self._worker:
            self._worker = None
        # Fire background pre-compaction now that the turn is fully done and
        # the worker reference has been cleared.  This runs after
        # _on_finished() has already persisted the new history, so the session
        # is consistent before we attempt to compact it.
        self._maybe_precompact()
