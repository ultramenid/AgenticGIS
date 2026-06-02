"""Quick environment check for AgenticGIS (zero-dependency build).

Run inside the QGIS Python you intend to use:

    python /path/to/AgenticGis/dev/verify_env.py

The plugin needs no third-party packages, so this just confirms QGIS imports
and the plugin package loads cleanly on this interpreter.
"""

import importlib
import os
import sys

OK, BAD = "✓", "✗"


def line(mark, text):
    print(f"  [{mark}] {text}")


def main():
    print("AgenticGIS environment check (zero-dependency build)")
    print("-" * 48)

    v = sys.version_info
    line(OK, f"Python {v.major}.{v.minor}.{v.micro}")

    try:
        from qgis.core import Qgis
        line(OK, f"qgis.core imports — QGIS {Qgis.QGIS_VERSION}")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"qgis.core import failed: {exc}")

    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent = os.path.dirname(pkg_dir)
    pkg_name = os.path.basename(pkg_dir)
    sys.path.insert(0, parent)
    try:
        pkg = importlib.import_module(pkg_name)
        line(OK if hasattr(pkg, "classFactory") else BAD,
             f"plugin package '{pkg_name}' imports; classFactory present")
        importlib.import_module(f"{pkg_name}.server")
        importlib.import_module(f"{pkg_name}.backends.anthropic_http")
        line(OK, "stdlib MCP bridge + HTTP client modules import")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"plugin import failed: {exc}")

    print("-" * 48)
    print("No dependencies to install. Ready to use on this QGIS.")


if __name__ == "__main__":
    main()
