"""Unattended QGIS Desktop ZIP install, locale, settings, and unload smoke."""

from __future__ import annotations

import configparser
import importlib
import json
import os
import sys
import traceback
from pathlib import Path

from qgis.PyQt.QtCore import QT_VERSION_STR, QCoreApplication, QSettings, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QLabel
from qgis.core import Qgis, QgsApplication


def _write_result(result_path: str | None, payload: dict) -> None:
    if not result_path:
        return
    target = Path(result_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _message_level(name: str, fallback: int) -> int:
    level = getattr(Qgis, name, None)
    if level is None:
        level = getattr(getattr(Qgis, "MessageLevel", Qgis), name, fallback)
    return int(level)


def _import_all_packaged_modules(plugin_dir: Path) -> int:
    imported = 0
    for py_path in sorted(plugin_dir.rglob("*.py")):
        if any(part in py_path.parts for part in ("__pycache__", "tests", "ci", "docs", ".git")):
            continue
        rel = py_path.relative_to(plugin_dir).with_suffix("")
        parts = ["AgenticGIS"] + [part for part in rel.parts if part != "__init__"]
        importlib.import_module(".".join(parts))
        imported += 1
    return imported


def _run_smoke() -> None:
    result_path = os.environ.get("AGENTICGIS_SMOKE_RESULT")
    zip_path = os.environ.get("AGENTICGIS_SMOKE_ZIP")
    expect_qgis = os.environ.get("AGENTICGIS_EXPECT_QGIS_MAJOR")
    expect_qt = os.environ.get("AGENTICGIS_EXPECT_QT_MAJOR")

    log_errors: list[dict[str, str]] = []
    critical_level = _message_level("Critical", 2)
    warning_level = _message_level("Warning", 1)
    message_log = QgsApplication.messageLog()

    def _on_message(message, tag, level) -> None:
        level_int = int(level)
        python_error_tags = {
            "Python error",
            QCoreApplication.translate("Python", "Python error"),
        }
        python_warning_tags = {
            "Python warning",
            QCoreApplication.translate("Python", "Python warning"),
        }
        is_agentic_critical = level_int == critical_level and tag == "AgenticGIS"
        is_python_diag = (
            level_int in (critical_level, warning_level)
            and (tag in python_error_tags or tag in python_warning_tags)
        )
        if is_agentic_critical or is_python_diag:
            log_errors.append({
                "tag": str(tag),
                "level": str(level_int),
                "message": str(message),
            })

    message_log.messageReceived.connect(_on_message)
    try:
        qgis_version = Qgis.QGIS_VERSION
        qt_version = QT_VERSION_STR
        print(f"SMOKE_VERSIONS qgis={qgis_version} qt={qt_version}", flush=True)

        if expect_qgis and qgis_version.split(".")[0] != expect_qgis:
            raise AssertionError(
                f"Expected QGIS major {expect_qgis}, got {qgis_version}"
            )
        if expect_qt and qt_version.split(".")[0] != expect_qt:
            raise AssertionError(f"Expected Qt major {expect_qt}, got {qt_version}")

        if not zip_path or not Path(zip_path).is_file():
            raise AssertionError(f"AGENTICGIS_SMOKE_ZIP missing or not a file: {zip_path!r}")

        settings = QSettings()
        settings.setValue("locale/overrideFlag", True)
        settings.setValue("locale/userLocale", "pt_BR")
        settings.setValue("AgenticGIS/mcp_enabled", False)
        settings.sync()

        import pyplugin_installer
        from pyplugin_installer.installer_data import plugins as installer_plugins
        import qgis.utils

        if qgis.utils.iface is None:
            raise AssertionError("qgis.utils.iface is None during desktop smoke")

        pyplugin_installer.initPluginInstaller()
        installer = pyplugin_installer.instance()
        if installer is None:
            raise AssertionError("pyplugin_installer.instance() returned None")

        installed = installer.installFromZipFile(zip_path)
        if not installed:
            raise AssertionError(f"installFromZipFile returned falsy: {installed!r}")

        if not qgis.utils.isPluginLoaded("AgenticGIS"):
            raise AssertionError("AgenticGIS was not loaded after ZIP installation")
        if "AgenticGIS" not in qgis.utils.active_plugins:
            raise AssertionError("AgenticGIS missing from qgis.utils.active_plugins")

        plugin = qgis.utils.plugins["AgenticGIS"]
        if getattr(plugin, "_server", "missing") is not None:
            raise AssertionError("MCP server should remain stopped when mcp_enabled=False")

        plugin_dir = Path(sys.modules["AgenticGIS"].__file__).resolve().parent
        if "plugins" not in plugin_dir.parts or (plugin_dir / "tests").exists() or (plugin_dir / "ci").exists():
            raise AssertionError(
                f"AgenticGIS loaded from unexpected directory instead of installed ZIP: {plugin_dir}"
            )
        meta_parser = configparser.ConfigParser(interpolation=None)
        meta_parser.read(plugin_dir / "metadata.txt", encoding="utf-8")
        icon_rel = meta_parser.get("general", "icon", fallback="").strip()
        if not icon_rel:
            raise AssertionError("Plugin metadata.txt missing icon entry")
        icon_rel = icon_rel[2:] if icon_rel.startswith("./") else icon_rel
        icon_file = (plugin_dir / icon_rel).resolve()
        if plugin_dir.resolve() not in icon_file.parents or not icon_file.is_file() or QIcon(str(icon_file)).isNull():
            raise AssertionError("Declared plugin icon failed to load as non-null QIcon")

        all_plugins = installer_plugins.all()
        if "AgenticGIS" not in all_plugins:
            raise AssertionError("AgenticGIS missing from Plugin Manager metadata registry")
        meta = all_plugins["AgenticGIS"]
        if "Assistente de conversa com agente" not in meta.get("description", ""):
            raise AssertionError(
                f"Localized description missing in Plugin Manager: {meta.get('description')!r}"
            )
        if "O AgenticGIS adiciona um painel de conversa ao QGIS" not in meta.get("about", ""):
            raise AssertionError(
                f"Localized about missing in Plugin Manager: {meta.get('about')!r}"
            )

        ready_pt = QCoreApplication.translate("ChatDock", "Ready")
        if ready_pt != "Pronto":
            raise AssertionError(f"Expected Portuguese Ready='Pronto', got {ready_pt!r}")

        from AgenticGIS.gui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(plugin.config, qgis.utils.iface.mainWindow())
        try:
            if dialog.windowTitle() != "AgenticGIS — Configurações":
                raise AssertionError(
                    f"Unexpected SettingsDialog title: {dialog.windowTitle()!r}"
                )
            labels = [lbl.text() for lbl in dialog.findChildren(QLabel)]
            if "Configurações" not in labels:
                raise AssertionError(
                    f"Expected 'Configurações' label inside SettingsDialog, got {labels[:10]!r}"
                )
        finally:
            dialog.close()
            dialog.deleteLater()

        dock = plugin._ensure_dock()
        if dock is None:
            raise AssertionError("plugin._ensure_dock() returned None")

        imported_modules = _import_all_packaged_modules(plugin_dir)
        if imported_modules <= 0:
            raise AssertionError("No packaged modules imported during smoke check")

        if not qgis.utils.unloadPlugin("AgenticGIS"):
            raise AssertionError("qgis.utils.unloadPlugin('AgenticGIS') returned False")
        if qgis.utils.isPluginLoaded("AgenticGIS"):
            raise AssertionError("AgenticGIS remained loaded after unloadPlugin")

        QCoreApplication.processEvents()
        ready_en = QCoreApplication.translate("ChatDock", "Ready")
        settings_en = QCoreApplication.translate("SettingsDialog", "Settings")
        if ready_en != "Ready" or settings_en != "Settings":
            raise AssertionError(
                f"Expected English fallback after unload, got Ready={ready_en!r}, Settings={settings_en!r}"
            )

        if log_errors:
            raise AssertionError(f"Unexpected QgsMessageLog errors/warnings: {log_errors!r}")

        _write_result(
            result_path,
            {
                "ok": True,
                "marker": "AGENTICGIS_SMOKE_OK",
                "qgis_version": qgis_version,
                "qt_version": qt_version,
                "imported_modules": imported_modules,
                "log_errors": log_errors,
            },
        )
        print("AGENTICGIS_SMOKE_OK", flush=True)
        QCoreApplication.quit()
        QTimer.singleShot(5000, lambda: os._exit(0))
    except BaseException as exc:
        QTimer.singleShot(100, lambda: os._exit(1))
        tb = traceback.format_exc()
        sys.stderr.write(tb + "\nAGENTICGIS_SMOKE_FAIL\n")
        sys.stderr.flush()
        try:
            _write_result(
                result_path,
                {
                    "ok": False,
                    "marker": "AGENTICGIS_SMOKE_FAIL",
                    "error": str(exc),
                    "log_errors": log_errors,
                },
            )
        except Exception as write_exc:
            sys.stderr.write(f"Failed to write smoke failure result: {write_exc}\n")
            sys.stderr.flush()
    finally:
        try:
            message_log.messageReceived.disconnect(_on_message)
        except Exception:  # nosec B110
            pass


QTimer.singleShot(0, _run_smoke)
