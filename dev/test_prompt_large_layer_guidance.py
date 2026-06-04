"""Regression checks for agent prompt large-layer safety guidance."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.backends import api_backend, cli_backend, openai_backend
from AgenticGis.core import tools


OUT_OF_SCOPE = "we dont do that here"


def _run_pyqgis_description():
    for spec in tools.TOOL_SPECS:
        if spec["name"] == "run_pyqgis":
            return spec["description"]
    raise AssertionError("run_pyqgis tool spec not found")


def test_prompts_preserve_scope_and_large_layer_safety():
    prompt_texts = [
        api_backend.DEFAULT_SYSTEM_PROMPT,
        openai_backend.DEFAULT_SYSTEM_PROMPT,
        cli_backend.CliToolBackend({"cli_tool": "codex"}, None)._with_guardrails("map layers"),
    ]

    for text in prompt_texts:
        assert OUT_OF_SCOPE in text
        assert "Prefer analyze_layer" in text
        assert "Do not use list(layer.getFeatures())" in text
        assert "Do not fetch geometry when only attributes are needed" in text


def test_run_pyqgis_description_discourages_large_layer_loops():
    description = _run_pyqgis_description()

    assert "analyze_layer" in description
    assert "Do not use list(layer.getFeatures())" in description
    assert "Do not fetch geometry when only attributes are needed" in description


def test_chart_label_field_guidance_is_generic():
    prompt_texts = [
        api_backend.DEFAULT_SYSTEM_PROMPT,
        openai_backend.DEFAULT_SYSTEM_PROMPT,
    ]

    for text in prompt_texts:
        assert "label_field" in text
        assert "code/name" in text
        assert "no hardcoded field names" in text

    chart_spec = next(spec for spec in tools.TOOL_SPECS if spec["name"] == "create_chart")
    assert "label_field" in chart_spec["description"]
    assert "readable display labels" in chart_spec["description"]
    assert "label_field" in chart_spec["input_schema"]["properties"]


def test_layer_removal_guidance_uses_structured_tools():
    prompt_texts = [
        api_backend.DEFAULT_SYSTEM_PROMPT,
        openai_backend.DEFAULT_SYSTEM_PROMPT,
        cli_backend.CliToolBackend({"cli_tool": "codex"}, None)._with_guardrails("clear layers"),
    ]

    for text in prompt_texts:
        assert "remove_layer" in text
        assert "clear_layers" in text
        assert "source files" in text

    remove_spec = next(spec for spec in tools.TOOL_SPECS if spec["name"] == "remove_layer")
    clear_spec = next(spec for spec in tools.TOOL_SPECS if spec["name"] == "clear_layers")
    assert "never deletes" in remove_spec["description"]
    assert "layer_id" in remove_spec["input_schema"]["properties"]
    assert clear_spec["input_schema"]["required"] == ["confirm"]


if __name__ == "__main__":
    test_prompts_preserve_scope_and_large_layer_safety()
    test_run_pyqgis_description_discourages_large_layer_loops()
    test_chart_label_field_guidance_is_generic()
    test_layer_removal_guidance_uses_structured_tools()
