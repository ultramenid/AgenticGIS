"""Settings dialog — styled to match the AskUser/permission card aesthetic.

Three connection modes:
  1. API key       — pick a built-in provider or custom endpoint.
  2. Custom endpoint — any OpenAI- or Anthropic-compatible server.
  3. Subscription   — use an installed CLI agent (Claude Code, OpenCode, …).
"""

from qgis.PyQt.QtCore import Qt, QThread, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from ..backends import providers

# ── Design tokens (matches ask_user_card.py) ──────────────────────────────────
_SURFACE     = "#1f1f1d"
_SURFACE_2   = "#262521"
_SURFACE_HOV = "#2d2b25"
_INPUT_BG    = "#191918"
_BORDER      = "#4a4234"
_BORDER_SOFT = "#343129"
_TEXT        = "#eeeeea"
_TEXT_2      = "#bbb7ad"
_TEXT_3      = "#7d786d"
_ACCENT      = "#e7dfcf"
_ACCENT_HOV  = "#f2eadb"
_WARN        = "#d99a3c"
_SUCCESS     = "#5aad6b"
_DANGER      = "#e05c5c"

# ── Mode / agent constants ────────────────────────────────────────────────────
_MODE_LABELS = [
    ("API key", config_mod.MODE_API_KEY),
    ("Custom endpoint", config_mod.MODE_CUSTOM),
    ("Subscription", config_mod.MODE_SUBSCRIPTION),
]
_CLI_AGENTS = [
    ("claude", "Claude Code"),
    ("opencode", "OpenCode"),
    ("codex", "OpenAI Codex CLI"),
    ("gemini", "Google Gemini CLI"),
]
_FORMAT_LABELS = [
    ("OpenAI-compatible", "openai"),
    ("Anthropic-compatible", "anthropic"),
]

# ── Font helper ───────────────────────────────────────────────────────────────
def _mono(size=10, weight=QFont.Normal):
    f = QFont("JetBrains Mono", size)
    f.setStyleHint(QFont.Monospace)
    f.setWeight(weight)
    return f


# ── Stylesheets ───────────────────────────────────────────────────────────────
_DIALOG_SS = (
    f"QDialog {{ background: {_SURFACE}; }}"
    f"QWidget {{ background: {_SURFACE}; color: {_TEXT}; }}"
    f"QScrollArea {{ background: {_SURFACE}; border: none; }}"
    f"QScrollBar:vertical {{"
    f"  background: {_SURFACE}; width: 6px; margin: 0;"
    f"}}"
    f"QScrollBar::handle:vertical {{"
    f"  background: {_BORDER_SOFT}; border-radius: 3px; min-height: 20px;"
    f"}}"
    f"QScrollBar::add-line:vertical,"
    f"QScrollBar::sub-line:vertical {{ height: 0; }}"
)

_INPUT_SS = (
    f"QLineEdit {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px;"
    f"  padding: 5px 9px;"
    f"  selection-background-color: {_BORDER};"
    f"}}"
    f"QLineEdit:focus {{ border-color: {_WARN}; }}"
    f"QLineEdit:disabled {{"
    f"  color: {_TEXT_3}; border-color: {_BORDER_SOFT};"
    f"}}"
)

_COMBO_SS = (
    f"QComboBox {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px; padding: 5px 9px;"
    f"}}"
    f"QComboBox::drop-down {{ border: none; width: 20px; }}"
    f"QComboBox QAbstractItemView {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER};"
    f"  selection-background-color: {_BORDER}; outline: none;"
    f"}}"
)

_TAB_SS = (
    f"QTabBar {{"
    f"  background: transparent;"
    f"}}"
    f"QTabBar::tab {{"
    f"  background: {_INPUT_BG}; color: {_TEXT_3};"
    f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px;"
    f"  padding: 7px 10px; margin-right: 6px;"
    f"}}"
    f"QTabBar::tab:hover {{"
    f"  background: {_SURFACE_HOV}; color: {_TEXT};"
    f"  border-color: {_BORDER};"
    f"}}"
    f"QTabBar::tab:selected {{"
    f"  background: {_ACCENT}; color: {_SURFACE};"
    f"  border-color: {_ACCENT};"
    f"}}"
)

_BTN_PRIMARY_SS = (
    f"QPushButton {{"
    f"  background: {_ACCENT}; color: {_SURFACE};"
    f"  border: none; border-radius: 7px; padding: 7px 20px; font-weight: 600;"
    f"}}"
    f"QPushButton:hover {{ background: {_ACCENT_HOV}; }}"
    f"QPushButton:pressed {{ background: {_TEXT_2}; }}"
)

_BTN_SECONDARY_SS = (
    f"QPushButton {{"
    f"  background: transparent; color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER}; border-radius: 7px; padding: 7px 20px;"
    f"}}"
    f"QPushButton:hover {{ background: {_SURFACE_HOV}; color: {_TEXT}; }}"
)

_BTN_GHOST_SS = (
    f"QPushButton {{"
    f"  background: {_INPUT_BG}; color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px; padding: 5px 10px;"
    f"}}"
    f"QPushButton:hover {{ background: {_SURFACE_HOV}; color: {_TEXT}; }}"
    f"QPushButton:disabled {{ color: {_TEXT_3}; border-color: {_BORDER_SOFT}; }}"
)

# ── Widget factories ──────────────────────────────────────────────────────────
def _inp(widget):
    widget.setFont(_mono(10))
    widget.setStyleSheet(_INPUT_SS)
    return widget


def _cmb(widget):
    widget.setFont(_mono(10))
    widget.setStyleSheet(_COMBO_SS)
    return widget


class _ModelPickerWidget(QWidget):
    """Select2-style model picker.

    Shows the selected model in a styled button. On click, drops a popup
    with a search field and a scrollable list — the active model pinned at
    the top with a coloured badge, the rest sorted alphabetically.
    Typing filters in real time; pressing Enter or clicking an item selects
    it. If no list entry matches, pressing Enter saves the typed text as a
    custom model name.
    """

    modelChanged = pyqtSignal(str)

    _POPUP_SS = (
        f"QFrame#ModelPopup {{"
        f"  background: {_INPUT_BG}; border: 1px solid {_BORDER};"
        f"  border-radius: 8px;"
        f"}}"
        f"QLineEdit {{"
        f"  background: {_SURFACE_2}; color: {_TEXT};"
        f"  border: none; border-bottom: 1px solid {_BORDER_SOFT};"
        f"  border-top-left-radius: 7px; border-top-right-radius: 7px;"
        f"  border-bottom-left-radius: 0; border-bottom-right-radius: 0;"
        f"  padding: 7px 10px;"
        f"}}"
        f"QListWidget {{"
        f"  background: transparent; color: {_TEXT};"
        f"  border: none; outline: none;"
        f"}}"
        f"QListWidget::item {{ padding: 6px 10px; border-radius: 4px; }}"
        f"QListWidget::item:hover {{ background: {_SURFACE_HOV}; }}"
        f"QListWidget::item:selected {{"
        f"  background: {_BORDER}; color: {_TEXT};"
        f"}}"
        f"QScrollBar:vertical {{"
        f"  background: {_INPUT_BG}; width: 5px; margin: 0;"
        f"}}"
        f"QScrollBar::handle:vertical {{"
        f"  background: {_BORDER_SOFT}; border-radius: 2px; min-height: 16px;"
        f"}}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
    )

    def __init__(self, placeholder="Select or type a model name", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._models = []    # full sorted list returned by the API
        self._active = ""    # model id currently saved in config
        self._selected = ""  # what the user has chosen or typed
        self._popup = None
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._btn = QPushButton()
        self._btn.setFont(_mono(10))
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn.clicked.connect(self._toggle_popup)
        self._refresh_btn()
        lay.addWidget(self._btn)

    def _refresh_btn(self):
        text = self._selected
        is_active = bool(text and text == self._active)
        display = (text + "  ●") if is_active else (text or self._placeholder)
        color = _TEXT if text else _TEXT_3
        badge_color = _WARN if is_active else "transparent"
        self._btn.setText(display)
        self._btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: {_INPUT_BG}; color: {color};"
            f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px;"
            f"  padding: 5px 30px 5px 9px; text-align: left;"
            f"}}"
            f"QPushButton:hover {{ border-color: {_WARN}; }}"
            f"QPushButton::menu-indicator {{ width: 0; }}"
        )
        # Arrow overlay via a child label positioned at the right
        if not hasattr(self, "_arrow_lbl"):
            self._arrow_lbl = QLabel("▾", self)
            self._arrow_lbl.setFont(_mono(9))
            self._arrow_lbl.setStyleSheet(
                f"color: {_TEXT_3}; background: transparent;"
            )
        self._arrow_lbl.adjustSize()
        self._arrow_lbl.move(
            self._btn.width() - self._arrow_lbl.width() - 10,
            (self._btn.height() - self._arrow_lbl.height()) // 2,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_btn()

    # ── public API ────────────────────────────────────────────────────────────
    def setModels(self, models, keep_current=True):
        self._models = sorted(models) if models else []
        if not keep_current:
            self._selected = ""
        if self._popup:
            self._rebuild_list()

    def setActive(self, model_id):
        self._active = model_id or ""
        self._refresh_btn()

    def currentText(self):
        return self._selected

    def setCurrentText(self, text):
        self._selected = text or ""
        self._refresh_btn()

    # ── popup ─────────────────────────────────────────────────────────────────
    def _toggle_popup(self):
        if self._popup and self._popup.isVisible():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self):
        popup = QFrame(None, Qt.Popup | Qt.FramelessWindowHint)
        popup.setObjectName("ModelPopup")
        popup.setStyleSheet(self._POPUP_SS)
        popup.setFixedWidth(max(self._btn.width(), 300))

        vlay = QVBoxLayout(popup)
        vlay.setContentsMargins(0, 0, 0, 6)
        vlay.setSpacing(0)

        search = QLineEdit()
        search.setFont(_mono(10))
        search.setPlaceholderText("Search models or type a custom name…")
        vlay.addWidget(search)

        lst = QListWidget()
        lst.setFont(_mono(10))
        lst.setMaximumHeight(240)
        vlay.addWidget(lst)

        self._popup = popup
        popup._search = search
        popup._list = lst

        self._rebuild_list()

        pos = self._btn.mapToGlobal(self._btn.rect().bottomLeft())
        popup.move(pos)
        popup.show()
        search.setFocus()

        search.textChanged.connect(self._rebuild_list)
        search.returnPressed.connect(self._commit_search)
        lst.itemClicked.connect(self._pick_item)
        lst.itemActivated.connect(self._pick_item)

    def _close_popup(self):
        if self._popup:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None

    def _rebuild_list(self, query=None):
        if not self._popup:
            return
        if query is None:
            query = self._popup._search.text()
        lst = self._popup._list
        lst.clear()
        q = query.strip().lower()

        # Section header helper
        def _header(text):
            it = QListWidgetItem(text)
            it.setFont(_mono(8, QFont.DemiBold))
            it.setForeground(QColor(_TEXT_3))
            it.setFlags(Qt.NoItemFlags)  # not selectable
            return it

        # ── Active model pinned at top ────────────────────────────────────
        if self._active:
            if not q or q in self._active.lower():
                lst.addItem(_header("  ACTIVE"))
                it = QListWidgetItem(f"  {self._active}")
                it.setData(Qt.UserRole, self._active)
                it.setForeground(QColor(_WARN))
                it.setFont(_mono(10, QFont.DemiBold))
                lst.addItem(it)

        # ── Available models ──────────────────────────────────────────────
        filtered = [
            m for m in self._models
            if m != self._active and (not q or q in m.lower())
        ]
        if filtered:
            lst.addItem(_header("  AVAILABLE"))
            for m in filtered:
                it = QListWidgetItem(f"  {m}")
                it.setData(Qt.UserRole, m)
                lst.addItem(it)

        # ── Custom entry hint when nothing matches ────────────────────────
        if q and not self._active_matches(q) and not filtered:
            lst.addItem(_header("  CUSTOM"))
            it = QListWidgetItem(f'  Use "{query.strip()}"')
            it.setData(Qt.UserRole, query.strip())
            it.setForeground(QColor(_TEXT_2))
            lst.addItem(it)

    def _active_matches(self, q):
        return bool(self._active and q and q in self._active.lower())

    def _commit_search(self):
        """Enter pressed in the search box: pick top selectable item or typed text."""
        if not self._popup:
            return
        lst = self._popup._list
        # Find first selectable item
        for i in range(lst.count()):
            it = lst.item(i)
            if it.flags() & Qt.ItemIsEnabled and it.data(Qt.UserRole):
                self._apply(it.data(Qt.UserRole))
                return
        # Fall back to raw typed text
        typed = self._popup._search.text().strip()
        if typed:
            self._apply(typed)

    def _pick_item(self, item):
        val = item.data(Qt.UserRole)
        if val:
            self._apply(val)

    def _apply(self, model_id):
        self._selected = model_id
        self._refresh_btn()
        self.modelChanged.emit(self._selected)
        self._close_popup()


# ── Background model-list / connection-test worker ─────────────────────────────
class _ModelFetchWorker(QThread):
    """Runs a list_models() call off the UI thread.

    ``fn`` returns ``(models, error)``; emits ``done(models, error)`` where an
    empty error string means success.
    """

    done = pyqtSignal(list, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            models, err = self._fn()
        except Exception as exc:  # noqa: BLE001
            models, err = [], f"{type(exc).__name__}: {exc}"
        self.done.emit(list(models or []), err or "")


def _lbl(text, color=_TEXT_2, size=10, italic=False):
    w = QLabel(text)
    f = _mono(size)
    if italic:
        f.setItalic(True)
    w.setFont(f)
    w.setStyleSheet(f"color: {color}; background: transparent;")
    return w


def _ghost_btn(text):
    b = QPushButton(text)
    b.setFont(_mono(10))
    b.setStyleSheet(_BTN_GHOST_SS)
    return b


def _separator():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {_BORDER_SOFT}; background: {_BORDER_SOFT};")
    line.setFixedHeight(1)
    return line


# ── Section card ──────────────────────────────────────────────────────────────
class _SectionCard(QFrame):
    """Rounded, bordered card with a labelled title strip."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsSectionCard")
        self.setStyleSheet(f"""
            QFrame#SettingsSectionCard {{
                background: {_SURFACE_2};
                border: 1px solid {_BORDER_SOFT};
                border-radius: 8px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("SectionCardHeader")
        header.setStyleSheet(f"""
            QWidget#SectionCardHeader {{
                background: transparent;
                border-bottom: 1px solid {_BORDER_SOFT};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
        """)
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 9, 14, 9)
        h.setSpacing(7)

        dot = QLabel("●")
        dot.setFont(_mono(7))
        dot.setStyleSheet(f"color: {_WARN}; background: transparent; border: none;")
        h.addWidget(dot, 0, Qt.AlignVCenter)

        cap = QLabel(title.upper())
        cap.setFont(_mono(8, QFont.DemiBold))
        cap.setStyleSheet(
            f"color: {_TEXT_3}; background: transparent; border: none; letter-spacing: 1px;"
        )
        h.addWidget(cap, 1, Qt.AlignVCenter)
        root.addWidget(header)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(14, 12, 14, 14)
        self._body.setSpacing(10)
        root.addWidget(body)

    def body(self):
        return self._body


# ── Collapsible card ──────────────────────────────────────────────────────────
class _CollapsibleCard(QFrame):
    def __init__(self, title, parent=None, initially_expanded=False):
        super().__init__(parent)
        self._expanded = initially_expanded
        self.setObjectName("SettingsCollapsibleCard")
        self.setStyleSheet(f"""
            QFrame#SettingsCollapsibleCard {{
                background: {_SURFACE_2};
                border: 1px solid {_BORDER_SOFT};
                border-radius: 8px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("CollapsibleHeader")
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet(
            "QWidget#CollapsibleHeader { background: transparent; }"
        )
        h = QHBoxLayout(self._header)
        h.setContentsMargins(14, 9, 14, 9)
        h.setSpacing(7)

        dot = QLabel("●")
        dot.setFont(_mono(7))
        dot.setStyleSheet(f"color: {_TEXT_3}; background: transparent; border: none;")
        h.addWidget(dot, 0, Qt.AlignVCenter)

        cap = QLabel(title.upper())
        cap.setFont(_mono(8, QFont.DemiBold))
        cap.setStyleSheet(
            f"color: {_TEXT_3}; background: transparent; border: none; letter-spacing: 1px;"
        )
        h.addWidget(cap, 1, Qt.AlignVCenter)

        self._arrow = QLabel("▾" if initially_expanded else "▸")
        self._arrow.setFont(_mono(9))
        self._arrow.setStyleSheet(
            f"color: {_TEXT_3}; background: transparent; border: none;"
        )
        h.addWidget(self._arrow, 0, Qt.AlignVCenter)
        root.addWidget(self._header)
        self._header.mousePressEvent = lambda _e: self._toggle()

        self._content = QWidget()
        self._content.setObjectName("CollapsibleContent")
        self._content.setStyleSheet(f"""
            QWidget#CollapsibleContent {{
                background: transparent;
                border-top: 1px solid {_BORDER_SOFT};
            }}
        """)
        self._body = QVBoxLayout(self._content)
        self._body.setContentsMargins(14, 12, 14, 14)
        self._body.setSpacing(10)
        root.addWidget(self._content)
        self._content.setVisible(initially_expanded)

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")

    def body(self):
        return self._body


# ── Main dialog ───────────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("AgenticGIS — Settings")
        self.setMinimumWidth(560)
        self.setMinimumHeight(660)
        self.resize(580, 760)
        self.setStyleSheet(_DIALOG_SS)
        self._build_ui()
        self._load()

    # ── build ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        # ─ header ─
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        marker = QLabel("●")
        marker.setFont(_mono(10))
        marker.setStyleSheet(f"color: {_WARN}; background: transparent;")
        hdr.addWidget(marker, 0, Qt.AlignVCenter)

        title = QLabel("AgenticGIS  —  Settings")
        title.setFont(_mono(13, QFont.DemiBold))
        title.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        hdr.addWidget(title, 1, Qt.AlignVCenter)
        root.addLayout(hdr)
        root.addWidget(_separator())

        # ─ scrollable body ─
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body_w = QWidget()
        body_w.setStyleSheet(f"background: {_SURFACE};")
        body = QVBoxLayout(body_w)
        body.setContentsMargins(0, 2, 0, 2)
        body.setSpacing(10)
        scroll.setWidget(body_w)
        root.addWidget(scroll, 1)

        # ─ Connection card ─
        conn = _SectionCard("Connection")
        cb = conn.body()

        self.connection_tabs = QTabBar()
        self.connection_tabs.setFont(_mono(10, QFont.DemiBold))
        self.connection_tabs.setStyleSheet(_TAB_SS)
        self.connection_tabs.setDrawBase(False)
        self.connection_tabs.setExpanding(False)
        self.connection_tabs.setUsesScrollButtons(False)
        for label, _ in _MODE_LABELS:
            self.connection_tabs.addTab(label)
        self.connection_tabs.currentChanged.connect(self.stack_set)
        cb.addWidget(self.connection_tabs, 0, Qt.AlignLeft)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent; border: none;")
        self.stack.addWidget(self._api_key_panel())
        self.stack.addWidget(self._custom_panel())
        self.stack.addWidget(self._subscription_panel())
        cb.addWidget(self.stack)
        body.addWidget(conn)

        # ─ Advanced card (collapsible) ─
        adv = _CollapsibleCard("Advanced", initially_expanded=False)
        ab = adv.body()

        form3 = QFormLayout()
        form3.setSpacing(10)
        form3.setContentsMargins(0, 0, 0, 0)
        form3.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.timeout_edit = _inp(QLineEdit())
        self.timeout_edit.setPlaceholderText("60")
        form3.addRow(_lbl("Main-thread timeout (s):"), self.timeout_edit)

        self.proc_timeout_edit = _inp(QLineEdit())
        self.proc_timeout_edit.setPlaceholderText("120")
        form3.addRow(_lbl("Processing timeout (s):"), self.proc_timeout_edit)

        self.poll_interval_edit = _inp(QLineEdit())
        self.poll_interval_edit.setPlaceholderText("0.5")
        form3.addRow(_lbl("MCP poll interval (s):"), self.poll_interval_edit)

        ab.addLayout(form3)
        body.addWidget(adv)

        body.addStretch(1)

        # ─ footer ─
        footer = _lbl(
            "No installation required — runs entirely on QGIS's bundled Python.",
            color=_TEXT_3, size=9,
        )
        footer.setWordWrap(True)
        root.addWidget(footer)

        root.addWidget(_separator())

        # ─ button row ─
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFont(_mono(10, QFont.DemiBold))
        self._cancel_btn.setStyleSheet(_BTN_SECONDARY_SS)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setFont(_mono(10, QFont.DemiBold))
        self._save_btn.setStyleSheet(_BTN_PRIMARY_SS)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(self._save_btn)

        root.addLayout(btn_row)

    # ── stack panels ──────────────────────────────────────────────────────────
    def stack_set(self, index):
        self.stack.setCurrentIndex(index)
        self._update_connection_tab_labels()

    def _current_mode(self):
        index = self.connection_tabs.currentIndex()
        if index < 0 or index >= len(_MODE_LABELS):
            index = 0
        return _MODE_LABELS[index][1]

    def _active_connection_index(self):
        mode = self.config.get("connection_mode")
        return next((i for i, (_, value) in enumerate(_MODE_LABELS) if value == mode), 0)

    def _update_connection_tab_labels(self):
        if not hasattr(self, "connection_tabs"):
            return

        active_index = self._active_connection_index()

        def active_label(index, text):
            if index == active_index:
                return f"Active · {text}"
            return text

        provider_label = self.provider_combo.currentText() if hasattr(self, "provider_combo") else ""
        api_model = self.model_picker.currentText().strip() if hasattr(self, "model_picker") else ""
        api_base_url = (
            self.api_base_url_edit.text().strip() if hasattr(self, "api_base_url_edit") else ""
        )
        api_text = "API key"
        self.connection_tabs.setTabText(0, active_label(0, api_text))
        self.connection_tabs.setTabToolTip(
            0,
            "\n".join(
                part for part in (active_label(0, api_text), provider_label, api_model, api_base_url)
                if part
            ),
        )

        custom_format = (
            self.custom_format_combo.currentText() if hasattr(self, "custom_format_combo") else ""
        )
        custom_model = (
            self.custom_model_picker.currentText().strip()
            if hasattr(self, "custom_model_picker") else ""
        )
        custom_url = self.custom_url_edit.text().strip() if hasattr(self, "custom_url_edit") else ""
        custom_text = "Custom"
        self.connection_tabs.setTabText(1, active_label(1, custom_text))
        self.connection_tabs.setTabToolTip(
            1,
            "\n".join(
                part for part in (active_label(1, custom_text), custom_format, custom_model, custom_url)
                if part
            ),
        )

        agent_label = self.cli_agent_combo.currentText() if hasattr(self, "cli_agent_combo") else ""
        cli_path = self.cli_path_edit.text().strip() if hasattr(self, "cli_path_edit") else ""
        sub_text = "Subscription"
        self.connection_tabs.setTabText(2, active_label(2, sub_text))
        self.connection_tabs.setTabToolTip(
            2,
            "\n".join(part for part in (active_label(2, sub_text), agent_label, cli_path) if part),
        )

    @staticmethod
    def _panel_form(w):
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(0, 4, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return form

    def _model_group(self, picker_attr):
        """Hidden-until-connected widget containing the model picker."""
        group = QWidget()
        group.setStyleSheet("background: transparent;")
        mg = QFormLayout(group)
        mg.setSpacing(6)
        mg.setContentsMargins(0, 2, 0, 0)
        mg.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        picker = _ModelPickerWidget("Select or type a model name")
        picker.modelChanged.connect(self._update_connection_tab_labels)
        setattr(self, picker_attr, picker)
        mg.addRow(_lbl("Model:"), picker)

        hint = _lbl(
            "Select from the list or type a custom model name, then press Enter.",
            color=_TEXT_3, size=9, italic=True,
        )
        hint.setWordWrap(True)
        mg.addRow(hint)
        group.setVisible(False)
        return group

    def _test_row(self, btn_attr, status_attr, mode):
        row = QHBoxLayout()
        row.setSpacing(8)
        btn = _ghost_btn("Test connection")
        btn.clicked.connect(lambda: self._test_connection(mode))
        setattr(self, btn_attr, btn)
        row.addWidget(btn, 0)
        status = _lbl("", color=_TEXT_3, size=9)
        status.setWordWrap(True)
        setattr(self, status_attr, status)
        row.addWidget(status, 1)
        return row

    def _api_key_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(10)

        form_w = QWidget()
        form_w.setStyleSheet("background: transparent;")
        form = QFormLayout(form_w)
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.provider_combo = _cmb(QComboBox())
        for p in providers.all_providers():
            self.provider_combo.addItem(p["label"], p["id"])
        form.addRow(_lbl("Provider:"), self.provider_combo)

        self.api_base_url_edit = _inp(QLineEdit())
        self.api_base_url_edit.setPlaceholderText("Provider API base URL")
        self.api_base_url_edit.textChanged.connect(self._update_connection_tab_labels)
        form.addRow(_lbl("Base URL:"), self.api_base_url_edit)

        self.api_key_edit = _inp(QLineEdit())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here")
        form.addRow(_lbl("API key:"), self.api_key_edit)
        col.addWidget(form_w)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

        col.addLayout(
            self._test_row("api_test_btn", "api_status", config_mod.MODE_API_KEY)
        )
        self.api_model_group = self._model_group("model_picker")
        col.addWidget(self.api_model_group)
        return w

    def _custom_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(10)

        form_w = QWidget()
        form_w.setStyleSheet("background: transparent;")
        form = QFormLayout(form_w)
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.custom_url_edit = _inp(QLineEdit())
        self.custom_url_edit.setPlaceholderText("https://api.example.com")
        self.custom_url_edit.textChanged.connect(self._update_connection_tab_labels)
        form.addRow(_lbl("Base URL:"), self.custom_url_edit)

        self.custom_key_edit = _inp(QLineEdit())
        self.custom_key_edit.setEchoMode(QLineEdit.Password)
        self.custom_key_edit.setPlaceholderText("API key for this endpoint")
        form.addRow(_lbl("API key:"), self.custom_key_edit)

        self.custom_format_combo = _cmb(QComboBox())
        for label, value in _FORMAT_LABELS:
            self.custom_format_combo.addItem(label, value)
        self.custom_format_combo.currentIndexChanged.connect(self._update_connection_tab_labels)
        self.custom_format_combo.currentIndexChanged.connect(
            lambda _i: self._reset_model_group(config_mod.MODE_CUSTOM)
        )
        form.addRow(_lbl("Wire format:"), self.custom_format_combo)
        col.addWidget(form_w)

        col.addLayout(
            self._test_row("custom_test_btn", "custom_status", config_mod.MODE_CUSTOM)
        )
        self.custom_model_group = self._model_group("custom_model_picker")
        col.addWidget(self.custom_model_group)
        return w

    def _subscription_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        form = self._panel_form(w)

        self.cli_agent_combo = _cmb(QComboBox())
        for slug, label in _CLI_AGENTS:
            self.cli_agent_combo.addItem(label, slug)
        self.cli_agent_combo.currentIndexChanged.connect(self._on_cli_agent_changed)
        self.cli_agent_combo.currentIndexChanged.connect(self._update_connection_tab_labels)
        form.addRow(_lbl("Agent:"), self.cli_agent_combo)

        login_row = QHBoxLayout()
        login_row.setSpacing(6)
        self.login_status = _lbl("Checking…", color=_TEXT_3)
        login_row.addWidget(self.login_status, 1)
        self.login_browser_btn = _ghost_btn("Login with Browser")
        self.login_browser_btn.clicked.connect(self._login_browser)
        login_row.addWidget(self.login_browser_btn)
        form.addRow(_lbl("Login:"), login_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.cli_path_edit = _inp(QLineEdit())
        self.cli_path_edit.setPlaceholderText("Auto-detect on PATH (leave empty)")
        self.cli_path_edit.editingFinished.connect(self._update_login_status)
        self.cli_path_edit.textChanged.connect(self._update_connection_tab_labels)
        path_row.addWidget(self.cli_path_edit, 1)
        browse_cli = _ghost_btn("Browse…")
        browse_cli.clicked.connect(self._browse_cli)
        path_row.addWidget(browse_cli)
        form.addRow(_lbl("Binary path:"), path_row)

        self.sub_status = _lbl("Uses the agent's existing login.", color=_TEXT_3, size=9, italic=True)
        form.addRow(self.sub_status)
        return w

    # ── slots ─────────────────────────────────────────────────────────────────
    def _on_provider_changed(self, index):
        pid = self.provider_combo.itemData(index)
        p = providers.get_provider(pid)
        if p:
            self.model_picker.setCurrentText(p["default_model"])
            self.api_base_url_edit.setText(p["base_url"])
            env = p.get("key_env", "")
            self.api_key_edit.setPlaceholderText(
                f"Paste your key (or set {env})" if env else "Paste your key"
            )
        # Changing provider invalidates any prior successful test.
        self._reset_model_group(config_mod.MODE_API_KEY)
        self._update_connection_tab_labels()

    # ── connection test / model discovery ───────────────────────────────────────
    def _panel_widgets(self, mode):
        """Return (status_label, test_btn, model_picker, model_group) for a mode."""
        if mode == config_mod.MODE_CUSTOM:
            return (self.custom_status, self.custom_test_btn,
                    self.custom_model_picker, self.custom_model_group)
        return (self.api_status, self.api_test_btn,
                self.model_picker, self.api_model_group)

    @staticmethod
    def _set_status(label, text, color):
        label.setText(text)
        label.setStyleSheet(f"color: {color}; background: transparent;")

    def _reset_model_group(self, mode):
        """Hide the model dropdown and clear status — forces a re-test."""
        status, _btn, _combo, group = self._panel_widgets(mode)
        group.setVisible(False)
        self._set_status(status, "", _TEXT_3)

    def _fill_models(self, picker, models):
        """Push a fresh model list into the picker, keeping the current selection."""
        picker.setModels(models, keep_current=True)

    def _connection_params(self, mode):
        """Resolve (wire_format, base_url, api_key) for the given mode's form."""
        if mode == config_mod.MODE_CUSTOM:
            fmt = self.custom_format_combo.currentData() or "openai"
            base_url = self.custom_url_edit.text().strip()
            key = self.custom_key_edit.text().strip()
            return fmt, base_url, key
        pid = self.provider_combo.currentData()
        p = providers.get_provider(pid) or {}
        fmt = p.get("format", "openai")
        base_url = self.api_base_url_edit.text().strip() or p.get("base_url", "")
        key = self.api_key_edit.text().strip()
        if not key and p.get("key_env"):
            import os
            key = os.environ.get(p["key_env"], "")
        return fmt, base_url, key

    def _test_connection(self, mode):
        status, btn, _combo, _group = self._panel_widgets(mode)
        fmt, base_url, key = self._connection_params(mode)

        if mode == config_mod.MODE_CUSTOM and not base_url:
            self._set_status(status, "Enter a base URL first.", _DANGER)
            return

        def fetch(fmt=fmt, base_url=base_url, key=key):
            if fmt == "anthropic":
                from ..backends.anthropic_http import AnthropicHttpClient
                client = AnthropicHttpClient(
                    api_key=key or None, base_url=base_url or None
                )
            else:
                from ..backends.openai_http import OpenAIHttpClient
                client = OpenAIHttpClient(
                    api_key=key or None, base_url=base_url or None
                )
            return client.list_models()

        self._set_status(status, "Checking…", _TEXT_3)
        btn.setEnabled(False)
        btn.setText("Checking…")

        worker = _ModelFetchWorker(fetch, self)
        self._fetch_worker = worker  # keep a reference so it isn't GC'd
        worker.done.connect(
            lambda models, err, m=mode: self._on_models_fetched(m, models, err)
        )
        worker.start()

    def _on_models_fetched(self, mode, models, err):
        status, btn, picker, group = self._panel_widgets(mode)
        btn.setEnabled(True)
        btn.setText("Test connection")

        if err:
            self._set_status(status, f"Failed — {err}", _DANGER)
            return

        count = len(models)
        if count:
            self._set_status(
                status,
                f"Connected · {count} model{'s' if count != 1 else ''} available",
                _SUCCESS,
            )
            # Mark the currently saved model as "active" inside the picker
            saved = self.config.get("model") or ""
            picker.setActive(saved)
            self._fill_models(picker, models)
        else:
            self._set_status(
                status,
                "Connected · no models listed — type a model name below",
                _SUCCESS,
            )
        group.setVisible(True)
        self._update_connection_tab_labels()

    def _browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select agent CLI binary")
        if path:
            self.cli_path_edit.setText(path)
            self._update_login_status()

    def _on_cli_agent_changed(self, _index):
        self._update_login_status()

    def _update_login_status(self):
        from ..backends.cli_backend import _resolve_binary, CliToolBackend

        tool = self.cli_agent_combo.currentData()
        path = self.cli_path_edit.text().strip()
        binary = _resolve_binary(tool, path)

        if not binary:
            self.login_status.setText("Binary not found")
            self.login_status.setStyleSheet(f"color: {_DANGER}; background: transparent;")
            self.login_browser_btn.setEnabled(False)
            return

        self.login_browser_btn.setEnabled(True)
        self.login_status.setText("Checking…")
        self.login_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")

        backend = CliToolBackend(self.config, lambda: None)
        backend.tool = tool
        backend.binary = binary
        try:
            logged_in = backend.check_login()
        except Exception:
            logged_in = False

        if logged_in:
            self.login_status.setText("Logged in")
            self.login_status.setStyleSheet(f"color: {_SUCCESS}; background: transparent;")
        else:
            self.login_status.setText("Not logged in")
            self.login_status.setStyleSheet(f"color: {_DANGER}; background: transparent;")

    def _login_browser(self):
        from ..backends.cli_backend import _resolve_binary, CliToolBackend

        tool = self.cli_agent_combo.currentData()
        path = self.cli_path_edit.text().strip()
        binary = _resolve_binary(tool, path)
        if not binary:
            QMessageBox.warning(
                self, "Binary not found",
                f"Could not find the '{tool}' binary.\n"
                "Set its path or make sure it is on PATH.",
            )
            return

        backend = CliToolBackend(self.config, lambda: None)
        backend.tool = tool
        backend.binary = binary

        ok = backend.login_browser()
        if ok:
            QMessageBox.information(
                self, "Browser login started",
                "A browser window should have opened.\n"
                "Complete the authentication, then check the status here.",
            )
            QTimer.singleShot(4000, self._update_login_status)
        else:
            QMessageBox.warning(self, "Login failed", "Could not start the browser login flow.")

    # ── load / save ───────────────────────────────────────────────────────────
    def _load(self):
        mode = self.config.get("connection_mode")
        index = next((i for i, (_, m) in enumerate(_MODE_LABELS) if m == mode), 0)
        self.connection_tabs.setCurrentIndex(index)
        self.stack.setCurrentIndex(index)

        pid = self.config.get("provider")
        idx = self.provider_combo.findData(pid)
        self.provider_combo.setCurrentIndex(max(0, idx))
        p = providers.get_provider(self.provider_combo.currentData())
        self.api_base_url_edit.setText(
            self.config.get("api_base_url") or (p["base_url"] if p else "")
        )
        self.api_key_edit.setText(self.config.get("api_key") or "")

        self.custom_url_edit.setText(self.config.get("custom_base_url") or "")
        self.custom_key_edit.setText(self.config.get("custom_api_key") or "")
        cfmt = self.config.get("custom_format")
        fidx = next((i for i, (_, v) in enumerate(_FORMAT_LABELS) if v == cfmt), 0)
        self.custom_format_combo.setCurrentIndex(fidx)

        cli = self.config.get("cli_tool")
        cidx = next((i for i, (s, _) in enumerate(_CLI_AGENTS) if s == cli), 0)
        self.cli_agent_combo.setCurrentIndex(cidx)
        self.cli_path_edit.setText(self.config.get("cli_path") or "")
        self._update_login_status()

        # Pre-fill saved models. If one already exists the user has connected
        # before, so reveal the dropdown without forcing a re-test (Test
        # connection refreshes the available list).
        api_model = self.config.get("model") or ""
        self.model_picker.setCurrentText(api_model)
        self.model_picker.setActive(api_model)   # badge the saved model as active
        if api_model:
            self.api_model_group.setVisible(True)
            self._set_status(
                self.api_status, "Test connection to refresh the model list.", _TEXT_3
            )

        custom_model = self.config.get("custom_model") or ""
        self.custom_model_picker.setCurrentText(custom_model)
        self.custom_model_picker.setActive(custom_model)
        if custom_model:
            self.custom_model_group.setVisible(True)
            self._set_status(
                self.custom_status, "Test connection to refresh the model list.", _TEXT_3
            )

        to_val = self.config.get("main_thread_timeout")
        self.timeout_edit.setText("" if to_val is None else str(to_val))
        pt_val = self.config.get("processing_timeout")
        self.proc_timeout_edit.setText("" if pt_val is None else str(pt_val))
        pi_val = self.config.get("mcp_poll_interval")
        self.poll_interval_edit.setText("" if pi_val is None else str(pi_val))
        self._update_connection_tab_labels()

    def _save_and_accept(self):
        mode = self._current_mode()

        if mode == config_mod.MODE_API_KEY:
            key = self.api_key_edit.text().strip()
            pid = self.provider_combo.currentData()
            provider_obj = providers.get_provider(pid)
            requires_key = provider_obj is None or provider_obj.get("id") != "ollama"
            if requires_key and not key:
                QMessageBox.warning(self, "API key required",
                                    "Please enter an API key for the selected provider.")
                return
        elif mode == config_mod.MODE_CUSTOM:
            if not self.custom_url_edit.text().strip():
                QMessageBox.warning(self, "Base URL required",
                                    "Please enter a base URL for the custom endpoint.")
                return

        self.config.set("connection_mode", mode)
        if mode == config_mod.MODE_API_KEY:
            model = self.model_picker.currentText().strip()
            provider_obj = providers.get_provider(self.provider_combo.currentData())
            if not model and provider_obj:
                model = provider_obj.get("default_model", "")
            self.config.set("provider", self.provider_combo.currentData())
            self.config.set("api_key", self.api_key_edit.text().strip())
            self.config.set("model", model)
            self.config.set("api_base_url", self.api_base_url_edit.text().strip())
        elif mode == config_mod.MODE_CUSTOM:
            custom_model = self.custom_model_picker.currentText().strip()
            self.config.set("provider", "custom")
            self.config.set("custom_base_url", self.custom_url_edit.text().strip())
            self.config.set("custom_api_key", self.custom_key_edit.text().strip())
            self.config.set("custom_format", self.custom_format_combo.currentData())
            self.config.set("custom_model", custom_model)
            self.config.set("model", custom_model)
        elif mode == config_mod.MODE_SUBSCRIPTION:
            self.config.set("cli_tool", self.cli_agent_combo.currentData())
            self.config.set("cli_path", self.cli_path_edit.text().strip())

        try:
            self.config.set("main_thread_timeout",
                            float(self.timeout_edit.text().strip() or 60))
        except ValueError:
            self.config.set("main_thread_timeout", 60.0)
        try:
            self.config.set("processing_timeout",
                            float(self.proc_timeout_edit.text().strip() or 0))
        except ValueError:
            self.config.set("processing_timeout", 0.0)
        try:
            self.config.set("mcp_poll_interval",
                            float(self.poll_interval_edit.text().strip() or 0.5))
        except ValueError:
            self.config.set("mcp_poll_interval", 0.5)

        self.accept()
