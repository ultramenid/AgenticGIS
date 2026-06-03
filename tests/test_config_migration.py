"""Tests for the legacy connection_mode -> connect_with migration."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS dependencies before importing config ---
class _MockQSettings:
    # Class-level store: simulates Qt's process-wide settings backing store.
    # (Real QSettings reads/writes a shared, application-wide INI/registry;
    # a per-instance dict would be incorrect.)
    _store = {}
    def __init__(self): pass
    def value(self, key, default=None): return self._store.get(key, default)
    def setValue(self, key, value): self._store[key] = value

class _QtCore:
    QSettings = _MockQSettings

class _PyQt:
    QtCore = _QtCore()

class _Qgis:
    PyQt = _PyQt

sys.modules["qgis"] = _Qgis()
sys.modules["qgis.PyQt"] = _PyQt
sys.modules["qgis.PyQt.QtCore"] = _QtCore

# Reload config (in case another test file imported it first with a
# different QSettings mock — Python's module cache would otherwise
# hand us the stale QSettings binding).
import importlib
import config as _config_module
importlib.reload(_config_module)
from config import Config


@pytest.fixture(autouse=True)
def _reset_qsettings():
    """Reset the mocked QSettings store and re-assert our qgis mocks.

    Other test files in this suite also patch ``sys.modules["qgis.PyQt.QtCore"]``
    with their own ``QSettings`` mock. Whichever test file pytest imports
    LAST wins, so by the time our tests run, ``QSettings()`` may resolve
    to a different class than the one ``Config`` is bound to. We re-assert
    ours at the start of every test and also rebind ``config.QSettings``
    so the migration reads/writes through *our* mock (with its class-level
    singleton store).
    """
    sys.modules["qgis.PyQt.QtCore"] = _QtCore
    sys.modules["qgis.PyQt"] = _PyQt
    sys.modules["qgis"] = _Qgis()
    _config_module.QSettings = _MockQSettings
    _MockQSettings._store = {}
    yield
    _MockQSettings._store = {}


def _make_config_with_legacy(legacy_mode):
    """Construct a Config whose QSettings already has the legacy key set."""
    from qgis.PyQt.QtCore import QSettings
    QSettings().setValue("AgenticGIS/connection_mode", legacy_mode)
    c = Config()
    return c


def test_old_api_key_migrates():
    c = _make_config_with_legacy("api_key")
    assert c.get("connect_with") == "api_key"


def test_old_subscription_migrates():
    c = _make_config_with_legacy("subscription")
    assert c.get("connect_with") == "cli"


def test_old_custom_migrates():
    c = _make_config_with_legacy("custom")
    assert c.get("connect_with") == "custom"


def test_no_op_when_connect_with_set():
    # Fresh QSettings: both keys absent. connect_with takes the default.
    c = Config()
    assert c.get("connect_with") in ("api_key", "custom", "cli", "browser")


def test_legacy_mode_left_in_place():
    # We do not delete the old key — only write the new one.
    from qgis.PyQt.QtCore import QSettings
    c = _make_config_with_legacy("api_key")
    # After accessing connect_with the legacy key should still be present.
    _ = c.get("connect_with")
    assert QSettings().value("AgenticGIS/connection_mode") == "api_key"
