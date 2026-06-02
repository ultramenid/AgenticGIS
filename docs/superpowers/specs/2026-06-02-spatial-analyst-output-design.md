# Spatial Analyst Output — Design Spec
**Date:** 2026-06-02

## Problem
Agent behaves as a QGIS operator (executes commands, says "done"). User wants spatial analyst behaviour: compute, interpret, and present results as tables, charts, or stat cards depending on the question.

## Three Gaps

1. **System prompt** — no analyst identity, no guidance on output format selection
2. **Auto-visualization** — `create_chart` / `get_layer_statistics` tools exist and return rich data but backends never emit `VISUALIZATION` events, so charts/stat cards never appear
3. **Markdown tables** — `_md_to_html` has no table parser; `| col | val |` renders as raw pipe characters

## Design: Approach C

### 1. New system prompt (both backends)

Spatial analyst persona with explicit output-format guidance:
- Attribute tables → markdown `| col | val |` in reply text
- Distributions/comparisons → `create_chart` (visual chart inline)
- Min/max/mean/count → `get_layer_statistics` (stat card inline)
- Custom analysis → `run_pyqgis` with `print()` or `result = {...}`
- Keep: act-first, no preamble, brief post-action summary

### 2. Auto-VISUALIZATION emission (api_backend.py + openai_backend.py)

After each tool dispatch, if result is success:
```python
if name == "create_chart" and isinstance(result, dict) and result.get("ok"):
    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
elif name == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
```

### 3. Markdown table rendering (message_bubble.py)

Added to `_md_to_html`, after code-block extraction, before `<br>` conversion:

Regex captures consecutive `| ... |` lines as a table block.
Separator lines (`|---|---|`) are detected and skipped.
Renders as a styled `<table>` with dark palette:
- Header row: `_TEXT` color, bold, `_BORDER` bottom border, `_SURFACE` background
- Data rows: `_TEXT_2` color, `_BORDER_SOFT` row dividers
- Full-width, monospace, 12px font

## Files Changed

| File | Change |
|---|---|
| `backends/api_backend.py` | New DEFAULT_SYSTEM_PROMPT + auto-VISUALIZATION emit |
| `backends/openai_backend.py` | Same new prompt + auto-VISUALIZATION emit |
| `gui/message_bubble.py` | `_md_to_html`: add `_render_md_table` helper + table regex |
