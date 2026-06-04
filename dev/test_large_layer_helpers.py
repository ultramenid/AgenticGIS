"""Regression checks for safe large-layer helpers exposed to run_pyqgis."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.core import QgsApplication

from AgenticGis.core.toolkit import QgisToolkit


def main():
    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        toolkit = QgisToolkit(iface=None)
        result = toolkit.run_pyqgis(
            """
layer = QgsVectorLayer("Point?field=id:integer", "sample", "memory")
provider = layer.dataProvider()
features = []
for i in range(5):
    feature = QgsFeature(layer.fields())
    feature.setAttribute("id", i)
    features.append(feature)
provider.addFeatures(features)
layer.updateExtents()

sample = _sample_features(layer, limit=3, fields=["id"])
iterator = _iterate_features(layer, limit=2, fields=["id"])
result = {
    "sample_count": len(sample),
    "iterator_type": type(iterator).__name__,
    "iterated_count": len(list(iterator)),
}
"""
        )

        assert result["ok"] is True
        text = result["result"]
        assert "'sample_count': 3" in text
        assert "'iterator_type': 'generator'" in text
        assert "'iterated_count': 2" in text
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
