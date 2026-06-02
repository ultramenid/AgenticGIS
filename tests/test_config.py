"""Tests for Config class."""
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS dependencies before importing config ---
class _MockQSettings:
    def __init__(self):
        self._store = {}
    def value(self, key, default=None):
        return self._store.get(key, default)
    def setValue(self, key, value):
        self._store[key] = value

class _QtCore:
    QSettings = _MockQSettings

class _PyQt:
    QtCore = _QtCore()

class _Qgis:
    PyQt = _PyQt()

sys.modules["qgis"] = _Qgis()
sys.modules["qgis.PyQt"] = _PyQt()
sys.modules["qgis.PyQt.QtCore"] = _QtCore()

from config import Config, DEFAULTS


def test_config_float_coercion():
    """Test that float values are properly coerced from QSettings strings."""
    c = Config()
    c.set("main_thread_timeout", "120.5")
    assert c.get("main_thread_timeout") == 120.5


def test_config_float_fallback():
    """Test that invalid float values fall back to defaults."""
    c = Config()
    c.set("main_thread_timeout", "not_a_number")
    assert c.get("main_thread_timeout") == 60.0  # default


def test_config_int_coercion():
    """Test that int values are properly coerced."""
    c = Config()
    c.set("max_iterations", "50")
    assert c.get("max_iterations") == 50


def test_config_defaults():
    """Test that all new performance defaults exist."""
    assert "main_thread_timeout" in DEFAULTS
    assert "processing_timeout" in DEFAULTS
    assert "mcp_poll_interval" in DEFAULTS
    assert DEFAULTS["main_thread_timeout"] == 60.0
    assert DEFAULTS["processing_timeout"] == 120.0
    assert DEFAULTS["mcp_poll_interval"] == 0.5
