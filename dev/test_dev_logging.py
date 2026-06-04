"""Regression checks for optional development logging."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core import dev_logging


def main():
    fd, path = tempfile.mkstemp(prefix="agenticgis-log-", suffix=".jsonl")
    os.close(fd)
    os.unlink(path)

    old_enabled = os.environ.get("AGENTICGIS_DEV_LOG")
    old_path = os.environ.get("AGENTICGIS_DEV_LOG_PATH")
    try:
        os.environ["AGENTICGIS_DEV_LOG"] = "0"
        os.environ["AGENTICGIS_DEV_LOG_PATH"] = path
        dev_logging.log_event("disabled_event")
        assert not os.path.exists(path)

        os.environ["AGENTICGIS_DEV_LOG"] = "1"
        dev_logging.log_event("unit_event", tool="demo", count=3)
        with dev_logging.timed("unit_timer", tool="demo"):
            pass

        with open(path, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]

        assert rows[0]["event"] == "unit_event"
        assert rows[0]["tool"] == "demo"
        assert rows[0]["count"] == 3
        assert rows[1]["event"] == "unit_timer.start"
        assert rows[2]["event"] == "unit_timer.end"
        assert "elapsed_ms" in rows[2]
    finally:
        if old_enabled is None:
            os.environ.pop("AGENTICGIS_DEV_LOG", None)
        else:
            os.environ["AGENTICGIS_DEV_LOG"] = old_enabled
        if old_path is None:
            os.environ.pop("AGENTICGIS_DEV_LOG_PATH", None)
        else:
            os.environ["AGENTICGIS_DEV_LOG_PATH"] = old_path


if __name__ == "__main__":
    main()
