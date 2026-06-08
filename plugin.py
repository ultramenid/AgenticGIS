"""AgenticGIS plugin entry point.

Owns the long-lived pieces (config, main-thread executor, toolkit, optional MCP
bridge) and the chat dock, and rebuilds the active backend whenever settings
change.
"""

import hashlib
import json

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
        self._cached_backend = None
        self._cached_backend_fingerprint = None

    # ------------------------------------------------------------------ #
    # QGIS plugin lifecycle                                              #
    # ------------------------------------------------------------------ #
    def initGui(self):
        self._action = QAction("AgenticGIS", self.iface.mainWindow())
        self._action.setCheckable(True)
        self._action.triggered.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToMenu("AgenticGIS", self._action)
        try:
            from .core.network_cache import (
                maybe_enable_default_cache,
                sweep_stale_cache_on_startup,
            )
            sweep_stale_cache_on_startup()
            # Enable a default cache size only if QGIS's cache is off; never
            # overrides a size the user already set.
            maybe_enable_default_cache()
        except Exception:  # nosec B110
            pass

    def unload(self):
        try:
            from .core.network_cache import clear_cache_on_unload
            clear_cache_on_unload()
        except Exception:  # nosec B110
            pass
        self.toolkit.cleanup_gee_tiffs()
        self._stop_server()
        if self._dock is not None:
            try:
                self._dock._stop_active_worker()
            except Exception:  # nosec B110
                pass
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        self._close_cached_backend()
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
        fingerprint = self._backend_settings_fingerprint()
        fingerprint_matches = self._cached_backend_fingerprint == fingerprint
        if self._cached_backend is not None and fingerprint_matches:
            return self._cached_backend

        backend = build_backend(
            self.config, self.toolkit, self.executor, self._server_provider
        )
        old_backend = self._cached_backend
        self._cached_backend = backend
        self._cached_backend_fingerprint = fingerprint
        if old_backend is not None:
            self._close_backend(old_backend)
        return backend

    def _backend_settings_fingerprint(self):
        """Return a stable digest of the settings used to build a backend."""
        if hasattr(self.config, "all"):
            settings = self.config.all()
        else:
            settings = {
                name: self.config.get(name)
                for name in sorted(config_mod.DEFAULTS)
            }
        serialized = json.dumps(
            settings,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    @staticmethod
    def _close_backend(backend):
        close = getattr(backend, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:  # nosec B110
            pass

    def _close_cached_backend(self):
        backend = self._cached_backend
        self._cached_backend = None
        self._cached_backend_fingerprint = None
        if backend is not None:
            self._close_backend(backend)

    def request_cancel(self):
        """Called by the dock's Stop button — flips the toolkit's cancel token
        so a long-running main-thread operation (run_pyqgis, processing.run,
        create_chart, get_layer_statistics) can stop cooperatively.
        """
        backend = self._cached_backend
        if backend is not None:
            try:
                backend.cancel_current_request()
            except Exception:  # nosec B110
                pass
        try:
            self.toolkit.request_cancel()
        except Exception:  # nosec B110
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
            except Exception:  # nosec B110
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
