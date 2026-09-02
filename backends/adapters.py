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
import shutil
import tempfile
from typing import Callable, ClassVar, List, Optional, Sequence


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
    models_args: ClassVar[Sequence[str]] = ()
    default_models: ClassVar[Sequence[str]] = ()
    models_parser: ClassVar[Optional[Callable[[str], List[str]]]] = None
    supports_continuation: ClassVar[bool] = False
    supports_mcp: ClassVar[bool] = False

    def stdin_prompt(self, prompt: str) -> Optional[str]:
        """Return the prompt to write to stdin, or None to pass on command line.

        Adapters that pipe the prompt via stdin instead of embedding it in
        the command line can override this to avoid ENAMETOOLONG errors
        from overlong argument strings.
        """
        return None

    def build_command(
        self, *, binary: str, prompt: str, extra_args: list, runtime_dir: str,
        mcp_url: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> list:
        cmd = [binary, "-p", prompt, *extra_args]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def build_continuation_command(
        self,
        *,
        binary: str,
        prompt: str,
        extra_args: list,
        runtime_dir: str,
        session_id: str,
        mcp_url: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> list:
        return self.build_command(
            binary=binary,
            prompt=prompt,
            extra_args=extra_args,
            runtime_dir=runtime_dir,
            mcp_url=mcp_url,
            model=model,
            **kwargs,
        )

    def parse_event(self, raw: dict) -> Optional[NormalizedEvent]:
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), is_final=True)
        return None

    def parse_protocol_text(self, text: str) -> Optional[NormalizedEvent]:
        """Parse the AgenticGIS tool_calls protocol embedded in text.

        The system prompt instructs the CLI to emit a single JSON object
        of the form ``{"type":"tool_calls","calls":[...]}`` when
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

        # Strip markdown code-block wrappers (```json ... ``` or ``` ... ```).
        # Handles both pure code blocks and text that contains a code block.
        if "```" in stripped:
            lines = stripped.splitlines()
            inside_block = False
            block_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    inside_block = not inside_block
                    continue
                if inside_block:
                    block_lines.append(line)
            if block_lines:
                stripped = "\n".join(block_lines).strip()

        # Try to find and parse the tool_calls protocol JSON anywhere in
        # the text. Claude may emit extra text around the JSON, so we search
        # for the {"type":"tool_calls" pattern and use json.JSONDecoder.
        # raw_decode() to extract the exact object — this handles braces
        # inside JSON strings correctly, unlike a naive brace-depth counter.
        if "{" not in stripped:
            return None
        decoder = json.JSONDecoder()
        search_start = 0
        while True:
            idx = stripped.find('"type"', search_start)
            if idx == -1:
                break
            # Walk back to the opening brace that starts this object
            brace_idx = None
            for i in range(idx, -1, -1):
                if stripped[i] == "{":
                    brace_idx = i
                    break
            if brace_idx is None:
                search_start = idx + 1
                continue
            try:
                payload, end = decoder.raw_decode(stripped, brace_idx)
            except (json.JSONDecodeError, ValueError):
                search_start = idx + 1
                continue
            if not isinstance(payload, dict):
                search_start = end
                continue
            if payload.get("type") != "tool_calls":
                search_start = end
                continue
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
        return None

    def env(self, mcp_url: Optional[str] = None) -> dict:
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


# ----------------------------------------------------------------------- #
# Native MCP tool registration                                            #
# ----------------------------------------------------------------------- #
# When the in-QGIS bridge is running, CLI agents get the AgenticGIS tools
# registered as native MCP tools so they can call them directly (instead of
# emitting the JSON text protocol). Clients name MCP tools with the server
# prefix, so parsed names are normalized back to the plain tool name.

MCP_SERVER_NAME = "agenticgis"


def normalize_tool_name(name):
    """Map an MCP-registered tool name back to the plain AgenticGIS name.

    Claude Code uses ``mcp__agenticgis__list_layers``; Codex/OpenCode use
    ``agenticgis__list_layers``, ``agenticgis.list_layers`` or
    ``agenticgis_list_layers``. Anything else (plain names, the text
    protocol, shell commands) passes through.
    """
    if not isinstance(name, str):
        return name
    lowered = name.lower()
    for prefix in (
        "mcp__agenticgis__", "agenticgis__", "agenticgis.", "agenticgis_",
    ):
        if lowered.startswith(prefix):
            return name[len(prefix):]
    return name


def _mcp_proxy_command(mcp_url):
    """Return ``[interpreter, script, --url, URL]`` for the stdio→HTTP proxy.

    Codex only accepts stdio MCP servers, so it launches the bundled
    ``server/mcp_stdio.py`` with a system python (never ``sys.executable`` —
    inside QGIS that is the QGIS binary, not a python).
    """
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "server", "mcp_stdio.py",
    )
    for interpreter in ("python3", "python"):
        resolved = shutil.which(interpreter)
        if resolved:
            return [resolved, script, "--url", mcp_url]
    return ["python3", script, "--url", mcp_url]


class ClaudeAdapter(CliAdapter):
    """Claude Code — ``stream-json`` over ``-p``."""

    id = "claude"
    label = "Claude Code"
    commands = ("claude",)
    credential_style = "Claude subscription or Anthropic credentials"
    warning = "Provider policy may treat third-party automation differently."
    supports_continuation = True
    supports_mcp = True

    auth_status_args = ("auth", "status")
    login_args = ("auth", "login")
    default_models = (
        "claude-3-7-sonnet",
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "claude-opus-4-8",
    )

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

    def stdin_prompt(self, prompt):
        # Pipe the prompt via stdin instead of argv: the system prompt + tool
        # specs + conversation easily exceed Windows' ~32K command-line limit
        # (WinError 206). `claude -p` reads the prompt from stdin when no
        # positional prompt is given. See issue #2.
        return prompt

    @staticmethod
    def _mcp_flags(runtime_dir, mcp_url):
        """Register the AgenticGIS bridge as a native MCP server.

        Claude Code speaks streamable HTTP directly (no stdio proxy needed)
        and headless ``-p`` mode needs the server's tools pre-allowed.
        """
        if not mcp_url:
            return []
        directory = runtime_dir or tempfile.gettempdir()
        os.makedirs(directory, exist_ok=True)
        config_path = os.path.join(directory, "agenticgis-mcp.json")
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(
                {"mcpServers": {MCP_SERVER_NAME: {"type": "http", "url": mcp_url}}},
                fh,
            )
        return [
            "--mcp-config", config_path,
            "--allowedTools", f"mcp__{MCP_SERVER_NAME}",
        ]

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "-p", *extra_args,
            "--output-format", "stream-json", "--verbose",
            "--setting-sources", "local", "--settings", "{}",
            "--disable-slash-commands",
            "--plugin-dir", _empty_runtime_dir("claude-empty-plugins"),
            *self._mcp_flags(runtime_dir, mcp_url),
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def build_continuation_command(
        self, *, binary, prompt, extra_args, runtime_dir, session_id, mcp_url=None, model=None, **kwargs,
    ):
        cmd = [
            binary, "-p", *extra_args,
            "--resume", session_id,
            "--output-format", "stream-json", "--verbose",
            "--setting-sources", "local", "--settings", "{}",
            "--disable-slash-commands",
            "--plugin-dir", _empty_runtime_dir("claude-empty-plugins"),
            *self._mcp_flags(runtime_dir, mcp_url),
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def parse_event(self, raw):
        etype = raw.get("type")
        sid = raw.get("session_id") or raw.get("sessionID") or ""
        if etype == "assistant":
            content = raw.get("message", {}).get("content") or []
            text_parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            tool_calls = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_calls.append({
                        "name": normalize_tool_name(b.get("name", "")),
                        "arguments": b.get("input", {}) or {},
                    })
            return NormalizedEvent(
                text="".join(text_parts),
                tool_calls=tool_calls,
                session_id=sid,
                is_final=True,
            )
        if etype == "content_block_delta":
            delta = raw.get("delta") or {}
            if isinstance(delta, dict):
                # Handle tool_calls deltas (new in recent Claude CLI versions)
                if delta.get("type") == "tool_calls":
                    calls = delta.get("calls", [])
                    tool_calls = []
                    for c in calls:
                        if isinstance(c, dict):
                            name = normalize_tool_name(c.get("name"))
                            if isinstance(name, str) and name:
                                tool_calls.append({
                                    "name": name,
                                    "arguments": c.get("arguments", {}) or {},
                                })
                    if tool_calls:
                        return NormalizedEvent(tool_calls=tool_calls)
                # Standard text delta
                text = delta.get("text") or delta.get("text_delta", "")
                return NormalizedEvent(text=str(text))
            return NormalizedEvent(text=str(delta))
        if etype == "user":
            for b in (raw.get("message", {}).get("content") or []):
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
        # Handle raw tool_calls protocol JSON (Claude CLI may emit this directly)
        if etype == "tool_calls":
            calls = raw.get("calls", [])
            tool_calls = []
            for c in calls:
                if isinstance(c, dict):
                    name = normalize_tool_name(c.get("name"))
                    if isinstance(name, str) and name:
                        tool_calls.append({
                            "name": name,
                            "arguments": c.get("arguments", {}) or {},
                        })
            if tool_calls:
                return NormalizedEvent(tool_calls=tool_calls)
        # Handle Claude result/summary events — these echo the final text and
        # must not fall through to the generic fallback or they duplicate the
        # assistant message already shown.
        if etype in ("result", "summary"):
            result_text = raw.get("result") or raw.get("message") or ""
            is_err = bool(raw.get("is_error")) or bool(raw.get("api_error_status"))
            if is_err:
                return NormalizedEvent(is_error=True, text=str(result_text))
            return NormalizedEvent(text=str(result_text), is_final=True)
        return None


class CodexAdapter(CliAdapter):
    """Codex CLI — ``exec --json`` event stream."""

    id = "codex"
    label = "Codex CLI"
    commands = ("codex",)
    credential_style = "OpenAI API key or ChatGPT account in Codex"
    supports_continuation = True
    supports_mcp = True

    auth_status_args = ("login", "status")
    login_args = ("login",)
    default_models = (
        "o3-mini",
        "o1",
        "gpt-4o",
        "gpt-4.5-preview",
        "gpt-4o-mini",
    )

    def stdin_prompt(self, prompt):
        # `codex exec … -` forces the prompt to be read from stdin. Required:
        # the system prompt + tool specs alone are ~30K chars, past Windows'
        # 32767-char CreateProcess limit (WinError 206).
        return prompt

    @staticmethod
    def _mcp_flags(mcp_url):
        """Register the AgenticGIS bridge via ``-c`` TOML overrides.

        Codex ignores the user config here, so CLI overrides are the only
        channel. Codex only speaks stdio MCP, hence the bundled proxy.
        """
        if not mcp_url:
            return []
        interpreter, script, _flag, url = _mcp_proxy_command(mcp_url)
        return [
            "-c", f'mcp_servers.{MCP_SERVER_NAME}.command="{interpreter}"',
            "-c", f'mcp_servers.{MCP_SERVER_NAME}.args=["{script}", "--url", "{url}"]',
        ]

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "exec", *extra_args,
            "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check",
            "--disable", "apps", "--disable", "plugins",
            "--cd", _empty_runtime_dir("codex-empty-workspace"),
            *self._mcp_flags(mcp_url),
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--json", "-"])
        return cmd

    def build_continuation_command(
        self, *, binary, prompt, extra_args, runtime_dir, session_id, mcp_url=None, model=None, **kwargs,
    ):
        cmd = [
            binary, "exec", "resume", *extra_args,
            "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check",
            "--disable", "apps", "--disable", "plugins",
            *self._mcp_flags(mcp_url),
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--json", session_id, "-"])
        return cmd

    def parse_event(self, raw):
        etype = raw.get("type")
        if etype == "thread.started":
            session_id = raw.get("thread_id") or raw.get("session_id") or ""
            return NormalizedEvent(session_id=session_id)
        if etype == "item.completed":
            item = raw.get("item") or {}
            it = item.get("type")
            if it == "agent_message":
                return NormalizedEvent(
                    text=item.get("text", ""), is_final=True,
                )
            if it in ("command_execution", "mcp_tool_call"):
                return NormalizedEvent(tool_calls=[{
                    "name": normalize_tool_name(
                        item.get("cmd") or item.get("tool") or it
                    ),
                    "arguments": item.get("arguments") or {"cmd": item.get("cmd", "")},
                    "output": item.get("output") or item.get("stdout") or item.get("result") or "",
                    "is_error": bool(item.get("exit_code", 0)),
                }])
            return None
        if etype == "task_complete":
            msg = raw.get("last_agent_message") or raw.get("message") or ""
            return NormalizedEvent(text=str(msg), is_final=True)
        if etype == "agent_message_content_delta":
            delta = raw.get("delta") or ""
            return NormalizedEvent(text=str(delta))
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

    supports_continuation = True
    supports_mcp = True

    auth_status_args = ("status",)
    login_args = ("login",)
    models_args = ("models",)

    def stdin_prompt(self, prompt):
        return prompt

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "run",
            "--pure",
            "--format", "json",
            "--auto",
            *extra_args,
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def env(self, mcp_url=None):
        config = json.loads(_opencode_config_json())
        if mcp_url:
            # Register the AgenticGIS bridge as a native remote MCP server
            # (streamable HTTP — opencode connects directly, no proxy).
            config["mcp"] = {
                MCP_SERVER_NAME: {"type": "remote", "url": mcp_url},
            }
        content = json.dumps(config)
        config_dir = _empty_runtime_dir("opencode-config")
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {
            "OPENCODE_PURE": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_CONFIG": config_path,
            "OPENCODE_CONFIG_DIR": config_dir,
            "OPENCODE_CONFIG_CONTENT": content,
        }

    def parse_event(self, raw):
        etype = raw.get("type")
        # opencode may nest the payload under "part" or put keys at top level.
        part = raw.get("part") or raw
        sid = raw.get("sessionID") or raw.get("session_id") or ""

        # ── text / message / thinking / content_block_delta ──────────────
        # Aggressive text extraction: try many locations for the payload.
        # This catches events where opencode nests text in unexpected keys.
        text = None
        if etype in ("text", "message", "thinking", "content_block_delta"):
            text = (
                part.get("text")
                or part.get("content")
                or part.get("message")
                or part.get("response")
                or raw.get("text")
                or raw.get("content")
                or raw.get("message")
                or raw.get("response")
                or ""
            )
            text = str(text).strip()
            if text:
                return NormalizedEvent(text=text, session_id=sid)
            return None

        if etype in ("tool_use", "tool_call"):
            tool_name = normalize_tool_name(part.get("tool", ""))
            state = part.get("state") or {}
            if tool_name == "invalid":
                tool_name = normalize_tool_name((state.get("input") or {}).get("name", ""))
            if not tool_name:
                # Some opencode versions put tool name directly under part.name
                tool_name = normalize_tool_name(part.get("name", ""))
            if not tool_name:
                return None
            args = state.get("input") or part.get("arguments") or part.get("args") or {}
            return NormalizedEvent(
                tool_calls=[{"name": tool_name, "arguments": args}],
                session_id=sid,
            )

        if etype == "error":
            err = raw.get("error") or {}
            if isinstance(err, dict):
                err_text = (
                    err.get("data", {}).get("message", "")
                    or err.get("message", "")
                    or err.get("text", "")
                    or str(err)
                )
            else:
                err_text = str(err)
            if err_text:
                return NormalizedEvent(is_error=True, text=err_text)
            return None

        if etype in ("step_start", "step_finish"):
            if etype == "step_finish" and (part.get("reason") == "stop" or raw.get("reason") == "stop"):
                return NormalizedEvent(session_id=sid, is_final=True)
            return None

        # Fallback for opencode's many output shapes: any top-level text key
        # that DefaultAdapter would catch, but also handle nested structures.
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), is_final=True)

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
    default_models = (
        "claude-3-7-sonnet",
        "claude-3-5-sonnet",
        "gpt-4o",
        "o3-mini",
    )

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        base = os.path.basename(binary or "")
        cmd = [
            binary, "agent", "-p", prompt, *extra_args,
            "--output-format", "json",
        ] if base.startswith("cursor") and not base.startswith("cursor-agent") else [
            binary, "-p", prompt, *extra_args,
            "--output-format", "json",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd

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
    default_models = (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    )

    auth_status_args = ("status",)
    login_args = ("login",)

    def stdin_prompt(self, prompt):
        # Piped stdin is treated as the prompt and keeps the CLI headless.
        # Passing it via -p overflows Windows' 32767-char command line.
        return prompt

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, *extra_args,
            "--output-format", "json",
            "--approval-mode", "default",
            "--extensions", "none",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd


class AntigravityAdapter(CliAdapter):
    """Antigravity CLI — ``agy`` agent interface."""

    id = "antigravity"
    label = "Antigravity CLI"
    commands = ("agy", "antigravity")
    credential_style = "Google account or Antigravity credentials"
    supports_continuation = True
    supports_mcp = False

    auth_status_args = ("models",)
    login_args = ()
    models_args = ("models",)

    @staticmethod
    def _auth_detail(output: str, default: str) -> str:
        lower = output.lower()
        if any(err in lower for err in ("not logged in", "login required", "unauthenticated", "unauthorized")):
            return "Not logged in"
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        models = [
            line for line in lines
            if not line.lower().startswith("fetching") and not line.lower().startswith("error")
        ]
        if models:
            return f"Logged in ({len(models)} models available)"
        if "error" in lower:
            return default or "Login required"
        return default or "Logged in"

    auth_detail_parser = _auth_detail

    @staticmethod
    def _parse_models(output: str) -> List[str]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        models = []
        for line in lines:
            lower = line.lower()
            if lower.startswith("fetching") or lower.startswith("error"):
                continue
            parts = line.split("\t")
            model_id = parts[0].strip() if parts else ""
            if not model_id:
                model_id = line.split()[0].strip()
            if model_id and model_id not in models:
                models.append(model_id)
        return models

    models_parser = _parse_models

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, *extra_args,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", prompt])
        return cmd

    def build_continuation_command(
        self, *, binary, prompt, extra_args, runtime_dir, session_id, mcp_url=None, model=None, **kwargs,
    ):
        cmd = [
            binary, *extra_args,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        if session_id:
            cmd.extend(["--conversation", session_id])
        cmd.extend(["-p", prompt])
        return cmd

    def parse_event(self, raw):
        event = raw.get("event")
        sid = (
            raw.get("conversation_id")
            or raw.get("session_id")
            or raw.get("sessionID")
            or raw.get("thread_id")
            or ""
        )
        if event == "init":
            return NormalizedEvent(session_id=sid)
        if event == "step_update":
            su = raw.get("step_update") or {}
            step_sid = su.get("conversation_id") or sid
            step_type = su.get("step_type")
            if step_type == "tool":
                tool_info = su.get("tool_info") or {}
                tool_name = normalize_tool_name(su.get("tool_name") or tool_info.get("name") or "")
                if tool_name:
                    params = tool_info.get("parameters") or tool_info.get("input") or {}
                    call = {"name": tool_name, "arguments": params}
                    if "output" in tool_info:
                        call["output"] = tool_info["output"]
                    return NormalizedEvent(tool_calls=[call], session_id=step_sid)
            text = su.get("text_delta") or ""
            if text:
                return NormalizedEvent(text=text, session_id=step_sid)
            return None
        if event == "result":
            res = raw.get("result") or {}
            res_sid = res.get("conversation_id") or sid
            if res.get("status") not in ("SUCCESS", None):
                err = res.get("error") or res.get("message") or "CLI failed"
                return NormalizedEvent(is_error=True, text=str(err), session_id=res_sid)
            resp = res.get("response") or ""
            return NormalizedEvent(text=str(resp).strip(), session_id=res_sid, is_final=True)

        etype = raw.get("type")
        if etype in ("assistant", "message", "text"):
            content = raw.get("content") or raw.get("text") or raw.get("message") or ""
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            text_parts.append(b.get("text", ""))
                        elif b.get("type") in ("tool_use", "tool_call"):
                            tool_calls.append({
                                "name": normalize_tool_name(b.get("name", "")),
                                "arguments": b.get("input") or b.get("arguments") or {},
                            })
                return NormalizedEvent(
                    text="".join(text_parts),
                    tool_calls=tool_calls,
                    session_id=sid,
                    is_final=True,
                )
            if isinstance(content, str) and content.strip():
                return NormalizedEvent(text=content.strip(), session_id=sid, is_final=True)
        if etype in ("tool_use", "tool_call", "tool_calls"):
            calls = raw.get("calls") if etype == "tool_calls" else [raw]
            tool_calls = []
            for c in (calls or []):
                if isinstance(c, dict):
                    name = normalize_tool_name(c.get("name") or c.get("tool") or "")
                    if name:
                        args = c.get("arguments") or c.get("input") or c.get("args") or {}
                        tool_calls.append({"name": name, "arguments": args})
            if tool_calls:
                return NormalizedEvent(tool_calls=tool_calls, session_id=sid)
        if etype in ("error", "turn.failed"):
            err = raw.get("error") or raw.get("message") or ""
            return NormalizedEvent(is_error=True, text=str(err), session_id=sid)
        for key in ("text", "response", "content", "output", "result", "message"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return NormalizedEvent(text=val.strip(), session_id=sid, is_final=True)
        return None


class QwenAdapter(CliAdapter):
    """Qwen Code — ``--prompt`` with ``--output-format stream-json``."""

    id = "qwen"
    label = "Qwen Code"
    commands = ("qwen",)
    credential_style = "DashScope or Qwen API key"
    default_models = (
        "qwen-max",
        "qwen-plus",
        "qwen-turbo",
        "qwen-2.5-coder-32b",
    )

    def stdin_prompt(self, prompt):
        # gemini-cli fork: piped stdin is the prompt, and avoids Windows'
        # 32767-char command-line limit.
        return prompt

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, *extra_args,
            "--output-format", "stream-json",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd


class KimiAdapter(CliAdapter):
    """Kimi CLI — ``-p`` with ``--output-format stream-json``."""

    id = "kimi"
    label = "Kimi CLI"
    commands = ("kimi",)
    credential_style = "Moonshot/Kimi API key"
    default_models = (
        "kimi-k2.5",
        "kimi-k2",
        "kimi-latest",
    )

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "-p", prompt, *extra_args,
            "--output-format", "stream-json",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd


class DevinAdapter(CliAdapter):
    """Devin for Terminal — ``--print`` with a sandboxed config."""

    id = "devin"
    label = "Devin for Terminal"
    commands = ("devin",)
    credential_style = "Devin account"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "--print",
            "--config", _runtime_json_file("devin-config", _devin_config_json()),
            *extra_args,
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--", prompt])
        return cmd


class KiroAdapter(CliAdapter):
    """Kiro CLI — ``chat --no-interactive``."""

    id = "kiro"
    label = "Kiro CLI"
    commands = ("kiro",)
    credential_style = "AWS credentials"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "chat",
            "--no-interactive",
            *extra_args,
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        return cmd


class PiAdapter(CliAdapter):
    """Pi — ``-p`` for non-interactive prompt."""

    id = "pi"
    label = "Pi"
    commands = ("pi",)
    credential_style = "Pi account"

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        cmd = [
            binary, "-p", prompt,
            *extra_args,
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd


class CopilotAdapter(CliAdapter):
    """GitHub Copilot CLI — ``gh copilot suggest`` or ``copilot suggest``."""

    id = "copilot"
    label = "GitHub Copilot CLI"
    commands = ("gh", "copilot")
    credential_style = "GitHub Copilot subscription"
    default_models = (
        "claude-3.5-sonnet",
        "gpt-4o",
        "o1",
    )

    def build_command(self, *, binary, prompt, extra_args, runtime_dir, mcp_url=None, model=None, **kwargs):
        if os.path.basename(binary or "") == "gh":
            cmd = [
                binary, "copilot", "suggest",
                *extra_args,
            ]
        else:
            cmd = [
                binary, "suggest",
                *extra_args,
            ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        return cmd

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
    "antigravity": AntigravityAdapter(),
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
