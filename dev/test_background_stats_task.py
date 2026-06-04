"""Regression check for background QgsTask statistics dispatch."""

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
    layer = QgsVectorLayer("Point?field=value:integer", "source", "memory")
    provider = layer.dataProvider()
    features = []
    for i in range(10):
        feature = QgsFeature(layer.fields())
        feature.setAttribute("value", i)
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

        slot = {"result": None, "error": None}

        def worker():
            try:
                slot["result"] = tools.dispatch(
                    toolkit,
                    executor,
                    "get_layer_statistics",
                    {"layer_id": layer.id(), "field_name": "value"},
                )
            except BaseException as exc:  # noqa: BLE001
                slot["error"] = exc
            finally:
                QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        app.exec_()
        thread.join(timeout=5)

        assert slot["error"] is None, slot["error"]
        result = slot["result"]
        assert result["ok"] is True
        stats = result["statistics"]
        assert stats["count"] == 10
        assert stats["min"] == 0.0
        assert stats["max"] == 9.0
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
