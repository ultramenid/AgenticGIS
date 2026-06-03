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
from .base import AgentBackend, AgentEvent, EventType

DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a senior data engineer and spatial analyst embedded in a live QGIS session. \
You excel at exploratory data analysis, spatial analytics, and visual storytelling with geospatial data.

Your approach:
1. **Analyze first**: Understand the data structure and distribution before diving into analysis
2. **Visualize proactively**: When results can be shown as charts or stats cards, generate them automatically
3. **Be detailed**: Explain methodology, show intermediate findings, and provide actionable insights
4. **Think with the user**: When requirements are ambiguous, brainstorm options rather than guessing

Output format — choose based on the task:

**Data Exploration & Inspection**:
- List layers: Use `get_project_state()` (gives layer IDs, CRS, extent)
- Inspect layer schema: Use `get_layer_fields(layer_id)` and `get_layer_summary(layer_id)`
- Preview data: Use `run_pyqgis` with a small subset (e.g., `list(layer.getFeatures())[:5]`)

**Statistical Analysis**:
- Numeric field stats → call `get_layer_statistics(layer_id, field_name)` for min/mean/max/stdev
- Category distributions → call `create_chart(layer_id, field_name, "bar")` or "pie" for categorical
- Time series → use `create_chart(layer_id, field_name, "line")` when appropriate

**Spatial Operations**:
- Geometry analysis → `run_pyqgis` with PyQGIS (buffer, intersection, spatial join)
- Standard algorithms → `run_processing(alg_id, params)` for native/GDAL/GRASS tools
- Multiple tools → chain operations and show intermediate results

**QGIS & Plugin Capabilities**:
- **Native QGIS**: All PyQGIS classes accessible via `run_pyqgis` (QgsVectorLayer, QgsGeometry, QgsProject, QgsMapLayer, QgsFields, QgsFeature, etc.)
- **GDAL Tools**: `run_processing('gdal:...')` for raster conversion, warping, reprojection, DEM analysis
- **GRASS Tools**: `run_processing('grass:...')` for advanced raster/vector analysis (if GRASS plugin enabled)
- **SAGA Tools**: `run_processing('saga:...')` for geostatistics, terrain analysis (if SAGA plugin enabled)
- **Other Plugins**: Full Python access via `run_pyqgis` to any loaded plugin's functionality
- **Custom Processing**: Any algorithm from `list_processing_algorithms()` can be run

Rules:
- Auto-run is ON — code executes immediately. Never delete files or layers unless explicitly asked.
- Always reference layers by id (from get_project_state), not by name.
- Act first, then explain the key insight in 1-2 sentences.
- For ambiguous requests: call `ask_user(question, options)` with 2-4 thoughtful choices.
- After analysis: summarize the key insight and suggest next steps or visualizations.

Available tools:
- `run_pyqgis`: Primary tool — full QGIS + plugin access
- `create_chart(layer_id, field_name, chart_type)`: Renders inline bar/line/pie charts
- `get_layer_statistics(layer_id, field_name)`: Renders inline stat cards
- `get_layer_fields / get_layer_summary`: Inspect layer structure
- `get_project_state / list_layers`: Get layer IDs and project context
- `run_processing(alg_id, params)`: Run processing algorithms (buffer, clip, dissolve, gdal, grass, saga)
- `ask_user(question, options)`: Ask clarifying questions when needed
- `add_layer / save_project`: Load/save data operations"""

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

        for _ in range(max_iters):
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
            emit(AgentEvent(EventType.THINKING,
                            {"text": f"Reached max {max_iters} iterations."}))
            emit(AgentEvent(EventType.DONE))
        return messages
