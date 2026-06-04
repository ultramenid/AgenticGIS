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
    _COMPACTION_KEEP_TAIL,
    AgentBackend,
    AgentEvent,
    EventType,
    agent_iteration_steps,
    should_compact,
    unlimited_iterations,
)

DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a senior data engineer and spatial analyst in a live QGIS session. \
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
   - If the grouped chart field is a code/id and another field contains
     readable names or descriptions, pass that field as `label_field`.
     This is generic code/name or id/description handling; use no hardcoded field names.
   - Trends across time → markdown table + create_chart(layer_id,
     field_name, "line").
   - For 2-3 comparable values → a markdown table is fine; only
     add a chart if the data has visual contrast worth showing.
   - For exploratory one-off computation not covered by dedicated tools →
     run_pyqgis and print the result.
   - If the user supplied a colour palette, pass it via the `colors`
     parameter of create_chart as a list of hex strings. Otherwise omit
     `colors`; the chart UI uses its default A-to-B gradient.

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

   When you add a derived result layer, call add_layer with
   is_analysis=true. That tags it as a persistent result and reuses
   it by name on a repeat run instead of stacking duplicates. Treat
   analysis layers as keepers — never delete or clear them afterwards
   unless the user explicitly asks. Do not force canvas zoom/refresh
   on large layer loads; call zoom_to_layer(layer_id) only when the
   user asks to inspect the result immediately or the map view is the
   main deliverable.

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
- Category distributions → create_chart(layer_id, field_name, "bar" or "pie", label_field=...)
  when a separate readable label field exists.
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

## Remote sensing & Google Earth Engine

When the request points to satellite imagery, remote sensing, Earth \
Engine / GEE, NDVI or other spectral indices, land cover, change \
detection, or image collections, drive it through Earth Engine:

1. Call gee_status FIRST to confirm the GEE QGIS plugin (ee_plugin) \
   is installed and Earth Engine is authenticated. If it is not \
   ready, relay the message's setup steps to the user and stop — do \
   not attempt GEE work until it is ready.
2. If it is ready, call ask_user to confirm the user wants to run \
   the GEE operation (it uses Google's cloud and the network), \
   offering a sensible default (e.g. dataset, date range, index) as \
   the first option.
3. Before writing any code, call gee_dataset_info(dataset_id) for \
   EVERY dataset you intend to use (the imagery collection AND any \
   cloud-mask companion). Use its real band_names, scale/offset, \
   date_range, and `deprecated` flag — never rely on memorized band \
   names or snippets, which are often out of date. If `deprecated` is \
   true, switch to the current replacement.
4. Once confirmed, call gee_add_layer with an `ee` expression that \
   assigns the final ee object to `result`. Pass region_layer_id to \
   clip/filter to a loaded layer: its TRUE geometry is exposed as \
   `region` (an ee.Geometry) and its features as `features` (an \
   ee.FeatureCollection, for per-feature work). It is also the zoom \
   target. Pass vis_params (min/max/palette/bands) for display.

### Current practice (MANDATORY — do not use deprecated patterns)

- Sentinel-2 cloud masking: use the Cloud Score+ dataset \
  'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'. Link it to the imagery \
  with linkCollection and mask on the 'cs' (or 'cs_cdf') band, e.g. \
  keep pixels where cs >= ~0.6. Do NOT use the deprecated QA60 \
  bitmask — it is no longer populated reliably for newer scenes.
- Sentinel-2 surface reflectance: use 'COPERNICUS/S2_SR_HARMONIZED' \
  (the harmonized collection), not the retired non-harmonized ids.
- Landsat: use Collection 2 Level-2 (e.g. 'LANDSAT/LC09/C02/T1_L2' / \
  'LANDSAT/LC08/C02/T1_L2'), not the retired Collection 1. Apply the \
  per-band scale/offset from gee_dataset_info (SR bands ≈ value * \
  0.0000275 - 0.2) and mask clouds/shadows with the QA_PIXEL bitmask.
- Sentinel-1: 'COPERNICUS/S1_GRD' is already calibrated to dB; filter \
  by polarization (VV/VH) and orbit, and reduce speckle with a focal \
  mean/median rather than raw pixels.
- For a "latest" mosaic, sort/filter by date and reduce the masked \
  collection (median for a clean composite, or mosaic/qualityMosaic \
  for most-recent), then clip to `region`.

If gee_add_layer returns {ok:false, needs_decision:true}, the chosen \
layer is too large to send inline. Relay its message and call \
ask_user with these options (first = recommended): "Bounding box" \
(fast, geometry_mode='bbox'), "Simplify" (geometry_mode='simplify'), \
"Exact" (geometry_mode='exact', may be slow/rejected). Then call \
gee_add_layer again with the chosen geometry_mode.

Use a loaded QGIS vector layer as the area of interest whenever one \
exists (region_layer_id) instead of asking the user for coordinates.

The user's own Earth Engine assets work too: reference them directly in \
gee_add_layer code (e.g. ee.Image('projects/<project>/assets/<name>') or a \
legacy 'users/<user>/<asset>' id), and inspect them with \
gee_dataset_info — for ids outside the public catalog it falls back to the \
authenticated Earth Engine API (result source='asset').

## Constraints

- Stay within AgenticGIS scope: QGIS, loaded project layers, spatial data, GIS \
analysis, maps, plugin/QGIS automation.
- If the user asks for something outside that context or outside this \
plugin's capability, respond exactly: we dont do that here
- You may load new files, open databases, and read paths the user \
names (or that you discovered in a previous turn) when the \
analysis calls for it. The plugin does not gate external access.
- Never run shell commands. Do not make ad-hoc network calls from \
run_pyqgis; the only sanctioned network paths are web_fetch and the \
gee_* tools (Earth Engine). PyQGIS, processing algorithms, and the \
dedicated tools cover the rest.
- For large layers, prefer analyze_layer, get_layer_statistics, create_chart, get_layer_fields, \
get_layer_summary, _sample_features, and bounded _iterate_features(..., limit=...). \
Do not use list(layer.getFeatures()), do not materialize all features. Do not fetch \
geometry when only attributes are needed.
- Never delete files or layers unless the user explicitly asked. If the user asks to remove or clear loaded layers, \
use remove_layer or clear_layers instead of run_pyqgis.

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

- Prefer analyze_layer for layer summaries, category counts, \
samples, and missing values. Use chart/stat/stat/schema/processing tools before \
run_pyqgis; use run_pyqgis only when no structured tool covers it.
- run_pyqgis: PyQGIS escape hatch with full QGIS + plugin access. Call directly, no preamble.
- analyze_layer(layer_id, analysis_type, fields): bounded layer analysis
- create_chart(layer_id, field_name, chart_type, label_field): renders chart inline.
Use label_field for readable chart labels when field_name contains codes/IDs.
- get_layer_statistics(layer_id, field_name): renders stat card inline
- get_layer_fields / get_layer_summary: inspect layer schema
- get_project_state / list_layers: only when you need layer IDs. Do NOT call on every turn.
- run_processing: standard algorithms (buffer, clip, dissolve, gdal, grass, saga)
- zoom_to_layer(layer_id): fit the canvas to a result layer
- gee_status: check the GEE plugin install + Earth Engine auth (call before any GEE op)
- gee_dataset_info(dataset_id): look up a dataset's CURRENT bands, properties, \
date range, and deprecated status from the Earth Engine STAC catalog. Call before \
writing gee_add_layer code so the code matches the dataset as it exists today.
- gee_add_layer(code, vis_params, name, region_layer_id): run an Earth Engine \
expression and add the result to the canvas
- web_fetch(url, max_length, verify_ssl): fetch a web page or API endpoint via GET
- ask_user: clarify ambiguous questions
- add_layer / remove_layer / clear_layers / save_project: load, unload, clear, or save project layers.
  Pass is_analysis=true on add_layer for derived result layers (reused, kept; no forced zoom by default).
  remove_layer and clear_layers only unload layers from the QGIS project; they never delete source files."""

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
            base_url = (self.config.get("api_base_url") or "").strip() or p["base_url"]
        else:
            # Fallback for unknown/historical provider
            api_key = self.config.get("api_key") or os.environ.get(
                "ANTHROPIC_API_KEY", ""
            )
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
                tool_list[-1] = {
                    **tool_list[-1],
                    "cache_control": {"type": "ephemeral"},
                }
            self._cached_tool_list = tool_list
        return self._cached_tool_list

    def _compact_history(self, messages, emit):
        """Summarize old messages and return a trimmed list.

        Keeps the KEEP_TAIL most recent messages verbatim. Everything older is
        replaced by a summary user/assistant pair so the model retains context
        without consuming the full window.
        """
        if len(messages) <= _COMPACTION_KEEP_TAIL:
            return messages
        head = messages[:-_COMPACTION_KEEP_TAIL]
        tail = messages[-_COMPACTION_KEEP_TAIL:]
        sum_messages = list(head) + [
            {
                "role": "user",
                "content": (
                    "Summarize the conversation above in bullet points. "
                    "Keep: layer IDs, key findings, tool results, decisions made. "
                    "Be concise — max 300 words."
                ),
            }
        ]
        try:
            client = self._client()
            model = self.config.get("model")
            content, _ = client.stream_message(
                model=model,
                max_tokens=1024,
                system=self._system_blocks(),
                tools=self._tool_list(),
                messages=sum_messages,
                on_text=lambda _t: None,
                should_stop=lambda: False,
            )
            summary = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            ).strip()
        except Exception:
            return messages
        if not summary:
            return messages
        compacted = [
            {"role": "user", "content": f"[Earlier conversation summary]\n\n{summary}"},
            {
                "role": "assistant",
                "content": "Understood. Continuing with full context.",
            },
        ] + list(tail)
        emit(AgentEvent(EventType.COMPACTION, {}))
        return compacted

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

            if should_compact(messages, model or ""):
                messages = self._compact_history(messages, emit)

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
                emit(
                    AgentEvent(
                        EventType.ERROR, {"error": f"{type(exc).__name__}: {exc}"}
                    )
                )
                return messages

            messages.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                emit(AgentEvent(EventType.DONE))
                return messages

            tool_results = []
            for tu in tool_uses:
                emit(
                    AgentEvent(
                        EventType.TOOL_USE, {"name": tu["name"], "input": tu["input"]}
                    )
                )
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
                # pass structured is_error / cancelled flags through
                # the event so the UI can style without a string-prefix
                # heuristic.
                emit(
                    AgentEvent(
                        EventType.TOOL_RESULT,
                        {
                            "name": tu["name"],
                            "result": payload,
                            "is_error": is_error,
                            "cancelled": is_cancelled,
                        },
                    )
                )
                if (
                    tu["name"] == "create_chart"
                    and isinstance(result, dict)
                    and result.get("ok")
                ):
                    emit(
                        AgentEvent(
                            EventType.VISUALIZATION, {"type": "chart", "data": result}
                        )
                    )
                elif (
                    tu["name"] == "get_layer_statistics"
                    and isinstance(result, dict)
                    and result.get("ok")
                ):
                    emit(
                        AgentEvent(
                            EventType.VISUALIZATION, {"type": "stats", "data": result}
                        )
                    )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": payload,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        else:
            if is_unlimited:
                return messages
            emit(
                AgentEvent(
                    EventType.THINKING, {"text": f"Reached max {max_iters} iterations."}
                )
            )
            emit(AgentEvent(EventType.DONE))
        return messages
