import ast
import gc
import io
import json
import re
import string
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import QgsApplication

from AgenticGIS.core.session_store import SETTINGS_KEY, SessionStore
from AgenticGIS.i18n import load_plugin_translator, resolve_qgis_locale
from AgenticGIS.plugin import AgenticGisPlugin
from AgenticGIS.server import mcp_server, mcp_stdio

ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    global app
    app = QgsApplication.instance() or QgsApplication([], False)


class LocaleTests(unittest.TestCase):
    def test_locale_matrix(self):
        for enabled, override, system, expected, portuguese in (
            (True, "pt-BR", "en_US", "pt_BR", True),
            (False, "en_US", "pt_BR.UTF-8", "pt_BR", True),
            (True, "en_US", "pt_BR", "en_US", False),
            (False, "", "en_US", "en_US", False),
            (True, "fr_FR", "pt_BR", "fr_FR", False),
            (True, "pt_PT", "pt_BR", "pt_PT", False),
            (True, "not valid", "pt_BR", "pt_BR", True),
            (True, None, "pt_BR", "pt_BR", True),
            (True, "xx_YY", "pt_BR", "pt_BR", True),
            (False, "", "C", "en", False),
        ):
            with self.subTest(override=override, system=system):
                settings = Mock()
                values = {"locale/overrideFlag": enabled, "locale/userLocale": override}
                settings.value.side_effect = lambda key, default, kind=None: values.get(key, default)
                original_locale = QLocale().name()
                locale = resolve_qgis_locale(settings, system)
                self.assertEqual(locale, expected)
                self.assertEqual(load_plugin_translator(locale) is not None, portuguese)
                self.assertEqual(QLocale().name(), original_locale)
                settings.setValue.assert_not_called()

    def test_corrupt_and_language_only_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "AgenticGIS_pt_BR.qm").write_bytes(b"invalid")
            self.assertIsNone(load_plugin_translator("pt_BR", directory))
            (directory / "AgenticGIS_pt.qm").write_bytes((ROOT / "i18n/AgenticGIS_pt_BR.qm").read_bytes())
            self.assertIsNotNone(load_plugin_translator("pt_BR", directory))
            self.assertIsNotNone(load_plugin_translator("pt_PT", directory))

    def test_plugin_lifecycle(self):
        iface = Mock()
        window = QMainWindow()
        iface.mainWindow.side_effect = lambda: self.assert_ready("Pronto") or window
        plugin = AgenticGisPlugin(iface)
        with patch("AgenticGIS.plugin.resolve_qgis_locale", return_value="pt_BR"), \
             patch.object(plugin, "_apply_mcp_state"), \
             patch("AgenticGIS.core.network_cache.sweep_stale_cache_on_startup"), \
             patch("AgenticGIS.core.network_cache.maybe_enable_default_cache"), \
             patch("AgenticGIS.core.network_cache.clear_cache_on_unload"):
            try:
                plugin.initGui()
                plugin.initGui()
                gc.collect()
                self.assert_ready("Pronto")
                iface.addToolBarIcon.assert_called_once()
            finally:
                plugin.unload()
            self.assertIsNone(plugin._translator)
            self.assert_ready("Ready")

    def test_translator_removed_when_gui_teardown_raises(self):
        iface = Mock()
        iface.mainWindow.return_value = QMainWindow()
        plugin = AgenticGisPlugin(iface)
        with patch("AgenticGIS.plugin.resolve_qgis_locale", return_value="pt_BR"), \
             patch.object(plugin, "_apply_mcp_state"), \
             patch("AgenticGIS.core.network_cache.sweep_stale_cache_on_startup"), \
             patch("AgenticGIS.core.network_cache.maybe_enable_default_cache"), \
             patch.object(plugin, "_unload_gui", side_effect=RuntimeError("teardown")):
            plugin.initGui()
            self.assert_ready("Pronto")
            with self.assertRaisesRegex(RuntimeError, "teardown"):
                plugin.unload()
        self.assertIsNone(plugin._translator)
        self.assert_ready("Ready")

    def assert_ready(self, expected):
        self.assertEqual(QCoreApplication.translate("ChatDock", "Ready"), expected)


class CatalogTests(unittest.TestCase):
    def test_catalog_matches_literal_translation_calls(self):
        tree = ET.parse(ROOT / "i18n/AgenticGIS_pt_BR.ts")
        catalog = {
            (context.findtext("name"), message.findtext("source"))
            for context in tree.findall("context")
            for message in context.findall("message")
        }
        literals = set()
        sources = [ROOT / "plugin.py"]
        for folder in ("gui", "core", "backends"):
            sources.extend((ROOT / folder).glob("*.py"))
        for source in sources:
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or len(node.args) < 2:
                    continue
                func = node.func
                if (
                    getattr(func, "id", None) != "QT_TRANSLATE_NOOP"
                    and not (
                        getattr(getattr(func, "value", None), "id", None) == "QCoreApplication"
                        and getattr(func, "attr", None) == "translate"
                    )
                ):
                    continue
                if all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args[:2]):
                    literals.add((node.args[0].value, node.args[1].value))
        self.assertEqual(catalog, literals)

    def test_finished_messages_and_placeholders(self):
        tree = ET.parse(ROOT / "i18n/AgenticGIS_pt_BR.ts")
        formatter = string.Formatter()
        for message in tree.findall(".//message"):
            source = message.findtext("source")
            translation = message.find("translation")
            self.assertIsNotNone(translation, source)
            self.assertIsNone(translation.get("type"), source)
            forms = [f.text for f in translation.findall("numerusform")] or [translation.text]
            placeholders = lambda text: sorted((name, spec, conv) for _, name, spec, conv in
                                               formatter.parse(text) if name is not None)
            for form in forms:
                self.assertTrue(form, source)
                self.assertEqual(placeholders(source), placeholders(form), source)
                self.assertEqual(sorted(re.findall(r"%L?(?:n|[1-9][0-9]*)", source)),
                                 sorted(re.findall(r"%L?(?:n|[1-9][0-9]*)", form)), source)


class SessionTests(unittest.TestCase):
    def test_names_and_history_are_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / "sessions.ini"), QSettings.Format.IniFormat)
            english = SessionStore(settings)
            original = english.active_session()
            original["transcript_events"] = [{"type": "assistant", "text": "User data"}]
            english.save_session(original["id"], transcript_events=original["transcript_events"])
            before = json.loads(settings.value(SETTINGS_KEY))
            portuguese = SessionStore(settings, default_name="Nova sessão")
            self.assertEqual(json.loads(settings.value(SETTINGS_KEY)), before)
            self.assertEqual(portuguese.active_session()["name"], "New session")
            portuguese.rename_session(original["id"], "My project")
            self.assertEqual(portuguese.create_session()["name"], "Nova sessão")
            self.assertEqual(portuguese.get_session(original["id"])["name"], "My project")

    def test_empty_session_name_fallbacks_localize_in_portuguese(self):
        from qgis.PyQt.QtWidgets import QLabel

        from AgenticGIS.gui.chat_dock import ChatDock

        translator = load_plugin_translator("pt_BR")
        self.assertIsNotNone(translator)
        self.assertTrue(QCoreApplication.installTranslator(translator))
        self.addCleanup(QCoreApplication.removeTranslator, translator)
        with tempfile.TemporaryDirectory() as temp:
            settings = QSettings(str(Path(temp) / "sessions.ini"), QSettings.Format.IniFormat)
            settings.setValue(
                SETTINGS_KEY,
                json.dumps({
                    "schema_version": 1,
                    "active_session_id": "s1",
                    "sessions": [{"id": "s1", "name": ""}],
                }),
            )
            store = SessionStore(settings, default_name="Nova sessão")
            self.assertEqual(store.active_session()["name"], "Nova sessão")

            dock = ChatDock(
                lambda: None,
                lambda: None,
                lambda: None,
                session_store=store,
                show_startup_picker=False,
            )
            self.addCleanup(dock.close)
            self.assertEqual(dock._status_session_name, "Nova sessão")
            dock._status_session_name = ""
            dock._render_status()
            self.assertIn("Nova sessão", dock.status.text())
            delete_dialog = dock._build_delete_session_dialog({"id": "s1", "name": ""})
            self.addCleanup(delete_dialog.close)
            labels = [lbl.text() for lbl in delete_dialog.findChildren(QLabel)]
            self.assertIn("Nova sessão", labels)

class McpTests(unittest.TestCase):
    def test_stdio_and_rpc_frames_match_across_locales(self):
        bridge = object.__new__(mcp_server._RpcServer)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "list_layers", "arguments": {}}},
        ]
        bridge.toolkit = Mock()
        bridge.executor = Mock()

        def post(frame):
            return 200, json.dumps(bridge.handle_rpc(json.loads(frame))).encode()

        def rpc_frames():
            stdout = io.StringIO()
            with patch.object(mcp_server.tools_mod, "dispatch", return_value={"ok": True, "layers": []}):
                mcp_stdio.serve(io.StringIO("\n".join(map(json.dumps, requests))), stdout, post)
            return stdout.getvalue()

        english = rpc_frames()
        translator = load_plugin_translator("pt_BR")
        self.assertTrue(QCoreApplication.installTranslator(translator))
        self.addCleanup(QCoreApplication.removeTranslator, translator)
        self.assertEqual(rpc_frames(), english)
        replies = [json.loads(line)["result"] for line in english.splitlines()]
        self.assertIn("list_layers", [tool["name"] for tool in replies[1]["tools"]])
        self.assertEqual(json.loads(replies[2]["content"][0]["text"]), {"ok": True, "layers": []})
