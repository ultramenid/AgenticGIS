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
  bounded layer summaries that stay fast on large datasets. Raster band
  statistics (min/max/mean/std/sum/range) via `analyze_raster`.
- **Visualise** — inline tables, charts (bar / pie / line), and stat cards
  rendered straight in the chat dock. Style layers by attribute with
  `set_layer_style` (categorized, graduated, rule-based, single-symbol, heatmap).
- **Process** — run native / GDAL / GRASS / SAGA algorithms (buffer, clip,
  dissolve, heatmap, …) and add the derived layer to the project. Discover
  algorithm parameters with `get_algorithm_parameters` before calling
  `run_processing` — no more guessing parameter names.
- **Select** — `select_by_attribute`, `select_by_expression`, `select_within`,
  `invert_selection`, and `clear_selection` for full feature-selection workflows.
- **Reproject & CRS** — `reproject_layer` and `set_project_crs` without writing
  PyQGIS.
- **Edit fields** — `field_calculator` adds calculated fields from QGIS
  expressions (permanent or virtual).
- **Layouts & export** — `create_layout` builds a print layout with map items;
  `export_layout` exports to PDF or PNG.
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
 └─ Tools (heavy work runs on a worker thread; project/canvas/UI calls stay on the main thread)
      run_pyqgis              arbitrary PyQGIS — layers, canvas, plugins, console
      run_processing          GDAL / GRASS / SAGA / native algorithms
      gee_*                   Google Earth Engine imagery & indices
      get_project_state       layer list, CRS, extent, field schemas
      set_layer_style         categorized / graduated / rule-based / heatmap symbology
      select_*                select by attribute, expression, within; invert; clear
      reproject_layer         reproject to a target CRS
      set_project_crs         set the project CRS
      analyze_raster          raster band statistics
      get_algorithm_parameters  discover processing algorithm parameter schemas
      field_calculator        add calculated fields from QGIS expressions
      create_layout           print layout with map items
      export_layout          export layout to PDF / PNG
      web_fetch               pull a public URL or API response
      ask_user                ask the user a clarifying question
```

`run_pyqgis` is the catch-all — it executes arbitrary Python inside the live
QGIS session, giving the agent access to everything QGIS and every installed
plugin can do. Long-running code (downloads, file conversions, Processing over
files, heavy compute) runs on a background worker thread so QGIS stays
responsive; code that touches the project, map canvas, or UI runs on the main
thread. Both transports are built on the Python standard library, so there is
nothing to install and it works on any QGIS Python.

## Connection modes (Settings → Connect via)

1. **API key** — a built-in provider (Anthropic, OpenAI, Groq, OpenRouter,
   Google Gemini, DeepSeek, Mistral, xAI, Ollama) using its key or the matching
   env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, …).
2. **Custom endpoint** — any OpenAI-compatible or Anthropic-compatible base URL
   (self-hosted, proxy, or another provider).
3. **CLI Agent** — use an installed local agent CLI as the model connection.
   AgenticGIS scans for supported CLIs, lets you test the selected agent, runs
   QGIS tools itself, and never reads or copies CLI-owned OAuth tokens.

### CLI Agent guide

CLI Agent mode is for users who already run a local agent CLI and want
AgenticGIS to use that CLI as the LLM connection. The CLI keeps ownership of
its own login, provider config, limits, and credentials; AgenticGIS keeps QGIS
tool execution inside the plugin process.

1. Install and log in to the CLI outside QGIS.
2. In AgenticGIS, open **Settings → CLI Agent**.
3. Click **Scan** or **Rescan** to detect installed CLIs.
4. Select an agent and click **Test binary** to confirm the command runs.
5. Click **Check auth** if that CLI exposes a safe status command. Some CLIs
   do not; in that case verify login directly in the CLI.
6. Click **Use**, then **Save**.

Supported CLI catalog:

`Claude Code`, `Codex CLI`, `Cursor Agent`, `Gemini CLI`, `Antigravity CLI`,
`GitHub Copilot CLI`, `OpenCode`, `Qwen Code`, `Grok`, `Hermes`, `Kimi CLI`,
`Devin for Terminal`, `DeepSeek TUI`, `Pi`, `Mistral Vibe CLI`, `Kiro CLI`,
`Kilo`, `Qoder CLI`.

Notes:

- AgenticGIS does not read CLI credential files or copy OAuth tokens.
- CLI scanning is manual so Settings opens quickly.
- Detected command paths are shown in the UI; if a command is a symlink, the
  resolved binary path is shown too.
- If auto-detection misses your CLI, use **Browse** to select the command
  manually.
- For Claude Code, auth checks use `claude auth status`; AgenticGIS does not
  run the interactive `claude status` command.
- **Native tool access** — Claude Code, Codex CLI, and OpenCode get the
  AgenticGIS tools registered as native MCP tools (via the local bridge,
  on while "Expose QGIS tools to external agent CLIs" is enabled), so the
  CLI agent calls `list_layers` / `run_pyqgis` / `run_processing` directly.
  Other CLIs use the JSON tool-call protocol embedded in the prompt.

## External agent access (MCP)

The QGIS tools don't only serve the in-panel agent: while the plugin is
loaded, they are also exposed as a **local MCP server**, so any MCP-capable
agent CLI — Claude Code, Codex CLI, OpenCode, Cursor Agent, Gemini CLI, … —
can drive the live QGIS session from outside: list layers, run Processing
algorithms, execute PyQGIS, style, export — everything the panel's agent can
do, against the same running project.

- The bridge starts with the plugin (disable: **Settings → External agent
  CLIs**) and listens on `127.0.0.1:7317` (override with the `mcp_port`
  setting; `0` = random free port).
- Every running instance publishes its URL to `~/.agenticgis/mcp.json`, so
  multiple QGIS profiles / QGIS versions can coexist and clients find a
  live one automatically.
- Most CLIs only speak the *stdio* MCP transport, so the plugin ships a
  tiny zero-dependency proxy that bridges stdio ↔ streamable HTTP. It runs
  on any `python3` (≥ 3.9) — nothing to install.

One-time setup per CLI — replace `<plugins>` with your profile's
`python/plugins` path (see [Install](#option-b--manual-folder-install)):

```bash
# Claude Code — tools appear as mcp__agenticgis__*
claude mcp add agenticgis -s user -- python3 "<plugins>/AgenticGIS/server/mcp_stdio.py"

# Codex CLI — ~/.codex/config.toml
[mcp_servers.agenticgis]
command = "python3"
args = ["<plugins>/AgenticGIS/server/mcp_stdio.py"]

# OpenCode — opencode.json
{
  "mcp": {
    "agenticgis": {
      "type": "local",
      "command": ["python3", "<plugins>/AgenticGIS/server/mcp_stdio.py"]
    }
  }
}
```

Any client that speaks streamable HTTP natively can skip the proxy and use
`http://127.0.0.1:7317/mcp` directly.

**Notes**

- Localhost only, no authentication: while the toggle is on, any local
  process can drive the QGIS tools. Turn it off to close the door.
- QGIS must be running with the plugin enabled — the proxy exits with a
  clear error when no live bridge answers.
- Tool calls run to completion before the result streams back — same
  execution model as CLI Agent mode.

## Requirements

- **QGIS 3.22+** (including QGIS 4 / Qt6) — that's the whole hard requirement.
  The plugin itself needs no Python packages (stdlib only). All QGIS enums use
  the scoped `Qgis.*` form with `getattr` fallbacks, so it works on both Qt5
  (QGIS 3) and Qt6 (QGIS 4) builds.
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
| Styling | *"Style the population layer with a graduated green-to-red ramp by density."* |
| Selection | *"Select all parcels larger than 1 ha."* |
| Raster analysis | *"What are the band statistics of this DEM?"* |
| Layout & export | *"Create a map layout with these three layers and export it to PDF."* |
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
5.  "Style the buffer layer with a graduated blue ramp by forest percentage"
    → set_layer_style applies a QgsGraduatedSymbolRenderer
6.  "Show me a cloud-free Sentinel-2 NDVI composite for those districts
     from the last six months"
    → agent calls gee_status → confirms GEE ready → fetches live STAC
      metadata → writes cloud-masked mosaic code → adds EE layer
7.  "Compare NDVI values inside vs outside the river buffer"
    → zonal statistics → inline stat cards for both zones
8.  "Create a map layout with the NDVI layer and the buffer, export to PDF"
    → create_layout + export_layout → PDF saved
```

Steps 2–6 are each a single sentence. The agent handles the tool chain,
algorithm selection, and parameter wiring — you steer the analysis.

Generated PyQGIS **auto-runs** (no per-step confirmation), scoped to the
current QGIS project/layers. Avoid pointing it at irreplaceable data without a
backup. Several guardrails apply:

- **Iteration cap** — the agent loop is capped at 25 tool-use iterations by
  default (configurable via `max_iterations` in Settings). The agent can no
  longer run forever consuming tokens if it gets stuck in a loop.
- **Dangerous call guard** — destructive built-ins in `run_pyqgis`
  (e.g. `os.system`, `shutil.rmtree`, `subprocess`) are blocked by default.
  Disable via the *confirm dangerous calls* setting if you need them.
- **External access** (loading files/URLs, `web_fetch`, Earth Engine, databases)
  is gated behind a one-time permission popup; you can allow it once or remember
  the choice. Agent-authored code can no longer self-grant this permission.
- **Project save guard** — `save_project` requires `confirm=true` to prevent
  accidentally overwriting the project file. Use `save_project_as` to save to a
  new path.
- **Project snapshots** — a temp project snapshot is taken before destructive
  `run_pyqgis` runs for recovery.

Layer-removal tools only unload layers from the project — they never delete
source files.



## Limitations — Google Earth Engine

AgenticGIS drives Earth Engine through the **ee_plugin** (a separate QGIS
plugin), not the native `earthengine` Python API. That keeps the install
dependency-free, but it inherits ee_plugin's behaviour and adds real
constraints you should understand before working with satellite imagery:

- **GEE layers are remote WMS tiles**, not local rasters — every pan/zoom is a
  network round-trip (see below).
- **`vis_params scale` is silently ignored** — resolution must be controlled in
  the Earth Engine expression itself.
- **GeoTIFF downloads are size-capped** at Earth Engine's 50 MB synchronous
  limit, so large/high-resolution areas may need a coarser scale or fall back to
  tiles.
- **Authentication is entirely ee_plugin's job** — AgenticGIS never logs you in.

AgenticGIS calls `gee_status` before any Earth Engine operation and refuses to
run GEE work until ee_plugin reports installed **and** authenticated, returning
actionable guidance instead of crashing.

### Tile performance (WMS, not local)

ee_plugin renders every GEE layer as **on-demand WMS tiles** — each zoom or pan
triggers fresh tile requests to Google's servers. There is no local pyramid of
precomputed overviews, so every navigation incurs a network round-trip.

### `vis_params scale` is silently ignored

Setting `scale` in `vis_params` (e.g. `{'min':0, 'max':1, 'scale':10}`) has
**zero effect** on tile resolution. The ee_plugin calls
`image.visualize(**vis_params)` then `getMapId({"image": image})` — the scale
parameter is dropped before it reaches the tile server.

To control resolution, use **`clipToBoundsAndScale(geometry=region, scale=N)`**
in the Earth Engine expression itself. This resamples the composite before the
tile server sees it.

### GeoTIFF export for fast zoom

For interactive exploration, AgenticGIS can download a GEE result as a **local
GeoTIFF** (``export_format='geotiff'`` — default). This loads as a native QGIS
raster layer with pyramid overviews, giving instant zoom/pan.

**Limitations of the GeoTIFF download:**

| Constraint | Detail |
|---|---|
| **Request size** | Earth Engine's synchronous download API rejects requests over **50 MB**. The tool auto-retries with 2× the requested scale (lower resolution = less data), up to 3 attempts. |
| **Large areas** | Province-scale regions may still fail the download even at reduced resolution (e.g. 400 m). In that case the agent falls back to ``export_format='map'`` (WMS tiles). |
| **No async export** | Only the synchronous `getDownloadId()` / `urlretrieve` path is implemented. `Export.image.toDrive()` and `Export.image.toCloudStorage()` are not supported. |
| **Temp file cleanup** | Downloaded GeoTIFFs are tracked via a persistent manifest. Temp files are deleted when the layer is removed from the project, on plugin unload, and on the next startup after a crash. |
| **Single-threaded** | Downloads block the agent loop until complete. Large downloads may take tens of seconds. |

### Authentication

AgenticGIS does not authenticate Earth Engine itself. You must install and
authenticate the **Google Earth Engine** QGIS plugin separately (see
[Requirements](#requirements) above).


## Limitations — General

- **CLI Agent mode** cannot stream partial results — the agent waits for the CLI
  to produce its full response before processing tool results. CLI-mode sessions
  do not support history compaction.
- **Reusing layers** depends on the LLM recognising that existing layers contain
  the data you need. The system prompt instructs it to prefer local clip/extract
  over re-running GEE, but this is a model-level behaviour, not guaranteed.
- **Rate limits** — both OpenAI and Anthropic backends retry transient HTTP
  errors (429, 500, 502, 503, 504) with exponential backoff and `Retry-After`
  support (up to 3 attempts). Permanent errors (400, 401, 403, 404) fail
  immediately with a clear message.
- **Reasoning models** — OpenAI o1/o3/o4 reasoning models are supported and use
  `max_completion_tokens` instead of `max_tokens` (which they reject).

## Roadmap

- **Custom skills** — user-defined tool bundles that extend the agent's
  capabilities beyond the built-in set.
- **MCP client** — connect the in-panel agent to external MCP servers for
  additional tools and data sources. (The server side — exposing QGIS tools
  to outside agent CLIs — is done; see
  [External agent access (MCP)](#external-agent-access-mcp).)
- **And more** — ongoing improvements to performance, stability, and
  you decide.
