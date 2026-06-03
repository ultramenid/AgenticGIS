"""Cancellation primitives shared by the executor, toolkit, and MCP server.

This module deliberately has **no QGIS dependencies** so it can be unit-
tested without the full QGIS environment. The plugin's other components
import the registry from here.
"""

import threading


class CancellationRegistry:
    """Tracks a single in-flight cancellation token.

    Setting ``cancel()`` flips the event; readers see it via ``is_cancelled``
    or the helper function injected into agent code (``_cancel_check``). Only
    one job is allowed to be cancellable at a time — the second caller gets
    ``False`` from ``register`` and its dispatch is *not* cooperative, but it
    still completes and tears its token down immediately.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._event = None  # type: threading.Event | None

    def register(self):
        """Return a ``(event, owner)`` pair, or ``(event, False)`` if occupied.

        ``owner`` is True when the returned event is the active token (the
        caller can poll it). When ``owner`` is False the event is a no-op
        and the caller's tool runs without cooperation.
        """
        with self._lock:
            if self._event is not None:
                return self._event, False
            self._event = threading.Event()
            return self._event, True

    def release(self, event):
        with self._lock:
            if self._event is event:
                self._event = None

    def cancel(self):
        with self._lock:
            if self._event is not None:
                self._event.set()

    def is_cancelled(self):
        with self._lock:
            return self._event is not None and self._event.is_set()

    def event(self):
        with self._lock:
            return self._event
