# Connection Section v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three-mode Settings dialog Connection section with a single "Connect with…" choice, surface an available-models list after a successful connect, and drop the Auto-run checkbox and the Advanced timeouts collapsible.

**Architecture:** A `QObject`-based `_ConnectionController` owns the IDLE/CONNECTED state machine and emits `state_changed`. A new `ModelFetcher(QRunnable)` does the HTTP `/v1/models` call on a `QThreadPool` and posts results back via a `pyqtSignal` to keep the UI thread responsive. The dialog swaps form widgets by hiding/showing groups, not via `QStackedWidget` (simpler given the variable field counts).

**Tech Stack:** Python 3.9 stdlib only (`urllib`, `subprocess`, `shutil`, `threading`), QGIS's bundled PyQt5 (`qgis.PyQt.QtCore`, `qgis.PyQt.QtWidgets`, `qgis.PyQt.QtGui`), pytest.

**Working directory:** `/Users/muhammadalichamdan/Documents/Development/AgenticGis`

**Test command:** `python3 -m pytest tests/ -v` (run from repo root)

---

## File Structure

| File | Responsibility | New / Modified |
|---|---|---|
| `config.py` | Add `connect_with` key; add migration from `connection_mode` | Modify |
| `backends/providers.py` | Add `login_url` per built-in provider | Modify |
| `backends/login_urls.py` | `login_url_for(provider_id) -> str` (single source of truth) | Create |
| `gui/model_fetcher.py` | `ModelFetcher(QRunnable)` + `FetcherSignals(QObject)` | Create |
| `gui/connection_controller.py` | `_ConnectionController(QObject)` state machine | Create |
| `gui/model_card.py` | `ModelCard` clickable card + `AddCustomModelRow` | Create |
| `gui/settings_dialog.py` | Rewrite `_build_ui`, add `_load`, `_save` updates, drop the three panels | Modify |
| `tests/test_config_migration.py` | Migration tests | Create |
| `tests/test_provider_registry.py` | login_url tests | Create |
| `tests/test_model_fetcher.py` | ModelFetcher tests | Create |
| `tests/test_connection_controller.py` | Controller state machine tests | Create |
| `README.md` | One-line changelog note | Modify |

---

## Task 1: Add `login_url` to built-in providers + `login_urls.py`

**Files:**
- Create: `backends/login_urls.py`
- Modify: `backends/providers.py:9-82`
- Test: `tests/test_provider_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provider_registry.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class _MockQSettings:
    def __init__(self): self._store = {}
    def value(self, key, default=None): return self._store.get(key, default)
    def setValue(self, key, value): self._store[key] = value
class _QtCore:
    QSettings = _MockQSettings
class _PyQt: QtCore = _QtCore()
class _Qgis: PyQt = _PyQt()
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
        assert url.startswith("https://"), f"{p['id']} login url not https: {url}"


def test_login_url_for_unknown_provider():
    # Unknown provider falls back to a sensible default (empty string OK,
    # controller disables the button in that case).
    assert login_url_for("does_not_exist") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_provider_registry.py -v`
Expected: ImportError or NameError (no `backends.login_urls` yet).

- [ ] **Step 3: Create `backends/login_urls.py`**

```python
"""Per-provider hosted login / API-key pages.

The "Browser login" connection option opens one of these in the user's
default browser so they can either sign in (OAuth) or grab/create a key.
Add a provider's URL here whenever a new built-in is registered.
"""

LOGIN_URLS = {
    "anthropic":  "https://console.anthropic.com/settings/keys",
    "openai":     "https://platform.openai.com/api-keys",
    "groq":       "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "gemini":     "https://aistudio.google.com/apikey",
    "deepseek":   "https://platform.deepseek.com/api_keys",
    "mistral":    "https://console.mistral.ai/api-keys",
    "xai":        "https://console.x.ai/team/api-keys",
    "ollama":     "",  # local; no hosted login
}


def login_url_for(provider_id):
    """Return the hosted login/key URL for a built-in provider, or ''."""
    return LOGIN_URLS.get(provider_id, "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_provider_registry.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backends/login_urls.py backends/providers.py tests/test_provider_registry.py
git commit -m "feat: add login_url lookup for built-in providers"
```

Note: providers.py does not need modification for these tests to pass; the test imports `all_providers()` and asserts the URL via the new helper. We will, however, expose the same value on each provider dict in a later task when the controller reads it.

---

## Task 2: Expose `login_url` on each provider dict

**Files:**
- Modify: `backends/providers.py:9-82`

- [ ] **Step 1: Write the failing test (extend `tests/test_provider_registry.py`)**

Add this at the bottom of `tests/test_provider_registry.py`:

```python
def test_provider_dict_has_login_url():
    for p in providers.all_providers():
        assert "login_url" in p, f"{p['id']} missing login_url"
        assert isinstance(p["login_url"], str)


def test_provider_login_url_matches_helper():
    for p in providers.all_providers():
        from backends.login_urls import login_url_for
        assert p["login_url"] == login_url_for(p["id"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_provider_registry.py::test_provider_dict_has_login_url -v`
Expected: FAIL (AssertionError on first provider: 'login_url' missing).

- [ ] **Step 3: Add `login_url` to each entry in `backends/providers.py`**

Edit the `BUILT_INS` list (lines 9–82). For every entry, add the key `"login_url"` using the same URL the helper knows about. Example for the first entry:

```python
{
    "id": "anthropic",
    "label": "Anthropic",
    "base_url": "https://api.anthropic.com",
    "format": "anthropic",
    "default_model": "claude-opus-4-8",
    "key_env": "ANTHROPIC_API_KEY",
    "login_url": "https://console.anthropic.com/settings/keys",
},
```

Do the same for every other entry — URLs are listed in `backends/login_urls.py` `LOGIN_URLS`. For `ollama`, set `"login_url": ""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_provider_registry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backends/providers.py tests/test_provider_registry.py
git commit -m "feat: expose login_url on built-in provider dicts"
```

---

## Task 3: Config migration `connection_mode` → `connect_with`

**Files:**
- Modify: `config.py:11-15, 17-42, 45-79`
- Test: `tests/test_config_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_migration.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class _MockQSettings:
    def __init__(self): self._store = {}
    def value(self, key, default=None): return self._store.get(key, default)
    def setValue(self, key, value): self._store[key] = value
class _QtCore: QSettings = _MockQSettings
class _PyQt: QtCore = _QtCore()
class _Qgis: PyQt = _PyQt()
sys.modules["qgis"] = _Qgis()
sys.modules["qgis.PyQt"] = _PyQt
sys.modules["qgis.PyQt.QtCore"] = _QtCore

from config import Config


def _make_config_with_legacy(legacy_mode):
    """Construct a Config whose QSettings already has the legacy key set."""
    from qgis.PyQt.QtCore import QSettings
    c = Config()
    QSettings().setValue("AgenticGIS/connection_mode", legacy_mode)
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
    # After accessing connect_with the legacy key should still be present
    # (we never write to it).
    _ = c.get("connect_with")
    assert QSettings().value("AgenticGIS/connection_mode") == "api_key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config_migration.py -v`
Expected: FAIL (KeyError or default returned: 'connect_with' not in DEFAULTS).

- [ ] **Step 3: Modify `config.py`**

Edit `config.py`:

1. Add the new constant near the top (after `MODE_SUBSCRIPTION` on line 15):
```python
# Connection method chosen in the redesigned Settings dialog.
# Replaces the legacy `connection_mode` key; values are: api_key | custom | cli | browser.
MODE_BROWSER = "browser"
```

2. Add `connect_with` to `DEFAULTS` (in the same dict at line 17):
```python
DEFAULTS = {
    "connect_with": MODE_API_KEY,   # replaces connection_mode
    "connection_mode": MODE_API_KEY,  # legacy; migrated on first read
    ...
}
```

3. Add the migration in `Config.__init__` (right after `self._s = QSettings()` at line 49):
```python
def __init__(self):
    self._s = QSettings()
    self._migrate_legacy_connection_mode()

def _migrate_legacy_connection_mode(self):
    """One-time migration: connection_mode -> connect_with."""
    if "connect_with" in self._s.value("AgenticGIS/connect_with", None) or \
       self._s.value("AgenticGIS/connect_with") is not None:
        return
    legacy = self._s.value("AgenticGIS/connection_mode", None)
    if legacy is None:
        return
    mapping = {
        MODE_API_KEY: MODE_API_KEY,
        MODE_CUSTOM: MODE_CUSTOM,
        MODE_SUBSCRIPTION: "cli",  # renamed
    }
    new = mapping.get(legacy, MODE_API_KEY)
    self._s.setValue("AgenticGIS/connect_with", new)
```

Note: the check `self._s.value("AgenticGIS/connect_with") is not None` is the actual condition. The first `in` check is harmless but redundant — keep just the second:
```python
def _migrate_legacy_connection_mode(self):
    if self._s.value("AgenticGIS/connect_with") is not None:
        return
    legacy = self._s.value("AgenticGIS/connection_mode", None)
    if legacy is None:
        return
    mapping = {
        MODE_API_KEY: MODE_API_KEY,
        MODE_CUSTOM: MODE_CUSTOM,
        MODE_SUBSCRIPTION: "cli",
    }
    new = mapping.get(legacy, MODE_API_KEY)
    self._s.setValue("AgenticGIS/connect_with", new)
```

4. Add `MODE_BROWSER` and `"browser"` to the `mapping` (not strictly necessary but explicit). Final mapping should be:
```python
mapping = {
    MODE_API_KEY: MODE_API_KEY,
    MODE_CUSTOM: MODE_CUSTOM,
    MODE_SUBSCRIPTION: "cli",
    MODE_BROWSER: MODE_BROWSER,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config_migration.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run full suite to confirm nothing else broke**

Run: `python3 -m pytest tests/ -v`
Expected: All previously passing tests still pass (38 + 5 = 43).

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_config_migration.py
git commit -m "feat: migrate legacy connection_mode to connect_with"
```

---

## Task 4: `ModelFetcher` (QRunnable + urllib)

**Files:**
- Create: `gui/model_fetcher.py`
- Test: `tests/test_model_fetcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_fetcher.py
import json
import os
import sys
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS for QRunnable / QObject / pyqtSignal --------------------
class _MockQtCore:
    class QObject:
        def __init__(self, *a, **k): pass
    class QRunnable:
        def __init__(self, *a, **k): pass
    @staticmethod
    def pyqtSignal(*a, **k):
        class _Sig:
            def __init__(self): self._slot = None
            def connect(self, slot): self._slot = slot
            def emit(self, *args):
                if self._slot is not None: self._slot(*args)
        return _Sig()
_PyQt = SimpleNamespace(QtCore=_MockQtCore)
sys.modules["qgis"] = SimpleNamespace(PyQt=_PyQt)
sys.modules["qgis.PyQt"] = _PyQt
sys.modules["qgis.PyQt.QtCore"] = _MockQtCore

# Load module directly so we can bypass the relative import chain.
_fetcher_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "model_fetcher.py",
)
with open(_fetcher_path) as _f:
    _source = _f.read()
_namespace = {"__name__": "model_fetcher", "__file__": _fetcher_path}
exec(compile(_source, _fetcher_path, "exec"), _namespace)
ModelFetcher = _namespace["ModelFetcher"]


def _mock_response(payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    return SimpleNamespace(read=lambda: body, status=status)


def test_fetch_openai_format():
    fetcher = ModelFetcher("https://api.example.com", "key123", token=1)
    with patch("urllib.request.urlopen", return_value=_mock_response({
        "data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]
    })) as mock_urlopen:
        result = {"called": False}
        def on_done(out, err):
            result["called"] = True
            result["out"] = out
            result["err"] = err
        fetcher.signals.done.connect(on_done)
        fetcher.run()
        assert result["called"] is True
        assert result["err"] is None
        assert sorted(result["out"]) == ["gpt-3.5", "gpt-4"]


def test_fetch_handles_404():
    fetcher = ModelFetcher("https://api.example.com", "key123", token=1)
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        "https://api.example.com/v1/models", 404, "Not Found", {}, None
    )):
        result = {"out": None, "err": None}
        fetcher.signals.done.connect(lambda o, e: (result.update(out=o, err=e)))
        fetcher.run()
        assert result["out"] == []  # empty list, not error
        assert result["err"] is None


def test_fetch_handles_401():
    fetcher = ModelFetcher("https://api.example.com", "key123", token=1)
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        "https://api.example.com/v1/models", 401, "Unauthorized", {}, None
    )):
        result = {"out": None, "err": None}
        fetcher.signals.done.connect(lambda o, e: (result.update(out=o, err=e)))
        fetcher.run()
        assert result["out"] is None
        assert "401" in result["err"]


def test_fetch_timeout():
    fetcher = ModelFetcher("https://api.example.com", "key123", token=1)
    with patch("urllib.request.urlopen", side_effect=TimeoutError("boom")):
        result = {"out": None, "err": None}
        fetcher.signals.done.connect(lambda o, e: (result.update(out=o, err=e)))
        fetcher.run()
        assert result["out"] is None
        assert "timed out" in result["err"].lower() or "timeout" in result["err"].lower()


def test_fetch_token_mismatch_drops_result():
    # Token is 1 when run() is called; the controller will see token=2 and
    # drop. We simulate by overriding the controller-side check directly:
    # the run() should emit only if the live token matches.
    fetcher = ModelFetcher("https://api.example.com", "key123", token=2)
    # Set the live token to 1 (simulating a newer fetch):
    fetcher._live_token = lambda: 1
    emitted = {"called": False}
    fetcher.signals.done.connect(lambda *_: emitted.update(called=True))
    with patch("urllib.request.urlopen", return_value=_mock_response(
        {"data": [{"id": "x"}]}
    )):
        fetcher.run()
    assert emitted["called"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_model_fetcher.py -v`
Expected: FileNotFoundError on `exec(open(_fetcher_path))` — file doesn't exist yet.

- [ ] **Step 3: Create `gui/model_fetcher.py`**

```python
"""Background model-list fetcher.

Calls ``GET {base_url}/v1/models`` with a Bearer token and posts results back
to the main thread via a ``pyqtSignal``. Cancellation is cooperative: the
controller holds a live token and the fetcher compares it before emitting.
"""
import json
import urllib.error
import urllib.request

from qgis.PyQt.QtCore import QObject, QRunnable, pyqtSignal


class _FetcherSignals(QObject):
    """Two signals: ``done(models_or_None, error_str_or_None)`` and a
    simple ``finished`` so the QThreadPool can release its slot."""
    done = pyqtSignal(object, object)
    finished = pyqtSignal()


class ModelFetcher(QRunnable):
    """Fetch a model's list from an OpenAI-compatible ``/v1/models`` endpoint.

    Parameters
    ----------
    base_url : str
        e.g. ``https://api.openai.com``
    api_key : str
        Bearer token sent in the ``Authorization`` header.
    token : int
        The token value at the time the fetcher was *created*. The controller
        passes a live token-getter; the fetcher only emits if the live token
        still equals ``token`` at the moment of completion.
    live_token : callable
        Zero-arg callable returning the *current* controller token. Defaults
        to ``lambda: token`` (i.e. never invalidated) for tests.
    timeout : float
        Seconds before the HTTP request is abandoned. Default 10.
    """

    def __init__(self, base_url, api_key, token, live_token=None,
                 timeout=10.0):
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self._creation_token = token
        self._live_token = live_token if live_token is not None else (lambda: token)
        self._timeout = timeout
        self.signals = _FetcherSignals()

    def run(self):
        url = self.base_url.rstrip("/") + "/v1/models"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            # 404 means the endpoint just doesn't expose /v1/models — return
            # an empty list, not an error. Other codes are real errors.
            if e.code == 404:
                self._emit([], None)
            else:
                self._emit(None, f"HTTP {e.code}: {e.reason}")
            return
        except (TimeoutError, urllib.error.URLError) as e:
            self._emit(None, f"Request timed out: {e}")
            return
        except Exception as e:  # network/SSL/etc.
            self._emit(None, f"Request failed: {e}")
            return

        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError) as e:
            self._emit(None, f"Bad response: {e}")
            return

        # OpenAI and Anthropic both use {"data": [{"id": ...}, ...]}
        ids = []
        if isinstance(payload, dict):
            data = payload.get("data") or payload.get("models") or []
            for entry in data:
                if isinstance(entry, dict) and "id" in entry:
                    ids.append(entry["id"])
                elif isinstance(entry, str):
                    ids.append(entry)
        elif isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict) and "id" in entry:
                    ids.append(entry["id"])
                elif isinstance(entry, str):
                    ids.append(entry)

        self._emit(ids, None)

    def _emit(self, models, error):
        if self._live_token() != self._creation_token:
            # Stale: a newer fetch has been kicked off. Drop silently.
            self.signals.finished.emit()
            return
        self.signals.done.emit(models, error)
        self.signals.finished.emit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_model_fetcher.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add gui/model_fetcher.py tests/test_model_fetcher.py
git commit -m "feat: ModelFetcher for /v1/models on a QRunnable"
```

---

## Task 5: `_ConnectionController` state machine

**Files:**
- Create: `gui/connection_controller.py`
- Test: `tests/test_connection_controller.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_connection_controller.py
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class _MockQtCore:
    class QObject:
        def __init__(self, *a, **k): pass
    @staticmethod
    def pyqtSignal(*a, **k):
        class _Sig:
            def __init__(self): self._slot = None
            def connect(self, slot): self._slot = slot
            def emit(self, *args):
                if self._slot is not None: self._slot(*args)
        return _Sig()
_PyQt = SimpleNamespace(QtCore=_MockQtCore)
sys.modules["qgis"] = SimpleNamespace(PyQt=_PyQt)
sys.modules["qgis.PyQt"] = _PyQt
sys.modules["qgis.PyQt.QtCore"] = _MockQtCore

_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "connection_controller.py",
)
with open(_path) as _f:
    _source = _f.read()
_ns = {"__name__": "connection_controller", "__file__": _path}
exec(compile(_source, _path, "exec"), _ns)
ConnectionController = _ns["ConnectionController"]


def test_controller_starts_disconnected():
    c = ConnectionController()
    assert c.is_connected is False


def test_api_key_with_key_connects():
    c = ConnectionController()
    states = []
    c.state_changed.connect(lambda s: states.append(s))
    c.set_api_key(provider_id="anthropic", api_key="sk-123")
    assert c.is_connected is True
    assert states == [True]


def test_api_key_empty_key_disconnects():
    c = ConnectionController()
    c.set_api_key(provider_id="anthropic", api_key="sk-123")
    assert c.is_connected is True
    c.set_api_key(provider_id="anthropic", api_key="")
    assert c.is_connected is False


def test_ollama_does_not_require_key():
    c = ConnectionController()
    c.set_api_key(provider_id="ollama", api_key="")
    assert c.is_connected is True


def test_custom_requires_url_and_key():
    c = ConnectionController()
    c.set_custom(base_url="", api_key="key", fmt="openai")
    assert c.is_connected is False
    c.set_custom(base_url="https://x.example.com", api_key="", fmt="openai")
    assert c.is_connected is False
    c.set_custom(base_url="https://x.example.com", api_key="key", fmt="openai")
    assert c.is_connected is True


def test_cli_with_login_required():
    c = ConnectionController()
    c.set_cli(agent="claude", logged_in=False)
    assert c.is_connected is False
    c.set_cli(agent="claude", logged_in=True)
    assert c.is_connected is True


def test_browser_login_with_provider_connected():
    c = ConnectionController()
    c.set_browser(provider_id="anthropic", logged_in=True)
    assert c.is_connected is True


def test_switching_option_resets_state():
    c = ConnectionController()
    c.set_api_key(provider_id="anthropic", api_key="sk-123")
    assert c.is_connected is True
    c.set_custom(base_url="", api_key="", fmt="openai")
    assert c.is_connected is False


def test_fetch_token_increments_on_change():
    c = ConnectionController()
    t0 = c.fetch_token
    c.set_api_key(provider_id="anthropic", api_key="sk-123")
    t1 = c.fetch_token
    assert t1 > t0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_connection_controller.py -v`
Expected: FileNotFoundError (no `connection_controller.py` yet).

- [ ] **Step 3: Create `gui/connection_controller.py`**

```python
"""Single source of truth for 'is the user connected?' in the dialog.

The dialog updates the controller with the current form state via
``set_api_key`` / ``set_custom`` / ``set_cli`` / ``set_browser``. The
controller emits ``state_changed(bool)`` whenever the answer flips. It
also exposes a ``fetch_token`` int that is incremented on every state
change; ``ModelFetcher`` uses this to drop stale results.
"""
from qgis.PyQt.QtCore import QObject, pyqtSignal


class ConnectionController(QObject):
    def __init__(self):
        super().__init__()
        self._connected = False
        self._fetch_token = 0
        self._current_option = None  # str: "api_key" | "custom" | "cli" | "browser"

    state_changed = pyqtSignal(bool)

    @property
    def is_connected(self):
        return self._connected

    @property
    def fetch_token(self):
        return self._fetch_token

    @property
    def current_option(self):
        return self._current_option

    # ---- update API -----------------------------------------------------
    def set_api_key(self, provider_id, api_key):
        provider = self._provider(provider_id)
        needs_key = provider is None or provider.get("id") != "ollama"
        connected = bool(provider_id) and (not needs_key or bool(api_key.strip()))
        self._update("api_key", connected, {
            "provider_id": provider_id,
            "api_key": api_key,
        })

    def set_custom(self, base_url, api_key, fmt):
        url = (base_url or "").strip()
        key = (api_key or "").strip()
        # Local-style endpoints (ollama, localhost) may not need a key.
        is_local = "localhost" in url or "127.0.0.1" in url
        connected = bool(url) and (is_local or bool(key))
        self._update("custom", connected, {
            "base_url": url,
            "api_key": key,
            "format": fmt,
        })

    def set_cli(self, agent, logged_in):
        connected = bool(agent) and bool(logged_in)
        self._update("cli", connected, {
            "agent": agent,
        })

    def set_browser(self, provider_id, logged_in):
        connected = bool(provider_id) and bool(logged_in)
        self._update("browser", connected, {
            "provider_id": provider_id,
        })

    # ---- internals ------------------------------------------------------
    def _provider(self, provider_id):
        from ..backends.providers import get_provider
        if not provider_id:
            return None
        return get_provider(provider_id)

    def _update(self, option, connected, state):
        if self._current_option != option:
            # Switching option resets the token; old fetches drop.
            self._current_option = option
            self._fetch_token += 1
        if connected != self._connected:
            self._connected = connected
            self._fetch_token += 1
            self.state_changed.emit(connected)
        self._last_state = state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_connection_controller.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add gui/connection_controller.py tests/test_connection_controller.py
git commit -m "feat: ConnectionController state machine"
```

---

## Task 6: `ModelCard` and `AddCustomModelRow` widgets

**Files:**
- Create: `gui/model_card.py`
- (No dedicated unit test for this widget; tested via the dialog in Task 7.)

- [ ] **Step 1: Create `gui/model_card.py`**

```python
"""Clickable model card + inline custom-model row.

A ``ModelCard`` is a small QFrame that emits ``clicked(model_id)`` when
the user clicks anywhere on it. The row of cards sits in a QHBoxLayout
inside a QWidget container owned by the dialog.
"""
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _card_selected_ss():
    return (
        "QFrame {"
        "  background: #1c1c20;"
        "  border: 1px solid #fafafa;"
        "  border-radius: 6px;"
        "  padding: 6px 10px;"
        "  color: #fafafa;"
        "}"
    )


def _card_normal_ss():
    return (
        "QFrame {"
        "  background: #1c1c20;"
        "  border: 1px solid #27272a;"
        "  border-radius: 6px;"
        "  padding: 6px 10px;"
        "  color: #a1a1aa;"
        "}"
        "QFrame:hover {"
        "  border: 1px solid #71717a;"
        "  color: #fafafa;"
        "}"
    )


class ModelCard(QFrame):
    """A clickable card displaying one model id."""

    clicked = pyqtSignal(str)

    def __init__(self, model_id, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(_card_normal_ss())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._label = QLabel(model_id)
        self._label.setStyleSheet("background: transparent;")
        self._label.setWordWrap(False)
        layout.addWidget(self._label)
        self._set_selected(False)

    @property
    def model_id(self):
        return self._model_id

    def set_selected(self, selected):
        self._set_selected(selected)

    def _set_selected(self, selected):
        self._selected = selected
        self.setStyleSheet(_card_selected_ss() if selected else _card_normal_ss())

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self._model_id)
        super().mousePressEvent(ev)


class AddCustomModelRow(QWidget):
    """Inline 'Add custom model' text field + confirm button."""

    confirmed = pyqtSignal(str)  # emits the entered model id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(6)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Custom model id (e.g. my-fine-tune)")
        self._edit.setStyleSheet(
            "QLineEdit {"
            "  background: #1c1c20;"
            "  color: #fafafa;"
            "  border: 1px solid #27272a;"
            "  border-radius: 6px;"
            "  padding: 4px 8px;"
            "}"
        )
        outer.addWidget(self._edit, 1)

        self._ok = QPushButton("✓")
        self._ok.setFixedWidth(32)
        self._ok.setStyleSheet(
            "QPushButton {"
            "  background: #fafafa;"
            "  color: #0a0a0b;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 4px 8px;"
            "  font-weight: bold;"
            "}"
            "QPushButton:hover { background: #e4e4e7; }"
        )
        self._ok.clicked.connect(self._on_confirm)
        outer.addWidget(self._ok)

    def value(self):
        return self._edit.text().strip()

    def clear(self):
        self._edit.clear()

    def _on_confirm(self):
        v = self.value()
        if not v or len(v) > 80 or v != v.strip():
            return
        self.confirmed.emit(v)
```

- [ ] **Step 2: Smoke-test that the module imports (no syntax errors)**

Run: `python3 -c "import ast; ast.parse(open('gui/model_card.py').read())"`
Expected: no output (parses cleanly).

- [ ] **Step 3: Commit**

```bash
git add gui/model_card.py
git commit -m "feat: ModelCard + AddCustomModelRow widgets"
```

---

## Task 7: Rewrite the Settings dialog

This is the largest task. Split into 4 steps for reviewability.

**Files:**
- Modify: `gui/settings_dialog.py:1-751` (substantial rewrite of the Connection section; Behaviour section trims the model echo)

### 7a — Add imports, design tokens, and a scrollable models container

- [ ] **Step 1: Add new imports to the top of `settings_dialog.py`**

Replace the import block (lines 11–29) with:

```python
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.PyQt.QtGui import QDesktopServices

from .connection_controller import ConnectionController
from .model_card import AddCustomModelRow, ModelCard
from .model_fetcher import ModelFetcher
```

- [ ] **Step 2: Run a smoke check (no errors yet, just imports)**

Run: `python3 -c "import ast; ast.parse(open('gui/settings_dialog.py').read())"`
Expected: no output.

### 7b — Add the new constants and replace the Connection section builder

- [ ] **Step 3: Replace `_MODE_LABELS` (lines 37-41) with the new connect_with list**

```python
_CONNECT_WITH_LABELS = [
    ("Custom",         "custom"),
    ("API key",        "api_key"),
    ("Installed CLI",  "cli"),
    ("Browser login",  "browser"),
]
```

- [ ] **Step 4: Add a new constant for the CLI list (already exists as `_CLI_AGENTS`, keep as is)**

No change. (It's at lines 43-48.)

- [ ] **Step 5: Replace `_build_ui`'s Connection section (lines 335-356) with the new layout**

The new code goes in place of the existing block from `layout.addWidget(self._section_header("Connection"))` through the end of the `QStackedWidget` block. Replace it with:

```python
        # ---- Connection section ----
        layout.addWidget(self._section_header("Connection"))
        layout.addWidget(self._separator())

        # Top-level "Connect with" choice.
        connect_row = QHBoxLayout()
        connect_row.setSpacing(8)
        connect_row.addWidget(self._form_label("Connect with:"))

        self.connect_with_combo = _make_combo(QComboBox())
        for label, value in _CONNECT_WITH_LABELS:
            self.connect_with_combo.addItem(label, value)
        self.connect_with_combo.currentIndexChanged.connect(self._on_connect_with_changed)
        connect_row.addWidget(self.connect_with_combo, 1)
        layout.addLayout(connect_row)

        # Container that holds whichever sub-form is active.
        self._forms_host = QWidget()
        self._forms_host.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        self._forms_layout = QVBoxLayout(self._forms_host)
        self._forms_layout.setContentsMargins(0, 6, 0, 6)
        self._forms_layout.setSpacing(0)
        layout.addWidget(self._forms_host)

        # Build the four sub-forms (only one shown at a time).
        self._form_api_key   = self._build_api_key_form()
        self._form_custom    = self._build_custom_form()
        self._form_cli       = self._build_cli_form()
        self._form_browser   = self._build_browser_form()
        for w in (self._form_api_key, self._form_custom, self._form_cli, self._form_browser):
            self._forms_layout.addWidget(w)
        self._form_api_key.setVisible(False)
        self._form_custom.setVisible(False)
        self._form_cli.setVisible(False)
        self._form_browser.setVisible(False)

        # Models section (hidden until state changes to connected).
        self._models_section = self._build_models_section()
        self._models_section.setVisible(False)
        layout.addWidget(self._models_section)

        layout.addSpacing(8)

        # Controller + thread pool.
        self._controller = ConnectionController()
        self._controller.state_changed.connect(self._on_connection_state_changed)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._active_fetcher = None
```

You also need to add `QThreadPool` to the QtWidgets import at the top of the file (Task 7a Step 1) — replace the `from qgis.PyQt.QtWidgets import (...)` block with one that includes `QThreadPool`:

```python
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QThreadPool,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
```

- [ ] **Step 6: Remove the now-orphaned panel builders and the `stack_set` slot**

Delete `_api_key_panel`, `_custom_panel`, `_subscription_panel`, and `stack_set` (lines 448-548). Their replacements are `_build_api_key_form`, `_build_custom_form`, `_build_cli_form`, `_build_browser_form` (added in the next step).

Also delete the `QStackedWidget` import + the old `self.stack` line (already done in Step 5).

### 7c — Add the four new sub-form builders

- [ ] **Step 7: Add four new form-builder methods + an `_on_connect_with_changed` slot + state-change handler + model-fetch trigger**

Insert these methods after `_build_ui` (i.e. between the end of `_build_ui` and the existing `stack_set` slot). Delete the existing `stack_set` and panel builders; these are the only form-related methods left.

```python
    # ------------------------------------------------------------------
    # Sub-form builders (one per "Connect with" option)
    # ------------------------------------------------------------------

    def _build_api_key_form(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.api_provider_combo = _make_combo(QComboBox())
        for p in providers.all_providers():
            self.api_provider_combo.addItem(p["label"], p["id"])
        self.api_provider_combo.currentIndexChanged.connect(self._push_state_api_key)
        form.addRow(self._form_label("Provider:"), self.api_provider_combo)

        self.api_key_edit = _make_input(QLineEdit())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here")
        self.api_key_edit.textChanged.connect(self._debounced_api_key)
        form.addRow(self._form_label("API key:"), self.api_key_edit)

        return w

    def _build_custom_form(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.custom_url_edit = _make_input(QLineEdit())
        self.custom_url_edit.setPlaceholderText("https://api.example.com")
        self.custom_url_edit.textChanged.connect(self._debounced_custom)
        form.addRow(self._form_label("Base URL:"), self.custom_url_edit)

        self.custom_key_edit = _make_input(QLineEdit())
        self.custom_key_edit.setEchoMode(QLineEdit.Password)
        self.custom_key_edit.setPlaceholderText("API key for this endpoint")
        self.custom_key_edit.textChanged.connect(self._debounced_custom)
        form.addRow(self._form_label("API key:"), self.custom_key_edit)

        self.custom_format_combo = _make_combo(QComboBox())
        for label, value in _FORMAT_LABELS:
            self.custom_format_combo.addItem(label, value)
        form.addRow(self._form_label("Wire format:"), self.custom_format_combo)

        return w

    def _build_cli_form(self):
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

        login_row = QHBoxLayout()
        login_row.setSpacing(6)
        self.login_status = QLabel("Not checked")
        self.login_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        login_row.addWidget(self.login_status, 1)
        self.login_browser_btn = QPushButton("Login with Browser")
        self.login_browser_btn.setStyleSheet(_BTN_GHOST_SS)
        self.login_browser_btn.clicked.connect(self._login_browser)
        login_row.addWidget(self.login_browser_btn)
        form.addRow(self._form_label("Login:"), login_row)

        return w

    def _build_browser_form(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        form = QFormLayout(w)
        form.setSpacing(8)
        form.setContentsMargins(0, 8, 0, 4)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.browser_provider_combo = _make_combo(QComboBox())
        for p in providers.all_providers():
            self.browser_provider_combo.addItem(p["label"], p["id"])
        self.browser_provider_combo.currentIndexChanged.connect(self._push_state_browser)
        form.addRow(self._form_label("Provider:"), self.browser_provider_combo)

        self.browser_login_btn = QPushButton("Login with Browser")
        self.browser_login_btn.setStyleSheet(_BTN_GHOST_SS)
        self.browser_login_btn.clicked.connect(self._browser_login)
        form.addRow("", self.browser_login_btn)

        self.browser_paste_label = QLabel("")
        self.browser_paste_label.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        self.browser_paste_label.setVisible(False)
        form.addRow(self.browser_paste_label)

        self.browser_paste_edit = _make_input(QLineEdit())
        self.browser_paste_edit.setEchoMode(QLineEdit.Password)
        self.browser_paste_edit.setPlaceholderText("Paste your API key here")
        self.browser_paste_edit.setVisible(False)
        self.browser_paste_edit.textChanged.connect(self._on_pasted_key)
        form.addRow(self._form_label("Or paste key:"), self.browser_paste_edit)

        return w

    def _build_models_section(self):
        from qgis.PyQt.QtWidgets import QFormLayout
        w = QWidget()
        w.setStyleSheet(f"QWidget {{ background: {_SURFACE}; }}")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(4)

        # Header row: "Models" + status label
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Models")
        title.setStyleSheet(
            f"color: {_TEXT_3}; font-size: 11px; font-weight: bold;"
            f"letter-spacing: 0.5px; background: transparent;"
        )
        header.addWidget(title)
        self._models_status = QLabel("")
        self._models_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
        header.addWidget(self._models_status, 1)
        outer.addLayout(header)

        # Cards (in a horizontal flow layout)
        self._models_flow = QHBoxLayout()
        self._models_flow.setContentsMargins(0, 0, 0, 0)
        self._models_flow.setSpacing(6)
        self._models_flow.addStretch(1)
        outer.addLayout(self._models_flow)

        # Add custom model row
        self._add_custom_btn = QPushButton("+ Add custom model")
        self._add_custom_btn.setStyleSheet(_BTN_GHOST_SS)
        self._add_custom_btn.clicked.connect(self._on_add_custom_clicked)
        outer.addWidget(self._add_custom_btn)

        self._custom_row = AddCustomModelRow()
        self._custom_row.confirmed.connect(self._on_custom_confirmed)
        outer.addWidget(self._custom_row)

        # Selected model label
        self._selected_label = QLabel("Selected: (none)")
        self._selected_label.setStyleSheet(
            f"color: {_TEXT_2}; font-size: 11px; background: transparent;"
        )
        outer.addWidget(self._selected_label)

        return w
```

### 7d — Add slots for state push, fetcher wiring, and behavior

- [ ] **Step 8: Add the slot methods for state push, fetcher wiring, and behavior**

Insert these methods after the sub-form builders (above):

```python
    # ------------------------------------------------------------------
    # Connect-with choice + state push
    # ------------------------------------------------------------------

    def _on_connect_with_changed(self, index):
        option = self.connect_with_combo.itemData(index)
        # Hide all sub-forms, then show the active one.
        for w, key in (
            (self._form_api_key, "api_key"),
            (self._form_custom, "custom"),
            (self._form_cli, "cli"),
            (self._form_browser, "browser"),
        ):
            w.setVisible(key == option)
        # Clear stale model selection when switching option.
        self._clear_model_cards()
        self._models_section.setVisible(False)
        # Push state for the newly-visible option.
        if option == "api_key":
            self._push_state_api_key()
        elif option == "custom":
            self._push_state_custom()
        elif option == "cli":
            self._update_login_status()
        elif option == "browser":
            self._push_state_browser()

    def _debounced_api_key(self):
        if hasattr(self, "_api_key_timer"):
            self._api_key_timer.stop()
        else:
            self._api_key_timer = QTimer(self)
            self._api_key_timer.setSingleShot(True)
            self._api_key_timer.timeout.connect(self._push_state_api_key)
        self._api_key_timer.start(250)

    def _push_state_api_key(self):
        if self.connect_with_combo.currentData() != "api_key":
            return
        pid = self.api_provider_combo.currentData()
        key = self.api_key_edit.text().strip()
        self._controller.set_api_key(provider_id=pid, api_key=key)

    def _debounced_custom(self):
        if hasattr(self, "_custom_timer"):
            self._custom_timer.stop()
        else:
            self._custom_timer = QTimer(self)
            self._custom_timer.setSingleShot(True)
            self._custom_timer.timeout.connect(self._push_state_custom)
        self._custom_timer.start(250)

    def _push_state_custom(self):
        if self.connect_with_combo.currentData() != "custom":
            return
        url = self.custom_url_edit.text().strip()
        key = self.custom_key_edit.text().strip()
        fmt = self.custom_format_combo.currentData() or "openai"
        self._controller.set_custom(base_url=url, api_key=key, fmt=fmt)

    def _push_state_browser(self):
        if self.connect_with_combo.currentData() != "browser":
            return
        pid = self.browser_provider_combo.currentData()
        # If the user has pasted a key in the fallback, treat as logged-in.
        pasted = self.browser_paste_edit.text().strip()
        self._controller.set_browser(provider_id=pid, logged_in=bool(pasted))

    def _on_pasted_key(self):
        self._push_state_browser()

    # ------------------------------------------------------------------
    # CLI panel
    # ------------------------------------------------------------------

    def _on_cli_agent_changed(self, index):
        self._update_login_status()

    def _update_login_status(self):
        from ..backends.cli_backend import _resolve_binary, CliToolBackend

        tool = self.cli_agent_combo.currentData()
        if not tool:
            self.login_status.setText("Pick an agent")
            self.login_status.setStyleSheet(f"color: {_TEXT_3}; background: transparent;")
            self.login_browser_btn.setEnabled(False)
            self._controller.set_cli(agent="", logged_in=False)
            return

        binary = _resolve_binary(tool, "")
        if not binary:
            self.login_status.setText("Binary not found")
            self.login_status.setStyleSheet(f"color: {_DANGER}; background: transparent;")
            self.login_browser_btn.setEnabled(False)
            self._controller.set_cli(agent=tool, logged_in=False)
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

        self._controller.set_cli(agent=tool, logged_in=logged_in)

    def _login_browser(self):
        from ..backends.cli_backend import _resolve_binary, CliToolBackend
        from .cli_login_urls import login_url_for_cli
        tool = self.cli_agent_combo.currentData()
        binary = _resolve_binary(tool, "")
        if not binary:
            QMessageBox.warning(self, "Binary not found",
                f"Could not find the '{tool}' binary.")
            return
        url = login_url_for_cli(tool)
        if url:
            QDesktopServices.openUrl(QUrl(url))
        backend = CliToolBackend(self.config, lambda: None)
        backend.tool = tool
        backend.binary = binary
        backend.login_browser()
        QTimer.singleShot(4000, self._update_login_status)

    # ------------------------------------------------------------------
    # Browser panel
    # ------------------------------------------------------------------

    def _browser_login(self):
        from ..backends.login_urls import login_url_for
        pid = self.browser_provider_combo.currentData()
        url = login_url_for(pid) if pid else ""
        if not url:
            QMessageBox.warning(self, "No login URL",
                "This provider does not have a hosted login page. "
                "Paste your API key below instead.")
            self.browser_paste_edit.setVisible(True)
            self.browser_paste_label.setVisible(True)
            self.browser_paste_label.setText("Paste key:")
            return
        ok = QDesktopServices.openUrl(QUrl(url))
        if not ok:
            QMessageBox.warning(self, "Could not open browser",
                f"Copy this URL and open it manually:\n{url}")
        # Poll for login success for up to 60s.
        self._browser_poll_count = 0
        self._browser_poll_timer = QTimer(self)
        self._browser_poll_timer.timeout.connect(self._browser_poll_tick)
        self._browser_poll_timer.start(2000)

    def _browser_poll_tick(self):
        from ..backends.providers import get_provider
        self._browser_poll_count += 1
        if self._browser_poll_count > 30:
            self._browser_poll_timer.stop()
            self.browser_paste_edit.setVisible(True)
            self.browser_paste_label.setVisible(True)
            self.browser_paste_label.setText("Auto-detect timed out — paste your key:")
            return
        pid = self.browser_provider_combo.currentData()
        provider = get_provider(pid) if pid else None
        if provider and provider.get("id") == "ollama":
            self._browser_poll_timer.stop()
            self._controller.set_browser(provider_id=pid, logged_in=True)
            return
        # For non-ollama: we don't have a generic check; user must paste
        # a key. Reveal the paste field.
        self._browser_poll_timer.stop()
        self.browser_paste_edit.setVisible(True)
        self.browser_paste_label.setVisible(True)
        self.browser_paste_label.setText("Paste your key:")

    # ------------------------------------------------------------------
    # Model cards
    # ------------------------------------------------------------------

    def _clear_model_cards(self):
        # Remove all ModelCard widgets from the flow (keep the trailing stretch).
        for i in reversed(range(self._models_flow.count() - 1)):
            item = self._models_flow.itemAt(i)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._selected_model = None
        self._selected_label.setText("Selected: (none)")

    def _add_model_card(self, model_id, selected=False):
        # Remove trailing stretch temporarily
        stretch_item = self._models_flow.takeAt(self._models_flow.count() - 1)
        card = ModelCard(model_id)
        card.clicked.connect(self._on_card_clicked)
        self._models_flow.addWidget(card)
        self._models_flow.addStretch(1)
        if selected:
            self._select_card(card)

    def _on_card_clicked(self, model_id):
        for i in range(self._models_flow.count() - 1):
            item = self._models_flow.itemAt(i)
            w = item.widget()
            if isinstance(w, ModelCard):
                w.set_selected(w.model_id == model_id)
        self._selected_model = model_id
        self._selected_label.setText(f"Selected: {model_id}")

    def _select_card(self, card):
        for i in range(self._models_flow.count() - 1):
            item = self._models_flow.itemAt(i)
            w = item.widget()
            if isinstance(w, ModelCard):
                w.set_selected(w is card)

    def _on_add_custom_clicked(self):
        self._custom_row.setVisible(True)
        self._custom_row._edit.setFocus()

    def _on_custom_confirmed(self, model_id):
        self._custom_row.setVisible(False)
        self._custom_row.clear()
        self._add_model_card(model_id, selected=True)
        self._selected_model = model_id
        self._selected_label.setText(f"Selected: {model_id}")

    # ------------------------------------------------------------------
    # State-change → model fetch
    # ------------------------------------------------------------------

    def _on_connection_state_changed(self, connected):
        if not connected:
            self._models_section.setVisible(False)
            self._active_fetcher = None
            return
        self._models_section.setVisible(True)
        self._clear_model_cards()
        self._models_status.setText("Loading models…")
        self._kick_off_fetch()

    def _kick_off_fetch(self):
        # Determine base_url and api_key from the active option.
        option = self.connect_with_combo.currentData()
        if option == "api_key":
            pid = self.api_provider_combo.currentData()
            provider = providers.get_provider(pid) if pid else None
            if not provider:
                self._models_status.setText("No provider")
                return
            base = provider["base_url"]
            key = self.api_key_edit.text().strip()
        elif option == "custom":
            base = self.custom_url_edit.text().strip()
            key = self.custom_key_edit.text().strip()
        else:
            # CLI / Browser: no /v1/models — show the CLI's default model.
            self._show_cli_default_model()
            return

        fetcher = ModelFetcher(
            base_url=base,
            api_key=key,
            token=self._controller.fetch_token,
            live_token=lambda: self._controller.fetch_token,
        )
        fetcher.signals.done.connect(self._on_models_fetched)
        self._active_fetcher = fetcher
        self._thread_pool.start(fetcher)

    def _show_cli_default_model(self):
        from ..backends.providers import get_provider
        if self.connect_with_combo.currentData() == "cli":
            tool = self.cli_agent_combo.currentData()
            defaults = {
                "claude": "claude-opus-4-8",
                "opencode": "opencode-default",
                "codex": "codex-default",
                "gemini": "gemini-2.0-flash",
            }
            model_id = defaults.get(tool, "default")
        else:
            # Browser — pick the chosen provider's default.
            pid = self.browser_provider_combo.currentData()
            provider = get_provider(pid) if pid else None
            model_id = provider["default_model"] if provider else "default"
        self._clear_model_cards()
        self._add_model_card(model_id, selected=True)
        self._selected_model = model_id
        self._selected_label.setText(f"Selected: {model_id}")
        self._models_status.setText("")

    def _on_models_fetched(self, models, error):
        if error:
            self._models_status.setText("Models (failed)")
            # Show a retry button via the add-custom path: leave room for it.
            return
        if models is None or models == []:
            self._models_status.setText("Models (none found)")
            return
        self._models_status.setText("")
        for mid in models:
            self._add_model_card(mid)
        # Pre-select the saved model if present, else first.
        saved = self.config.get("model")
        if saved and saved in models:
            for i in range(self._models_flow.count() - 1):
                item = self._models_flow.itemAt(i)
                w = item.widget()
                if isinstance(w, ModelCard) and w.model_id == saved:
                    self._select_card(w)
                    self._selected_model = saved
                    self._selected_label.setText(f"Selected: {saved}")
                    return
        # Otherwise, default to the first one (the API will return a list
        # ordered by the server — usually "best" first).
        if self._models_flow.count() > 1:
            first = self._models_flow.itemAt(0).widget()
            if isinstance(first, ModelCard):
                self._select_card(first)
                self._selected_model = first.model_id
                self._selected_label.setText(f"Selected: {first.model_id}")
```

### 7e — Create the small CLI login-URL helper

- [ ] **Step 9: Create `gui/cli_login_urls.py`**

```python
"""Hosted login pages for the installed CLI tools.

The CLI login button can either spawn the CLI's own ``login`` subcommand
(handled in cli_backend) or open one of these URLs in a browser so the
user can grab a fresh session if their CLI is wedged.
"""

LOGIN_URLS = {
    "claude":   "https://console.anthropic.com/login",
    "opencode": "https://opencode.ai/login",
    "codex":    "https://platform.openai.com/login",
    "gemini":   "https://aistudio.google.com/",
}


def login_url_for_cli(slug):
    """Return the hosted login URL for a CLI agent, or ''."""
    return LOGIN_URLS.get(slug, "")
```

### 7f — Update `_load` and `_save_and_accept`

- [ ] **Step 10: Replace `_load` (lines 642-676) with the new version**

```python
    def _load(self):
        # Connect-with choice
        connect_with = self.config.get("connect_with")
        idx = next((i for i, (_, v) in enumerate(_CONNECT_WITH_LABELS) if v == connect_with), 0)
        self.connect_with_combo.setCurrentIndex(idx)

        # API key form
        pid = self.config.get("provider")
        if pid != "custom":
            api_idx = self.api_provider_combo.findData(pid)
            self.api_provider_combo.setCurrentIndex(max(0, api_idx))
        self.api_key_edit.setText(self.config.get("api_key") or "")

        # Custom form
        self.custom_url_edit.setText(self.config.get("custom_base_url") or "")
        self.custom_key_edit.setText(self.config.get("custom_api_key") or "")
        cfmt = self.config.get("custom_format")
        fidx = next((i for i, (_, v) in enumerate(_FORMAT_LABELS) if v == cfmt), 0)
        self.custom_format_combo.setCurrentIndex(fidx)

        # CLI form
        cli = self.config.get("cli_tool")
        cidx = next((i for i, (s, _) in enumerate(_CLI_AGENTS) if s == cli), 0)
        self.cli_agent_combo.setCurrentIndex(cidx)
        self._update_login_status()

        # Browser form: nothing extra to load; defaults are fine.

        # Behaviour
        self.system_edit.setPlainText(self.config.get("system_prompt") or "")
        self._selected_model = self.config.get("model") or ""
        if self._selected_model:
            self._selected_label.setText(f"Selected: {self._selected_model}")
        self.model_edit.setText(self._selected_model)

        # Trigger the controller so the right form becomes visible
        # and (later) the model section appears if connected.
        self._on_connect_with_changed(idx)
        # If we loaded with a connected state, push it explicitly.
        if self._controller.is_connected:
            self._on_connection_state_changed(True)
```

- [ ] **Step 11: Replace `_save_and_accept` (lines 678-750) with the new version**

```python
    def _save_and_accept(self):
        connect_with = _CONNECT_WITH_LABELS[self.connect_with_combo.currentIndex()][1]

        # Validation per option
        if connect_with == "api_key":
            key = self.api_key_edit.text().strip()
            pid = self.api_provider_combo.currentData()
            provider_obj = providers.get_provider(pid)
            requires_key = (provider_obj is None) or (provider_obj.get("id") != "ollama")
            if requires_key and not key:
                QMessageBox.warning(self, "API key required",
                    "Please enter an API key for the selected provider.")
                return
        elif connect_with == "custom":
            url = self.custom_url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "Base URL required",
                    "Please enter a base URL for the custom endpoint.")
                return
        elif connect_with == "cli":
            if not self._controller.is_connected:
                QMessageBox.warning(self, "CLI not ready",
                    "Pick a CLI agent and make sure it is logged in.")
                return
        elif connect_with == "browser":
            if not self._controller.is_connected:
                QMessageBox.warning(self, "Not connected",
                    "Complete the browser login or paste an API key.")
                return

        # Persist
        self.config.set("connect_with", connect_with)
        if connect_with == "api_key":
            self.config.set("provider", self.api_provider_combo.currentData())
            self.config.set("api_key", self.api_key_edit.text().strip())
        elif connect_with == "custom":
            self.config.set("provider", "custom")
            self.config.set("custom_base_url", self.custom_url_edit.text().strip())
            self.config.set("custom_api_key", self.custom_key_edit.text().strip())
            self.config.set("custom_format", self.custom_format_combo.currentData())
        elif connect_with == "cli":
            self.config.set("cli_tool", self.cli_agent_combo.currentData())
        elif connect_with == "browser":
            self.config.set("provider", self.browser_provider_combo.currentData())

        if self._selected_model:
            self.config.set("model", self._selected_model)

        self.config.set("system_prompt", self.system_edit.toPlainText().strip())
        self.accept()
```

### 7g — Trim the Behaviour section

- [ ] **Step 12: Trim Behaviour — remove Auto-run + Advanced; keep System prompt + Model echo**

Replace the Behaviour block in `_build_ui` (lines 360-407) with:

```python
        # ---- Behaviour section (trimmed) ----
        layout.addWidget(self._section_header("Behaviour"))
        layout.addWidget(self._separator())

        beh_form = QFormLayout()
        beh_form.setSpacing(8)
        beh_form.setContentsMargins(0, 8, 0, 8)
        beh_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.model_edit = _make_input(QLineEdit())
        self.model_edit.setReadOnly(True)
        self.model_edit.setPlaceholderText("(no model selected)")
        beh_form.addRow(self._form_label("Model:"), self.model_edit)

        self.system_edit = QPlainTextEdit()
        self.system_edit.setPlaceholderText("Leave empty for the built-in GIS system prompt.")
        self.system_edit.setFixedHeight(72)
        self.system_edit.setStyleSheet(_INPUT_SS)
        beh_form.addRow(self._form_label("System prompt:"), self.system_edit)

        layout.addLayout(beh_form)

        layout.addSpacing(4)
```

This removes:
- The `auto_run_cb` checkbox.
- The `_CollapsibleSection("Advanced", ...)` block (timeouts).
- The `max_iterations` field (not currently in dialog — confirmed).

### 7h — Wire selected model → model_edit echo

- [ ] **Step 13: Update `_on_card_clicked` and `_on_custom_confirmed` to also update `model_edit`**

In the existing methods (added in Step 8), append the line that updates the echo. Replace `_on_card_clicked`:

```python
    def _on_card_clicked(self, model_id):
        for i in range(self._models_flow.count() - 1):
            item = self._models_flow.itemAt(i)
            w = item.widget()
            if isinstance(w, ModelCard):
                w.set_selected(w.model_id == model_id)
        self._selected_model = model_id
        self._selected_label.setText(f"Selected: {model_id}")
        self.model_edit.setText(model_id)
```

And replace `_on_custom_confirmed`:

```python
    def _on_custom_confirmed(self, model_id):
        self._custom_row.setVisible(False)
        self._custom_row.clear()
        self._add_model_card(model_id, selected=True)
        self._selected_model = model_id
        self._selected_label.setText(f"Selected: {model_id}")
        self.model_edit.setText(model_id)
```

- [ ] **Step 14: Run the full suite to confirm nothing broke**

Run: `python3 -m pytest tests/ -v`
Expected: All previously passing tests still pass; existing `test_config.py` still works (it tests the float/int coercion, not the new `connect_with` key).

- [ ] **Step 15: Smoke test that the module imports without errors**

Run: `python3 -c "import ast; ast.parse(open('gui/settings_dialog.py').read())"`
Expected: no output (parses cleanly).

- [ ] **Step 16: Commit**

```bash
git add gui/settings_dialog.py gui/cli_login_urls.py
git commit -m "feat: rewrite Settings dialog Connection section (v2)"
```

---

## Task 8: Manual QA pass + README note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README "Connection modes" section**

Replace the lines `## Connection modes (Settings → Connect via)` (lines 39-46) with:

```markdown
## Connection modes (Settings → Connect with)

1. **Custom** — paste your own base URL + API key + wire format.
2. **API key** — pick a built-in provider (Anthropic, OpenAI, Groq, …) and paste a key.
3. **Installed CLI** — use an already-logged-in agent (Claude Code / OpenCode / Codex / Gemini CLI). No key needed.
4. **Browser login** — opens the provider's hosted login page; paste a key if you prefer.

After connecting, a Models list appears with every model available at your endpoint, plus an **Add custom model** option.
```

- [ ] **Step 2: Run the full test suite one more time**

Run: `python3 -m pytest tests/ -v`
Expected: 38 + 5 (provider) + 5 (config migration) + 5 (model_fetcher) + 9 (controller) = 62 tests pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README connection modes section updated for v2"
```

---

## Self-review

**Spec coverage**:
- Single "Connect with…" choice → Tasks 1, 2, 7 (Step 5, Step 7).
- Model list after connect → Tasks 4, 5, 6, 7 (Step 7).
- Remove Auto-run + Advanced timeouts → Task 7 Step 12.
- Remove binary path field → Tasks 7 Step 5 + 7 Step 7 (no `cli_path_edit` in the new `_build_cli_form`).
- Custom (API key + base URL + SDK compat) → Task 7 Step 7 (`_build_custom_form`).
- Available models list + custom add → Task 6, Task 7 Step 7 (`_build_models_section`).
- Per-provider browser login adaptation → Task 1, Task 7 Step 8 (`_browser_login` uses `login_url_for`).
- Migration `connection_mode` → `connect_with` → Task 3.
- Error handling (no silent failures) → Task 7 Step 8 (`_on_models_fetched`, `_browser_poll_tick`).
- Async safety (`fetch_token`) → Task 4 Step 3, Task 5 Step 3.
- Manual QA checklist → Task 8 (covered in plan; not a hard test).

**Placeholder scan**: No "TBD" / "TODO" / "implement later". All code is concrete and shown.

**Type consistency**:
- `ConnectionController.set_api_key(provider_id, api_key)` — used in Task 7 Step 8.
- `ConnectionController.set_custom(base_url, api_key, fmt)` — used in Task 7 Step 8.
- `ConnectionController.set_cli(agent, logged_in)` — used in Task 7 Step 8.
- `ConnectionController.set_browser(provider_id, logged_in)` — used in Task 7 Step 8.
- `ConnectionController.is_connected` — used in `_on_connection_state_changed`, `_load`, `_save_and_accept`.
- `ConnectionController.fetch_token` — used in `_kick_off_fetch`.
- `ModelFetcher(base_url, api_key, token, live_token, timeout)` — Task 4 Step 3.
- `ModelFetcher.signals.done(models, error)` — Task 4 Step 3, Task 7 Step 8.
- `_FetcherSignals.done`, `_FetcherSignals.finished` — both defined and used in Task 4.
- `ModelCard(model_id).clicked(str)` — Task 6, Task 7 Step 8.
- `AddCustomModelRow.confirmed(str)` — Task 6, Task 7 Step 8.
- `login_url_for(provider_id) -> str` — Task 1, Task 7 Step 8.
- `login_url_for_cli(slug) -> str` — Task 7 Step 9, Task 7 Step 8.

All names and signatures match across tasks.
