"""Settings dialog — styled to match the AskUser/permission card aesthetic.

Three connection modes:
  1. API key       — pick a built-in provider or custom endpoint.
  2. Custom endpoint — any OpenAI- or Anthropic-compatible server.
  3. Subscription   — use an installed CLI agent (Claude Code, OpenCode, …).
"""

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
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

        form1 = QFormLayout()
        form1.setSpacing(10)
        form1.setContentsMargins(0, 0, 0, 0)
        form1.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.mode_combo = _cmb(QComboBox())
        for label, _ in _MODE_LABELS:
            self.mode_combo.addItem(label)
        self.mode_combo.currentIndexChanged.connect(self.stack_set)
        form1.addRow(_lbl("Connect via:"), self.mode_combo)
        cb.addLayout(form1)

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

    @staticmethod
    def _panel_form(w):
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(0, 4, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return form

    def _api_key_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        form = self._panel_form(w)

        self.provider_combo = _cmb(QComboBox())
        for p in providers.all_providers():
            self.provider_combo.addItem(p["label"], p["id"])
        form.addRow(_lbl("Provider:"), self.provider_combo)

        self.model_edit = _inp(QLineEdit())
        self.model_edit.setPlaceholderText("Provider model")
        form.addRow(_lbl("Model:"), self.model_edit)

        self.api_base_url_edit = _inp(QLineEdit())
        self.api_base_url_edit.setPlaceholderText("Provider API base URL")
        form.addRow(_lbl("Base URL:"), self.api_base_url_edit)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

        self.api_key_edit = _inp(QLineEdit())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here")
        form.addRow(_lbl("API key:"), self.api_key_edit)
        return w

    def _custom_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        form = self._panel_form(w)

        self.custom_url_edit = _inp(QLineEdit())
        self.custom_url_edit.setPlaceholderText("https://api.example.com")
        form.addRow(_lbl("Base URL:"), self.custom_url_edit)

        self.custom_key_edit = _inp(QLineEdit())
        self.custom_key_edit.setEchoMode(QLineEdit.Password)
        self.custom_key_edit.setPlaceholderText("API key for this endpoint")
        form.addRow(_lbl("API key:"), self.custom_key_edit)

        self.custom_format_combo = _cmb(QComboBox())
        for label, value in _FORMAT_LABELS:
            self.custom_format_combo.addItem(label, value)
        form.addRow(_lbl("Wire format:"), self.custom_format_combo)

        self.custom_model_edit = _inp(QLineEdit())
        self.custom_model_edit.setPlaceholderText("e.g. llama3.1, gpt-4, claude-sonnet")
        form.addRow(_lbl("Model:"), self.custom_model_edit)
        return w

    def _subscription_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        form = self._panel_form(w)

        self.cli_agent_combo = _cmb(QComboBox())
        for slug, label in _CLI_AGENTS:
            self.cli_agent_combo.addItem(label, slug)
        self.cli_agent_combo.currentIndexChanged.connect(self._on_cli_agent_changed)
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
            self.model_edit.setText(p["default_model"])
            self.api_base_url_edit.setText(p["base_url"])
            env = p.get("key_env", "")
            self.api_key_edit.setPlaceholderText(
                f"Paste your key (or set {env})" if env else "Paste your key"
            )

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
        self.mode_combo.setCurrentIndex(index)
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
        self.custom_model_edit.setText(self.config.get("custom_model") or "")

        cli = self.config.get("cli_tool")
        cidx = next((i for i, (s, _) in enumerate(_CLI_AGENTS) if s == cli), 0)
        self.cli_agent_combo.setCurrentIndex(cidx)
        self.cli_path_edit.setText(self.config.get("cli_path") or "")
        self._update_login_status()

        self.model_edit.setText(self.config.get("model") or "")

        to_val = self.config.get("main_thread_timeout")
        self.timeout_edit.setText("" if to_val is None else str(to_val))
        pt_val = self.config.get("processing_timeout")
        self.proc_timeout_edit.setText("" if pt_val is None else str(pt_val))
        pi_val = self.config.get("mcp_poll_interval")
        self.poll_interval_edit.setText("" if pi_val is None else str(pi_val))

    def _save_and_accept(self):
        mode = _MODE_LABELS[self.mode_combo.currentIndex()][1]

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
            self.config.set("provider", self.provider_combo.currentData())
            self.config.set("api_key", self.api_key_edit.text().strip())
            self.config.set("model", self.model_edit.text().strip())
            self.config.set("api_base_url", self.api_base_url_edit.text().strip())
        elif mode == config_mod.MODE_CUSTOM:
            self.config.set("provider", "custom")
            self.config.set("custom_base_url", self.custom_url_edit.text().strip())
            self.config.set("custom_api_key", self.custom_key_edit.text().strip())
            self.config.set("custom_format", self.custom_format_combo.currentData())
            self.config.set("custom_model", self.custom_model_edit.text().strip())
            self.config.set("model", self.custom_model_edit.text().strip())
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
