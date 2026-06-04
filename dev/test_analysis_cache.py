"""Regression checks for analysis result cache."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core.analysis_cache import AnalysisCache


def main():
    cache = AnalysisCache(max_entries=1, ttl_seconds=60)
    key_a = ("layer", "a")
    key_b = ("layer", "b")

    value = {"ok": True, "rows": [{"value": 1}]}
    cache.set(key_a, value)
    value["rows"][0]["value"] = 2

    cached = cache.get(key_a)
    assert cached["rows"][0]["value"] == 1
    cached["rows"][0]["value"] = 3
    assert cache.get(key_a)["rows"][0]["value"] == 1

    cache.set(key_b, {"ok": True})
    assert cache.get(key_a) is None
    assert cache.get(key_b)["ok"] is True

    cache.clear()
    assert cache.get(key_b) is None


if __name__ == "__main__":
    main()
