"""QGIS network (WMS/WMTS/XYZ) disk-cache control + lifecycle cleanup.

QGIS keeps one shared QNetworkDiskCache on the global QgsNetworkAccessManager;
WMS/WMTS/XYZ tile responses (including streaming GEE 'ee_plugin' layers and web
basemaps) flow through it. This module enables/sizes that cache, reports its
state, clears it, and auto-cleans it on shutdown with a force-close fallback:
a QSettings sentinel is set on startup and reset on clean unload, so a crash or
force-close that skips unload() is detected and swept on the next startup.

Stdlib + PyQGIS only. Every function is best-effort and never raises — the
network manager can be absent in headless/test contexts.
"""

from qgis.PyQt.QtCore import QSettings

# QGIS desktop stores its network disk-cache config under these QSettings keys
# (the same ones behind Settings -> Options -> Network -> Cache).
_CACHE_SIZE_KEY = "cache/size"          # bytes
_CACHE_DIR_KEY = "cache/directory"
# Sentinel used to detect a previous run that did not clean up (force-close).
_DIRTY_KEY = "agenticgis/network_cache_dirty"
_MB = 1024 * 1024
# Default size applied at startup ONLY when QGIS's cache is off (size 0). A
# user's existing positive size (including QGIS's own default) is never changed.
DEFAULT_CACHE_MB = 1024


def _nam():
    """Return the global QgsNetworkAccessManager, or None if unavailable."""
    try:
        from qgis.core import QgsNetworkAccessManager
        return QgsNetworkAccessManager.instance()
    except Exception:  # nosec B110
        return None


def _truthy(value):
    return str(value).lower() in ("true", "1", "yes")


def network_cache_state():
    """Report the current network cache state as a dict."""
    nam = _nam()
    if nam is None:
        return {"ok": False, "error": "network access manager unavailable"}
    try:
        cache = nam.cache()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if cache is None:
        return {
            "ok": True, "enabled": False, "max_size_mb": 0,
            "used_size_mb": 0, "directory": None,
        }
    try:
        max_bytes = int(cache.maximumCacheSize())
    except Exception:  # nosec B110
        max_bytes = 0
    try:
        used_bytes = int(cache.cacheSize())
    except Exception:  # nosec B110
        used_bytes = 0
    try:
        directory = cache.cacheDirectory()  # QNetworkDiskCache only
    except Exception:  # nosec B110
        directory = None
    return {
        "ok": True,
        "enabled": max_bytes > 0,
        "max_size_mb": round(max_bytes / _MB, 2),
        "used_size_mb": round(used_bytes / _MB, 2),
        "directory": directory,
    }


def set_network_cache_size(size_mb):
    """Set the cache max size in MB (size_mb > 0 enables, 0 disables).

    Persists to QSettings so it survives restarts, then applies immediately.
    When a cache already exists, only its maximum size is adjusted (proxy and
    other network settings are left untouched).  When no cache exists, the NAM
    cache is rebuilt via setupDefaultProxyAndCache().
    Returns the resulting state dict.
    """
    try:
        size_mb = float(size_mb)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"invalid size_mb: {size_mb!r}"}
    if size_mb < 0:
        return {"ok": False, "error": "size_mb must be >= 0"}
    size_bytes = int(size_mb * _MB)
    QSettings().setValue(_CACHE_SIZE_KEY, size_bytes)
    nam = _nam()
    if nam is None:
        return {"ok": False, "error": "network access manager unavailable"}
    try:
        cache = nam.cache()
    except Exception:  # nosec B110
        cache = None
    if cache is not None and hasattr(cache, "setMaximumCacheSize"):
        cache.setMaximumCacheSize(size_bytes)
    else:
        try:
            nam.setupDefaultProxyAndCache()
            cache = nam.cache()
            if cache is not None and hasattr(cache, "setMaximumCacheSize"):
                cache.setMaximumCacheSize(size_bytes)
        except Exception:  # nosec B110
            pass
    return network_cache_state()


def clear_network_cache():
    """Clear all cached network responses. Best-effort; returns ok dict."""
    nam = _nam()
    if nam is None:
        return {"ok": False, "error": "network access manager unavailable"}
    try:
        cache = nam.cache()
        if cache is not None:
            cache.clear()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def maybe_enable_default_cache(default_mb=DEFAULT_CACHE_MB):
    """Apply the default cache size unconditionally.

    Overrides any existing QGIS cache setting (including QGIS's own default) so
    the plugin always runs with a known, generous cache. Best-effort; never
    raises. Returns a dict noting the resulting state.
    """
    state = network_cache_state()
    if not state.get("ok"):
        return {"ok": False, "applied": False, "error": state.get("error")}
    return set_network_cache_size(default_mb)


# -- Force-close-safe auto-clean lifecycle --------------------------------- #

def sweep_stale_cache_on_startup(settings=None):
    """On plugin load: if the previous run left the sentinel set (force-close
    or crash skipped unload), clear the cache now. Then mark this run active so
    a later clean unload knows to clear on exit. Best-effort; never raises."""
    try:
        s = settings or QSettings()
        was_dirty = _truthy(s.value(_DIRTY_KEY, "false"))
        if was_dirty:
            clear_network_cache()
        s.setValue(_DIRTY_KEY, "true")
        return {"ok": True, "swept": was_dirty}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def clear_cache_on_unload(settings=None):
    """On graceful plugin unload: clear the cache and reset the sentinel."""
    result = clear_network_cache()
    try:
        s = settings or QSettings()
        s.setValue(_DIRTY_KEY, "false")
    except Exception:  # nosec B110
        pass
    return result
