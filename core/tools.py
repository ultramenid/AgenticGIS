"""Single declarative definition of the agent tool surface.

Both the in-process API backend and the MCP bridge build their tool lists from
``TOOL_SPECS`` so the two connection modes expose identical capabilities. Each
spec maps a tool name to a JSON Schema and the ``QgisToolkit`` method that
implements it.
"""

import time
import traceback

from .dev_logging import log_event

TOOL_SPECS = [
    {
        "name": "run_pyqgis",
        "method": "run_pyqgis",
        "description": (
            "Execute arbitrary PyQGIS Python code in the running QGIS instance. "
            "Prefer analyze_layer and dedicated tools first. Pre-bound: iface, "
            "QgsProject, QgsApplication, processing, QgsFeatureRequest, "
            "get_layer, _iterate_features, _sample_features, all qgis.core names. "
            "Always fetch layers with get_layer(ref) — it resolves project layer "
            "ids, kept tool-output ids (e.g. 'output_...' from run_processing), "
            "and layer names; QgsProject.mapLayer alone cannot see kept outputs. "
            "Do not use list(layer.getFeatures()); use _sample_features() or "
            "_iterate_features(limit=...). Do not fetch geometry when only "
            "attributes are needed. Assign `result` to return a value; if "
            "`result` is a layer it is kept in memory and its id is returned "
            "for use with add_layer or run_processing (or call "
            "_register_layer(layer) for extra layers). "
            "External file/URL/database access requires user permission. "
            "Set background=true for long downloads, network loops, or heavy "
            "compute that do NOT touch the QGIS UI/canvas/project — the code "
            "then runs off the main thread so QGIS stays responsive. In "
            "background mode do not call iface, mapCanvas, or add layers to the "
            "project; download/compute there, then load results with add_layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "PyQGIS code to execute."},
                "background": {
                    "type": "boolean",
                    "description": (
                        "Run off the main thread in a QgsTask (keeps QGIS "
                        "responsive). Use for downloads/network loops/heavy "
                        "compute that do not touch the QGIS UI, canvas, or "
                        "project. Default false (runs on the main thread)."
                    ),
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_project_state",
        "method": "get_project_state",
        "description": (
            "Return QGIS project state: path, CRS, layer list, active layer, "
            "and canvas extent. Call first to understand the workspace."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_layers",
        "method": "list_layers",
        "description": "List project layers with brief metadata. Supports pagination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max layers to return.",
                    "minimum": 1,
                },
                "offset": {
                    "type": "integer",
                    "description": "Layers to skip (default 0).",
                    "minimum": 0,
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "get_layer_fields",
        "method": "get_layer_fields",
        "description": "List attribute fields of a vector layer.",
        "input_schema": {
            "type": "object",
            "properties": {"layer_id": {"type": "string"}},
            "required": ["layer_id"],
        },
    },
    {
        "name": "get_layer_summary",
        "method": "get_layer_summary",
        "description": "Detailed summary of a layer: source, extent, fields, counts.",
        "input_schema": {
            "type": "object",
            "properties": {"layer_id": {"type": "string"}},
            "required": ["layer_id"],
        },
    },
    {
        "name": "analyze_layer",
        "method": "analyze_layer",
        "description": (
            "Preferred tool for exploratory vector layer analysis. Returns "
            "bounded/performance-safe summary, field statistics, top category "
            "values, samples, and missing-value counts. Use before run_pyqgis, "
            "especially on large layers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "analysis_type": {
                    "type": "string",
                    "description": "auto|summary|field_stats|category_counts|top_values|sample|missing_values",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields to analyze (omit for all).",
                },
                "field_name": {
                    "type": "string",
                    "description": "Single field shorthand when fields is omitted.",
                },
                "sample_limit": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Max sample rows.",
                },
                "scan_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Max features to scan before truncated=true.",
                },
                "top_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Max top values per categorical field.",
                },
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "list_plugins",
        "method": "list_plugins",
        "description": "List installed/active QGIS plugins and their capabilities.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_processing_algorithms",
        "method": "list_processing_algorithms",
        "description": (
            "Search available Processing algorithms (native, GDAL, GRASS, "
            "SAGA, plugins) by id or name substring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"filter_text": {"type": "string"}},
        },
    },
    {
        "name": "run_processing",
        "method": "run_processing",
        "description": (
            "Run a Processing algorithm by id with a parameter dict "
            "(e.g. alg_id='native:buffer', params={'INPUT': id, 'DISTANCE': 50, "
            "'OUTPUT': 'memory:'}). Runs as a background task with cancellation. "
            "Temporary output layers are kept in memory: pass the returned output "
            "id (e.g. 'output_...') directly as a param to a later run_processing "
            "call to chain algorithms, or as add_layer uri to add it to the project. "
            "Vector outputs also report <KEY>_feature_count — when the question "
            "only needs a count (how many inside/outside/within), read it from "
            "the result instead of counting with run_pyqgis. "
            "File/URI/database output outside loaded layers requires user permission."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "alg_id": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["alg_id", "params"],
        },
    },
    {
        "name": "add_layer",
        "method": "add_layer",
        "description": (
            "Load a layer from a file path/URI and add it to the project. "
            "Also accepts an in-memory result id from run_processing output or "
            "a run_pyqgis result (pass it as uri) — that exact layer is added, "
            "not re-opened. External sources require user permission. No zoom "
            "by default. Set is_analysis=true for derived results (buffers, "
            "joins, etc.) to track and auto-rename them. Call zoom_to_layer after."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "name": {"type": "string"},
                "provider": {
                    "type": "string",
                    "description": "ogr (vector, default), gdal (raster), postgres, etc.",
                },
                "zoom": {
                    "type": "boolean",
                    "description": "Zoom the map canvas to the new layer (default false).",
                    "default": False,
                },
                "is_analysis": {
                    "type": "boolean",
                    "description": "Mark as a derived result layer: tracked, reused by name, kept across turns.",
                    "default": False,
                },
            },
            "required": ["uri"],
        },
    },
    {
        "name": "zoom_to_layer",
        "method": "zoom_to_layer",
        "description": (
            "Fit the map canvas to a layer's extent. Provide layer_id (preferred) "
            "or exact layer_name. Call after producing a result layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "layer_name": {"type": "string"},
            },
        },
    },
    {
        "name": "gee_status",
        "method": "gee_status",
        "description": (
            "Check GEE plugin status and authentication. Call FIRST before any "
            "GEE operation (remote sensing, satellite imagery, NDVI, land cover, "
            "image collections). Returns plugin_installed, ee_available, "
            "initialized, authenticated, and next-step message if incomplete."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "gee_dataset_info",
        "method": "gee_dataset_info",
        "description": (
            "Look up current metadata for an Earth Engine dataset from the public "
            "STAC catalog (no auth needed). Call BEFORE writing gee_add_layer or "
            "gee_animation code to get real band names, properties, date range, "
            "and deprecated flag — not a memorized snapshot. Returns band_names, "
            "bands (gee:scale/offset, gsd), properties, date_range, type, "
            "deprecated. For cloud masking also look up the companion mask dataset. "
            "Works for user assets too (source='asset'; requires gee_status authenticated). "
            "CRITICAL: after this call, IMMEDIATELY call gee_add_layer or gee_animation "
            "— do NOT stop or ask the user for confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": (
                        "Exact, case-sensitive EE dataset id (e.g. "
                        "'COPERNICUS/S2_SR_HARMONIZED') or user asset path."
                    ),
                },
            },
            "required": ["dataset_id"],
        },
    },
    {
        "name": "gee_add_layer",
        "method": "gee_add_layer",
        "description": (
            "Run an EE expression and add the result to QGIS via ee_plugin. "
            "Requires gee_status=ready and user consent. `code` has `ee`, `Map`, "
            "`iface`, `region` (ee.Geometry, EPSG:4326), `features` (ee.FeatureCollection|None); "
            "must assign ee.Image/ImageCollection/FeatureCollection to `result`. "
            "vis_params 'scale' is ignored — use clipToBoundsAndScale in code. "
            "geometry_mode='auto' returns needs_decision if layer too large — retry "
            "with 'bbox'/'simplify'/'exact'. Use export_format='geotiff' for instant zoom."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "EE Python expression. Must assign ee object to `result`. "
                        "`ee`, `region` (ee.Geometry), `features` (ee.FeatureCollection|None) in scope."
                    ),
                },
                "vis_params": {
                    "type": "object",
                    "description": (
                        "EE visualization params (min, max, palette, bands, gamma). "
                        "scale is ignored — control resolution in code."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the added GEE layer.",
                },
                "region_layer_id": {
                    "type": "string",
                    "description": (
                        "QGIS layer id whose geometry defines `region`/`features` "
                        "(EPSG:4326) and is the zoom target."
                    ),
                },
                "zoom": {
                    "type": "boolean",
                    "description": "Zoom to region_layer_id after adding (default true).",
                    "default": True,
                },
                "geometry_mode": {
                    "type": "string",
                    "enum": ["auto", "exact", "simplify", "bbox"],
                    "description": (
                        "How to send region_layer_id geometry. 'auto': true geometry, "
                        "returns needs_decision if too large. 'exact': true geometry "
                        "(hard ceiling). 'simplify': reduce vertices. 'bbox': bounding box."
                    ),
                    "default": "auto",
                },
                "max_vertices": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Vertex budget before 'auto' asks the user (default 5000).",
                    "default": 5000,
                },
                "max_features": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Feature-count budget before 'auto' asks the user (default 2000).",
                    "default": 2000,
                },
                "export_format": {
                    "type": "string",
                    "enum": ["map", "geotiff"],
                    "description": (
                        "'geotiff' (default): local raster download (instant zoom). "
                        "'map': WMS tile layer via ee_plugin (no download wait)."
                    ),
                    "default": "geotiff",
                },
                "export_scale": {
                    "type": "integer",
                    "minimum": 10,
                    "description": "GeoTIFF resolution in meters (default 250). Smaller = more detail.",
                    "default": 250,
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "gee_animation",
        "method": "gee_animation",
        "description": (
            "Produce an animated GIF timelapse from an Earth Engine "
            "ImageCollection and show it inline in the chat. Use for any "
            "request to visualize change over time or create a timelapse/GIF. "
            "Only use after gee_status confirms GEE is ready AND gee_dataset_info "
            "has been called for all needed datasets. Do not use run_pyqgis for GIFs. "
            "`code` runs with `ee`, `Map`, `region` (ee.Geometry), and `features` "
            "in scope; must assign an ee.ImageCollection to `result` — one frame "
            "per image (e.g. one composite per month, each visualized as RGB). "
            "Keep `dimensions` and frame count modest: EE caps animations at "
            "6,553,600 total pixels (e.g. 480px x ~12-24 frames). GIF only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "EE Python expression. Must assign an ee.ImageCollection "
                        "(one frame per image) to `result`. `ee`, `region`, `features` in scope."
                    ),
                },
                "vis_params": {
                    "type": "object",
                    "description": (
                        "EE visualization params merged into video params. "
                        "Prefer .visualize(**vis) inside `code` for RGB frames."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the animation.",
                },
                "region_layer_id": {
                    "type": "string",
                    "description": (
                        "QGIS layer id whose geometry defines `region`/`features` "
                        "and the animation footprint."
                    ),
                },
                "fps": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Frames per second of the GIF (default 2).",
                    "default": 2,
                },
                "dimensions": {
                    "type": "integer",
                    "minimum": 16,
                    "description": "Larger side in pixels (default 480). Stay under the 6,553,600-pixel cap.",
                    "default": 480,
                },
                "frame_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Per-frame captions in playback order (e.g. ['2020','2021']). "
                        "Derive from the actual date sequence, never hardcode."
                    ),
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "remove_layer",
        "method": "remove_layer",
        "description": (
            "Unload a layer from the QGIS project by layer_id or exact layer_name. "
            "This never deletes source files, tables, or remote data. Use only when the "
            "user explicitly asks to remove or unload a layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": "Exact QGIS layer id from get_project_state or list_layers.",
                },
                "layer_name": {
                    "type": "string",
                    "description": "Exact layer name. Refused if ambiguous (returns candidate IDs).",
                },
            },
        },
    },
    {
        "name": "clear_layers",
        "method": "clear_layers",
        "description": (
            "Unload all layers from the QGIS project (never deletes source files). "
            "Use only when the user explicitly asks to clear all layers. "
            "Requires confirm=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to confirm clearing all layers.",
                },
            },
            "required": ["confirm"],
        },
    },
    {
        "name": "save_project",
        "method": "save_project",
        "description": "Save the current QGIS project to its file.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_chart",
        "method": "create_chart",
        "description": (
            "Create a bar/line/pie chart. Two modes: (1) from a vector layer — "
            "pass layer_id + field_name; by default counts features per distinct "
            "field_name value. For a numeric measure (e.g. total area per "
            "category), set value_field and aggregate='sum'/'mean'/'max'/'min'; "
            "field_name is the category axis. Do NOT pass a numeric field "
            "directly as field_name — use value_field instead. Use label_field "
            "for readable display labels when field_name holds codes/IDs. "
            "(2) from values you already computed — pass data=[{label, value}, "
            "...] (and optionally title) with NO layer_id; never build a memory "
            "layer just to chart numbers you already have. Supply colors (hex "
            "strings) to override the default palette; cycles if shorter than "
            "the data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": "Layer mode only. Omit when passing data.",
                },
                "field_name": {
                    "type": "string",
                    "description": "Layer mode only. Omit when passing data.",
                },
                "data": {
                    "type": "array",
                    "description": (
                        "Already-computed chart rows: [{'label': str, 'value': "
                        "number}, ...]. When given, layer_id/field_name are "
                        "ignored and no layer is read."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "number"},
                        },
                        "required": ["label", "value"],
                    },
                },
                "title": {
                    "type": "string",
                    "description": "Chart title (used with data mode).",
                },
                "label_field": {
                    "type": "string",
                    "description": (
                        "Optional field for readable display labels (e.g. name "
                        "field paired with a code field_name). Falls back to "
                        "field_name values if invalid or blank."
                    ),
                },
                "value_field": {
                    "type": "string",
                    "description": (
                        "Optional numeric field to aggregate per category "
                        "(grouped by field_name). Omit to count occurrences."
                    ),
                },
                "aggregate": {
                    "type": "string",
                    "enum": ["count", "sum", "mean", "max", "min"],
                    "description": (
                        "Aggregation for value_field. Defaults to 'count' if "
                        "value_field is omitted, else 'sum'."
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "description": "bar (default), line, or pie",
                },
                "colors": {
                    "type": "array",
                    "description": (
                        "Optional hex color strings (e.g. '#5d8aa8'). Applied "
                        "in display order; cycles if shorter than data points."
                    ),
                    "items": {
                        "type": "string",
                        "pattern": "^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$",
                    },
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_layer_statistics",
        "method": "get_layer_statistics",
        "description": (
            "Calculate statistics for a vector layer or a specific field. "
            "Returns count, min, max, mean, sum, and distinct value counts. "
            "Scans at most 100k features; if `truncated` is true the values "
            "are partial — compute exact aggregates another way."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "field_name": {"type": "string", "description": "Optional field to analyze"},
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "configure_network_cache",
        "method": "configure_network_cache",
        "description": (
            "Enable, adjust, or report QGIS's shared network disk cache "
            "(WMS/WMTS/XYZ/GEE tile layers). Pass size_mb to set max cache size "
            "(>0 enables; 0 disables); omit to report current size and directory. "
            "Does not affect GEE geotiff downloads (those save local files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "size_mb": {
                    "type": "number",
                    "description": "Max cache in MB (>0 enables; 0 disables). Omit to report without changing.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "warm_cache",
        "method": "warm_cache",
        "description": (
            "Pre-fetch map tiles for a WMS/XYZ/GEE tile layer into the disk "
            "cache for instant access. Useful for demo prep or repeat views. "
            "zoom_levels defaults to current zoom ± 1; capped at 500 tiles."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": "Layer ID from get_project_state or list_layers.",
                },
                "zoom_levels": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Zoom levels to preload, e.g. [5, 6, 7].  "
                        "Default: current zoom ± 1."
                    ),
                },
                "max_tiles": {
                    "type": "integer",
                    "default": 500,
                    "description": "Hard safety limit for number of tiles.",
                },
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "web_fetch",
        "method": "web_fetch",
        "description": (
            "Fetch a public URL (GET only). Text responses return body, HTTP "
            "status, content-type, and parsed JSON when available. Binary "
            "responses (ZIP, GeoTIFF, images, ...) are saved to a temp file "
            "instead — the result then has file_path and size_bytes, never "
            "the raw bytes; pass file_path to add_layer or extract it with "
            "run_pyqgis. Requires explicit user permission for external "
            "URLs. Set verify_ssl=false for self-signed or incomplete "
            "certificate chains."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch (http:// or https://).",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum response length in bytes (default 500k).",
                    "minimum": 1,
                    "maximum": 1_000_000,
                    "default": 500_000,
                },
                "verify_ssl": {
                    "type": "boolean",
                    "description": "Verify SSL certificate (default true). Set false for self-signed certs.",
                    "default": True,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "ask_user",
        "method": "ask_user",
        "description": (
            "Pause and ask the user a clarifying question. Use PROACTIVELY "
            "whenever fields, layer, CRS, or any required detail is unspecified, "
            "and reactively when a result looks suspicious. Always provide 2-4 "
            "options (first = recommended). Returns 'choice' (picked label or "
            "null), 'free_text' (typed reply or null), 'cancelled' (bool)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The clarifying question, in the user's working language.",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Short button label."},
                            "description": {"type": "string", "description": "Helper text."},
                        },
                        "required": ["label"],
                    },
                    "description": "2-4 choices; first is the recommended one.",
                },
                "allow_free_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, the user can type a reply instead of picking an option.",
                },
            },
            "required": ["question", "options"],
        },
    },
]

TOOL_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}

_GEE_TOOL_NAMES = {"gee_status", "gee_dataset_info", "gee_add_layer", "gee_animation"}


def tool_specs(include_gee=True):
    """Return the list of tool specs, optionally omitting the four gee_* tools.

    Use this everywhere a filtered view is needed instead of referencing
    ``TOOL_SPECS`` directly.  ``TOOL_SPECS`` itself is intentionally left
    unchanged so existing callers keep working.
    """
    if include_gee:
        return TOOL_SPECS
    return [spec for spec in TOOL_SPECS if spec["name"] not in _GEE_TOOL_NAMES]


def _dispatch_cancelled(should_stop):
    try:
        return bool(should_stop and should_stop())
    except Exception:
        return False


def _cancelled_result():
    return {"ok": False, "error": "cancelled by user", "cancelled": True}


def _log_dispatch_end(tool, path, start, result=None):
    """Emit a uniform ``tool.dispatch.end`` log_event for any dispatch path."""
    ok = None
    cancelled = False
    error = None
    if isinstance(result, dict):
        ok = result.get("ok")
        cancelled = bool(result.get("cancelled"))
    elif isinstance(result, str):
        ok = False
        error = result
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log_event(
        "tool.dispatch.end",
        tool=tool,
        path=path,
        elapsed_ms=elapsed_ms,
        ok=ok,
        cancelled=cancelled,
        error=error,
    )


# run_pyqgis is BACKGROUND-BY-DEFAULT: code runs on a QgsTask worker thread unless
# it touches main-thread-only QGIS state (see _PYQGIS_MAIN_THREAD_MARKERS). This
# inverts the old allowlist (which only offloaded a hardcoded set of "heavy"
# idioms and froze the UI on any idiom it had not seen yet). Now any long
# operation — a download, a file conversion, a Processing run over files, a big
# pure-Python loop — offloads automatically, even ones not enumerated here.
#
# The network/heavy marker lists below no longer drive the decision; they are kept
# only to classify *why* a given snippet was offloaded in the dev log, which makes
# diagnosing future freezes/crashes far easier.
_PYQGIS_NETWORK_MARKERS = (
    "urllib.request", "urlopen", "urlretrieve",
    "requests.", "http.client", "httpx", "httplib", "urllib3",
    "socket.socket", "ftplib", "QgsNetworkAccessManager",
    "QgsBlockingNetworkRequest", "smtplib",
)
_PYQGIS_HEAVY_MARKERS = (
    "QgsVectorFileWriter", "writeAsVectorFormat",
    "shutil.", "zipfile", "tarfile",
    "ogr2ogr", "gdal.Translate", "gdal.Warp", "gdal.Rasterize",
    "processing.run",
)

# Substrings that mean the code touches main-thread-only QGIS state — the GUI,
# the map canvas, the message bar, print layouts, or the project's layer registry.
# These are NOT safe to run on a worker thread: doing so can crash QGIS, not just
# hang it. Code matching any of these stays on the main thread regardless of how
# heavy it is (the agent can split the heavy download/convert step from the UI
# step). Project *layers* are not thread-safe to read/write from a worker, hence
# the registry/editing/toolkit-helper markers. Keep this list conservative and
# broad: a false negative here (an unlisted main-thread API) is a potential crash,
# whereas a false positive only means a bit of heavy code stays foreground.
_PYQGIS_MAIN_THREAD_MARKERS = (
    # GUI widgets / dialogs
    "iface", "QMessageBox", "QDialog", "QInputDialog", "QFileDialog",
    "QProgressDialog", "QColorDialog", "QFontDialog",
    # map canvas, rubber bands, map tools, rendering
    "mapCanvas", "QgsRubberBand", "QgsVertexMarker", "QgsHighlight",
    "QgsMapTool", "QgsMapRendererJob", "QgsMapSettings", "triggerRepaint",
    # message bar / status
    "messageBar", "pushMessage",
    # layer tree
    "layerTreeRoot", "layerTreeView",
    # print / report layouts
    "QgsLayout", "QgsPrintLayout", "QgsLayoutExporter", "layoutManager",
    # project layer registry (reading/mutating it off-thread is unsafe)
    "addMapLayer", "removeMapLayer", "setActiveLayer",
    "addVectorLayer", "addRasterLayer",
    "mapLayer(", "mapLayersByName",
    # layer editing buffers (tied to the project layer they edit)
    "startEditing", "commitChanges", "rollBack",
    # toolkit helpers that read project layers on the main thread
    "get_layer", "_iterate_features", "_sample_features", "_make_layer_cache",
    "_register_layer",
)


def _should_auto_background_pyqgis(code):
    """True when run_pyqgis ``code`` should run on a worker thread.

    Background-by-default: anything that does NOT touch main-thread-only QGIS
    state (UI, canvas, print layouts, project layers) is offloaded so a long
    operation the model forgot to mark ``background=true`` cannot freeze the QGIS
    UI — including heavy idioms not individually enumerated. Code that touches
    main-thread state stays foreground because running it on a worker can crash
    QGIS.
    """
    if not isinstance(code, str) or not code:
        return False
    return not any(marker in code for marker in _PYQGIS_MAIN_THREAD_MARKERS)


def _pyqgis_background_reason(code):
    """Human-readable classification of why ``code`` was offloaded — for the dev
    log only. Falls back to a generic label so unrecognised heavy idioms still
    get a sensible reason."""
    if any(m in code for m in _PYQGIS_NETWORK_MARKERS):
        return "network I/O detected; routed off main thread"
    if any(m in code for m in _PYQGIS_HEAVY_MARKERS):
        return "heavy local IO / processing detected; routed off main thread"
    return ("no main-thread (UI/canvas/project) access detected; "
            "routed off main thread by default")


def dispatch(toolkit, executor, name, arguments, should_stop=None):
    """Run tool ``name`` with ``arguments`` against ``toolkit`` on the main
    thread (via ``executor``) and return its result. Raises ``KeyError`` for
    an unknown tool name."""
    spec = TOOL_BY_NAME[name]
    method = getattr(toolkit, spec["method"])
    args = dict(arguments or {})
    start = time.perf_counter()
    log_event("tool.dispatch.start", tool=name, arg_keys=sorted(args.keys()))

    try:
        if _dispatch_cancelled(should_stop):
            _log_dispatch_end(name, "pre_cancelled", start, _cancelled_result())
            return _cancelled_result()

        # Truncated/corrupt tool-call JSON arrives as {"_parse_error": ...}
        # (see backends/openai_http.py). Calling the method with that key as a
        # kwarg raises TypeError; instead hand the parse error straight back so
        # the model knows to re-emit the call.
        if "_parse_error" in args:
            result = {
                "ok": False,
                "error": str(args["_parse_error"]),
                "hint": (
                    "Your previous tool call's arguments were not valid JSON "
                    "(likely truncated). Re-issue the call with complete, valid "
                    "JSON arguments."
                ),
            }
            _log_dispatch_end(name, "parse_error", start, result)
            return result

        if name == "ask_user":
            if _dispatch_cancelled(should_stop):
                return _cancelled_result()
            result = method(**args)
            _log_dispatch_end(name, "ask_user", start, result)
            return result

        confirm_external_access = getattr(toolkit, "confirm_external_access", None)
        if confirm_external_access is not None:
            if _dispatch_cancelled(should_stop):
                return _cancelled_result()
            denied = confirm_external_access(name, args)
            if denied is not None:
                _log_dispatch_end(name, "denied", start, denied)
                return denied

        if name == "run_pyqgis" and hasattr(toolkit, "run_pyqgis_background"):
            wants_bg = bool(args.get("background"))
            auto_bg = not wants_bg and _should_auto_background_pyqgis(args.get("code"))
            if wants_bg or auto_bg:
                if _dispatch_cancelled(should_stop):
                    return _cancelled_result()
                if auto_bg:
                    log_event(
                        "tool.dispatch.auto_background",
                        tool=name,
                        reason=_pyqgis_background_reason(args.get("code") or ""),
                    )
                result = toolkit.run_pyqgis_background(executor, args.get("code"))
                if auto_bg and isinstance(result, dict):
                    result.setdefault(
                        "hint",
                        "This code did not touch the QGIS project/canvas/UI so it "
                        "was run off the main thread automatically to keep QGIS "
                        "responsive. Pass background=true explicitly for "
                        "downloads, conversions, and other long operations.",
                    )
                _log_dispatch_end(
                    name,
                    "pyqgis_background_auto" if auto_bg else "pyqgis_background",
                    start,
                    result,
                )
                return result

        if name == "run_processing" and hasattr(toolkit, "run_processing_background"):
            if _dispatch_cancelled(should_stop):
                return _cancelled_result()
            result = toolkit.run_processing_background(
                executor,
                args.get("alg_id"),
                args.get("params") or {},
            )
            _log_dispatch_end(name, "processing_task", start, result)
            return result

        can_run_background = getattr(toolkit, "can_run_background", None)
        if can_run_background is not None and can_run_background(name):
            if _dispatch_cancelled(should_stop):
                return _cancelled_result()
            result = toolkit.run_background_tool(executor, name, args)
            _log_dispatch_end(name, "background", start, result)
            return result

        def run_method():
            if _dispatch_cancelled(should_stop):
                return _cancelled_result()
            return method(**args)

        result = executor.run_sync(run_method)
        _log_dispatch_end(name, "main_thread", start, result)
        return result
    except BaseException as exc:
        log_event(
            "tool.dispatch.error",
            tool=name,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            error_type=type(exc).__name__,
            error=str(exc),
            traceback="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__, limit=4)
            ),
        )
        raise


def anthropic_tool_list(include_gee=True):
    """Tool definitions in Anthropic Messages API shape."""
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec["input_schema"],
        }
        for spec in tool_specs(include_gee=include_gee)
    ]
