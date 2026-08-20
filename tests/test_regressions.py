"""Regression contracts for shutdown, unsupported platforms, and exports."""
from __future__ import annotations

import threading
import unittest

import clipboard_event
from clipboard_event import Clipboard
from clipboard_event._core import _STOP

from .fake_backend import (
    CallbackLog,
    CloseRaceBackend,
    FakeClipboardBackend,
    UnsupportedBackend,
)


class ShutdownObservedClipboard(Clipboard):
    def __init__(self, backend) -> None:
        super().__init__(backend=backend)
        self.shutdown_enqueued = threading.Event()

    def _put_latest(self, events, event) -> None:
        super()._put_latest(events, event)
        if event is _STOP:
            self.shutdown_enqueued.set()


class UnsupportedBackendContracts(unittest.TestCase):
    def test_unsupported_backend_fails_synchronously_without_starting_threads(self):
        clipboard = Clipboard(backend=UnsupportedBackend())
        threads_before = set(threading.enumerate())

        with self.assertRaises(RuntimeError):
            clipboard.read()
        with self.assertRaises(RuntimeError):
            clipboard.write("value")
        with self.assertRaises(RuntimeError):
            clipboard.on_change(lambda _value: None)

        self.assertEqual(set(threading.enumerate()), threads_before)
        clipboard.close()


class CloseFromCallbackContracts(unittest.TestCase):
    def test_callback_can_close_then_monitoring_restarts_without_stale_subscribers(self):
        backend = FakeClipboardBackend()
        clipboard = Clipboard(backend=backend)
        self.addCleanup(clipboard.close)
        close_returned = threading.Event()
        release_callback = threading.Event()
        stale = CallbackLog()
        restarted = CallbackLog()

        def closing_callback(_value):
            clipboard.close()
            close_returned.set()
            if not release_callback.wait(1.0):
                raise AssertionError("closing callback was not released")

        clipboard.on_change(closing_callback)
        clipboard.on_change(stale)
        old_dispatcher = clipboard._dispatcher_thread
        backend.external_change("close now")
        self.assertTrue(close_returned.wait(1.0))

        clipboard.on_change(restarted)
        release_callback.set()
        self.assertIsNotNone(old_dispatcher)
        old_dispatcher.join(timeout=1.0)
        self.assertFalse(old_dispatcher.is_alive())
        self.assertEqual(stale.values, [])

        backend.external_change("after restart")
        restarted.wait_for_value("after restart")


class NarrowCloseRaceContracts(unittest.TestCase):
    def test_post_stop_monitor_read_cannot_replace_dispatcher_shutdown(self):
        backend = CloseRaceBackend()
        clipboard = ShutdownObservedClipboard(backend)
        clipboard.on_change(lambda _value: None)
        backend.external_change("race")
        self.assertTrue(backend.monitor_read_entered.wait(1.0))
        dispatcher = clipboard._dispatcher_thread
        close_finished = threading.Event()

        def close_clipboard():
            clipboard.close()
            close_finished.set()

        closer = threading.Thread(target=close_clipboard)
        closer.start()
        self.assertTrue(backend.close_called.wait(1.0))
        self.assertTrue(clipboard.shutdown_enqueued.wait(1.0))

        backend.release_monitor_read.set()

        self.assertTrue(close_finished.wait(3.0))
        closer.join(timeout=1.0)
        self.assertFalse(closer.is_alive())
        self.assertIsNotNone(dispatcher)
        self.assertFalse(dispatcher.is_alive())


class PublicTypingContracts(unittest.TestCase):
    def test_callback_and_backend_types_are_public_exports(self):
        from clipboard_event import Backend, Callback

        self.assertIs(Callback, clipboard_event.Callback)
        self.assertIs(Backend, clipboard_event.Backend)
        self.assertIn("Callback", clipboard_event.__all__)
        self.assertIn("Backend", clipboard_event.__all__)


if __name__ == "__main__":
    unittest.main()
