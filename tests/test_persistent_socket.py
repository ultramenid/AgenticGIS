import os
import sys
import threading
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from AgenticGis.backends.openai_http import _PersistentSocket


class TestPersistentSocketClose(unittest.TestCase):
    def test_close_sets_short_timeout_before_close(self):
        """Close must shorten the socket timeout before closing it.

        When ``_close()`` runs on the GUI thread while a worker thread is
        blocked in ``recv()``, the close-from-another-thread race can wedge
        the worker (especially for TLS sockets). Shortening the timeout
        first ensures the in-flight ``recv()`` returns within ~50ms, the
        worker sees the cancellation flag, and exits cleanly.
        """
        sock = _PersistentSocket.__new__(_PersistentSocket)
        sock.host = "example.com"
        sock.is_https = True
        sock.timeout = 5
        sock._lock = threading.Lock()
        sock._line_buffer = bytearray()

        events = []

        class MockSock:
            def settimeout(self, t):
                events.append(("settimeout", t))

            def close(self):
                events.append(("close",))

        sock._sock = MockSock()
        sock._close()

        settimeout_calls = [e for e in events if e[0] == "settimeout"]
        close_calls = [e for e in events if e[0] == "close"]
        self.assertEqual(len(settimeout_calls), 1,
                         f"Expected exactly one settimeout, got {events!r}")
        self.assertEqual(len(close_calls), 1,
                         f"Expected exactly one close, got {events!r}")
        self.assertLessEqual(
            settimeout_calls[0][1], 0.5,
            f"Close-time timeout must be <=0.5s, got {settimeout_calls[0][1]!r}",
        )
        # settimeout must come before close
        self.assertLess(events.index(settimeout_calls[0]),
                        events.index(close_calls[0]),
                        "settimeout must be called before close()")
        # And _close must clear _sock
        self.assertIsNone(sock._sock,
                          "_close() must set _sock to None after closing")

    def test_close_handles_settimeout_failure(self):
        """A socket that rejects settimeout (e.g. already closed) must not
        prevent the close from completing.
        """
        sock = _PersistentSocket.__new__(_PersistentSocket)
        sock.host = "example.com"
        sock.is_https = True
        sock.timeout = 5
        sock._lock = threading.Lock()
        sock._line_buffer = bytearray()

        class BadSock:
            def settimeout(self, t):
                raise OSError("already closed")

            def close(self):
                pass

        sock._sock = BadSock()
        # Must not raise
        sock._close()
        self.assertIsNone(sock._sock)

    def test_close_idempotent_when_sock_already_none(self):
        """Calling _close() twice must not crash."""
        sock = _PersistentSocket.__new__(_PersistentSocket)
        sock.host = "example.com"
        sock.is_https = True
        sock.timeout = 5
        sock._lock = threading.Lock()
        sock._line_buffer = bytearray()
        sock._sock = None
        sock._close()  # must not raise


if __name__ == "__main__":
    unittest.main()
