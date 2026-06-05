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

    def stdin_prompt(self, prompt: str) -> Optional[str]:
        """Return the prompt to write to stdin, or None to pass on command line.

        Adapters that pipe the prompt via stdin instead of embedding it in
        the command line can override this to avoid ENAMETOOLONG errors
        from overlong argument strings.
        """
        return None

    def build_command(
        self, *, binary: str, prompt: str, extra_args: list, runtime_dir: str,
    ) -> list:
        return [binary, "-p", prompt, *extra_args]

    def parse_event(self, raw: dict) -> Optional[NormalizedEvent]:
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), is_final=True)
        return None

    def parse_protocol_text(self, text: str) -> Optional[NormalizedEvent]:
        """Parse the AgenticGIS tool_calls protocol embedded in text.

        The system prompt instructs the CLI to emit a single JSON object
        of the form ``{"type":"tool_calls","calls":[{...}, ...]}`` when
        it needs to call one or more AgenticGIS tools, and to use plain
        text/markdown for final answers. When the LLM follows the
        protocol, the JSON often appears inside the assistant's text
        payload (e.g. as the ``text`` field of a Claude stream event).
        Without this method, ``NormalizingStream`` would emit the raw
        JSON as a TEXT chat message and the user would see the protocol
        in their bubble.

        Subclasses normally inherit this implementation. Override only
        if a CLI has its own wire-level tool call format that should win
        over the AgenticGIS protocol (the native format will already
        have been handled by ``parse_event`` before this is called).
        """
        if not text:
            return None
        stripped = text.strip()
        if not stripped.startswith("{"):
            return None
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("type") != "tool_calls":
            return None
        calls = payload.get("calls")
        if not isinstance(calls, list) or not calls:
            return None
        tool_calls = []
        for c in calls:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = c.get("arguments", {}) or {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append({"name": name, "arguments": arguments})
        if not tool_calls:
            return None
        return NormalizedEvent(tool_calls=tool_calls, is_final=True)

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

    @staticmethod
    def _auth_detail(output: str, default: str) -> str:
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

    auth_detail_parser = _auth_detail

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
    """opencode — ``run`` with structured JSON output.

    Pipes the prompt via stdin (to avoid ENAMETOOLONG on the argument
    line) and consumes the ``--format json`` event stream.  Non-json
    output falls through to the base-class text-key extraction.
    """

    id = "opencode"
    label = "OpenCode"
    commands = ("opencode",)
    credential_style = "Provider keys in OpenCode config"

    auth_status_args = ("status",)
    login_args = ("login",)

    def stdin_prompt(self, prompt):
        return prompt

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "run",
            "--format", "json",
            "--dangerously-skip-permissions",
            *extra_args,
        ]

    def parse_event(self, raw):
        etype = raw.get("type")
        part = raw.get("part") or raw
        sid = raw.get("sessionID") or raw.get("session_id") or ""
        if etype == "text":
            return NormalizedEvent(
                text=str(part.get("text") or "").strip(),
                session_id=sid, is_final=True,
            )
        if etype == "tool_use":
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
        if etype == "error":
            err = raw.get("error") or {}
            if isinstance(err, dict):
                err_text = err.get("data", {}).get("message", "") or err.get("message", "") or str(err)
            else:
                err_text = str(err)
            return NormalizedEvent(is_error=True, text=err_text)
        if etype in ("step_start", "step_finish"):
            return None
        return super().parse_event(raw)


class CursorAdapter(CliAdapter):
    """Cursor Agent — ``-p`` with ``--output-format json``.

    Cursor's JSON events lack a top-level ``type`` discriminator so we
    fall back to the well-known text keys from ``DefaultAdapter``.
    """

    id = "cursor"
    label = "Cursor Agent"
    commands = ("cursor-agent", "cursor")
    credential_style = "Cursor account or configured provider keys"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        base = os.path.basename(binary or "")
        if base.startswith("cursor") and not base.startswith("cursor-agent"):
            return [
                binary, "agent", "-p", prompt, *extra_args,
                "--output-format", "json",
            ]
        return [
            binary, "-p", prompt, *extra_args,
            "--output-format", "json",
        ]

    def parse_event(self, raw):
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), is_final=True)
        return None


class GeminiAdapter(CliAdapter):
    """Gemini CLI — ``-p`` with ``--output-format json``."""

    id = "gemini"
    label = "Gemini CLI"
    commands = ("gemini",)
    credential_style = "Google account or Gemini API key"

    auth_status_args = ("status",)
    login_args = ("login",)

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "-p", prompt, *extra_args,
            "--output-format", "json",
            "--approval-mode", "default",
            "--extensions", "none",
        ]


class QwenAdapter(CliAdapter):
    """Qwen Code — ``--prompt`` with ``--output-format stream-json``."""

    id = "qwen"
    label = "Qwen Code"
    commands = ("qwen",)
    credential_style = "DashScope or Qwen API key"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "--prompt", prompt, *extra_args,
            "--output-format", "stream-json",
        ]


class KimiAdapter(CliAdapter):
    """Kimi CLI — ``-p`` with ``--output-format stream-json``."""

    id = "kimi"
    label = "Kimi CLI"
    commands = ("kimi",)
    credential_style = "Moonshot/Kimi API key"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "-p", prompt, *extra_args,
            "--output-format", "stream-json",
        ]


class DevinAdapter(CliAdapter):
    """Devin for Terminal — ``--print`` with a sandboxed config."""

    id = "devin"
    label = "Devin for Terminal"
    commands = ("devin",)
    credential_style = "Devin account"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "--print",
            "--config", _runtime_json_file("devin-config", _devin_config_json()),
            *extra_args,
            "--", prompt,
        ]


class KiroAdapter(CliAdapter):
    """Kiro CLI — ``chat --no-interactive``."""

    id = "kiro"
    label = "Kiro CLI"
    commands = ("kiro",)
    credential_style = "AWS credentials"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "chat",
            "--no-interactive",
            *extra_args,
            prompt,
        ]


class PiAdapter(CliAdapter):
    """Pi — ``-p`` for non-interactive prompt."""

    id = "pi"
    label = "Pi"
    commands = ("pi",)
    credential_style = "Pi account"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        return [
            binary, "-p", prompt,
            *extra_args,
        ]


class CopilotAdapter(CliAdapter):
    """GitHub Copilot CLI — ``gh copilot suggest`` or ``copilot suggest``."""

    id = "copilot"
    label = "GitHub Copilot CLI"
    commands = ("gh", "copilot")
    credential_style = "GitHub Copilot subscription"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir):
        if os.path.basename(binary or "") == "gh":
            return [
                binary, "copilot", "suggest",
                *extra_args,
                prompt,
            ]
        return [
            binary, "suggest",
            *extra_args,
            prompt,
        ]

    def test_commands(self, *, binary):
        if os.path.basename(binary or "") == "gh":
            return [[binary, "copilot", "--help"]]
        return []


class DefaultAdapter(CliAdapter):
    """Generic fallback for catalog entries without bespoke parsing.

    Uses ``binary -p <prompt>`` for invocation and walks the well-known
    top-level text keys for event parsing — same fallback shape that
    ``_emit_line`` had at the end of the legacy code.
    """

    def parse_event(self, raw):
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), is_final=True)
        return None


# Order matches CLI_AGENT_CATALOG in cli_backend.py. Catalog entries
# that previously fell through to _build_default_command and the generic
# _emit_line fallback now explicitly use DefaultAdapter.
ADAPTERS: dict = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpenCodeAdapter(),
    "cursor": CursorAdapter(),
    "gemini": GeminiAdapter(),
    "qwen": QwenAdapter(),
    "kimi": KimiAdapter(),
    "devin": DevinAdapter(),
    "kiro": KiroAdapter(),
    "pi": PiAdapter(),
    "copilot": CopilotAdapter(),
    # Generic fallbacks (one instance is fine — stateless).
    "grok": DefaultAdapter(),
    "hermes": DefaultAdapter(),
    "deepseek_tui": DefaultAdapter(),
    "mistral_vibe": DefaultAdapter(),
    "kilo": DefaultAdapter(),
    "qoder": DefaultAdapter(),
}


def get_adapter(tool_id: str) -> CliAdapter:
    """Return the registered adapter for ``tool_id``, or DefaultAdapter."""
    return ADAPTERS.get(tool_id) or DefaultAdapter()
