"""Regression checks for readable chart labels separate from grouped values."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.core import QgsApplication, QgsFeature, QgsProject, QgsVectorLayer

from AgenticGis.core.toolkit import QgisToolkit


def _build_layer():
    layer = QgsVectorLayer(
        "Point?field=code:string&field=name:string",
        "coded_categories",
        "memory",
    )
    provider = layer.dataProvider()
    rows = [
        ("A01", "Alpha"),
        ("A01", ""),
        ("B02", "Beta"),
        ("C03", None),
    ]
    features = []
    for row in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def main():
    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        layer = _build_layer()
        toolkit = QgisToolkit(iface=None)

        result = toolkit.create_chart(
            layer.id(),
            "code",
            chart_type="bar",
            label_field="name",
        )

        assert result["ok"] is True
        rows = {row.get("raw_label", row["label"]): row for row in result["data"]}
        assert rows["A01"]["label"] == "Alpha"
        assert rows["A01"]["value"] == 2
        assert rows["B02"]["label"] == "Beta"
        assert rows["C03"]["label"] == "C03"
        assert "raw_label" not in rows["C03"]

        fallback = toolkit.create_chart(
            layer.id(),
            "code",
            chart_type="bar",
            label_field="does_not_exist",
        )
        assert fallback["ok"] is True
        assert {row["label"] for row in fallback["data"]} == {"A01", "B02", "C03"}
    finally:
        QgsProject.instance().clear()
        app.exitQgis()


if __name__ == "__main__":
    main()
