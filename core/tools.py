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
            "Execute arbitrary PyQGIS Python code inside the running QGIS "
            "instance. For layer analysis, prefer dedicated tools for summaries, "
            "statistics, charts, schema inspection, project state, and processing "
            "algorithms, especially analyze_layer, before using arbitrary PyQGIS. "
            "This escape hatch can reach "
            "EVERY QGIS feature and EVERY installed plugin. Pre-bound names: iface, "
            "QgsProject, QgsApplication, processing, QgsFeatureRequest, "
            "_iterate_features, _sample_features, and all qgis.core names. "
            "For large layers: Do not use list(layer.getFeatures()), do not "
            "materialize all features. Do not fetch geometry when only "
            "attributes are needed; use _sample_features(...) for previews or "
            "iterate _iterate_features(..., limit=...) in chunks. "
            "Assign to a variable named `result` to return a value. stdout and "
            "stderr are captured and returned. Access to external files, URLs, "
            "databases, or other sources outside currently loaded project layers "
            "requires explicit user permission."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "PyQGIS code to execute."}
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_project_state",
        "method": "get_project_state",
        "description": (
            "Return the current QGIS project state: path, CRS, layer list with "
            "brief metadata, the active layer, and the canvas extent. Call this "
            "first to understand the workspace."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_layers",
        "method": "list_layers",
        "description": "List layers in the project with brief metadata. Supports optional pagination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of layers to return.",
                    "minimum": 1,
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of layers to skip before returning results.",
                    "minimum": 0,
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "get_layer_fields",
        "method": "get_layer_fields",
        "description": "List the attribute fields of a vector layer by its id.",
        "input_schema": {
            "type": "object",
            "properties": {"layer_id": {"type": "string"}},
            "required": ["layer_id"],
        },
    },
    {
        "name": "get_layer_summary",
        "method": "get_layer_summary",
        "description": "Detailed summary of one layer: source, extent, fields, counts.",
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
            "Preferred tool for exploratory vector layer analysis. Returns a "
            "bounded, performance-safe summary, field statistics, top category "
            "values, samples, and missing-value counts using no-geometry "
            "feature requests and background processing where possible. Use "
            "this before run_pyqgis for layer analysis, especially on large "
            "layers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "analysis_type": {
                    "type": "string",
                    "description": "auto, summary, field_stats, category_counts, top_values, sample, or missing_values",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of fields to analyze. Omit to analyze all fields.",
                },
                "field_name": {
                    "type": "string",
                    "description": "Convenience single field to analyze when fields is omitted.",
                },
                "sample_limit": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Maximum sample rows to return.",
                },
                "scan_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum features to scan before returning truncated=true.",
                },
                "top_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum top values per categorical field.",
                },
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "list_plugins",
        "method": "list_plugins",
        "description": (
            "List installed/active/loaded QGIS plugins so you know what extra "
            "capability is available to drive via run_pyqgis or run_processing."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_processing_algorithms",
        "method": "list_processing_algorithms",
        "description": (
            "Search available Processing algorithms (native, GDAL, GRASS, SAGA "
            "and plugin-provided). Filter by a substring of id or display name."
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
            "Run a Processing algorithm by id with a parameter dict, e.g. "
            "alg_id='native:buffer', params={'INPUT': <id>, 'DISTANCE': 50, "
            "'OUTPUT': 'memory:'}. Runs through a QGIS background task when "
            "available, with cancellation support. File path, URI, database, or non-memory output "
            "parameters outside loaded layers require explicit user permission."
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
            "Load a layer from a file path / URI and add it to the project. "
            "External sources outside currently loaded layers require explicit "
            "user permission. By default this does not zoom or force an immediate "
            "canvas refresh, which keeps large layer loads responsive. "
            "Set is_analysis=true for layers you derive as analysis results "
            "(buffers, joins, filtered subsets, etc.): these are tracked as "
            "persistent results, preserved across turns, and automatically "
            "renamed (e.g. 'NDVI (2)') if a layer with the same name already "
            "exists from a previous turn. After this, call zoom_to_layer so the user "
            "sees the result on the map."
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
                    "description": (
                        "Mark this as a derived analysis/result layer so it is "
                        "tracked, reused by name, and kept (not auto-deleted)."
                    ),
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
            "Fit the map canvas to a layer's extent so the user sees the "
            "result. Provide layer_id (preferred) or an exact layer_name. Call "
            "this after producing a result layer the user should look at."
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
            "Check whether the Google Earth Engine QGIS plugin (ee_plugin) is "
            "installed and whether Earth Engine is authenticated and "
            "initialized. Call this FIRST, before any GEE operation, whenever "
            "the user's request involves remote sensing, satellite imagery, "
            "Earth Engine, NDVI or other spectral indices, land cover, or "
            "image collections. Returns plugin_installed, ee_available, "
            "initialized, authenticated, and a human-readable message with "
            "next steps if setup is incomplete."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "gee_dataset_info",
        "method": "gee_dataset_info",
        "description": (
            "Look up the CURRENT metadata for an Earth Engine dataset from the "
            "public Earth Engine STAC catalog (no auth needed). Call this BEFORE "
            "writing gee_add_layer code for a dataset, so the code uses the "
            "dataset's real, present-day band names, properties, date range, and "
            "status — not a memorized snapshot that may be deprecated. Returns "
            "band_names, bands (with gee:scale/offset and gsd), properties "
            "(per-image/feature schema), date_range, type (image / "
            "image_collection / table), and a `deprecated` flag. Example: "
            "gee_dataset_info('COPERNICUS/S2_SR_HARMONIZED'). For cloud masking, "
            "also look up the companion mask dataset, e.g. "
            "'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'. Also works for the "
            "user's OWN Earth Engine assets (e.g. "
            "'projects/<project>/assets/<name>' or 'users/<user>/<asset>'): when "
            "the id is not in the public catalog it is resolved via the "
            "authenticated Earth Engine API (result has source='asset'; requires "
            "gee_status to report authenticated)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": (
                        "Exact, case-sensitive Earth Engine dataset id, e.g. "
                        "'COPERNICUS/S2_SR_HARMONIZED', 'LANDSAT/LC09/C02/T1_L2', "
                        "'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED', or one of the "
                        "user's own assets such as "
                        "'projects/my-project/assets/my_image'."
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
            "Run an Earth Engine expression and add the result to the QGIS "
            "canvas via the ee_plugin. Only use after gee_status confirms GEE "
            "is ready AND the user has agreed (via ask_user) to run GEE "
            "operations. The `code` runs with `ee`, `Map` (ee_plugin), `iface`, "
            "`region` (an ee.Geometry built from region_layer_id in EPSG:4326 — "
            "the layer's TRUE geometry by default, not just its bounding box), "
            "and `features` (an ee.FeatureCollection of that layer's features, "
            "for per-feature work like zonal stats; None when unavailable) in "
            "scope. It MUST assign the final ee object (ee.Image / "
            "ee.ImageCollection mosaic / ee.FeatureCollection) to a variable "
            "named `result`. Example NDVI clipped to a QGIS layer: "
            "\"img = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')"
            ".filterBounds(region).filterDate('2023-01-01','2023-03-01').median(); "
            "result = img.normalizedDifference(['B8','B4']).clip(region)\". "
            "Pass vis_params for display, e.g. {'min':-0.2,'max':0.8,"
            "'palette':['blue','white','green']}. "
            "NOTE: vis_params 'scale' is silently ignored by the "
            "ee_plugin — use clipToBoundsAndScale or reduceResolution "
            "in the ee expression code itself to control resolution. "
            "IMPORTANT: when geometry_mode is 'auto' (default) and the layer is "
            "too large to send inline, this returns {ok:false, "
            "needs_decision:true} instead of running. In that case call ask_user "
            "with the offered options, then call gee_add_layer again with "
            "geometry_mode set to 'bbox', 'simplify', or 'exact'. "
            "For faster zoom/pan, set export_format='geotiff' — downloads the "
            "image as a local GeoTIFF and loads it as a local raster layer "
            "(instant zoom at the cost of upfront download time)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Earth Engine Python expression. Must assign the ee "
                        "object to `result`. `region` (ee.Geometry), `features` "
                        "(ee.FeatureCollection|None) and `ee` are in scope."
                    ),
                },
                "vis_params": {
                    "type": "object",
                    "description": (
                        "Earth Engine visualization params (min, max, palette, "
                        "bands, gamma). NOTE: scale here is silently ignored "
                        "by the ee_plugin — control resolution via "
                        "clipToBoundsAndScale/reduceResolution in the code."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the added GEE layer.",
                },
                "region_layer_id": {
                    "type": "string",
                    "description": (
                        "Optional QGIS layer id. Its features define `region` "
                        "and `features` (EPSG:4326) for filtering/clipping, and "
                        "it is the zoom target."
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
                        "How to convert region_layer_id. 'auto' (default): true "
                        "geometry, but returns needs_decision if too large. "
                        "'exact': true geometry (subject to hard ceilings). "
                        "'simplify': reduce vertices to fit. 'bbox': bounding "
                        "box only. Set this on the retry after asking the user."
                    ),
                    "default": "auto",
                },
                "max_vertices": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Vertex budget for inline geometry before 'auto' asks "
                        "the user (default 5000)."
                    ),
                    "default": 5000,
                },
                "max_features": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Feature-count budget before 'auto' asks the user "
                        "(default 2000)."
                    ),
                    "default": 2000,
                },
                "export_format": {
                    "type": "string",
                    "enum": ["map", "geotiff"],
                    "description": (
                        "'geotiff' (default): download and load as local raster "
                        "(instant zoom/pan). 'map': add as WMS tile layer via "
                        "ee_plugin (slower zoom, no download wait)."
                    ),
                    "default": "geotiff",
                },
                "export_scale": {
                    "type": "integer",
                    "minimum": 10,
                    "description": (
                        "Resolution in meters for GeoTIFF export when "
                        "export_format='geotiff'. Default 250. "
                        "Smaller = more detail but larger download."
                    ),
                    "default": 250,
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "remove_layer",
        "method": "remove_layer",
        "description": (
            "Unload one currently loaded layer from the QGIS project by layer_id "
            "or exact layer_name. This only removes the layer from the project; "
            "it never deletes the source file, database table, or remote data. "
            "Use only when the user explicitly asks to remove, clear, unload, "
            "or delete a loaded layer."
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
                    "description": (
                        "Exact layer name. If multiple loaded layers have this "
                        "name, the tool refuses and returns candidate layer IDs."
                    ),
                },
            },
        },
    },
    {
        "name": "clear_layers",
        "method": "clear_layers",
        "description": (
            "Unload all currently loaded layers from the QGIS project. This "
            "only clears the project layer list/canvas; it never deletes source "
            "files, database tables, or remote data. Use only when the user "
            "explicitly asks to clear or remove all loaded layers. Requires "
            "confirm=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true when the user explicitly asked to clear all loaded layers.",
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
            "Create a chart visualization from a vector layer's field values. "
            "Returns chart data that can be displayed as a bar, line, or pie chart. "
            "By default the chart COUNTS how many features fall into each distinct "
            "field_name value (a category frequency chart). To chart a NUMERIC "
            "MEASURE instead — e.g. total/average area per category — set value_field "
            "to the numeric field and aggregate to 'sum' (or 'mean'/'max'/'min'); "
            "field_name is then the category axis and bar height is the aggregated "
            "value. Do NOT chart an already-aggregated numeric field directly as "
            "field_name (that just counts each distinct number once, giving equal "
            "bars) — pass the category as field_name and the number as value_field. "
            "Use optional label_field for readable display labels when field_name "
            "contains codes/IDs and another field contains names or descriptions. "
            "Optionally supply a custom colors list (hex strings like '#5d8aa8') "
            "to use instead of the default palette — one color per data point "
            "in display order; the list cycles if shorter than the data. "
            "If colors is omitted, the chart UI uses its default A-to-B gradient."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "field_name": {"type": "string"},
                "label_field": {
                    "type": "string",
                    "description": (
                        "Optional field used only for readable display labels. "
                        "Use this for generic code/name or id/description field "
                        "pairs; no hardcoded field names are assumed. If invalid "
                        "or blank, labels fall back to field_name values."
                    ),
                },
                "value_field": {
                    "type": "string",
                    "description": (
                        "Optional numeric field to aggregate per category. When "
                        "set, bar height is the aggregated value of this field "
                        "grouped by field_name (instead of a feature count). Use "
                        "for measures like area, length, population, or a "
                        "pre-aggregated total. Omit to count occurrences."
                    ),
                },
                "aggregate": {
                    "type": "string",
                    "enum": ["count", "sum", "mean", "max", "min"],
                    "description": (
                        "How to reduce value_field per category. Defaults to "
                        "'count' when value_field is omitted, else 'sum'. "
                        "'mean'/'max'/'min' also require value_field."
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "description": "bar (default), line, or pie",
                },
                "colors": {
                    "type": "array",
                    "description": (
                        "Optional list of hex color strings (e.g. ['#5d8aa8', "
                        "'#c678dd', '#98c379']). Applied to the chart in "
                        "display order; cycles if shorter than the number "
                        "of data points. Useful for matching a project "
                        "palette, brand colors, or accessibility needs."
                    ),
                    "items": {
                        "type": "string",
                        "pattern": "^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$",
                    },
                },
            },
            "required": ["layer_id", "field_name"],
        },
    },
    {
        "name": "get_layer_statistics",
        "method": "get_layer_statistics",
        "description": (
            "Calculate statistics for a vector layer or a specific field. "
            "Returns count, min, max, mean, and distinct value counts."
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
            "Enable, adjust, or report QGIS's shared network disk cache, which "
            "caches WMS/WMTS/XYZ tile responses (including streaming GEE "
            "'ee_plugin' layers and web basemaps). Pass size_mb to set the "
            "maximum cache size (size_mb > 0 enables caching; 0 disables); omit "
            "it to just report current size, used space, and cache directory. "
            "Note: this is QGIS's single shared network cache, not WMS-only — "
            "it does not affect GEE 'geotiff' downloads, which save local files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "size_mb": {
                    "type": "number",
                    "description": (
                        "Maximum cache size in megabytes. > 0 enables/adjusts "
                        "the cache; 0 disables it. Omit to report current "
                        "settings without changing anything."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "web_fetch",
        "method": "web_fetch",
        "description": (
            "Fetch the content of a public URL (GET only). Returns the body, "
            "HTTP status, content-type, and parsed JSON when available. "
            "Use this to read API documentation, GeoJSON endpoints, or small data "
            "files accessible over HTTP/HTTPS. External URL access requires "
            "explicit user permission. If the remote server has an untrusted or "
            "incomplete SSL certificate chain, set verify_ssl=false to skip "
            "certificate verification."
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
                    "description": (
                        "Verify remote SSL certificate (default true). "
                        "Set false for incomplete/self-signed certificates."
                    ),
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
            "Pause and ask the user a clarifying question. Use this "
            "PROACTIVELY and OFTEN — it is your primary tool for "
            "resolving ambiguity instead of guessing. Call it whenever "
            "the user's request does not specify which fields to analyse, "
            "which layer to use, which CRS target, or any other detail "
            "you need. Also use it reactively when a tool result looks "
            "suspicious (no spatial index, empty result, schema mismatch, "
            "out-of-range value). Wait for the user's reply before "
            "continuing. Always provide 2-4 options (list the available "
            "choices as options) with the first one being the recommended "
            "choice. Returns a dict with 'choice' (the picked option's "
            "label, or null), 'free_text' (typed reply, or null), and "
            "'cancelled' (true if the user stopped the question)."
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
                            "description": {"type": "string", "description": "Optional helper text."},
                        },
                        "required": ["label"],
                    },
                    "description": "2-4 options. The first is the recommended choice.",
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


def anthropic_tool_list():
    """Tool definitions in Anthropic Messages API shape."""
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec["input_schema"],
        }
        for spec in TOOL_SPECS
    ]
