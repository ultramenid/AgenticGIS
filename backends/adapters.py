"""Per-CLI adapter layer for AgenticGIS CLI Agent mode.

Every supported CLI is described by one ``Adapter`` class. The adapter
owns the command-line invocation (``build_command``) and the wire-format
parser (``parse_event``). All adapters return a single shape —
``NormalizedEvent`` — regardless of which CLI produced the event.

A single streaming pipeline (``NormalizingStream`` in
``cli_backend.py``) consumes those events and emits ``AgentEvent`` to
the chat dock. The chat dock is backend-agnostic; the only CLI-specific
knowledge lives here.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from typing import Callable, ClassVar, Optional, Sequence


class NormalizedEvent:
    """The one shape that flows out of every adapter."""

    __slots__ = ("text", "tool_calls", "session_id", "is_error", "is_final")

    def __init__(
        self,
        *,
        text: str = "",
        tool_calls: Optional[list] = None,
        session_id: str = "",
        is_error: bool = False,
        is_final: bool = False,
    ):
        self.text = text
        self.tool_calls = tool_calls or []
        self.session_id = session_id
        self.is_error = is_error
        self.is_final = is_final


class CliAdapter:
    """Base class — concrete adapters set the class-level identity and
    override ``build_command`` / ``parse_event`` as needed.

    This is the production base; tests can subclass it for stubs.
    """

    id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    commands: ClassVar[Sequence[str]] = ()
    credential_style: ClassVar[str] = ""
    warning: ClassVar[str] = ""
    auth_status_args: ClassVar[Sequence[str]] = ()
    login_args: ClassVar[Sequence[str]] = ()
    auth_detail_parser: ClassVar[Optional[Callable[[str, str], str]]] = None

    def build_command(
        self, *, binary: str, prompt: str, extra_args: list, runtime_dir: str,
    ) -> list:
        return [binary, "-p", prompt, *extra_args]

    def parse_event(self, raw: dict) -> Optional[NormalizedEvent]:
        return None

    def env(self) -> dict:
        return {}

    def test_commands(self, *, binary: str) -> list:
        return []


# ----------------------------------------------------------------------- #
# Runtime helpers (relocated from cli_backend.py)
# ----------------------------------------------------------------------- #


def _empty_runtime_dir(name: str) -> str:
    path = os.path.join(tempfile.gettempdir(), "AgenticGIS", name)
    os.makedirs(path, exist_ok=True)
    return path


def _runtime_json_file(name: str, content: str) -> str:
    path = os.path.join(_empty_runtime_dir(name), "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _opencode_config_json() -> str:
    return json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "instructions": [],
        "plugin": [],
        "skills": {"paths": [], "urls": []},
        "mcp": {},
        "permission": {
            "bash": "deny",
            "edit": "deny",
            "glob": "deny",
            "grep": "deny",
            "read": "deny",
            "write": "deny",
            "webfetch": "deny",
            "task": "deny",
            "skill": "deny",
        },
    })


def _devin_config_json() -> str:
    return json.dumps({
        "permissions": {"allow": [], "deny": [], "ask": []},
        "mcpServers": {},
        "read_config_from": {
            "cursor": False,
            "windsurf": False,
            "claude": False,
        },
    })
