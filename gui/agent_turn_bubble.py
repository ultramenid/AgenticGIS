"""AgentTurnBubble — one widget per complete agent response turn.

Reasoning ticker streams LLM thinking in one line above grouped tool calls.
Tool calls group by name with braille spinners → ✓/! on completion.
"""

import html as _html
import json

from qgis.PyQt.QtCore import Qt, QElapsedTimer, QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .downloadable import HoverDownloadButton, save_text, _safe_name
from .message_bubble import (
    _md_inline,
    _md_to_html,
    _show_code_context_menu,
    _count_complete_fenced_blocks,
    _count_complete_tables,
)

from .theme import (
    DOCK_SURFACE as _SURFACE,
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


class ReasoningTicker(QWidget):
    """Single-line streaming reasoning display. Shows last 100 chars of LLM thinking."""

    _MAX_CHARS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer = ""
        self._phase = 0
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setItalic(True)

        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(12, 2, 12, 2)
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


class ToolSubItem(QWidget):
    """One tool call line: [connector]  [icon]  [key_label]  [json_suffix]"""

    def __init__(self, tool_input: dict, group, is_last: bool = False, parent=None):
        super().__init__(parent)
        self._group = group   # ToolGroupRow | None
        self._done = False
        self._bubble = None   # set by AgentTurnBubble.add_tool()
        self._result = ""

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background:transparent;")

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(20, 1, 12, 1)
        hbox.setSpacing(4)

        self._conn_lbl = QLabel("└─" if is_last else "├─")
        self._conn_lbl.setFont(mono)
        self._conn_lbl.setStyleSheet(f"color:{_BORDER}; background:transparent;")
        hbox.addWidget(self._conn_lbl)

        self._icon_lbl = QLabel("·")
        self._icon_lbl.setFont(mono)
        self._icon_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        self._icon_lbl.setFixedWidth(14)
        hbox.addWidget(self._icon_lbl)

        key_lbl = QLabel(self._extract_key(tool_input))
        key_lbl.setFont(mono)
        key_lbl.setStyleSheet(f"color:{_TEXT}; background:transparent; font-size:10px;")
        key_lbl.setTextFormat(Qt.TextFormat.PlainText)
        hbox.addWidget(key_lbl)

        json_str = json.dumps(tool_input, default=str)
        if len(json_str) > 60:
            json_str = json_str[:60] + "…"
        json_lbl = QLabel(json_str)
        json_lbl.setFont(mono)
        json_lbl.setStyleSheet(f"color:{_TEXT_3}; background:transparent; font-size:10px;")
        json_lbl.setTextFormat(Qt.TextFormat.PlainText)
        hbox.addWidget(json_lbl)
        hbox.addStretch()

    # ── Public API ────────────────────────────────────────────────────────

    def set_last(self, is_last: bool) -> None:
        """Recalculate connector prefix when a new sibling is added."""
        try:
            self._conn_lbl.setText("└─" if is_last else "├─")
        except RuntimeError:
            pass

    def mark_done(self, is_error: bool = False) -> None:
        """Show ✓ or !. Internal — call set_result() from chat_dock."""
        if self._done:
            return
        self._done = True
        try:
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


class ToolGroupRow(QWidget):
    """Groups all ToolSubItems for one tool_name under a CLI-style spinner."""

    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._running_count = 0
        self._had_error = False
        self._finalized = False
        self._pulse = 0
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet("background:transparent;")

        mono = QFont("JetBrains Mono", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(0)

        # Header row
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 2, 12, 2)
        hbox.setSpacing(6)

        self._dot_lbl = QLabel(_SPINNER_FRAMES[0])
        self._dot_lbl.setFont(mono)
        self._dot_lbl.setFixedWidth(18)
        self._dot_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        hbox.addWidget(self._dot_lbl)

        name_lbl = QLabel(_html.escape(tool_name))
        name_lbl.setFont(mono)
        name_lbl.setStyleSheet(
            f"color:{_TEXT}; background:transparent; font-size:10px;"
        )
        name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        hbox.addWidget(name_lbl)

        self._count_lbl = QLabel("")
        self._count_lbl.setFont(mono)
        self._count_lbl.setStyleSheet(
            f"color:{_TEXT_3}; background:transparent; font-size:10px;"
        )
        hbox.addWidget(self._count_lbl)
        hbox.addStretch()

        self._state_lbl = QLabel("·")
        self._state_lbl.setFont(mono)
        self._state_lbl.setStyleSheet(f"color:{_WARN}; background:transparent;")
        hbox.addWidget(self._state_lbl)

        self._layout.addWidget(header)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick_running)
        self._timer.start()
        self._tick_running()

    def add_item(self, tool_input: dict) -> ToolSubItem:
        """Append a sub-item; recalculate connectors so only the last shows └─."""
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
        if hasattr(self, "_timer"):
            self._timer.stop()
        try:
            if self._had_error:
                self._dot_lbl.setText("!")
                self._dot_lbl.setStyleSheet(f"color:{_DANGER}; background:transparent;")
                self._state_lbl.setText("!")
                self._state_lbl.setStyleSheet(f"color:{_DANGER}; background:transparent;")
            else:
                self._dot_lbl.setText("✓")
                self._dot_lbl.setStyleSheet(f"color:{_SUCCESS}; background:transparent;")
                self._state_lbl.setText("✓")
                self._state_lbl.setStyleSheet(f"color:{_SUCCESS}; background:transparent;")
        except RuntimeError:
            pass

    def _tick_running(self) -> None:
        if self._finalized:
            return
        self._pulse = (self._pulse + 1) % len(_SPINNER_FRAMES)
        elapsed = self._elapsed.elapsed() / 1000.0
        try:
            self._dot_lbl.setText(_SPINNER_FRAMES[self._pulse])
            self._state_lbl.setText(f"processing {elapsed:.1f}s")
        except RuntimeError:
            self._timer.stop()


class AgentTurnBubble(QFrame):
    """One agent turn: reasoning ticker + grouped tool rows + streaming text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: dict = {}   # tool_name → ToolGroupRow
        self._tool_keys: dict = {}  # (name, input_json) → ToolSubItem (dedup)
        self._stream_text = ""
        self._stream_html = ""
        self._progress_text = ""
        self._progress_phase = 0
        self._progress_elapsed = QElapsedTimer()
        self._user_decision_lbl = None
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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
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
        self._tools_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._tools_area.setStyleSheet("background:transparent;")
        self._tools_layout = QVBoxLayout(self._tools_area)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(0)
        self._outer.addWidget(self._tools_area)

        # Inline file/download cards — between tool rows and the answer text.
        self._files_area = QWidget(self)
        self._files_area.setVisible(False)
        self._files_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._files_area.setStyleSheet("background:transparent;")
        self._files_layout = QVBoxLayout(self._files_area)
        self._files_layout.setContentsMargins(12, 6, 12, 10)
        self._files_layout.setSpacing(6)
        self._outer.addWidget(self._files_area)

        self.text_lbl = QLabel("")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setMinimumWidth(0)
        self.text_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.text_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.text_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.text_lbl.setOpenExternalLinks(True)
        self.text_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_lbl.customContextMenuRequested.connect(self._show_text_context_menu)
        font = QFont("JetBrains Mono", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_lbl.setFont(font)
        self.text_lbl.setStyleSheet(f"""
            color:{_TEXT}; background:transparent; border:none;
            font-family:'JetBrains Mono',monospace;
            font-size:12px; line-height:1.5;
        """)
        self.text_lbl.setContentsMargins(12, 6, 12, 0)
        self._outer.addWidget(self.text_lbl)

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

    def _refresh_text_geometry(self) -> None:
        """Propagate the text view's size into the transcript layout."""
        if self._outer is not None and self.width() > 0:
            margins = self._outer.contentsMargins()
            label_w = self.width() - margins.left() - margins.right()
            if label_w > 0:
                self.text_lbl.setFixedWidth(label_w)

        label_h = self.text_lbl.heightForWidth(self.text_lbl.width())
        if label_h > 0:
            self.text_lbl.setMinimumHeight(label_h)

        self.text_lbl.updateGeometry()
        if self._outer is not None:
            self._outer.invalidate()
        self.updateGeometry()

        parent = self.parentWidget()
        if parent is not None:
            layout = parent.layout()
            if layout is not None:
                layout.invalidate()
            parent.updateGeometry()

    def _show_text_context_menu(self, pos) -> None:
        _show_code_context_menu(self, self.text_lbl, pos, self._stream_text)

    def add_tool(self, tool_name: str, tool_input: dict) -> ToolSubItem:
        """Add a tool call; creates group if tool_name is new. Returns ToolSubItem."""
        tool_key = (tool_name, json.dumps(tool_input or {}, sort_keys=True))
        if tool_key in self._tool_keys:
            # Duplicate TOOL_USE event (e.g. CLI backend emits during stream
            # and again in _dispatch_one_tool).  Return the existing item.
            return self._tool_keys[tool_key]
        if tool_name not in self._groups:
            group = ToolGroupRow(tool_name, self._tools_area)
            self._groups[tool_name] = group
            self._tools_layout.addWidget(group)
            self._tools_area.setVisible(True)
        item = self._groups[tool_name].add_item(tool_input)
        item._bubble = self
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
            self.text_lbl.setText(body + cursor)
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
            self.text_lbl.setText(self._last_stream_html + cursor)
            self._stream_doc_dirty = False
        else:
            self.text_lbl.setText(self._last_stream_html + cursor)

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
        self.text_lbl.setText(self._stream_html)
        self._refresh_text_geometry()
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
        self._stop_progress()
        self._ticker.hide_ticker()
        self._last_stream_text = ""
        self._last_stream_html = ""
        self._auto_format = False
        self._reset_format_cache()
        self._done = True
        self._stream_doc_dirty = True
        self.text_lbl.setText(self._stream_html)
        self._refresh_text_geometry()
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
        self._stop_progress()
        self._stream_text = ""
        self._stream_html = ""
        self._last_stream_text = ""
        self._last_stream_html = ""
        self._auto_format = False
        self._reset_format_cache()
        self._done = False
        self._stream_doc_dirty = False
        self.text_lbl.setText("")
        self.updateGeometry()

    def has_content(self) -> bool:
        return (
            bool(self._groups) or bool(self._stream_text) or bool(self._progress_text)
            or self._ticker.isVisible() or self._files_area.isVisible()
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
        self.text_lbl.setText(body + cursor)
        self._stream_doc_dirty = False
        if not self._geo_timer.isActive():
            self._geo_timer.start()

    def _stop_progress(self) -> None:
        if self._progress_timer.isActive():
            self._progress_timer.stop()
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
        prefix = (
            f'<span style="color:{_TEXT_3};font-weight:400;">{frame}</span>'
            f'<span style="color:{_TEXT_4};">&nbsp;&nbsp;</span>'
        )
        body = _md_to_html(f"{self._progress_text}{tail}{self._progress_elapsed_suffix()}")
        body = body.replace(">", f">{prefix}", 1)
        self._stream_html = body
        self._stream_doc_dirty = True
        self.text_lbl.setText(body)
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
                files_h = (
                    self._files_area.sizeHint().height()
                    if self._files_area.isVisible()
                    else 0
                )
                ticker_h = (
                    self._ticker.sizeHint().height()
                    if self._ticker.isVisible()
                    else 0
                )
                if lh >= 0:
                    return lh + tools_h + files_h + ticker_h + m.top() + m.bottom() + 8
        return -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._outer:
            m = self._outer.contentsMargins()
            w = event.size().width() - m.left() - m.right()
            if w > 0:
                self.text_lbl.setFixedWidth(w)
