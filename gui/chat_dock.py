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
from .ask_user_card import AskUserCard

# ── Design Tokens (monochrome minimal palette) ─────────────────────────────
_SURFACE     = "#161616"
_CANVAS      = "#0a0a0a"
_INPUT_BG    = "#1e1e1e"
_BORDER      = "#2e2e2e"
_BORDER_SOFT = "#242424"
_TEXT        = "#ececec"
_TEXT_2      = "#a0a0a0"
_TEXT_3      = "#707070"
_ACCENT      = "#e0e0e0"
_ACCENT_HOV  = "#c8c8c8"
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"
_WARN        = "#f0a500"


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
        self._current_text = ""            # accumulated streaming text
        self._thinking_started = False     # whether add_thinking_block was called this turn
        self._ask_user_card = None
        self._ask_user_payload = None
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._build_ui()
        if self._toolkit is not None:
            self._toolkit.set_ask_user_emitter(
                self._ask_user_emitter
            )

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
            f"<span style='color:{_SUCCESS};font-size:7px;'>&#9632;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self.status.setTextFormat(Qt.RichText)
        self.status.setStyleSheet("background: transparent; padding-right: 4px;")
        top.addWidget(self.status)

        for label, tip in (("Set", "Settings"), ("Clr", "Clear chat")):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedSize(32, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 9px;
                    font-weight: 500;
                    letter-spacing: 0.04em;
                    border: none;
                    border-radius: 6px;
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

        # -- Input bar --------------------------------------------------- #
        input_wrap = QWidget()
        input_wrap.setStyleSheet(f"background-color: {_SURFACE};")
        input_bar = QVBoxLayout(input_wrap)
        input_bar.setContentsMargins(16, 10, 16, 14)
        input_bar.setSpacing(6)

        # Model chip row (above input box)
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(0)
        self._model_chip = QLabel(self._get_model_name())
        self._model_chip.setStyleSheet(f"""
            color: {_TEXT_3};
            font-family: 'SF Mono', 'Consolas', 'Courier New', monospace;
            font-size: 9px;
            background: transparent;
            padding: 0;
        """)
        meta_row.addWidget(self._model_chip)
        meta_row.addStretch(1)
        input_bar.addLayout(meta_row)

        # Input box + button row
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)

        # Text input — monospace, rectangular with soft border
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Message AgenticGIS…")
        self.input.setFixedHeight(36)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        mono_font = QFont("Consolas")
        mono_font.setStyleHint(QFont.Monospace)
        mono_font.setPointSize(11)
        self.input.setFont(mono_font)
        self.input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {_INPUT_BG};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px 6px 10px;
                selection-background-color: {_TEXT};
                selection-color: {_CANVAS};
            }}
        """)
        input_row.addWidget(self.input, 1)

        # Send button: arrow
        self.send_btn = QPushButton("→")
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.setFixedSize(32, 28)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_ACCENT};
                color: {_CANVAS};
                border: 1px solid {_ACCENT};
                border-radius: 4px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {_ACCENT_HOV}; border-color: {_ACCENT_HOV}; }}
            QPushButton:pressed {{ background-color: {_TEXT_2}; border-color: {_TEXT_2}; }}
            QPushButton:disabled {{ background-color: {_BORDER}; border-color: {_BORDER}; color: {_TEXT_3}; }}
        """)
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn, 0, Qt.AlignVCenter)

        # Stop button: block symbol
        self.stop_btn = QPushButton("■")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setFixedSize(32, 28)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {_DANGER};
                border: 1px solid {_DANGER};
                border-radius: 4px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {_DANGER}; color: {_SURFACE}; border-color: {_DANGER}; }}
            QPushButton:disabled {{
                border: 1px solid {_BORDER};
                color: {_TEXT_3};
                background-color: transparent;
            }}
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        input_row.addWidget(self.stop_btn, 0, Qt.AlignVCenter)

        input_bar.addLayout(input_row)
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
            self._thinking_started = False
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
    def _ask_user_emitter(self, question, options, allow_free_text):
        """Called by the toolkit on the main thread to surface a question."""
        self._show_ask_user(question, options, allow_free_text)

    def _show_ask_user(self, question, options, allow_free_text):
        """Build and show the AskUserCard popover; record the pending slot."""
        if self._ask_user_card is not None:
            return
        self._hide_typing()
        self._ask_user_payload = None
        card = AskUserCard(question, options, allow_free_text=allow_free_text, parent=self)
        card.submitted.connect(self._resolve_ask_user)
        if not hasattr(self, "_ask_user_container") or self._ask_user_container is None:
            from qgis.PyQt.QtWidgets import QWidget as _QW
            self._ask_user_container = _QW()
            self._ask_user_container.setStyleSheet(f"background-color: {_SURFACE};")
            self.widget().layout().addWidget(self._ask_user_container)
        from qgis.PyQt.QtWidgets import QVBoxLayout as _QV
        if self._ask_user_container.layout() is None:
            self._ask_user_container.setLayout(_QV())
            self._ask_user_container.layout().setContentsMargins(16, 0, 16, 8)
        self._ask_user_container.layout().addWidget(card)
        self._ask_user_card = card
        self.status.setText(
            f"<span style='color:{_TEXT_2};font-size:7px;'>&#9632;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Awaiting input</span>"
        )

    def _resolve_ask_user(self, payload):
        """User picked an option or typed a reply; close the card and unblock."""
        self._ask_user_payload = payload
        if self._ask_user_card is not None:
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        label = payload.get("choice")
        text = payload.get("free_text")
        if label:
            self._add_user_message(f"→ {label}")
        elif text:
            self._add_user_message(f"→ {text}")
        self.status.setText(
            f"<span style='color:{_SUCCESS};font-size:7px;'>&#9632;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        if self._toolkit is not None:
            cancelled = bool(payload.get("cancelled", False))
            self._toolkit._resolve_ask_user({
                "choice": payload.get("choice"),
                "free_text": payload.get("free_text"),
                "cancelled": cancelled,
            })

    # ------------------------------------------------------------------ #
    def _clear(self):
        if self._ask_user_card is not None:
            if self._toolkit is not None:
                self._toolkit._resolve_ask_user({
                    "choice": None, "free_text": None, "cancelled": True,
                })
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        self._history = []
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.status.setText(
            f"<span style='color:{_SUCCESS};font-size:7px;'>&#9632;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._thinking_started = False
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
        self._scroll_to_bottom()
        self._streaming = False
        self._pending_tool = None
        self._current_text = ""
        self._current_agent_turn = None
        self._current_tool_row = None
        self._thinking_started = False
        self._scroll_locked = False

        self.send_btn.setEnabled(False)
        self.send_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self.status.setText(
            f"<span style='color:{_TEXT_3};font-size:7px;'>&#9632;</span> "
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
            self.status.setText(
                f"<span style='color:{_DANGER};font-size:7px;'>&#9632;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Stopping</span>"
            )

    # ------------------------------------------------------------------ #
    def _on_event(self, ev):
        if ev.type == EventType.TEXT:
            # Finalize any active thinking block before streaming text
            if self._thinking_started and self._current_agent_turn is not None:
                try:
                    self._current_agent_turn.finalize_thinking()
                except Exception:
                    pass
                self._thinking_started = False

            if not self._streaming:
                self._streaming = True
                self._current_text = ""
            delta = ev.data.get("text", "")
            if delta:
                self._current_text += delta
                # Render every token immediately for real-time feel —
                # the delta-based renderer in AgentTurnBubble is cheap,
                # so no debounce is needed.
                turn = self._get_or_create_agent_turn()
                turn.set_streaming_text(self._current_text)
                if not self._scroll_locked:
                    self._scroll_to_bottom()

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
                self.status.setText(
                    f"<span style='color:{_DANGER};font-size:7px;'>&#9632;</span> "
                    f"<span style='color:{_TEXT_3}; font-size:11px;'>Cancelled</span>"
                )
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
            # Route thinking text to a ThinkingBlock on the current agent turn.
            turn = self._get_or_create_agent_turn()
            if not self._thinking_started:
                try:
                    turn.add_thinking_block()
                except Exception:
                    pass
                self._thinking_started = True
            thinking_text = ev.data.get("text", "")
            if thinking_text:
                try:
                    turn.set_thinking_text(thinking_text)
                except Exception:
                    pass
            self.status.setText(
                f"<span style='color:{_TEXT_3};font-size:7px;'>&#9632;</span> "
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
            self._thinking_started = False
            self._current_agent_turn = None
            self.status.setText(
                f"<span style='color:{_SUCCESS};font-size:7px;'>&#9632;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
            )
            self._scroll_to_bottom()
            self._pending_tool = None

    def _finish_streaming(self):
        """Finalize streaming text in current agent turn — applies full markdown.

        The bubble already received every token via set_streaming_text, so we
        just call finalize_text to apply full markdown (code blocks, tables,
        headings) and drop the cursor.  We do *not* clear ``_current_text``
        so that text streaming can resume into the same bubble after the
        tool returns (otherwise the pre-tool text gets lost).
        """
        if self._current_text:
            turn = self._get_or_create_agent_turn()
            turn.finalize_text(self._current_text)
        elif self._current_agent_turn is None:
            # No text and no turn yet — create an empty turn so the tool row
            # has somewhere to live.
            self._get_or_create_agent_turn()

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
        self.status.setText(
            f"<span style='color:{_SUCCESS};font-size:7px;'>&#9632;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self._worker = None
