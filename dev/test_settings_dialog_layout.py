"""Regression checks for settings dialog organization."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel, QLineEdit

from AgenticGis import config as config_mod
from AgenticGis.gui.settings_dialog import SettingsDialog


class _Config:
    def __init__(self):
        self.values = dict(config_mod.DEFAULTS)

    def get(self, name, default=None):
        if default is None:
            default = config_mod.DEFAULTS.get(name)
        return self.values.get(name, default)

    def set(self, name, value):
        self.values[name] = value


def _label_texts(dialog):
    return "\n".join(label.text() for label in dialog.findChildren(QLabel))


def main():
    app = QApplication.instance() or QApplication([])
    cfg = _Config()
    cfg.values["api_key"] = "test-key"
    dialog = SettingsDialog(cfg)
    labels = _label_texts(dialog)

    assert "CONNECTION" in labels
    assert "BEHAVIOUR" not in labels
    assert "BEHAVIOR" not in labels
    assert "DEVELOPER" not in labels
    assert "Enable dev logging" not in labels
    assert "System prompt:" not in labels

    model_edits = [
        widget for widget in dialog.findChildren(QLineEdit)
        if widget is dialog.model_edit
    ]
    assert len(model_edits) == 1
    assert dialog.stack.indexOf(dialog.model_edit.parentWidget()) == 0
    assert dialog.stack.indexOf(dialog.api_base_url_edit.parentWidget()) == 0

    dialog.config.values["system_prompt"] = "keep"
    dialog.config.values["auto_run"] = False
    dialog.config.values["confirm_dangerous_calls"] = True
    dialog.model_edit.setText("model-in-connection")
    dialog.api_base_url_edit.setText("https://proxy.example.com")
    dialog._save_and_accept()
    assert dialog.config.values["model"] == "model-in-connection"
    assert dialog.config.values["api_base_url"] == "https://proxy.example.com"
    assert dialog.config.values["system_prompt"] == "keep"
    assert dialog.config.values["auto_run"] is False
    assert dialog.config.values["confirm_dangerous_calls"] is True

    dialog.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
