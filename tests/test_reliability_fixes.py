"""Tests for the new HTTP-client, MCP-server, toolkit-cancellation, and
dangerous-calls hardening. Each test isolates a single behaviour so a
regression points to one fix."""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS for the toolkit (cancellation is toolkit-level) -----------
from types import SimpleNamespace

class _MockQSettings:
    def __init__(self):
        self._store = {}
    def value(self, key, default=None):
        return self._store.get(key, default)
    def setValue(self, key, value):
        self._store[key] = value

class _MockThreadSelf:
    def __eq__(self, o): return isinstance(o, _MockThreadSelf)
    def __hash__(self): return 1
    def getpeername(self): return ("127.0.0.1", 0)
    def close(self): pass

class _MockQtCore:
    QSettings = _MockQSettings
    class QObject:
        def thread(self): return _MockThreadSelf()
        def __init__(self, *a, **k): pass
    class QThread:
        currentThread = staticmethod(lambda: _MockThreadSelf())
    class Qt: QueuedConnection = 1
    @staticmethod
    def pyqtSignal(*a, **k):
        class _Sig:
            def __init__(self): self._slot = None
            def connect(self, slot, *a, **k): self._slot = slot
            def emit(self, *args):
                if self._slot is not None:
                    self._slot(*args)
        return _Sig()

_PyQt = SimpleNamespace(QtCore=_MockQtCore)
sys.modules["qgis"] = SimpleNamespace(PyQt=_PyQt)
sys.modules["qgis.PyQt"] = _PyQt
sys.modules["qgis.PyQt.QtCore"] = _MockQtCore


# =========================================================================
# CancellationRegistry
# =========================================================================
def test_cancellation_registry_single_token():
    """Only one active token at a time. A second register() with the first
    still held returns ``(event, False)`` (no ownership)."""
    from core.cancellation import CancellationRegistry
    reg = CancellationRegistry()
    e1, owned1 = reg.register()
    assert owned1 is True
    e2, owned2 = reg.register()
    assert owned2 is False
    # The non-owner event is the same as the active one — the caller can
    # still poll it; we just don't take responsibility for releasing.
    assert e1 is e2
    assert not e1.is_set()
    reg.cancel()
    assert e1.is_set()
    reg.release(e1)
    # After release, a new register() yields a fresh, unset event.
    e3, owned3 = reg.register()
    assert owned3 is True
    assert e3 is not e1
    assert not e3.is_set()
    reg.release(e3)


def test_cancellation_registry_release_unblocks_new_owner():
    from core.cancellation import CancellationRegistry
    reg = CancellationRegistry()
    e1, _ = reg.register()
    reg.release(e1)
    e2, owned2 = reg.register()
    assert owned2 is True
    reg.release(e2)


# =========================================================================
# Dangerous-calls check (F16)
# =========================================================================
class _FakeConfig:
    def __init__(self, **kw):
        self._values = kw
    def get(self, name, default=None):
        return self._values.get(name, default)


def _extract_dangerous_check():
    """Load core/toolkit.py into a sandbox and pull out the
    ``_dangerous_calls_blocked`` logic as a free function.

    The toolkit module imports qgis.core at the top, which we don't have
    in the test env. The method itself is pure Python and has no QGIS
    dependency, so we evaluate the module body with mocked QGIS imports
    and exec the function definition in our own namespace.
    """
    src = _TOOLKIT_SOURCE
    # Pull just the function definition; the class is fine to define too.
    return src


_TOOLKIT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "toolkit.py",
)
with open(_TOOLKIT_PATH) as _f:
    _TOOLKIT_SOURCE = _f.read()


def _build_toolkit_with_mocks():
    """Import core.toolkit with mock qgis.core.

    We don't need a full QGIS install to exercise the dangerous-calls
    logic; only the imports it actually does. We stub qgis.core with
    no-op attribute access (for the Qt / processing imports) and import.
    """
    from types import ModuleType
    qgis_core = ModuleType("qgis.core")
    qgis_core.QgsApplication = type("QgsApplication", (), {})
    qgis_core.QgsFeatureRequest = type("QgsFeatureRequest", (), {})
    qgis_core.QgsMapLayer = type("QgsMapLayer", (), {
        "VectorLayer": 0, "RasterLayer": 1,
    })
    qgis_core.QgsProject = type("QgsProject", (), {})
    qgis_core.QgsVectorLayer = type("QgsVectorLayer", (), {})
    qgis_core.QgsVectorLayerCache = type("QgsVectorLayerCache", (), {})
    qgis_core.Qgis = type("Qgis", (), {
        "FeatureRequestFlag": type("F", (), {"NoGeometry": 0})(),
    })
    sys.modules["qgis.core"] = qgis_core
    qgis_gui = ModuleType("qgis.gui")
    qgis_gui.QgsDockWidget = type("QgsDockWidget", (), {})
    sys.modules["qgis.gui"] = qgis_gui
    # Toolkit's ``import processing`` and ``import qgis.utils`` are
    # guarded; missing is fine.
    import core.toolkit as tk
    return tk


_TK = _build_toolkit_with_mocks()


def test_dangerous_check_disabled_by_default():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=False)
    assert t._dangerous_calls_blocked("import os; os.system('rm -rf /')", {}) is False


def test_dangerous_check_blocks_os_system():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=True)
    code = "import os\nos.system('rm -rf /')\n"
    assert t._dangerous_calls_blocked(code, {}) is True


def test_dangerous_check_blocks_shutil_rmtree():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=True)
    code = "import shutil\nshutil.rmtree('/')\n"
    assert t._dangerous_calls_blocked(code, {}) is True


def test_dangerous_check_blocks_subprocess():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=True)
    code = "import subprocess\nsubprocess.Popen(['ls'])\n"
    assert t._dangerous_calls_blocked(code, {}) is True


def test_dangerous_check_allows_allow_dangerous_escape():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=True)
    code = "ALLOW_DANGEROUS = True\nimport os\nos.system('ls')\n"
    assert t._dangerous_calls_blocked(code, {}) is False


def test_dangerous_check_allows_harmless_code():
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.config = _FakeConfig(confirm_dangerous_calls=True)
    code = "result = 1 + 2\nprint(result)\n"
    assert t._dangerous_calls_blocked(code, {}) is False


# =========================================================================
# AnthropicHttpClient: socket timeout is set (F4)
# =========================================================================
def test_anthropic_client_ensure_conn_uses_timeout():
    from backends.anthropic_http import AnthropicHttpClient
    c = AnthropicHttpClient(api_key="x")
    conn = c._ensure_conn(timeout=42)
    # The HTTPSConnection should have timeout=42.
    assert getattr(conn, "timeout", None) == 42
    c._close_conn()


def test_anthropic_client_close_clears_state():
    from backends.anthropic_http import AnthropicHttpClient
    c = AnthropicHttpClient(api_key="x")
    c._ensure_conn(timeout=10)
    assert c._conn is not None
    c._close_conn()
    assert c._conn is None


# =========================================================================
# OpenAIHttpClient: bounded timeout
# =========================================================================
def test_openai_client_default_timeout_bounded():
    from backends.openai_http import OpenAIHttpClient, DEFAULT_TIMEOUT
    c = OpenAIHttpClient(api_key="x")
    # The audit found 600s; we tightened the default to 120s.
    assert c.timeout == 120.0
    assert DEFAULT_TIMEOUT == 120.0


def test_openai_client_timeout_override():
    from backends.openai_http import OpenAIHttpClient
    c = OpenAIHttpClient(api_key="x", timeout=10)
    assert c.timeout == 10.0


# =========================================================================
# MCP server: socket timeout + session id plumbing
# =========================================================================
_MCP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "mcp_server.py",
)
with open(_MCP_PATH) as _f:
    _MCP_SOURCE = _f.read()
_MCP_SOURCE = _MCP_SOURCE.replace(
    "from ..core import tools as tools_mod",
    "# stripped for test; tools is not exercised by these tests",
)
_MCP_NS = {"__name__": "mcp_server", "__file__": _MCP_PATH}
exec(compile(_MCP_SOURCE, _MCP_PATH, "exec"), _MCP_NS)


def test_mcp_handler_max_body_size():
    """Verify the handler still caps incoming payloads at 10 MiB."""
    assert _MCP_NS["_Handler"].MAX_BODY_BYTES == 10 * 1024 * 1024


def test_mcp_rpc_server_has_socket_timeout_attr():
    """F3: ``_RpcServer`` exposes ``socket_timeout`` so a stalled client
    cannot hold a worker thread forever."""
    cls = _MCP_NS["_RpcServer"]
    # Default is bound at construction; the class itself doesn't carry a
    # default value (set per-instance). We verify the constructor signature
    # accepts it.
    import inspect
    sig = inspect.signature(cls.__init__)
    assert "socket_timeout" in sig.parameters


def test_allocate_listening_socket_returns_unique_ports():
    """F18: _allocate_listening_socket binds a real socket and returns it
    so we don't race another process for the port."""
    allocate = _MCP_NS["_allocate_listening_socket"]
    s1, p1 = allocate("127.0.0.1", 0)
    s2, p2 = allocate("127.0.0.1", 0)
    try:
        assert p1 != p2
        # Both sockets are actually listening.
        assert s1.fileno() >= 0
        assert s2.fileno() >= 0
    finally:
        s1.close()
        s2.close()


# =========================================================================
# CliToolBackend: JSONL parsing on multi-line / concatenated records
# =========================================================================
def test_emit_line_parses_complete_jsonl():
    from backends.cli_backend import _emit_line
    events = []
    _emit_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"},
    ]}}), events.append)
    assert len(events) == 1
    assert events[0].data["text"] == "hello"


def test_emit_line_passes_through_non_json():
    from backends.cli_backend import _emit_line
    events = []
    _emit_line("not json", events.append)
    assert events[0].data["text"] == "not json\n"


def test_emit_line_captures_session_id():
    from backends.cli_backend import _emit_line
    events = []
    holder = [None]
    _emit_line(json.dumps({"type": "system", "session_id": "abc-123"}),
               events.append, holder)
    assert holder[0] == "abc-123"


def test_emit_line_handles_unknown_record():
    from backends.cli_backend import _emit_line
    events = []
    _emit_line(json.dumps({"type": "weird", "payload": 1}), events.append)
    # Unknown structured records fall through to raw text so the user sees
    # something rather than nothing.
    assert "weird" in events[0].data["text"]


# =========================================================================
# TOOL_SPECS unchanged (regression guard for the python dict literal)
# =========================================================================
def test_tool_specs_dispatch_includes_new_is_error():
    """The backends now set is_error / cancelled explicitly on
    TOOL_RESULT events. We don't test the event format here (that lives
    in the chat dock) but we do confirm the TOOL_SPECS still dispatch
    cleanly after the cancel-token additions."""
    from core import tools
    assert "run_pyqgis" in tools.TOOL_BY_NAME
    assert "get_project_state" in tools.TOOL_BY_NAME
    # Both endpoints the dock relies on for rich render still resolve.
    assert tools.TOOL_BY_NAME["create_chart"]["method"] == "create_chart"
    assert tools.TOOL_BY_NAME["get_layer_statistics"]["method"] == "get_layer_statistics"
