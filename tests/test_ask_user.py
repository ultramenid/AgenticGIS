"""Tests for the toolkit.ask_user() method (clarifying-question flow)."""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- Minimal QGIS mock ---------------------------------------------------- #
class _MockQtCore:
    class QObject:
        def __init__(self, *a, **k):
            pass

    class QThread:
        @staticmethod
        def currentThread():
            class _T:
                def __eq__(self, o):
                    return True

                def __hash__(self):
                    return 1

            return _T()


_PyQt = SimpleNamespace()
sys.modules.setdefault("qgis", SimpleNamespace(PyQt=_PyQt))
sys.modules.setdefault("qgis.PyQt", _PyQt)
sys.modules.setdefault("qgis.PyQt.QtCore", _MockQtCore)


def _qgis_class_factory(name):
    def _factory(*a, **k):
        return SimpleNamespace()
    _factory.__name__ = name
    return _factory


_qgis_core_attrs = [
    "Qgis", "QgsApplication", "QgsFeatureRequest", "QgsMapLayer",
    "QgsProject", "QgsVectorLayer", "QgsVectorLayerCache", "QgsFeedback",
]
_qgis_core = SimpleNamespace(**{n: _qgis_class_factory(n) for n in _qgis_core_attrs})
sys.modules["qgis.core"] = _qgis_core
sys.modules["qgis.gui"] = SimpleNamespace(
    QgsDockWidget=type("QgsDockWidget", (), {})
)


def _load_toolkit_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "core.toolkit",
        os.path.join(os.path.dirname(__file__), "..", "core", "toolkit.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TK = _load_toolkit_module()


def _fresh_toolkit():
    """Build a bare QgisToolkit with no iface/config (we only test ask_user)."""
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.iface = SimpleNamespace()
    t.config = None
    t._ns_template = None
    t._canvas_dirty = False
    t._alg_cache = None
    t._cancel = _TK._CancellationRegistry()
    t._ask_emitter = None
    t._ask_user_lock = threading.Lock()
    t._ask_user_pending = None
    return t


def test_ask_user_returns_choice():
    """ask_user blocks until the emitter fires the event with a payload."""
    tk = _fresh_toolkit()
    captured = {}

    def emitter(question, options, allow_free_text):
        captured["question"] = question
        captured["options"] = options
        captured["allow_free_text"] = allow_free_text
        # Fire the reply from a tiny delay so the kit side is actually blocking.
        def _reply():
            time.sleep(0.02)
            tk._resolve_ask_user({"choice": "yes", "free_text": None})

        threading.Thread(target=_reply, daemon=True).start()

    tk.set_ask_user_emitter(emitter)
    result = tk.ask_user(
        "Proceed?",
        [{"label": "yes", "description": "recommended"},
         {"label": "no"}],
        allow_free_text=True,
    )
    assert captured["question"] == "Proceed?"
    assert len(captured["options"]) == 2
    assert captured["allow_free_text"] is True
    assert result == {"ok": True, "choice": "yes", "free_text": None, "cancelled": False}


def test_ask_user_returns_free_text():
    """If the user types (free_text only, no choice), both are passed through."""
    tk = _fresh_toolkit()

    def emitter(question, options, allow_free_text):
        tk._resolve_ask_user({"choice": None, "free_text": "do it differently"})

    tk.set_ask_user_emitter(emitter)
    result = tk.ask_user("How?", [{"label": "A"}, {"label": "B"}])
    assert result["free_text"] == "do it differently"
    assert result["choice"] is None


def test_ask_user_rejects_too_few_options():
    """< 2 options is a tool error, not a question."""
    tk = _fresh_toolkit()
    called = []
    tk.set_ask_user_emitter(lambda *a, **k: called.append(a))
    result = tk.ask_user("Pick one", [{"label": "only"}])
    assert "2-4" in str(result) or "options" in str(result).lower()
    assert called == []  # emitter was never called


def test_ask_user_rejects_too_many_options():
    """> 4 options is a tool error, not a question."""
    tk = _fresh_toolkit()
    tk.set_ask_user_emitter(lambda *a, **k: None)
    result = tk.ask_user(
        "Pick one",
        [{"label": f"o{i}"} for i in range(5)],
    )
    assert "2-4" in str(result) or "options" in str(result).lower()


def test_ask_user_recursive_guard():
    """A second concurrent ask_user returns a tool error (not a hang)."""
    tk = _fresh_toolkit()
    tk.set_ask_user_emitter(lambda *a, **k: None)  # never replies
    # Simulate "already waiting" by manually setting the pending slot.
    tk._ask_user_pending = ("fake", "fake")
    result = tk.ask_user("Q?", [{"label": "a"}, {"label": "b"}])
    assert "already waiting" in str(result).lower()
