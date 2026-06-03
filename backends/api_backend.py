"""In-process agent loop talking to the Anthropic Messages API over HTTP.

Uses the dependency-free ``AnthropicHttpClient`` (stdlib only), so it runs on a
stock QGIS Python with nothing to install. Used by both API-key and
subscription modes (they differ only in how credentials are supplied). The
model's tool calls map onto ``QgisToolkit`` via ``core.tools.dispatch``; every
QGIS operation is marshaled to the main thread by the executor. The system
prompt and tool list are prompt-cached to keep multi-turn sessions cheap.
"""

import json
import os

from ..core import tools as tools_mod
from .anthropic_http import AnthropicHttpClient, AnthropicHttpError
from .base import (
    AgentBackend,
    AgentEvent,
    EventType,
    agent_iteration_steps,
    unlimited_iterations,
)

DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a senior data engineer and spatial analyst embedded in a live QGIS session. \
You excel at exploratory data analysis, spatial analytics, and visual storytelling with geospatial data.

## What to produce for analysis

When the user asks a question that requires data, produce a clean \
response with up to three elements:

1. **A short summary** (1-3 sentences) — the key analytical
   finding as a concrete claim with a number. This describes the
   *answer to the user's question*, not the layer that was
   produced. Even when you also create a derived layer, the
   summary is the analysis, not the layer description.
   - Good: "60% of forest patches are within 500m of a river."
   - Bad: "Layer 'forest_within_500m' was added to the project
     with 4,521 features." (that's a description of the layer,
     not the analysis).

2. **A table and a chart** — for any analysis with data, produce
   both. The table gives exact values; the chart gives the shape.
   - Numeric summaries (min/mean/max/count) → markdown table +
     get_layer_statistics(layer_id, field_name) for the aggregates
     + create_chart(layer_id, field_name, "bar") for the
     distribution.
   - Category breakdowns → markdown table + create_chart(layer_id,
     field_name, "bar" or "pie").
   - Trends across time → markdown table + create_chart(layer_id,
     field_name, "line").
   - For 2-3 comparable values → a markdown table is fine; only
     add a chart if the data has visual contrast worth showing.
   - For exploratory one-off computation not covered by dedicated tools →
     run_pyqgis and print the result.
   - If the user supplied a colour palette (e.g. brand colours, project
     palette) or you have one in mind that matches the data, pass it
     via the `colors` parameter of create_chart as a list of hex
     strings like `["#5d8aa8", "#c678dd", "#98c379"]`. The list
     cycles if shorter than the number of data points.

3. **A derived layer (when the analysis produces a spatial result)** —
   if the question implies a map output, build a new layer the user
   can drop on the canvas. Examples:
   - Buffer / intersect / spatial join / clip → use run_processing
     with the appropriate native / GDAL / GRASS / SAGA algorithm,
     then add the result to the project with add_layer.
   - Filtered subset (e.g. "show me only protected forests") →
     use run_pyqgis to write a memory layer, then add_layer.
   - Heatmap / kernel density / hexagon aggregation → run_processing
     with qgis:heatmap or native:hexagonalgrid, then add_layer.
   - Centroid or convex-hull summary geometry → run_pyqgis to
     compute, then add_layer.
   Skip this step when the question is purely descriptive ("what is
   the average area?") and the answer is just numbers — no map
   needed.

Prefer the table first, then the chart, then the layer. The table
anchors the numbers, the chart gives the shape, the layer gives the
user something to keep.

After the analysis, end with one sentence suggesting the most \
useful follow-up question. Do not list more than one.

## Style

- Plain language. No filler. No emoji.
- Reference layers by id from get_project_state, not by name.
- If a tool result is empty or an error, say so plainly and suggest why.
- Never invent values. If you do not have the data, say so and ask which layer / field to use.
- For ambiguous requests, call ask_user(question, options) with 2-4 thoughtful choices.
- For conversational questions, answer directly in one short paragraph.
- Act first, explain after. Do not narrate what you are about to do.

## Capabilities

**Data exploration:**
- List layers: get_project_state() (gives layer IDs, CRS, extent)
- Inspect schema: get_layer_fields(layer_id) and get_layer_summary(layer_id)
- Structured analysis: analyze_layer(layer_id, analysis_type, fields) for
  summaries, field stats, category counts, samples, and missing values
- Preview: run_pyqgis with _sample_features(layer, limit=5); never materialize big layers

**Statistical analysis:**
- General analysis → analyze_layer(layer_id, "auto", fields)
- Numeric field stats → get_layer_statistics(layer_id, field_name) for min/mean/max/stdev
- Category distributions → create_chart(layer_id, field_name, "bar" or "pie")
- Time series → create_chart(layer_id, field_name, "line")
- Prefer analyze_layer before arbitrary run_pyqgis for layer analysis.

**Spatial operations:**
- Geometry analysis → run_pyqgis with PyQGIS (buffer, intersection, spatial join)
- Standard algorithms → run_processing(alg_id, params) for native/GDAL/GRASS/SAGA

**QGIS access:**
- Native: all PyQGIS classes via run_pyqgis (QgsVectorLayer, QgsGeometry, etc.)
- GDAL: run_processing('gdal:...') for raster conversion, warping, DEM analysis
- GRASS: run_processing('grass:...') for advanced raster/vector analysis
- SAGA: run_processing('saga:...') for geostatistics, terrain analysis
- Other plugins: full Python access via run_pyqgis to any loaded plugin
- Custom: any algorithm from list_processing_algorithms() can be run

## Constraints

- Stay within AgenticGIS scope: QGIS, loaded project layers, spatial data, GIS \
analysis, maps, plugin/QGIS automation.
- You may load new files, open databases, and read paths the user \
names (or that you discovered in a previous turn) when the \
analysis calls for it. The plugin does not gate external access.
- Never run shell commands or network calls. PyQGIS, processing \
algorithms, and the dedicated tools are all you need.
- For large layers, prefer analyze_layer, get_layer_statistics, create_chart, get_layer_fields, \
get_layer_summary, _sample_features, and bounded _iterate_features(..., limit=...). \
Do not use list(layer.getFeatures()), do not materialize all features. Do not fetch \
geometry when only attributes are needed.
- Never delete files or layers unless the user explicitly asked.

## Performance

run_pyqgis runs on the QGIS main thread and blocks the UI for its entire duration. \
On layers with >10k features a naive loop takes 5–30 seconds. Avoid it for data analysis:

- NEVER use list(layer.getFeatures()) or a bare for-loop over all features for stats.
- NEVER compute count / mean / sum / min / max manually in run_pyqgis — \
  use get_layer_statistics(layer_id, field_name) instead (runs in background, cached).
- NEVER use run_pyqgis to summarise a field — use analyze_layer instead.
- Use layer.featureCount() for a count; it is instant.
- Use run_pyqgis only for spatial operations (geometry, buffer, join, layer creation) \
  or QGIS automation that no structured tool provides.

If a run_pyqgis result includes a "slow_ms" key, the last call was slow. \
Switch to a structured tool for the same operation on the next step.

## Available tools

- Prefer analyze_layer for layer summaries, field stats, category counts, \
samples, and missing values. Use chart/stat/stat/schema/processing tools before \
run_pyqgis; use run_pyqgis only when no structured tool covers it.
- run_pyqgis: PyQGIS escape hatch with full QGIS + plugin access. Call directly, no preamble.
- analyze_layer(layer_id, analysis_type, fields): bounded layer analysis
- create_chart(layer_id, field_name, chart_type): renders chart inline
- get_layer_statistics(layer_id, field_name): renders stat card inline
- get_layer_fields / get_layer_summary: inspect layer schema
- get_project_state / list_layers: only when you need layer IDs. Do NOT call on every turn.
- run_processing: standard algorithms (buffer, clip, dissolve, gdal, grass, saga)
- ask_user: clarify ambiguous questions
- add_layer / save_project: load/save data operations"""

MAX_TOKENS = 4096


class ApiBackend(AgentBackend):
    label = "API (Anthropic)"

    def __init__(self, config, toolkit, executor):
        self.config = config
        self.toolkit = toolkit
        self.executor = executor
        # Cache static tool specs and system prompt
        self._cached_system_key = None
        self._cached_system_blocks = None
        self._cached_tool_list = None

    # ------------------------------------------------------------------ #
    def _provider(self):
        from . import providers
        pid = self.config.get("provider")
        if pid == "custom":
            return None
        return providers.get_provider(pid)

    def _client(self):
        p = self._provider()
        if p:
            api_key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            base_url = p["base_url"]
        else:
            # Fallback for unknown/historical provider
            api_key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        return AnthropicHttpClient(
            api_key=api_key or None, auth_token=None, base_url=base_url
        )

    def validate(self):
        p = self._provider()
        if p:
            key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            label = p["label"]
        else:
            key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            label = "Anthropic"
        if not key:
            return (
                f"No API key set for {label}. "
                f"Add one in Settings (or set {p['key_env'] if p else 'ANTHROPIC_API_KEY'})."
            )
        return None

    def _system_blocks(self):
        text = self.config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        if text != self._cached_system_key:
            self._cached_system_key = text
            self._cached_system_blocks = [
                {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
            ]
        return self._cached_system_blocks

    def _tool_list(self):
        if self._cached_tool_list is None:
            tool_list = tools_mod.anthropic_tool_list()
            if tool_list:
                tool_list[-1] = {**tool_list[-1], "cache_control": {"type": "ephemeral"}}
            self._cached_tool_list = tool_list
        return self._cached_tool_list

    # ------------------------------------------------------------------ #
    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        client = self._client()
        model = self.config.get("model")
        max_iters = self.config.get("max_iterations")

        messages = list(history)
        messages.append({"role": "user", "content": message})

        is_unlimited = unlimited_iterations(max_iters)
        for _ in agent_iteration_steps(max_iters):
            if should_stop():
                emit(AgentEvent(EventType.THINKING, {"text": "Stopped."}))
                break

            try:
                content, stop_reason = client.stream_message(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=self._system_blocks(),
                    tools=self._tool_list(),
                    messages=messages,
                    on_text=lambda t: emit(AgentEvent(EventType.TEXT, {"text": t})),
                    should_stop=should_stop,
                )
            except AnthropicHttpError as exc:
                emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
                return messages
            except Exception as exc:  # noqa: BLE001
                emit(AgentEvent(EventType.ERROR,
                                {"error": f"{type(exc).__name__}: {exc}"}))
                return messages

            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                emit(AgentEvent(EventType.DONE))
                return messages

            tool_results = []
            for tu in tool_uses:
                emit(AgentEvent(EventType.TOOL_USE,
                                {"name": tu["name"], "input": tu["input"]}))
                result = None
                is_error = False
                is_cancelled = False
                try:
                    result = tools_mod.dispatch(
                        self.toolkit, self.executor, tu["name"], tu["input"]
                    )
                    if isinstance(result, dict):
                        is_error = result.get("ok") is False
                        is_cancelled = bool(result.get("cancelled"))
                    else:
                        # Non-dict result is a contract violation; treat as
                        # an error so the model and UI see the failure
                        # rather than silently accepting a string.
                        is_error = True
                        is_cancelled = False
                    payload = json.dumps(result, default=str)
                    if len(payload) > 200_000:
                        payload = payload[:200_000] + "\n... [output truncated]"
                except Exception as exc:  # noqa: BLE001
                    payload = f"Tool error: {type(exc).__name__}: {exc}"
                    is_error = True
                # F11: pass structured is_error / cancelled flags through
                # the event so the UI can style without a string-prefix
                # heuristic.
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": tu["name"],
                    "result": payload,
                    "is_error": is_error,
                    "cancelled": is_cancelled,
                }))
                if tu["name"] == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif tu["name"] == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": payload,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        else:
            if is_unlimited:
                return messages
            emit(AgentEvent(EventType.THINKING,
                            {"text": f"Reached max {max_iters} iterations."}))
            emit(AgentEvent(EventType.DONE))
        return messages
