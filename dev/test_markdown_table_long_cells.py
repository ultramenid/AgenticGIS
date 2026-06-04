"""Regression check that long table cells cannot overlap later columns."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.gui.message_bubble import _md_to_html


def main():
    layer_id = "Kawasanhutan_7427cc64_2335_4988_8abc_4b3e26ec9115"
    rendered = _md_to_html(
        "| Name | ID | Type | Features |\n"
        "| --- | --- | --- | --- |\n"
        f"| Kawasanhutan | `{layer_id}` | Vector | 99,353 |"
    )

    assert "<table" not in rendered
    assert "<pre" in rendered
    assert "&lt;code style=" not in rendered
    assert "white-space:pre" in rendered
    assert "overflow-x:auto" in rendered
    assert layer_id in rendered
    assert "Vector" in rendered and "99,353" in rendered


if __name__ == "__main__":
    main()
