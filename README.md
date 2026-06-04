# AgenticGIS

An in-QGIS agentic chat assistant. Type a request in plain language and an LLM
agent carries it out inside the running QGIS session — driving **PyQGIS**,
**Processing** (GDAL / GRASS / SAGA / native), **Google Earth Engine**, and
**every installed plugin**. If QGIS can do it, the agent can do it: anything you
could script in the Python console or run from a toolbox, it reaches the same
way.

**Zero dependencies.** The plugin runs entirely on QGIS's bundled Python
standard library — no `pip`, no `conda`, no Python upgrade. Drop it in, enable
it, and connect.

## What it can do

- **Analyse** — field statistics, category breakdowns, missing-value scans, and
  bounded layer summaries that stay fast on large datasets.
- **Visualise** — inline tables, charts (bar / pie / line), and stat cards
  rendered straight in the chat dock.
- **Process** — run native / GDAL / GRASS / SAGA algorithms (buffer, clip,
  dissolve, heatmap, …) and add the derived layer to the project.
- **Remote sensing** — drive Google Earth Engine for satellite imagery, spectral
  indices, cloud-masked mosaics, and land-cover work. It looks up each dataset's
  *current* bands and best practice (e.g. Sentinel-2 Cloud Score+, not the
  deprecated QA60) before writing code, and can also read your own EE assets.
- **Fetch** — pull a public URL or API endpoint with `web_fetch` when a task
  needs outside reference data.

## How it works

```
QGIS (main thread)
 ├─ Chat dock (you type here)
 ├─ QgisToolkit ............ run_pyqgis / project state / processing / layers
 ├─ MainThreadExecutor ..... marshals every QGIS op onto the main thread
 ├─ Backend (pluggable):
 │    • CLI tool  → spawns Claude Code / OpenCode → talks to the MCP bridge
 │    • API key   → in-process Anthropic tool-use loop (stdlib urllib)
 │    • Subscription → same loop, OAuth/bearer via env
 └─ MCP bridge (background thread) ... stdlib http.server exposing the toolkit
```

The catch-all tool is `run_pyqgis`, which executes arbitrary PyQGIS — that is
what makes "all features + all plugins" possible. Convenience tools
(`get_project_state`, `run_processing`, `list_plugins`, …) keep common requests
cheap and reliable. The same tool set is exposed both in-process (API mode) and
over MCP (CLI mode) from one definition in `core/tools.py`.

Both transports are hand-rolled on the standard library:
`backends/anthropic_http.py` (Messages API over `urllib`, SSE streaming) and
`server/mcp_server.py` (MCP = JSON-RPC 2.0 over `http.server`). This is why
there is nothing to install and it works on any QGIS, including the Python 3.9
that the official macOS `.dmg` ships
([qgis/QGIS#54491](https://github.com/qgis/QGIS/issues/54491)).

## Connection modes (Settings → Connect via)

1. **CLI tool** — use an installed, already-logged-in agent (Claude Code /
   OpenCode). No API key needed; the plugin auto-starts the local MCP bridge.
2. **API key** — a built-in provider (Anthropic, OpenAI, Groq, OpenRouter,
   Google Gemini) using its key or the matching env var (`ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `GROQ_API_KEY`, …).
3. **Custom endpoint** — any OpenAI-compatible or Anthropic-compatible base URL
   (self-hosted, proxy, or another provider).
4. **Subscription / OAuth** — rides an existing login via `ANTHROPIC_AUTH_TOKEN`
   (+ optional `ANTHROPIC_BASE_URL`). For a key-less subscription, CLI-tool mode
   is usually simpler.

## Requirements

- **QGIS 3.22+** — that's the whole hard requirement. The plugin itself needs
  no Python packages (stdlib only).
- **An LLM connection** — one of the connection modes above (a logged-in CLI
  agent, or an API key / endpoint).
- **Remote sensing (optional)** — to use the Google Earth Engine features you
  must have the **Google Earth Engine** QGIS plugin (`ee_plugin`) installed
  **and already authenticated**:
  1. Install *Google Earth Engine* from **Plugins → Manage and Install
     Plugins**.
  2. Authenticate once in the QGIS **Python Console**:
     ```python
     import ee
     ee.Authenticate()
     ee.Initialize(project="YOUR_CLOUD_PROJECT")
     ```
  AgenticGIS calls `gee_status` before any Earth Engine operation and will
  **not** run GEE work until the plugin reports installed + authenticated —
  it relays the setup steps instead. Asset lookups for your own private assets
  also require this authenticated session.

## Install

1. Copy/symlink this folder into the QGIS plugins directory as `AgenticGIS`:
   `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
2. Enable **AgenticGIS** in Plugins → Manage and Install Plugins.
3. Click the toolbar icon to open the chat dock, then **⚙ Settings** to pick a
   connection mode.

That's it — no dependency step.

## Safety

Generated PyQGIS **auto-runs** (no per-step confirmation), scoped to the
current QGIS project/layers. Avoid pointing it at irreplaceable data without a
backup. Two guardrails apply:

- **External access** (loading files/URLs, `web_fetch`, Earth Engine, databases)
  is gated behind a one-time permission popup; you can allow it once or remember
  the choice.
- Destructive built-ins in `run_pyqgis` (e.g. `os.system`, `shutil.rmtree`) can
  be blocked via the *confirm dangerous calls* setting.

Layer-removal tools only unload layers from the project — they never delete
source files.
