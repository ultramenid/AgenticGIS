"""In-process agent loop talking to any OpenAI-compatible Chat Completions endpoint.

Uses the stdlib ``OpenAIHttpClient`` (backends/openai_http.py) so the plugin
runs on a stock QGIS with no packages. The model's tool_calls map onto
``QgisToolkit`` via ``core.tools.dispatch``.
"""

import json
import os

from ..core import tools as tools_mod
from .base import (
    AgentBackend,
    AgentEvent,
    EventType,
    _COMPACTION_KEEP_TAIL,
    agent_iteration_steps,
    should_compact,
    unlimited_iterations,
)
from .openai_http import OpenAIHttpClient, OpenAIHttpError

DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a spatial data analyst embedded in a live QGIS session. \
Analyse, compute, interpret, and explain — not just execute.

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

## Output style

- Plain language. No filler. No emoji.
- Be direct. The user is a GIS analyst.
- Reference layers by id (from get_project_state), not by name.
- If a tool result is empty or an error, say so plainly and suggest why.
- Never invent values. If you do not have the data, say so and ask \
  which layer / field to use.
- For ambiguous requests, call ask_user(question, options) with 2-4 \
  thoughtful options.
- For conversational questions, answer directly in one short paragraph.
- Act first, explain after. Do not narrate what you are about to do.

## Tools

- Prefer analyze_layer for layer summaries, field stats, category counts,
  samples, and missing values. Use chart/stat/stat/schema/processing tools
  before run_pyqgis; use run_pyqgis only when no structured tool covers it.
- run_pyqgis: PyQGIS escape hatch with full QGIS + plugin access. Call directly, no preamble.
- analyze_layer(layer_id, analysis_type, fields): bounded layer analysis
- create_chart(layer_id, field_name, chart_type): renders chart inline
- get_layer_statistics(layer_id, field_name): renders stat card inline
- get_layer_fields / get_layer_summary: inspect layer schema
- get_project_state / list_layers: only when you need layer IDs
- run_processing: standard algorithms (buffer, clip, dissolve, etc.)
- add_layer / save_project: when asked to load or save

## Constraints

- Stay within AgenticGIS scope: QGIS, loaded project layers, spatial
  data, GIS analysis, maps, plugin/QGIS automation.
- You may load new files, open databases, and read paths the user
  names (or that you discovered in a previous turn) when the
  analysis calls for it. The plugin does not gate external access.
- Never run shell commands or network calls. PyQGIS, processing
  algorithms, and the dedicated tools are all you need.
- For large layers, prefer analyze_layer, get_layer_statistics,
  create_chart, get_layer_fields, get_layer_summary, _sample_features,
  and bounded _iterate_features(..., limit=...). Do not use
  list(layer.getFeatures()), do not materialize all features. Do not
  fetch geometry when only attributes are needed.
- Never delete files or layers unless the user explicitly asked.

## Performance

run_pyqgis runs on the QGIS main thread and blocks the UI for its entire
duration. On layers with >10k features a naive loop takes 5–30 seconds.
Avoid it for data analysis:

- NEVER use list(layer.getFeatures()) or a bare for-loop over all features
  for stats.
- NEVER compute count / mean / sum / min / max manually in run_pyqgis —
  use get_layer_statistics(layer_id, field_name) instead (runs in
  background, cached).
- NEVER use run_pyqgis to summarise a field — use analyze_layer instead.
- Use layer.featureCount() for a count; it is instant.
- Use run_pyqgis only for spatial operations (geometry, buffer, join,
  layer creation) or QGIS automation that no structured tool provides.

If a run_pyqgis result includes a "slow_ms" key, the last call was slow.
Switch to a structured tool for the same operation on the next step."""

MAX_TOKENS = 4096


class OpenAIBackend(AgentBackend):
    label = "API (OpenAI-compatible)"

    def __init__(self, config, toolkit, executor):
        self.config = config
        self.toolkit = toolkit
        self.executor = executor
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
            api_key = self.config.get("api_key") or ""
            base_url = self.config.get("custom_base_url")
        return OpenAIHttpClient(api_key=api_key or None, base_url=base_url)

    def validate(self):
        p = self._provider()
        if p:
            key = self.config.get("api_key") or os.environ.get(p["key_env"], "")
            label = p["label"]
        else:
            key = self.config.get("api_key")
            label = "Custom endpoint"
        if not key:
            return (
                f"No API key set for {label}. "
                f"Add one in Settings (or set {p['key_env'] if p else 'the provider key env'})."
            )
        return None

    def _system_text(self):
        return self.config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    def _tool_list(self):
        if self._cached_tool_list is None:
            self._cached_tool_list = OpenAIHttpClient.build_tool_list(tools_mod.TOOL_SPECS)
        return self._cached_tool_list

    def _compact_history(self, messages, emit):
        """Summarize old messages and return a trimmed list."""
        if len(messages) <= _COMPACTION_KEEP_TAIL:
            return messages
        head = messages[:-_COMPACTION_KEEP_TAIL]
        tail = messages[-_COMPACTION_KEEP_TAIL:]
        sum_messages = list(head) + [{
            "role": "user",
            "content": (
                "Summarize the conversation above in bullet points. "
                "Keep: layer IDs, key findings, tool results, decisions made. "
                "Be concise — max 300 words."
            ),
        }]
        try:
            client = self._client()
            model = self.config.get("model")
            content, _ = client.stream_message(
                model=model,
                max_tokens=1024,
                system=self._system_text(),
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
            {"role": "assistant", "content": "Understood. Continuing with full context."},
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
                content, finish_reason = client.stream_message(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=self._system_text(),
                    tools=self._tool_list(),
                    messages=messages,
                    on_text=lambda t: emit(AgentEvent(EventType.TEXT, {"text": t})),
                    should_stop=should_stop,
                )
            except OpenAIHttpError as exc:
                emit(AgentEvent(EventType.ERROR, {"error": str(exc)}))
                return messages
            except Exception as exc:  # noqa: BLE001
                emit(AgentEvent(EventType.ERROR,
                                {"error": f"{type(exc).__name__}: {exc}"}))
                return messages

            # Build the assistant message for the next turn
            text = ""
            tool_calls = []
            for b in content:
                if b.get("type") == "text":
                    text = b.get("text", "")
                elif b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    })
            assistant_msg = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if finish_reason != "tool_calls" or not tool_calls:
                emit(AgentEvent(EventType.DONE))
                return messages

            # Dispatch tools and build tool result messages
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                emit(AgentEvent(EventType.TOOL_USE, {"name": name, "input": args}))
                result = None
                is_error = False
                is_cancelled = False
                try:
                    result = tools_mod.dispatch(
                        self.toolkit, self.executor, name, args
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
                except Exception as exc:  # noqa: BLE001
                    payload = f"Tool error: {type(exc).__name__}: {exc}"
                    is_error = True
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": name,
                    "result": payload,
                    "is_error": is_error,
                    "cancelled": is_cancelled,
                }))
                if name == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif name == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
                messages.append(
                    OpenAIHttpClient.build_tool_result_message(tc["id"], payload)
                )
        else:
            if is_unlimited:
                return messages
            emit(AgentEvent(EventType.THINKING,
                            {"text": f"Reached max {max_iters} iterations."}))
            emit(AgentEvent(EventType.DONE))
        return messages
