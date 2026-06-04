"""Regression checks for structured vector layer analysis helpers."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.core import QgsApplication, QgsFeature, QgsVectorLayer

from AgenticGis.core.layer_analysis import analyze_vector_layer


def _build_layer():
    layer = QgsVectorLayer(
        "Point?field=id:integer&field=score:double&field=kind:string&field=note:string",
        "analysis_sample",
        "memory",
    )
    provider = layer.dataProvider()
    rows = [
        (1, 10.0, "park", "ready"),
        (2, 20.0, "road", ""),
        (3, None, "park", None),
        (4, 40.0, "water", "checked"),
        (5, 50.0, "park", "ready"),
    ]
    features = []
    for row in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def main():
    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        layer = _build_layer()
        result = analyze_vector_layer(
            layer,
            sample_limit=2,
            scan_limit=3,
            top_limit=2,
        )

        assert result["summary"]["name"] == "analysis_sample"
        assert result["summary"]["feature_count"] == 5
        assert result["summary"]["geometry_type"] == "Point"
        assert [field["name"] for field in result["summary"]["fields"]] == [
            "id",
            "score",
            "kind",
            "note",
        ]

        score_stats = result["field_stats"]["score"]
        assert score_stats["count"] == 2
        assert score_stats["missing"] == 1
        assert score_stats["min"] == 10.0
        assert score_stats["max"] == 20.0
        assert score_stats["sum"] == 30.0
        assert score_stats["mean"] == 15.0
        assert score_stats["provider_min"] == 10.0
        assert score_stats["provider_max"] == 50.0

        kind_counts = result["category_counts"]["kind"]
        assert kind_counts["top_values"] == [
            {"value": "park", "count": 2},
            {"value": "road", "count": 1},
        ]
        assert kind_counts["unique_scanned"] == 2
        assert set(kind_counts["provider_unique_values"]) == {"park", "road", "water"}

        assert result["top_values"]["kind"] == kind_counts["top_values"]
        assert len(result["sample"]) == 2
        assert result["sample"][0]["id"] == 1
        assert result["sample"][1]["kind"] == "road"

        assert result["missing_values"]["score"] == 1
        assert result["missing_values"]["note"] == 1
        assert result["scanned_features"] == 3
        assert result["truncated"] is True
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
