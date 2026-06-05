"""Settings dialog — styled to match the AskUser/permission card aesthetic.

Three connection modes:
  1. API key       — pick a built-in provider or custom endpoint.
  2. Custom endpoint — any OpenAI- or Anthropic-compatible server.
  3. CLI Agent     — delegate to an installed local agent CLI.
"""

import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QPalette
from qgis.PyQt.QtWidgets import (
    QApplication,
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
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from ..backends.cli_backend import CLI_AGENT_CATALOG
from ..backends import providers
from .theme import (
    DIALOG_SURFACE as _SURFACE,
    DIALOG_SURFACE_2 as _SURFACE_2,
    DIALOG_SURFACE_HOV as _SURFACE_HOV,
    DIALOG_INPUT_BG as _INPUT_BG,
    DIALOG_BORDER as _BORDER,
    DIALOG_BORDER_SOFT as _BORDER_SOFT,
    DIALOG_TEXT as _TEXT,
    DIALOG_TEXT_2 as _TEXT_2,
    DIALOG_TEXT_3 as _TEXT_3,
    DIALOG_ACCENT as _ACCENT,
    DIALOG_ACCENT_HOV as _ACCENT_HOV,
    DIALOG_WARN as _WARN,
    DIALOG_SUCCESS as _SUCCESS,
    DIALOG_DANGER as _DANGER,
)

# ── Mode / agent constants ────────────────────────────────────────────────────
_MODE_LABELS = [
    ("API key", config_mod.MODE_API_KEY),
    ("Custom endpoint", config_mod.MODE_CUSTOM),
    ("CLI Agent", config_mod.MODE_CLI_TOOL),
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

_TOOLTIP_SS = (
    f"QToolTip {{"
    f"  background-color: {_SURFACE_2}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER}; border-radius: 4px;"
    f"  padding: 6px 8px;"
    f"}}"
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

_LIST_SS = (
    f"QListWidget {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER_SOFT}; border-radius: 6px;"
    f"  outline: none; padding: 4px;"
    f"}}"
    f"QListWidget::item {{"
    f"  padding: 7px 8px; border-radius: 4px;"
    f"}}"
    f"QListWidget::item:selected {{"
    f"  background: {_BORDER}; color: {_TEXT};"
    f"}}"
    f"QListWidget::item:hover {{"
    f"  background: {_SURFACE_HOV};"
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


def _install_tooltip_palette():
    palette = QToolTip.palette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        palette.setColor(group, QPalette.ToolTipBase, QColor(_SURFACE_2))
        palette.setColor(group, QPalette.ToolTipText, QColor(_TEXT))
    QToolTip.setPalette(palette)
    QToolTip.setFont(_mono(9))
    app = QApplication.instance()
    if app is not None and "QToolTip" not in app.styleSheet():
        existing = app.styleSheet().rstrip()
        app.setStyleSheet((existing + "\n" if existing else "") + _TOOLTIP_SS)


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
        popup = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
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
        lst.itemPressed.connect(self._pick_item)
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
        _install_tooltip_palette()
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
        self.stack.addWidget(self._cli_agent_panel())
        cb.addWidget(self.stack)
        body.addWidget(conn)

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
        return getattr(self, "_pending_connection_mode", self.config.get("connection_mode"))

    def _set_active_connection_mode(self, mode):
        if mode == config_mod.MODE_SUBSCRIPTION:
            mode = config_mod.MODE_CLI_TOOL
        self._pending_connection_mode = mode
        self._update_connection_tab_labels()

    def _active_connection_index(self):
        mode = self._current_mode()
        if mode == config_mod.MODE_SUBSCRIPTION:
            mode = config_mod.MODE_CLI_TOOL
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

        agent_label = self.cli_agent_name.text() if hasattr(self, "cli_agent_name") else ""
        cli_path = self.cli_path_edit.text().strip() if hasattr(self, "cli_path_edit") else ""
        cli_text = "CLI Agent"
        self.connection_tabs.setTabText(2, active_label(2, cli_text))
        self.connection_tabs.setTabToolTip(
            2,
            "\n".join(part for part in (active_label(2, cli_text), agent_label, cli_path) if part),
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

    def _test_row(self, btn_attr, status_attr, mode, use_btn_attr=None):
        row = QHBoxLayout()
        row.setSpacing(8)
        btn = _ghost_btn("Test connection")
        btn.clicked.connect(lambda: self._test_connection(mode))
        setattr(self, btn_attr, btn)
        row.addWidget(btn, 0)
        if use_btn_attr:
            use_btn = _ghost_btn("Use")
            use_btn.clicked.connect(lambda _checked=False, m=mode: self._use_connection_mode(m))
            setattr(self, use_btn_attr, use_btn)
            row.addWidget(use_btn, 0)
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
            self._test_row(
                "api_test_btn", "api_status", config_mod.MODE_API_KEY,
                use_btn_attr="api_use_btn",
            )
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
            self._test_row(
                "custom_test_btn", "custom_status", config_mod.MODE_CUSTOM,
                use_btn_attr="custom_use_btn",
            )
        )
        self.custom_model_group = self._model_group("custom_model_picker")
        col.addWidget(self.custom_model_group)
        return w

    def _cli_agent_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 4, 0, 4)
        col.setSpacing(10)

        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.cli_scan_btn = _ghost_btn("Scan")
        self.cli_scan_btn.clicked.connect(self._scan_cli_agents)
        scan_row.addWidget(self.cli_scan_btn, 0)
        self.cli_rescan_btn = _ghost_btn("Rescan")
        self.cli_rescan_btn.clicked.connect(self._scan_cli_agents)
        scan_row.addWidget(self.cli_rescan_btn, 0)
        self.cli_scan_status = _lbl("", color=_TEXT_3, size=9)
        scan_row.addWidget(self.cli_scan_status, 1)
        col.addLayout(scan_row)

        self.cli_agent_list = QListWidget()
        self.cli_agent_list.setStyleSheet(_LIST_SS)
        self.cli_agent_list.setMinimumHeight(190)
        self.cli_agent_list.currentItemChanged.connect(self._on_cli_agent_selected)
        col.addWidget(self.cli_agent_list)

        details = QWidget()
        details.setStyleSheet("background: transparent;")
        form = self._panel_form(details)

        self.cli_agent_name = _lbl("Select an agent", color=_TEXT, size=10)
        form.addRow(_lbl("Selected:"), self.cli_agent_name)

        self.cli_agent_credentials = _lbl("", color=_TEXT_3, size=9)
        self.cli_agent_credentials.setWordWrap(True)
        form.addRow(_lbl("Credentials:"), self.cli_agent_credentials)

        self.cli_agent_warning = _lbl("", color=_WARN, size=9)
        self.cli_agent_warning.setWordWrap(True)
        self.cli_agent_warning.setVisible(False)
        form.addRow(self.cli_agent_warning)

        self.cli_auth_status = _lbl("Auth not checked", color=_TEXT_3, size=9)
        self.cli_auth_status.setWordWrap(True)
        form.addRow(_lbl("Auth:"), self.cli_auth_status)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self._cli_path_is_override = False
        self._syncing_cli_path = False
        self.cli_path_edit = _inp(QLineEdit())
        self.cli_path_edit.setPlaceholderText("Auto-detect on PATH (leave empty)")
        self.cli_path_edit.editingFinished.connect(self._scan_cli_agents)
        self.cli_path_edit.textEdited.connect(self._mark_cli_path_override)
        self.cli_path_edit.textChanged.connect(self._update_connection_tab_labels)
        path_row.addWidget(self.cli_path_edit, 1)
        browse_cli = _ghost_btn("Browse…")
        browse_cli.clicked.connect(self._browse_cli)
        path_row.addWidget(browse_cli)
        form.addRow(_lbl("Command path:"), path_row)

        self.cli_resolved_path = _lbl("", color=_TEXT_3, size=9)
        self.cli_resolved_path.setWordWrap(True)
        self.cli_resolved_path.setVisible(False)
        form.addRow(_lbl("Resolved:"), self.cli_resolved_path)

        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        self.cli_test_btn = _ghost_btn("Test binary")
        self.cli_test_btn.clicked.connect(self._test_cli_agent)
        test_row.addWidget(self.cli_test_btn, 0)
        self.cli_auth_btn = _ghost_btn("Check auth")
        self.cli_auth_btn.clicked.connect(self._check_cli_auth)
        test_row.addWidget(self.cli_auth_btn, 0)
        self.cli_use_btn = _ghost_btn("Use")
        self.cli_use_btn.clicked.connect(self._use_cli_agent)
        test_row.addWidget(self.cli_use_btn, 0)
        self.cli_test_status = _lbl("", color=_TEXT_3, size=9)
        self.cli_test_status.setWordWrap(True)
        test_row.addWidget(self.cli_test_status, 1)
        form.addRow(_lbl("Agent:"), test_row)

        self.sub_status = _lbl(
            "Delegates to the selected local CLI. AgenticGIS never reads OAuth tokens.",
            color=_TEXT_3, size=9, italic=True,
        )
        form.addRow(self.sub_status)
        col.addWidget(details)
        return w

    # ── slots ─────────────────────────────────────────────────────────────────
    def _use_connection_mode(self, mode):
        self._set_active_connection_mode(mode)
        if mode == config_mod.MODE_API_KEY:
            self._set_status(self.api_status, "Using API key connection", _SUCCESS)
        elif mode == config_mod.MODE_CUSTOM:
            self._set_status(self.custom_status, "Using custom endpoint", _SUCCESS)

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
            self._cli_path_is_override = True
            self.cli_path_edit.setText(path)
            self._scan_cli_agents()

    def _mark_cli_path_override(self, _text):
        if getattr(self, "_syncing_cli_path", False):
            return
        self._cli_path_is_override = True

    def _selected_cli_agent_id(self):
        item = self.cli_agent_list.currentItem() if hasattr(self, "cli_agent_list") else None
        if item:
            return item.data(Qt.UserRole)
        return self.config.get("cli_tool") or "claude"

    def _cli_path_overrides(self):
        selected = self._selected_cli_agent_id()
        path = self.cli_path_edit.text().strip() if hasattr(self, "cli_path_edit") else ""
        if selected and path and getattr(self, "_cli_path_is_override", False):
            return {selected: path}
        return {}

    def _cli_scan_row(self, agent_id):
        for row in getattr(self, "_cli_scan_rows", []):
            if row.get("id") == agent_id:
                return row
        return None

    def _select_cli_agent(self, agent_id):
        for i in range(self.cli_agent_list.count()):
            item = self.cli_agent_list.item(i)
            if item.data(Qt.UserRole) == agent_id:
                self.cli_agent_list.setCurrentRow(i)
                return
        if self.cli_agent_list.count():
            self.cli_agent_list.setCurrentRow(0)

    def _scan_cli_agents(self):
        from ..backends.cli_backend import scan_cli_agents

        selected = self._selected_cli_agent_id()
        self._cli_scan_rows = scan_cli_agents(self._cli_path_overrides())
        self._cli_scan_performed = True
        self._fill_cli_agent_list(selected, scanned=True)

    def _fill_cli_agent_list(self, selected, scanned):
        self.cli_agent_list.blockSignals(True)
        self.cli_agent_list.clear()
        found = 0
        for row in self._cli_scan_rows:
            found += 1 if row.get("installed") else 0
            if row.get("installed") and (scanned or row.get("_selected_probe")):
                status = "found"
            else:
                status = "missing" if scanned else "not scanned"
            active = "  ·  active" if row.get("id") == selected else ""
            item = QListWidgetItem(f"{row['label']}  ·  {status}{active}")
            item.setData(Qt.UserRole, row["id"])
            item.setForeground(QColor(_TEXT if row.get("installed") or not scanned else _TEXT_3))
            self.cli_agent_list.addItem(item)
        self.cli_agent_list.blockSignals(False)
        self._select_cli_agent(selected)
        if scanned:
            self.cli_scan_status.setText(f"{found} of {len(self._cli_scan_rows)} agents found")
        else:
            self.cli_scan_status.setText("Open this tab or click Scan to detect installed CLIs.")
        self.cli_scan_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        self._update_cli_agent_detail()

    def _seed_cli_agents(self):
        from ..backends.cli_backend import _resolve_binary

        selected = self.config.get("cli_tool") or "claude"
        self._cli_scan_performed = False
        selected_path = _resolve_binary(
            selected,
            self.config.get("cli_path") or "",
        )
        self._cli_scan_rows = [
            dict(
                agent,
                path=selected_path if agent["id"] == selected and selected_path else "",
                real_path=(
                    os.path.realpath(selected_path)
                    if agent["id"] == selected and selected_path else ""
                ),
                installed=bool(agent["id"] == selected and selected_path),
                _catalog_index=index,
                _selected_probe=agent["id"] == selected,
            )
            for index, agent in enumerate(CLI_AGENT_CATALOG)
        ]
        self._fill_cli_agent_list(selected, scanned=False)

    def _on_cli_agent_selected(self, _current, _previous=None):
        self.cli_test_status.setText("")
        self.cli_auth_status.setText("Auth not checked")
        self.cli_auth_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        self._cli_path_is_override = False
        self._update_cli_agent_detail()
        self._update_connection_tab_labels()

    def _update_cli_agent_detail(self):
        agent_id = self._selected_cli_agent_id()
        row = self._cli_scan_row(agent_id)
        if not row:
            row = next((dict(agent) for agent in CLI_AGENT_CATALOG if agent["id"] == agent_id), None)
        if not row:
            return

        self.cli_agent_name.setText(row["label"])
        self.cli_agent_credentials.setText(row.get("credential_style", ""))
        warning = row.get("warning", "")
        self.cli_agent_warning.setText(warning)
        self.cli_agent_warning.setVisible(bool(warning))

        scanned = getattr(self, "_cli_scan_performed", False)
        selected_probe = bool(row.get("_selected_probe"))
        detected = row.get("path", "") if (scanned or selected_probe) else ""
        real_path = row.get("real_path", "")
        self._syncing_cli_path = True
        try:
            if not self._cli_path_is_override:
                self.cli_path_edit.setText(detected)
            self.cli_path_edit.setPlaceholderText("Auto-detect on PATH (leave empty)")
        finally:
            self._syncing_cli_path = False

        show_resolved = bool(real_path and detected and real_path != detected)
        self.cli_resolved_path.setText(real_path if show_resolved else "")
        self.cli_resolved_path.setVisible(show_resolved)

        installed = bool(row.get("installed")) if (scanned or selected_probe) else False
        has_path = bool(self.cli_path_edit.text().strip())
        self.cli_test_btn.setEnabled(installed or has_path)
        self.cli_auth_btn.setEnabled(installed or has_path)
        self.cli_use_btn.setEnabled(True)

    def _selected_cli_backend(self):
        from ..backends.cli_backend import _resolve_binary, CliToolBackend

        agent_id = self._selected_cli_agent_id()
        path = self.cli_path_edit.text().strip() if self._cli_path_is_override else ""
        binary = _resolve_binary(agent_id, path)
        if not binary:
            return None
        backend = CliToolBackend(self.config, None, None)
        backend.tool = agent_id
        backend.binary = binary
        return backend

    def _test_cli_agent(self):
        backend = self._selected_cli_backend()
        if backend is None:
            self.cli_test_status.setText("Binary not found")
            self.cli_test_status.setStyleSheet(f"color: {_DANGER}; background: transparent;")
            return

        self.cli_test_status.setText("Testing…")
        self.cli_test_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        ok, detail = backend.test_cli()
        color = _SUCCESS if ok else _DANGER
        prefix = "OK" if ok else "Failed"
        self.cli_test_status.setText(f"{prefix} · {detail}")
        self.cli_test_status.setStyleSheet(f"color: {color}; background: transparent;")

    def _check_cli_auth(self):
        backend = self._selected_cli_backend()
        if backend is None:
            self.cli_auth_status.setText("Binary not found")
            self.cli_auth_status.setStyleSheet(f"color: {_DANGER}; background: transparent;")
            return

        self.cli_auth_status.setText("Checking…")
        self.cli_auth_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        state, detail = backend.auth_status()
        if state == "ready":
            color = _SUCCESS
            text = f"Ready · {detail}"
        elif state == "login_required":
            color = _WARN
            text = f"Login required · {detail}"
        elif state == "missing":
            color = _DANGER
            text = detail
        else:
            color = _TEXT_3
            text = f"Auth check unavailable · {detail}"
        self.cli_auth_status.setText(text)
        self.cli_auth_status.setStyleSheet(f"color: {color}; background: transparent;")

    def _use_cli_agent(self):
        self.connection_tabs.setCurrentIndex(2)
        self._set_active_connection_mode(config_mod.MODE_CLI_TOOL)
        agent_id = self._selected_cli_agent_id()
        row = self._cli_scan_row(agent_id)
        label = row.get("label", agent_id) if row else agent_id
        self.cli_test_status.setText(f"Using {label}")
        self.cli_test_status.setStyleSheet(f"color: {_SUCCESS}; background: transparent;")
        self._update_connection_tab_labels()

    # ── load / save ───────────────────────────────────────────────────────────
    def _load(self):
        mode = self.config.get("connection_mode")
        if mode == config_mod.MODE_SUBSCRIPTION:
            mode = config_mod.MODE_CLI_TOOL
        self._pending_connection_mode = mode
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

        saved_cli_path = self.config.get("cli_path") or ""
        self._cli_path_is_override = bool(saved_cli_path)
        self._syncing_cli_path = True
        try:
            self.cli_path_edit.setText(saved_cli_path)
        finally:
            self._syncing_cli_path = False
        self._seed_cli_agents()
        self._select_cli_agent(self.config.get("cli_tool") or "claude")

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
        elif mode == config_mod.MODE_CLI_TOOL:
            self.config.set("cli_tool", self._selected_cli_agent_id())
            self.config.set(
                "cli_path",
                self.cli_path_edit.text().strip() if self._cli_path_is_override else "",
            )

        self.accept()
