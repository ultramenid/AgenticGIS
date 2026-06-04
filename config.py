"""Persistent configuration for AgenticGIS, backed by ``QSettings``.

All settings live under the ``AgenticGIS/`` group so they survive QGIS
restarts and are scoped to the plugin.
"""

from qgis.PyQt.QtCore import QSettings

_GROUP = "AgenticGIS"

# Connection modes the user can pick in the Settings dialog.
MODE_CLI_TOOL = "cli_tool"          # use an installed, already-logged-in agent CLI
MODE_API_KEY = "api_key"            # talk to the provider API directly with a key
MODE_CUSTOM = "custom"              # any OpenAI-compatible or Anthropic-compatible endpoint
MODE_SUBSCRIPTION = "subscription"  # OAuth / bearer-token session via ANTHROPIC_AUTH_TOKEN (no API key needed)

DEFAULTS = {
    "connection_mode": MODE_API_KEY,
    # CLI-tool mode
    "cli_tool": "claude",          # "claude" | "opencode"
    "cli_path": "",                # explicit binary path; empty => auto-detect on PATH
    # API / subscription mode
    "provider": "anthropic",
    "model": "claude-opus-4-8",
    "api_key": "",
    "api_base_url": "",          # override built-in provider base URL in API-key mode
    # Custom endpoint (when provider == "custom")
    "custom_base_url": "",
    "custom_api_key": "",       # separate from api_key (API-key mode)
    "custom_format": "openai",
    "custom_model": "",
    # Behaviour
    "system_prompt": "",           # empty => built-in default
    "auto_run": True,              # execute generated PyQGIS without confirmation
    "max_iterations": 0,           # 0 or less => unlimited agent tool-use loop
    # When True, run_pyqgis refuses (returns an error) if agent code calls a
    # "dangerous" builtin — os.system, subprocess, shutil.rmtree, ctypes, etc.
    # Users can opt out per-call by setting ``ALLOW_DANGEROUS = True`` at the
    # top of their code.
    "confirm_dangerous_calls": False,
    # User can choose "Always allow" in the external access permission popup.
    # This permits future file/path/URL/database access without prompting.
    "external_access_always_allowed": False,
    # Local MCP bridge (used by CLI-tool mode)
    "mcp_host": "127.0.0.1",
    "mcp_port": 0,                 # 0 => pick a free port at runtime
    # Performance
    "main_thread_timeout": 60.0,      # seconds for main-thread operations
    "processing_timeout": 0.0,        # 0 or less => unlimited processing task setup wait
    "mcp_poll_interval": 0.5,         # seconds for MCP server poll interval
}


class Config:
    """Thin typed wrapper around ``QSettings`` for plugin options."""

    def __init__(self):
        self._s = QSettings()

    def _key(self, name):
        return f"{_GROUP}/{name}"

    def get(self, name, default=None):
        if default is None:
            default = DEFAULTS.get(name)
        value = self._s.value(self._key(name), default)
        # QSettings stores everything as strings on some platforms; coerce
        # back to the type of the default so callers get real bools/ints.
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        return value

    def set(self, name, value):
        self._s.setValue(self._key(name), value)

    def all(self):
        return {name: self.get(name) for name in DEFAULTS}
