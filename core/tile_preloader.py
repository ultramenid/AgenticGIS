"""Pre-fetch map tiles for loaded layers and warm the QNetworkDiskCache.

Supports XYZ, WMS, and GEE ee_plugin tile layers. Computes tile coordinates
from a layer’s extent, generates the corresponding tile URLs, and fetches
them through the global QgsNetworkAccessManager so they are stored in the
current QNetworkDiskCache (including our ForcedNetworkDiskCache wrapper).

Stdlib + PyQGIS only.
"""

import math
import time

from qgis.PyQt.QtCore import QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsMessageLog,
    QgsNetworkAccessManager,
    QgsProject,
)


# ── helpers ───────────────────────────────────────────────────────────────


def _log(msg):
    try:
        QgsMessageLog.logMessage(f"[AgenticGIS TilePreloader] {msg}", "AgenticGIS")
    except Exception:  # nosec B110
        pass


def _extract_url_template(layer):
    """Try to find a tile URL template from a loaded layer."""
    uri = layer.source()
    provider = layer.providerType()

    # XYZ / GEE ee_plugin → URL contains {z} {x} {y}
    if "{z}" in uri and "{x}" in uri and "{y}" in uri:
        # raw URI may be wrapped as type=xyz&url=<template>
        if "type=xyz&url=" in uri:
            return uri.split("type=xyz&url=", 1)[-1]
        return uri

    # GEE ee_plugin layers via QgsRasterLayer often have a data provider
    # that stores the tile URL internally. Try to get it from the provider.
    if provider == "wms":
        dp = layer.dataProvider()
        if dp is not None:
            try:
                # Some providers expose the base URL via metadata
                meta = dp.htmlMetadata()  # type: ignore[attr-defined]
                # Best-effort scan for a tile-looking URL
                if "earthengine" in meta.lower():
                    for line in meta.splitlines():
                        if "http" in line and "{" in line and "}" in line:
                            return line.strip()
            except Exception:  # nosec B110
                pass
        # Fallback: parse the WMS URI for a GetMap endpoint
        if "url=" in uri:
            return uri.split("url=", 1)[-1].split("&")[0]

    return None


def _tile_range_for_extent(extent_4326, zoom):
    """Return (min_x, max_x, min_y, max_y) tile indices for a bbox at zoom."""
    min_lon = max(extent_4326.xMinimum(), -180.0)
    max_lon = min(extent_4326.xMaximum(), 180.0)
    min_lat = max(extent_4326.yMinimum(), -85.05112878)
    max_lat = min(extent_4326.yMaximum(), 85.05112878)

    def _lon2tilex(lon, z):
        return int((lon + 180.0) / 360.0 * (1 << z))

    def _lat2tiley(lat, z):
        lat_rad = math.radians(lat)
        return int(
            (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (1 << z)
        )

    min_x = _lon2tilex(min_lon, zoom)
    max_x = _lon2tilex(max_lon, zoom)
    min_y = _lat2tiley(max_lat, zoom)  # y increases southward
    max_y = _lat2tiley(min_lat, zoom)
    return min_x, max_x, min_y, max_y


def _build_tile_url(template, z, x, y):
    """Substitute {z}/{x}/{y} in a URL template."""
    url = template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    # Some templates use {-y} for TMS convention (flip Y)
    max_y = (1 << z) - 1
    url = url.replace("{-y}", str(max_y - y))
    return url


# ── main public API ───────────────────────────────────────────────────────


def warm_cache_for_layer(layer_id, zoom_levels=None, max_tiles=500, feedback=None):
    """Pre-fetch tiles for *layer_id* and store them in the NAM disk cache.

    Parameters
    ----------
    layer_id : str
        QGIS layer ID.
    zoom_levels : list[int] | None
        Zoom levels to warm.  ``None`` defaults to the layer’s native zoom
        range or ``[current_zoom - 1, current_zoom, current_zoom + 1]``.
    max_tiles : int
        Hard safety limit.  When the computed tile count exceeds this,
        the function returns an error without fetching anything.
    feedback : QgsFeedback | None
        Optional feedback object for cancellation checks.

    Returns
    -------
    dict
        {"ok": True|False, "fetched": int, "total": int, "skipped": int,
         "error": str|None}
    """
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        return {"ok": False, "error": f"Layer not found: {layer_id!r}"}

    url_template = _extract_url_template(layer)
    if url_template is None:
        return {
            "ok": False,
            "error": (
                "Could not extract a tile URL template from this layer. "
                "Only XYZ / WMS / GEE tile layers are supported."
            ),
        }

    # Determine zoom levels
    if zoom_levels is None or not zoom_levels:
        # Default: try to detect from layer or use a sensible range
        canvas = None
        try:
            from qgis.utils import iface
            canvas = iface.mapCanvas()
        except Exception:  # nosec B110
            pass
        if canvas is not None:
            z = int(canvas.scale())  # rough proxy – not exact zoom
            zoom_levels = [max(0, z - 1), z, z + 1]
        else:
            zoom_levels = [5, 6, 7]

    # Compute extent in EPSG:4326
    extent = layer.extent()
    crs = layer.crs()
    extent_4326 = extent
    if crs.isValid() and crs.authid() != "EPSG:4326":
        try:
            transform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            extent_4326 = transform.transformBoundingBox(extent)
        except QgsCsException:
            _log(f"CRS transform failed for {layer.name()}, using raw extent")

    # Build tile URL list
    tiles = []
    for z in zoom_levels:
        min_x, max_x, min_y, max_y = _tile_range_for_extent(extent_4326, z)
        # Clamp to world bounds
        max_x = min(max_x, (1 << z) - 1)
        max_y = min(max_y, (1 << z) - 1)
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                tiles.append((z, x, y))

    total = len(tiles)
    if total > max_tiles:
        return {
            "ok": False,
            "error": (
                f"Too many tiles to preload ({total} > {max_tiles} limit). "
                f"Try fewer zoom_levels or a smaller area."
            ),
        }

    if total == 0:
        return {"ok": True, "fetched": 0, "total": 0, "skipped": 0}

    # Fetch tiles
    nam = QgsNetworkAccessManager.instance()
    fetched = 0
    skipped = 0

    _log(f"Starting preload for '{layer.name()}': {total} tiles, zooms {zoom_levels}")

    for z, x, y in tiles:
        if feedback is not None and feedback.isCanceled():
            _log("Cancelled by feedback")
            break

        url = _build_tile_url(url_template, z, x, y)
        if not url.startswith(("http://", "https://")):
            skipped += 1
            continue

        req = QNetworkRequest(QUrl(url))
        reply = nam.get(req)

        # Synchronous-ish wait with event-loop processing (keeps UI alive-ish)
        loop = QEventLoop()
        reply.finished.connect(loop.quit)
        # Use a simple polling approach instead of nested event loops to avoid
        # dead-locking in some QGIS contexts.
        deadline = time.time() + 5.0
        while not reply.isFinished() and time.time() < deadline:
            loop.processEvents()
            time.sleep(0.01)

        if reply.error() == reply.NoError:
            fetched += 1
        else:
            skipped += 1

        reply.deleteLater()

    _log(f"Preload finished: {fetched} fetched, {skipped} skipped / {total} total")
    return {"ok": True, "fetched": fetched, "total": total, "skipped": skipped}
