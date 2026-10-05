"""AgentTurnBubble — one widget per complete agent response turn.

Reasoning ticker streams LLM thinking in one line above the tool calls.
Each tool call is one row (spinner → ✓/!/—) with a readable result summary;
the answer itself sits in a bubble below.
"""

import html as _html
import json
import re

from qgis.PyQt.QtCore import Qt, QElapsedTimer, QSize, QTimer
from qgis.PyQt.QtGui import QFont, QKeySequence
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .formatting import humanize_tool_name, summarize_tool_args, summarize_tool_result, split_md_tables
from .downloadable import HoverDownloadButton, save_text, _safe_name
from .message_bubble import (
    _copy_to_clipboard,
    _md_inline,
    _md_to_html,
    _show_code_context_menu,
    _count_complete_fenced_blocks,
    _count_complete_tables,
)

from .theme import (
    fs,
    mono_font,
    MONO_STACK,
    ui_font,
    sans_family,
    DOCK_CANVAS as _CANVAS,
    DOCK_SURFACE as _SURFACE,
    DOCK_BLUE as _BLUE,
    DOCK_SURFACE_2 as _SURFACE_2,
    DOCK_BORDER as _BORDER,
    DOCK_BORDER_SOFT as _BORDER_SOFT,
    DOCK_TEXT as _TEXT,
    DOCK_TEXT_2 as _TEXT_2,
    DOCK_TEXT_3 as _TEXT_3,
    DOCK_TEXT_4 as _TEXT_4,
    DOCK_WARN as _WARN,
    DOCK_SUCCESS as _SUCCESS,
    DOCK_DANGER as _DANGER,
)

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# Past this many seconds of an unchanged "Thinking..." label, the spinner
# looks identical whether the agent is working or the process died/hung —
# flip it to a visible warning so a stuck session doesn't read as normal.
_STALL_HINT_SECONDS = 45


class MarkdownTable(QTableWidget):
    """Read-only table for a finished answer: scrolls sideways when wider than the bubble."""

    _MAX_VISIBLE_ROWS = 12

    def __init__(self, rows, parent=None):
        cols = max(len(r) for r in rows)
        super().__init__(len(rows) - 1, cols, parent)
        self._rows = rows
        self.setHorizontalHeaderLabels(rows[0] + [""] * (cols - len(rows[0])))
        for r, row in enumerate(rows[1:]):
            for c, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.setItem(r, c, item)

        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(fs(13) + 14)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setMaximumSectionSize(320)
        self.resizeColumnsToContents()
        header.setStretchLastSection(True)

        self.setStyleSheet(f"""
            QTableWidget {{
                background:{_SURFACE}; alternate-background-color:{_BORDER_SOFT};
                color:{_TEXT_2}; border:1px solid {_BORDER}; border-radius:8px;
                font-family:{sans_family()}; font-size:{fs(12)}px;
                selection-background-color:{_BORDER}; selection-color:{_TEXT};
            }}
            QTableWidget::item {{ padding:0 10px; border:none; }}
            QHeaderView::section {{
                background:{_SURFACE_2}; color:{_TEXT}; font-weight:600;
                padding:6px 10px; border:none; border-bottom:1px solid {_BORDER};
            }}
            QTableCornerButton::section {{ background:{_SURFACE_2}; border:none; }}
            QScrollBar:horizontal {{ background:transparent; height:8px; margin:0 4px 2px 4px; }}
            QScrollBar:vertical {{ background:transparent; width:8px; margin:4px 2px 4px 0; }}
            QScrollBar::handle {{ background:{_BORDER}; border-radius:3px; min-width:24px; min-height:24px; }}
            QScrollBar::handle:hover {{ background:{_TEXT_4}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        visible = min(self.rowCount(), self._MAX_VISIBLE_ROWS)
        # Reserve the scrollbar's strip so a wide table doesn't hide its last row.
        self.setFixedHeight(
            header.sizeHint().height() + visible * self.verticalHeader().defaultSectionSize() + 12
        )

    def _as_tsv(self, selected_only=False):
        if not selected_only:
            return "\n".join("\t".join(r) for r in self._rows)
        cells = sorted((i.row(), i.column(), i.text()) for i in self.selectedItems())
        lines, last = [], None
        for r, _c, text in cells:
            if r != last:
                lines.append([])
                last = r
            lines[-1].append(text)
        return "\n".join("\t".join(line) for line in lines)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            _copy_to_clipboard(self._as_tsv(selected_only=True))
            return
        super().keyPressEvent(event)

    def _show_menu(self, pos):
        menu = QMenu(self)
        if self.selectedItems():
            menu.addAction("Copy selection", lambda: _copy_to_clipboard(self._as_tsv(selected_only=True)))
        menu.addAction("Copy table", lambda: _copy_to_clipboard(self._as_tsv()))
        menu.exec(self.viewport().mapToGlobal(pos))


class ReasoningTicker(QWidget):
    """Single-line streaming reasoning display. Shows last 100 chars of LLM thinking."""

    _MAX_CHARS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._phase = 0
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        mono = ui_font(12)
        mono.setItalic(True)

        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(2, 2, 2, 2)
        hbox.setSpacing(4)

        self._prefix_lbl = QLabel(_SPINNER_FRAMES[0])
        self._prefix_lbl.setFont(mono)
        self._prefix_lbl.setFixedWidth(18)
        self._prefix_lbl.setStyleSheet(f"color:{_TEXT_3}; background:transparent;")
        hbox.addWidget(self._prefix_lbl)

        self._lbl = QLabel("")
        self._lbl.setFont(mono)
        self._lbl.setStyleSheet(
            f"color:{_TEXT_3}; background:transparent; font-style:italic;"
        )
        self._lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hbox.addWidget(self._lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)

    def append(self, text_chunk: str) -> None:
        """Append a streaming delta and update the single-line display."""
        if not text_chunk:
            return
        self._buffer += text_chunk
        self._render()
        if not self.isVisible():
            self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()

    def set_full(self, text: str) -> None:
        """Replace buffer entirely (for cumulative set_thinking_text calls)."""
        self._buffer = text or ""
        self._render()
        if self._buffer and not self.isVisible():
            self.setVisible(True)
        if self._buffer and not self._timer.isActive():
            self._timer.start()
        elif not self._buffer:
            self._timer.stop()
            self.setVisible(False)

    def hide_ticker(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % len(_SPINNER_FRAMES)
        self._render()

    def _render(self) -> None:
        display = self._buffer
        if len(display) > self._MAX_CHARS:
            display = "…" + display[-self._MAX_CHARS:]
        display = display.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        self._prefix_lbl.setText(_SPINNER_FRAMES[self._phase])
        self._lbl.setText(_html.escape(display))


class ToolCallRow(QFrame):
    """One tool call: status glyph, readable name, argument, result summary, time.

    Click the row to expand the full input and result.
    """

    def __init__(self, tool_name: str, tool_input: dict, parent=None):
        super().__init__(parent)
        self._done = False
        self._bubble = None   # set by AgentTurnBubble.add_tool()
        self._result = ""
        self._input = tool_input
        self._input_json = json.dumps(tool_input, default=str)
        self._expanded = False
        self._details = None
        self._pulse = 0
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

        self.setObjectName("ToolRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tool_name)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"QFrame#ToolRow {{ background:transparent; border:none; border-radius:8px; }}"
            f"QFrame#ToolRow:hover {{ background:{_SURFACE}; }}"
        )

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(8, 5, 8, 5)
        self._outer.setSpacing(1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self._icon_lbl = QLabel(_SPINNER_FRAMES[0])
        self._icon_lbl.setFont(mono_font(12))
        self._icon_lbl.setFixedWidth(14)
        self._icon_lbl.setStyleSheet(f"color:{_BLUE}; background:transparent;")
        top.addWidget(self._icon_lbl)

        name_lbl = QLabel(humanize_tool_name(tool_name))
        name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        name_lbl.setFont(ui_font(12.5, QFont.Weight.Medium))
        name_lbl.setStyleSheet(f"color:{_TEXT}; background:transparent; font-size:{fs(12.5)}px;")
        top.addWidget(name_lbl)

        self._arg_lbl = QLabel(summarize_tool_args(tool_input))
        self._arg_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._arg_lbl.setFont(mono_font(11))
        self._arg_lbl.setStyleSheet(f"color:{_TEXT_3}; background:transparent; font-size:{fs(11)}px;")
        self._arg_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self._arg_lbl, 1)

        self._time_lbl = QLabel("")
        self._time_lbl.setFont(ui_font(11))
        self._time_lbl.setStyleSheet(f"color:{_TEXT_4}; background:transparent; font-size:{fs(11)}px;")
        top.addWidget(self._time_lbl)

        self._chev_lbl = QLabel("›")
        self._chev_lbl.setFont(ui_font(13))
        self._chev_lbl.setFixedWidth(10)
        self._chev_lbl.setStyleSheet(f"color:{_TEXT_4}; background:transparent; font-size:{fs(13)}px;")
        top.addWidget(self._chev_lbl)
        self._outer.addLayout(top)

        # Result summary line — indented under the name, filled on completion.
        self._summary_lbl = QLabel("Running…")
        self._summary_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._summary_lbl.setFont(ui_font(11.5))
        self._summary_lbl.setStyleSheet(
            f"color:{_TEXT_3}; background:transparent; font-size:{fs(11.5)}px; padding-left:19px;"
        )
        self._summary_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._outer.addWidget(self._summary_lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── Public API ────────────────────────────────────────────────────────

    def set_result(self, result_str: str, is_error: bool = False, is_cancelled: bool = False) -> None:
        """Called by chat_dock.py with the tool's output."""
        if self._done:
            return
        self._result = result_str
        if is_cancelled:
            summary = "Cancelled"
        else:
            summary = summarize_tool_result(result_str, is_error) or "Done"
        self.mark_done("error" if is_error else "cancelled" if is_cancelled else "ok", summary)

    def mark_done(self, state: str = "ok", summary: str = "") -> None:
        """Stop the spinner; ``state`` is ok / error / cancelled / unknown."""
        if self._done:
            return
        self._done = True
        self._timer.stop()
        glyph, color = {
            "ok": ("✓", _SUCCESS),
            "error": ("!", _DANGER),
            "cancelled": ("—", _WARN),
        }.get(state, ("·", _TEXT_3))
        try:
            self._icon_lbl.setText(glyph)
            self._icon_lbl.setStyleSheet(f"color:{color}; background:transparent;")
            secs = self._elapsed.elapsed() / 1000.0
            self._time_lbl.setText(f"{secs:.1f}s" if secs >= 0.1 else "")
            self._summary_lbl.setText(summary)
            self._summary_lbl.setStyleSheet(
                f"color:{_DANGER if state == 'error' else _TEXT_3}; background:transparent;"
                f" font-size:{fs(11.5)}px; padding-left:19px;"
            )
            self._summary_lbl.setVisible(bool(summary))
        except RuntimeError:
            pass
        self._relayout()

    def append_reasoning(self, delta: str) -> None:
        """Called by chat_dock.py; reasoning belongs to the turn's ticker."""
        if self._bubble is not None:
            self._bubble.stream_reasoning(delta)

    # ── Internals ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._pulse = (self._pulse + 1) % len(_SPINNER_FRAMES)
        try:
            self._icon_lbl.setText(_SPINNER_FRAMES[self._pulse])
            self._time_lbl.setText(f"{self._elapsed.elapsed() / 1000.0:.1f}s")
        except RuntimeError:
            self._timer.stop()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_details()

    def _toggle_details(self) -> None:
        self._expanded = not self._expanded
        if self._expanded and self._details is None:
            self._build_details()
        try:
            self._chev_lbl.setText("⌄" if self._expanded else "›")
            self._details.setVisible(self._expanded)
        except (RuntimeError, AttributeError):
            pass
        self._relayout()

    def _relayout(self) -> None:
        try:
            self.updateGeometry()
            if self._bubble is not None:
                self._bubble._refresh_text_geometry()
        except RuntimeError:
            pass

    @staticmethod
    def _pretty(text: str) -> str:
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            pass
        # ponytail: long outputs are clipped for the label; Copy gives the full text.
        return text if len(text) <= 6000 else text[:6000] + "\n…"

    def _build_details(self) -> None:
        panel = QFrame()
        panel.setObjectName("ToolDetails")
        panel.setCursor(Qt.CursorShape.ArrowCursor)
        panel.setStyleSheet(
            f"QFrame#ToolDetails {{ background:{_CANVAS}; border:1px solid {_BORDER};"
            f" border-radius:8px; }}"
        )
        col = QVBoxLayout(panel)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(4)

        def _section(label_text: str, body_text: str):
            head_row = QHBoxLayout()
            head_row.setContentsMargins(0, 0, 0, 0)
            head = QLabel(label_text.upper())
            head.setFont(ui_font(10, QFont.Weight.DemiBold))
            head.setStyleSheet(
                f"color:{_TEXT_4}; background:transparent; font-size:{fs(10)}px; letter-spacing:0.5px;"
            )
            head_row.addWidget(head)
            head_row.addStretch()
            if body_text:
                btn = QPushButton("Copy")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFlat(True)
                btn.setStyleSheet(
                    f"QPushButton {{ color:{_TEXT_3}; background:transparent;"
                    f" border:none; padding:0 2px; font-size:{fs(11)}px; }}"
                    f"QPushButton:hover {{ color:{_TEXT}; }}"
                )
                btn.clicked.connect(lambda _checked=False, t=body_text: _copy_to_clipboard(t))
                head_row.addWidget(btn)
            col.addLayout(head_row)
            body = QLabel(self._pretty(body_text) if body_text else "(none)")
            body.setFont(mono_font(11))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setStyleSheet(f"color:{_TEXT_2}; background:transparent; font-size:{fs(11)}px; border:none;")
            col.addWidget(body)

        _section("Input", self._input_json if self._input else "")
        if self._done:
            _section("Result", self._result)
        self._details = panel
        self._outer.addSpacing(4)
        self._outer.addWidget(panel)


class AgentTurnBubble(QFrame):
    """One agent turn: reasoning ticker + grouped tool rows + streaming text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool_rows: list = []
        self._tool_keys: dict = {}  # (name, input_json) → ToolCallRow (dedup)
        self._stream_text = ""
        self._stream_html = ""
        self._progress_text = ""
        self._progress_phase = 0
        self._progress_elapsed = QElapsedTimer()
        self._user_decision_lbl = None
        self._footer = None
        self._last_stream_text = ""
        self._last_stream_html = ""
        # True when the text view's document holds something other than the
        # accumulated stream HTML (progress line, finalized answer) — the next
        # streamed frame must full-replace instead of appending.
        self._stream_doc_dirty = False
        self._done = False
        # Auto-format: switch to full _md_to_html once a complete fenced block/table appears.
        self._auto_format = False
        # Incremental auto-format cache: full re-parse only when a new
        # fence/table marker arrives, not on every streamed frame.
        self._fmt_sig = None
        self._fmt_base_len = 0
        self._fmt_base_html = ""
        self._geo_timer = QTimer(self)
        self._geo_timer.setInterval(50)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.timeout.connect(self._refresh_text_geometry)
        # Debounce timer for expensive _md_to_html re-parses during streaming.
        # Re-parsing the whole accumulated text on every frame is O(n) and
        # blocks the main thread. We only re-parse when fence/table markers
        # change, AND at least 80ms have passed since the last re-parse.
        self._fmt_debounce_timer = QTimer(self)
        self._fmt_debounce_timer.setInterval(80)
        self._fmt_debounce_timer.setSingleShot(True)
        self._fmt_debounce_timer.timeout.connect(self._do_fmt_reparse)
        self._fmt_pending_text = None

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet("AgentTurnBubble { background: transparent; border: none; }")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 2, 0, 4)
        self._outer.setSpacing(0)

        self._ticker = ReasoningTicker(self)
        self._outer.addWidget(self._ticker)

        self._tools_area = QWidget(self)
        self._tools_area.setVisible(False)
        self._tools_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._tools_area.setStyleSheet("background:transparent;")
        self._tools_layout = QVBoxLayout(self._tools_area)
        self._tools_layout.setContentsMargins(0, 0, 0, 6)
        self._tools_layout.setSpacing(1)
        self._outer.addWidget(self._tools_area)

        # Inline file/download cards — between tool rows and the answer text.
        self._files_area = QWidget(self)
        self._files_area.setVisible(False)
        self._files_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._files_area.setStyleSheet("background:transparent;")
        self._files_layout = QVBoxLayout(self._files_area)
        self._files_layout.setContentsMargins(2, 6, 2, 8)
        self._files_layout.setSpacing(6)
        self._outer.addWidget(self._files_area)

        self.text_lbl = self._new_text_label()
        # Extra answer pieces (text labels / MarkdownTable) after text_lbl, only
        # on a finished answer that contains markdown tables.
        self._segments: list = []
        self._final_text = None
        # Answer bubble — hidden while empty or while showing a progress line.
        self._answer = QFrame(self)
        self._answer.setObjectName("AnswerBubble")
        self._answer_layout = QVBoxLayout(self._answer)
        self._answer_layout.addWidget(self.text_lbl)
        self._set_bubble(True)
        self._answer.setVisible(False)
        self._outer.addWidget(self._answer)

        # Inline visuals (charts/stats/gifs) — rendered inside this turn,
        # below the answer text, instead of in a separate transcript bubble.
        self._visuals_area = QWidget(self)
        self._visuals_area.setVisible(False)
        self._visuals_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._visuals_area.setStyleSheet("background:transparent;")
        self._visuals_layout = QVBoxLayout(self._visuals_area)
        self._visuals_layout.setContentsMargins(2, 6, 2, 4)
        self._visuals_layout.setSpacing(6)
        self._outer.addWidget(self._visuals_area)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(180)
        self._progress_timer.timeout.connect(self._render_progress_text)

        # Hover-to-download: save the agent's answer text as Markdown.
        HoverDownloadButton(self, self._save_text, tooltip="Save response (.md)")

    # ── Core public API ───────────────────────────────────────────────────

    def _save_text(self) -> None:
        text = self._stream_text or ""
        if not text.strip():
            return
        save_text(self, text, _safe_name(text.split("\n", 1)[0], "response", ".md"))

    def _effective_width(self) -> int:
        """Return the actual usable width for layout calculations."""
        if self.width() > 0:
            return self.width()
        p = self.parentWidget()
        while p is not None:
            if p.width() > 0:
                m = p.contentsMargins()
                return max(100, p.width() - m.left() - m.right() - 32)
            p = p.parentWidget()
        return 400

    def _refresh_text_geometry(self) -> None:
        """Propagate the text view's size into the transcript layout."""
        if not self._outer:
            return

        target_w = self._effective_width()
        margins = self._outer.contentsMargins()
        label_w = max(50, target_w - margins.left() - margins.right() - self._text_inset()[0])
        for w in [self.text_lbl, *self._segments]:
            if w.maximumWidth() != label_w or w.minimumWidth() != label_w:
                w.setFixedWidth(label_w)
            w.updateGeometry()
        self.updateGeometry()

    def _new_text_label(self) -> QLabel:
        lbl = QLabel("")
        lbl.setWordWrap(True)
        lbl.setMinimumWidth(0)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        lbl.setOpenExternalLinks(True)
        lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl.customContextMenuRequested.connect(
            lambda pos, lbl=lbl: _show_code_context_menu(self, lbl, pos, self._stream_text)
        )
        font = QFont()
        font.setPixelSize(fs(13))
        lbl.setFont(font)
        lbl.setStyleSheet(f"""
            color:{_TEXT}; background:transparent; border:none;
            font-family:{sans_family()};
            font-size:{fs(13)}px; line-height:1.6;
        """)
        return lbl

    def _set_bubble(self, on: bool) -> None:
        """Bubble chrome for answers; none for the transient progress line."""
        if getattr(self, "_bubble_on", None) == on:
            return
        self._bubble_on = on
        self._answer_layout.setContentsMargins(*((14, 10, 14, 10) if on else (2, 0, 2, 0)))
        self._answer.setStyleSheet(
            f"QFrame#AnswerBubble {{ background:{_SURFACE}; border:1px solid {_BORDER};"
            f" border-radius:14px; }}" if on else
            "QFrame#AnswerBubble { background:transparent; border:none; }"
        )

    def _set_text(self, html: str, bubble: bool = True) -> None:
        self._final_text = None
        for w in self._segments:
            self._answer_layout.removeWidget(w)
            w.deleteLater()
        self._segments = []
        self.text_lbl.setText(html)
        self.text_lbl.setVisible(True)
        self._set_bubble(bubble)
        self._answer.setVisible(bool(html))

    def _set_final(self, text: str) -> None:
        """Render a finished answer; markdown tables become scrollable MarkdownTable widgets."""
        parts = split_md_tables(text) if text else []
        if not any(kind == "table" for kind, _ in parts):
            self._set_text(_md_to_html(text) if text else "")
        else:
            first = parts[0][1] if parts[0][0] == "text" else ""
            self._set_text(_md_to_html(first) if first else "")
            self.text_lbl.setVisible(bool(first))
            for kind, value in parts[1:] if first else parts:
                if kind == "table":
                    w = MarkdownTable(value, self._answer)
                else:
                    w = self._new_text_label()
                    w.setText(_md_to_html(value))
                self._answer_layout.addWidget(w)
                self._segments.append(w)
            self._answer.setVisible(True)
        self._final_text = text

    def _text_inset(self):
        """Horizontal and vertical space the answer frame adds around the text."""
        m = self._answer_layout.contentsMargins()
        border = 2 if self._bubble_on else 0
        return m.left() + m.right() + border, m.top() + m.bottom() + border

    def add_tool(self, tool_name: str, tool_input: dict) -> ToolCallRow:
        """Add a tool call row (deduplicated on name + input)."""
        tool_key = (tool_name, json.dumps(tool_input or {}, sort_keys=True))
        if tool_key in self._tool_keys:
            # Duplicate TOOL_USE event (e.g. CLI backend emits during stream
            # and again in _dispatch_one_tool).  Return the existing item.
            return self._tool_keys[tool_key]
        item = ToolCallRow(tool_name, tool_input or {}, self._tools_area)
        item._bubble = self
        self._tool_rows.append(item)
        self._tools_layout.addWidget(item)
        self._tools_area.setVisible(True)
        self._tool_keys[tool_key] = item
        # Force layout + paint so the tool row appears immediately,
        # even when the next queued slot blocks the main thread.
        self.updateGeometry()
        self.repaint()
        return item

    def stream_reasoning(self, text_chunk: str) -> None:
        self._ticker.append(text_chunk)

    def add_file(self, widget) -> None:
        """Embed a file/download card inside this turn (below tools, above text)."""
        self._files_layout.addWidget(widget)
        self._files_area.setVisible(True)
        self.updateGeometry()

    def add_visual(self, widget) -> None:
        """Embed a chart/stats/gif widget inside this turn (below the answer)."""
        self._visuals_layout.addWidget(widget)
        self._visuals_area.setVisible(True)
        self.updateGeometry()
        self._refresh_text_geometry()

    def set_streaming_text(self, text: str) -> None:
        was_progress = self._progress_timer.isActive()
        self._stop_progress()
        if text == self._stream_text and not was_progress:
            return
        if self._ticker.isVisible():
            self._ticker.hide_ticker()
        self._stream_text = text
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>'

        # Auto-promote: once we see a complete fenced block or table, switch to
        # full markdown rendering so syntax highlighting and tables appear live.
        # Cheap substring checks gate the regex scans — they would otherwise
        # walk the whole accumulated answer on every streamed frame.
        if not self._auto_format:
            maybe_fence = text.count("```") >= 2
            maybe_table = "\n|" in text or text.startswith("|")
            if (maybe_fence and _count_complete_fenced_blocks(text) > 0) or (
                maybe_table and _count_complete_tables(text) > 0
            ):
                self._auto_format = True

        if self._auto_format:
            # Debounced incremental render: full _md_to_html re-parse is O(n)
            # and blocks the main thread. We only re-parse when fence/table
            # markers change, AND only after 200ms of inactivity (so bursts of
            # deltas don't queue expensive re-parses). Between re-parses,
            # append inline-rendered deltas to the cached base.
            sig = (text.count("```"), text.count("\n|"))
            need_reparse = (
                sig != self._fmt_sig
                or len(text) < self._fmt_base_len
                or not self._fmt_base_html
            )
            if need_reparse:
                self._fmt_sig = sig
                self._fmt_pending_text = text
                # Restart the debounce timer; actual re-parse happens when
                # text has been stable for 200ms.
                if self._fmt_debounce_timer.isActive():
                    self._fmt_debounce_timer.stop()
                self._fmt_debounce_timer.start()
                # Immediate render: use the OLD cached base + inline tail of
                # everything since the last base length, so the user sees
                # progress right away even though the expensive re-parse is
                # deferred.
                tail = text[self._fmt_base_len:] if len(text) > self._fmt_base_len else text
                base = self._fmt_base_html or ""
                if base.endswith("</div>"):
                    body = base[:-len("</div>")] + _md_inline(tail) + "</div>"
                else:
                    body = base + _md_inline(tail) if tail else base
            else:
                # No new markers — use cached base + inline tail.
                tail = text[self._fmt_base_len:]
                base = self._fmt_base_html
                if tail and base.endswith("</div>"):
                    body = base[:-len("</div>")] + _md_inline(tail) + "</div>"
                elif tail:
                    body = base + _md_inline(tail)
                else:
                    body = base
            self._last_stream_text = text
            self._last_stream_html = body
            self._stream_html = body
            self._set_text(body + cursor)
            self._stream_doc_dirty = False
            if not self._geo_timer.isActive():
                self._geo_timer.start()
            return

        # Fast delta-only path: append into the view's persistent document —
        # O(delta) per frame. Full replaces are reserved for resets and for
        # frames where the document content is not the accumulated stream.
        rewound = len(self._last_stream_text) > len(text)
        if rewound:
            self._last_stream_text = ""
            self._last_stream_html = ""
            delta = text
        else:
            delta = text[len(self._last_stream_text):]
        self._last_stream_text = text

        if delta:
            html_delta = _md_inline(delta)
            self._last_stream_html += html_delta
            self._stream_html = self._last_stream_html
        if rewound or self._stream_doc_dirty or not delta:
            self._set_text(self._last_stream_html + cursor)
            self._stream_doc_dirty = False
        else:
            self._set_text(self._last_stream_html + cursor)

        if not self._geo_timer.isActive():
            self._geo_timer.start()

    def set_progress_text(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            self.clear_streaming_text()
            return
        if self._ticker.isVisible():
            self._ticker.hide_ticker()
        label = clean.rstrip(".")
        if label != self._progress_text or not self._progress_elapsed.isValid():
            self._progress_elapsed.start()
        self._progress_text = label
        self._stream_text = clean
        self._progress_phase = 0
        self._render_progress_text()
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def finalize_text(self, text: str) -> None:
        self._stop_progress()
        self._ticker.hide_ticker()
        self._stream_text = text
        self._stream_html = _md_to_html(text) if text else ""
        self._last_stream_text = ""
        self._last_stream_html = ""
        self._auto_format = False
        self._reset_format_cache()
        self._done = True
        self._stream_doc_dirty = True
        self._set_final(text)
        self._refresh_text_geometry()

    def finalize(self) -> None:
        """Stop all spinners; mark any still-running tools as timed out."""
        self._stop_progress()
        self._ticker.hide_ticker()
        self._last_stream_text = ""
        self._last_stream_html = ""
        self._auto_format = False
        self._reset_format_cache()
        self._done = True
        self._stream_doc_dirty = True
        if self._final_text is None or self._final_text != self._stream_text:
            self._set_text(self._stream_html)
        self._refresh_text_geometry()
        for row in self._tool_rows:
            row.mark_done("unknown", "No result reported")

    def mark_stopped(self) -> None:
        """Mark this turn as stopped by the user, keeping any partial output.

        Finalizes in-progress streaming text in place (no full markdown
        re-parse — the partial text stays as-is), stops all spinners, and
        appends a small "— Stopped —" note below the answer text so it's
        clear the turn was interrupted rather than complete.
        """
        self._stop_progress()
        self._ticker.hide_ticker()
        self._auto_format = False
        self._reset_format_cache()
        self._done = True
        self._stream_doc_dirty = True
        # Finalize the text with whatever was streamed so far so the cursor
        # is dropped and the document holds the partial answer.
        if self._stream_text:
            self._stream_html = _md_to_html(self._stream_text)
            self._set_final(self._stream_text)
        self._refresh_text_geometry()
        for row in self._tool_rows:
            row.mark_done("cancelled", "Stopped")
        # Append a visible "Stopped" note if not already present.
        if self._user_decision_lbl is None or "Stopped" not in (
            self._user_decision_lbl.text() if self._user_decision_lbl else ""
        ):
            self.set_user_decision("— Stopped —")

    # ── Backward-compat shims for chat_dock.py ────────────────────────────

    def add_thinking_block(self) -> None:
        pass  # reasoning now routes to ReasoningTicker via set_thinking_text

    def set_thinking_text(self, text: str) -> None:
        self._ticker.set_full(text)

    def finalize_thinking(self) -> None:
        pass  # no-op; ticker accumulates via append() and hides on set_streaming_text()

    def clear_streaming_text(self) -> None:
        self._stop_progress()
        self._stream_text = ""
        self._stream_html = ""
        self._last_stream_text = ""
        self._last_stream_html = ""
        self._auto_format = False
        self._reset_format_cache()
        self._done = False
        self._stream_doc_dirty = False
        self._set_text("")
        self.updateGeometry()

    def has_content(self) -> bool:
        return (
            bool(self._tool_rows) or bool(self._stream_text) or bool(self._progress_text)
            or self._ticker.isVisible() or self._files_area.isVisible()
            or self._visuals_area.isVisible()
        )

    def _reset_format_cache(self) -> None:
        self._fmt_sig = None
        self._fmt_base_len = 0
        self._fmt_base_html = ""
        self._fmt_pending_text = None
        if self._fmt_debounce_timer.isActive():
            self._fmt_debounce_timer.stop()

    def _do_fmt_reparse(self) -> None:
        """Debounced full re-parse of accumulated streaming text.

        Called 200ms after the last fence/table marker change. Updates the
        cached base HTML so subsequent inline-delta appends are correct.
        """
        text = self._last_stream_text
        if text is None:
            return
        self._fmt_base_html = _md_to_html(text) if text else ""
        self._fmt_base_len = len(text)
        self._fmt_pending_text = None
        # Refresh the visible label with the newly parsed base.
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>' if not self._done else ""
        body = self._fmt_base_html
        self._last_stream_text = text
        self._last_stream_html = body
        self._stream_html = body
        self._set_text(body + cursor)
        self._stream_doc_dirty = False
        if not self._geo_timer.isActive():
            self._geo_timer.start()

    def _stop_progress(self) -> None:
        if self._progress_timer.isActive():
            self._progress_timer.stop()
        if self._progress_text:
            self._stream_html = ""
            self._set_text("")
            self._refresh_text_geometry()
        self._progress_text = ""
        self._progress_elapsed.invalidate()

    def _progress_elapsed_suffix(self) -> str:
        if not self._progress_elapsed.isValid():
            return ""
        seconds = int(self._progress_elapsed.elapsed() / 1000)
        if seconds < 1:
            return ""
        if seconds < 60:
            return f" {seconds}s"
        return f" {seconds // 60}m {seconds % 60}s"

    def _render_progress_text(self) -> None:
        if not self._progress_text:
            return
        frame = _SPINNER_FRAMES[self._progress_phase % len(_SPINNER_FRAMES)]
        tail = "." * (self._progress_phase % 4)
        self._progress_phase += 1
        stalled = (
            self._progress_elapsed.isValid()
            and self._progress_elapsed.elapsed() >= _STALL_HINT_SECONDS * 1000
        )
        spinner_color = _WARN if stalled else _TEXT_3
        # One inline line (no markdown → no <p>), so the spinner sits beside the label.
        label = _html.escape(f"{self._progress_text}{tail}{self._progress_elapsed_suffix()}")
        label = re.sub(r"`([^`]+)`", rf'<span style="font-family:{MONO_STACK};">\1</span>', label)
        hint = (
            f'<span style="color:{_WARN};"> — taking longer than usual, press Esc to stop</span>'
            if stalled else ""
        )
        body = (
            f'<span style="color:{spinner_color};">{frame}</span>&nbsp;&nbsp;'
            f'<span style="color:{_TEXT_2};">{label}</span>{hint}'
        )
        self._stream_html = body
        self._stream_doc_dirty = True
        self._set_text(body, bubble=False)
        self._refresh_text_geometry()

    def set_user_decision(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        if self._user_decision_lbl is None:
            self._user_decision_lbl = QLabel("")
            self._user_decision_lbl.setWordWrap(True)
            self._user_decision_lbl.setMinimumWidth(0)
            self._user_decision_lbl.setTextFormat(Qt.TextFormat.PlainText)
            self._user_decision_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._user_decision_lbl.setStyleSheet(
                f"color:{_TEXT_2}; background:{_SURFACE_2}; border:1px solid {_BORDER_SOFT};"
                f" border-radius:8px; padding:6px 10px; margin:6px 2px 0 2px;"
                f" font-size:{fs(12)}px;"
            )
            self._outer.addWidget(self._user_decision_lbl)
        if clean.startswith("—") and clean.endswith("—"):
            self._user_decision_lbl.setText(clean)
        elif clean.lower() == "cancelled":
            self._user_decision_lbl.setText("— Cancelled —")
        else:
            self._user_decision_lbl.setText(f"User chose: {clean}")
        self.updateGeometry()

    def set_footer(self, model: str = "", elapsed: str = "") -> None:
        """Quiet row under a finished answer: Copy · model · duration."""
        if self._footer is None:
            self._footer = QWidget(self)
            self._footer.setStyleSheet("background:transparent;")
            row = QHBoxLayout(self._footer)
            row.setContentsMargins(2, 8, 2, 0)
            row.setSpacing(8)
            copy_btn = QPushButton("⧉ Copy")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setToolTip("Copy response as Markdown")
            copy_btn.setStyleSheet(
                f"QPushButton {{ color:{_TEXT_3}; background:transparent; border:none;"
                f" padding:0; font-size:{fs(11)}px; }}"
                f"QPushButton:hover {{ color:{_TEXT}; }}"
            )
            copy_btn.clicked.connect(self._copy_answer)
            row.addWidget(copy_btn)
            self._footer_meta = QLabel("")
            self._footer_meta.setTextFormat(Qt.TextFormat.PlainText)
            self._footer_meta.setStyleSheet(
                f"color:{_TEXT_4}; background:transparent; font-size:{fs(11)}px;"
            )
            row.addWidget(self._footer_meta)
            row.addStretch(1)
            self._outer.addWidget(self._footer)
        bits = [b for b in (model, f"worked for {elapsed}" if elapsed else "") if b]
        self._footer_meta.setText("  ·  ".join(bits))
        self._footer.setVisible(bool(self._stream_text.strip()))
        self.updateGeometry()

    def _copy_answer(self) -> None:
        if self._stream_text.strip():
            _copy_to_clipboard(self._stream_text)

    # ── Layout ────────────────────────────────────────────────────────────

    def hasHeightForWidth(self):
        return True

    def _content_height(self, width):
        """Tight height the turn needs at ``width`` — sum of the visible rows."""
        if not self._outer:
            return -1
        m = self._outer.contentsMargins()
        inner_w = width - m.left() - m.right()
        if inner_w <= 0:
            return -1

        raw_text = self.text_lbl.text() or ""
        if raw_text.strip():
            inset_w, inset_h = self._text_inset()
            lh = self.text_lbl.heightForWidth(inner_w - inset_w)
            if lh <= 0:
                lh = self.text_lbl.sizeHint().height()
            lh += inset_h
        else:
            lh = 0

        def _area_h(area):
            return area.sizeHint().height() if area.isVisible() else 0

        tools_h = _area_h(self._tools_area)
        files_h = _area_h(self._files_area)
        visuals_h = _area_h(self._visuals_area)
        ticker_h = _area_h(self._ticker)
        decision_h = (
            self._user_decision_lbl.sizeHint().height()
            if self._user_decision_lbl is not None and self._user_decision_lbl.isVisible()
            else 0
        )
        footer_h = (
            self._footer.sizeHint().height()
            if self._footer is not None and self._footer.isVisible()
            else 0
        )
        total = (
            lh + tools_h + files_h + visuals_h + ticker_h + decision_h + footer_h
            + m.top() + m.bottom()
        )
        return max(1, total)

    def heightForWidth(self, width):
        return self._content_height(width)

    def sizeHint(self):
        base = super().sizeHint()
        w = self._effective_width()
        h = self._content_height(w)
        if h > 0:
            return QSize(base.width(), h)
        return base

    def minimumSizeHint(self):
        return super().minimumSizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._outer:
            m = self._outer.contentsMargins()
            w = event.size().width() - m.left() - m.right() - self._text_inset()[0]
            if w > 0 and self.text_lbl.width() != w:
                self.text_lbl.setFixedWidth(w)
        if not self._geo_timer.isActive():
            self._geo_timer.start()
