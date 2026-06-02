"""AgenticGIS — in-QGIS agentic chat assistant.

Zero third-party dependencies: everything runs on QGIS's bundled Python
standard library, so the plugin is drop-in on any stock QGIS install. The
QGIS plugin loader calls ``classFactory`` to obtain the plugin instance.
"""


def classFactory(iface):  # noqa: N802 (QGIS API naming)
    from .plugin import AgenticGisPlugin

    return AgenticGisPlugin(iface)
