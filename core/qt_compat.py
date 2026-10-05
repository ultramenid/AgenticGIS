"""Qt enum compatibility helpers for QGIS 3/PyQt5 and QGIS 4/PyQt6."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontDatabase


def _qt_enum(enum_name, member_name, owner=Qt):
    enum_type = getattr(owner, enum_name, None)
    if enum_type is not None and hasattr(enum_type, member_name):
        return getattr(enum_type, member_name)
    return getattr(owner, member_name)


QUEUED_CONNECTION = _qt_enum("ConnectionType", "QueuedConnection")
RIGHT_DOCK_WIDGET_AREA = _qt_enum("DockWidgetArea", "RightDockWidgetArea")
GENERAL_FONT = _qt_enum("SystemFont", "GeneralFont", owner=QFontDatabase)
FIXED_FONT = _qt_enum("SystemFont", "FixedFont", owner=QFontDatabase)
