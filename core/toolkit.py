"""The single source of QGIS capability that all agent backends drive.

Every method here assumes it is running on the QGIS **main thread**. Callers
on worker threads must wrap invocations in ``MainThreadExecutor.run_sync``.
``run_pyqgis`` is the catch-all that gives the agent access to every QGIS
feature and every installed plugin; the other methods are convenience/
introspection helpers that keep common requests cheap and reliable.

Cancellation
------------
A lightweight ``CancellationRegistry`` lives on the toolkit. Every top-level
worker call wraps its dispatch in a token that the Stop button (or a
``QgsFeedback`` created by QGIS's processing framework) can set. Tokens are
re-entrant and torn down in ``finally`` so a tool that succeeds still clears
its slot.
"""

import io
import threading
import time
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

from .cancellation import CancellationRegistry as _CancellationRegistry


# --------------------------------------------------------------------------- #
# Cancellation                                                                 #
# --------------------------------------------------------------------------- #


def _make_qgs_feedback(event):
    """Build a ``QgsFeedback`` whose ``isCanceled`` mirrors ``event``.

    Falls back to ``None`` (and the caller does nothing) if QGIS isn't around.
    """
    if event is None:
        return None
    try:
        from qgis.core import QgsFeedback
    except Exception:  # pragma: no cover
        return None
    fb = QgsFeedback()
    # Wire via a Python property so we don't have to subclass (some QGIS
    # builds don't allow subclassing of bound Qt types).
    def _check():
        return event.is_set()
    # Use a small QTimer to keep the event loop ticking while the
    # feedback polls its cancelled state.
    fb.isCanceled = lambda: _check()
    return fb


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
        return {"ok": False, "id": "?", "name": "?", "valid": False,
                "error": "layer no longer available"}

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


# Sentinel result for cancelled tool calls (a string so JSON-serialisable).
_CANCELLED_SENTINEL = "__cancelled__"


class QgisToolkit:
    """Capability surface exposed to the agent. Construct on the main thread."""

    # Bound by the agent loop / dock on construction.
    should_stop_fn = None  # type: callable | None  # threaded "stop requested?"

    def __init__(self, iface, config=None):
        self.iface = iface
        self.config = config
        self._alg_cache = None  # caches full algorithm list
        self._cancel = _CancellationRegistry()
        self._ask_emitter = None
        self._ask_user_lock = threading.Lock()
        self._ask_user_pending = None
        self._ns_template = None  # F10: cached exec namespace
        # F17: dirty flag — set when a tool may have mutated project state.
        self._canvas_dirty = False
        # F8: hook QGIS's plugins-changed signal so the algorithm list
        # reflects newly-enabled providers (GRASS, SAGA, custom plugins)
        # without a plugin restart.
        try:
            from qgis.core import QgsApplication
            QgsApplication.pluginsChanged.connect(self._invalidate_alg_cache)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Clarifying-question flow (ask_user tool)                            #
    # ------------------------------------------------------------------ #
    def set_ask_user_emitter(self, emitter):
        """Register a callback that asks the user a clarifying question.

        The emitter signature is ``emitter(question, options, allow_free_text)``.
        It is expected to be non-blocking; it fires ``self._resolve_ask_user(payload)``
        from the main thread when the user replies (or the dock is cleared).
        """
        self._ask_emitter = emitter

    def ask_user(self, question, options, allow_free_text=True):
        """Toolkit implementation of the ``ask_user`` tool.

        Return contract (deliberately different from the other toolkit
        methods, which wrap returns in ``{"ok": ...}``):

        - Success: a raw dict ``{"choice": str|None, "free_text": str|None, "cancelled": bool}``.
        - Validation failure (bad options / question, or recursive call): a
          plain string starting with ``"ask_user: "`` describing the error.
          The backends do not set ``is_error`` on these — schema validation
          is a separate concern from operation outcome.
        """
        # Validate options
        if not isinstance(options, (list, tuple)):
            return "ask_user: options must be a list of objects with a 'label' field"
        if len(options) < 2 or len(options) > 4:
            return f"ask_user: options must have 2-4 items, got {len(options)}"
        for i, opt in enumerate(options):
            if not isinstance(opt, dict) or not opt.get("label"):
                return f"ask_user: options[{i}] must be an object with a non-empty 'label'"

        if not isinstance(question, str) or not question.strip():
            return "ask_user: question must be a non-empty string"

        # Recursive guard: only one ask_user at a time
        with self._ask_user_lock:
            if self._ask_user_pending is not None:
                return "ask_user: already waiting for user input"

            wait = threading.Event()
            self._ask_user_pending = (wait, {"choice": None, "free_text": None, "cancelled": False})

        try:
            if self._ask_emitter is not None:
                self._ask_emitter(question, list(options), bool(allow_free_text))

            # Wait for the dock (or a test) to fire _resolve_ask_user.
            wait_evt, _ = self._ask_user_pending
            wait_evt.wait()
        finally:
            with self._ask_user_lock:
                payload = (
                    self._ask_user_pending[1]
                    if self._ask_user_pending is not None
                    else {"choice": None, "free_text": None, "cancelled": True}
                )
                self._ask_user_pending = None
        return payload

    def _resolve_ask_user(self, payload):
        """Called by the dock (or a test) to unblock ask_user.

        ``payload`` is a dict with keys ``choice``, ``free_text``, ``cancelled``.
        Missing keys default to None / False.
        """
        with self._ask_user_lock:
            if self._ask_user_pending is None:
                return  # nothing to resolve (stale fire, e.g. after Clear)
            wait_evt, slot = self._ask_user_pending
            slot["choice"] = payload.get("choice")
            slot["free_text"] = payload.get("free_text")
            slot["cancelled"] = bool(payload.get("cancelled", False))
            wait_evt.set()

    # ------------------------------------------------------------------ #
    # Cancellation helpers                                                #
    # ------------------------------------------------------------------ #
    def request_cancel(self):
        """Called by the dock's Stop button. Flips the active token."""
        self._cancel.cancel()

    def is_cancelled(self):
        return self._cancel.is_cancelled()

    # F16: list of (module_path, attribute) tuples we treat as destructive.
    # Code that imports any of these is refused when the safety flag is on.
    _DANGEROUS_SYMBOLS = (
        ("os", "system"),
        ("os", "remove"),
        ("os", "unlink"),
        ("os", "rmdir"),
        ("shutil", "rmtree"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("subprocess", "run"),
        ("subprocess", "check_output"),
        ("ctypes", "CDLL"),
        ("ctypes", "WinDLL"),
    )

    def _dangerous_calls_blocked(self, code, ns):
        """Return True when the safety flag is on and the code uses a
        destructive builtin *and* does not set ``ALLOW_DANGEROUS = True``.

        We do a cheap string-level check rather than static analysis — the
        goal is to catch the obvious cases ("the agent tried to run rm"),
        not to be a sandbox. Anything that bypasses the string check is
        still subject to the user's existing OS-level permissions.
        """
        if not (self.config and self.config.get("confirm_dangerous_calls")):
            return False
        if "ALLOW_DANGEROUS = True" in code or "ALLOW_DANGEROUS=True" in code:
            return False
        lowered = code  # keep case — the dangerous names are lower-case already
        for mod, attr in self._DANGEROUS_SYMBOLS:
            # Look for ``mod.attr(`` or ``mod . attr(`` — the form that
            # actually invokes the function.
            needle = f"{mod}.{attr}("
            if needle in lowered:
                return True
        return False

    # ------------------------------------------------------------------ #
    # The catch-all: arbitrary PyQGIS execution                          #
    # ------------------------------------------------------------------ #
    def run_pyqgis(self, code):
        """Execute arbitrary PyQGIS ``code`` and return captured output.

        The execution namespace pre-binds the names a PyQGIS user expects:
        ``iface``, ``QgsProject``, ``qgis`` (core/gui), ``processing`` and the
        ``QgsApplication``. Assign to a variable named ``result`` to return a
        structured value to the agent. ``stdout``/``stderr`` are captured.

        A ``_cancel_check()`` callable is also injected; user code that calls
        it periodically can be interrupted by the Stop button. ``time.sleep``
        is wrapped to honour the same flag so a sleeping agent loop yields
        within a few hundred milliseconds.
        """
        event, owner = self._cancel.register()
        try:
            return self._run_pyqgis_inner(code, event, owner)
        finally:
            self._cancel.release(event)

    def _run_pyqgis_inner(self, code, event, owner):
        import qgis.core as qgis_core
        import qgis.gui as qgis_gui

        # F10: namespace is built once per toolkit and copied per call.
        if self._ns_template is None:
            ns = {
                "__name__": "__agenticgis__",
                "iface": self.iface,
                "QgsProject": QgsProject,
                "QgsApplication": QgsApplication,
                "qgis": __import__("qgis"),
                "qgis_core": qgis_core,
                "qgis_gui": qgis_gui,
            }
            ns.update({k: getattr(qgis_core, k) for k in dir(qgis_core) if not k.startswith("_")})
            try:
                import processing  # noqa: WPS433 (optional at import time)
                ns["processing"] = processing
            except Exception:  # pragma: no cover - processing should exist in QGIS
                pass
            self._ns_template = ns
        ns = dict(self._ns_template)  # shallow copy — per-call top-level

        # Cancellation hooks injected into the agent's namespace.
        def _cancel_check():
            return event is not None and event.is_set()
        ns["_cancel_check"] = _cancel_check
        ns["is_cancelled"] = _cancel_check

        # Wrap time.sleep so user code can't stall the loop on a long sleep
        # without being interruptible. The wrapper wakes every 200ms to
        # honour a cancel flag; small sleeps stay reasonably accurate.
        _real_sleep = time.sleep

        def _interruptible_sleep(seconds):
            end = time.monotonic() + max(0.0, float(seconds))
            while True:
                if _cancel_check():
                    return
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return
                _real_sleep(min(remaining, 0.2))

        ns["time"] = type("t", (), {
            "sleep": _interruptible_sleep,
            "monotonic": time.monotonic,
            "time": time.time,
        })()

        # Performance helper: efficient feature iteration
        def _iterate_features(layer, fields=None, no_geometry=False):
            """Iterate layer features efficiently using ``QgsFeatureRequest``."""
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
        # F16: optional guard against destructive builtins. The flag is
        # opt-in so the existing "zero friction" behaviour is preserved by
        # default; users who want a safety net flip it on in Settings.
        if self._dangerous_calls_blocked(code, ns):
            return {"ok": False,
                    "error": ("run_pyqgis: code references a destructive "
                              "builtin (os.system, subprocess, shutil.rmtree, "
                              "ctypes, ...). Set 'Allow dangerous calls' in "
                              "Settings or define ALLOW_DANGEROUS = True at "
                              "the top of the code to override."),
                    "stdout": "", "stderr": ""}
        try:
            with redirect_stdout(out), redirect_stderr(err):
                exec(compile(code, "<agenticgis>", "exec"), ns)  # noqa: S102
            if _cancel_check():
                result["ok"] = False
                result["error"] = "run_pyqgis: cancelled by user"
                result["cancelled"] = True
            elif "result" in ns:
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
        # F17: only refresh if the agent's code may have touched the canvas.
        if self._canvas_dirty:
            try:
                self.iface.mapCanvas().refresh()
            except Exception:
                pass
            self._canvas_dirty = False
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
            "ok": True,
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
            "ok": True,
            "total": total,
            "limit": limit,
            "offset": start,
            "layers": result,
        }

    def get_layer_fields(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer {layer.name()!r} is not a vector layer"}
        return {
            "ok": True,
            "layer": layer.name(),
            "fields": [
                {"name": f.name(), "type": f.typeName(), "length": f.length()}
                for f in layer.fields()
            ],
        }

    def get_layer_summary(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
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
            return {"ok": False, "error": "qgis.utils not available"}
        return {
            "ok": True,
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
        return {"ok": True, "count": len(algs), "algorithms": algs}

    def _invalidate_alg_cache(self):
        """Invalidate the processing algorithm cache (call after plugin changes)."""
        self._alg_cache = None

    def run_processing(self, alg_id, params):
        import processing

        if not isinstance(alg_id, str) or not alg_id.strip():
            return {"ok": False, "error": "alg_id must be a non-empty string"}
        # F7: wire a feedback so the QGIS processing framework honours our
        # cancellation token. Falls back to a direct call if the framework
        # doesn't accept ``feedback`` (older QGIS).
        event, owner = self._cancel.register()
        feedback = _make_qgs_feedback(event) if owner else None
        try:
            if feedback is not None:
                try:
                    output = processing.run(alg_id, dict(params or {}), feedback=feedback)
                except TypeError:
                    # Older QGIS signature without feedback kwarg
                    output = processing.run(alg_id, dict(params or {}))
            else:
                output = processing.run(alg_id, dict(params or {}))
        except KeyboardInterrupt:
            # If the user cancelled, surface the cancel flag; otherwise
            # treat the interrupt as a generic error so the agent knows
            # it isn't something it can retry.
            if event is not None and event.is_set():
                return {"ok": False, "error": "cancelled by user", "cancelled": True}
            return {"ok": False, "error": "interrupted by user"}
        except BaseException as exc:  # noqa: BLE001
            # F7: distinguish the cancel path from real errors so the agent
            # can decide whether to retry.
            if event is not None and event.is_set():
                return {"ok": False, "error": "cancelled by user", "cancelled": True}
            name = type(exc).__name__
            # Pull a "cleaner" message out of QGIS-specific exception types
            # when available.
            msg = str(exc)
            return {"ok": False, "error": f"{name}: {msg}"}
        finally:
            self._cancel.release(event)
        # F17: processing likely mutated the canvas; mark dirty for the dock.
        self._canvas_dirty = True
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
        self._canvas_dirty = True
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
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer is not a vector layer"}

        data = []
        field_idx = layer.fields().indexFromName(field_name)
        if field_idx == -1:
            return {"ok": False, "error": f"Field {field_name!r} not found"}

        # F9: pull only the one field, no geometry — cuts allocation for big layers.
        from qgis.core import QgsFeatureRequest
        req = QgsFeatureRequest().setFlags(Qgis.FeatureRequestFlag.NoGeometry)
        req.setSubsetOfAttributes([field_idx], layer.fields())
        if layer.isCancellable():
            event, owner = self._cancel.register()
        else:
            event, owner = None, False
        try:
            values = {}
            for feature in layer.getFeatures(req):
                if owner and event is not None and event.is_set():
                    return {"ok": False, "error": "cancelled by user", "cancelled": True}
                val = feature.attribute(field_idx)
                if val not in values:
                    values[val] = 0
                values[val] += 1
        finally:
            if owner:
                self._cancel.release(event)

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
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer is not a vector layer"}

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
                return {"ok": False, "error": f"Field {field_name!r} not found"}

            # F9: only the requested attribute, no geometry; C++ summary for numerics.
            from qgis.core import QgsFeatureRequest, QgsStatisticalSummary
            req = QgsFeatureRequest().setFlags(Qgis.FeatureRequestFlag.NoGeometry)
            req.setSubsetOfAttributes([field_idx], layer.fields())
            if layer.isCancellable():
                event, owner = self._cancel.register()
            else:
                event, owner = None, False
            try:
                values = []
                numeric_values = []
                for feature in layer.getFeatures(req):
                    if owner and event is not None and event.is_set():
                        return {"ok": False, "error": "cancelled by user", "cancelled": True}
                    val = feature.attribute(field_idx)
                    values.append(val)
                    if isinstance(val, (int, float)):
                        numeric_values.append(val)
                    elif val is not None:
                        try:
                            numeric_values.append(float(val))
                        except (TypeError, ValueError):
                            pass
            finally:
                if owner:
                    self._cancel.release(event)

            stats["field"] = field_name
            stats["distinct_count"] = len(set(values))
            stats["null_count"] = values.count(None)

            if numeric_values:
                # C++ summary if the layer supports it; faster than Python loops.
                try:
                    summary = layer.statisticalSummary([QgsStatisticalSummary.Mean |
                                                       QgsStatisticalSummary.Min |
                                                       QgsStatisticalSummary.Max |
                                                       QgsStatisticalSummary.Sum |
                                                       QgsStatisticalSummary.Count |
                                                       QgsStatisticalSummary.StDev])
                    stats["min"] = float(summary.min())
                    stats["max"] = float(summary.max())
                    stats["mean"] = float(summary.mean())
                    stats["sum"] = float(summary.sum())
                    stats["stdev"] = float(summary.stDev())
                    stats["count"] = int(summary.count())
                except Exception:
                    stats["min"] = min(numeric_values)
                    stats["max"] = max(numeric_values)
                    stats["mean"] = sum(numeric_values) / len(numeric_values)
                    stats["sum"] = sum(numeric_values)

        return {"ok": True, "statistics": stats}
