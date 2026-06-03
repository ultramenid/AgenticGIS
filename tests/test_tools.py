"""Tests for tool dispatch and spec generation."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tools


def test_tool_by_name():
    """Test that all tool specs are indexed by name."""
    for spec in tools.TOOL_SPECS:
        assert spec["name"] in tools.TOOL_BY_NAME


def test_anthropic_tool_list():
    """Test that anthropic tool list matches specs."""
    tl = tools.anthropic_tool_list()
    assert len(tl) == len(tools.TOOL_SPECS)
    for i, spec in enumerate(tools.TOOL_SPECS):
        assert tl[i]["name"] == spec["name"]


def test_list_layers_pagination_schema():
    """Test that list_layers tool spec has pagination parameters."""
    spec = tools.TOOL_BY_NAME["list_layers"]
    schema = spec["input_schema"]
    assert "limit" in schema["properties"]
    assert "offset" in schema["properties"]


def test_ask_user_spec_registered():
    """The ask_user tool must be in TOOL_BY_NAME and the Anthropic export."""
    from core import tools
    assert "ask_user" in tools.TOOL_BY_NAME
    spec = tools.TOOL_BY_NAME["ask_user"]
    assert spec["method"] == "ask_user"
    assert "question" in spec["input_schema"]["properties"]
    assert "options" in spec["input_schema"]["properties"]
    assert spec["input_schema"]["required"] == ["question", "options"]
    # Must be exported to the Anthropic shape too
    anthropic_list = tools.anthropic_tool_list()
    assert any(t["name"] == "ask_user" for t in anthropic_list)
