"""Drive an installed, already-logged-in agent CLI (Claude Code / OpenCode).

The plugin hosts a local MCP server (see ``server.mcp_server``); this backend
spawns the CLI in headless mode pointed at that server, so the CLI's own agent
loop calls the QGIS tools. Because the CLI is already authenticated, no API
key is needed. We stream the CLI's stdout back into the chat dock.

Conversation continuity is handled per-tool: Claude Code returns a
``session_id`` we ``--resume`` on later turns.
"""

import json
import os
import shutil
import subprocess

from .base import AgentBackend, AgentEvent, EventType

# Fallback locations discovered on this machine, used if PATH lookup fails.
_KNOWN_PATHS = {
    "claude": [
        "/Applications/cmux.app/Contents/Resources/bin/claude",
        os.path.expanduser("~/.claude/local/claude"),
    ],
    "opencode": [
        os.path.expanduser("~/.opencode/bin/opencode"),
    ],
    "codex": [
        os.path.expanduser("~/.codex/bin/codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ],
    "gemini": [
        os.path.expanduser("~/.gemini/bin/gemini"),
        "/opt/homebrew/bin/gemini",
        "/usr/local/bin/gemini",
    ],
}


def _resolve_binary(tool, explicit_path):
    if explicit_path and os.path.exists(explicit_path):
        return explicit_path
    found = shutil.which(tool)
    if found:
        return found
    for candidate in _KNOWN_PATHS.get(tool, []):
        if os.path.exists(candidate):
            return candidate
    return None


class CliToolBackend(AgentBackend):
    def __init__(self, config, server_provider):
        self.config = config
        self.tool = config.get("cli_tool")
        self.binary = _resolve_binary(self.tool, config.get("cli_path"))
        # Zero-arg callable returning the running MCP server's base URL.
        self._server_provider = server_provider
        self._proc = None
        self._session_id = None

    @property
    def label(self):
        return f"CLI ({self.tool})"

    def validate(self):
        if self.binary is None:
            return (f"Could not find the '{self.tool}' binary. Set its path in "
                    "Settings, or make sure it is on PATH.")
        if self._server_provider is None or self._server_provider() is None:
            return "The local MCP bridge could not start."
        return None

    # ------------------------------------------------------------------ #
    # Login / auth helpers
    # ------------------------------------------------------------------ #

    def _check_login_cmd(self):
        """Return the command to check authentication status."""
        if self.tool == "claude":
            return [self.binary, "status"]
        if self.tool == "opencode":
            return [self.binary, "status"]
        if self.tool == "codex":
            return [self.binary, "status"]
        if self.tool == "gemini":
            return [self.binary, "status"]
        return [self.binary, "status"]

    def check_login(self):
        """Return True if the CLI reports an active session."""
        if not self.binary:
            return False
        try:
            result = subprocess.run(
                self._check_login_cmd(),
                capture_output=True, text=True, timeout=8,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def _login_cmd(self):
        """Return the command to open a browser login flow."""
        if self.tool == "claude":
            return [self.binary, "login"]
        if self.tool == "opencode":
            return [self.binary, "login"]
        if self.tool == "codex":
            return [self.binary, "login"]
        if self.tool == "gemini":
            return [self.binary, "login"]
        return [self.binary, "login"]

    def login_browser(self):
        """Launch the browser-based login flow."""
        if not self.binary:
            return False
        try:
            subprocess.Popen(
                self._login_cmd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    def _mcp_config_json(self, base_url):
        return json.dumps({
            "mcpServers": {
                "AgenticGIS": {"type": "http", "url": base_url}
            }
        })

    def _build_command(self, message, base_url):
        if self.tool == "claude":
            cmd = [
                self.binary, "-p", message,
                "--output-format", "stream-json", "--verbose",
                "--mcp-config", self._mcp_config_json(base_url),
                "--permission-mode", "bypassPermissions",
                "--allowedTools", "mcp__AgenticGIS",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd
        if self.tool == "opencode":
            return [self.binary, "run", message]
        if self.tool == "codex":
            # Codex CLI (beta) — headless mode with MCP config
            cmd = [
                self.binary, "run", message,
                "--mcp-config", self._mcp_config_json(base_url),
                "--json",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd
        if self.tool == "gemini":
            # Gemini CLI — stream JSON output
            return [self.binary, "run", message, "--format", "json"]
        # Fallback: run with message as the only argument
        return [self.binary, message]

    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        base_url = self._server_provider()
        cmd = self._build_command(message, base_url)
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except Exception as exc:
            emit(AgentEvent(EventType.ERROR, {"error": f"Failed to launch {self.tool}: {exc}"}))
            return history

        import threading
        import queue

        def _enqueue(pipe, q, kind):
            for line in pipe:
                q.put((kind, line))
            q.put((kind, None))   # sentinel

        q = queue.Queue()
        stderr_acc = []

        t_out = threading.Thread(target=_enqueue, args=(self._proc.stdout, q, "out"))
        t_err = threading.Thread(target=_enqueue, args=(self._proc.stderr, q, "err"))
        t_out.start()
        t_err.start()

        open_readers = 2
        try:
            while open_readers > 0:
                if should_stop():
                    self._proc.terminate()
                    break
                try:
                    kind, line = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:          # sentinel: that pipe closed
                    open_readers -= 1
                    continue
                line = line.strip()
                if not line:
                    continue
                if kind == "out":
                    if self.tool == "claude":
                        self._handle_claude_event(line, emit)
                    else:
                        emit(AgentEvent(EventType.TEXT, {"text": line + "\n"}))
                else:
                    stderr_acc.append(line)

            self._proc.wait(timeout=5)
        except Exception as exc:
            emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
        finally:
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            if self._proc:
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
                if self._proc.returncode not in (0, None) and stderr_acc:
                    emit(AgentEvent(EventType.ERROR,
                                    {"error": "\n".join(stderr_acc)[:2000]}))
                self._proc = None

        emit(AgentEvent(EventType.DONE))
        return history

    def _handle_claude_event(self, line, emit):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        etype = event.get("type")
        if etype == "system" and event.get("session_id"):
            self._session_id = event["session_id"]
        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    emit(AgentEvent(EventType.TEXT, {"text": block["text"]}))
                elif block.get("type") == "tool_use":
                    emit(AgentEvent(EventType.TOOL_USE,
                                    {"name": block.get("name"), "input": block.get("input")}))
        elif etype == "user":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    text = content if isinstance(content, str) else json.dumps(content, default=str)
                    emit(AgentEvent(EventType.TOOL_RESULT,
                                    {"name": "tool", "result": text[:4000]}))
        elif etype == "result":
            if event.get("session_id"):
                self._session_id = event["session_id"]
