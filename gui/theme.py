"""Canonical design tokens for the AgenticGIS UI.

One neutral dark palette (modelled on monocode's: hue-less greys, hairline
strokes, a single blue accent) shared by the dock and the settings dialog.
The ``DIALOG_*`` names are kept as aliases so existing imports keep working.
"""

# ── Palette ──────────────────────────────────────────────────────────────────
DOCK_CANVAS = "#171717"        # window background
DOCK_SURFACE = "#1d1d1d"       # cards, composer
DOCK_SURFACE_2 = "#262626"     # raised / hover / selected
DOCK_BORDER = "#2c2c2c"        # card and input borders
DOCK_BORDER_SOFT = "#222222"   # hairline dividers
DOCK_TEXT = "#ebebeb"
DOCK_TEXT_2 = "#9a9a9a"
DOCK_TEXT_3 = "#767676"
DOCK_TEXT_4 = "#565656"
DOCK_ACCENT = "#ebebeb"        # primary button fill
DOCK_ACCENT_DIM = "#bdbdbd"
DOCK_ACCENT_HOV = "#ffffff"
DOCK_BLUE = "#459bf7"          # focus rings, toggles, "active" marks
DOCK_PURPLE = "#767676"
DOCK_WARN = "#e0a43c"
DOCK_SUCCESS = "#4fbf87"
DOCK_DANGER = "#ef5b6b"
DOCK_CODE_GREEN = "#ebebeb"

DIALOG_SURFACE = DOCK_CANVAS
DIALOG_SURFACE_2 = DOCK_SURFACE
DIALOG_SURFACE_HOV = DOCK_SURFACE_2
DIALOG_INPUT_BG = "#191919"
DIALOG_BORDER = DOCK_BORDER
DIALOG_BORDER_SOFT = DOCK_BORDER_SOFT
DIALOG_TEXT = DOCK_TEXT
DIALOG_TEXT_2 = DOCK_TEXT_2
DIALOG_TEXT_3 = DOCK_TEXT_3
DIALOG_ACCENT = DOCK_ACCENT
DIALOG_ACCENT_HOV = DOCK_ACCENT_HOV
DIALOG_BLUE = DOCK_BLUE
DIALOG_WARN = DOCK_WARN
DIALOG_SUCCESS = DOCK_SUCCESS
DIALOG_DANGER = DOCK_DANGER

# ── Font stacks ──────────────────────────────────────────────────────────────
# Prose uses the QGIS UI font; code, tables and tool rows stay monospace.
# Qt doesn't resolve the generic ``monospace``, so name the fonts each OS ships.
MONO_STACK = "'JetBrains Mono','SF Mono','Menlo','Consolas','DejaVu Sans Mono','Courier New',monospace"


def ui_font(size=12, weight=None):
    """QFont for interface text: the QGIS UI font at ``size`` px (scaled)."""
    from qgis.PyQt.QtGui import QFont
    font = QFont()
    font.setPixelSize(fs(size))
    if weight is not None:
        font.setWeight(weight)
    return font


def mono_font(size=12):
    """QFont for code/paths: the platform's fixed-width font at ``size`` px."""
    from qgis.PyQt.QtGui import QFontDatabase
    from ..core.qt_compat import FIXED_FONT
    font = QFontDatabase.systemFont(FIXED_FONT)
    font.setPixelSize(fs(size))
    return font


def sans_family():
    """CSS family for prose: the QGIS application font (follows QGIS Options)."""
    try:
        from qgis.PyQt.QtWidgets import QApplication
        family = QApplication.font().family()
        if family:
            return f"'{family}',sans-serif"
    except Exception:  # nosec B110 - outside Qt (tests) fall back to generic
        pass
    return "sans-serif"


# ── Font scale ───────────────────────────────────────────────────────────────
FONT_SCALE_MIN = 0.75
FONT_SCALE_MAX = 2.0
_user_scale = None
_qgis_ratio = None


def clamp_font_scale(value):
    try:
        return min(FONT_SCALE_MAX, max(FONT_SCALE_MIN, float(value)))
    except (TypeError, ValueError):
        return 1.0


def set_font_scale(value):
    """Override the cached plugin scale (Settings dialog calls this on save)."""
    global _user_scale
    _user_scale = clamp_font_scale(value)


def qgis_font_ratio():
    """QGIS's font size (Options → General) relative to the OS default size."""
    try:
        from qgis.PyQt.QtGui import QFontDatabase
        from qgis.utils import iface
        from ..core.qt_compat import GENERAL_FONT
        system_pt = QFontDatabase.systemFont(GENERAL_FONT).pointSizeF()
        qgis_pt = float(iface.defaultStyleSheetOptions().get("fontPointSize") or system_pt)
        return min(3.0, max(0.5, qgis_pt / system_pt))
    except Exception:
        return 1.0


def fs(size):
    """Scale a base font size by QGIS's font size and the plugin ``font_scale``.

    Read lazily on first use so importing this module never touches QGIS state.
    """
    global _qgis_ratio
    if _qgis_ratio is None:
        _qgis_ratio = qgis_font_ratio()
    if _user_scale is None:
        try:
            from ..config import Config
            set_font_scale(Config().get("font_scale"))
        except Exception:
            set_font_scale(1.0)
    return max(1, round(size * _qgis_ratio * _user_scale))
