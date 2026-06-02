"""The single source of QGIS capability that all agent backends drive.

Every method here assumes it is running on the QGIS **main thread**. Callers
on worker threads must wrap invocations in ``MainThreadExecutor.run_sync``.
``run_pyqgis`` is the catch-all that gives the agent access to every QGIS
feature and every installed plugin; the other methods are convenience/
introspection helpers that keep common requests cheap and reliable.
"""

import io
import traceback
from contextlib import redirect_stderr, redirect_stdout

from qgis.core import Qgis
from qgis.core import (
    QgsApplication,
    QgsFeatureRequest,
    QgsMapLayer,
    QgsProject,
    QgsVectorLayer,
    QgsVectorLayerCache,
)


def _layer_brief(layer):
    # Guard every call: temporary/scratch layers can have a live Python wrapper
    # but a partially-freed C++ object, causing a segfault if accessed naively.
    try:
        info = {
            "id": layer.id(),
            "name": layer.name(),
            "type": "vector" if layer.type() == QgsMapLayer.VectorLayer else (
                "raster" if layer.type() == QgsMapLayer.RasterLayer else "other"
            ),
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "valid": layer.isValid(),
        }
    except RuntimeError:
        return {"id": "?", "name": "?", "valid": False, "error": "layer no longer available"}

    if isinstance(layer, QgsVectorLayer):
        try:
            info["feature_count"] = layer.featureCount()
        except Exception:
            info["feature_count"] = -1
        try:
            info["geometry_type"] = layer.geometryType()  # 0=point,1=line,2=polygon
        except Exception:
            info["geometry_type"] = -1
        try:
            info["selected_count"] = layer.selectedFeatureCount()
        except Exception:
            info["selected_count"] = 0
    return info


class QgisToolkit:
    """Capability surface exposed to the agent. Construct on the main thread."""

    def __init__(self, iface):
        self.iface = iface
        self._alg_cache = None  # caches full algorithm list

    # ------------------------------------------------------------------ #
    # The catch-all: arbitrary PyQGIS execution                          #
    # ------------------------------------------------------------------ #
    def run_pyqgis(self, code):
        """Execute arbitrary PyQGIS ``code`` and return captured output.

        The execution namespace pre-binds the names a PyQGIS user expects:
        ``iface``, ``QgsProject``, ``qgis`` (core/gui), ``processing`` and the
        ``QgsApplication``. Assign to a variable named ``result`` to return a
        structured value to the agent. ``stdout``/``stderr`` are captured.
        """
        import qgis.core as qgis_core
        import qgis.gui as qgis_gui

        ns = {
            "__name__": "__agenticgis__",
            "iface": self.iface,
            "QgsProject": QgsProject,
            "QgsApplication": QgsApplication,
            "qgis": __import__("qgis"),
            "qgis_core": qgis_core,
            "qgis_gui": qgis_gui,
        }
        # Make `from qgis.core import *`-style names available without imports.
        ns.update({k: getattr(qgis_core, k) for k in dir(qgis_core) if not k.startswith("_")})
        try:
            import processing  # noqa: WPS433 (optional at import time)
            ns["processing"] = processing
        except Exception:  # pragma: no cover - processing should exist in QGIS
            pass

        # Performance helper: efficient feature iteration
        def _iterate_features(layer, fields=None, no_geometry=False):
            """Iterate layer features efficiently using QgsFeatureRequest."""
            if not isinstance(layer, QgsVectorLayer):
                return []
            req = QgsFeatureRequest()
            if no_geometry:
                req.setFlags(Qgis.FeatureRequestFlag.NoGeometry)
            if fields:
                req.setSubsetOfAttributes(fields, layer.fields())
            return list(layer.getFeatures(req))

        ns["_iterate_features"] = _iterate_features
        ns["QgsFeatureRequest"] = QgsFeatureRequest

        # Layer cache helper for repeated efficient access
        def _make_layer_cache(layer_id, cache_size=10000):
            layer = QgsProject.instance().mapLayer(layer_id)
            if not isinstance(layer, QgsVectorLayer):
                return None
            cache = QgsVectorLayerCache(layer, cache_size)
            cache.setFullCache(True)
            return cache

        ns["_make_layer_cache"] = _make_layer_cache
        ns["QgsVectorLayerCache"] = QgsVectorLayerCache

        out, err = io.StringIO(), io.StringIO()
        result = {"ok": True, "stdout": "", "stderr": "", "result": None, "error": None}
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "run_pyqgis: code must be a non-empty string", "stdout": "", "stderr": ""}
        if len(code) > 200_000:
            return {"ok": False, "error": "run_pyqgis: code is too large (>200k chars)", "stdout": "", "stderr": ""}
        try:
            with redirect_stdout(out), redirect_stderr(err):
                exec(compile(code, "<agenticgis>", "exec"), ns)  # noqa: S102
            if "result" in ns:
                result["result"] = repr(ns["result"])
        except KeyboardInterrupt:
            return {"ok": False, "error": "run_pyqgis: interrupted by user", "stdout": out.getvalue(), "stderr": err.getvalue()}
        except SystemExit:
            return {"ok": False, "error": "run_pyqgis: code called sys.exit()", "stdout": out.getvalue(), "stderr": err.getvalue()}
        except BaseException as exc:  # noqa: BLE001 — report back to the agent, never crash QGIS
            result["ok"] = False
            result["error"] = traceback.format_exc()
        finally:
            result["stdout"] = out.getvalue()
            result["stderr"] = err.getvalue()
        # Reflect any visual changes immediately.
        try:
            self.iface.mapCanvas().refresh()
        except Exception:
            pass
        return result

    # ------------------------------------------------------------------ #
    # Introspection helpers                                              #
    # ------------------------------------------------------------------ #
    def get_project_state(self):
        project = QgsProject.instance()
        canvas = self.iface.mapCanvas()
        active = self.iface.activeLayer()
        extent = canvas.extent()
        return {
            "project_path": project.fileName() or None,
            "title": project.title() or None,
            "crs": project.crs().authid() if project.crs().isValid() else None,
            "layer_count": len(project.mapLayers()),
            "active_layer_id": active.id() if active else None,
            "active_layer_name": active.name() if active else None,
            "canvas_extent": [extent.xMinimum(), extent.yMinimum(),
                              extent.xMaximum(), extent.yMaximum()],
            "layers": [_layer_brief(layer) for layer in project.mapLayers().values()],
        }

    def list_layers(self, limit=None, offset=0):
        project = QgsProject.instance()
        layers = list(project.mapLayers().values())
        total = len(layers)
        start = offset or 0
        end = (start + limit) if limit else total
        sliced = layers[start:end]
        result = [_layer_brief(layer) for layer in sliced]
        # Backward compatibility: return raw list when no pagination requested
        if limit is None and not offset:
            return result
        return {
            "total": total,
            "limit": limit,
            "offset": start,
            "layers": result,
        }

    def get_layer_fields(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"error": f"Layer {layer.name()!r} is not a vector layer"}
        return {
            "layer": layer.name(),
            "fields": [
                {"name": f.name(), "type": f.typeName(), "length": f.length()}
                for f in layer.fields()
            ],
        }

    def get_layer_summary(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"error": f"No layer with id {layer_id!r}"}
        summary = _layer_brief(layer)
        summary["source"] = layer.publicSource()
        if isinstance(layer, QgsVectorLayer):
            extent = layer.extent()
            summary["extent"] = [extent.xMinimum(), extent.yMinimum(),
                                 extent.xMaximum(), extent.yMaximum()]
            summary["fields"] = [f.name() for f in layer.fields()]
        return summary

    def list_plugins(self):
        """List installed and active QGIS plugins so the agent knows what is
        available to drive (e.g. via ``run_pyqgis`` or their algorithms)."""
        try:
            from qgis.utils import active_plugins, available_plugins, plugins
        except Exception:
            return {"error": "qgis.utils not available"}
        return {
            "active": sorted(active_plugins),
            "available": sorted(available_plugins),
            "loaded": sorted(plugins.keys()),
        }

    # ------------------------------------------------------------------ #
    # Processing framework                                               #
    # ------------------------------------------------------------------ #
    def list_processing_algorithms(self, filter_text=""):
        if self._alg_cache is None:
            self._alg_cache = [
                {"id": alg.id(), "name": alg.displayName()}
                for alg in QgsApplication.processingRegistry().algorithms()
            ]
        algs = self._alg_cache
        needle = (filter_text or "").lower()
        if needle:
            algs = [a for a in algs if needle in a["id"].lower() or needle in a["name"].lower()]
        return {"count": len(algs), "algorithms": algs}

    def _invalidate_alg_cache(self):
        """Invalidate the processing algorithm cache (call after plugin changes)."""
        self._alg_cache = None

    def run_processing(self, alg_id, params):
        import processing

        if not isinstance(alg_id, str) or not alg_id.strip():
            return {"ok": False, "error": "alg_id must be a non-empty string"}
        try:
            output = processing.run(alg_id, dict(params or {}))
        except KeyboardInterrupt:
            return {"ok": False, "error": "interrupted by user"}
        except BaseException as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        # Outputs may contain layers / non-serialisable objects; stringify.
        try:
            return {"ok": True, "output": {k: str(v) for k, v in (output or {}).items()}}
        except BaseException as exc:  # noqa: BLE001
            return {"ok": False, "error": f"failed to serialize output: {type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------ #
    # Project mutation helpers                                           #
    # ------------------------------------------------------------------ #
    def add_layer(self, uri, name=None, provider="ogr"):
        name = name or uri.split("/")[-1]
        if provider in ("gdal", "raster"):
            from qgis.core import QgsRasterLayer
            layer = QgsRasterLayer(uri, name)
        else:
            layer = QgsVectorLayer(uri, name, provider)
        if not layer.isValid():
            return {"ok": False, "error": f"Layer is not valid: {uri!r}"}
        QgsProject.instance().addMapLayer(layer)
        return {"ok": True, "layer_id": layer.id(), "name": layer.name()}

    def save_project(self):
        ok = QgsProject.instance().write()
        return {"ok": bool(ok), "path": QgsProject.instance().fileName() or None}

    def create_chart(self, layer_id, field_name, chart_type="bar"):
        """Generate chart data from a vector layer field.

        Returns structured data for the chat dock to render as a chart.
        """
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"error": f"Layer is not a vector layer"}

        data = []
        field_idx = layer.fields().indexFromName(field_name)
        if field_idx == -1:
            return {"error": f"Field {field_name!r} not found"}

        # Collect values
        values = {}
        for feature in layer.getFeatures():
            val = feature.attribute(field_idx)
            if val not in values:
                values[val] = 0
            values[val] += 1

        # Sort by count
        sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:20]  # top 20
        data = [{"label": str(k), "value": v} for k, v in sorted_items]

        return {
            "ok": True,
            "chart_type": chart_type,
            "title": f"{field_name} in {layer.name()}",
            "data": data,
            "field": field_name,
            "layer_name": layer.name(),
        }

    def get_layer_statistics(self, layer_id, field_name=None):
        """Calculate statistics for a vector layer or specific field."""
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"error": f"Layer is not a vector layer"}

        stats = {
            "layer_name": layer.name(),
            "total_features": layer.featureCount(),
            "valid": layer.isValid(),
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "geometry_type": layer.geometryType(),
        }

        if field_name:
            field_idx = layer.fields().indexFromName(field_name)
            if field_idx == -1:
                return {"error": f"Field {field_name!r} not found"}

            # Calculate field stats
            values = []
            numeric_values = []
            for feature in layer.getFeatures():
                val = feature.attribute(field_idx)
                values.append(val)
                try:
                    numeric_values.append(float(val))
                except (TypeError, ValueError):
                    pass

            stats["field"] = field_name
            stats["distinct_count"] = len(set(values))
            stats["null_count"] = values.count(None)

            if numeric_values:
                stats["min"] = min(numeric_values)
                stats["max"] = max(numeric_values)
                stats["mean"] = sum(numeric_values) / len(numeric_values)
                stats["sum"] = sum(numeric_values)

        return {"ok": True, "statistics": stats}
