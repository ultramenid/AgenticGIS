"""Checks for the gee_dataset_info tool: registration, STAC URL building,
JSON shaping, input validation, and the external-access guard.

The network is stubbed (urllib.request.urlopen is monkeypatched), so this
runs without hitting Google and without an Earth Engine install.

Run inside the QGIS Python:

    QT_QPA_PLATFORM=offscreen \
        /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 \
        AgenticGis/dev/test_gee_dataset_info.py
"""

import io
import json
import os
import sys
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core import tools  # noqa: E402
from AgenticGis.core.toolkit import QgisToolkit  # noqa: E402


def _toolkit():
    return QgisToolkit(iface=None)


# A trimmed but structurally faithful STAC entry (matches the real catalog
# shape: summaries.eo:bands, summaries.gee:schema, extent.temporal.interval).
_FAKE_STAC = {
    "id": "COPERNICUS/S2_SR_HARMONIZED",
    "title": "Harmonized Sentinel-2 MSI: Level-2A (SR)",
    "gee:type": "image_collection",
    "gee:status": "ready",
    "gee:interval": {"type": "revisit_interval", "interval": 5, "unit": "day"},
    "extent": {"temporal": {"interval": [["2017-03-28T00:00:00Z", "2026-06-04T00:00:00Z"]]}},
    "summaries": {
        "eo:bands": [
            {"name": "B2", "description": "Blue", "gsd": 10,
             "center_wavelength": 0.49, "gee:scale": 0.0001},
            {"name": "B4", "description": "Red", "gsd": 10, "gee:scale": 0.0001},
            {"name": "B8", "description": "NIR", "gsd": 10, "gee:scale": 0.0001},
        ],
        "gee:schema": [
            {"name": "CLOUDY_PIXEL_PERCENTAGE", "type": "DOUBLE",
             "description": "Granule-specific cloudy pixel percentage."},
        ],
    },
}


class _FakeResp:
    def __init__(self, payload_bytes):
        self._b = payload_bytes

    def read(self, _n=None):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkey_payload, captured):
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        if isinstance(monkey_payload, Exception):
            raise monkey_payload
        return _FakeResp(monkey_payload)

    urllib.request.urlopen = fake_urlopen


_REAL_URLOPEN = urllib.request.urlopen


def _restore_urlopen():
    urllib.request.urlopen = _REAL_URLOPEN


def test_tool_registered_and_dispatchable():
    names = {spec["name"] for spec in tools.TOOL_SPECS}
    assert "gee_dataset_info" in names, "gee_dataset_info missing from TOOL_SPECS"
    spec = tools.TOOL_BY_NAME["gee_dataset_info"]
    assert hasattr(QgisToolkit, spec["method"]), spec["method"]
    props = spec["input_schema"]["properties"]
    assert "dataset_id" in props
    assert spec["input_schema"]["required"] == ["dataset_id"]


def test_empty_dataset_id_errors_without_network():
    tk = _toolkit()
    res = tk.gee_dataset_info("")
    assert res["ok"] is False
    assert "dataset_id" in res["error"]
    res2 = tk.gee_dataset_info("   ")
    assert res2["ok"] is False


def test_builds_correct_stac_url_and_shapes_payload():
    tk = _toolkit()
    captured = {}
    _patch_urlopen(json.dumps(_FAKE_STAC).encode("utf-8"), captured)
    try:
        res = tk.gee_dataset_info("COPERNICUS/S2_SR_HARMONIZED")
    finally:
        _restore_urlopen()
    assert (
        captured["url"]
        == "https://storage.googleapis.com/earthengine-stac/catalog/"
        "COPERNICUS/COPERNICUS_S2_SR_HARMONIZED.json"
    ), captured["url"]
    assert res["ok"] is True
    assert res["id"] == "COPERNICUS/S2_SR_HARMONIZED"
    assert res["type"] == "image_collection"
    assert res["deprecated"] is False
    assert res["band_names"] == ["B2", "B4", "B8"]
    assert res["bands"][0]["scale"] == 0.0001
    assert res["date_range"][0].startswith("2017-03-28")
    assert res["properties"][0]["name"] == "CLOUDY_PIXEL_PERCENTAGE"


def test_nested_id_underscores_the_full_path():
    tk = _toolkit()
    captured = {}
    _patch_urlopen(json.dumps(_FAKE_STAC).encode("utf-8"), captured)
    try:
        tk.gee_dataset_info("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    finally:
        _restore_urlopen()
    assert captured["url"].endswith(
        "/GOOGLE/GOOGLE_CLOUD_SCORE_PLUS_V1_S2_HARMONIZED.json"
    ), captured["url"]


def test_deprecated_flag_from_status():
    tk = _toolkit()
    payload = dict(_FAKE_STAC)
    payload["gee:status"] = "deprecated"
    captured = {}
    _patch_urlopen(json.dumps(payload).encode("utf-8"), captured)
    try:
        res = tk.gee_dataset_info("LANDSAT/LC08/C01/T1")
    finally:
        _restore_urlopen()
    assert res["ok"] is True
    assert res["deprecated"] is True


def test_404_gives_helpful_error():
    tk = _toolkit()
    err = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b"")
    )
    captured = {}
    _patch_urlopen(err, captured)
    try:
        res = tk.gee_dataset_info("NOT/A/REAL/DATASET")
    finally:
        _restore_urlopen()
    assert res["ok"] is False
    assert "No Earth Engine dataset found" in res["error"]


class _FakeEEData:
    def __init__(self, asset):
        self._asset = asset

    def getAsset(self, asset_id):  # noqa: N802 (mirror ee API)
        if self._asset is None:
            raise RuntimeError("Asset not found: " + asset_id)
        return self._asset


class _FakeEEModule:
    """Minimal stand-in for the `ee` module, injected into sys.modules."""

    def __init__(self, asset):
        self.data = _FakeEEData(asset)

    def Initialize(self, *a, **k):  # noqa: N802
        return None


def _with_fake_ee(asset):
    """Install a fake `ee` module so _gee_asset_info_via_ee can import it."""
    import types

    mod = types.ModuleType("ee")
    fake = _FakeEEModule(asset)
    mod.data = fake.data
    mod.Initialize = fake.Initialize
    sys.modules["ee"] = mod


def _remove_fake_ee():
    sys.modules.pop("ee", None)


def test_404_falls_back_to_user_asset_via_ee():
    tk = _toolkit()
    err = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b"")
    )
    captured = {}
    _patch_urlopen(err, captured)
    _with_fake_ee(
        {
            "type": "IMAGE",
            "name": "projects/my-proj/assets/my_image",
            "id": "projects/my-proj/assets/my_image",
            "bands": [
                {"id": "elevation", "dataType": {"precision": "INT"}},
                {"id": "slope"},
            ],
            "properties": {"description": "my DEM", "year": 2024},
            "startTime": "2024-01-01T00:00:00Z",
            "endTime": "2024-12-31T00:00:00Z",
        }
    )
    try:
        res = tk.gee_dataset_info("projects/my-proj/assets/my_image")
    finally:
        _restore_urlopen()
        _remove_fake_ee()
    assert res["ok"] is True
    assert res["source"] == "asset"
    assert res["type"] == "image"
    assert res["band_names"] == ["elevation", "slope"]
    assert res["properties"]["year"] == 2024
    assert res["date_range"] == ["2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z"]


def test_404_with_unreadable_asset_returns_helpful_error():
    tk = _toolkit()
    err = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b"")
    )
    captured = {}
    _patch_urlopen(err, captured)
    _with_fake_ee(None)  # getAsset raises -> fallback returns None
    try:
        res = tk.gee_dataset_info("projects/my-proj/assets/missing")
    finally:
        _restore_urlopen()
        _remove_fake_ee()
    assert res["ok"] is False
    assert "your own asset" in res["error"]


def test_catalog_result_tagged_source_catalog():
    tk = _toolkit()
    captured = {}
    _patch_urlopen(json.dumps(_FAKE_STAC).encode("utf-8"), captured)
    try:
        res = tk.gee_dataset_info("COPERNICUS/S2_SR_HARMONIZED")
    finally:
        _restore_urlopen()
    assert res["source"] == "catalog"


def test_external_access_guard_fires_for_dataset_info():
    tk = _toolkit()
    reason = tk._external_access_reason(
        "gee_dataset_info", {"dataset_id": "COPERNICUS/S2_SR_HARMONIZED"}
    )
    assert reason is not None
    assert "STAC" in reason
    # No dataset id -> no external access reason.
    assert tk._external_access_reason("gee_dataset_info", {}) is None


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
