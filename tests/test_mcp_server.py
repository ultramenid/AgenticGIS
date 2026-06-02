"""Tests for MCP server behavior."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS dependencies before importing server ---
class _MockQThread:
    def __init__(self, parent=None):
        pass
    def run(self):
        pass
    def wait(self, timeout):
        return True

class _QtCore:
    QThread = _MockQThread

class _PyQt:
    QtCore = _QtCore()

class _Qgis:
    PyQt = _PyQt()

sys.modules["qgis"] = _Qgis()
sys.modules["qgis.PyQt"] = _PyQt()
sys.modules["qgis.PyQt.QtCore"] = _QtCore()

# Load mcp_server.py directly to bypass the relative import (..core)
_mcp_server_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "server", "mcp_server.py"
)

with open(_mcp_server_path, "r") as f:
    _source = f.read()

# Strip the relative import line so exec succeeds
_source = _source.replace("from ..core import tools as tools_mod", "# relative import stripped for test")

_namespace = {
    "__name__": "mcp_server",
    "__file__": _mcp_server_path,
}
exec(compile(_source, _mcp_server_path, "exec"), _namespace)
_Handler = _namespace["_Handler"]


def test_max_body_size():
    """Test that MAX_BODY_BYTES is set correctly."""
    assert _Handler.MAX_BODY_BYTES == 10 * 1024 * 1024


def test_handler_class_exists():
    """Test that handler class exists and has required attributes."""
    assert hasattr(_Handler, 'MAX_BODY_BYTES')
    assert hasattr(_Handler, 'protocol_version')
