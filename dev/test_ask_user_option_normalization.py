"""Regression checks for tolerant ask_user option normalization."""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core.toolkit import QgisToolkit


def _answering_toolkit():
    toolkit = QgisToolkit(iface=None)
    seen = []

    def emitter(_question, options, _allow_free_text):
        seen.append(options)
        threading.Timer(
            0.01,
            lambda: toolkit._resolve_ask_user({
                "choice": options[0]["label"],
                "free_text": None,
                "cancelled": False,
            }),
        ).start()

    toolkit.set_ask_user_emitter(emitter)
    return toolkit, seen


def main():
    toolkit, seen = _answering_toolkit()
    result = toolkit.ask_user(
        "Proceed?",
        ["Allow once", "Deny"],
        allow_free_text=False,
    )
    assert result["choice"] == "Allow once", result
    assert seen[-1][0] == {"label": "Allow once", "description": ""}

    toolkit, seen = _answering_toolkit()
    result = toolkit.ask_user(
        "Proceed?",
        [{"title": "Allow once", "detail": "Permit this operation."}],
        allow_free_text=False,
    )
    assert result["choice"] == "Allow once", result
    assert len(seen[-1]) == 2
    assert seen[-1][1]["label"] == "Cancel"

    toolkit, seen = _answering_toolkit()
    result = toolkit.ask_user(
        "Proceed?",
        {"Allow once": "Permit this operation.", "Deny": "Block it."},
        allow_free_text=False,
    )
    assert result["choice"] == "Allow once", result
    assert seen[-1][0] == {
        "label": "Allow once",
        "description": "Permit this operation.",
    }


if __name__ == "__main__":
    main()
