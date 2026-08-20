"""Public contracts for clipboard-event's instance and module APIs."""
from __future__ import annotations

import importlib
import threading
import unittest
from unittest.mock import MagicMock, patch
import clipboard_event
from clipboard_event import Clipboard

from .fake_backend import CallbackLog, FakeClipboardBackend


class ClipboardInstanceContracts(unittest.TestCase):
    def make_clipboard(self, initial: str | None = None):
        backend = FakeClipboardBackend(initial)
        clipboard = Clipboard(backend=backend)
        self.addCleanup(clipboard.close)
        return clipboard, backend

    def test_read_and_write_round_trip(self):
        clipboard, _ = self.make_clipboard()

        self.assertIsNone(clipboard.read())
        self.assertIsNone(clipboard.write("written"))
        self.assertEqual(clipboard.read(), "written")

    def test_own_write_triggers_callback(self):
        clipboard, _ = self.make_clipboard()
        calls = CallbackLog()
        clipboard.on_change(calls)

        clipboard.write("own write")

        calls.wait_for_value("own write")

    def test_external_change_triggers_callback(self):
        clipboard, backend = self.make_clipboard()
        calls = CallbackLog()
        clipboard.on_change(calls)

        backend.external_change("external")

        calls.wait_for_value("external")

    def test_emit_initial_delivers_current_value(self):
        clipboard, _ = self.make_clipboard("already present")
        calls = CallbackLog()

        clipboard.on_change(calls, emit_initial=True)

        calls.wait_for_value("already present")

    def test_cancel_removes_only_that_subscription(self):
        clipboard, backend = self.make_clipboard()
        cancelled_calls = CallbackLog()
        active_calls = CallbackLog()
        cancelled = clipboard.on_change(cancelled_calls)
        clipboard.on_change(active_calls)
        cancelled.cancel()
        cancelled.cancel()

        backend.external_change("after cancel")

        active_calls.wait_for_value("after cancel")
        self.assertEqual(cancelled_calls.values, [])

    def test_close_is_idempotent_cancels_all_and_releases_monitor(self):
        clipboard, backend = self.make_clipboard("before")
        calls = CallbackLog()
        clipboard.on_change(calls)

        clipboard.close()
        clipboard.close()
        backend.external_change("after close")

        self.assertEqual(calls.values, [])
        self.assertEqual(backend.monitor_close_count, 1)
        self.assertEqual(clipboard.read(), "after close")
        clipboard.write("still usable")
        self.assertEqual(clipboard.read(), "still usable")

    def test_on_change_restarts_monitoring_after_close(self):
        clipboard, backend = self.make_clipboard()
        clipboard.close()
        calls = CallbackLog()

        clipboard.on_change(calls)
        backend.external_change("restarted")

        calls.wait_for_value("restarted")

    def test_callback_exception_does_not_stop_other_subscriptions(self):
        clipboard, backend = self.make_clipboard()
        healthy_calls = CallbackLog()

        def broken_callback(_value):
            raise RuntimeError("callback failure")

        clipboard.on_change(broken_callback)
        clipboard.on_change(healthy_calls)

        backend.external_change("survives")

        healthy_calls.wait_for_value("survives")

    def test_callbacks_run_sequentially_on_one_dispatcher_thread(self):
        clipboard, backend = self.make_clipboard()
        first = CallbackLog()
        second = CallbackLog()
        clipboard.on_change(first)
        clipboard.on_change(second)
        caller_thread = threading.get_ident()

        backend.external_change("one dispatch")
        first.wait_for_value("one dispatch")
        second.wait_for_value("one dispatch")
        callback_threads = set(first.thread_ids + second.thread_ids)
        self.assertEqual(len(callback_threads), 1)
        self.assertNotIn(caller_thread, callback_threads)



    def test_backend_name_reports_selected_backend(self):
        clipboard, _ = self.make_clipboard()

        self.assertEqual(clipboard.backend_name, "fake")


class ModuleFacadeContracts(unittest.TestCase):
    def test_module_facade_exposes_lazy_default_surface(self):
        self.assertTrue(callable(clipboard_event.read))
        self.assertTrue(callable(clipboard_event.write))
        self.assertTrue(callable(clipboard_event.on_change))
        self.assertTrue(callable(clipboard_event.close))
    def test_module_facade_uses_one_lazy_default_instance(self):
        module = importlib.reload(clipboard_event)
        default = MagicMock()
        default.read.return_value = "facade value"
        default.write.return_value = None
        default.close.return_value = None
        subscription = object()
        default.on_change.return_value = subscription

        with patch.object(module, "Clipboard", return_value=default) as constructor:
            callback = lambda _value: None
            self.assertEqual(module.read(), "facade value")
            self.assertIsNone(module.write("facade write"))
            self.assertIs(module.on_change(callback, emit_initial=True), subscription)
            self.assertIsNone(module.close())

        constructor.assert_called_once_with()
        default.write.assert_called_once_with("facade write")
        default.on_change.assert_called_once_with(callback, emit_initial=True)
        default.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
