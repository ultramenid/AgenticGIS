"""Regression checks for settings dialog organization."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel, QLineEdit, QTabBar

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

    # The model picker is a _ModelPickerWidget (custom Select2-style widget).
    from AgenticGis.gui.settings_dialog import _ModelPickerWidget
    assert isinstance(dialog.model_picker, _ModelPickerWidget)
    assert dialog.stack.indexOf(dialog.api_base_url_edit.parentWidget().parentWidget()) == 0
    assert isinstance(dialog.connection_tabs, QTabBar)
    assert dialog.connection_tabs.currentIndex() == 0
    assert dialog.stack.currentIndex() == 0
    assert dialog.connection_tabs.tabText(0).startswith("Active ·")
    assert dialog.connection_tabs.tabText(0) == "Active · API key"

    dialog.connection_tabs.setCurrentIndex(1)
    assert dialog.stack.currentIndex() == 1
    assert dialog.connection_tabs.tabText(0) == "Active · API key"
    assert dialog.connection_tabs.tabText(1) == "Custom"

    dialog.connection_tabs.setCurrentIndex(2)
    assert dialog.stack.currentIndex() == 2
    assert dialog.connection_tabs.tabText(0) == "Active · API key"
    assert dialog.connection_tabs.tabText(2) == "Subscription"

    custom_cfg = _Config()
    custom_cfg.values["connection_mode"] = config_mod.MODE_CUSTOM
    custom_cfg.values["custom_base_url"] = "https://proxy.example.com"
    custom_dialog = SettingsDialog(custom_cfg)
    assert custom_dialog.connection_tabs.tabText(0) == "API key"
    assert custom_dialog.connection_tabs.tabText(1) == "Active · Custom"
    assert custom_dialog.connection_tabs.tabText(2) == "Subscription"
    assert "OpenAI-compatible" not in custom_dialog.connection_tabs.tabText(1)
    custom_dialog.connection_tabs.setCurrentIndex(0)
    assert custom_dialog.connection_tabs.tabText(1) == "Active · Custom"
    custom_dialog.deleteLater()

    dialog.connection_tabs.setCurrentIndex(0)

    dialog.config.values["system_prompt"] = "keep"
    dialog.config.values["auto_run"] = False
    dialog.config.values["confirm_dangerous_calls"] = True
    dialog.model_picker.setCurrentText("model-in-connection")
    dialog.api_base_url_edit.setText("https://proxy.example.com")
    dialog._save_and_accept()
    assert dialog.config.values["model"] == "model-in-connection"
    assert dialog.config.values["api_base_url"] == "https://proxy.example.com"
    assert dialog.config.values["system_prompt"] == "keep"
    assert dialog.config.values["auto_run"] is False
    assert dialog.config.values["confirm_dangerous_calls"] is True

    dialog.deleteLater()
    app.processEvents()

    _test_connect_first_flow(app)


def _test_connect_first_flow(app):
    """Model dropdown stays hidden until a connection test succeeds."""
    cfg = _Config()
    cfg.values["model"] = ""            # no saved model -> hidden on load
    cfg.values["custom_model"] = ""
    dialog = SettingsDialog(cfg)

    # Hidden until a successful test (isHidden tracks our explicit show/hide
    # intent even when the dialog itself was never shown on screen).
    assert dialog.api_model_group.isHidden() is True
    assert dialog.custom_model_group.isHidden() is True

    # A failed test surfaces the reason and keeps the dropdown hidden.
    dialog._on_models_fetched(config_mod.MODE_API_KEY, [], "HTTP 401: bad key")
    assert dialog.api_model_group.isHidden() is True
    assert "Failed" in dialog.api_status.text()
    assert "401" in dialog.api_status.text()

    # A successful test reveals the picker and populates the model list.
    models = ["claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"]
    dialog._on_models_fetched(config_mod.MODE_API_KEY, models, "")
    assert dialog.api_model_group.isHidden() is False
    assert "Connected" in dialog.api_status.text()
    assert dialog.model_picker._models == sorted(models)

    # The user can still set a custom model name directly.
    dialog.model_picker.setCurrentText("my-private-model")
    assert dialog.model_picker.currentText() == "my-private-model"

    # Switching provider hides the dropdown again (forces a re-test).
    dialog.provider_combo.setCurrentIndex(
        1 if dialog.provider_combo.count() > 1 else 0
    )
    if dialog.provider_combo.count() > 1:
        assert dialog.api_model_group.isHidden() is True

    dialog.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
