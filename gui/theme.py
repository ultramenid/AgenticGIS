"""Canonical color tokens for the AgenticGIS UI.

Two named palettes are provided so the dark dock (chat_dock.py) and the
settings dialog (settings_dialog.py) can share token names while keeping
their distinct visual identities.  Import the palette you need and alias
back to the local ``_TOKEN`` names so existing style-sheet strings don't
break.
"""

# ── Chat-dock palette (cool neutral grays) ───────────────────────────────────
DOCK_CANVAS = "#141414"
DOCK_SURFACE = "#1c1c1c"
DOCK_SURFACE_2 = "#232323"
DOCK_BORDER = "#2b2b2b"
DOCK_BORDER_SOFT = "#222222"
DOCK_TEXT = "#e8e8e8"
DOCK_TEXT_2 = "#9a9a9a"
DOCK_TEXT_3 = "#6f6f6f"
DOCK_TEXT_4 = "#4a4a4a"
DOCK_ACCENT = "#e8e8e8"
DOCK_ACCENT_DIM = "#9a9a9a"
DOCK_ACCENT_HOV = "#ffffff"
DOCK_PURPLE = "#6f6f6f"
DOCK_WARN = "#d99a3c"
DOCK_SUCCESS = "#5aa86f"
DOCK_DANGER = "#d05a5a"
DOCK_CODE_GREEN = "#e8e8e8"

# ── Settings-dialog palette (warm sepia neutrals) ────────────────────────────
DIALOG_SURFACE = "#1f1f1d"
DIALOG_SURFACE_2 = "#262521"
DIALOG_SURFACE_HOV = "#2d2b25"
DIALOG_INPUT_BG = "#191918"
DIALOG_BORDER = "#4a4234"
DIALOG_BORDER_SOFT = "#343129"
DIALOG_TEXT = "#eeeeea"
DIALOG_TEXT_2 = "#bbb7ad"
DIALOG_TEXT_3 = "#7d786d"
DIALOG_ACCENT = "#e7dfcf"
DIALOG_ACCENT_HOV = "#f2eadb"
DIALOG_WARN = "#d99a3c"
DIALOG_SUCCESS = "#5aad6b"
DIALOG_DANGER = "#e05c5c"
