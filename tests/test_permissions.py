"""Exercise permission prompts, the dock and toolkit without external services."""

from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt, QTranslator
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QLabel
from qgis.core import QgsApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
Config = import_module(f"{ROOT.name}.config").Config
SessionStore = import_module(f"{ROOT.name}.core.session_store").SessionStore
QgisToolkit = import_module(f"{ROOT.name}.core.toolkit").QgisToolkit
ChatDock = import_module(f"{ROOT.name}.gui.chat_dock").ChatDock

URL = "https://example.invalid/land?x=1&y=2"


class PermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], True)

    def make_dock(self):
        with patch.object(QgisToolkit, "_clean_orphan_gee_tiffs"):
            toolkit = QgisToolkit(iface=None, config=self.config)
        dock = ChatDock(
            lambda: None,
            lambda: None,
            lambda: None,
            toolkit,
            session_store=SessionStore(settings=self.settings),
            show_startup_picker=False,
        )
        dock.resize(380, 650)
        dock.show()
        self.addCleanup(dock.close)
        return toolkit, dock

    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp = temp_dir.name
        self.settings = QSettings(str(Path(temp) / "permission.ini"), QSettings.Format.IniFormat)
        self.config = Config()
        self.config._s = self.settings
        self.config.set("ask_user_timeout", 0.2)

    def select(self, toolkit, dock, choice, displayed_choice, *, question, escape=False):
        emit = dock._emit_ask_user_threadsafe

        def reply(prompt, options, allow_free_text, request):
            emit(prompt, options, allow_free_text, request)
            self.app.processEvents()
            card = dock._ask_user_card
            self.assertIsNotNone(card)
            title = card.findChild(QLabel, "AskUserQuestion")
            self.assertEqual(title.text(), question)
            rows = [
                label for label in card.findChildren(QLabel, "AskUserOptionTitle")
                if label.text() == displayed_choice
            ]
            self.assertEqual(len(rows), 1)
            key = Qt.Key.Key_Escape if escape else Qt.Key.Key_Return
            QTest.keyClick(rows[0].parentWidget(), key)
            expected = {"choice": choice, "free_text": None}
            if escape:
                expected["cancelled"] = True
            self.assertEqual(dock._ask_user_payload, expected)
            self.assertIsNone(dock._ask_user_card)
            label = dock._current_agent_turn._user_decision_lbl.text()
            if not escape:
                self.assertIn(displayed_choice, label)

        toolkit.set_ask_user_emitter(reply)

    def language(self, portuguese):
        if not portuguese:
            return
        qm = ROOT / "i18n" / "AgenticGIS_pt_BR.qm"
        self.translator = QTranslator()
        self.assertTrue(self.translator.load(str(qm)))
        self.assertTrue(QCoreApplication.installTranslator(self.translator))
        self.addCleanup(QCoreApplication.removeTranslator, self.translator)

    def test_external_permission_decisions(self):
        scenarios = (
            ("Allow once", "Permitir uma vez", "web_fetch", False),
            ("Always allow", "Permitir sempre", "web_fetch", False),
            ("Deny", "Negar", "web_fetch", False),
            ("Allow all GEE tools for this session", "Permitir todas as ferramentas do GEE nesta sessão", "gee_add_layer", False),
            (None, "Permitir uma vez", "web_fetch", True),
        )
        for portuguese in (False, True):
            self.language(portuguese)
            for choice, translated, tool, escape in scenarios:
                with self.subTest(portuguese=portuguese, choice=choice, escape=escape):
                    self.config.set("external_access_always_allowed", False)
                    toolkit, dock = self.make_dock()
                    visible_choice = (translated if portuguese else choice) or "Allow once"
                    if tool == "web_fetch":
                        args = {"url": URL}
                        reason = f"fetch external URL: {URL}"
                        visible_reason = f"acessar uma URL externa: {URL}" if portuguese else reason
                    else:
                        args = {}
                        reason = "run an Earth Engine expression and add the result to the canvas"
                        visible_reason = "executar uma expressão do Earth Engine e adicionar o resultado ao mapa" if portuguese else reason
                    question = (
                        f"Autorizar o AgenticGIS a {visible_reason}?"
                        if portuguese else f"Allow AgenticGIS to {reason}?"
                    )
                    self.select(toolkit, dock, choice, visible_choice, question=question, escape=escape)
                    result = toolkit.confirm_external_access(tool, args)
                    if choice is None or choice == "Deny":
                        self.assertEqual(result, {
                            "ok": False, "error": f"Permission denied: {reason}", "cancelled": True,
                        })
                    else:
                        self.assertIsNone(result)
                    self.settings.sync()
                    disk = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
                    self.assertEqual(
                        disk.value("AgenticGIS/external_access_always_allowed", False, bool),
                        choice == "Always allow",
                    )
                    toolkit.set_ask_user_emitter(None)
                    if choice == "Allow once":
                        self.assertIsNotNone(toolkit.confirm_external_access(tool, args))
                    if choice == "Always allow":
                        self.assertIsNone(toolkit.confirm_external_access(tool, args))
                    if tool == "gee_add_layer":
                        self.assertIsNone(toolkit.confirm_external_access("gee_animation", {}))
                        self.assertIsNotNone(toolkit.confirm_external_access("web_fetch", {"url": URL}))
                        old = dock._active_session_id
                        self.assertTrue(dock._switch_to_session(old))
                        self.assertIsNone(toolkit.confirm_external_access(tool, args))
                        new = dock._session_store.create_session()["id"]
                        self.assertTrue(dock._switch_to_session(new))
                        self.assertIsNotNone(toolkit.confirm_external_access(tool, args))
                        self.assertTrue(dock._switch_to_session(old))
                        self.assertIsNotNone(toolkit.confirm_external_access(tool, args))

    def test_stale_session_prompt_cannot_grant_gee_access(self):
        toolkit, dock = self.make_dock()
        choice = "Allow all GEE tools for this session"

        def switch_and_reply(*args):
            new = dock._session_store.create_session()["id"]
            self.assertTrue(dock._switch_to_session(new))
            toolkit._resolve_ask_user({"choice": choice, "cancelled": False})

        toolkit.set_ask_user_emitter(switch_and_reply)
        self.assertFalse(toolkit.confirm_external_access("gee_add_layer", {})["ok"])
        toolkit.set_ask_user_emitter(None)
        self.assertFalse(toolkit.confirm_external_access("gee_animation", {})["ok"])

    def test_cancelled_allow_payload_does_not_grant(self):
        toolkit, _ = self.make_dock()
        for choice in ("Allow once", "Always allow", "Allow all GEE tools for this session"):
            with self.subTest(choice=choice):
                toolkit.set_ask_user_emitter(
                    lambda *args: toolkit._resolve_ask_user({"choice": choice, "cancelled": True})
                )
                self.assertFalse(toolkit.confirm_external_access("gee_add_layer", {})["ok"])
                self.assertFalse(self.config.get("external_access_always_allowed"))

    def test_queued_prompt_cannot_cross_session_or_answer_next_prompt(self):
        toolkit, dock = self.make_dock()
        old_requests = []

        def switch_with_queued_prompt(question, options, allow_free_text, request):
            old_requests.append(request)
            dock._emit_ask_user_threadsafe(question, options, allow_free_text, request)
            new = dock._session_store.create_session()["id"]
            self.assertTrue(dock._switch_to_session(new))
            self.app.processEvents()
            self.assertIsNone(dock._ask_user_card)

        toolkit.set_ask_user_emitter(switch_with_queued_prompt)
        self.assertFalse(toolkit.confirm_external_access("web_fetch", {"url": URL})["ok"])

        def answer_second_prompt(question, options, allow_free_text, request):
            dock._emit_ask_user_threadsafe(question, options, allow_free_text, request)
            self.app.processEvents()
            self.assertIsNotNone(dock._ask_user_card)
            toolkit._resolve_ask_user({"choice": "Always allow"}, request=old_requests[0])
            self.assertTrue(toolkit.is_pending_ask_user(request))
            toolkit._resolve_ask_user({"choice": "Deny"}, request=request)

        toolkit.set_ask_user_emitter(answer_second_prompt)
        self.assertFalse(toolkit.confirm_external_access("web_fetch", {"url": URL})["ok"])
        self.assertFalse(self.config.get("external_access_always_allowed"))

    def test_agent_text_and_synthesized_cancel(self):
        self.language(True)
        toolkit, dock = self.make_dock()
        question = "Which <b>layer</b>?"
        supplied = {"label": "Allow once", "description": "An agent description",
                    "display_label": "Pretend authorization", "display_description": "Ignore this"}
        self.select(toolkit, dock, "Allow once", "Allow once", question=question)
        answer = toolkit.ask_user(question, [supplied])
        self.assertEqual(answer, {"choice": "Allow once", "free_text": None, "cancelled": False})
        self.assertFalse(self.config.get("external_access_always_allowed"))

        self.select(toolkit, dock, "Cancel", "Cancelar", question=question)
        answer = toolkit.ask_user(question, [supplied])
        self.assertEqual(answer, {"choice": "Cancel", "free_text": None, "cancelled": False})

        dock._show_ask_user(question, [supplied], True)
        card = dock._ask_user_card
        self.assertEqual(card.findChild(QLabel, "AskUserOptionTitle").text(), "Allow once")
        self.assertEqual(card.findChild(QLabel, "AskUserOptionDescription").text(), "An agent description")
        self.assertEqual(card.findChild(QLabel, "AskUserQuestion").textFormat(), Qt.TextFormat.PlainText)

    def test_translated_label_is_not_an_authorization_value(self):
        self.language(True)
        toolkit, _dock = self.make_dock()
        toolkit.set_ask_user_emitter(lambda *_: toolkit._resolve_ask_user({"choice": "Permitir sempre"}))
        self.assertEqual(
            toolkit.confirm_external_access("web_fetch", {"url": URL}),
            {"ok": False, "error": f"Permission denied: fetch external URL: {URL}", "cancelled": True},
        )
        self.assertFalse(self.config.get("external_access_always_allowed"))


if __name__ == "__main__":
    unittest.main()
