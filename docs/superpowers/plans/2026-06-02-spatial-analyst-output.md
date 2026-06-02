# Spatial Analyst Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the agent from a QGIS operator into a spatial data analyst that produces tables, charts, and stat cards inline in the chat.

**Architecture:** Three parallel changes — rewrite system prompt in both backends to establish analyst persona and output-format guidance; add auto-VISUALIZATION event emission in both backends when create_chart/get_layer_statistics succeed; add markdown table rendering to _md_to_html.

**Tech Stack:** Python 3.9+, PyQt5 (qgis.PyQt), stdlib re + html, Anthropic/OpenAI backends

---

## File Map

| File | Change |
|---|---|
| `backends/api_backend.py` | Replace DEFAULT_SYSTEM_PROMPT; add auto-VISUALIZATION after tool dispatch |
| `backends/openai_backend.py` | Same prompt + same auto-VISUALIZATION after tool dispatch |
| `gui/message_bubble.py` | Add `_render_md_table` + table regex to `_md_to_html` |

---

### Task 1: Rewrite DEFAULT_SYSTEM_PROMPT and add auto-VISUALIZATION in api_backend.py

**Files:**
- Modify: `backends/api_backend.py`

- [ ] **Step 1: Replace DEFAULT_SYSTEM_PROMPT**

Find this block (lines 18–35):
```python
DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a GIS assistant embedded in a running QGIS session. \
Operate QGIS by calling tools. Be direct — act first, explain briefly after.

Tools:
- run_pyqgis: use for almost everything. Full QGIS + plugin access. Call it \
directly without preamble.
- get_project_state / list_layers: call only when you genuinely need layer \
IDs or project context. Do NOT call on every turn.
- run_processing: use for standard algorithms (buffer, clip, dissolve, etc.).

Rules:
- Auto-run is ON — code executes immediately. Never delete files or layers \
unless explicitly asked.
- Always reference layers by id, not name.
- Do not explain what you are about to do. Just do it.
- After acting: one short sentence on what changed. No narration.
- For questions that need no tools: answer directly without calling anything."""
```

Replace with:
```python
DEFAULT_SYSTEM_PROMPT = """\
You are AgenticGIS, a spatial data analyst embedded in a live QGIS session. \
Analyse, compute, interpret, and explain — not just execute.

Output format — choose based on the question:
- Attribute data / feature comparison → write a markdown table in your reply: \
| Field | Value |
- Field distribution or category count → call create_chart (renders a chart inline)
- Numeric field stats (min/max/mean/count) → call get_layer_statistics \
(renders a stat card inline)
- Custom spatial analysis → use run_pyqgis; assign result = {{...}} or print \
a formatted table

Tools:
- run_pyqgis: primary tool — full QGIS + plugin access. Call directly, no preamble.
- create_chart(layer_id, field_name, chart_type): bar/line/pie chart rendered inline
- get_layer_statistics(layer_id, field_name): stat card rendered inline
- get_layer_fields / get_layer_summary: inspect layer schema before analysis
- get_project_state / list_layers: only when you need layer IDs. \
Do NOT call on every turn.
- run_processing: standard algorithms (buffer, clip, dissolve, etc.)
- add_layer / save_project: when asked to load or save

Rules:
- Auto-run is ON — code executes immediately. Never delete files or layers \
unless explicitly asked.
- Always reference layers by id, not name.
- Do not explain what you are about to do. Act, then explain the result briefly.
- After analysis: summarise the key insight in 1–2 sentences.
- For questions needing no tools: answer directly."""
```

- [ ] **Step 2: Add auto-VISUALIZATION emission after tool dispatch**

In `api_backend.py`, find the tool dispatch loop. Locate this block (inside the `for tu in tool_uses:` loop, after `emit(AgentEvent(EventType.TOOL_RESULT, ...))`):

```python
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": tu["name"], "result": payload}))
                tool_results.append({
```

Replace with:
```python
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": tu["name"], "result": payload}))
                # Auto-render chart and stats tool results as inline widgets
                if tu["name"] == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif tu["name"] == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
                tool_results.append({
```

Note: the variable `result` holds the raw Python dict from `tools_mod.dispatch` — it is defined earlier in the same loop as `result = tools_mod.dispatch(...)`. The `payload` variable is the JSON-serialised string used for the API. Use `result` (not `payload`) for the isinstance check.

- [ ] **Step 3: Verify the except branch still sets is_error correctly**

After your edit, confirm the except block still comes before `tool_results.append`. The full loop body should read:
```python
            for tu in tool_uses:
                emit(AgentEvent(EventType.TOOL_USE, ...))
                try:
                    result = tools_mod.dispatch(...)
                    payload = json.dumps(result, default=str)
                    if len(payload) > 200_000:
                        payload = payload[:200_000] + "\n... [output truncated]"
                    is_error = isinstance(result, dict) and result.get("ok") is False
                except Exception as exc:
                    payload = f"Tool error: ..."
                    is_error = True
                emit(AgentEvent(EventType.TOOL_RESULT, ...))
                # Auto-render chart / stats
                if tu["name"] == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif tu["name"] == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
                tool_results.append({...})
```

The auto-viz block is after the TOOL_RESULT emit and before `tool_results.append` — this is intentional so the chart/stat card appears immediately below the tool result widget.

---

### Task 2: Same changes in openai_backend.py

**Files:**
- Modify: `backends/openai_backend.py`

- [ ] **Step 1: Replace DEFAULT_SYSTEM_PROMPT**

Find the exact same old prompt block as in Task 1 (lines 15–32 in openai_backend.py) and replace with the identical new DEFAULT_SYSTEM_PROMPT from Task 1 Step 1. The two backends must stay in sync.

- [ ] **Step 2: Add auto-VISUALIZATION emission**

In `openai_backend.py`, find the tool dispatch loop (`for tc in tool_calls:`). Locate:

```python
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": name, "result": payload}))
                messages.append(
                    OpenAIHttpClient.build_tool_result_message(tc["id"], payload)
                )
```

Replace with:
```python
                emit(AgentEvent(EventType.TOOL_RESULT,
                                {"name": name, "result": payload}))
                # Auto-render chart and stats tool results as inline widgets
                if name == "create_chart" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "chart", "data": result}))
                elif name == "get_layer_statistics" and isinstance(result, dict) and result.get("ok"):
                    emit(AgentEvent(EventType.VISUALIZATION, {"type": "stats", "data": result}))
                messages.append(
                    OpenAIHttpClient.build_tool_result_message(tc["id"], payload)
                )
```

Note: in openai_backend.py the variable holding the raw result is `result` (set by `result = tools_mod.dispatch(...)`). The variable `payload` is the JSON string. Use `result` for the isinstance check.

---

### Task 3: Add markdown table rendering to _md_to_html in message_bubble.py

**Files:**
- Modify: `gui/message_bubble.py`

- [ ] **Step 1: Add _render_md_table helper before _md_to_html**

Open `gui/message_bubble.py`. Find the line `def _md_to_html(text: str) -> str:` (around line 37). Immediately before that line, insert this helper function:

```python
def _render_md_table(match: "re.Match") -> str:
    """Convert a matched markdown table block to a styled HTML table."""
    raw = match.group(0)
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    # Detect and skip separator lines: lines where all non-pipe chars are - : space
    data_lines = [l for l in lines if not re.match(r'^[\s|:\-]+$', l)]
    if len(data_lines) < 1:
        return raw

    th_style = (
        f"padding:6px 10px; text-align:left; color:{_TEXT}; font-weight:600; "
        f"border-bottom:1px solid {_BORDER}; background:{_SURFACE}; white-space:nowrap;"
    )
    td_style = (
        f"padding:5px 10px; text-align:left; color:{_TEXT_2}; "
        f"border-bottom:1px solid {_BORDER_SOFT};"
    )
    table_style = (
        f"border-collapse:collapse; width:100%; margin:6px 0; "
        f"font-size:12px; font-family:'Consolas','Courier New',monospace; "
        f"background:{_INPUT_BG}; border:1px solid {_BORDER}; border-radius:6px;"
    )

    rows_html = []
    for i, line in enumerate(data_lines):
        # Split on | and strip; skip empty cells from leading/trailing |
        cells = [c.strip() for c in line.split("|")]
        cells = [c for j, c in enumerate(cells) if c or (0 < j < len(cells) - 1)]
        if not cells:
            continue
        if i == 0:
            cells_html = "".join(f"<th style='{th_style}'>{c}</th>" for c in cells)
        else:
            cells_html = "".join(f"<td style='{td_style}'>{c}</td>" for c in cells)
        rows_html.append(f"<tr>{cells_html}</tr>")

    if not rows_html:
        return raw
    return f'<table style="{table_style}">{"".join(rows_html)}</table>'

```

- [ ] **Step 2: Insert table regex into _md_to_html**

Inside `_md_to_html`, find the section after code blocks are extracted and headings/lists are applied, but **before** the `safe = safe.replace("\n", "<br>")` line. The line before `<br>` conversion currently reads:

```python
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    safe = safe.replace("\n", "<br>")
```

Replace with:
```python
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    # Markdown tables — must be applied before \n → <br> conversion
    safe = re.sub(
        r"(?m)(?:^\|.+\|\s*\n)+(?:^\|.+\|[ \t]*$)?",
        _render_md_table,
        safe,
    )

    safe = safe.replace("\n", "<br>")
```

- [ ] **Step 3: Verify table renders correctly**

The regex `(?m)(?:^\|.+\|\s*\n)+(?:^\|.+\|[ \t]*$)?` matches:
- One or more lines starting and ending with `|` (followed by newline)
- Optionally a final line starting and ending with `|` (no trailing newline)

This handles tables both mid-text and at end of message.

Test mentally: given input
```
| Name | Count |
|------|-------|
| A    | 10    |
| B    | 25    |
```
Expected HTML: `<table ...><tr><th>Name</th><th>Count</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>25</td></tr></table>`

The separator line (`|------|-------|`) matches `r'^[\s|:\-]+$'` → filtered out. ✓

---

### Task 4: Commit all changes

- [ ] **Step 1: Stage and commit**

```bash
git add backends/api_backend.py backends/openai_backend.py gui/message_bubble.py
git commit -m "feat: spatial analyst persona — tables, auto-charts, stat cards inline

- System prompt: analyst identity with output-format guidance (table/chart/stats)
- api_backend + openai_backend: auto-emit VISUALIZATION events for create_chart
  and get_layer_statistics so charts and stat cards appear inline automatically
- message_bubble._md_to_html: markdown table rendering with dark-palette styling

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
