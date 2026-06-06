import os
import sys
import unittest
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.backends.cli_backend import CliToolBackend, NormalizingStream
from AgenticGis.backends.adapters import get_adapter


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Executor:
    def run_sync(self, fn, timeout=None):
        return fn()


class _Toolkit:
    def get_layer_fields(self, layer_id):
        return {
            "ok": True,
            "layer_id": layer_id,
            "fields": [{"name": "name", "type": "String"}],
        }


class _EmptyPostToolCliBackend(CliToolBackend):
    def __init__(self):
        super().__init__(
            _Config({
                "cli_tool": "codex",
                "max_iterations": 3,
                "cli_final_answer_timeout": 1,
            }),
            _Toolkit(),
            _Executor(),
        )
        self.binary = "/bin/echo"
        self._runs = 0

    def _run_stream(self, adapter, request_body, emit, should_stop, timeout=None):
        self._runs += 1
        if self._runs == 1:
            return SimpleNamespace(
                content_blocks=[{
                    "type": "tool_use",
                    "id": "cli_tool_call_1",
                    "name": "get_layer_fields",
                    "input": {"layer_id": "layer-1"},
                }],
                finish_reason="tool_calls",
                had_error=False,
                timed_out=False,
                pending_tool_call=None,
                final_text=None,
            )
        return SimpleNamespace(
            content_blocks=[],
            finish_reason="stop",
            had_error=False,
            timed_out=False,
            pending_tool_call=None,
            final_text=None,
        )


class _PostToolFinalCliBackend(CliToolBackend):
    def __init__(self):
        super().__init__(
            _Config({
                "cli_tool": "codex",
                "max_iterations": 3,
                "cli_final_answer_timeout": 1,
            }),
            _Toolkit(),
            _Executor(),
        )
        self.binary = "/bin/echo"
        self._runs = 0
        self.request_bodies = []
        self.timeouts = []

    def _run_stream(self, adapter, request_body, emit, should_stop, timeout=None):
        self._runs += 1
        self.request_bodies.append(request_body)
        self.timeouts.append(timeout)
        if self._runs == 1:
            return SimpleNamespace(
                content_blocks=[{
                    "type": "tool_use",
                    "id": "cli_tool_call_1",
                    "name": "get_layer_fields",
                    "input": {"layer_id": "layer-1"},
                }],
                finish_reason="tool_calls",
                had_error=False,
                timed_out=False,
                pending_tool_call=None,
                final_text=None,
            )
        # Emit text event like the real NormalizingStream would
        emit(AgentEvent(EventType.TEXT, {"text": "Layer layer-1 has one field: name."}))
        return SimpleNamespace(
            content_blocks=[{
                "type": "text",
                "text": "Layer layer-1 has one field: name.",
            }],
            finish_reason="stop",
            had_error=False,
            timed_out=False,
            pending_tool_call=None,
            final_text="Layer layer-1 has one field: name.",
        )


class _ErrorOnlyCliBackend(CliToolBackend):
    def __init__(self):
        super().__init__(
            _Config({
                "cli_tool": "codex",
                "max_iterations": 3,
            }),
            _Toolkit(),
            _Executor(),
        )
        self.binary = "/bin/echo"

    def _run_stream(self, adapter, request_body, emit, should_stop, timeout=None):
        emit(AgentEvent(EventType.ERROR, {"error": "Codex failed"}))
        return SimpleNamespace(
            content_blocks=[],
            finish_reason=None,
            had_error=True,
            timed_out=False,
            pending_tool_call=None,
            final_text=None,
        )


class _CodexNativeToolThenFinalBackend(CliToolBackend):
    def __init__(self):
        super().__init__(
            _Config({
                "cli_tool": "codex",
                "max_iterations": 3,
            }),
            _Toolkit(),
            _Executor(),
        )
        self.binary = "/bin/echo"

    def _run_stream(self, adapter, request_body, emit, should_stop, timeout=None):
        stream = NormalizingStream(adapter, emit)
        stream.feed_line(
            b'{"type":"exec_command_end","parsed_cmd":["python","-V"],'
            b'"aggregated_output":"Python 3.12.0\\n","exit_code":0}'
        )
        stream.feed_line(
            b'{"type":"task_complete","last_agent_message":'
            b'"Layer layer-1 has one field: name."}'
        )
        return stream


class _CodexDeltaThenTaskCompleteBackend(CliToolBackend):
    def __init__(self):
        super().__init__(
            _Config({
                "cli_tool": "codex",
                "max_iterations": 3,
            }),
            _Toolkit(),
            _Executor(),
        )
        self.binary = "/bin/echo"

    def _run_stream(self, adapter, request_body, emit, should_stop, timeout=None):
        stream = NormalizingStream(adapter, emit)
        stream.feed_line(
            b'{"type":"agent_message_content_delta","delta":"Layer layer-1 "}'
        )
        stream.feed_line(
            b'{"type":"agent_message_content_delta","delta":"has one field: name."}'
        )
        stream.feed_line(
            b'{"type":"task_complete","last_agent_message":'
            b'"Layer layer-1 has one field: name."}'
        )
        return stream


class CliBackendTests(unittest.TestCase):
    def test_post_tool_empty_cli_response_emits_visible_fallback(self):
        backend = _EmptyPostToolCliBackend()
        events = []

        backend.send(
            "fields apa saja?",
            [],
            events.append,
            lambda: False,
        )

        text_events = [ev for ev in events if ev.type == EventType.TEXT]
        self.assertEqual(1, len(text_events))
        self.assertIn("completed without returning a response", text_events[0].data["text"])
        self.assertEqual(EventType.DONE, events[-1].type)

    def test_error_only_cli_response_does_not_emit_generic_fallback(self):
        backend = _ErrorOnlyCliBackend()
        events = []

        history = backend.send(
            "hello",
            [],
            events.append,
            lambda: False,
        )

        self.assertEqual(
            ["Codex failed"],
            [ev.data["error"] for ev in events if ev.type == EventType.ERROR],
        )
        self.assertEqual([], [ev for ev in events if ev.type == EventType.TEXT])
        self.assertEqual([{"role": "user", "content": "hello"}], history)

    def test_codex_native_tooling_uses_task_complete_final_message(self):
        backend = _CodexNativeToolThenFinalBackend()
        events = []

        history = backend.send(
            "fields apa saja?",
            [],
            events.append,
            lambda: False,
        )

        self.assertEqual(
            ["Layer layer-1 has one field: name."],
            [ev.data["text"] for ev in events if ev.type == EventType.TEXT],
        )
        self.assertEqual(
            {"role": "assistant", "content": "Layer layer-1 has one field: name."},
            history[-1],
        )
        self.assertNotIn(
            "The CLI agent completed without returning a response.",
            history[-1]["content"],
        )
        self.assertEqual(EventType.DONE, events[-1].type)

    def test_codex_task_complete_does_not_duplicate_streamed_deltas(self):
        backend = _CodexDeltaThenTaskCompleteBackend()
        events = []

        history = backend.send(
            "fields apa saja?",
            [],
            events.append,
            lambda: False,
        )

        self.assertEqual(
            ["Layer layer-1 ", "has one field: name."],
            [ev.data["text"] for ev in events if ev.type == EventType.TEXT],
        )
        self.assertEqual(
            {"role": "assistant", "content": "Layer layer-1 has one field: name."},
            history[-1],
        )
        self.assertEqual(EventType.DONE, events[-1].type)

    def test_normalizing_stream_tracks_adapter_errors(self):
        events = []
        stream = NormalizingStream(get_adapter("codex"), events.append)

        stream.feed_line(
            b'{"type":"turn.failed","message":"Authentication failed"}'
        )

        self.assertTrue(stream.had_error)
        self.assertEqual(
            ["Authentication failed"],
            [ev.data["error"] for ev in events if ev.type == EventType.ERROR],
        )

    def test_generic_fallback_extracts_text_from_unknown_json(self):
        """If a CLI outputs an event type the adapter doesn't know, the
        generic fallback should still surface assistant text so the user
        sees a response instead of the empty fallback."""
        events = []
        stream = NormalizingStream(get_adapter("claude"), events.append)

        # Unknown event type with text nested deeply
        stream.feed_line(
            b'{"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"Hello from CLI"}}'
        )

        self.assertFalse(stream.had_error)
        self.assertEqual(
            ["Hello from CLI"],
            [ev.data["text"] for ev in events if ev.type == EventType.TEXT],
        )
        # finish_reason should remain None because is_final was not set
        self.assertIsNone(stream.finish_reason)

    def test_generic_fallback_skips_command_events(self):
        """Tool/command lifecycle events must not leak as chat text."""
        events = []
        stream = NormalizingStream(get_adapter("codex"), events.append)

        stream.feed_line(
            b'{"type":"exec_command_end","parsed_cmd":["python","-V"],'
            b'"aggregated_output":"Python 3.12.0\\n","exit_code":0}'
        )

        self.assertEqual([], [ev for ev in events if ev.type == EventType.TEXT])
        self.assertEqual([], stream.content_blocks)

    def test_generic_fallback_skips_step_start_lifecycle(self):
        """Lifecycle events like step_start must not leak as chat text."""
        events = []
        stream = NormalizingStream(get_adapter("codex"), events.append)

        # Codex-style step_start event with text echoing the type name
        stream.feed_line(
            b'{"type":"step_start","text":"step_start"}'
        )

        self.assertEqual([], [ev for ev in events if ev.type == EventType.TEXT])
        self.assertEqual([], stream.content_blocks)

    def test_generic_fallback_skips_uuid_only_text(self):
        """UUID-only strings must not leak as chat text."""
        events = []
        stream = NormalizingStream(get_adapter("codex"), events.append)

        # Event whose text field is just a UUID
        stream.feed_line(
            b'{"type":"unknown","text":"019e991e-073c-71b3-ba7f-dc3d40bac178"}'
        )

        self.assertEqual([], [ev for ev in events if ev.type == EventType.TEXT])
        self.assertEqual([], stream.content_blocks)


class ParseProtocolTextTests(unittest.TestCase):
    """Tests for CliAdapter.parse_protocol_text — the function that
    extracts tool-call JSON from CLI agent text output."""

    def setUp(self):
        self.adapter = get_adapter("opencode")

    def test_plain_json_no_markdown(self):
        result = self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":[{"name":"get_layers","arguments":{}}]}'
        )
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("get_layers", result.tool_calls[0]["name"])
        self.assertEqual({}, result.tool_calls[0]["arguments"])
        self.assertTrue(result.is_final)

    def test_json_in_markdown_code_block_with_tag(self):
        result = self.adapter.parse_protocol_text(
            "```json\n"
            '{"type":"tool_calls","calls":[{"name":"get_layers","arguments":{}}]}\n'
            "```"
        )
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("get_layers", result.tool_calls[0]["name"])

    def test_json_in_markdown_code_block_without_tag(self):
        result = self.adapter.parse_protocol_text(
            "```\n"
            '{"type":"tool_calls","calls":[{"name":"get_layers","arguments":{}}]}\n'
            "```"
        )
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("get_layers", result.tool_calls[0]["name"])

    def test_text_before_code_block(self):
        """LLM sometimes adds a brief thought before the code block."""
        result = self.adapter.parse_protocol_text(
            "Let me check the layers.\n"
            "```json\n"
            '{"type":"tool_calls","calls":[{"name":"get_layers","arguments":{}}]}\n'
            "```"
        )
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.tool_calls))

    def test_empty_text_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text("   \n  \t  "))

    def test_normal_text_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            "I found 3 layers in the project."
        ))

    def test_malformed_json_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":[broken}'
        ))

    def test_malformed_json_in_code_block_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            "```json\n"
            '{"type":"tool_calls","calls":[broken}\n'
            "```"
        ))

    def test_wrong_type_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            '{"type":"text","content":"hello"}'
        ))

    def test_empty_calls_array_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":[]}'
        ))

    def test_empty_code_block_returns_none(self):
        self.assertIsNone(self.adapter.parse_protocol_text(
            "```json\n\n```"
        ))

    def test_multiple_tool_calls(self):
        result = self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":['
            '{"name":"get_layers","arguments":{}},'
            '{"name":"get_layer_info","arguments":{"layer_id":"1"}}'
            "]}"
        )
        self.assertIsNotNone(result)
        self.assertEqual(2, len(result.tool_calls))
        self.assertEqual("get_layers", result.tool_calls[0]["name"])
        self.assertEqual("get_layer_info", result.tool_calls[1]["name"])

    def test_none_arguments(self):
        result = self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":[{"name":"get_layers","arguments":null}]}'
        )
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual({}, result.tool_calls[0]["arguments"])

    def test_tool_with_arguments(self):
        result = self.adapter.parse_protocol_text(
            '{"type":"tool_calls","calls":['
            '{"name":"get_layer_info","arguments":{"layer_id":"roads"}}'
            "]}"
        )
        self.assertIsNotNone(result)
        self.assertEqual("roads", result.tool_calls[0]["arguments"]["layer_id"])


if __name__ == "__main__":
    unittest.main()
