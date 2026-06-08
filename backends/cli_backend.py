"""Use an installed, already-logged-in agent CLI as a model proxy.

This backend no longer exposes QGIS through MCP to the spawned CLI. Instead it
prompts the CLI for a small JSON instruction, then AgenticGIS executes any QGIS
tool calls in-process just like the custom/API backends. Because the CLI is
already authenticated, no API key is needed and AgenticGIS never reads the
CLI-owned OAuth tokens.

Conversation continuity is handled per-tool: Claude Code returns a
``session_id`` we ``--resume`` on later turns.

Reliability hardening
---------------------
* Robust JSONL parsing — read raw bytes and split on ``\\n`` ourselves, so
  multi-line JSON records (or two records in one ``write()``) survive.
* Single select()-driven reader so a verbose CLI cannot deadlock us by
  filling the 64 kB pipe buffer. We never spawn two blocking reader
  threads.
* ``start_new_session=True`` so Ctrl-C in the spawned CLI doesn't kill
  QGIS, and so child file descriptors don't leak.
* A bounded ``kill_timeout`` (5 s) before we escalate to ``SIGKILL`` if
  the process refuses to terminate cleanly.
"""

import json
import os
import platform
import select
import shlex
import shutil
import signal
import subprocess  # nosec B404
import tempfile
import threading
import time

from ..core import tools as tools_mod
from ..core.dev_logging import log_event
from .adapters import (
    ClaudeAdapter,
    CodexAdapter,
    CopilotAdapter,
    CursorAdapter,
    DefaultAdapter,
    DevinAdapter,
    GeminiAdapter,
    KimiAdapter,
    KiroAdapter,
    OpenCodeAdapter,
    PiAdapter,
    QwenAdapter,
    get_adapter,
)
from .base import (
    AgentBackend,
    AgentEvent,
    EventType,
    _dispatch_one_tool,
    agent_iteration_steps,
)
from .openai_backend import DEFAULT_SYSTEM_PROMPT as AGENTICGIS_SYSTEM_PROMPT

CLI_AGENT_CATALOG = (
    {
        "id": "claude",
        "label": "Claude Code",
        "commands": ("claude",),
        "credential_style": "Claude subscription or Anthropic credentials",
        "warning": "Provider policy may treat third-party automation differently.",
        "adapter_class": ClaudeAdapter,
    },
    {
        "id": "codex",
        "label": "Codex CLI",
        "commands": ("codex",),
        "credential_style": "OpenAI API key or ChatGPT account in Codex",
        "adapter_class": CodexAdapter,
    },
    {
        "id": "cursor",
        "label": "Cursor Agent",
        "commands": ("cursor-agent", "cursor"),
        "credential_style": "Cursor account or configured provider keys",
        "adapter_class": CursorAdapter,
    },
    {
        "id": "gemini",
        "label": "Gemini CLI",
        "commands": ("gemini",),
        "credential_style": "Google account or Gemini API key",
        "adapter_class": GeminiAdapter,
    },
    {
        "id": "copilot",
        "label": "GitHub Copilot CLI",
        "commands": ("gh", "copilot"),
        "credential_style": "GitHub Copilot subscription",
        "adapter_class": CopilotAdapter,
    },
    {
        "id": "opencode",
        "label": "OpenCode",
        "commands": ("opencode",),
        "credential_style": "Provider keys in OpenCode config",
        "adapter_class": OpenCodeAdapter,
    },
    {
        "id": "qwen",
        "label": "Qwen Code",
        "commands": ("qwen",),
        "credential_style": "DashScope or Qwen API key",
        "adapter_class": QwenAdapter,
    },
    {
        "id": "grok",
        "label": "Grok",
        "commands": ("grok",),
        "credential_style": "xAI account or key",
        "adapter_class": DefaultAdapter,
    },
    {
        "id": "hermes",
        "label": "Hermes",
        "commands": ("hermes",),
        "credential_style": "Configured provider keys",
        "adapter_class": DefaultAdapter,
    },
    {
        "id": "kimi",
        "label": "Kimi CLI",
        "commands": ("kimi",),
        "credential_style": "Moonshot/Kimi API key",
        "adapter_class": KimiAdapter,
    },
    {
        "id": "devin",
        "label": "Devin for Terminal",
        "commands": ("devin",),
        "credential_style": "Devin account",
        "adapter_class": DevinAdapter,
    },
    {
        "id": "deepseek_tui",
        "label": "DeepSeek TUI",
        "commands": ("deepseek", "deepseek-tui"),
        "credential_style": "DeepSeek API key",
        "adapter_class": DefaultAdapter,
    },
    {
        "id": "pi",
        "label": "Pi",
        "commands": ("pi",),
        "credential_style": "Pi account",
        "adapter_class": PiAdapter,
    },
    {
        "id": "mistral_vibe",
        "label": "Mistral Vibe CLI",
        "commands": ("mistral-vibe", "vibe"),
        "credential_style": "Mistral API key",
        "adapter_class": DefaultAdapter,
    },
    {
        "id": "kiro",
        "label": "Kiro CLI",
        "commands": ("kiro",),
        "credential_style": "AWS credentials",
        "adapter_class": KiroAdapter,
    },
    {
        "id": "kilo",
        "label": "Kilo",
        "commands": ("kilo",),
        "credential_style": "Configured provider keys",
        "adapter_class": DefaultAdapter,
    },
    {
        "id": "qoder",
        "label": "Qoder CLI",
        "commands": ("qoder",),
        "credential_style": "Qoder account or provider keys",
        "adapter_class": DefaultAdapter,
    },
)

_AGENT_BY_ID = {agent["id"]: agent for agent in CLI_AGENT_CATALOG}
_BINARY_RESOLUTION_CACHE = {}
_BINARY_RESOLUTION_LOCK = threading.Lock()
_BINARY_MISS_TTL_SECONDS = 5.0


def agent_by_id(agent_id):
    """Return a CLI agent catalog entry by id, or None."""
    return _AGENT_BY_ID.get(agent_id)


def _windows_pathexts():
    raw = os.environ.get("PATHEXT") or ".EXE;.CMD;.BAT;.COM"
    exts = []
    for ext in raw.split(";"):
        value = ext.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = "." + value
        if value not in exts:
            exts.append(value)
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if ext not in exts:
            exts.append(ext)
    return tuple(exts)


def _expand_candidate_path(path):
    expanded = os.path.expandvars(str(path))
    if platform.system() == "Windows":
        home = _windows_home_dir()
        if home and expanded == "~":
            return home
        if home and expanded.startswith(("~/", "~\\")):
            return os.path.join(home, expanded[2:].lstrip("\\/"))
    return os.path.expanduser(expanded)


def _is_usable_file(path):
    if not path:
        return False
    path = _expand_candidate_path(path)
    if not os.path.isfile(path):
        return False
    if platform.system() == "Windows":
        return os.path.splitext(path)[1].lower() in _windows_pathexts()
    return os.access(path, os.X_OK)


def _subprocess_cmd(cmd):
    """Wrap .cmd/.bat files with ``cmd.exe /c`` on Windows.

    On Windows only .exe files are directly executable by the OS loader.
    npm-global and Scoop shims are .cmd files; without this wrapper
    subprocess raises WinError 193 / WinError 2.
    """
    if not cmd or platform.system() != "Windows":
        return list(cmd)
    binary = cmd[0]
    if isinstance(binary, str) and binary.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c"] + list(cmd)
    return list(cmd)


def _creation_flags():
    """Return ``creationflags`` that suppress the console window on Windows.

    Without ``CREATE_NO_WINDOW`` (Win32 0x08000000), Windows briefly
    allocates a console for every spawned CLI binary (codex.exe,
    gemini.cmd, gh.exe, ...) and shows it as a flashing terminal popup,
    even when ``stdout``/``stderr`` are captured. Returning 0 on
    non-Windows keeps the default behaviour (which on POSIX does not
    open a visible window).

    The constant is referenced as ``subprocess.CREATE_NO_WINDOW`` on
    Python 3.7+; the literal is used here as a fallback for type-checkers
    that don't see the attribute.
    """
    if platform.system() == "Windows":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def _decode_process_output(data):
    if not data:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _process_output(result):
    stdout = _decode_process_output(getattr(result, "stdout", b""))
    stderr = _decode_process_output(getattr(result, "stderr", b""))
    return f"{stdout}\n{stderr}".strip()


def _looks_like_agent_binary(path):
    """Filter out launcher stubs that exist but immediately report missing tools."""
    try:
        result = subprocess.run(  # nosec B603
            _subprocess_cmd([path, "--version"]),
            capture_output=True,
            timeout=4,
            creationflags=_creation_flags(),
        )
    except Exception:
        return True
    output = _process_output(result).lower()
    if result.returncode == 127 and "not found in path" in output:
        return False
    return True


def _unique_paths(paths):
    seen = set()
    for path in paths:
        if not path:
            continue
        expanded = _expand_candidate_path(path)
        key = os.path.normcase(os.path.abspath(expanded))
        if key in seen:
            continue
        seen.add(key)
        yield expanded


def _windows_home_dir():
    userprofile = os.environ.get("USERPROFILE") or ""
    if userprofile:
        return userprofile
    home_drive = os.environ.get("HOMEDRIVE") or ""
    home_path = os.environ.get("HOMEPATH") or ""
    if home_drive and home_path:
        return home_drive + home_path
    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        return expanded
    return ""


def _empty_runtime_dir(name):
    path = os.path.join(tempfile.gettempdir(), "AgenticGIS", name)
    os.makedirs(path, exist_ok=True)
    return path


_COMMON_RUNTIME_ENV = {
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "PI_OFFLINE": "1",
}


_RUNTIME_PATH_HINTS = (
    # Tool-specific well-known bin dirs
    "~/.local/bin",
    "~/.opencode/bin",
    "~/.codex/bin",
    "~/.gemini/bin",
    "~/.claude/local",
    "~/.kimi-code/bin",
    "~/.kiro/bin",
    "~/node_modules/.bin",
    # System package managers
    "/opt/homebrew/bin",
    "/opt/homebrew/Cellar/node/*/bin",
    "/opt/local/bin",  # MacPorts
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/snap/bin",
)


_ENV_AUTH_KEYS = {
    "pi": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "KIMI_API_KEY",
        "MISTRAL_API_KEY",
    ),
}


_EXTRA_CANDIDATE_ROOTS = {
    "claude": ("~/.claude/local", "~/.local/share/claude"),
    "opencode": ("~/.opencode/bin",),
    "gemini": ("~/.gemini/bin",),
    "codex": ("~/.codex/bin",),
    "kimi": ("~/.kimi-code/bin", "~/.kimi/bin"),
    "kiro": ("~/.kiro/bin",),
    "pi": (
        "~/.pi/bin",
        "~/.local/share/pi",
        "/opt/homebrew/bin",
        "/opt/homebrew/Cellar/node/*/bin",
    ),
}


def _existing_dirs(paths):
    out = []
    for path in paths:
        if not path:
            continue
        expanded = _expand_candidate_path(path)
        if "*" in expanded:
            import glob

            candidates = glob.glob(expanded)
        else:
            candidates = [expanded]
        for candidate in candidates:
            if os.path.isdir(candidate):
                out.append(candidate)
    return out


def _prepend_path(env, paths):
    path_key = next((key for key in env if key.lower() == "path"), "PATH")
    existing = env.get(path_key, "")
    seen = set()
    merged = []
    for entry in list(paths) + [part for part in existing.split(os.pathsep) if part]:
        norm = os.path.normcase(os.path.abspath(_expand_candidate_path(entry)))
        if norm in seen:
            continue
        seen.add(norm)
        merged.append(entry)
    env[path_key] = os.pathsep.join(merged)


def _scrub_child_env(env):
    exact = {
        "CODEX_CI",
        "CODEX_SANDBOX",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_THREAD_ID",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_ENTRYPOINT",
    }
    prefixes = (
        "MCP__PLUGIN_",
        "SUPERPOWERS_",
    )
    for key in list(env):
        upper = key.upper()
        if upper in exact or any(upper.startswith(prefix) for prefix in prefixes):
            env.pop(key, None)


def _windows_program_dirs():
    yielded = False
    for key in (
        "LOCALAPPDATA",
        "APPDATA",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
    ):
        value = os.environ.get(key)
        if value:
            yielded = True
            yield value
    userprofile = _windows_home_dir()
    if userprofile:
        yielded = True
        yield os.path.join(userprofile, ".local", "bin")
        yield os.path.join(userprofile, "AppData", "Roaming")
        yield os.path.join(userprofile, "AppData", "Local")
    if not yielded:
        yield "~/.local/bin"
        yield "~/AppData/Roaming/npm"
        yield "~/AppData/Local/Programs"


def _windows_package_manager_bin_dirs():
    """Yield known package-manager binary/shim directories on Windows.

    These are directories where executables land directly (no further
    sub-path joining needed).  Checked in order: env-var-customised paths
    first, then well-known defaults.
    """
    userprofile = _windows_home_dir()
    localappdata = os.environ.get("LOCALAPPDATA") or (
        os.path.join(userprofile, "AppData", "Local") if userprofile else ""
    )
    appdata = os.environ.get("APPDATA") or (
        os.path.join(userprofile, "AppData", "Roaming") if userprofile else ""
    )
    programfiles = os.environ.get("ProgramFiles") or ""
    programdata = os.environ.get("ProgramData") or ""

    npm_prefix = (
        os.environ.get("NPM_CONFIG_PREFIX") or os.environ.get("npm_config_prefix") or ""
    )
    if npm_prefix:
        # Windows npm global shims live directly in the prefix. Some custom
        # prefixes still use a bin subdir, so check both.
        yield npm_prefix
        yield os.path.join(npm_prefix, "bin")

    # npm global: on Windows executables land directly in the prefix dir.
    # Default prefix is %APPDATA%\npm (NOT %APPDATA%\npm\bin as on Unix).
    if appdata:
        yield os.path.join(appdata, "npm")

    if userprofile:
        # User-level toolchain bins that GUI apps commonly miss when PATH is
        # stripped. Mirrors Open Design's "well known user toolchain bins"
        # approach, adjusted for Windows npm shim layout.
        for path in (
            os.path.join(userprofile, ".local", "bin"),
            os.path.join(userprofile, ".codex", "bin"),
            os.path.join(userprofile, ".vite-plus", "bin"),
            os.path.join(userprofile, ".bun", "bin"),
            os.path.join(userprofile, ".cargo", "bin"),
            os.path.join(userprofile, ".asdf", "shims"),
            os.path.join(userprofile, "node_modules", ".bin"),
            os.path.join(userprofile, ".npm-global"),
            os.path.join(userprofile, ".npm-global", "bin"),
            os.path.join(userprofile, ".npm-packages"),
            os.path.join(userprofile, ".npm-packages", "bin"),
            os.path.join(userprofile, ".local", "share", "mise", "shims"),
            os.path.join(
                userprofile,
                ".local",
                "share",
                "mise",
                "installs",
                "npm-openai-codex",
                "*",
                "bin",
            ),
            os.path.join(
                userprofile, ".local", "share", "mise", "installs", "node", "*", "bin"
            ),
            os.path.join(userprofile, ".nvm", "versions", "node", "*", "bin"),
            os.path.join(
                userprofile,
                ".local",
                "share",
                "fnm",
                "node-versions",
                "*",
                "installation",
                "bin",
            ),
            os.path.join(
                userprofile, ".fnm", "node-versions", "*", "installation", "bin"
            ),
        ):
            yield path

    bun_install = os.environ.get("BUN_INSTALL") or ""
    if bun_install:
        yield os.path.join(bun_install, "bin")

    # Scoop shims (per-user install)
    scoop_root = os.environ.get("SCOOP") or (
        os.path.join(userprofile, "scoop") if userprofile else ""
    )
    if scoop_root:
        yield os.path.join(scoop_root, "shims")

    # Scoop shims (system-wide install)
    scoop_global = os.environ.get("SCOOP_GLOBAL") or (
        os.path.join(programdata, "scoop") if programdata else ""
    )
    if scoop_global:
        yield os.path.join(scoop_global, "shims")

    # Volta shims: %VOLTA_HOME%\bin or %LOCALAPPDATA%\Volta\bin
    volta_home = os.environ.get("VOLTA_HOME") or (
        os.path.join(localappdata, "Volta") if localappdata else ""
    )
    if volta_home:
        yield os.path.join(volta_home, "bin")
    # Volta system installer puts binaries directly in %ProgramFiles%\Volta
    if programfiles:
        yield os.path.join(programfiles, "Volta")

    # Chocolatey: %ChocolateyInstall%\bin
    choco = os.environ.get("ChocolateyInstall") or (
        os.path.join(programdata, "chocolatey") if programdata else ""
    )
    if choco:
        yield os.path.join(choco, "bin")

    # pnpm global: %PNPM_HOME% or %LOCALAPPDATA%\pnpm
    pnpm_home = os.environ.get("PNPM_HOME") or (
        os.path.join(localappdata, "pnpm") if localappdata else ""
    )
    if pnpm_home:
        yield pnpm_home

    # nvm-windows: NVM_SYMLINK points to the currently active Node.js dir
    nvm_symlink = os.environ.get("NVM_SYMLINK")
    if nvm_symlink:
        yield nvm_symlink
    # NVM_HOME contains per-version subdirs; the active symlink is better but
    # fall back to probing version dirs via glob in _candidate_paths.
    nvm_home = os.environ.get("NVM_HOME")
    if nvm_home:
        yield nvm_home

    # Yarn global: %LOCALAPPDATA%\Yarn\bin
    if localappdata:
        yield os.path.join(localappdata, "Yarn", "bin")

    # winget managed symlinks: %LOCALAPPDATA%\Microsoft\WinGet\Links
    if localappdata:
        yield os.path.join(localappdata, "Microsoft", "WinGet", "Links")

    # Windows Store/App Installer aliases, including Codex app aliases, are
    # normally on a terminal PATH but often absent from GUI-launched QGIS.
    if localappdata:
        yield os.path.join(localappdata, "Microsoft", "WindowsApps")

    # Microsoft Store package payloads can expose Codex under WindowsApps.
    # Best-effort glob; if Windows denies listing this folder it is ignored.
    if programfiles:
        yield os.path.join(programfiles, "WindowsApps", "OpenAI.Codex_*", "app")


def _unix_package_manager_bin_dirs():
    """Yield known package-manager binary directories on macOS and Linux.

    Called when QGIS launches without the user's full shell PATH.
    Respects env-var overrides before falling back to well-known defaults.
    Glob patterns (``*``) are left in place — callers that pass results
    through ``_existing_dirs`` or ``_resolve_binary`` expand them correctly.
    """
    # Volta: $VOLTA_HOME/bin or ~/.volta/bin
    volta_home = os.environ.get("VOLTA_HOME") or "~/.volta"
    yield os.path.join(volta_home, "bin")

    # nvm: newest installed Node version first when glob-sorted
    nvm_dir = os.environ.get("NVM_DIR") or "~/.nvm"
    yield os.path.join(nvm_dir, "versions", "node", "*", "bin")

    # fnm (Fast Node Manager): env var or OS-specific defaults
    fnm_dir = os.environ.get("FNM_DIR")
    if fnm_dir:
        yield os.path.join(fnm_dir, "node-versions", "*", "installation", "bin")
    else:
        yield "~/.local/share/fnm/node-versions/*/installation/bin"
        yield "~/Library/Application Support/fnm/node-versions/*/installation/bin"

    # asdf version manager shims: $ASDF_DATA_DIR/shims or ~/.asdf/shims
    asdf_data = os.environ.get("ASDF_DATA_DIR") or "~/.asdf"
    yield os.path.join(asdf_data, "shims")

    # mise / rtx shims: $MISE_DATA_DIR/shims or ~/.local/share/mise/shims
    mise_data = os.environ.get("MISE_DATA_DIR") or "~/.local/share/mise"
    yield os.path.join(mise_data, "shims")
    yield "~/.mise/shims"

    # pnpm global bin: $PNPM_HOME or ~/.local/share/pnpm
    pnpm_home = os.environ.get("PNPM_HOME") or "~/.local/share/pnpm"
    yield pnpm_home

    # Yarn global bin: $YARN_GLOBAL_FOLDER/bin or ~/.yarn/bin
    yarn_global = os.environ.get("YARN_GLOBAL_FOLDER")
    yield os.path.join(yarn_global, "bin") if yarn_global else "~/.yarn/bin"

    # MacPorts
    yield "/opt/local/bin"


def _command_file_names(command, system=None):
    system = system or platform.system()
    if system == "Windows":
        if os.path.splitext(command)[1].lower() in _windows_pathexts():
            return (command,)
        return tuple(f"{command}{ext}" for ext in _windows_pathexts()) + (command,)
    return (command,)


def _agent_id_for_binary_path(path, system=None):
    """Infer the catalog agent from a selected binary path, if possible."""
    if not path:
        return None
    system = system or platform.system()
    base = os.path.basename(str(path).replace("\\", "/")).lower()
    stem, _ext = os.path.splitext(base)
    for agent in CLI_AGENT_CATALOG:
        for command in agent.get("commands", (agent["id"],)):
            names = {name.lower() for name in _command_file_names(command, system)}
            stems = {os.path.splitext(name)[0] for name in names}
            if base in names or stem in stems:
                return agent["id"]
    return None


def _candidate_paths(tool, command, system=None):
    """Known install locations when QGIS launches without the user's shell PATH."""
    system = system or platform.system()
    names = _command_file_names(command, system)
    paths = []

    if system == "Windows":
        # 1. Package-manager shim/bin dirs — executables live here directly.
        for bin_dir in _windows_package_manager_bin_dirs():
            for name in names:
                paths.append(os.path.join(bin_dir, name))

        # 2. nvm-windows version dirs via glob (%NVM_HOME%\v*\)
        nvm_home = os.environ.get("NVM_HOME")
        if nvm_home:
            import glob as _glob

            for ver_dir in sorted(
                _glob.glob(os.path.join(nvm_home, "v*")), reverse=True
            ):
                for name in names:
                    paths.append(os.path.join(ver_dir, name))

        # 3. Broader program-root dirs with common sub-path patterns.
        for root in _windows_program_dirs():
            for name in names:
                paths.extend(
                    [
                        os.path.join(root, name),
                        os.path.join(root, "npm", name),
                        os.path.join(root, "bin", name),
                        os.path.join(root, "Programs", name),
                        os.path.join(root, "Programs", tool, name),
                        os.path.join(root, tool, name),
                        os.path.join(root, "Claude", name),
                        os.path.join(root, "ClaudeCode", name),
                        os.path.join(root, "Anthropic", "Claude Code", name),
                    ]
                )

        # 4. Per-tool known install roots, e.g. ~/.codex/bin/codex.cmd.
        for root in _EXTRA_CANDIDATE_ROOTS.get(tool, ()):
            for name in names:
                paths.append(os.path.join(root, name))
        return list(_unique_paths(paths))

    # 1. Package-manager shim/bin dirs (Volta, nvm, fnm, asdf, mise, pnpm, Yarn…)
    for bin_dir in _unix_package_manager_bin_dirs():
        for name in names:
            paths.append(os.path.join(bin_dir, name))

    # 2. Common user-local and npm-global dirs
    home_roots = [
        "~/.local/bin",
        f"~/.{tool}/bin",
        f"~/.{tool}/local",
        "~/.npm-global/bin",
        "~/node_modules/.bin",
    ]

    # 3. System-wide package manager prefixes
    package_roots = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/snap/bin",
        "/opt/local/bin",  # MacPorts
    ]
    if system == "Darwin":
        package_roots.extend(
            [
                "/opt/homebrew/Cellar/node/*/bin",
                "/Applications/Claude.app/Contents/MacOS",
                "/Applications/Claude Code.app/Contents/MacOS",
            ]
        )

    for root in home_roots + package_roots:
        for name in names:
            paths.append(os.path.join(root, name))

    # 4. Per-tool known install roots
    for root in _EXTRA_CANDIDATE_ROOTS.get(tool, ()):
        for name in names:
            paths.append(os.path.join(root, name))

    return list(_unique_paths(paths))


def _binary_resolution_key(tool, explicit_path):
    path = ""
    if explicit_path:
        expanded = _expand_candidate_path(explicit_path)
        path = os.path.normcase(os.path.abspath(expanded))
    return str(tool or ""), path


def _resolve_binary_uncached(tool, explicit_path):
    if explicit_path and _is_usable_file(explicit_path):
        return explicit_path
    agent = agent_by_id(tool)
    command_names = agent.get("commands", (tool,)) if agent else (tool,)
    for command in command_names:
        found = shutil.which(command)
        if found and _looks_like_agent_binary(found):
            return found
        for candidate in _candidate_paths(tool, command):
            if "*" in candidate:
                import glob

                expanded = sorted(glob.glob(candidate), reverse=True)
            else:
                expanded = [candidate]
            for path in expanded:
                if _is_usable_file(path) and _looks_like_agent_binary(path):
                    return path
    return None


def _resolve_binary(tool, explicit_path):
    key = _binary_resolution_key(tool, explicit_path)
    now = time.monotonic()
    with _BINARY_RESOLUTION_LOCK:
        cached = _BINARY_RESOLUTION_CACHE.get(key)
        if cached is not None:
            path, cached_at = cached
            if path:
                if _is_usable_file(path):
                    return path
                _BINARY_RESOLUTION_CACHE.pop(key, None)
            elif now - cached_at < _BINARY_MISS_TTL_SECONDS:
                return None

        path = _resolve_binary_uncached(tool, explicit_path)
        _BINARY_RESOLUTION_CACHE[key] = (path, now)
        return path


def scan_cli_agents(path_overrides=None):
    """Return catalog rows with detected path/install status."""
    path_overrides = path_overrides or {}
    rows = []
    for index, agent in enumerate(CLI_AGENT_CATALOG):
        row = dict(agent)
        path = _resolve_binary(agent["id"], path_overrides.get(agent["id"], ""))
        row["path"] = path or ""
        row["real_path"] = os.path.realpath(path) if path else ""
        row["installed"] = bool(path)
        row["_catalog_index"] = index
        rows.append(row)
    rows.sort(key=lambda row: (not row["installed"], row["_catalog_index"]))
    return rows


class NormalizingStream:
    """Reads raw JSONL lines from a CLI subprocess and emits AgentEvents.

    The single bridge between a CLI's wire format and the AgentEvent
    emit pipeline. ``send()`` reads ``final_text`` and
    ``pending_tool_call`` from the stream after ``_collect_into_stream``
    returns to drive the agent tool loop.
    """

    _STARTUP_NOISE_MARKERS = (
        "hookSpecificOutput",
        "SessionStart",
        "<context_window_protection>",
        "<EXTREMELY_IMPORTANT>",
        "context-mode",
        "superpowers",
    )

    def __init__(self, adapter, emit, process_id=None):
        self.adapter = adapter
        self.emit = emit
        self.process_id = process_id
        self.session_id = ""
        self.final_text = None
        self.pending_tool_call = None
        self.had_error = False
        self.content_blocks = []
        self.finish_reason = None
        self._text_emitted = False
        self._first_event_logged = False
        self._first_text_logged = False
        # Deduplicate tool calls that arrive twice (e.g. Claude CLI may emit
        # both a content_block_delta and raw JSON for the same call).
        self._emitted_tool_call_keys = set()

    def _log_first_event(self, event_type):
        if self._first_event_logged:
            return
        self._first_event_logged = True
        log_event(
            "cli.stream.first_event",
            tool=self.adapter.id,
            pid=self.process_id,
            event_type=event_type,
        )

    def _log_first_text(self):
        if self._first_text_logged:
            return
        self._first_text_logged = True
        log_event(
            "cli.stream.first_text",
            tool=self.adapter.id,
            pid=self.process_id,
        )

    @classmethod
    def _is_startup_noise(cls, raw_bytes: bytes, raw_obj) -> bool:
        if isinstance(raw_obj, dict) and "hookSpecificOutput" in raw_obj:
            return True
        for marker in cls._STARTUP_NOISE_MARKERS:
            if marker in raw_bytes.decode("utf-8", "replace"):
                return True
        return False

    def feed_line(self, raw_bytes: bytes) -> None:
        decoded = raw_bytes.decode("utf-8", "replace")
        stripped = decoded.strip()

        # Early protocol check: if the raw line is the AgenticGIS tool_calls
        # protocol JSON, parse it immediately before any other processing.
        # This catches both raw protocol JSON and protocol embedded in CLI
        # text events (e.g. Claude's content_block_delta text fields).
        if stripped.startswith("{"):
            protocol = self.adapter.parse_protocol_text(stripped)
            if protocol is not None:
                self._log_first_event("tool_calls")
                for call in protocol.tool_calls:
                    key = (call.get("name"), json.dumps(call.get("arguments", {}), sort_keys=True))
                    if key in self._emitted_tool_call_keys:
                        continue
                    self._emitted_tool_call_keys.add(key)
                    if self.pending_tool_call is None:
                        self.pending_tool_call = call
                    self.emit(
                        AgentEvent(
                            EventType.TOOL_USE,
                            {
                                "name": call["name"],
                                "input": call.get("arguments", {}),
                            },
                        )
                    )
                if protocol.is_final:
                    self.final_text = protocol.text or self.final_text
                return

        try:
            raw = json.loads(decoded)
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = stripped
            if text and not self._is_startup_noise(raw_bytes, {}):
                self._log_first_event("text")
                self._log_first_text()
                self.emit(AgentEvent(EventType.TEXT, {"text": text}))
                self.final_text = text
            return
        if not isinstance(raw, dict):
            return
        if self._is_startup_noise(raw_bytes, raw):
            return
        self._log_first_event(raw.get("type") or "unknown")
        norm = self.adapter.parse_event(raw)
        if norm is None:
            # Fallback: check if raw JSON is the tool_calls protocol
            protocol = self.adapter.parse_protocol_text(decoded)
            if protocol is not None:
                for call in protocol.tool_calls:
                    key = (call.get("name"), json.dumps(call.get("arguments", {}), sort_keys=True))
                    if key in self._emitted_tool_call_keys:
                        continue
                    self._emitted_tool_call_keys.add(key)
                    if self.pending_tool_call is None:
                        self.pending_tool_call = call
                    self.emit(
                        AgentEvent(
                            EventType.TOOL_USE,
                            {
                                "name": call["name"],
                                "input": call.get("arguments", {}),
                            },
                        )
                    )
                if protocol.is_final:
                    self.final_text = protocol.text or self.final_text
                return
            sid = raw.get("session_id") or raw.get("sessionID") or ""
            if sid:
                self.session_id = sid
            return
        if norm.session_id:
            self.session_id = norm.session_id
        if norm.is_error:
            self.had_error = True
            err_text = norm.text[:2000] if norm.text else ""
            self.emit(AgentEvent(EventType.ERROR, {"error": err_text}))
            return
        if norm.text:
            # If the assistant's text is actually the AgenticGIS
            # tool_calls protocol JSON, convert it to a tool call
            # rather than emitting the raw JSON as a chat message.
            protocol_event = self.adapter.parse_protocol_text(norm.text)
            if protocol_event is not None:
                norm = protocol_event
            else:
                # Suppress duplicate final text when it was already
                # streamed via deltas (e.g. Codex task_complete).
                if not (norm.is_final and self._text_emitted):
                    self._log_first_text()
                    self.emit(AgentEvent(EventType.TEXT, {"text": norm.text}))
                    self._text_emitted = True
        for call in norm.tool_calls:
            # Deduplicate identical tool calls (Claude CLI may emit the
            # same call via both content_block_delta and raw JSON).
            key = (call.get("name"), json.dumps(call.get("arguments", {}), sort_keys=True))
            if key in self._emitted_tool_call_keys:
                continue
            self._emitted_tool_call_keys.add(key)
            if self.pending_tool_call is None:
                self.pending_tool_call = call
            self.emit(
                AgentEvent(
                    EventType.TOOL_USE,
                    {
                        "name": call["name"],
                        "input": call.get("arguments", {}),
                    },
                )
            )
            if "output" in call:
                self.emit(
                    AgentEvent(
                        EventType.TOOL_RESULT,
                        {
                            "name": call["name"],
                            "result": str(call["output"])[:4000],
                            "is_error": bool(call.get("is_error", False)),
                        },
                    )
                )
        if norm.is_final:
            self.final_text = norm.text or self.final_text


class CliToolBackend(AgentBackend):
    def __init__(self, config, toolkit=None, executor=None, server_provider=None):
        self.config = config
        self.tool = config.get("cli_tool")
        self.binary = _resolve_binary(self.tool, config.get("cli_path"))
        try:
            self.extra_args = shlex.split(config.get("cli_args") or "")
        except ValueError:
            self.extra_args = []
        self.toolkit = toolkit
        self.executor = executor
        # Kept only for backwards-compatible construction; no longer used for
        # CLI chat because CLI mode is an in-process proxy, not an MCP client.
        self._server_provider = server_provider
        # synchronise mutations of _proc and _session_id across the
        # dock and the worker thread.
        self._lock = threading.Lock()
        self._proc = None
        self._session_id = None

    @property
    def label(self):
        agent = agent_by_id(self.tool)
        label = agent["label"] if agent else self.tool
        return f"CLI Agent ({label})"

    def validate(self):
        if self.binary is None:
            return (
                f"Could not find the '{self.tool}' binary. Set its path in "
                "Settings, or make sure it is on PATH."
            )
        return None

    def export_session_state(self):
        with self._lock:
            return {"session_id": self._session_id} if self._session_id else {}

    def import_session_state(self, state):
        with self._lock:
            self._session_id = (state or {}).get("session_id") or None

    def _continuation_session_id(self, adapter):
        if not adapter.supports_continuation:
            return None
        with self._lock:
            return self._session_id

    def _remember_session_id(self, adapter, session_id):
        if not adapter.supports_continuation or not session_id:
            return
        with self._lock:
            self._session_id = session_id

    def cancel_current_request(self):
        """Terminate the active CLI process group immediately."""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        self._terminate_process_group(proc, kill=True)

    # ------------------------------------------------------------------ #
    # Login / auth helpers
    # ------------------------------------------------------------------ #

    def _check_login_cmd(self):
        """Return the command to check authentication status."""
        args = get_adapter(self.tool).auth_status_args
        return [self.binary, *args] if args else None

    def _env_auth_status(self):
        """Cheap readiness checks for CLIs that rely on provider env vars."""
        keys = _ENV_AUTH_KEYS.get(self.tool, ())
        configured = [key for key in keys if os.environ.get(key)]
        if configured:
            return "ready", f"Env key configured: {configured[0]}"
        if keys:
            return (
                "unsupported",
                "Auth check unavailable. Run the CLI directly to verify login or API keys.",
            )
        return None

    def auth_status(self):
        """Return (state, detail) for the selected CLI's auth readiness.

        ``state`` is one of: ``ready``, ``login_required``, ``unsupported``,
        or ``missing``.
        """
        if not self.binary:
            return "missing", "Binary not found"
        env_status = self._env_auth_status()
        if env_status:
            return env_status
        cmd = self._check_login_cmd()
        if not cmd:
            return "unsupported", "Auth check unavailable for this CLI."
        try:
            result = subprocess.run(  # nosec B603
                _subprocess_cmd(cmd),
                capture_output=True,
                timeout=8,
                creationflags=_creation_flags(),
            )
        except Exception as exc:  # noqa: BLE001
            return "unsupported", str(exc)

        output = _process_output(result)
        detail = output.splitlines()[0] if output else ""
        parser = get_adapter(self.tool).auth_detail_parser
        if callable(parser):
            detail = parser(output, detail)
        if result.returncode == 0:
            return "ready", detail or "Logged in"
        if result.returncode in (1, 2) and detail:
            return "login_required", detail
        return "unsupported", detail or "No auth status command available"

    def check_login(self):
        """Return True if the CLI reports an active session."""
        return self.auth_status()[0] == "ready"

    def _login_cmd(self):
        """Return the command to open a browser login flow."""
        args = get_adapter(self.tool).login_args or ("login",)
        return [self.binary, *args]

    def login_browser(self):
        """Launch the browser-based login flow."""
        if not self.binary:
            return False
        try:
            subprocess.Popen(  # nosec B603
                _subprocess_cmd(self._login_cmd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
            return True
        except Exception:
            return False

    def test_cli(self):
        """Run a lightweight smoke check for settings UI diagnostics."""
        if not self.binary:
            return False, "Binary not found"
        commands = [
            [self.binary, "--version"],
            [self.binary, "version"],
            [self.binary, "--help"],
        ]
        test_commands = get_adapter(self.tool).test_commands(binary=self.binary)
        if test_commands:
            commands = list(test_commands) + commands
        last_err = ""
        for cmd in commands:
            try:
                result = subprocess.run(  # nosec B603
                    _subprocess_cmd(cmd),
                    capture_output=True,
                    timeout=8,
                    creationflags=_creation_flags(),
                )
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
            output = _process_output(result)
            if result.returncode == 0:
                return True, (output.splitlines()[0] if output else "OK")
            if output:
                last_err = output.splitlines()[0]
        return False, last_err or "CLI did not respond successfully"

    # ------------------------------------------------------------------ #
    def _system_prompt(self):
        return (
            f"{AGENTICGIS_SYSTEM_PROMPT}\n\n"
            "## CLI proxy rules\n\n"
            "You are running inside a CLI transport, but AgenticGIS executes "
            "all QGIS tools in-process. Do not use this CLI's own filesystem, "
            "shell, browser, plugin, skill, or MCP tools to answer the user's "
            "GIS request. When you need project data, request an AgenticGIS "
            "tool call in the JSON protocol below.\n\n"
            "For final answers, write normal plain text/markdown directly, "
            "with the same rich, useful, detailed answer you would give in "
            "API/custom mode. Do not wrap final answers in JSON. Preserve the "
            "normal AgenticGIS style: concrete findings, useful context, "
            "tables/charts/layers when relevant, and one useful follow-up "
            "sentence after analysis.\n\n"
            "Return JSON ONLY when you need to call one or more AgenticGIS "
            "tools. No markdown fences, no extra text around the JSON.\n"
            "To call one or more AgenticGIS tools:\n"
            '{"type":"tool_calls","calls":[{"name":"list_layers","arguments":{}}]}\n'
            "Use tool_calls when QGIS project data is needed. Use plain final "
            "text for ordinary conversation or once tool results are sufficient."
        )

    def _tool_prompt(self):
        specs = [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            }
            for spec in tools_mod.TOOL_SPECS
        ]
        return json.dumps(specs, default=str)

    def _conversation_prompt(self, messages):
        return (
            f"{self._system_prompt()}\n\n"
            f"Available AgenticGIS tools:\n{self._tool_prompt()}\n\n"
            "Conversation, newest last:\n"
            f"{json.dumps(messages, default=str)}"
        )

    @staticmethod
    def _continuation_prompt(messages):
        if len(messages) == 1:
            message = messages[0]
            if message.get("role") == "user" and isinstance(
                message.get("content"), str
            ):
                return message["content"]
        return (
            "New conversation messages since the previous CLI turn:\n"
            f"{json.dumps(messages, default=str)}"
        )

    def _build_command(self, prompt, _base_url=None):
        return get_adapter(self.tool).build_command(
            binary=self.binary,
            prompt=prompt,
            extra_args=self.extra_args,
            runtime_dir=self._runtime_cwd(),
        )

    def _runtime_env(self):
        env = os.environ.copy()
        _scrub_child_env(env)
        if platform.system() == "Windows":
            extra = list(_windows_package_manager_bin_dirs())
        else:
            extra = list(_unix_package_manager_bin_dirs())
        node_and_wrapper_dirs = _existing_dirs(
            [os.path.dirname(self.binary or ""), *extra, *_RUNTIME_PATH_HINTS]
        )
        _prepend_path(env, node_and_wrapper_dirs)
        env.update(_COMMON_RUNTIME_ENV)
        adapter_env = get_adapter(self.tool).env()
        if adapter_env:
            env.update(adapter_env)
        return env

    def _runtime_cwd(self):
        path = _empty_runtime_dir(f"{self.tool or 'cli'}-workspace")
        # Some CLI agents (e.g. OpenCode) require the working directory to be
        # a git repository. Initialise one lazily so they don't hang on startup.
        git_dir = os.path.join(path, ".git")
        if not os.path.isdir(git_dir):
            try:
                subprocess.run(  # nosec
                    ["git", "init"],
                    cwd=path,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:  # nosec B110
                pass
        return path

    def _collect_process_output(self, cmd, env, cwd, should_stop):
        try:
            with self._lock:
                self._proc = subprocess.Popen(  # nosec B603
                    _subprocess_cmd(cmd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    env=env,
                    cwd=cwd,
                    start_new_session=True,
                    close_fds=True,
                    creationflags=_creation_flags(),
                )
        except Exception as exc:
            return "", f"Failed to launch {self.tool}: {exc}", False

        proc = self._proc
        stdout = proc.stdout
        stderr = proc.stderr
        out_chunks = []
        err_chunks = []
        stopped = False
        try:
            while True:
                if should_stop():
                    stopped = True
                    self._terminate_process_group(proc, kill=True)
                    break
                if proc.poll() is not None:
                    for stream, chunks in ((stdout, out_chunks), (stderr, err_chunks)):
                        if not stream:
                            continue
                        while True:
                            try:
                                rest = os.read(stream.fileno(), 4096)
                            except Exception:
                                rest = b""
                            if not rest:
                                break
                            chunks.append(rest)
                    break
                try:
                    readable, _, _ = select.select([stdout, stderr], [], [], 0.2)
                except (OSError, ValueError):
                    continue
                for stream in readable:
                    try:
                        chunk = os.read(stream.fileno(), 4096)
                    except Exception:
                        chunk = b""
                    if not chunk:
                        continue
                    if stream is stdout:
                        out_chunks.append(chunk)
                    else:
                        err_chunks.append(chunk)
        finally:
            self._finalize_process(proc, stopped=stopped)

        out_text = b"".join(out_chunks).decode("utf-8", "replace")
        err_text = b"".join(err_chunks).decode("utf-8", "replace")
        if stopped:
            return out_text, "Stopped.", False
        return out_text, err_text, proc.returncode == 0

    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        from ..core import tools as tools_mod

        messages = list(history or [])
        user_message = {"role": "user", "content": message}
        messages.append(user_message)
        new_messages = [user_message]
        max_iters = self.config.get("max_iterations")
        adapter = get_adapter(self.tool)

        for _ in agent_iteration_steps(max_iters):
            if should_stop():
                emit(AgentEvent(EventType.THINKING, {"text": "Stopped."}))
                emit(AgentEvent(EventType.DONE))
                return messages

            if self._continuation_session_id(adapter):
                prompt = self._continuation_prompt(new_messages)
            else:
                prompt = self._conversation_prompt(messages)
            stream = self._run_stream(adapter, prompt, emit, should_stop)
            self._remember_session_id(
                adapter, getattr(stream, "session_id", "")
            )

            if stream.pending_tool_call is not None:
                call = stream.pending_tool_call
                name = call.get("name")
                args = call.get("arguments", {}) or {}
                if name in tools_mod.TOOL_BY_NAME:
                    payload, is_error, is_cancelled, _result = _dispatch_one_tool(
                        self.toolkit,
                        self.executor,
                        name,
                        args,
                        emit,
                        should_stop,
                    )
                    if should_stop() or is_cancelled:
                        emit(AgentEvent(EventType.DONE))
                        return messages
                    tool_message = {
                        "role": "tool",
                        "name": name,
                        "content": payload,
                    }
                    messages.append(tool_message)
                    new_messages = [tool_message]
                    continue
                # Non-AgenticGIS tool (e.g. Codex command_execution surfaced
                # for UI display only). End the turn with the assistant's text.
            if stream.final_text is not None:
                messages.append({"role": "assistant", "content": stream.final_text})
            elif not stream.had_error and not stream._text_emitted:
                # No text returned and no tool call — show a visible fallback
                # so the user knows the CLI completed rather than hung.
                fallback = "The CLI agent completed without returning a response."
                emit(AgentEvent(EventType.TEXT, {"text": fallback}))
                messages.append({"role": "assistant", "content": fallback})
            emit(AgentEvent(EventType.DONE))
            return messages

        emit(
            AgentEvent(
                EventType.ERROR,
                {"error": "CLI proxy reached the maximum tool iterations."},
            )
        )
        emit(AgentEvent(EventType.DONE))
        return messages

    # ------------------------------------------------------------------ #
    # Streaming I/O — single-threaded select() loop, robust JSONL parser #
    # ------------------------------------------------------------------ #

    def _collect_into_stream(self, proc, stream, should_stop):
        """Read both pipes via ``select`` and feed lines into ``stream``.

        Replacing the two reader threads eliminates a deadlock class where
        one pipe's kernel buffer (64 kB on macOS) fills before the other
        is drained — the writer blocks, the reader thread blocks on
        ``readline``, and nothing moves. ``select`` notifies us whenever
        *any* pipe is readable.
        """
        stdout = proc.stdout
        stderr = proc.stderr
        out_buf = b""
        err_acc = []
        poller_stopped = False

        while True:
            if should_stop():
                poller_stopped = True
                break
            if proc.poll() is not None:
                if stdout:
                    out_buf = self._read_into_stream(out_buf, stdout, stream)
                if stderr:
                    err_acc.append(self._read_all(stderr, None))
                break
            try:
                rlist, _, _ = select.select([stdout, stderr], [], [], 0.2)
            except (OSError, ValueError):
                break
            for stream_obj in rlist:
                if stream_obj is stdout:
                    out_buf = self._read_into_stream(out_buf, stdout, stream)
                elif stream_obj is stderr:
                    err_acc.append(self._read_all(stderr, None))

        if poller_stopped:
            self._terminate_process_group(proc, kill=True)

        # Persist the session id for the next turn's supported continuation.
        self._remember_session_id(stream.adapter, stream.session_id)

        # If stdout was closed before we drained, pick up any leftover
        # partial line.
        if out_buf.strip():
            stream.feed_line(out_buf)
            self._remember_session_id(stream.adapter, stream.session_id)

        if err_acc and not poller_stopped:
            try:
                rc = proc.returncode
            except Exception:
                rc = None
            if rc not in (0, None):
                joined = "\n".join(s for s in err_acc if s).strip()
                if joined:
                    stream.emit(AgentEvent(EventType.ERROR, {"error": joined[:2000]}))

    def _run_stream(self, adapter, prompt, emit, should_stop):
        """Build the CLI command, spawn the subprocess, run the
        select()-driven reader, and return the populated NormalizingStream."""
        session_id = self._continuation_session_id(adapter)
        command_args = {
            "binary": self.binary,
            "prompt": prompt,
            "extra_args": self.extra_args,
            "runtime_dir": self._runtime_cwd(),
        }
        if session_id:
            cmd = adapter.build_continuation_command(
                session_id=session_id,
                **command_args,
            )
        else:
            cmd = adapter.build_command(**command_args)
        stdin_data = adapter.stdin_prompt(prompt)
        env = self._runtime_env()
        cwd = self._runtime_cwd()
        try:
            with self._lock:
                self._proc = subprocess.Popen(  # nosec B603
                    _subprocess_cmd(cmd),
                    stdin=subprocess.PIPE
                    if stdin_data is not None
                    else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    env=env,
                    cwd=cwd,
                    start_new_session=True,
                    close_fds=True,
                    creationflags=_creation_flags(),
                )
            log_event(
                "cli.process.spawn",
                tool=self.tool,
                pid=getattr(self._proc, "pid", None),
                resumed=bool(session_id),
            )
            if stdin_data is not None:
                self._proc.stdin.write(stdin_data.encode("utf-8"))
                self._proc.stdin.close()
        except Exception as exc:
            emit(
                AgentEvent(
                    EventType.ERROR, {"error": f"Failed to launch {self.tool}: {exc}"}
                )
            )
            stream = NormalizingStream(adapter, emit)
            return stream
        try:
            stream = NormalizingStream(
                adapter,
                emit,
                process_id=getattr(self._proc, "pid", None),
            )
            self._collect_into_stream(self._proc, stream, should_stop)
        finally:
            self._finalize_process(self._proc)
        return stream

    @staticmethod
    def _read_into_stream(buf, stream_obj, normalizer):
        try:
            chunk = stream_obj.read(4096)
        except (OSError, ValueError):
            return buf
        if not chunk:
            return buf
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line:
                normalizer.feed_line(line)
        return buf

    @staticmethod
    def _read_all(stream, _unused):
        try:
            return stream.read().decode("utf-8", "replace")
        except Exception:
            return ""

    @staticmethod
    def _close_process_pipes(proc):
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:  # nosec B110
                pass

    def _finalize_process(self, proc, stopped=False):
        """Reap the child, escalating to SIGKILL if it refuses to die."""
        if stopped:
            self._terminate_process_group(proc, kill=True)
            try:
                proc.wait(timeout=0.05)
            except Exception:  # nosec B110
                pass
            self._close_process_pipes(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            return
        # Bounded shutdown: ask the process group to exit, then kill quickly if needed.
        self._terminate_process_group(proc, kill=False)
        try:
            proc.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            self._terminate_process_group(proc, kill=True)
        try:
            proc.wait(timeout=1.5)
        except Exception:  # nosec B110
            pass
        self._close_process_pipes(proc)
        with self._lock:
            if self._proc is proc:
                self._proc = None

    @staticmethod
    def _terminate_process_group(proc, kill=False):
        # SIGKILL / process groups are POSIX-only; on Windows fall straight
        # through to proc.kill() / proc.terminate().
        if platform.system() != "Windows":
            sig = signal.SIGKILL if kill else signal.SIGTERM
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, sig)
                return
            except Exception:  # nosec B110
                pass
        try:
            if kill:
                proc.kill()
            else:
                proc.terminate()
        except Exception:  # nosec B110
            pass
