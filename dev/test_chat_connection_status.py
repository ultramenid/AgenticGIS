"""Regression checks for active connection status in the chat header."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis import config as config_mod
from AgenticGis.gui.chat_dock import ChatDock


class _Config:
    def __init__(self, values):
        self.values = dict(config_mod.DEFAULTS)
        self.values.update(values)

    def get(self, name, default=None):
        if default is None:
            default = config_mod.DEFAULTS.get(name)
        return self.values.get(name, default)


class _Backend:
    def __init__(self, values):
        self.config = _Config(values)


def main():
    app = QApplication.instance() or QApplication([])

    backend = _Backend({
        "connection_mode": config_mod.MODE_API_KEY,
        "provider": "anthropic",
        "model": "claude-opus-4-8",
    })
    dock = ChatDock(lambda: backend, lambda: None, lambda: None)
    assert "API key" in dock._connection_chip.toolTip()
    assert "Anthropic" in dock._connection_chip.toolTip()
    assert "claude-opus-4-8" in dock._connection_chip.toolTip()

    backend.config.values.update({
        "connection_mode": config_mod.MODE_CUSTOM,
        "custom_format": "openai",
        "custom_model": "gpt-custom",
        "model": "gpt-custom",
    })
    dock._refresh_connection_status()
    assert "Custom" in dock._connection_chip.toolTip()
    assert "OpenAI" in dock._connection_chip.toolTip()
    assert "gpt-custom" in dock._connection_chip.toolTip()

    backend.config.values.update({
        "connection_mode": config_mod.MODE_SUBSCRIPTION,
        "cli_tool": "codex",
    })
    dock._refresh_connection_status()
    assert "Subscription" in dock._connection_chip.toolTip()
    assert "codex" in dock._connection_chip.toolTip()

    dock._get_backend = lambda: None
    dock._refresh_connection_status()
    assert dock._connection_chip.toolTip() == "No active connection"

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
