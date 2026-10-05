"""Settings dialog — styled to match the AskUser/permission card aesthetic.

Three connection modes:
  1. API key       — pick a built-in provider or custom endpoint.
  2. Custom endpoint — any OpenAI- or Anthropic-compatible server.
  3. CLI Agent     — delegate to an installed local agent CLI.
"""

import os

from qgis.PyQt.QtCore import QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
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
from .agent_logos import AGENT_LOGOS
from ..backends.cli_backend import CLI_AGENT_CATALOG, _agent_id_for_binary_path
from ..backends import providers
from .theme import (
    clamp_font_scale,
    set_font_scale,
    ui_font,
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
    DIALOG_BLUE as _BLUE,
    DOCK_TEXT_4 as _TEXT_4,
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


# ── Fonts ─────────────────────────────────────────────────────────────────────
def _mono(size=12, weight=None):
    """Interface font (the QGIS UI sans) at ``size`` px.

    Historical name: the dialog used JetBrains Mono everywhere; it now follows
    monocode and keeps monospace for code only.
    """
    return ui_font(size, weight)


# ── Stylesheets ───────────────────────────────────────────────────────────────
_DIALOG_SS = (
    f"QDialog {{ background: {_SURFACE}; }}"
    f"QWidget {{ color: {_TEXT}; }}"
    f"QScrollArea {{ background: transparent; border: none; }}"
    f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}"
    f"QScrollBar::handle:vertical {{ background: {_BORDER}; border-radius: 3px; min-height: 24px; }}"
    f"QScrollBar::handle:vertical:hover {{ background: {_TEXT_4}; }}"
    f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
)

_TOOLTIP_SS = (
    f"QToolTip {{"
    f"  background-color: {_SURFACE_HOV}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER}; border-radius: 6px; padding: 5px 8px;"
    f"}}"
)

_INPUT_SS = (
    f"QLineEdit {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER}; border-radius: 6px;"
    f"  padding: 4px 8px; min-height: 18px;"
    f"  selection-background-color: {_BLUE};"
    f"}}"
    f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
    f"QLineEdit:disabled {{ color: {_TEXT_3}; }}"
)

_COMBO_SS = (
    f"QComboBox {{"
    f"  background: {_INPUT_BG}; color: {_TEXT};"
    f"  border: 1px solid {_BORDER}; border-radius: 6px; padding: 4px 8px; min-height: 18px;"
    f"}}"
    f"QComboBox:hover {{ border-color: {_TEXT_4}; }}"
    f"QComboBox:focus {{ border-color: {_BLUE}; }}"
    f"QComboBox::drop-down {{ border: none; width: 18px; }}"
    f"QComboBox QAbstractItemView {{"
    f"  background: {_SURFACE_2}; color: {_TEXT}; border: 1px solid {_BORDER};"
    f"  selection-background-color: {_SURFACE_HOV}; outline: none; padding: 4px;"
    f"}}"
)

# Agent picker: a row of icon tiles instead of a text list.
_AGENT_TILES_SS = (
    f"QListWidget {{ background: transparent; border: none; outline: none; }}"
    f"QListWidget::item {{ color: {_TEXT_2}; border-radius: 8px; }}"
    f"QListWidget::item:hover {{ background: {_SURFACE_HOV}; }}"
    f"QListWidget::item:selected {{ background: {_BORDER}; color: {_TEXT}; }}"
)


class _TileList(QListWidget):
    """Wrapping icon grid whose height always fits its visible tiles (no scrollbars)."""

    def __init__(self, tile=QSize(92, 74), parent=None):
        super().__init__(parent)
        self._tile = tile
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(True)
        self.setIconSize(QSize(36, 36))
        self.setGridSize(tile)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def fit(self):
        shown = sum(1 for i in range(self.count()) if not self.item(i).isHidden())
        per_row = max(1, self.viewport().width() // self._tile.width())
        rows = max(1, -(-shown // per_row))
        self.setFixedHeight(rows * self._tile.height() + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


def _agent_logo_renderer(agent_id, installed):
    """QSvgRenderer for the agent's brand mark, or None (no logo / no QtSvg)."""
    entry = AGENT_LOGOS.get(agent_id)
    if not entry:
        return None
    try:
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtSvg import QSvgRenderer
    except ImportError:
        return None
    tint, svg = entry
    if tint:
        color = tint if installed else _TEXT_3
        if QColor(color).lightness() < 60:
            color = _TEXT  # near-black marks would vanish on the dark tile
        svg = svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    return renderer if renderer.isValid() else None


def _agent_icon(agent_id, label, in_use=False, installed=True, size=36):
    """Agent badge: brand mark (or two-letter monogram) on a disc, green ring when in use."""
    ratio = 2
    pix = QPixmap(size * ratio, size * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(QColor(0, 0, 0, 0))
    logo = _agent_logo_renderer(agent_id, installed)
    hue = sum(ord(c) * (i + 1) for i, c in enumerate(agent_id)) % 360
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(_SUCCESS) if in_use else QColor(0, 0, 0, 0))
    painter.setBrush(QColor(_SURFACE_HOV) if logo else QColor.fromHsl(hue, 70 if installed else 0, 70))
    inset = 2 if in_use else 1
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    if logo:
        pad = size * 0.25
        if not installed:
            painter.setOpacity(0.45)  # full-colour marks can't be tinted grey
        logo.render(painter, QRectF(pad, pad, size - 2 * pad, size - 2 * pad))
        painter.setOpacity(1.0)
    else:
        painter.setPen(QColor(_TEXT if installed else _TEXT_3))
        painter.setFont(_mono(12, QFont.Weight.DemiBold))
        letters = (label.split() or ["?"])[0][:2].title()
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letters)
    painter.end()
    return QIcon(pix)


# Segmented control: a pill track with the selected segment raised.
_TAB_SS = (
    f"QTabBar {{ background: transparent; }}"
    f"QTabBar::tab {{"
    f"  background: transparent; color: {_TEXT_3}; border: none;"
    f"  border-radius: 6px; padding: 5px 12px; margin: 2px;"
    f"}}"
    f"QTabBar::tab:hover:!selected {{ color: {_TEXT}; }}"
    f"QTabBar::tab:selected {{ background: {_BORDER}; color: {_TEXT}; }}"
)

_RAIL_SS = (
    f"QListWidget {{ background: transparent; border: none; outline: none; }}"
    f"QListWidget::item {{ color: {_TEXT_3}; padding: 6px 8px; border-radius: 6px; margin: 1px 0; }}"
    f"QListWidget::item:hover {{ background: {_SURFACE_2}; color: {_TEXT}; }}"
    f"QListWidget::item:selected {{ background: {_SURFACE_HOV}; color: {_TEXT}; }}"
)

_BTN_PRIMARY_SS = (
    f"QPushButton {{"
    f"  background: {_ACCENT}; color: {_SURFACE};"
    f"  border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600;"
    f"}}"
    f"QPushButton:hover {{ background: {_ACCENT_HOV}; }}"
    f"QPushButton:pressed {{ background: {_TEXT_2}; }}"
)

_BTN_SECONDARY_SS = (
    f"QPushButton {{"
    f"  background: transparent; color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER}; border-radius: 6px; padding: 6px 16px;"
    f"}}"
    f"QPushButton:hover {{ background: {_SURFACE_2}; color: {_TEXT}; }}"
)

_BTN_GHOST_SS = (
    f"QPushButton {{"
    f"  background: transparent; color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER}; border-radius: 6px; padding: 4px 10px;"
    f"}}"
    f"QPushButton:hover {{ background: {_SURFACE_HOV}; color: {_TEXT}; }}"
    f"QPushButton:disabled {{ color: {_TEXT_4}; border-color: {_BORDER_SOFT}; }}"
)


# ── Widget factories ──────────────────────────────────────────────────────────
def _inp(widget):
    widget.setFont(_mono(12))
    widget.setStyleSheet(_INPUT_SS)
    return widget


def _cmb(widget):
    widget.setFont(_mono(12))
    widget.setStyleSheet(_COMBO_SS)
    return widget


def _install_tooltip_palette():
    palette = QToolTip.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.ToolTipBase, QColor(_SURFACE_HOV))
        palette.setColor(group, QPalette.ColorRole.ToolTipText, QColor(_TEXT))
    QToolTip.setPalette(palette)
    QToolTip.setFont(_mono(11))
    app = QApplication.instance()
    if app is not None and "QToolTip" not in app.styleSheet():
        existing = app.styleSheet().rstrip()
        app.setStyleSheet((existing + "\n" if existing else "") + _TOOLTIP_SS)


def _plugin_version():
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata.txt")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


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
        f"  background: {_SURFACE_2}; border: 1px solid {_BORDER};"
        f"  border-radius: 10px;"
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
        self._btn.setFont(_mono(12))
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn.clicked.connect(self._toggle_popup)
        self._refresh_btn()
        lay.addWidget(self._btn)

    def _refresh_btn(self):
        text = self._selected
        is_active = bool(text and text == self._active)
        display = (text + "  ●") if is_active else (text or self._placeholder)
        color = _TEXT if text else _TEXT_3
        self._btn.setText(display)
        self._btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: {_INPUT_BG}; color: {color};"
            f"  border: 1px solid {_BORDER}; border-radius: 6px;"
            f"  padding: 4px 30px 4px 8px; text-align: left; min-height: 18px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {_BLUE}; }}"
            f"QPushButton::menu-indicator {{ width: 0; }}"
        )
        # Arrow overlay via a child label positioned at the right
        if not hasattr(self, "_arrow_lbl"):
            self._arrow_lbl = QLabel("▾", self)
            self._arrow_lbl.setFont(_mono(11))
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
        popup = QFrame(self.window(), Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        popup.setObjectName("ModelPopup")
        popup.setStyleSheet(self._POPUP_SS)
        popup.setFixedWidth(max(self._btn.width(), 300))

        vlay = QVBoxLayout(popup)
        vlay.setContentsMargins(0, 0, 0, 6)
        vlay.setSpacing(0)

        search = QLineEdit()
        search.setFont(_mono(12))
        search.setPlaceholderText("Search models or type a custom name…")
        vlay.addWidget(search)

        lst = QListWidget()
        lst.setFont(_mono(12))
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
            it.setFont(_mono(10, QFont.Weight.DemiBold))
            it.setForeground(QColor(_TEXT_3))
            it.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable
            return it

        # ── Active model pinned at top ────────────────────────────────────
        if self._active:
            if not q or q in self._active.lower():
                lst.addItem(_header("  ACTIVE"))
                it = QListWidgetItem(f"  {self._active}")
                it.setData(Qt.ItemDataRole.UserRole, self._active)
                it.setForeground(QColor(_BLUE))
                it.setFont(_mono(12, QFont.Weight.DemiBold))
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
                it.setData(Qt.ItemDataRole.UserRole, m)
                lst.addItem(it)

        # ── Custom entry hint when nothing matches ────────────────────────
        if q and not self._active_matches(q) and not filtered:
            lst.addItem(_header("  CUSTOM"))
            it = QListWidgetItem(f'  Use "{query.strip()}"')
            it.setData(Qt.ItemDataRole.UserRole, query.strip())
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
            if it.flags() & Qt.ItemFlag.ItemIsEnabled and it.data(Qt.ItemDataRole.UserRole):
                self._apply(it.data(Qt.ItemDataRole.UserRole))
                return
        # Fall back to raw typed text
        typed = self._popup._search.text().strip()
        if typed:
            self._apply(typed)

    def _pick_item(self, item):
        val = item.data(Qt.ItemDataRole.UserRole)
        if val:
            self._apply(val)

    def _apply(self, model_id):
        self._selected = model_id
        self._refresh_btn()
        self.modelChanged.emit(self._selected)
        self._close_popup()


# ── Background tasks ─────────────────────────────────────────────────────────
class _BgTask(QThread):
    """Runs ``fn()`` off the UI thread; emits ``done(result, error)``."""

    done = pyqtSignal(object, str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result, err = self._fn(), ""
        except Exception as exc:  # noqa: BLE001
            result, err = None, f"{type(exc).__name__}: {exc}"
        self.done.emit(result, err)


# Running tasks are held here, not by the dialog, so closing the dialog
# mid-scan can't destroy a QThread that is still running.
_LIVE_TASKS = set()
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _lbl(text, color=_TEXT_2, size=12, italic=False):
    w = QLabel(text)
    f = _mono(size)
    if italic:
        f.setItalic(True)
    w.setFont(f)
    w.setStyleSheet(f"color: {color}; background: transparent;")
    return w


def _ghost_btn(text):
    b = QPushButton(text)
    b.setFont(_mono(12))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(_BTN_GHOST_SS)
    return b


class _Toggle(QCheckBox):
    """Pill switch (monocode style) that keeps the QCheckBox API."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 20)

    def sizeHint(self):
        return QSize(36, 20)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_BLUE if self.isChecked() else _BORDER))
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(18 if self.isChecked() else 2, 2, 16, 16))
        p.end()


class _Card(QFrame):
    """Settings card: rounded surface holding rows split by hairlines.

    A row is ``label + description`` on the left and a control on the right;
    ``add_widget`` adds a full-width block (lists, notes).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.setStyleSheet(
            f"QFrame#SettingsCard {{ background: {_SURFACE_2}; border: 1px solid {_BORDER_SOFT};"
            f" border-radius: 12px; }}"
        )
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    def _new_row(self):
        row = QFrame()
        row.setObjectName("SettingsRow")
        divider = f"border-top: 1px solid {_BORDER_SOFT};" if self._lay.count() else ""
        row.setStyleSheet(f"QFrame#SettingsRow {{ background: transparent; border: none; {divider} }}")
        self._lay.addWidget(row)
        return row

    def add_row(self, label, description=None, control=None, stretch_control=False):
        """Add a row; ``label``/``description`` may be text or a ready QLabel."""
        row = self._new_row()
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(3)
        title = label if isinstance(label, QLabel) else _lbl(label, color=_TEXT, size=13)
        title.setFont(_mono(13, QFont.Weight.Medium))
        left.addWidget(title)
        if description is not None:
            desc = description if isinstance(description, QLabel) else _lbl(description, color=_TEXT_3)
            desc.setWordWrap(True)
            left.addWidget(desc)
        h.addLayout(left, 1)
        if control is not None:
            holder = QHBoxLayout()
            holder.setSpacing(6)
            holder.addStretch(0 if stretch_control else 1)
            if isinstance(control, QWidget):
                holder.addWidget(control, 1 if stretch_control else 0)
            else:
                holder.addLayout(control, 1 if stretch_control else 0)
            h.addLayout(holder, 1 if stretch_control else 0)
        return row

    def add_widget(self, widget, margins=(16, 12, 16, 12)):
        row = self._new_row()
        v = QVBoxLayout(row)
        v.setContentsMargins(*margins)
        v.addWidget(widget)
        return row


def _group_header(title, description=""):
    """Section title + muted description shown above a card."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(2, 0, 2, 2)
    v.setSpacing(3)
    t = _lbl(title, color=_TEXT, size=14)
    t.setFont(_mono(14, QFont.Weight.DemiBold))
    v.addWidget(t)
    if description:
        d = _lbl(description, color=_TEXT_3)
        d.setWordWrap(True)
        v.addWidget(d)
    return box


# ── Main dialog ───────────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("AgenticGIS — Settings")
        self.setMinimumSize(720, 560)
        self.resize(820, 680)
        _install_tooltip_palette()
        self.setStyleSheet(_DIALOG_SS)
        self._build_ui()
        self._load()

    # ── build ─────────────────────────────────────────────────────────────────
    _PAGES = ("Connection", "External agents", "Appearance")

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ─ left rail ─
        rail = QFrame()
        rail.setObjectName("SettingsRail")
        rail.setFixedWidth(184)
        rail.setStyleSheet(
            f"QFrame#SettingsRail {{ background: {_SURFACE_2}; border: none;"
            f" border-right: 1px solid {_BORDER_SOFT}; }}"
        )
        rail_col = QVBoxLayout(rail)
        rail_col.setContentsMargins(10, 16, 10, 12)
        rail_col.setSpacing(6)
        brand = _lbl("AgenticGIS", color=_TEXT, size=13)
        brand.setFont(_mono(13, QFont.Weight.DemiBold))
        brand.setContentsMargins(8, 0, 0, 0)
        rail_col.addWidget(brand)
        group_lbl = _lbl("Settings", color=_TEXT_4, size=11)
        group_lbl.setContentsMargins(8, 8, 0, 0)
        rail_col.addWidget(group_lbl)
        self._rail = QListWidget()
        self._rail.setFont(_mono(13))
        self._rail.setStyleSheet(_RAIL_SS)
        self._rail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name in self._PAGES:
            self._rail.addItem(name)
        rail_col.addWidget(self._rail, 1)
        version = _plugin_version()
        if version:
            ver = _lbl(f"v{version}", color=_TEXT_4, size=11)
            ver.setContentsMargins(8, 0, 0, 0)
            rail_col.addWidget(ver)
        root.addWidget(rail)

        # ─ content column ─
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        crumb_bar = QFrame()
        crumb_bar.setObjectName("SettingsCrumb")
        crumb_bar.setFixedHeight(42)
        crumb_bar.setStyleSheet(
            f"QFrame#SettingsCrumb {{ background: transparent; border: none;"
            f" border-bottom: 1px solid {_BORDER_SOFT}; }}"
        )
        crumb_row = QHBoxLayout(crumb_bar)
        crumb_row.setContentsMargins(24, 0, 24, 0)
        self._crumb = QLabel()
        self._crumb.setFont(_mono(13))
        self._crumb.setStyleSheet("background: transparent;")
        crumb_row.addWidget(self._crumb)
        col.addWidget(crumb_bar)

        self._pages = QStackedWidget()
        self._pages.setStyleSheet("QStackedWidget { background: transparent; }")
        self._pages.addWidget(self._page(self._connection_page()))
        self._pages.addWidget(self._page(self._mcp_section()))
        self._pages.addWidget(self._page(self._appearance_section()))
        col.addWidget(self._pages, 1)

        # ─ footer ─
        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        footer.setStyleSheet(
            f"QFrame#SettingsFooter {{ background: transparent; border: none;"
            f" border-top: 1px solid {_BORDER_SOFT}; }}"
        )
        btn_row = QHBoxLayout(footer)
        btn_row.setContentsMargins(24, 12, 24, 12)
        btn_row.setSpacing(8)
        note = _lbl("Runs entirely on QGIS's bundled Python — nothing to install.", color=_TEXT_4, size=11)
        btn_row.addWidget(note, 1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFont(_mono(12))
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(_BTN_SECONDARY_SS)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setFont(_mono(12, QFont.Weight.DemiBold))
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setStyleSheet(_BTN_PRIMARY_SS)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(self._save_btn)
        col.addWidget(footer)
        root.addWidget(content, 1)

        self._rail.currentRowChanged.connect(self._show_page)
        self._rail.setCurrentRow(0)

    def _page(self, body):
        """Wrap a page body in a scroll area with comfortable margins."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        v = QVBoxLayout(holder)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(0)
        v.addWidget(body)
        v.addStretch(1)
        scroll.setWidget(holder)
        scroll.viewport().setStyleSheet("background: transparent;")
        return scroll

    def _show_page(self, index):
        if index < 0:
            return
        self._pages.setCurrentIndex(index)
        self._crumb.setText(
            f"<span style='color:{_TEXT_3};'>Settings</span>"
            f"<span style='color:{_TEXT_4};'>&nbsp;&nbsp;›&nbsp;&nbsp;</span>"
            f"<span style='color:{_TEXT};'>{self._PAGES[index]}</span>"
        )

    def _connection_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addWidget(_group_header(
            "Connection",
            "How AgenticGIS reaches a model. Configure any mode; the one marked "
            "“Active” is used for chat after you Save.",
        ))

        track = QFrame()
        track.setObjectName("SegmentTrack")
        track.setStyleSheet(
            f"QFrame#SegmentTrack {{ background: {_SURFACE_2}; border: 1px solid {_BORDER_SOFT};"
            f" border-radius: 8px; }}"
        )
        track_row = QHBoxLayout(track)
        track_row.setContentsMargins(2, 2, 2, 2)
        self.connection_tabs = QTabBar()
        self.connection_tabs.setFont(_mono(12, QFont.Weight.Medium))
        self.connection_tabs.setStyleSheet(_TAB_SS)
        self.connection_tabs.setDrawBase(False)
        self.connection_tabs.setExpanding(False)
        self.connection_tabs.setUsesScrollButtons(False)
        for label, _ in _MODE_LABELS:
            self.connection_tabs.addTab(label)
        self.connection_tabs.currentChanged.connect(self.stack_set)
        track_row.addWidget(self.connection_tabs)
        v.addWidget(track, 0, Qt.AlignmentFlag.AlignLeft)

        # Only the active panel is visible, so the page is exactly as tall as
        # it (a QStackedWidget is as tall as its tallest panel).
        self._mode_panels = [self._api_key_panel(), self._custom_panel(), self._cli_agent_panel()]
        for panel in self._mode_panels:
            v.addWidget(panel)
        return page

    # ── external agents (MCP) ────────────────────────────────────────────────
    def _mcp_section(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addWidget(_group_header(
            "External agents",
            "Let agent CLIs outside QGIS drive this session through a local MCP server.",
        ))
        card = _Card()
        self.mcp_enabled_check = _Toggle()
        self.mcp_enabled_check.setChecked(bool(self.config.get("mcp_enabled")))
        card.add_row(
            "Expose QGIS tools over MCP",
            "Claude Code, Codex, OpenCode and others can use the live QGIS session via "
            "AgenticGIS's stdio MCP proxy (setup per CLI in the README, \"External agent "
            "access (MCP)\"). Listens on 127.0.0.1 only; takes effect immediately.",
            self.mcp_enabled_check,
        )
        v.addWidget(card)
        return page

    # ── appearance ───────────────────────────────────────────────────────────
    def _appearance_section(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addWidget(_group_header("Appearance", "Text size in the chat dock and this dialog."))
        card = _Card()
        self.font_scale_combo = _cmb(QComboBox())
        self.font_scale_combo.setMinimumWidth(110)
        saved = clamp_font_scale(self.config.get("font_scale"))
        scales = sorted({0.75, 0.9, 1.0, 1.15, 1.25, 1.5, 1.75, 2.0, saved})
        for scale in scales:
            self.font_scale_combo.addItem(f"{round(scale * 100)}%", scale)
        self.font_scale_combo.setCurrentIndex(scales.index(saved))
        card.add_row(
            "Font size",
            "Follows the QGIS font size (Settings → Options → General) and scales on top "
            "of it. New messages update right away; reload the plugin or restart QGIS to "
            "resize what is already on screen.",
            self.font_scale_combo,
        )
        v.addWidget(card)
        return page

    # ── stack panels ──────────────────────────────────────────────────────────
    def stack_set(self, index):
        for i, panel in enumerate(self._mode_panels):
            panel.setVisible(i == index)
        self._update_connection_tab_labels()
        if (index == 2 and getattr(self, "_loaded", False)
                and not getattr(self, "_cli_scan_performed", False)):
            self._scan_cli_agents()

    def _current_mode(self):
        return getattr(self, "_pending_connection_mode", self.config.get("connection_mode"))

    def _set_active_connection_mode(self, mode):
        if mode == config_mod.MODE_SUBSCRIPTION:
            mode = config_mod.MODE_CLI_TOOL
        self._pending_connection_mode = mode
        # Refresh the CLI agent list so the "· active" marker (and the
        # active-first reorder) reflects the new mode. In API key or
        # Custom mode no CLI is in use, so the marker is removed.
        if hasattr(self, "_cli_scan_rows") and hasattr(self, "cli_agent_list"):
            if self.cli_agent_list.count():
                selected = self._selected_cli_agent_id()
            else:
                selected = self.config.get("cli_tool") or "claude"
            self._fill_cli_agent_list(
                selected, scanned=self._cli_scan_performed
            )
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
        cli_model = (
            self.cli_model_picker.currentText().strip()
            if hasattr(self, "cli_model_picker") else ""
        )
        cli_path = self.cli_path_edit.text().strip() if hasattr(self, "cli_path_edit") else ""
        cli_text = "CLI Agent"
        self.connection_tabs.setTabText(2, active_label(2, cli_text))
        self.connection_tabs.setTabToolTip(
            2,
            "\n".join(part for part in (active_label(2, cli_text), agent_label, cli_model, cli_path) if part),
        )

    def _model_group(self, card, picker_attr):
        """Hidden-until-connected card row holding the model picker."""
        picker = _ModelPickerWidget("Select or type a model name")
        picker.setMinimumWidth(240)
        picker.modelChanged.connect(self._update_connection_tab_labels)
        setattr(self, picker_attr, picker)
        row = card.add_row(
            "Model", "Pick from the list or type a custom name, then press Enter.", picker,
        )
        row.setVisible(False)
        return row

    def _test_row(self, card, btn_attr, status_attr, mode, use_btn_attr=None):
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        btn = _ghost_btn("Test connection")
        btn.clicked.connect(lambda: self._test_connection(mode))
        setattr(self, btn_attr, btn)
        buttons.addWidget(btn)
        if use_btn_attr:
            use_btn = _ghost_btn("Use")
            use_btn.clicked.connect(lambda _checked=False, m=mode: self._use_connection_mode(m))
            setattr(self, use_btn_attr, use_btn)
            buttons.addWidget(use_btn)
        status = _lbl("Not tested yet", color=_TEXT_3)
        status.setWordWrap(True)
        setattr(self, status_attr, status)
        card.add_row("Connection", status, buttons)

    @staticmethod
    def _panel_column():
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        return w, col

    def _api_key_panel(self):
        w, col = self._panel_column()
        card = _Card()

        self.provider_combo = _cmb(QComboBox())
        self.provider_combo.setMinimumWidth(220)
        for p in providers.all_providers():
            self.provider_combo.addItem(p["label"], p["id"])
        card.add_row("Provider", "Built-in API provider.", self.provider_combo)

        self.api_base_url_edit = _inp(QLineEdit())
        self.api_base_url_edit.setMinimumWidth(260)
        self.api_base_url_edit.setPlaceholderText("Provider API base URL")
        self.api_base_url_edit.textChanged.connect(self._update_connection_tab_labels)
        card.add_row("Base URL", "Leave the default unless you use a proxy.", self.api_base_url_edit)

        self.api_key_edit = _inp(QLineEdit())
        self.api_key_edit.setMinimumWidth(260)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here")
        card.add_row("API key", "Stored in your QGIS settings on this machine.", self.api_key_edit)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._test_row(card, "api_test_btn", "api_status", config_mod.MODE_API_KEY, use_btn_attr="api_use_btn")
        self.api_model_group = self._model_group(card, "model_picker")
        col.addWidget(card)
        return w

    def _custom_panel(self):
        w, col = self._panel_column()
        card = _Card()

        self.custom_url_edit = _inp(QLineEdit())
        self.custom_url_edit.setMinimumWidth(260)
        self.custom_url_edit.setPlaceholderText("https://api.example.com")
        self.custom_url_edit.textChanged.connect(self._update_connection_tab_labels)
        card.add_row("Base URL", "Any OpenAI- or Anthropic-compatible server.", self.custom_url_edit)

        self.custom_key_edit = _inp(QLineEdit())
        self.custom_key_edit.setMinimumWidth(260)
        self.custom_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.custom_key_edit.setPlaceholderText("API key for this endpoint")
        card.add_row("API key", "Optional for local servers.", self.custom_key_edit)

        self.custom_format_combo = _cmb(QComboBox())
        self.custom_format_combo.setMinimumWidth(220)
        for label, value in _FORMAT_LABELS:
            self.custom_format_combo.addItem(label, value)
        self.custom_format_combo.currentIndexChanged.connect(self._update_connection_tab_labels)
        self.custom_format_combo.currentIndexChanged.connect(
            lambda _i: self._reset_model_group(config_mod.MODE_CUSTOM)
        )
        card.add_row("Wire format", "The API shape the server speaks.", self.custom_format_combo)

        self._test_row(
            card, "custom_test_btn", "custom_status", config_mod.MODE_CUSTOM, use_btn_attr="custom_use_btn",
        )
        self.custom_model_group = self._model_group(card, "custom_model_picker")
        col.addWidget(card)
        return w

    def _cli_agent_panel(self):
        w, col = self._panel_column()
        col.setSpacing(8)
        self._cli_auth_state = None     # None until checked; see _apply_cli_auth
        self._cli_detail_agent = None

        card = _Card()
        col.addWidget(card)

        # Agents: icon tiles. Picking one checks sign-in and loads its models.
        self.cli_scan_btn = _ghost_btn("↻")
        self.cli_scan_btn.setToolTip("Rescan for installed agent CLIs")
        self.cli_scan_btn.clicked.connect(self._scan_cli_agents)
        self.cli_rescan_btn = self.cli_scan_btn   # one button; old name kept for _run_busy lists
        self.cli_scan_status = _lbl("", color=_TEXT_3)
        card.add_row("Agent", self.cli_scan_status, self.cli_scan_btn)
        self.cli_agent_list = _TileList()
        self.cli_agent_list.setFont(_mono(11))
        self.cli_agent_list.setStyleSheet(_AGENT_TILES_SS)
        self.cli_agent_list.currentItemChanged.connect(self._on_cli_agent_selected)
        card.add_widget(self.cli_agent_list, margins=(8, 2, 8, 2))

        # Status: who, sign-in result, re-check, use.
        self.cli_agent_name = _lbl("Select an agent", color=_TEXT, size=13)
        self.cli_auth_status = _lbl("Not checked yet", color=_TEXT_3)
        self.cli_auth_status.setWordWrap(True)
        self.cli_auth_btn = _ghost_btn("↻")
        self.cli_auth_btn.setToolTip("Check sign-in again and reload models")
        self.cli_auth_btn.clicked.connect(self._check_cli_auth)
        self.cli_use_btn = QPushButton("Use")
        self.cli_use_btn.setFont(_mono(12, QFont.Weight.DemiBold))
        self.cli_use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cli_use_btn.setStyleSheet(_BTN_PRIMARY_SS)
        self.cli_use_btn.clicked.connect(self._use_cli_agent)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(self.cli_auth_btn)
        controls.addWidget(self.cli_use_btn)
        card.add_row(self.cli_agent_name, self.cli_auth_status, controls)
        self.cli_agent_warning = _lbl("", color=_WARN)
        self.cli_agent_warning.setWordWrap(True)
        self.cli_agent_warning.setVisible(False)
        card.add_widget(self.cli_agent_warning, margins=(16, 0, 16, 10))

        self.cli_model_group = self._model_group(card, "cli_model_picker")
        self.cli_model_group.setVisible(True)
        self.cli_model_picker.modelChanged.connect(self._refresh_cli_steps)

        # ─ advanced: binary path + smoke test ─
        self._cli_adv_btn = QPushButton("Advanced  ›")
        self._cli_adv_btn.setFlat(True)
        self._cli_adv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cli_adv_btn.setFont(_mono(12))
        self._cli_adv_btn.setStyleSheet(
            f"QPushButton {{ color: {_TEXT_3}; background: transparent; border: none;"
            f" padding: 6px 2px; text-align: left; }}"
            f"QPushButton:hover {{ color: {_TEXT}; }}"
        )
        col.addWidget(self._cli_adv_btn, 0, Qt.AlignmentFlag.AlignLeft)
        adv = _Card()
        adv.setVisible(False)
        self._cli_adv_btn.clicked.connect(lambda: (
            adv.setVisible(not adv.isVisible()),
            self._cli_adv_btn.setText("Advanced  ⌄" if adv.isVisible() else "Advanced  ›"),
        ))
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self._cli_path_is_override = False
        self._syncing_cli_path = False
        self._syncing_cli_selection = False
        self.cli_path_edit = _inp(QLineEdit())
        self.cli_path_edit.setMinimumWidth(220)
        self.cli_path_edit.setPlaceholderText("Auto-detect on PATH (leave empty)")
        self.cli_path_edit.editingFinished.connect(self._scan_cli_agents)
        self.cli_path_edit.textEdited.connect(self._mark_cli_path_override)
        self.cli_path_edit.textChanged.connect(self._update_connection_tab_labels)
        path_row.addWidget(self.cli_path_edit, 1)
        browse_cli = _ghost_btn("Browse…")
        browse_cli.clicked.connect(self._browse_cli)
        path_row.addWidget(browse_cli)
        self.cli_resolved_path = _lbl("", color=_TEXT_3, size=11)
        self.cli_resolved_path.setWordWrap(True)
        self.cli_resolved_path.setVisible(False)
        adv.add_row("Command path", self.cli_resolved_path, path_row, stretch_control=True)
        self.cli_test_status = _lbl("Runs the CLI's version check.", color=_TEXT_3)
        self.cli_test_status.setWordWrap(True)
        self.cli_test_btn = _ghost_btn("Test binary")
        self.cli_test_btn.clicked.connect(self._test_cli_agent)
        adv.add_row("Binary", self.cli_test_status, self.cli_test_btn)
        col.addWidget(adv)

        self.sub_status = _lbl(
            "Delegates to the selected local CLI. AgenticGIS never reads OAuth tokens.",
            color=_TEXT_4, size=11,
        )
        self.sub_status.setContentsMargins(4, 0, 0, 0)
        col.addWidget(self.sub_status)
        return w

    def _cli_agent_ready(self):
        row = self._cli_scan_row(self._selected_cli_agent_id())
        has_path = bool(self.cli_path_edit.text().strip())
        return bool(row and row.get("installed")) or has_path

    def _refresh_cli_steps(self, *_args):
        """Sync the Use button and the in-use ring with the current selection."""
        if not hasattr(self, "cli_use_btn"):
            return
        in_use = (
            self._current_mode() == config_mod.MODE_CLI_TOOL
            and self._selected_cli_agent_id() == getattr(self, "_cli_used_agent", None)
        )
        self.cli_use_btn.setText("✓ In use" if in_use else "Use")
        self.cli_use_btn.setEnabled(self._cli_agent_ready() and not in_use)
        self._paint_cli_agent_icons()

    def _paint_cli_agent_icons(self):
        used = getattr(self, "_cli_used_agent", None) if self._current_mode() == config_mod.MODE_CLI_TOOL else None
        for i in range(self.cli_agent_list.count()):
            item = self.cli_agent_list.item(i)
            agent_id = item.data(Qt.ItemDataRole.UserRole)
            row = self._cli_scan_row(agent_id) or {}
            item.setIcon(_agent_icon(agent_id, row.get("label", agent_id), agent_id == used,
                                     bool(row.get("installed"))))

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
        if mode == config_mod.MODE_CLI_TOOL:
            return (self.cli_auth_status, self.cli_auth_btn,
                    self.cli_model_picker, self.cli_model_group)
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

        def done(result, err, m=mode):
            models, fetch_err = result if result else ([], "")
            self._on_models_fetched(m, list(models or []), err or fetch_err or "")

        self._run_busy(fetch, done, status, "Checking connection…", [btn])

    def _on_models_fetched(self, mode, models, err):
        status, _btn, picker, group = self._panel_widgets(mode)

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
        import platform as _platform
        if _platform.system() == "Windows":
            flt = "Executables (*.exe *.cmd *.bat *.com);;All files (*)"
        else:
            flt = "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select agent CLI binary", "", flt)
        if path:
            agent_id = _agent_id_for_binary_path(path)
            if agent_id:
                self._select_cli_agent(agent_id, preserve_path_override=True)
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
            return item.data(Qt.ItemDataRole.UserRole)
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

    def _select_cli_agent(self, agent_id, preserve_path_override=False):
        if preserve_path_override:
            self._syncing_cli_selection = True
        try:
            for i in range(self.cli_agent_list.count()):
                item = self.cli_agent_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == agent_id:
                    self.cli_agent_list.setCurrentRow(i)
                    return
            if self.cli_agent_list.count():
                self.cli_agent_list.setCurrentRow(0)
        finally:
            if preserve_path_override:
                self._syncing_cli_selection = False

    def _run_busy(self, fn, on_done, label, text, buttons):
        """Run ``fn`` in the background with a spinner in ``label``.

        ``buttons`` are disabled until it finishes; ``on_done(result, err)``
        then runs on the UI thread.
        """
        for btn in buttons:
            btn.setEnabled(False)
        label.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        frame = [0]

        def tick():
            label.setText(f"{_SPINNER[frame[0] % len(_SPINNER)]}  {text}")
            frame[0] += 1

        tick()
        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(80)

        task = _BgTask(fn)
        _LIVE_TASKS.add(task)

        def finish(result, err):
            _LIVE_TASKS.discard(task)
            task.deleteLater()
            try:
                timer.stop()
                for btn in buttons:
                    btn.setEnabled(True)
                on_done(result, err)
            except RuntimeError:
                pass  # dialog was closed while the task ran  # nosec B110

        task.done.connect(finish)
        task.start()

    def _scan_cli_agents(self):
        from ..backends.cli_backend import scan_cli_agents

        if getattr(self, "_cli_scanning", False):
            return
        self._cli_scanning = True
        overrides = self._cli_path_overrides()

        def done(rows, err):
            self._cli_scanning = False
            if err:
                self._set_status(self.cli_scan_status, f"Scan failed · {err}", _DANGER)
                return
            self._cli_scan_rows = rows
            self._cli_scan_performed = True
            self._fill_cli_agent_list(self._selected_cli_agent_id(), scanned=True)

        self._run_busy(
            lambda: scan_cli_agents(overrides), done, self.cli_scan_status,
            "Scanning for agent CLIs…",
            [self.cli_scan_btn, self.cli_rescan_btn, self.cli_test_btn, self.cli_auth_btn],
        )

    def _fill_cli_agent_list(self, selected, scanned):
        self.cli_agent_list.blockSignals(True)
        self.cli_agent_list.clear()
        found = 0
        # The "· active" marker and the active-first reorder only apply
        # when CLI Agent is the active connection mode. In API key or
        # Custom mode no CLI is in use, so no agent should be flagged
        # active or hoisted to the top.
        is_cli_mode = self._current_mode() == config_mod.MODE_CLI_TOOL
        active_id = selected if is_cli_mode else None
        active_row = None
        rest = []
        for row in self._cli_scan_rows:
            if active_row is None and row.get("id") == active_id:
                active_row = row
            else:
                rest.append(row)
        if scanned:
            rest.sort(key=lambda r: not r.get("installed"))
        ordered_rows = ([active_row] if active_row is not None else []) + rest
        for row in ordered_rows:
            found += 1 if row.get("installed") else 0
            if row.get("installed") and (scanned or row.get("_selected_probe")):
                status = "installed"
            else:
                status = "not installed" if scanned else "not scanned"
            active = " · in use" if row.get("id") == active_id else ""
            item = QListWidgetItem(row["label"].replace(" CLI", ""))
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"{row['label']} · {status}{active}\n{row.get('credential_style', '')}".strip())
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.cli_agent_list.addItem(item)
            # Only installed agents get a tile (plus the saved one, so it stays selectable).
            item.setHidden(not row.get("installed") and row.get("id") != selected)
        self.cli_agent_list.blockSignals(False)
        self.cli_agent_list.fit()
        self._select_cli_agent(selected, preserve_path_override=True)
        if scanned:
            missing = len(self._cli_scan_rows) - found
            self.cli_scan_status.setText(
                f"{found} installed · {missing} more supported" if found
                else "None found — install an agent CLI, or set its path under Advanced."
            )
        else:
            self.cli_scan_status.setText("Scanning when this tab opens…")
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
        agent_id = self._selected_cli_agent_id()
        if agent_id != self._cli_detail_agent:
            # A list rebuild (scan) re-selects the same agent — keep its state.
            self._cli_detail_agent = agent_id
            self._cli_auth_state = None
            self.cli_test_status.setText("Runs the CLI's version check.")
            self._set_status(self.cli_auth_status, "Not checked yet", _TEXT_3)
            self.cli_model_picker.setModels([], keep_current=False)
            self.cli_model_picker.setCurrentText("")
            if self._cli_agent_ready():
                QTimer.singleShot(0, self._check_cli_auth)
        if not getattr(self, "_syncing_cli_selection", False):
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
        self.cli_agent_name.setToolTip(row.get("credential_style", ""))
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
        self._refresh_cli_steps()

    def _selected_cli_backend_factory(self):
        """Return a thread-safe ``make()`` that builds the selected CLI backend.

        Widget state and settings are read here on the UI thread; ``make()``
        only resolves the binary (may spawn processes) and needs no Qt/QGIS.
        """
        from ..backends.cli_backend import _resolve_binary, CliToolBackend

        agent_id = self._selected_cli_agent_id()
        path = self.cli_path_edit.text().strip() if self._cli_path_is_override else ""
        settings = self.config.all()  # plain dict: no QSettings access off-thread

        def make():
            binary = _resolve_binary(agent_id, path)
            if not binary:
                return None
            backend = CliToolBackend(settings, None, None)
            backend.tool = agent_id
            backend.binary = binary
            return backend

        return make

    def _test_cli_agent(self):
        make = self._selected_cli_backend_factory()

        def work():
            backend = make()
            return None if backend is None else backend.test_cli()

        def done(result, err):
            if err:
                ok, detail = False, err
            elif result is None:
                ok, detail = False, "Binary not found"
            else:
                ok, detail = result
            color = _SUCCESS if ok else _DANGER
            text = f"{'OK' if ok else 'Failed'} · {detail}" if result else detail
            self._set_status(self.cli_test_status, text, color)

        self._run_busy(
            work, done, self.cli_test_status, "Testing…",
            [self.cli_test_btn, self.cli_auth_btn, self.cli_scan_btn, self.cli_rescan_btn],
        )

    def _check_cli_auth(self):
        make = self._selected_cli_backend_factory()

        def work():
            backend = make()
            if backend is None:
                return None
            state, detail = backend.auth_status()
            try:
                models = backend.list_models()
            except Exception:  # noqa: BLE001 — a model list is optional
                models = []
            return state, detail, models

        def done(result, err):
            if err:
                result = ("unknown", err, [])
            if result is None:
                result = ("missing", "Binary not found", [])
            self._apply_cli_auth(*result)

        self._run_busy(
            work, done, self.cli_auth_status, "Checking auth…",
            [self.cli_auth_btn, self.cli_test_btn, self.cli_scan_btn, self.cli_rescan_btn],
        )

    def _apply_cli_auth(self, state, detail, models):
        self._cli_auth_state = state
        n = len(models)
        loaded = f" · {n} model{'s' if n != 1 else ''}" if n else ""
        if state == "ready":
            self._set_status(self.cli_auth_status, f"Signed in · {detail}{loaded}", _SUCCESS)
        elif state == "login_required":
            self._set_status(
                self.cli_auth_status,
                f"Not signed in · {detail}. Run the CLI in a terminal to log in, then check again.",
                _WARN,
            )
        elif state == "missing":
            self._set_status(self.cli_auth_status, detail, _DANGER)
        else:
            self._set_status(
                self.cli_auth_status,
                f"This CLI can't report sign-in status — continue if it works in a terminal{loaded}",
                _TEXT_3,
            )
        saved = self.config.get("cli_model") or ""
        self.cli_model_picker.setActive(saved)
        self._fill_models(self.cli_model_picker, models)
        if not self.cli_model_picker.currentText().strip():
            same_agent = self._selected_cli_agent_id() == (self.config.get("cli_tool") or "")
            if saved and same_agent:
                self.cli_model_picker.setCurrentText(saved)
            elif models:
                self.cli_model_picker.setCurrentText(models[0])
        self._refresh_cli_steps()
        self._update_connection_tab_labels()

    def _use_cli_agent(self):
        self._cli_used_agent = self._selected_cli_agent_id()
        self._set_active_connection_mode(config_mod.MODE_CLI_TOOL)
        self._refresh_cli_steps()
        self._update_connection_tab_labels()

    # ── load / save ───────────────────────────────────────────────────────────
    def _load(self):
        mode = self.config.get("connection_mode")
        if mode == config_mod.MODE_SUBSCRIPTION:
            mode = config_mod.MODE_CLI_TOOL
        self._pending_connection_mode = mode
        index = next((i for i, (_, m) in enumerate(_MODE_LABELS) if m == mode), 0)
        self.connection_tabs.setCurrentIndex(index)
        self.stack_set(index)

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

        cli_model = self.config.get("cli_model") or ""
        self.cli_model_picker.setCurrentText(cli_model)
        self.cli_model_picker.setActive(cli_model)
        if mode == config_mod.MODE_CLI_TOOL:
            # Set up before: unlock the steps; Check sign-in refreshes models.
            self._cli_used_agent = self.config.get("cli_tool") or "claude"
            self._cli_auth_state = "saved"
            self._set_status(
                self.cli_auth_status, "Set up previously — check again to refresh the model list.", _TEXT_3,
            )
        self._refresh_cli_steps()

        self._update_connection_tab_labels()
        self._loaded = True
        if index == 2:
            QTimer.singleShot(0, self._scan_cli_agents)

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
            cli_model = self.cli_model_picker.currentText().strip()
            self.config.set("cli_model", cli_model)
            if cli_model:
                self.config.set("model", cli_model)

        self.config.set(
            "mcp_enabled",
            self.mcp_enabled_check.isChecked(),
        )
        font_scale = self.font_scale_combo.currentData()
        self.config.set("font_scale", font_scale)
        set_font_scale(font_scale)

        self.accept()
