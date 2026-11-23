"""Offline checks for localized widget presentation and unchanged exports."""

from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qgis.PyQt.QtCore import QCoreApplication, QLocale, Qt, QTranslator
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QLabel, QPushButton, QWidget
from qgis.core import QgsApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

downloadable = import_module(f"{ROOT.name}.gui.downloadable")
formatting = import_module(f"{ROOT.name}.gui.formatting")
message_bubble = import_module(f"{ROOT.name}.gui.message_bubble")
theme = import_module(f"{ROOT.name}.gui.theme")
AgentTurnBubble = import_module(f"{ROOT.name}.gui.agent_turn_bubble").AgentTurnBubble
GifWidget = import_module(f"{ROOT.name}.gui.gif_widget").GifWidget
SessionPopup = import_module(f"{ROOT.name}.gui.session_popup").SessionPopup
StatsWidget = import_module(f"{ROOT.name}.gui.stats_widget").StatsWidget


class PresentationTests(unittest.TestCase):
    def test_generated_chart_titles_preserve_wire_values(self):
        import json

        from AgenticGIS.core.toolkit import _chart_from_data, _chart_title
        from AgenticGIS.gui.backend_feedback import display_backend_feedback

        generated = _chart_from_data([{"label": "A", "value": 1}], "bar", [], None)
        custom = _chart_from_data([{"label": "A", "value": 1}], "bar", [], "Chart")
        self.assertEqual(json.dumps(generated), json.dumps(custom))
        self.assertEqual(display_backend_feedback(generated["title"]), "Gráfico")
        self.assertEqual(display_backend_feedback(custom["title"]), "Chart")
        title = _chart_title("region", "Rios", "area", "sum")
        self.assertEqual(json.loads(json.dumps(title)), "sum of area by region in Rios")
        self.assertEqual(display_backend_feedback(title), "soma de area por region em Rios")

    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], True)

    def setUp(self):
        translator = QTranslator()
        self.assertTrue(translator.load(str(ROOT / "i18n" / "AgenticGIS_pt_BR.qm")))
        self.assertTrue(QCoreApplication.installTranslator(translator))
        self.addCleanup(QCoreApplication.removeTranslator, translator)

    def test_dates_counts_and_tool_names(self):
        now = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
        cases = (
            (timedelta(seconds=10), "agora mesmo"),
            (timedelta(minutes=1), "há 1 min"),
            (timedelta(minutes=5), "há 5 min"),
            (timedelta(hours=5), "há 5 h"),
            (timedelta(days=5), "há 5 d"),
            (timedelta(days=8), "04/11/2026"),
        )
        for delta, display in cases:
            with self.subTest(delta=delta):
                self.assertEqual(formatting.format_relative_time((now - delta).isoformat(), now), display)
        self.assertEqual(formatting.summarize_tool_result("[]"), "0 itens")
        self.assertEqual(formatting.summarize_tool_result("[1]"), "1 item")
        self.assertEqual(formatting.summarize_tool_result("[1, 2]"), "2 itens")
        self.assertEqual(formatting.humanize_tool_name("mcp__agenticgis__list_layers"), "Listar camadas")
        self.assertEqual(formatting.humanize_tool_name("mcp__external__list_layers"), "List layers")
        self.assertEqual(formatting.summarize_tool_result('{"ok":false,"error":"Custom error"}'), "Custom error")

    def test_sessions_at_narrow_width_and_font_scales(self):
        original_scale = theme._user_scale
        self.addCleanup(setattr, theme, "_user_scale", original_scale)
        anchor = QPushButton("Sessions")
        anchor.show()
        self.addCleanup(anchor.close)
        for scale in (0.75, 1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                theme.set_font_scale(scale)
                selected = []
                popup = SessionPopup(
                    [{"id": "old", "name": "New session", "updated_at": "2026-10-01"}], "old",
                    lambda sid: selected.append(("open", sid)), lambda: selected.append(("new", "")),
                    lambda sid: selected.append(("rename", sid)), lambda sid: selected.append(("delete", sid)),
                    lambda size: f"{size} B", anchor,
                )
                try:
                    popup.show_below(anchor, 260)
                    self.app.processEvents()
                    self.assertEqual(popup.width(), 260)
                    self.assertEqual(popup.search.placeholderText(), "Buscar sessões…")
                    self.assertGreaterEqual(
                        popup.search.width(),
                        popup.search.fontMetrics().horizontalAdvance(popup.search.placeholderText()) + 20,
                    )
                    row = popup._rows[0]
                    self.assertEqual(row.name, "New session")
                    self.assertIs(self.app.focusWidget(), popup.search)
                    QTest.keyClick(popup.search, Qt.Key.Key_Tab)
                    self.assertIs(self.app.focusWidget(), popup._new_button)
                    QTest.keyClick(popup._new_button, Qt.Key.Key_Tab)
                    self.assertIs(self.app.focusWidget(), row)
                    QTest.keyClick(row, Qt.Key.Key_Tab)
                    rename = row._actions[0]
                    self.assertIs(self.app.focusWidget(), rename)
                    self.assertEqual(rename.accessibleName(), "Renomear sessão")
                    QTest.keyClick(rename, Qt.Key.Key_Space)
                    self.assertEqual(selected, [("rename", "old")])
                finally:
                    popup.close()

    def test_result_controls_and_raw_stats_export(self):
        turn = AgentTurnBubble()
        self.addCleanup(turn.close)
        turn.set_footer()
        row = turn.add_tool("list_layers", {})
        self.assertEqual(row._summary_lbl.text(), "Executando…")
        row.set_result("")
        self.assertEqual(row._summary_lbl.text(), "Concluído")
        self.assertIn("Copiar resposta como Markdown", [b.toolTip() for b in turn.findChildren(QPushButton)])

        untrusted_choice = "<b>Always allow</b>"
        turn.set_user_decision(untrusted_choice)
        decision = turn._user_decision_lbl
        self.assertEqual(decision.text(), f"Escolha do usuário: {untrusted_choice}")
        self.assertEqual(decision.textFormat(), Qt.TextFormat.PlainText)

        stats = StatsWidget({"layer_name": "<b>Rios</b>", "total_features": 3, "distinct_count": 2, "mean": 2.5})
        self.addCleanup(stats.close)
        labels = {label.text(): label for label in stats.findChildren(QLabel)}
        self.assertIn("Feições", labels)
        self.assertIn("Distintos", labels)
        self.assertIn(f"2{QLocale().decimalPoint()}50", labels)
        self.assertEqual(labels["<b>Rios</b>"].textFormat(), Qt.TextFormat.PlainText)
        stats_module = import_module(f"{ROOT.name}.gui.stats_widget")
        with patch.object(stats_module, "save_csv") as save:
            stats._save()
            self.assertEqual(save.call_args.args[1], [
                ["Metric", "Value"], ["Features", "3"], ["Distinct", "2"], ["Mean", "2.50"],
            ])

        gif = GifWidget({})
        self.addCleanup(gif.close)
        self.assertEqual(gif._animation_label.text(), "A animação não está mais disponível.")

    def test_save_filters_and_markdown_bytes(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "response.md"
            original = "**Texto**\n<link>\n"
            with patch.object(downloadable.QFileDialog, "getSaveFileName", return_value=(str(path), "")) as dialog:
                downloadable.save_text(parent, original)
                self.assertEqual(dialog.call_args.args[1:], (
                    "Salvar", "response.md", "Markdown (*.md);;Texto (*.txt)",
                ))
                self.assertEqual(path.read_bytes(), original.encode("utf-8"))
                dialog.return_value = (str(Path(temp) / "image"), "")
                downloadable.save_widget_png(parent, parent)
                self.assertEqual(dialog.call_args.args[1:], ("Salvar", "chart.png", "Imagem PNG (*.png)"))
                self.assertTrue((Path(temp) / "image.png").is_file())
                dialog.return_value = (str(Path(temp) / "data.csv"), "")
                downloadable.save_csv(parent, [])
                self.assertEqual(dialog.call_args.args[1:], ("Salvar", "data.csv", "CSV (*.csv)"))
            with patch.object(downloadable.QMessageBox, "warning") as warning:
                downloadable.save_file_copy(parent, "", "missing.gif", "GIF (*.gif)")
                self.assertEqual(warning.call_args.args[1:], (
                    "Falha ao salvar", "O arquivo de origem não está mais disponível.",
                ))
        menu = message_bubble._build_code_context_menu(parent, ["code 1", "code 2"])
        self.assertEqual([action.text() for action in menu.actions()], [
            "Bloco de código", "Copiar bloco de código 1", "Copiar bloco de código 2",
        ])


if __name__ == "__main__":
    unittest.main()
