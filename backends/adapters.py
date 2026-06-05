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


class ClaudeAdapter(CliAdapter):
    """Claude Code — ``stream-json`` over ``-p``."""

    id = "claude"
    label = "Claude Code"
    commands = ("claude",)
    credential_style = "Claude subscription or Anthropic credentials"
    warning = "Provider policy may treat third-party automation differently."

    auth_status_args = ("auth", "status")
    login_args = ("auth", "login")

    def _auth_detail(self, output: str, default: str) -> str:
        if not output.startswith("{"):
            return default
        try:
            payload = json.loads(output)
        except Exception:
            return default
        if payload.get("loggedIn") is True:
            auth_method = payload.get("authMethod") or "logged in"
            provider = payload.get("apiProvider") or ""
            return " · ".join(part for part in (auth_method, provider) if part)
        if payload.get("loggedIn") is False:
            return "Not logged in"
        return default

    auth_detail_parser = staticmethod(_auth_detail)

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "-p", prompt, *extra_args,
            "--output-format", "stream-json", "--verbose",
            "--setting-sources", "local", "--settings", "{}",
            "--disable-slash-commands",
            "--plugin-dir", _empty_runtime_dir("claude-empty-plugins"),
            "--no-session-persistence",
        ]

    def parse_event(self, raw):
        etype = raw.get("type")
        sid = raw.get("session_id") or raw.get("sessionID") or ""
        if etype == "assistant":
            parts = [
                b.get("text", "")
                for b in raw.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return NormalizedEvent(text="".join(parts), session_id=sid)
        if etype == "user":
            for b in raw.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return NormalizedEvent(
                        tool_calls=[{
                            "name": "tool",
                            "arguments": {},
                            "output": b.get("content", ""),
                            "is_error": bool(b.get("is_error", False)),
                        }],
                        session_id=sid,
                    )
            return None
        return None


class CodexAdapter(CliAdapter):
    """Codex CLI — ``exec --json`` event stream."""

    id = "codex"
    label = "Codex CLI"
    commands = ("codex",)
    credential_style = "OpenAI API key or ChatGPT account in Codex"

    auth_status_args = ("login", "status")
    login_args = ("login",)

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "exec", *extra_args,
            "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check",
            "--disable", "apps", "--disable", "plugins",
            "--cd", _empty_runtime_dir("codex-empty-workspace"),
            "--json", prompt,
        ]

    def parse_event(self, raw):
        etype = raw.get("type")
        if etype == "item.completed":
            item = raw.get("item") or {}
            it = item.get("type")
            if it == "agent_message":
                return NormalizedEvent(
                    text=item.get("text", ""), is_final=True,
                )
            if it in ("command_execution", "mcp_tool_call"):
                return NormalizedEvent(tool_calls=[{
                    "name": item.get("cmd") or item.get("tool") or it,
                    "arguments": item.get("arguments") or {"cmd": item.get("cmd", "")},
                    "output": item.get("output") or item.get("stdout") or item.get("result") or "",
                    "is_error": bool(item.get("exit_code", 0)),
                }])
            return None
        if etype in ("turn.failed", "error"):
            msg = raw.get("message") or raw.get("error") or raw.get("detail") or ""
            if isinstance(msg, dict):
                msg = msg.get("message") or json.dumps(msg, default=str)
            return NormalizedEvent(is_error=True, text=str(msg))
        return None


class OpenCodeAdapter(CliAdapter):
    """opencode — ``run`` with ``--format json`` and a runtime config."""

    id = "opencode"
    label = "OpenCode"
    commands = ("opencode",)
    credential_style = "Provider keys in OpenCode config"

    auth_status_args = ("status",)
    login_args = ("login",)

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "run", prompt, *extra_args,
            "--pure",
            "--format", "json",
        ]

    def env(self) -> dict:
        config = _opencode_config_json()
        config_path = _runtime_json_file("opencode-config", config)
        return {
            "OPENCODE_CONFIG_CONTENT": config,
            "OPENCODE_CONFIG": config_path,
            "OPENCODE_CONFIG_DIR": os.path.dirname(config_path),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_PURE": "1",
            "OPENCODE_ENABLE_EXA": "0",
        }

    def parse_event(self, raw):
        # opencode events carry a "part" object that holds the actual payload.
        part = raw.get("part") or {}
        if not isinstance(part, dict):
            return None
        sid = raw.get("session_id") or raw.get("sessionID") or ""
        ptype = part.get("type")
        if ptype == "text":
            return NormalizedEvent(text=str(part.get("text") or "").strip(), session_id=sid)
        if ptype == "tool":
            tool_name = part.get("tool", "")
            state = part.get("state") or {}
            if tool_name == "invalid":
                tool_name = (state.get("input") or {}).get("name", "")
            if not tool_name:
                return None
            return NormalizedEvent(
                tool_calls=[{
                    "name": tool_name,
                    "arguments": state.get("input") or {},
                }],
                session_id=sid,
            )
        return None
