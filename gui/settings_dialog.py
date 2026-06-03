"""Settings dialog: choose how to connect to the LLM and tune behaviour.

Three connection methods:
  1. API key       — pick a built-in provider (Anthropic, OpenAI, Groq, …)
                     or a custom endpoint, and paste a key.
  2. Custom endpoint — any OpenAI-compatible or Anthropic-compatible server.
  3. Subscription   — use an installed, already-logged-in CLI agent
                     (Claude Code, OpenCode, Codex, Gemini CLI).
"""

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from ..backends import providers

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
_SURFACE     = "#161616"
_CANVAS      = "#0a0a0b"
_INPUT_BG    = "#1e1e1e"
_BORDER      = "#2e2e2e"
_BORDER_SOFT = "#242424"
_TEXT        = "#ececec"
_TEXT_2      = "#a0a0a0"
_TEXT_3      = "#707070"
_ACCENT      = "#e0e0e0"
_ACCENT_HOV  = "#c8c8c8"
_DANGER      = "#e57373"
_SUCCESS     = "#81c784"

# ---------------------------------------------------------------------------
# Shared stylesheet fragments
# ---------------------------------------------------------------------------
_INPUT_SS = (
    f"background: {_INPUT_BG};"
    f"color: {_TEXT};"
    f"border: 1px solid {_BORDER};"
    f"border-radius: 6px;"
    f"padding: 4px 8px;"
    f"selection-background-color: {_BORDER};"
)

_COMBO_SS = (
    f"QComboBox {{"
    f"  background: {_INPUT_BG};"
    f"  color: {_TEXT};"
    f"  border: 1px solid {_BORDER};"
    f"  border-radius: 6px;"
    f"  padding: 4px 8px;"
    f"}}"
    f"QComboBox::drop-down {{"
    f"  border: none;"
    f"  width: 20px;"
    f"}}"
    f"QComboBox QAbstractItemView {{"
    f"  background: {_INPUT_BG};"
    f"  color: {_TEXT};"
    f"  border: 1px solid {_BORDER};"
    f"  selection-background-color: {_BORDER};"
    f"  outline: none;"
    f"}}"
)

_LABEL_SS = f"color: {_TEXT_2}; background: transparent;"

_BTN_PRIMARY_SS = (
    f"QPushButton {{"
    f"  background: {_ACCENT};"
    f"  color: {_CANVAS};"
    f"  border: none;"
    f"  border-radius: 6px;"
    f"  padding: 6px 16px;"
    f"  font-weight: bold;"
    f"}}"
    f"QPushButton:hover {{"
    f"  background: {_ACCENT_HOV};"
    f"}}"
    f"QPushButton:pressed {{"
    f"  background: {_TEXT_2};"
    f"}}"
)

_BTN_SECONDARY_SS = (
    f"QPushButton {{"
    f"  background: transparent;"
    f"  color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER};"
    f"  border-radius: 6px;"
    f"  padding: 6px 16px;"
    f"}}"
    f"QPushButton:hover {{"
    f"  background: {_INPUT_BG};"
    f"  color: {_TEXT};"
    f"}}"
)

_BTN_GHOST_SS = (
    f"QPushButton {{"
    f"  background: {_INPUT_BG};"
    f"  color: {_TEXT_2};"
    f"  border: 1px solid {_BORDER};"
    f"  border-radius: 6px;"
    f"  padding: 4px 10px;"
    f"}}"
    f"QPushButton:hover {{"
    f"  background: {_BORDER};"
    f"  color: {_TEXT};"
    f"}}"
)

_CHECKBOX_SS = (
    f"QCheckBox {{"
    f"  color: {_TEXT_2};"
    f"  background: transparent;"
    f"  spacing: 6px;"
    f"}}"
    f"QCheckBox::indicator {{"
    f"  width: 14px;"
    f"  height: 14px;"
    f"  border: 1px solid {_BORDER};"
    f"  border-radius: 3px;"
    f"  background: {_INPUT_BG};"
    f"}}"
    f"QCheckBox::indicator:checked {{"
    f"  background: {_ACCENT};"
    f"  border-color: {_ACCENT};"
    f"}}"
)

_DIALOG_SS = (
    f"QDialog {{"
    f"  background: {_SURFACE};"
    f"}}"
    f"QWidget {{"
    f"  background: {_SURFACE};"
    f"  color: {_TEXT};"
    f"}}"
    f"QScrollBar:vertical {{"
    f"  background: {_SURFACE};"
    f"  width: 6px;"
    f"  margin: 0;"
    f"}}"
    f"QScrollBar::handle:vertical {{"
    f"  background: {_BORDER};"
    f"  border-radius: 3px;"
    f"  min-height: 20px;"
    f"}}"
    f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{"
    f"  height: 0;"
    f"}}"
)


def _make_input(widget):
    widget.setStyleSheet(_INPUT_SS)
    return widget


def _make_combo(widget):
    widget.setStyleSheet(_COMBO_SS)
    return widget


def _make_label(widget):
    widget.setStyleSheet(_LABEL_SS)
    return widget


# ---------------------------------------------------------------------------
# Collapsible section
# ---------------------------------------------------------------------------

class _CollapsibleSection(QWidget):
    """A titled section that can be toggled open/closed."""

    def __init__(self, title, parent=None, initially_expanded=True):
        super().__init__(parent)
        self._expanded = initially_expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(initially_expanded)
        self._toggle_btn.setArrowType(Qt.DownArrow if initially_expanded else Qt.RightArrow)
        self._toggle_btn.setStyleSheet(
            f"QToolButton {{"
            f"  background: transparent;"
            f"  border: none;"
            f"  color: {_TEXT_3};"
            f"  padding: 0;"
            f"}}"
        )
        self._toggle_btn.clicked.connect(self._on_toggle)
        header.addWidget(self._toggle_btn)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {_TEXT_3};"
            f"font-size: 11px;"
            f"font-weight: bold;"
            f"letter-spacing: 0.5px;"
            f"background: transparent;"
        )
        header.addWidget(lbl)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER_SOFT}; background: {_BORDER_SOFT};")
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        line.setFixedHeight(1)
        header.addWidget(line, 1)
        outer.addLayout(header)

        self._content = QWidget()
        self._content.setVisible(initially_expanded)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 6, 0, 0)
        self._content_layout.setSpacing(0)
        outer.addWidget(self._content)

    def _on_toggle(self, checked):
        self._expanded = checked
        self._content.setVisible(checked)
        self._toggle_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("AgenticGIS — Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(_DIALOG_SS)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_header(text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {_TEXT};"
            f"font-weight: bold;"
            f"font-size: 12px;"
            f"background: transparent;"
            f"padding-top: 6px;"
            f"padding-bottom: 2px;"
        )
        return lbl

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER}; background: {_BORDER};")
        line.setFixedHeight(1)
        return line

    def _form_label(self, text):
        lbl = QLabel(text)
        _make_label(lbl)
        return lbl

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        from qgis.PyQt.QtWidgets import QFormLayout

        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(16, 16, 16, 16)

        # ---- Connection section ----
        layout.addWidget(self._section_header("Connection"))
        layout.addWidget(self._separator())

        conn_form = QFormLayout()
        conn_form.setSpacing(8)
        conn_form.setContentsMargins(0, 8, 0, 8)
        conn_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.mode_combo = _make_combo(QComboBox())
        for label, _ in _MODE_LABELS:
            self.mode_combo.addItem(label)
        self.mode_combo.currentIndexChanged.connect(self.stack_set)
        conn_form.addRow(self._form_label("Connect via:"), self.mode_combo)
        layout.addLayout(conn_form)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"QStackedWidget {{ background: {_SURFACE}; border: none; }}")
        self.stack.addWidget(self._api_key_panel())
        self.stack.addWidget(self._custom_panel())
        self.stack.addWidget(self._subscription_panel())
        layout.addWidget(self.stack)

        layout.addSpacing(8)

        # ---- Behaviour section ----
        layout.addWidget(self._section_header("Behaviour"))
        layout.addWidget(self._separator())

        beh_form = QFormLayout()
        beh_form.setSpacing(8)
        beh_form.setContentsMargins(0, 8, 0, 8)
        beh_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.model_edit = _make_input(QLineEdit())
        beh_form.addRow(self._form_label("Model:"), self.model_edit)

        self.system_edit = QPlainTextEdit()
        self.system_edit.setPlaceholderText("Leave empty for the built-in GIS system prompt.")
        self.system_edit.setFixedHeight(72)
        self.system_edit.setStyleSheet(_INPUT_SS)
        beh_form.addRow(self._form_label("System prompt:"), self.system_edit)

        self.autorun_cb = QCheckBox("Auto-run generated code (no confirmation)")
        self.autorun_cb.setStyleSheet(_CHECKBOX_SS)
        beh_form.addRow("", self.autorun_cb)

        # F16: when on, run_pyqgis refuses code that calls os.system,
        # subprocess, shutil.rmtree, ctypes, etc., unless the code sets
        # ALLOW_DANGEROUS = True at the top.
        self.confirm_dangerous_cb = QCheckBox(
            "Block destructive builtins in agent code (os.system, subprocess, shutil.rmtree, ctypes)"
        )
        self.confirm_dangerous_cb.setStyleSheet(_CHECKBOX_SS)
        beh_form.addRow("", self.confirm_dangerous_cb)

        layout.addLayout(beh_form)

        layout.addSpacing(4)

        # ---- Advanced section (collapsible, collapsed by default) ----
        adv = _CollapsibleSection("Advanced", initially_expanded=False)

        adv_form = QFormLayout()
        adv_form.setSpacing(8)
        adv_form.setContentsMargins(0, 4, 0, 4)
        adv_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.timeout_edit = _make_input(QLineEdit())
        self.timeout_edit.setPlaceholderText("60")
        adv_form.addRow(self._form_label("Main-thread timeout (s):"), self.timeout_edit)

        self.proc_timeout_edit = _make_input(QLineEdit())
        self.proc_timeout_edit.setPlaceholderText("120")
        adv_form.addRow(self._form_label("Processing timeout (s):"), self.proc_timeout_edit)

        self.poll_interval_edit = _make_input(QLineEdit())
        self.poll_interval_edit.setPlaceholderText("0.5")
        adv_form.addRow(self._form_label("MCP poll interval (s):"), self.poll_interval_edit)

        adv.add_layout(adv_form)
        layout.addWidget(adv)

        layout.addSpacing(8)

        # ---- Footer note ----
        note = QLabel(
            "No installation required — the plugin runs entirely on QGIS's bundled Python."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {_TEXT_3};"
            f"font-size: 11px;"
            f"background: transparent;"
            f"padding: 4px 0;"
        )
        layout.addWidget(note)

        layout.addSpacing(4)

        # ---- Button row ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setStyleSheet(_BTN_SECONDARY_SS)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setStyleSheet(_BTN_PRIMARY_SS)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Stack panels
    # ------------------------------------------------------------------

    def stack_set(self, index):
        self.stack.setCurrentIndex(index)

    def _api_key_panel(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.provider_combo = _make_combo(QComboBox())
        for p in providers.all_providers():
            self.provider_combo.addItem(p["label"], p["id"])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow(self._form_label("Provider:"), self.provider_combo)

        self.api_key_edit = _make_input(QLineEdit())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here")
        form.addRow(self._form_label("API key:"), self.api_key_edit)

        return w

    def _custom_panel(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.custom_url_edit = _make_input(QLineEdit())
        self.custom_url_edit.setPlaceholderText("https://api.example.com")
        form.addRow(self._form_label("Base URL:"), self.custom_url_edit)

        self.custom_key_edit = _make_input(QLineEdit())
        self.custom_key_edit.setEchoMode(QLineEdit.Password)
        self.custom_key_edit.setPlaceholderText("API key for this endpoint")
        form.addRow(self._form_label("API key:"), self.custom_key_edit)

        self.custom_format_combo = _make_combo(QComboBox())
        for label, value in _FORMAT_LABELS:
            self.custom_format_combo.addItem(label, value)
        form.addRow(self._form_label("Wire format:"), self.custom_format_combo)

        self.custom_model_edit = _make_input(QLineEdit())
        self.custom_model_edit.setPlaceholderText("e.g. llama3.1, gpt-4, claude-sonnet")
        form.addRow(self._form_label("Model:"), self.custom_model_edit)

        return w

    def _subscription_panel(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.cli_agent_combo = _make_combo(QComboBox())
        for slug, label in _CLI_AGENTS:
            self.cli_agent_combo.addItem(label, slug)
        self.cli_agent_combo.currentIndexChanged.connect(self._on_cli_agent_changed)
        form.addRow(self._form_label("Agent:"), self.cli_agent_combo)

        # --- Login status row ---
        login_row = QHBoxLayout()
        login_row.setSpacing(6)
        self.login_status = QLabel("Checking…")
        self.login_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        login_row.addWidget(self.login_status, 1)

        self.login_browser_btn = QPushButton("Login with Browser")
        self.login_browser_btn.setStyleSheet(_BTN_GHOST_SS)
        self.login_browser_btn.clicked.connect(self._login_browser)
        login_row.addWidget(self.login_browser_btn)
        form.addRow(self._form_label("Login:"), login_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.cli_path_edit = _make_input(QLineEdit())
        self.cli_path_edit.setPlaceholderText("Auto-detect on PATH (leave empty)")
        self.cli_path_edit.editingFinished.connect(self._update_login_status)
        path_row.addWidget(self.cli_path_edit, 1)
        browse = QPushButton("Browse…")
        browse.setStyleSheet(_BTN_GHOST_SS)
        browse.clicked.connect(self._browse_cli)
        path_row.addWidget(browse)
        form.addRow(self._form_label("Binary path:"), path_row)

        self.sub_status = QLabel("Uses the agent's existing login.")
        self.sub_status.setStyleSheet(
            f"color: {_TEXT_3}; font-style: italic; background: transparent;"
        )
        form.addRow(self.sub_status)

        return w

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_provider_changed(self, index):
        pid = self.provider_combo.itemData(index)
        p = providers.get_provider(pid)
        if p:
            self.model_edit.setText(p["default_model"])
            env = p.get("key_env", "")
            if env:
                self.api_key_edit.setPlaceholderText(f"Paste your key (or set {env})")
            else:
                self.api_key_edit.setPlaceholderText("Paste your key")

    def _browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select agent CLI binary")
        if path:
            self.cli_path_edit.setText(path)
            self._update_login_status()

    def _on_cli_agent_changed(self, index):
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
                "Set its path or make sure it is on PATH."
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
                "Please complete the authentication, then check the status here."
            )
            QTimer.singleShot(4000, self._update_login_status)
        else:
            QMessageBox.warning(
                self, "Login failed",
                "Could not start the browser login flow."
            )

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load(self):
        mode = self.config.get("connection_mode")
        index = next((i for i, (_, m) in enumerate(_MODE_LABELS) if m == mode), 0)
        self.mode_combo.setCurrentIndex(index)
        self.stack.setCurrentIndex(index)

        pid = self.config.get("provider")
        idx = self.provider_combo.findData(pid)
        self.provider_combo.setCurrentIndex(max(0, idx))
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
        self.system_edit.setPlainText(self.config.get("system_prompt") or "")
        self.autorun_cb.setChecked(bool(self.config.get("auto_run")))
        self.confirm_dangerous_cb.setChecked(bool(self.config.get("confirm_dangerous_calls")))

        to_val = self.config.get("main_thread_timeout")
        self.timeout_edit.setText("" if to_val is None else str(to_val))
        pt_val = self.config.get("processing_timeout")
        self.proc_timeout_edit.setText("" if pt_val is None else str(pt_val))
        pi_val = self.config.get("mcp_poll_interval")
        self.poll_interval_edit.setText("" if pi_val is None else str(pi_val))

    def _save_and_accept(self):
        mode = _MODE_LABELS[self.mode_combo.currentIndex()][1]

        # Validation
        if mode == config_mod.MODE_API_KEY:
            key = self.api_key_edit.text().strip()
            pid = self.provider_combo.currentData()
            provider_obj = providers.get_provider(pid)
            requires_key = (provider_obj is None) or (provider_obj.get("id") != "ollama")
            if requires_key and not key:
                QMessageBox.warning(
                    self,
                    "API key required",
                    "Please enter an API key for the selected provider.",
                )
                return
        elif mode == config_mod.MODE_CUSTOM:
            url = self.custom_url_edit.text().strip()
            if not url:
                QMessageBox.warning(
                    self,
                    "Base URL required",
                    "Please enter a base URL for the custom endpoint.",
                )
                return

        # Persist
        self.config.set("connection_mode", mode)

        if mode == config_mod.MODE_API_KEY:
            self.config.set("provider", self.provider_combo.currentData())
            self.config.set("api_key", self.api_key_edit.text().strip())
            self.config.set("model", self.model_edit.text().strip())

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

        self.config.set("system_prompt", self.system_edit.toPlainText().strip())
        self.config.set("auto_run", self.autorun_cb.isChecked())
        self.config.set("confirm_dangerous_calls", self.confirm_dangerous_cb.isChecked())

        try:
            self.config.set(
                "main_thread_timeout",
                float(self.timeout_edit.text().strip() or 60),
            )
        except ValueError:
            self.config.set("main_thread_timeout", 60.0)

        try:
            self.config.set(
                "processing_timeout",
                float(self.proc_timeout_edit.text().strip() or 120),
            )
        except ValueError:
            self.config.set("processing_timeout", 120.0)

        try:
            self.config.set(
                "mcp_poll_interval",
                float(self.poll_interval_edit.text().strip() or 0.5),
            )
        except ValueError:
            self.config.set("mcp_poll_interval", 0.5)

        self.accept()
