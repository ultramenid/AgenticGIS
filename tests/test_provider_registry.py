"""Tests for the provider registry + per-provider login URL lookup."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS dependencies before importing config ---
class _MockQSettings:
    def __init__(self): self._store = {}
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

from backends import providers
from backends.login_urls import login_url_for


def test_login_url_for_known_provider():
    assert login_url_for("anthropic") == "https://console.anthropic.com/settings/keys"


def test_login_url_for_all_builtins():
    for p in providers.all_providers():
        url = login_url_for(p["id"])
        # Empty string is allowed for local providers (e.g. ollama) that
        # have no hosted login page; otherwise the URL must be https.
        assert url == "" or url.startswith("https://"), (
            f"{p['id']} login url not https: {url}"
        )


def test_login_url_for_unknown_provider():
    # Unknown provider falls back to a sensible default (empty string OK,
    # controller disables the button in that case).
    assert login_url_for("does_not_exist") == ""
