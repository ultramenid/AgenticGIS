"""Regression checks for outside-layer access permission guardrails."""

import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core import tools
from AgenticGis.core.toolkit import QgisToolkit


class _Config:
    def __init__(self):
        self.values = {}

    def get(self, name, default=None):
        return self.values.get(name, default)

    def set(self, name, value):
        self.values[name] = value


class _Executor:
    def __init__(self):
        self.called = False

    def run_sync(self, fn, timeout=None):
        self.called = True
        return fn()


def _make_toolkit(choice, config=None, seen=None):
    toolkit = QgisToolkit(iface=None, config=config)

    def emitter(_question, options, _allow_free_text):
        if seen is not None:
            seen.append(list(options))
        threading.Timer(
            0.01,
            lambda: toolkit._resolve_ask_user({
                "choice": choice,
                "free_text": None,
                "cancelled": choice is None,
            }),
        ).start()

    toolkit.set_ask_user_emitter(emitter)
    return toolkit


def test_denies_external_layer_without_running_tool():
    toolkit = _make_toolkit("Deny")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "add_layer",
        {"uri": "/tmp/outside.gpkg", "name": "Outside", "provider": "ogr"},
    )

    assert result["ok"] is False
    assert result["cancelled"] is True
    assert executor.called is False


def test_allows_external_layer_once():
    toolkit = _make_toolkit("Allow once")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "add_layer",
        {"uri": "/tmp/outside.gpkg", "name": "Outside", "provider": "ogr"},
    )

    assert executor.called is True
    assert isinstance(result, dict)


def test_external_access_prompt_has_three_choices():
    seen = []
    toolkit = _make_toolkit("Deny", seen=seen)
    executor = _Executor()

    tools.dispatch(
        toolkit,
        executor,
        "add_layer",
        {"uri": "/tmp/outside.gpkg", "name": "Outside", "provider": "ogr"},
    )

    labels = [option["label"] for option in seen[-1]]
    assert labels == ["Allow once", "Always allow", "Deny"]


def test_always_allow_persists_and_skips_future_prompts():
    config = _Config()
    seen = []
    toolkit = _make_toolkit("Always allow", config=config, seen=seen)
    executor = _Executor()

    first = tools.dispatch(
        toolkit,
        executor,
        "add_layer",
        {"uri": "/tmp/outside.gpkg", "name": "Outside", "provider": "ogr"},
    )

    assert executor.called is True
    assert isinstance(first, dict)
    assert config.get("external_access_always_allowed") is True
    assert len(seen) == 1

    executor.called = False
    second = tools.dispatch(
        toolkit,
        executor,
        "add_layer",
        {"uri": "/tmp/another.gpkg", "name": "Another", "provider": "ogr"},
    )

    assert executor.called is True
    assert isinstance(second, dict)
    assert len(seen) == 1


def test_safe_memory_qgsvectorlayer_does_not_prompt():
    toolkit = _make_toolkit("Deny")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "run_pyqgis",
        {
            "code": (
                "from qgis.core import QgsVectorLayer\n"
                "layer = QgsVectorLayer('Point?field=id:integer', 'tmp', 'memory')\n"
                "result = layer.isValid()"
            )
        },
    )

    assert executor.called is True
    assert isinstance(result, dict)


def test_run_pyqgis_external_path_still_prompts():
    toolkit = _make_toolkit("Deny")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "run_pyqgis",
        {
            "code": (
                "from qgis.core import QgsVectorLayer\n"
                "layer = QgsVectorLayer('/tmp/outside.gpkg', 'outside', 'ogr')\n"
                "result = layer.isValid()"
            )
        },
    )

    assert result["ok"] is False
    assert result["cancelled"] is True
    assert executor.called is False


def main():
    test_denies_external_layer_without_running_tool()
    test_allows_external_layer_once()
    test_external_access_prompt_has_three_choices()
    test_always_allow_persists_and_skips_future_prompts()
    test_safe_memory_qgsvectorlayer_does_not_prompt()
    test_run_pyqgis_external_path_still_prompts()


if __name__ == "__main__":
    main()
