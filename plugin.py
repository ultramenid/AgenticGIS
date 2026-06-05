"""AgenticGIS plugin entry point.

Owns the long-lived pieces (config, main-thread executor, toolkit, optional MCP
bridge) and the chat dock, and rebuilds the active backend whenever settings
change.
"""

from qgis.PyQt.QtWidgets import QAction

from . import config as config_mod
from .backends import build_backend
from .core.executor import MainThreadExecutor
from .core.toolkit import QgisToolkit


class AgenticGisPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.config = config_mod.Config()
        self.executor = MainThreadExecutor(config=self.config)       # created on the main thread
        self.toolkit = QgisToolkit(iface, config=self.config)
        self._action = None
        self._dock = None
        self._server = None

    # ------------------------------------------------------------------ #
    # QGIS plugin lifecycle                                              #
    # ------------------------------------------------------------------ #
    def initGui(self):
        self._action = QAction("AgenticGIS", self.iface.mainWindow())
        self._action.setCheckable(True)
        self._action.triggered.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToMenu("AgenticGIS", self._action)

    def unload(self):
        self.toolkit.cleanup_gee_tiffs()
        self._stop_server()
        if self._dock is not None:
            try:
                self._dock._stop_active_worker()
            except Exception:
                pass
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        if self._action is not None:
            self.iface.removeToolBarIcon(self._action)
            self.iface.removePluginMenu("AgenticGIS", self._action)
            self._action = None

    # ------------------------------------------------------------------ #
    # Dock                                                               #
    # ------------------------------------------------------------------ #
    def _ensure_dock(self):
        if self._dock is None:
            from .core.qt_compat import RIGHT_DOCK_WIDGET_AREA
            from .gui.chat_dock import ChatDock

            self._dock = ChatDock(self._get_backend, self._open_settings,
                                  self.request_cancel,
                                  toolkit=self.toolkit,
                                  parent=self.iface.mainWindow())
            self.iface.addDockWidget(RIGHT_DOCK_WIDGET_AREA, self._dock)
            self._dock.visibilityChanged.connect(
                lambda visible: self._action.setChecked(visible)
            )
        return self._dock

    def _toggle_dock(self, checked):
        dock = self._ensure_dock()
        dock.setVisible(checked)

    # ------------------------------------------------------------------ #
    # Backend + MCP bridge                                                #
    # ------------------------------------------------------------------ #
    def _get_backend(self):
        return build_backend(
            self.config, self.toolkit, self.executor, self._server_provider
        )

    def request_cancel(self):
        """Called by the dock's Stop button — flips the toolkit's cancel token
        so a long-running main-thread operation (run_pyqgis, processing.run,
        create_chart, get_layer_statistics) can stop cooperatively.
        """
        try:
            self.toolkit.request_cancel()
        except Exception:
            pass

    def _server_provider(self):
        """Ensure the stdlib MCP bridge is running (CLI mode needs it) and
        return its base URL."""
        from .server import McpBridgeServer

        if self._server is None or not self._server.isRunning():
            self._server = McpBridgeServer(
                self.toolkit,
                self.executor,
                host=self.config.get("mcp_host"),
                port=self.config.get("mcp_port"),
                poll_interval=self.config.get("mcp_poll_interval", 0.5),
            )
            self._server.start()
        return self._server.base_url

    def _stop_server(self):
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None

    # ------------------------------------------------------------------ #
    # Settings                                                            #
    # ------------------------------------------------------------------ #
    def _open_settings(self):
        from .gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.config, self.iface.mainWindow())
        dialog.exec()
        # Backend is rebuilt lazily on the next send via _get_backend().
