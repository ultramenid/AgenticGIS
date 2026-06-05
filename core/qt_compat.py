"""Qt enum compatibility helpers for QGIS 3/PyQt5 and QGIS 4/PyQt6."""

from qgis.PyQt.QtCore import Qt


def _qt_enum(enum_name, member_name):
    enum_type = getattr(Qt, enum_name, None)
    if enum_type is not None and hasattr(enum_type, member_name):
        return getattr(enum_type, member_name)
    return getattr(Qt, member_name)


QUEUED_CONNECTION = _qt_enum("ConnectionType", "QueuedConnection")
RIGHT_DOCK_WIDGET_AREA = _qt_enum("DockWidgetArea", "RightDockWidgetArea")
