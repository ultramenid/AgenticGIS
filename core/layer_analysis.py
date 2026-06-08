"""Structured, bounded analysis helpers for QGIS vector layers."""

from collections import Counter
import math

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import NULL, QgsFeatureRequest, QgsWkbTypes

try:
    from qgis.core import Qgis
except ImportError:  # pragma: no cover - compatibility with older QGIS builds.
    Qgis = None


DEFAULT_SCAN_LIMIT = 10000
DEFAULT_SAMPLE_LIMIT = 5
DEFAULT_TOP_LIMIT = 10


def _geometry_type_name(layer):
    """Return human-readable geometry type string, compatible with both
    QGIS 3 (QgsWkbTypes.geometryDisplayString) and QGIS 4 (may be removed)."""
    try:
        gtype = layer.geometryType()
    except Exception:  # noqa: BLE001
        return "unknown"
    # Try QGIS 3 API first
    try:
        return QgsWkbTypes.geometryDisplayString(gtype)
    except AttributeError:
        pass
    # QGIS 4 fallback: use Qgis.GeometryType enum if available
    if Qgis is not None:
        geom_cls = getattr(Qgis, "GeometryType", None)
        if geom_cls is not None:
            names = {getattr(geom_cls, "Point", 1): "Point",
                     getattr(geom_cls, "Line", 2): "LineString",
                     getattr(geom_cls, "Polygon", 3): "Polygon",
                     getattr(geom_cls, "Null", 4): "No geometry",
                     getattr(geom_cls, "Unknown", 0): "Unknown geometry"}
            return names.get(gtype, "Unknown geometry")
    # Final fallback: map integer constants
    names = {0: "Unknown geometry", 1: "Point", 2: "LineString",
             3: "Polygon", 4: "No geometry"}
    return names.get(int(gtype) if gtype is not None else 0, "Unknown geometry")


def analyze_vector_layer(
    layer,
    fields=None,
    sample_limit=DEFAULT_SAMPLE_LIMIT,
    scan_limit=DEFAULT_SCAN_LIMIT,
    top_limit=DEFAULT_TOP_LIMIT,
    feedback=None,
):
    """Return a bounded, read-only analysis dictionary for a QgsVectorLayer."""

    field_names = _selected_field_names(layer, fields)
    field_map = {field.name(): field for field in layer.fields()}
    numeric_fields = {
        name for name in field_names if _is_numeric_field(field_map.get(name))
    }

    result = {
        "summary": summarize_vector_layer(layer),
        "field_stats": {
            name: _empty_numeric_stats(layer, name) for name in sorted(numeric_fields)
        },
        "category_counts": {
            name: {
                "top_values": [],
                "unique_scanned": 0,
                "provider_unique_values": _provider_unique_values(layer, name, DEFAULT_TOP_LIMIT),
            }
            for name in field_names
            if name not in numeric_fields
        },
        "top_values": {},
        "sample": [],
        "missing_values": {name: 0 for name in field_names},
        "scanned_features": 0,
        "truncated": False,
        "canceled": False,
        "scan_limit": int(scan_limit),
    }

    counters = {
        name: Counter() for name in result["category_counts"]
    }
    scan_limit = max(0, int(scan_limit))
    sample_limit = max(0, int(sample_limit))
    top_limit = max(1, int(top_limit))

    request = _attribute_request(layer, field_names)
    if scan_limit:
        request.setLimit(scan_limit + 1)

    for i, feature in enumerate(layer.getFeatures(request)):
        if _is_canceled(feedback):
            result["canceled"] = True
            break
        if result["scanned_features"] >= scan_limit:
            result["truncated"] = True
            break

        if i % 50 == 0:
            QCoreApplication.processEvents()

        row = _feature_row(feature, field_names)
        result["scanned_features"] += 1
        if len(result["sample"]) < sample_limit:
            result["sample"].append(dict(row))

        for name, value in row.items():
            if _is_missing(value):
                result["missing_values"][name] += 1
                if name in result["field_stats"]:
                    result["field_stats"][name]["missing"] += 1
                continue

            if name in result["field_stats"]:
                _add_numeric_value(result["field_stats"][name], value)
            elif name in counters:
                counters[name][_normalize_value(value)] += 1

    if not result["truncated"] and _known_feature_count(layer) > result["scanned_features"]:
        result["truncated"] = True

    for name, stats in result["field_stats"].items():
        if stats["count"]:
            stats["mean"] = stats["sum"] / stats["count"]
        else:
            stats["mean"] = None

    for name, counter in counters.items():
        values = [
            {"value": value, "count": count}
            for value, count in counter.most_common(top_limit)
        ]
        result["category_counts"][name] = {
            "top_values": values,
            "unique_scanned": len(counter),
            "provider_unique_values": result["category_counts"][name][
                "provider_unique_values"
            ],
        }
        result["top_values"][name] = values

    return result


def summarize_vector_layer(layer):
    """Return stable layer metadata without scanning features."""

    fields = []
    for field in layer.fields():
        fields.append(
            {
                "name": field.name(),
                "type": field.typeName(),
                "is_numeric": _is_numeric_field(field),
            }
        )

    return {
        "id": layer.id(),
        "name": layer.name(),
        "source": layer.source(),
        "provider": layer.providerType(),
        "is_valid": layer.isValid(),
        "feature_count": _known_feature_count(layer),
        "geometry_type": _geometry_type_name(layer),
        "crs": layer.crs().authid() if layer.crs().isValid() else None,
        "fields": fields,
    }


def _selected_field_names(layer, fields):
    available = {field.name() for field in layer.fields()}
    if fields is None:
        return [field.name() for field in layer.fields()]
    return [name for name in fields if name in available]


def _attribute_request(layer, field_names):
    request = QgsFeatureRequest()
    no_geometry = None
    if Qgis is not None and hasattr(Qgis, "FeatureRequestFlag"):
        no_geometry = Qgis.FeatureRequestFlag.NoGeometry
    elif hasattr(QgsFeatureRequest, "NoGeometry"):
        no_geometry = QgsFeatureRequest.NoGeometry
    if no_geometry is not None:
        request.setFlags(no_geometry)
    request.setSubsetOfAttributes(field_names, layer.fields())
    return request


def _empty_numeric_stats(layer, field_name):
    stats = {
        "count": 0,
        "missing": 0,
        "min": None,
        "max": None,
        "sum": 0.0,
        "mean": None,
        "provider_min": None,
        "provider_max": None,
    }
    index = layer.fields().indexOf(field_name)
    if index < 0:
        return stats

    provider = layer.dataProvider()
    try:
        stats["provider_min"] = _normalize_value(provider.minimumValue(index))
    except Exception:  # noqa: BLE001 - provider capabilities vary by backend.
        stats["provider_min"] = None
    try:
        stats["provider_max"] = _normalize_value(provider.maximumValue(index))
    except Exception:  # noqa: BLE001 - provider capabilities vary by backend.
        stats["provider_max"] = None
    return stats


def _add_numeric_value(stats, value):
    number = _to_float(value)
    if number is None:
        stats["missing"] += 1
        return
    stats["count"] += 1
    stats["sum"] += number
    stats["min"] = number if stats["min"] is None else min(stats["min"], number)
    stats["max"] = number if stats["max"] is None else max(stats["max"], number)


def _feature_row(feature, field_names):
    row = {}
    for name in field_names:
        row[name] = _normalize_value(feature.attribute(name))
    return row


def _known_feature_count(layer):
    try:
        count = layer.featureCount()
    except Exception:  # noqa: BLE001 - provider capabilities vary by backend.
        return -1
    return int(count) if count is not None else -1


def _provider_unique_values(layer, field_name, limit):
    if _known_feature_count(layer) > DEFAULT_SCAN_LIMIT:
        return []

    index = layer.fields().indexOf(field_name)
    if index < 0:
        return []

    provider = layer.dataProvider()
    try:
        values = provider.uniqueValues(index, int(limit))
    except Exception:  # noqa: BLE001 - provider capabilities vary by backend.
        return []
    return [_normalize_value(value) for value in values if not _is_missing(value)]


def _is_numeric_field(field):
    if field is None:
        return False
    if hasattr(field, "isNumeric"):
        return bool(field.isNumeric())
    return field.typeName().lower() in {
        "integer",
        "integer64",
        "int",
        "int4",
        "int8",
        "real",
        "double",
        "float",
        "decimal",
        "numeric",
    }


def _is_missing(value):
    if value is None:
        return True
    try:
        if value == NULL:
            return True
    except Exception:  # noqa: BLE001 # nosec B110
        pass
    return False


def _to_float(value):
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _normalize_value(value):
    if _is_missing(value):
        return None
    if hasattr(value, "toPyObject"):
        value = value.toPyObject()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "toString"):
        try:
            return value.toString()
        except Exception:  # noqa: BLE001 # nosec B110
            pass
    return str(value)


def _is_canceled(feedback):
    if feedback is None:
        return False
    is_canceled = getattr(feedback, "isCanceled", None)
    if callable(is_canceled) and is_canceled():
        return True
    is_set = getattr(feedback, "is_set", None)
    if callable(is_set) and is_set():
        return True
    return False
