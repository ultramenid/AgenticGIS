"""The in-QGIS chat dock: minimal, refined chat interface.

Inspired by Dribbble's "Minimal Chat Box UI" — clean off-white surface,
soft pill input, restrained type, generous spacing. Works in light
mode (default) and respects QGIS's own theme via neutral grays.
"""

import html

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt, QEvent, QThread, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..backends.base import AgentEvent, EventType
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
            try:
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


class ChatDock(QgsDockWidget):
    _ask_user_signal = pyqtSignal(str, object, bool)

    def __init__(self, get_backend, open_settings, request_cancel, toolkit=None, parent=None):
        super().__init__("AgenticGIS", parent)
        self.setObjectName("AgenticGisDock")
        self._get_backend = get_backend
        self._open_settings = open_settings
        self._request_cancel = request_cancel
        self._toolkit = toolkit
        self._history = []
        self._worker = None
        self._streaming = False
        self._pending_tool = None
        self._typing_widget = None
        self._current_agent_turn = None   # AgentTurnBubble for the active turn
        self._current_tool_row = None      # ToolRowWidget awaiting its result
        self._current_text = ""            # accumulated final-answer text
        self._tool_progress_text = ""      # temporary visible text while a tool runs
        self._showing_tool_progress = False
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
        self._build_ui()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(120)
        self._status_timer.timeout.connect(self._tick_status)
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._ask_user_signal.connect(self._show_ask_user, Qt.QueuedConnection)
        if self._toolkit is not None:
            self._toolkit.set_ask_user_emitter(
                self._emit_ask_user_threadsafe
            )

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

        for label, tip in (("Setting", "Settings"), ("Clear", "Clear chat")):
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
        input_frame.setFixedHeight(38)
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

        self.input = QLineEdit()
        self.input.setPlaceholderText("Message AgenticGIS…")
        self.input.setFixedHeight(28)
        self.input.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.input.setTextMargins(0, 0, 0, 0)
        mono_font = QFont("JetBrains Mono")
        mono_font.setStyleHint(QFont.Monospace)
        mono_font.setPointSize(10)
        self.input.setFont(mono_font)
        self.input.setStyleSheet(f"""
            QLineEdit {{
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 12px;
                border: none;
                background: transparent;
                color: {_TEXT};
                padding: 0px;
                selection-background-color: {_BORDER};
            }}
        """)
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

    def _get_model_name(self) -> str:
        """Return a short model name string for the chip label."""
        if self._toolkit is not None:
            for attr in ("model", "model_name", "_model", "_model_name"):
                val = getattr(self._toolkit, attr, None)
                if val and isinstance(val, str):
                    return val
        backend = self._get_backend() if self._get_backend else None
        if backend is not None:
            for attr in ("model", "model_name", "_model", "_model_name"):
                val = getattr(backend, attr, None)
                if val and isinstance(val, str):
                    return val
        return "LLM"

    def _refresh_model_chip(self):
        """Update the model chip text (call after backend changes)."""
        if hasattr(self, "_model_chip"):
            self._model_chip.setText(self._get_model_name())

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)
        self._refresh_model_chip()

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
            self._thinking_started = False
        return self._current_agent_turn

    def _add_chart(self, chart_data):
        self._add_widget(ChartWidget(chart_data))

    def _add_stats(self, stats_data):
        self._add_widget(StatsWidget(stats_data))

    def _add_compaction_notice(self):
        w = QLabel("── history compacted ──")
        w.setAlignment(Qt.AlignCenter)
        w.setStyleSheet(
            f"color:{_TEXT_4}; font-size:10px; font-family:'JetBrains Mono',monospace;"
            f" padding:4px 0; background:transparent;"
        )
        self._add_widget(w)

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
        card_size = self._ask_card_frame.sizeHint()
        card_h = min(card_size.height(), max(240, ov.height() - 32))
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
    def _clear(self):
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
        self._history = []
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._set_status("Ready", _SUCCESS, icon="✓")
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._tool_progress_text = ""
        self._showing_tool_progress = False
        self._thinking_text = ""
        self._thinking_started = False
        self._scroll_locked = False

    def _on_send(self):
        if self._worker is not None:
            return
        message = self.input.text().strip()
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
        # Reset scroll lock and force-scroll to bottom. The user just hit
        # send, so they want to see the response that follows. We use
        # the deferred-scroll path so the new message widget has been
        # laid out into the scroll range first.
        self._scroll_locked = False
        self._scroll_to_bottom_after_layout()
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._tool_progress_text = ""
        self._showing_tool_progress = False
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
        self._worker.finished_history.connect(self._on_finished)
        self._worker.start()

    def _on_stop(self):
        if self._worker is not None:
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
            self._set_status("Stopping", _DANGER, spinning=True)

    # ------------------------------------------------------------------ #
    def _on_event(self, ev):
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
                    turn.set_streaming_text(self._tool_progress_text)
                    if not self._scroll_locked:
                        self._scroll_to_bottom()
                    return

                # Final-answer text begins after thinking/progress. Collapse
                # the thinking block so the turn stays compact.
                if self._showing_tool_progress:
                    turn.clear_streaming_text()
                    self._tool_progress_text = ""
                    self._showing_tool_progress = False
                if self._thinking_started and self._current_agent_turn is not None:
                    try:
                        self._current_agent_turn.finalize_thinking()
                    except Exception:
                        pass
                    self._thinking_started = False

                if not self._streaming:
                    self._streaming = True
                self._current_text += delta
                # Render every token immediately for real-time feel —
                # the delta-based renderer in AgentTurnBubble is cheap,
                # so no debounce is needed.
                turn.set_streaming_text(self._current_text)
                if not self._scroll_locked:
                    self._scroll_to_bottom()

        elif ev.type == EventType.TOOL_USE:
            self._hide_typing()
            turn = self._get_or_create_agent_turn()
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
            self._set_tool_progress(f"Processing `{tool_name}`...\n")
            self._maybe_scroll_to_bottom()

        elif ev.type == EventType.TOOL_RESULT:
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
            self._show_ask_user(
                ev.data.get("question", ""),
                ev.data.get("options", []),
                ev.data.get("allow_free_text", True),
            )

        elif ev.type == EventType.VISUALIZATION:
            viz = ev.data.get("type")
            d = ev.data.get("data", {})
            if viz == "chart":
                self._add_chart(d)
            elif viz == "stats":
                self._add_stats(d)

        elif ev.type == EventType.THINKING:
            self._hide_typing()
            thinking_text = ev.data.get("text", "")
            if thinking_text:
                try:
                    self._append_thinking_text(thinking_text)
                except Exception:
                    pass
            self._set_status("Thinking", _TEXT_3, spinning=True)

        elif ev.type == EventType.COMPACTION:
            self._add_compaction_notice()

        elif ev.type == EventType.ERROR:
            self._hide_typing()
            self._finish_streaming()
            msg = html.escape(str(ev.data.get("error", "")))
            self._add_widget(MessageContainer(msg, is_user=False, is_error=True))

        elif ev.type == EventType.DONE:
            self._hide_typing()
            self._finish_streaming()
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
        if self._current_text:
            turn = self._get_or_create_agent_turn()
            turn.finalize_text(self._current_text)
            turn.finalize()
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
        turn = self._get_or_create_agent_turn()
        self._tool_progress_text = text
        self._showing_tool_progress = True
        turn.set_progress_text(self._tool_progress_text)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

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

    def _on_finished(self, history):
        self._history = history if history is not None else self._history
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
        self._worker = None
