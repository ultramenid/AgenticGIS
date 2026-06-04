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

Your message enters a **think → call tool → observe** loop. The LLM picks a
tool, it runs inside QGIS, the result feeds back — repeating until the task is
done. One message can chain many tools without further prompting.

```
QGIS session
 ├─ Chat dock ................. you type here; results stream back
 ├─ Backend (pluggable)
 │    • API key    → Anthropic / OpenAI / Groq / Gemini / DeepSeek / Ollama / …
 │    • Custom URL → any OpenAI- or Anthropic-compatible endpoint
 │    • CLI Agent  → installed local agent CLIs such as Codex, Gemini, OpenCode
 └─ Tools (every call runs on the QGIS main thread)
      run_pyqgis         arbitrary PyQGIS — layers, canvas, plugins, console
      run_processing     GDAL / GRASS / SAGA / native algorithms
      gee_*              Google Earth Engine imagery & indices
      get_project_state  layer list, CRS, extent, field schemas
      web_fetch          pull a public URL or API response
```

`run_pyqgis` is the catch-all — it executes arbitrary Python inside the live
QGIS session, giving the agent access to everything QGIS and every installed
plugin can do. Both transports are built on the Python standard library, so
there is nothing to install and it works on any QGIS Python.

## Connection modes (Settings → Connect via)

1. **API key** — a built-in provider (Anthropic, OpenAI, Groq, OpenRouter,
   Google Gemini, DeepSeek, Mistral, xAI, Ollama) using its key or the matching
   env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, …).
2. **Custom endpoint** — any OpenAI-compatible or Anthropic-compatible base URL
   (self-hosted, proxy, or another provider).
3. **CLI Agent** — delegate to an installed local agent CLI. AgenticGIS scans
   for supported CLIs, lets you test the selected agent, and never reads or
   copies CLI-owned OAuth tokens.

### CLI Agent guide

CLI Agent mode is for users who already run a local agent CLI and want
AgenticGIS to delegate QGIS work to that tool. The CLI keeps ownership of its
own login, provider config, limits, and credentials.

1. Install and log in to the CLI outside QGIS.
2. In AgenticGIS, open **Settings → CLI Agent**.
3. Click **Scan** or **Rescan** to detect installed CLIs.
4. Select an agent and click **Test binary** to confirm the command runs.
5. Click **Check auth** if that CLI exposes a safe status command. Some CLIs
   do not; in that case verify login directly in the CLI.
6. Click **Use**, then **Save**.

Supported CLI catalog:

`Claude Code`, `Codex CLI`, `Cursor Agent`, `Gemini CLI`, `GitHub Copilot CLI`,
`OpenCode`, `Qwen Code`, `Grok`, `Hermes`, `Kimi CLI`, `Devin for Terminal`,
`DeepSeek TUI`, `Pi`, `Mistral Vibe CLI`, `Kiro CLI`, `Kilo`, `Qoder CLI`.

Notes:

- AgenticGIS does not read CLI credential files or copy OAuth tokens.
- CLI scanning is manual so Settings opens quickly.
- Detected command paths are shown in the UI; if a command is a symlink, the
  resolved binary path is shown too.
- If auto-detection misses your CLI, use **Browse** to select the command
  manually.
- For Claude Code, auth checks use `claude auth status`; AgenticGIS does not
  run the interactive `claude status` command.

## Requirements

- **QGIS 3.22+** — that's the whole hard requirement. The plugin itself needs
  no Python packages (stdlib only).
- **An LLM connection** — one of the connection modes above (API key, custom
  endpoint, or local CLI agent).
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

The QGIS Plugins menu handles both install paths.

### Option A — from a downloaded zip (recommended)

1. Go to the [**Releases** page](https://github.com/ultramenid/AgenticGIS/releases)
   and download the latest `AgenticGIS-<version>.zip`.
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Browse to the downloaded zip, click **Install Plugin**.
4. Enable **AgenticGIS** in the same dialog if it isn't already ticked.
5. Click the new toolbar icon to open the chat dock, then **⚙ Settings** to
   pick a connection mode and enter your API key.

### Option B — manual folder install

1. Locate your QGIS profile's `python/plugins` folder:
   - **macOS:** `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
2. Copy or clone this repository there as `AgenticGIS` (the folder must be
   named exactly `AgenticGIS`).
3. Restart QGIS, then enable **AgenticGIS** in **Plugins → Manage and Install
   Plugins**.
4. Click the toolbar icon, then **⚙ Settings** to configure.

That's it — no dependency step.

## Best use case

AgenticGIS works best as a **prepare → analyse → iterate** loop. Each phase
builds on the last, and the agent keeps full context of what it already loaded
and found — so each follow-up is a one-liner, not a re-explanation.

### 1. Prepare your workspace

Load everything the analysis will touch before asking questions. The agent
reads whatever is already in the project; it does not guess at file paths.

- Load your vector and raster layers (`add_layer`, drag-and-drop, or the QGIS
  browser — all work).
- If you need satellite imagery, load your area-of-interest layer so the agent
  can use it as the region boundary for Earth Engine queries.
- For large or multi-source projects, a brief "*what layers do I have?*" prompt
  lets the agent map out the project before diving in.

> **Tip:** name your layers meaningfully before starting. The agent references
> them by id internally, but uses the name when explaining results to you.

### 2. Ask your analysis question

State the question as you would to a colleague — the agent picks the right
tools, writes the code, runs it, and returns a result. You do not need to know
which QGIS function or algorithm to use.

Productive question patterns:

| What you want | Example prompt |
|---|---|
| Field summary | *"What is the distribution of land-cover classes in the forest layer?"* |
| Spatial operation | *"Buffer the river layer by 500 m and clip it to the study area."* |
| Cross-layer analysis | *"How many buildings fall within the flood-risk zone?"* |
| Remote sensing index | *"Show me an NDVI cloud-masked mosaic for this area for the last dry season."* |
| Trend over time | *"Plot the monthly average NDVI for the watershed from 2020 to 2024."* |
| Data quality | *"Are there any null values or geometry errors in the parcels layer?"* |

The agent produces a **summary finding → table → chart → derived layer** in
one turn. Derived layers are tagged as analysis results and reused by name on
repeat runs instead of stacking duplicates.

### 3. Iterate — refine, drill down, extend

Once you have a result, keep going in the same session. The agent remembers
what it loaded and found.

- *"Filter that to only patches larger than 10 ha."*
- *"Break the previous chart down by ownership category instead."*
- *"Now do the same analysis but for the northern district only."*
- *"Export the result layer"*

Each message refines or extends the prior result without re-loading context.
For long multi-step workflows, the conversation history is automatically
compacted when it grows large, preserving layer IDs, key findings, and
decisions so the agent stays coherent across dozens of turns.

### example

```
1.  Load: admin boundaries, land-cover raster, river network, DEM
2.  "Summarise land-cover distribution by district"
    → agent returns table + bar chart + district-summary layer
3.  "Which districts have more than 30 % forest cover?"
    → filtered layer added; findings stated as a one-sentence claim
4.  "For those districts, buffer rivers by 200 m and compute what
     percentage of forest falls within the buffer"
    → processing chain runs; result layer + percentage table
5.  "Show me a cloud-free Sentinel-2 NDVI composite for those districts
     from the last six months"
    → agent calls gee_status → confirms GEE ready → fetches live STAC
      metadata → writes cloud-masked mosaic code → adds EE layer
6.  "Compare NDVI values inside vs outside the river buffer"
    → zonal statistics → inline stat cards for both zones
```

Steps 2–6 are each a single sentence. The agent handles the tool chain,
algorithm selection, and parameter wiring — you steer the analysis.

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
