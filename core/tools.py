"""Single declarative definition of the agent tool surface.

Both the in-process API backend and the MCP bridge build their tool lists from
``TOOL_SPECS`` so the two connection modes expose identical capabilities. Each
spec maps a tool name to a JSON Schema and the ``QgisToolkit`` method that
implements it.
"""

TOOL_SPECS = [
    {
        "name": "run_pyqgis",
        "method": "run_pyqgis",
        "description": (
            "Execute arbitrary PyQGIS Python code inside the running QGIS "
            "instance. This is the primary tool: it can reach EVERY QGIS "
            "feature and EVERY installed plugin. Pre-bound names: iface, "
            "QgsProject, QgsApplication, processing, QgsFeatureRequest, "
            "_iterate_features, and all qgis.core names. "
            "Assign to a variable named `result` to return a value. stdout and "
            "stderr are captured and returned."
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
            "'OUTPUT': 'memory:'}."
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
        "description": "Load a layer from a file path / URI and add it to the project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
                "name": {"type": "string"},
                "provider": {
                    "type": "string",
                    "description": "ogr (vector, default), gdal (raster), postgres, etc.",
                },
            },
            "required": ["uri"],
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
            "Returns chart data that can be displayed as a bar, line, or pie chart."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "field_name": {"type": "string"},
                "chart_type": {
                    "type": "string",
                    "description": "bar (default), line, or pie",
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
        "name": "ask_user",
        "method": "ask_user",
        "description": (
            "Pause and ask the user a clarifying question. Use proactively "
            "when the request is ambiguous (e.g. no analysis field named, "
            "no CRS target, no comparison layer) and reactively when a "
            "tool result looks suspicious (no spatial index, empty result, "
            "schema mismatch, out-of-range value). Wait for the user's "
            "reply before continuing. Always provide 2-4 options with the "
            "first one being the recommended choice. Returns a dict with "
            "'choice' (the picked option's label, or null), 'free_text' "
            "(typed reply, or null), and 'cancelled' (true if the user "
            "stopped the question)."
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


def dispatch(toolkit, executor, name, arguments):
    """Run tool ``name`` with ``arguments`` against ``toolkit`` on the main
    thread (via ``executor``) and return its result. Raises ``KeyError`` for
    an unknown tool name."""
    spec = TOOL_BY_NAME[name]
    method = getattr(toolkit, spec["method"])
    args = dict(arguments or {})
    return executor.run_sync(lambda: method(**args))


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
