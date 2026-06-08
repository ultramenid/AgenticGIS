"""AgenticGIS — in-QGIS agentic chat assistant.

Zero third-party dependencies: everything runs on QGIS's bundled Python
standard library, so the plugin is drop-in on any stock QGIS install. The
QGIS plugin loader calls ``classFactory`` to obtain the plugin instance.
"""


def classFactory(iface):  # noqa: N802 (QGIS API naming)
    try:
        from .plugin import AgenticGisPlugin

        return AgenticGisPlugin(iface)
    except Exception:
        import traceback

        msg = "AgenticGIS classFactory error:\n" + traceback.format_exc()
        try:
            from qgis.core import QgsMessageLog, Qgis

            # QGIS 4 compat: MessageLevel enum may be nested
            level = getattr(Qgis, "Critical", None)
            if level is None:
                level = getattr(getattr(Qgis, "MessageLevel", Qgis), "Critical", 2)
            QgsMessageLog.logMessage(msg, "AgenticGIS", level)
        except Exception:
            pass
        try:
            print(msg)  # noqa: T201
        except Exception:
            pass
        raise
