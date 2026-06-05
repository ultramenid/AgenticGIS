# QGIS Compatibility Rules

AgenticGIS must support both:

- QGIS 4 with Qt 6 / PyQt6.
- QGIS 3 with Qt 5 / PyQt5.

These rules apply to every code change that touches QGIS, Qt, PyQt, plugin
loading, widgets, signals, threading, dock behavior, dialogs, or Processing.

## Core Rules

1. Do not use Qt 5-only enum access directly in production code.
   Use compatibility helpers instead of direct names such as
   `Qt.QueuedConnection` or `Qt.RightDockWidgetArea`.

2. Put cross-version Qt enum access in `core/qt_compat.py`.
   If a new Qt enum is needed, add a named constant there and use that constant
   everywhere else.

3. Prefer this compatibility pattern:

   ```python
   def _qt_enum(enum_name, member_name):
       enum_type = getattr(Qt, enum_name, None)
       if enum_type is not None and hasattr(enum_type, member_name):
           return getattr(enum_type, member_name)
       return getattr(Qt, member_name)
   ```

4. QGIS 4 / PyQt6 scoped enum examples:

   ```python
   Qt.ConnectionType.QueuedConnection
   Qt.DockWidgetArea.RightDockWidgetArea
   ```

   QGIS 3 / PyQt5 fallback examples:

   ```python
   Qt.QueuedConnection
   Qt.RightDockWidgetArea
   ```

5. Never assume plugin startup only runs on QGIS 3.
   `classFactory()` and `AgenticGisPlugin.__init__()` must work before any dock
   or settings UI is opened.

6. Avoid importing optional or UI-heavy modules at plugin import time unless
   they are required for startup.
   Lazy-import dialogs, dock widgets, and backend-specific helpers where
   practical.

7. Do not add third-party runtime dependencies.
   The plugin must remain installable on stock QGIS Python.

8. When using subprocesses or local CLI agents, preserve platform compatibility:
   macOS, Linux, and Windows behavior must be considered separately.

## Required Checks Before Release

Run these checks before publishing any release:

```bash
git diff --check
/Applications/QGIS-LTR.app/Contents/MacOS/bin/python3.9 -c "import sys; sys.path.insert(0, '/Users/muhammadalichamdan/Documents/Development'); import AgenticGis; print(AgenticGis.classFactory(None).__class__.__name__)"
QT_QPA_PLATFORM=offscreen /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3.9 -c "import sys; sys.path.insert(0, '/Users/muhammadalichamdan/Documents/Development'); from qgis.PyQt.QtWidgets import QApplication; from AgenticGis.gui.chat_dock import ChatDock; app=QApplication([]); dock=ChatDock(lambda: None, lambda: None, lambda: None, toolkit=None, show_startup_picker=False); print(dock.objectName()); dock.deleteLater()"
rg -n "Qt\.QueuedConnection|Qt\.RightDockWidgetArea" core gui plugin.py
```

The final `rg` command should return no matches.

## Review Checklist

- New Qt enum usage goes through `core/qt_compat.py`.
- Plugin startup works through `classFactory()`.
- Chat dock construction works offscreen.
- QGIS 4 / PyQt6 scoped enum changes are covered.
- QGIS 3 / PyQt5 fallback behavior is preserved.
- Release metadata version is bumped for published zips.
- The release zip is built from the release tag, not from a dirty worktree.

## Known Compatibility Pitfalls

- `Qt.QueuedConnection` works in PyQt5 but fails in PyQt6. Use
  `QUEUED_CONNECTION`.
- `Qt.RightDockWidgetArea` works in PyQt5 but may fail in PyQt6. Use
  `RIGHT_DOCK_WIDGET_AREA`.
- Direct enum comparisons may differ between PyQt5 and PyQt6. Prefer comparing
  behavior or using compatibility constants.
- QThread cleanup can behave differently across bindings. Avoid deleting a
  worker thread from inside its own `finished` signal path unless verified.
