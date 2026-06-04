"""Regression checks for web_fetch tool and its permission guardrails."""

import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core import tools
from AgenticGis.core.toolkit import QgisToolkit


class _Executor:
    def __init__(self):
        self.called = False

    def run_sync(self, fn, timeout=None):
        self.called = True
        return fn()


def _make_toolkit(choice):
    toolkit = QgisToolkit(iface=None)

    def emitter(_question, _options, _allow_free_text):
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


def test_web_fetch_denied_without_permission():
    toolkit = _make_toolkit("Deny")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "web_fetch",
        {"url": "https://example.com/data.json"},
    )

    assert result["ok"] is False
    assert result["cancelled"] is True
    assert executor.called is False


def test_web_fetch_allowed_once():
    toolkit = _make_toolkit("Allow once")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "web_fetch",
        {"url": "https://example.com/data.json"},
    )

    assert executor.called is True
    assert isinstance(result, dict)
    assert "status" in result or "error" in result


def test_web_fetch_bogus_url_returns_error():
    toolkit = _make_toolkit("Allow once")
    executor = _Executor()

    result = tools.dispatch(
        toolkit,
        executor,
        "web_fetch",
        {"url": "not-a-url"},
    )

    assert executor.called is True
    assert result["ok"] is False
    assert "http" in result["error"].lower() or "only" in result["error"].lower()


def test_web_fetch_tool_spec_exists():
    spec = next((s for s in tools.TOOL_SPECS if s["name"] == "web_fetch"), None)
    assert spec is not None
    assert spec["method"] == "web_fetch"
    props = spec["input_schema"]["properties"]
    assert "url" in props
    assert "max_length" in props


def main():
    test_web_fetch_denied_without_permission()
    test_web_fetch_allowed_once()
    test_web_fetch_bogus_url_returns_error()
    test_web_fetch_tool_spec_exists()
    print("web_fetch tests passed")


if __name__ == "__main__":
    main()
