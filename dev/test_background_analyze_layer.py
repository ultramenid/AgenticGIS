"""Regression check for background structured layer analysis dispatch."""

import os
import sys
import tempfile
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtCore import QMetaObject, Qt
from qgis.core import (
    QgsApplication,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from AgenticGis.core import tools
from AgenticGis.core.executor import MainThreadExecutor
from AgenticGis.core.toolkit import QgisToolkit


def _write_test_gpkg(path):
    layer = QgsVectorLayer(
        "Point?field=value:integer&field=kind:string",
        "source",
        "memory",
    )
    provider = layer.dataProvider()
    features = []
    rows = [(1, "a"), (2, "a"), (3, "b")]
    for value, kind in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([value, kind])
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "source"
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        path,
        QgsCoordinateTransformContext(),
        options,
    )
    assert result[0] == QgsVectorFileWriter.NoError, result


def main():
    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        executor = MainThreadExecutor()
        toolkit = QgisToolkit(iface=None)

        fd, path = tempfile.mkstemp(suffix=".gpkg")
        os.close(fd)
        os.unlink(path)
        _write_test_gpkg(path)

        layer = QgsVectorLayer(f"{path}|layername=source", "source", "ogr")
        assert layer.isValid()
        QgsProject.instance().addMapLayer(layer)

        slot = {"results": [], "error": None}

        def worker():
            try:
                args = {
                    "layer_id": layer.id(),
                    "analysis_type": "auto",
                    "fields": ["value", "kind"],
                    "sample_limit": 2,
                    "scan_limit": 100,
                    "top_limit": 2,
                }
                slot["results"].append(tools.dispatch(toolkit, executor, "analyze_layer", args))
                slot["results"].append(tools.dispatch(toolkit, executor, "analyze_layer", args))
            except BaseException as exc:  # noqa: BLE001
                slot["error"] = exc
            finally:
                QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        app.exec_()
        thread.join(timeout=5)

        assert slot["error"] is None, slot["error"]
        first, second = slot["results"]
        assert first["ok"] is True, first
        assert first["cached"] is False, first
        assert second["ok"] is True, second
        assert second["cached"] is True, second
        assert first["summary"]["name"] == "source"
        assert first["field_stats"]["value"]["count"] == 3
        assert first["category_counts"]["kind"]["top_values"][0] == {
            "value": "a",
            "count": 2,
        }
        assert len(first["sample"]) == 2
        assert first["truncated"] is False
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
