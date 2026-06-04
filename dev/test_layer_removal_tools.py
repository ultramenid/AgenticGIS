"""Regression checks for chat-driven layer removal tools."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer

from AgenticGis.core import tools
from AgenticGis.core.toolkit import QgisToolkit


def _add_memory_layer(name):
    layer = QgsVectorLayer("Point?field=id:integer", name, "memory")
    assert layer.isValid()
    QgsProject.instance().addMapLayer(layer)
    return layer


def test_remove_layer_by_id_unloads_only_that_layer():
    project = QgsProject.instance()
    project.clear()
    first = _add_memory_layer("keep_me")
    second = _add_memory_layer("remove_me")
    first_id = first.id()
    second_id = second.id()
    toolkit = QgisToolkit(iface=None)

    result = toolkit.remove_layer(layer_id=second_id)

    assert result["ok"] is True
    assert result["removed_count"] == 1
    assert result["removed"][0]["id"] == second_id
    assert result["removed"][0]["name"] == "remove_me"
    assert result["remaining_count"] == 1
    assert project.mapLayer(first_id) is not None
    assert project.mapLayer(second_id) is None


def test_remove_layer_by_exact_name():
    project = QgsProject.instance()
    project.clear()
    layer = _add_memory_layer("roads")
    layer_id = layer.id()
    toolkit = QgisToolkit(iface=None)

    result = toolkit.remove_layer(layer_name="roads")

    assert result["ok"] is True
    assert result["removed_count"] == 1
    assert result["removed"][0]["id"] == layer_id
    assert project.mapLayer(layer_id) is None


def test_remove_layer_by_duplicate_name_refuses_without_id():
    project = QgsProject.instance()
    project.clear()
    first = _add_memory_layer("duplicate")
    second = _add_memory_layer("duplicate")
    toolkit = QgisToolkit(iface=None)

    result = toolkit.remove_layer(layer_name="duplicate")

    assert result["ok"] is False
    assert "multiple" in result["error"].lower()
    assert {item["id"] for item in result["matches"]} == {first.id(), second.id()}
    assert project.mapLayer(first.id()) is not None
    assert project.mapLayer(second.id()) is not None


def test_clear_layers_requires_confirmation():
    project = QgsProject.instance()
    project.clear()
    layer = _add_memory_layer("protected")
    toolkit = QgisToolkit(iface=None)

    result = toolkit.clear_layers()

    assert result["ok"] is False
    assert "confirm" in result["error"].lower()
    assert project.mapLayer(layer.id()) is not None


def test_clear_layers_removes_all_loaded_layers_when_confirmed():
    project = QgsProject.instance()
    project.clear()
    first = _add_memory_layer("a")
    second = _add_memory_layer("b")
    layer_ids = {first.id(), second.id()}
    toolkit = QgisToolkit(iface=None)

    result = toolkit.clear_layers(confirm=True)

    assert result["ok"] is True
    assert result["removed_count"] == 2
    assert {item["id"] for item in result["removed"]} == layer_ids
    assert result["remaining_count"] == 0
    assert len(project.mapLayers()) == 0


def test_tool_specs_expose_layer_removal():
    by_name = {spec["name"]: spec for spec in tools.TOOL_SPECS}

    assert by_name["remove_layer"]["method"] == "remove_layer"
    assert "layer_id" in by_name["remove_layer"]["input_schema"]["properties"]
    assert "layer_name" in by_name["remove_layer"]["input_schema"]["properties"]
    assert by_name["clear_layers"]["method"] == "clear_layers"
    assert by_name["clear_layers"]["input_schema"]["required"] == ["confirm"]


def main():
    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        test_remove_layer_by_id_unloads_only_that_layer()
        test_remove_layer_by_exact_name()
        test_remove_layer_by_duplicate_name_refuses_without_id()
        test_clear_layers_requires_confirmation()
        test_clear_layers_removes_all_loaded_layers_when_confirmed()
        test_tool_specs_expose_layer_removal()
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
