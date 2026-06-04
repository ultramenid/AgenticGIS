"""Regression checks for auto-zoom, analysis-layer tracking, and GEE tools.

Run inside the QGIS Python:

    QT_QPA_PLATFORM=offscreen \
        /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 \
        AgenticGis/dev/test_layer_zoom_and_gee.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import math  # noqa: E402

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
)

from AgenticGis.core import tools  # noqa: E402
from AgenticGis.core.toolkit import QgisToolkit  # noqa: E402

MEM_URI = "Point?crs=EPSG:4326&field=id:integer"


class _FakeMapSettings:
    def destinationCrs(self):
        return QgsCoordinateReferenceSystem("EPSG:4326")


class _FakeCanvas:
    def __init__(self):
        self.set_extent_calls = 0
        self.refresh_calls = 0

    def mapSettings(self):
        return _FakeMapSettings()

    def setExtent(self, _extent):
        self.set_extent_calls += 1

    def refresh(self):
        self.refresh_calls += 1


class _FakeIface:
    def __init__(self):
        self.canvas = _FakeCanvas()

    def mapCanvas(self):
        return self.canvas


def _toolkit():
    return QgisToolkit(iface=None)


class _FakeEE:
    """Minimal ee stand-in: records construction without a real backend."""

    class Geometry:
        def __init__(self, geojson):
            self.geojson = geojson

        @staticmethod
        def Rectangle(coords):
            return {"type": "rect", "coords": coords}

    class Feature:
        def __init__(self, geom, attrs):
            self.geom = geom
            self.attrs = attrs

    class FeatureCollection:
        def __init__(self, feats):
            self.feats = list(feats)

        def geometry(self):
            return {"type": "union", "count": len(self.feats)}


def _square_wkt(x=0.0, y=0.0, s=1.0):
    return (
        f"POLYGON(({x} {y},{x} {y + s},{x + s} {y + s},"
        f"{x + s} {y},{x} {y}))"
    )


def _dense_circle_wkt(n=100, cx=10.0, cy=10.0, r=1.0):
    pts = [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    pts.append(pts[0])
    return "POLYGON((" + ",".join(f"{x} {y}" for x, y in pts) + "))"


def _poly_layer(wkts, name="aoi"):
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:4326&field=id:integer&field=name:string", name, "memory"
    )
    pr = layer.dataProvider()
    feats = []
    for i, wkt in enumerate(wkts):
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        f.setAttributes([i, f"poly{i}"])
        feats.append(f)
    pr.addFeatures(feats)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def test_add_layer_marks_and_tracks_analysis_layer():
    QgsProject.instance().clear()
    tk = _toolkit()
    res = tk.add_layer(MEM_URI, name="ndvi_zones", provider="memory", is_analysis=True)
    assert res["ok"] is True, res
    assert res["is_analysis"] is True
    layer = QgsProject.instance().mapLayer(res["layer_id"])
    assert layer is not None
    assert layer.customProperty("agenticgis/analysis") in (True, "true", 1)
    assert tk._analysis_layers["ndvi_zones"] == res["layer_id"]


def test_add_layer_reuses_analysis_layer_by_name():
    QgsProject.instance().clear()
    tk = _toolkit()
    first = tk.add_layer(MEM_URI, name="result", provider="memory", is_analysis=True)
    second = tk.add_layer(MEM_URI, name="result", provider="memory", is_analysis=True)
    # The old layer with the same logical name is removed, not stacked.
    assert QgsProject.instance().mapLayer(first["layer_id"]) is None
    assert QgsProject.instance().mapLayer(second["layer_id"]) is not None
    same_name = [
        l for l in QgsProject.instance().mapLayers().values() if l.name() == "result"
    ]
    assert len(same_name) == 1, [l.name() for l in same_name]


def test_add_layer_without_analysis_does_not_track():
    QgsProject.instance().clear()
    tk = _toolkit()
    res = tk.add_layer(MEM_URI, name="plain", provider="memory")
    assert res["ok"] is True
    assert res["is_analysis"] is False
    assert "plain" not in tk._analysis_layers


def test_add_layer_does_not_zoom_by_default():
    QgsProject.instance().clear()
    tk = _toolkit()
    zoom_calls = []
    tk._zoom_to_layer = lambda layer: zoom_calls.append(layer) or True

    res = tk.add_layer(MEM_URI, name="copy", provider="memory")

    assert res["ok"] is True
    assert res["zoomed"] is False
    assert zoom_calls == []
    assert tk._canvas_dirty is False


def test_explicit_zoom_does_not_force_immediate_canvas_refresh():
    QgsProject.instance().clear()
    iface = _FakeIface()
    tk = QgisToolkit(iface=iface)
    source = _poly_layer([_square_wkt(0, 0)], name="source")

    zoomed = tk._zoom_to_layer(source)

    assert zoomed is True
    assert iface.canvas.set_extent_calls == 1
    assert iface.canvas.refresh_calls == 0


def test_zoom_to_layer_handles_missing_canvas_gracefully():
    QgsProject.instance().clear()
    tk = _toolkit()  # iface=None -> no canvas
    added = tk.add_layer(MEM_URI, name="z", provider="memory")
    res = tk.zoom_to_layer(layer_id=added["layer_id"])
    # Layer exists, so ok is True even though there is no canvas to zoom.
    assert res["ok"] is True
    assert res["zoomed"] is False
    assert "note" in res


def test_zoom_to_layer_unknown_id_errors():
    QgsProject.instance().clear()
    tk = _toolkit()
    res = tk.zoom_to_layer(layer_id="does-not-exist")
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_gee_status_returns_structured_report():
    tk = _toolkit()
    res = tk.gee_status()
    assert res["ok"] is True
    for key in ("plugin_installed", "ee_available", "initialized", "authenticated"):
        assert key in res and isinstance(res[key], bool)
    assert isinstance(res["message"], str)


def test_gee_add_layer_without_ee_returns_helpful_error():
    tk = _toolkit()
    res = tk.gee_add_layer(code="result = ee.Image(1)", name="x")
    # ee / ee_plugin are absent in the test interpreter -> graceful failure.
    if not res["ok"]:
        assert "earth engine" in res["error"].lower() or "ee_plugin" in res["error"].lower()


def test_new_tools_registered_and_dispatchable():
    names = {spec["name"] for spec in tools.TOOL_SPECS}
    for name in ("zoom_to_layer", "gee_status", "gee_add_layer"):
        assert name in names, f"{name} missing from TOOL_SPECS"
        spec = tools.TOOL_BY_NAME[name]
        assert hasattr(QgisToolkit, spec["method"]), spec["method"]
    # add_layer schema exposes the new flags.
    add_props = tools.TOOL_BY_NAME["add_layer"]["input_schema"]["properties"]
    assert "is_analysis" in add_props
    assert "zoom" in add_props
    assert add_props["zoom"]["default"] is False


def test_ee_inputs_exact_builds_featurecollection():
    QgsProject.instance().clear()
    tk = _toolkit()
    layer = _poly_layer([_square_wkt(0, 0), _square_wkt(2, 2)])
    info = tk._ee_inputs_from_layer(_FakeEE, layer.id(), geometry_mode="auto")
    assert "error" not in info and not info.get("needs_decision"), info
    assert info["mode_used"] == "exact"
    assert info["feature_count"] == 2
    assert isinstance(info["features"], _FakeEE.FeatureCollection)
    # Attributes are carried onto each ee.Feature.
    assert info["features"].feats[0].attrs["name"] == "poly0"


def test_ee_inputs_bbox_mode_uses_rectangle():
    QgsProject.instance().clear()
    tk = _toolkit()
    layer = _poly_layer([_square_wkt(0, 0)])
    info = tk._ee_inputs_from_layer(_FakeEE, layer.id(), geometry_mode="bbox")
    assert info["mode_used"] == "bbox"
    assert info["features"] is None
    assert info["region"]["type"] == "rect"


def test_ee_inputs_auto_asks_when_too_many_features():
    QgsProject.instance().clear()
    tk = _toolkit()
    layer = _poly_layer([_square_wkt(i, i) for i in range(5)])
    info = tk._ee_inputs_from_layer(
        _FakeEE, layer.id(), geometry_mode="auto", max_features=2
    )
    assert info.get("needs_decision") is True
    assert info["reason"] == "too_many_features"
    assert info["feature_count"] == 5


def test_ee_inputs_auto_asks_when_geometry_too_large():
    QgsProject.instance().clear()
    tk = _toolkit()
    layer = _poly_layer([_dense_circle_wkt(n=120)])
    info = tk._ee_inputs_from_layer(
        _FakeEE, layer.id(), geometry_mode="auto", max_vertices=20
    )
    assert info.get("needs_decision") is True
    assert info["reason"] == "geometry_too_large"


def test_ee_inputs_simplify_reduces_vertices():
    QgsProject.instance().clear()
    tk = _toolkit()
    layer = _poly_layer([_dense_circle_wkt(n=200)])
    info = tk._ee_inputs_from_layer(
        _FakeEE, layer.id(), geometry_mode="simplify", max_vertices=40
    )
    # Either it fit under the budget, or it honestly reports it could not.
    if info.get("needs_decision"):
        assert info["reason"] == "geometry_too_large_after_simplify"
    else:
        assert info["mode_used"] == "simplified"
        assert info["vertex_count"] <= 40


def test_gee_decision_payload_shapes_ask_user_retry():
    tk = _toolkit()
    payload = tk._gee_decision_payload(
        {"needs_decision": True, "reason": "geometry_too_large",
         "vertex_count_at_least": 9999, "max_vertices": 5000, "feature_count": 1}
    )
    assert payload["ok"] is False
    assert payload["needs_decision"] is True
    assert payload["options"] == ["bbox", "simplify", "exact"]
    assert "geometry_mode" in payload["message"]


def test_gee_add_layer_spec_exposes_geometry_mode():
    props = tools.TOOL_BY_NAME["gee_add_layer"]["input_schema"]["properties"]
    assert props["geometry_mode"]["enum"] == ["auto", "exact", "simplify", "bbox"]
    assert "max_vertices" in props and "max_features" in props


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
