"""Regression check for readable spacing after markdown tables."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.gui.message_bubble import _md_to_html


def main():
    html = _md_to_html("| Field | Value |\n| --- | --- |\n| Name | Roads |\nNext content")

    assert "</pre></div><br><span" in html, "content after a table should have explicit spacing"
    assert "Next content" in html


if __name__ == "__main__":
    main()
