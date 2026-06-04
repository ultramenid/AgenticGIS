"""Small in-memory cache for read-only layer analysis results."""

import copy
import threading
import time
from collections import OrderedDict


class AnalysisCache:
    """LRU cache keyed by layer state and analysis request.

    The cache is intentionally process-local. It avoids repeated scans in a
    single QGIS session without persisting possibly sensitive layer summaries.
    """

    def __init__(self, max_entries=64, ttl_seconds=300):
        self.max_entries = int(max_entries)
        self.ttl_seconds = int(ttl_seconds)
        self._lock = threading.Lock()
        self._items = OrderedDict()

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            ts, value = item
            if self.ttl_seconds > 0 and now - ts > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(value)

    def set(self, key, value):
        with self._lock:
            self._items[key] = (time.monotonic(), copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self):
        with self._lock:
            self._items.clear()


def layer_cache_token(layer):
    """Return a best-effort token that changes when layer content changes."""
    try:
        return (
            layer.id(),
            layer.source(),
            layer.featureCount(),
            tuple(field.name() for field in layer.fields()),
            layer.subsetString() if hasattr(layer, "subsetString") else "",
        )
    except Exception:
        return (getattr(layer, "id", lambda: "?")(),)
