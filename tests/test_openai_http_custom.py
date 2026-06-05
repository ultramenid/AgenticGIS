"""Tests for OpenAIHttpClient robustness with non-standard endpoints."""

import json
import os
import sys
import unittest
from io import BytesIO

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from AgenticGis.backends.openai_http import OpenAIHttpClient, OpenAIHttpError


class MockSSEResponse:
    """A file-like object that yields SSE lines one by one."""

    def __init__(self, lines):
        self._lines = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]
        self._idx = 0

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class TestOpenAIHttpClientCustomEndpoint(unittest.TestCase):
    """Custom / non-standard OpenAI-compatible endpoints may return deltas
    with ``tool_calls`` explicitly set to ``null``.  The client must not
    crash with *TypeError: 'NoneType' object is not iterable*.
    """

    def _patch_urlopen(self, lines):
        """Return a context manager that patches ``urllib.request.urlopen``
        to yield the given SSE lines."""
        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, **kwargs):
            return MockSSEResponse(lines)

        class _Patcher:
            def __enter__(p):
                urllib.request.urlopen = fake_urlopen
                return p

            def __exit__(p, *exc):
                urllib.request.urlopen = original
                return False

        return _Patcher()

    def test_null_tool_calls_delta_does_not_crash(self):
        """A chunk where ``delta.tool_calls`` is ``null`` must be skipped
        gracefully instead of raising *TypeError*.
        """
        lines = [
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }]
            }) + "\n",
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }]
            }) + "\n",
            # Non-standard endpoint may send tool_calls: null
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"tool_calls": None},
                    "finish_reason": None,
                }]
            }) + "\n",
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"content": " world"},
                    "finish_reason": "stop",
                }]
            }) + "\n",
            "data: [DONE]\n",
        ]

        client = OpenAIHttpClient(api_key="fake", base_url="http://localhost:9999")
        texts = []

        with self._patch_urlopen(lines):
            blocks, finish_reason = client.stream_message(
                model="test-model",
                max_tokens=100,
                system="",
                tools=[],
                messages=[],
                on_text=texts.append,
                should_stop=lambda: False,
            )

        self.assertEqual("".join(texts), "Hello world")
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], {"type": "text", "text": "Hello world"})

    def test_empty_choices_list_does_not_crash(self):
        """Some custom endpoints emit chunks with ``choices: []`` (e.g.
        keep-alive pings or intermediate routing events).  The default
        ``event.get("choices", [{}])`` is **not** enough — when the key
        exists with an empty list the default is ignored and ``[0]`` on
        the empty list raises *IndexError*.
        """
        lines = [
            "data: " + json.dumps({"choices": []}) + "\n",
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }]
            }) + "\n",
            "data: " + json.dumps({
                "choices": [{
                    "delta": {},
                    "finish_reason": "stop",
                }]
            }) + "\n",
            "data: [DONE]\n",
        ]

        client = OpenAIHttpClient(api_key="fake", base_url="http://localhost:9999")
        texts = []

        with self._patch_urlopen(lines):
            blocks, finish_reason = client.stream_message(
                model="test-model",
                max_tokens=100,
                system="",
                tools=[],
                messages=[],
                on_text=texts.append,
                should_stop=lambda: False,
            )

        self.assertEqual("".join(texts), "Hello")
        self.assertEqual(finish_reason, "stop")

    def test_tool_calls_present_then_none_then_present(self):
        """A tool call may start, be interrupted by a ``null`` delta, then
        resume.  The client must collect the resumed tool call correctly.
        """
        lines = [
            "data: " + json.dumps({
                "choices": [{
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "tc_1", "type": "function",
                             "function": {"name": "list_layers", "arguments": ""}}
                        ]
                    },
                    "finish_reason": None,
                }]
            }) + "\n",
            # Rogue null delta from a flaky proxy / custom endpoint
            "data: " + json.dumps({
                "choices": [{
                    "delta": {"tool_calls": None},
                    "finish_reason": None,
                }]
            }) + "\n",
            "data: " + json.dumps({
                "choices": [{
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": "{}"}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }]
            }) + "\n",
            "data: [DONE]\n",
        ]

        client = OpenAIHttpClient(api_key="fake", base_url="http://localhost:9999")

        with self._patch_urlopen(lines):
            blocks, finish_reason = client.stream_message(
                model="test-model",
                max_tokens=100,
                system="",
                tools=[],
                messages=[],
                on_text=lambda _t: None,
                should_stop=lambda: False,
            )

        self.assertEqual(finish_reason, "tool_calls")
        tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0]["name"], "list_layers")


if __name__ == "__main__":
    unittest.main()
