"""Small display formatters shared by the dock."""

import json
import re
from datetime import datetime, timezone

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QT_TRANSLATE_NOOP


def localize_decimal(value):
    """Apply Qt's decimal mark to a formatted UI value without changing precision."""
    return value.replace(".", QLocale().decimalPoint())


def format_elapsed(seconds):
    """``42`` → ``"42s"``, ``243`` → ``"4m 3s"``, ``3720`` → ``"1h 2m"``."""
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {s % 3600 // 60}m"


def format_relative_time(value, now=None):
    """ISO timestamp → ``"just now"``, ``"5m ago"``, ``"3h ago"``, ``"2d ago"``, or a date."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    delta = int((now - dt).total_seconds())
    if delta < 60:
        return QCoreApplication.translate("Formatting", "just now")
    if delta < 3600:
        return QCoreApplication.translate("Formatting", "%nm ago", None, delta // 60)
    if delta < 86400:
        return QCoreApplication.translate("Formatting", "%nh ago", None, delta // 3600)
    if delta < 7 * 86400:
        return QCoreApplication.translate("Formatting", "%nd ago", None, delta // 86400)
    return dt.strftime(QCoreApplication.translate("Formatting", "%Y-%m-%d"))


# ── Tool call rows ───────────────────────────────────────────────────────────

_ARG_KEYS = (
    "alg_id", "path", "file_path", "filename", "layer", "layer_name", "layer_id",
    "query", "sql", "expression", "url", "name", "id",
)


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


_TOOL_LABELS = {
    "run_pyqgis": QT_TRANSLATE_NOOP("ToolNames", "Run pyqgis"),
    "get_project_state": QT_TRANSLATE_NOOP("ToolNames", "Get project state"),
    "list_layers": QT_TRANSLATE_NOOP("ToolNames", "List layers"),
    "get_layer_fields": QT_TRANSLATE_NOOP("ToolNames", "Get layer fields"),
    "get_layer_summary": QT_TRANSLATE_NOOP("ToolNames", "Get layer summary"),
    "analyze_layer": QT_TRANSLATE_NOOP("ToolNames", "Analyze layer"),
    "list_plugins": QT_TRANSLATE_NOOP("ToolNames", "List plugins"),
    "list_processing_algorithms": QT_TRANSLATE_NOOP("ToolNames", "List processing algorithms"),
    "run_processing": QT_TRANSLATE_NOOP("ToolNames", "Run processing"),
    "add_layer": QT_TRANSLATE_NOOP("ToolNames", "Add layer"),
    "zoom_to_layer": QT_TRANSLATE_NOOP("ToolNames", "Zoom to layer"),
    "gee_status": QT_TRANSLATE_NOOP("ToolNames", "Gee status"),
    "gee_dataset_info": QT_TRANSLATE_NOOP("ToolNames", "Gee dataset info"),
    "gee_add_layer": QT_TRANSLATE_NOOP("ToolNames", "Gee add layer"),
    "gee_animation": QT_TRANSLATE_NOOP("ToolNames", "Gee animation"),
    "remove_layer": QT_TRANSLATE_NOOP("ToolNames", "Remove layer"),
    "clear_layers": QT_TRANSLATE_NOOP("ToolNames", "Clear layers"),
    "save_project": QT_TRANSLATE_NOOP("ToolNames", "Save project"),
    "create_chart": QT_TRANSLATE_NOOP("ToolNames", "Create chart"),
    "get_layer_statistics": QT_TRANSLATE_NOOP("ToolNames", "Get layer statistics"),
    "configure_network_cache": QT_TRANSLATE_NOOP("ToolNames", "Configure network cache"),
    "warm_cache": QT_TRANSLATE_NOOP("ToolNames", "Warm cache"),
    "web_fetch": QT_TRANSLATE_NOOP("ToolNames", "Web fetch"),
    "ask_user": QT_TRANSLATE_NOOP("ToolNames", "Ask user"),
    "set_layer_style": QT_TRANSLATE_NOOP("ToolNames", "Set layer style"),
    "select_by_attribute": QT_TRANSLATE_NOOP("ToolNames", "Select by attribute"),
    "select_by_expression": QT_TRANSLATE_NOOP("ToolNames", "Select by expression"),
    "select_within": QT_TRANSLATE_NOOP("ToolNames", "Select within"),
    "clear_selection": QT_TRANSLATE_NOOP("ToolNames", "Clear selection"),
    "invert_selection": QT_TRANSLATE_NOOP("ToolNames", "Invert selection"),
    "reproject_layer": QT_TRANSLATE_NOOP("ToolNames", "Reproject layer"),
    "set_project_crs": QT_TRANSLATE_NOOP("ToolNames", "Set project crs"),
    "analyze_raster": QT_TRANSLATE_NOOP("ToolNames", "Analyze raster"),
    "get_algorithm_parameters": QT_TRANSLATE_NOOP("ToolNames", "Get algorithm parameters"),
    "field_calculator": QT_TRANSLATE_NOOP("ToolNames", "Field calculator"),
    "save_project_as": QT_TRANSLATE_NOOP("ToolNames", "Save project as"),
    "create_layout": QT_TRANSLATE_NOOP("ToolNames", "Create layout"),
    "export_layout": QT_TRANSLATE_NOOP("ToolNames", "Export layout"),
}


def humanize_tool_name(name):
    """``"list_layers"`` → ``"List layers"``; MCP prefixes are dropped."""
    raw_name = str(name or "")
    if raw_name.startswith("mcp__agenticgis__"):
        raw_name = raw_name.split("__", 2)[-1]
    if raw_name in _TOOL_LABELS:
        return QCoreApplication.translate("ToolNames", _TOOL_LABELS[raw_name])
    label = raw_name.rsplit("__", 1)[-1].replace("_", " ").strip()
    return label[:1].upper() + label[1:] if label else QCoreApplication.translate("ToolNames", "Tool")


def summarize_tool_args(tool_input, limit=60):
    """The one argument that says what a call is about, or ``""``."""
    if not isinstance(tool_input, dict):
        return _clip(tool_input, limit) if tool_input else ""
    code = tool_input.get("code")
    if isinstance(code, str) and code.strip():
        return _clip(code.strip().splitlines()[0], limit)
    for key in _ARG_KEYS:
        if tool_input.get(key) not in (None, ""):
            return _clip(tool_input[key], limit)
    for value in tool_input.values():
        if isinstance(value, (str, int, float)) and value != "":
            return _clip(value, limit)
    return ""


def summarize_tool_result(result, is_error=False, limit=80):
    """One readable line for a tool result: an error, a message, or a count."""
    text = str(result or "").strip()
    failed = QCoreApplication.translate("Formatting", "Failed")
    if not text:
        return failed if is_error else ""
    try:
        data = json.loads(text)
    except ValueError:
        return _clip(text.splitlines()[0], limit)
    # MCP wraps results as {"content": [{"type": "text", "text": "..."}]}.
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        parts = [c.get("text", "") for c in data["content"] if isinstance(c, dict)]
        if parts and all(isinstance(p, str) for p in parts):
            return summarize_tool_result("\n".join(parts), is_error or bool(data.get("isError")), limit)
    if isinstance(data, list):
        count = len(data)
        if count == 1:
            return QCoreApplication.translate("Formatting", "1 item")
        return QCoreApplication.translate("Formatting", "%n items", None, count)
    if not isinstance(data, dict):
        return _clip(data, limit)
    if is_error or data.get("ok") is False:
        return _clip(data.get("error") or data.get("message") or failed, limit)
    for key in ("message", "summary", "status", "result", "output"):
        if isinstance(data.get(key), str) and data[key].strip():
            return _clip(data[key], limit)
    for key, value in data.items():
        if isinstance(value, list):
            label = key.replace("_", " ")
            if not value:
                return QCoreApplication.translate("Formatting", "No {label}").format(label=label)
            return QCoreApplication.translate("Formatting", "{count} {label}").format(
                count=len(value), label=label
            )
    return QCoreApplication.translate("Formatting", "Done")


# ── Markdown tables ────────────────────────────────────────────────────────

TABLE_RE = re.compile(r"(?m)(?:^\|[^\n]*\n){2,}(?:^\|[^\n]*)?")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_SEPARATOR_RE = re.compile(r"^[\s|:\-]+$")


def _clean_cell(cell):
    # Cells render as plain text, so drop common inline markdown markers.
    cell = re.sub(r"`([^`]+)`", r"\1", cell)
    cell = re.sub(r"\*\*(.+?)\*\*", r"\1", cell)
    return re.sub(r"\*(.+?)\*", r"\1", cell)


def md_table_rows(raw):
    """Markdown table source → list of cell lists (header first, separators dropped)."""
    rows = []
    for line in raw.strip().splitlines():
        if not line.strip() or _SEPARATOR_RE.match(line):
            continue
        cells = [c.strip() for c in line.split("|")]
        cells = [c for j, c in enumerate(cells) if c or 0 < j < len(cells) - 1]
        if cells:
            rows.append([_clean_cell(c) for c in cells])
    return rows


def split_md_tables(text):
    """Split markdown into ``("text", str)`` / ``("table", rows)`` segments.

    Tables inside ``` fences stay part of the surrounding text.
    """
    fences = [m.span() for m in _FENCE_RE.finditer(text)]
    out, pos = [], 0
    for m in TABLE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in fences):
            continue
        rows = md_table_rows(m.group(0))
        if len(rows) < 2:
            continue
        if text[pos:m.start()].strip():
            out.append(("text", text[pos:m.start()]))
        out.append(("table", rows))
        pos = m.end()
    if text[pos:].strip():
        out.append(("text", text[pos:]))
    return out
