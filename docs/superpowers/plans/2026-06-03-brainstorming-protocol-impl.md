# Brainstorming Protocol + ask_user Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent a structured way to ask the user a clarifying question and wait for an answer, both proactively (ambiguous request) and reactively (suspicious tool result). The user answers via clickable options or a free-text field rendered as a popover above the chat input.

**Architecture:** Add one new tool `ask_user` to the existing `TOOL_SPECS` registry in `core/tools.py` (auto-surfaces to Anthropic + OpenAI + MCP). Implement the method on `QgisToolkit`, where it emits a new `ASK_USER` event and blocks on a `threading.Event` wired into the existing cancellation registry. The dock renders the event as a popover above the input bar; the dock's reply sets the event payload. A new "Brainstorming Protocol" section is added to both `DEFAULT_SYSTEM_PROMPT` copies with a one-line rule and a 5-item trigger list.

**Tech Stack:** Python 3, PyQt5 (via `qgis.PyQt.QtCore/QtWidgets`), QGIS API mocks for unit tests, stdlib `threading` + existing `MainThreadExecutor` and `_CancellationRegistry`.

---

## File map

| File | Change | Responsibility |
|------|--------|----------------|
| `core/tools.py` | modify | Add `ask_user` to `TOOL_SPECS` |
| `core/toolkit.py` | modify | Add `ask_user()` method, `set_ask_user_emitter()` setter, internal wait registry |
| `backends/base.py` | modify | Add `EventType.ASK_USER` |
| `gui/ask_user_card.py` | create | New `AskUserCard` widget (popover with options + free-text) |
| `gui/chat_dock.py` | modify | Wire `_show_ask_user` / `_resolve_ask_user`, add popover, expose toolkit callback |
| `backends/api_backend.py` | modify | Append "Brainstorming Protocol" section to `DEFAULT_SYSTEM_PROMPT` |
| `backends/openai_backend.py` | modify | Append "Brainstorming Protocol" section to `DEFAULT_SYSTEM_PROMPT` |
| `plugin.py` | modify | Pass toolkit reference into dock so dock can set the emitter |
| `tests/test_tools.py` | modify | Assert `ask_user` is registered and exported |
| `tests/test_ask_user.py` | create | Unit tests for toolkit `ask_user()` |

---

## Task 1: Add EventType.ASK_USER

**Files:**
- Modify: `backends/base.py:13-20`

- [ ] **Step 1: Add the constant**

Open `backends/base.py`. The `EventType` class currently has constants in alphabetical order with `ERROR` last. Insert the new line after `VISUALIZATION` (line 19) and before `ERROR` (line 20):

```python
    ASK_USER = "ask_user"    # agent asks the user      (data: {"question", "options", "allow_free_text"})
```

The full block becomes:

```python
class EventType:
    TEXT = "text"            # assistant text delta (data: {"text": str})
    THINKING = "thinking"    # status / reasoning note  (data: {"text": str})
    TOOL_USE = "tool_use"    # agent invoked a tool      (data: {"name", "input"})
    TOOL_RESULT = "tool_result"  # tool returned          (data: {"name", "result"})
    DONE = "done"            # turn finished
    VISUALIZATION = "visualization"  # data visualization (charts, stats) (data: {"type": str, "data": dict})
    ASK_USER = "ask_user"    # agent asks the user      (data: {"question", "options", "allow_free_text"})
    ERROR = "error"          # something failed          (data: {"error": str})
```

- [ ] **Step 2: Run existing tests to confirm no regression**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -q`

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add backends/base.py
git commit -m "feat: add EventType.ASK_USER for clarifying-question flow"
```

---

## Task 2: Add `ask_user` tool spec

**Files:**
- Modify: `core/tools.py:178` (end of `TOOL_SPECS` list)
- Modify: `tests/test_tools.py` (add a test)

- [ ] **Step 1: Add the spec to TOOL_SPECS**

Open `core/tools.py`. Find the `]` that closes the `TOOL_SPECS` list (line 178, right above `TOOL_BY_NAME = ...`). Insert a new entry before it:

```python
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
            "first one being the recommended choice."
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
```

- [ ] **Step 2: Add a test for the new spec**

Open `tests/test_tools.py`. Append at the end (the file currently ends with `test_list_layers_pagination_schema` on line 29):

```python
def test_ask_user_spec_registered():
    """The ask_user tool must be in TOOL_BY_NAME and the Anthropic export."""
    from core import tools
    assert "ask_user" in tools.TOOL_BY_NAME
    spec = tools.TOOL_BY_NAME["ask_user"]
    assert spec["method"] == "ask_user"
    assert "question" in spec["input_schema"]["properties"]
    assert "options" in spec["input_schema"]["properties"]
    assert spec["input_schema"]["required"] == ["question", "options"]
    # Must be exported to the Anthropic shape too
    anthropic_list = tools.anthropic_tool_list()
    assert any(t["name"] == "ask_user" for t in anthropic_list)
```

- [ ] **Step 3: Run the new test**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/test_tools.py -v`

Expected: 3 tests pass (the 2 pre-existing + the new one).

- [ ] **Step 4: Commit**

```bash
git add core/tools.py tests/test_tools.py
git commit -m "feat: register ask_user tool spec"
```

---

## Task 3: Add `ask_user` method to QgisToolkit

**Files:**
- Create: `tests/test_ask_user.py`
- Modify: `core/toolkit.py` (add method, helper, and `__init__` initialisers)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ask_user.py` with this content (mirrors the mock pattern in `tests/test_reliability_fixes.py:13-153`):

```python
"""Tests for the toolkit.ask_user() method (clarifying-question flow)."""
import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- Minimal QGIS mock ---------------------------------------------------- #
class _MockQtCore:
    class QObject:
        def __init__(self, *a, **k):
            pass

    class QThread:
        @staticmethod
        def currentThread():
            class _T:
                def __eq__(self, o):
                    return True

                def __hash__(self):
                    return 1

            return _T()


_PyQt = SimpleNamespace()
sys.modules.setdefault("qgis", SimpleNamespace(PyQt=_PyQt))
sys.modules.setdefault("qgis.PyQt", _PyQt)
sys.modules.setdefault("qgis.PyQt.QtCore", _MockQtCore)


def _qgis_class_factory(name):
    def _factory(*a, **k):
        return SimpleNamespace()
    _factory.__name__ = name
    return _factory


_qgis_core_attrs = [
    "Qgis", "QgsApplication", "QgsFeatureRequest", "QgsMapLayer",
    "QgsProject", "QgsVectorLayer", "QgsVectorLayerCache", "QgsFeedback",
]
_qgis_core = SimpleNamespace(**{n: _qgis_class_factory(n) for n in _qgis_core_attrs})
sys.modules["qgis.core"] = _qgis_core
sys.modules["qgis.gui"] = SimpleNamespace(
    QgsDockWidget=type("QgsDockWidget", (), {})
)


def _load_toolkit_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "core.toolkit",
        os.path.join(os.path.dirname(__file__), "..", "core", "toolkit.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TK = _load_toolkit_module()


def _fresh_toolkit():
    """Build a bare QgisToolkit with no iface/config (we only test ask_user)."""
    t = _TK.QgisToolkit.__new__(_TK.QgisToolkit)
    t.iface = SimpleNamespace()
    t.config = None
    t._ns_template = None
    t._canvas_dirty = False
    t._alg_cache = None
    t._cancel = _TK._CancellationRegistry()
    t._ask_emitter = None
    t._ask_user_lock = threading.Lock()
    t._ask_user_pending = None
    return t


def test_ask_user_returns_choice():
    """ask_user blocks until the emitter fires the event with a payload."""
    tk = _fresh_toolkit()
    captured = {}

    def emitter(question, options, allow_free_text):
        captured["question"] = question
        captured["options"] = options
        captured["allow_free_text"] = allow_free_text
        # Fire the reply from a tiny delay so the kit side is actually blocking.
        def _reply():
            time.sleep(0.02)
            tk._resolve_ask_user({"choice": "yes", "free_text": None})

        threading.Thread(target=_reply, daemon=True).start()

    tk.set_ask_user_emitter(emitter)
    result = tk.ask_user(
        "Proceed?",
        [{"label": "yes", "description": "recommended"},
         {"label": "no"}],
        allow_free_text=True,
    )
    assert captured["question"] == "Proceed?"
    assert len(captured["options"]) == 2
    assert captured["allow_free_text"] is True
    assert result == {"choice": "yes", "free_text": None, "cancelled": False}


def test_ask_user_returns_free_text():
    """If the user types (free_text only, no choice), both are passed through."""
    tk = _fresh_toolkit()

    def emitter(question, options, allow_free_text):
        tk._resolve_ask_user({"choice": None, "free_text": "do it differently"})

    tk.set_ask_user_emitter(emitter)
    result = tk.ask_user("How?", [{"label": "A"}, {"label": "B"}])
    assert result["free_text"] == "do it differently"
    assert result["choice"] is None


def test_ask_user_rejects_too_few_options():
    """< 2 options is a tool error, not a question."""
    tk = _fresh_toolkit()
    called = []
    tk.set_ask_user_emitter(lambda *a, **k: called.append(a))
    result = tk.ask_user("Pick one", [{"label": "only"}])
    assert "2-4" in str(result) or "options" in str(result).lower()
    assert called == []  # emitter was never called


def test_ask_user_rejects_too_many_options():
    """> 4 options is a tool error, not a question."""
    tk = _fresh_toolkit()
    tk.set_ask_user_emitter(lambda *a, **k: None)
    result = tk.ask_user(
        "Pick one",
        [{"label": f"o{i}"} for i in range(5)],
    )
    assert "2-4" in str(result) or "options" in str(result).lower()


def test_ask_user_recursive_guard():
    """A second concurrent ask_user returns a tool error (not a hang)."""
    tk = _fresh_toolkit()
    tk.set_ask_user_emitter(lambda *a, **k: None)  # never replies
    # Simulate "already waiting" by manually setting the pending slot.
    tk._ask_user_pending = ("fake", "fake")
    result = tk.ask_user("Q?", [{"label": "a"}, {"label": "b"}])
    assert "already waiting" in str(result).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/test_ask_user.py -v`

Expected: all 5 tests fail with `AttributeError` (toolkit has no `ask_user` / `set_ask_user_emitter` / `_resolve_ask_user`).

- [ ] **Step 3: Add the toolkit implementation**

Open `core/toolkit.py`. `threading` is already imported (line 19). Insert the following block just before the "Cancellation helpers" section, i.e. immediately after the `is_cancelled` method (which currently ends at line 131). The insertion point is right after the comment line `# Cancellation helpers` header — keep the header line; insert directly above it.

```python
    # ------------------------------------------------------------------ #
    # Clarifying-question flow (ask_user tool)                            #
    # ------------------------------------------------------------------ #
    def set_ask_user_emitter(self, emitter):
        """Register a callback that asks the user a clarifying question.

        The emitter signature is ``emitter(question, options, allow_free_text)``.
        It is expected to be non-blocking; it fires ``self._resolve_ask_user(payload)``
        from the main thread when the user replies (or the dock is cleared).
        """
        self._ask_emitter = emitter

    def ask_user(self, question, options, allow_free_text=True):
        """Toolkit implementation of the ``ask_user`` tool.

        Returns a dict ``{"choice": str|None, "free_text": str|None, "cancelled": bool}``.
        Returns a plain string error for bad inputs so the agent loop can surface it.
        """
        # Validate options
        if not isinstance(options, (list, tuple)):
            return "ask_user: options must be a list of objects with a 'label' field"
        if len(options) < 2 or len(options) > 4:
            return f"ask_user: options must have 2-4 items, got {len(options)}"
        for i, opt in enumerate(options):
            if not isinstance(opt, dict) or not opt.get("label"):
                return f"ask_user: options[{i}] must be an object with a non-empty 'label'"

        if not isinstance(question, str) or not question.strip():
            return "ask_user: question must be a non-empty string"

        # Recursive guard: only one ask_user at a time
        with self._ask_user_lock:
            if self._ask_user_pending is not None:
                return "ask_user: already waiting for user input"

            wait = threading.Event()
            self._ask_user_pending = (wait, {"choice": None, "free_text": None, "cancelled": False})

        try:
            if self._ask_emitter is not None:
                self._ask_emitter(question, list(options), bool(allow_free_text))

            # Wait for the dock (or a test) to fire _resolve_ask_user.
            wait_evt, _ = self._ask_user_pending
            wait_evt.wait()
        finally:
            with self._ask_user_lock:
                payload = (
                    self._ask_user_pending[1]
                    if self._ask_user_pending is not None
                    else {"choice": None, "free_text": None, "cancelled": True}
                )
                self._ask_user_pending = None
        return payload

    def _resolve_ask_user(self, payload):
        """Called by the dock (or a test) to unblock ask_user.

        ``payload`` is a dict with keys ``choice``, ``free_text``, ``cancelled``.
        Missing keys default to None / False.
        """
        with self._ask_user_lock:
            if self._ask_user_pending is None:
                return  # nothing to resolve (stale fire, e.g. after Clear)
            wait_evt, slot = self._ask_user_pending
            slot["choice"] = payload.get("choice")
            slot["free_text"] = payload.get("free_text")
            slot["cancelled"] = bool(payload.get("cancelled", False))
            wait_evt.set()

    # ------------------------------------------------------------------ #
    # Cancellation helpers                                                #
    # ------------------------------------------------------------------ #
```

(The trailing `# Cancellation helpers` header is preserved so the existing code below it stays in place.)

- [ ] **Step 4: Initialise the new instance attributes in `__init__`**

Find `__init__` in `core/toolkit.py`. Add three attribute initialisations. Place them right after the existing `self._cancel = _CancellationRegistry()` line. The result should look like:

```python
        self._cancel = _CancellationRegistry()
        self._ask_emitter = None
        self._ask_user_lock = threading.Lock()
        self._ask_user_pending = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/test_ask_user.py -v`

Expected: all 5 tests pass.

- [ ] **Step 6: Run the full test suite to confirm no regression**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add core/toolkit.py tests/test_ask_user.py
git commit -m "feat: toolkit.ask_user() blocks for user reply with cancel guard"
```

---

## Task 4: Create AskUserCard widget

**Files:**
- Create: `gui/ask_user_card.py`

- [ ] **Step 1: Create the widget**

Create `gui/ask_user_card.py` with this content:

```python
"""Popover card that lets the user answer a clarifying question from the agent.

The dock inserts one of these above the input bar when an ``ASK_USER`` event
arrives. Clicking an option button (or submitting the free-text field) emits
the ``submitted`` signal with ``{"choice": str|None, "free_text": str|None}``.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Reuse the dock's design tokens for visual consistency.
_SURFACE = "#131316"
_INPUT_BG = "#1c1c20"
_BORDER = "#27272a"
_BORDER_SOFT = "#1f1f23"
_TEXT = "#fafafa"
_TEXT_2 = "#a1a1aa"
_TEXT_3 = "#71717a"
_ACCENT = "#fafafa"
_ACCENT_HOV = "#e4e4e7"


class AskUserCard(QFrame):
    submitted = pyqtSignal(object)  # emits dict {choice, free_text}

    def __init__(self, question, options, allow_free_text=True, parent=None):
        super().__init__(parent)
        self.setObjectName("AskUserCard")
        self.setStyleSheet(f"""
            QFrame#AskUserCard {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        self._options = list(options)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        header = QLabel("Agent needs input")
        header.setStyleSheet(
            f"color:{_TEXT_3}; font-size:10px; letter-spacing:0.06em; "
            f"text-transform:uppercase; background:transparent; border:none;"
        )
        outer.addWidget(header)

        q = QLabel(question)
        q.setWordWrap(True)
        q.setStyleSheet(
            f"color:{_TEXT}; font-size:13px; font-weight:500; "
            f"background:transparent; border:none;"
        )
        outer.addWidget(q)

        # Options row
        opt_row = QHBoxLayout()
        opt_row.setContentsMargins(0, 0, 0, 0)
        opt_row.setSpacing(6)
        for opt in self._options:
            btn = QPushButton(opt.get("label", "?"))
            tip = opt.get("description", "")
            if tip:
                btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_SURFACE};
                    color: {_TEXT};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-size: 12px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {_BORDER_SOFT};
                    border-color: {_TEXT_3};
                }}
                QPushButton:pressed {{
                    background-color: {_ACCENT};
                    color: {_SURFACE};
                }}
            """)
            label = opt.get("label", "")
            btn.clicked.connect(lambda _checked=False, lbl=label: self._on_option(lbl))
            opt_row.addWidget(btn)
        opt_row.addStretch(1)
        outer.addLayout(opt_row)

        # Free-text fallback
        if allow_free_text:
            ft_row = QHBoxLayout()
            ft_row.setContentsMargins(0, 0, 0, 0)
            ft_row.setSpacing(6)
            self._free_text = QLineEdit()
            self._free_text.setPlaceholderText("Or type your own answer…")
            self._free_text.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {_SURFACE};
                    color: {_TEXT};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    padding: 6px 10px;
                    font-size: 12px;
                    selection-background-color: {_TEXT};
                    selection-color: {_SURFACE};
                }}
            """)
            self._free_text.returnPressed.connect(self._on_free_text)
            ft_row.addWidget(self._free_text, 1)

            send = QPushButton("Send")
            send.setCursor(Qt.PointingHandCursor)
            send.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_ACCENT};
                    color: {_SURFACE};
                    border: none;
                    border-radius: 8px;
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {_ACCENT_HOV}; }}
                QPushButton:disabled {{ background-color: {_BORDER}; color: {_TEXT_3}; }}
            """)
            send.clicked.connect(self._on_free_text)
            ft_row.addWidget(send)
            outer.addLayout(ft_row)
        else:
            self._free_text = None

    def _on_option(self, label):
        self.submitted.emit({"choice": label, "free_text": None})

    def _on_free_text(self):
        if self._free_text is None:
            return
        text = self._free_text.text().strip()
        if not text:
            return
        self.submitted.emit({"choice": None, "free_text": text})
```

- [ ] **Step 2: Smoke-check the import**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -c "from gui.ask_user_card import AskUserCard; print('ok')"`

Expected: `ok` (the import will fail outside a QGIS Python environment, which is fine — the test below exercises the dock wiring in Task 5; if the import fails because PyQt is unavailable, that's a real error and you should investigate before continuing).

- [ ] **Step 3: Commit**

```bash
git add gui/ask_user_card.py
git commit -m "feat: AskUserCard popover widget for clarifying questions"
```

---

## Task 5: Wire the dock to the toolkit's ask_user emitter

**Files:**
- Modify: `gui/chat_dock.py` (constructor, `_on_event`, `_clear`, new methods)

- [ ] **Step 1: Add AskUserCard import**

Open `gui/chat_dock.py`. Add this import after the existing `from .typing_indicator import TypingIndicator` line (line 29):

```python
from .ask_user_card import AskUserCard
```

- [ ] **Step 2: Extend the dock constructor**

In `__init__` (starts line 73), add two parameters — `toolkit` — and add three instance attributes. Replace the existing `__init__` body from `self._get_backend = get_backend` down to `self._build_ui()` with the version below. The constructor signature changes from:

```python
def __init__(self, get_backend, open_settings, request_cancel, parent=None):
```

to:

```python
def __init__(self, get_backend, open_settings, request_cancel, toolkit=None, parent=None):
```

And the body becomes (additions marked with `# +` comments — apply the diff in place, then strip the `# +` markers):

```python
        self._get_backend = get_backend
        self._open_settings = open_settings
        self._request_cancel = request_cancel
        self._toolkit = toolkit                              # +
        self._history = []
        self._worker = None
        self._streaming = False
        self._pending_tool = None
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._scroll_locked = False
        self._programmatic_scroll = False
        self._ask_user_card = None                           # +
        self._ask_user_payload = None                       # +
        self._build_ui()
        if self._toolkit is not None:                        # +
            self._toolkit.set_ask_user_emitter(             # +
                self._ask_user_emitter                       # +
            )                                                # +
```

The new `self._ask_user_emitter` method is a thin wrapper that just calls `_show_ask_user` on the main thread. Add it to the dock class, right after `_clear()` (around line 410):

```python
    def _ask_user_emitter(self, question, options, allow_free_text):
        """Called by the toolkit on the main thread to surface a question."""
        self._show_ask_user(question, options, allow_free_text)
```

- [ ] **Step 3: Add `_show_ask_user` and `_resolve_ask_user` methods**

Add the two new methods, immediately after `_ask_user_emitter`:

```python
    def _show_ask_user(self, question, options, allow_free_text):
        """Build and show the AskUserCard popover; record the pending payload."""
        if self._ask_user_card is not None:
            return  # already showing — caller (toolkit guard) should prevent this
        self._hide_typing()
        self._ask_user_payload = None
        card = AskUserCard(question, options, allow_free_text=allow_free_text, parent=self)
        card.submitted.connect(self._resolve_ask_user)
        # Insert above the input bar. We keep a reference on the dock so the
        # layout can be cleaned up on _clear.
        if not hasattr(self, "_ask_user_container") or self._ask_user_container is None:
            from qgis.PyQt.QtWidgets import QWidget as _QW
            self._ask_user_container = _QW()
            self._ask_user_container.setStyleSheet(f"background-color: {_SURFACE};")
            # Splice it into the layout between the transcript area and the
            # input bar. We rely on the dock's _build_ui having added the
            # input bar last.
            self.widget().layout().addWidget(self._ask_user_container)
        # The container has a vertical layout with the card inside.
        from qgis.PyQt.QtWidgets import QVBoxLayout as _QV
        if self._ask_user_container.layout() is None:
            self._ask_user_container.setLayout(_QV())
            self._ask_user_container.layout().setContentsMargins(16, 0, 16, 8)
        self._ask_user_container.layout().addWidget(card)
        self._ask_user_card = card
        # Status update so the user knows we're waiting.
        self.status.setText(
            f"<span style='color:{_ACCENT};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Awaiting input</span>"
        )

    def _resolve_ask_user(self, payload):
        """User picked an option or typed a reply; close the card and unblock."""
        self._ask_user_payload = payload
        if self._ask_user_card is not None:
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        # Echo the reply into the transcript as a user message so the user
        # has a record of what they answered.
        label = payload.get("choice")
        text = payload.get("free_text")
        if label:
            self._add_user_message(f"→ {label}")
        elif text:
            self._add_user_message(f"→ {text}")
        # Status update
        self.status.setText(
            f"<span style='color:{_SUCCESS};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        # Forward to the toolkit so its blocked ask_user() returns.
        if self._toolkit is not None:
            cancelled = bool(payload.get("cancelled", False))
            self._toolkit._resolve_ask_user({
                "choice": payload.get("choice"),
                "free_text": payload.get("free_text"),
                "cancelled": cancelled,
            })
```

- [ ] **Step 4: Handle ASK_USER events in `_on_event`**

In `_on_event` (line 470), add a new `elif` branch for the new event. The new branch goes after the `TOOL_RESULT` block (line 514) and before any other branches that may be added later. Insert this block right after the `if is_cancelled:` block ends (find the matching `if is_cancelled:` in the `TOOL_RESULT` branch and add the new branch immediately after it):

```python
        elif ev.type == EventType.ASK_USER:
            self._show_ask_user(
                ev.data.get("question", ""),
                ev.data.get("options", []),
                ev.data.get("allow_free_text", True),
            )
```

- [ ] **Step 5: Cancel the pending ask_user on Stop / Clear**

In `_on_stop` (line 452), the existing code calls `self._request_cancel()`. After that call, add a defensive fire of the resolve so any in-flight `ask_user` is unblocked:

```python
            if self._request_cancel is not None:
                try:
                    self._request_cancel()
                except Exception:
                    pass
            if self._ask_user_card is not None and self._toolkit is not None:
                self._toolkit._resolve_ask_user({
                    "choice": None, "free_text": None, "cancelled": True,
                })
            self.status.setText(
                f"<span style='color:{_DANGER};'>&#9679;</span> "
                f"<span style='color:{_TEXT_3}; font-size:11px;'>Stopping</span>"
            )
```

In `_clear` (line 394), add the same fire-and-forget cancel at the top of the method body:

```python
    def _clear(self):
        if self._ask_user_card is not None:
            if self._toolkit is not None:
                self._toolkit._resolve_ask_user({
                    "choice": None, "free_text": None, "cancelled": True,
                })
            self._ask_user_card.deleteLater()
            self._ask_user_card = None
        self._history = []
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.status.setText(
            f"<span style='color:{_SUCCESS};'>&#9679;</span> "
            f"<span style='color:{_TEXT_3}; font-size:11px;'>Ready</span>"
        )
        self._typing_widget = None
        self._current_agent_turn = None
        self._current_tool_row = None
        self._current_text = ""
        self._scroll_locked = False
```

- [ ] **Step 6: Run the full test suite to confirm no regression**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -q`

Expected: all tests still pass. (The dock code only runs inside QGIS, so unit tests do not exercise it; a failure here would be a Python syntax error in `chat_dock.py`.)

- [ ] **Step 7: Commit**

```bash
git add gui/chat_dock.py
git commit -m "feat: dock renders ask_user popover and routes replies to toolkit"
```

---

## Task 6: Pass toolkit into the dock from the plugin

**Files:**
- Modify: `plugin.py:55-68` (dock construction)

- [ ] **Step 1: Update `_ensure_dock` to pass the toolkit**

Open `plugin.py`. In `_ensure_dock` (line 55), change the `ChatDock` construction call from:

```python
            self._dock = ChatDock(self._get_backend, self._open_settings,
                                  self.request_cancel,
                                  self.iface.mainWindow())
```

to:

```python
            self._dock = ChatDock(self._get_backend, self._open_settings,
                                  self.request_cancel,
                                  toolkit=self.toolkit,
                                  parent=self.iface.mainWindow())
```

- [ ] **Step 2: Run the test suite**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -q`

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add plugin.py
git commit -m "feat: plugin passes toolkit into dock for ask_user wiring"
```

---

## Task 7: Add Brainstorming Protocol section to both system prompts

**Files:**
- Modify: `backends/api_backend.py:18-48` (`DEFAULT_SYSTEM_PROMPT`)
- Modify: `backends/openai_backend.py:15-45` (`DEFAULT_SYSTEM_PROMPT`)

- [ ] **Step 1: Append the section to `backends/api_backend.py`**

Open `backends/api_backend.py`. The `DEFAULT_SYSTEM_PROMPT` triple-quoted string ends with `- For questions needing no tools: answer directly."""`. Append immediately before the closing `"""`:

```
- For questions needing no tools: answer directly.

Brainstorming protocol:
- When in doubt, call `ask_user(question, options, allow_free_text=True)`
  instead of guessing. Always include 2-4 options; mark the first as
  recommended in its description.

Triggers — brainstorm when you see any of these:
- A vector layer has no spatial index (you noticed it in get_layer_summary
  or a slow operation) and the user asked for a spatial operation.
- The user did not name a field, CRS, comparison layer, or time window that
  the analysis needs.
- A tool result is empty / all zeros / zero features where the user clearly
  expected non-empty data.
- Schema or CRS mismatch between two layers you're about to join, clip, or
  overlay.
- A value is out of the range you expected (e.g. population count > 1e9,
  density = 0, year = 1900).

When the user picks an option, treat their choice as the new instruction and
proceed. Do not re-ask the same question.
```

- [ ] **Step 2: Append the same section to `backends/openai_backend.py`**

Open `backends/openai_backend.py`. The `DEFAULT_SYSTEM_PROMPT` triple-quoted string ends with `- For questions needing no tools: answer directly."""`. Apply the same edit — append the exact same `Brainstorming protocol:` block before the closing `"""`.

- [ ] **Step 3: Run the test suite**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -q`

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backends/api_backend.py backends/openai_backend.py
git commit -m "feat: brainstorming protocol section in default system prompts"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/muhammadalichamdan/Documents/Development/AgenticGis && python -m pytest tests/ -v`

Expected: every test passes. The new file `tests/test_ask_user.py` adds 5 tests; the modified `tests/test_tools.py` adds 1; total expected is the previous count + 6.

- [ ] **Step 2: Manual integration sanity check (best-effort)**

Open the plugin in QGIS (if a QGIS dev environment is available). Send a prompt like *"analyse the data"* and confirm:
1. The agent calls `ask_user` (visible as a tool bubble).
2. The popover card appears above the input bar with the question and option buttons.
3. Clicking an option closes the popover, echoes the choice in the transcript, and lets the agent continue.

If QGIS is not available in the test environment, skip this step and document it in the PR description.

- [ ] **Step 3: Final commit (if any post-review fixes were needed)**

If Step 1 revealed a regression, fix it and commit with a descriptive message. Otherwise, skip this step.

---

## Self-review notes (executed before publishing)

- **Spec coverage:**
  - Spec §3 (tool + event + prompt) → Tasks 1, 2, 3, 4, 5, 7.
  - Spec §4.1 (toolkit method) → Task 3.
  - Spec §5.1 (event) → Task 1.
  - Spec §5.2 (popover) → Task 4.
  - Spec §5.3 (dock wiring) → Task 5.
  - Spec §6 (prompt section) → Task 7.
  - Spec §8 (error handling: too few/many options, recursive guard) → Task 3 (validation + tests).
  - Spec §8 (cancellation on Stop / Clear) → Task 5.
  - Spec §9 (tests) → Tasks 2, 3, plus the final full-suite run in Task 8.
  - No task gaps.

- **Type consistency:** `_resolve_ask_user` takes a dict with keys `choice`, `free_text`, `cancelled` everywhere it's called (Task 3 definition matches Task 5 callers match Task 5 stop/clear callers).

- **Placeholder scan:** no `TBD` / `TODO` / "fill in". Every code block is complete and runnable.
