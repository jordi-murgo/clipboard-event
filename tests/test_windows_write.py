"""Regression contracts for the Windows backend's clipboard write retry.

The Windows clipboard is a shared resource contended by clipboard managers,
RDP redirection (rdpclip), and peer clipboard tunnels. A write whose
``OpenClipboard`` succeeds can still fail inside the sequence
(``EmptyClipboard``/``SetClipboardData``) when a peer races us. These
contracts pin the library-side retry so callers no longer observe
``OSError: SetClipboardData failed`` bursts.

The win32 entry points are duck-typed fakes so the contracts run on every
platform, not only on Windows runners.
"""
from __future__ import annotations

import ctypes
import unittest

from clipboard_event._backends import (
    _windows_write_once,
    _windows_write_with_retry,
)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class FakeKernel32:
    """Duck-typed kernel32 with real writable allocations."""

    def __init__(self) -> None:
        self._allocations: list[ctypes.Array] = []
        self.allocations: list[int] = []
        self.freed: list[int] = []
        self.last_error = 5  # ERROR_ACCESS_DENIED

    def GlobalAlloc(self, flags: int, size: int) -> int:
        buffer = ctypes.create_string_buffer(size)
        address = ctypes.addressof(buffer)
        self._allocations.append(buffer)  # keep the memory alive
        self.allocations.append(address)
        return address

    def GlobalLock(self, handle: int) -> int:
        return handle

    def GlobalUnlock(self, handle: int) -> int:
        return 1

    def GlobalFree(self, handle: int) -> int:
        self.freed.append(handle)
        return 0

    def GetLastError(self) -> int:
        return self.last_error


class FakeUser32:
    def __init__(
        self,
        set_clipboard_failures: int = 0,
        open_failures: int = 0,
    ) -> None:
        self._set_clipboard_failures = set_clipboard_failures
        self._open_failures = open_failures
        self.open_calls = 0
        self.empty_calls = 0
        self.set_calls = 0
        self.close_calls = 0

    def OpenClipboard(self, owner: int) -> int:
        self.open_calls += 1
        if self.open_calls <= self._open_failures:
            return 0
        return 1

    def EmptyClipboard(self) -> int:
        self.empty_calls += 1
        return 1

    def SetClipboardData(self, format_id: int, handle: int) -> int:
        self.set_calls += 1
        if self.set_calls <= self._set_clipboard_failures:
            return 0
        return handle

    def CloseClipboard(self) -> int:
        self.close_calls += 1
        return 1


def payload(value: str) -> bytes:
    return (value + "\0").encode("utf-16-le")


def no_sleep(_: float) -> None:
    pass


class WindowsWriteRetryContracts(unittest.TestCase):
    def test_transient_setclipboarddata_failure_is_retried_until_success(self):
        user32 = FakeUser32(set_clipboard_failures=2)
        kernel32 = FakeKernel32()

        _windows_write_with_retry(
            user32, kernel32, payload("hola"), attempts=5, backoff=0.0, sleep=no_sleep
        )

        self.assertEqual(user32.set_calls, 3)
        self.assertEqual(user32.close_calls, 3)

    def test_openclipboard_busy_is_retried_until_success(self):
        user32 = FakeUser32(open_failures=2)
        kernel32 = FakeKernel32()

        _windows_write_with_retry(
            user32, kernel32, payload("hola"), attempts=5, backoff=0.0, sleep=no_sleep
        )

        self.assertEqual(user32.open_calls, 3)
        self.assertEqual(user32.set_calls, 1)

    def test_persistent_failure_raises_after_bounded_attempts(self):
        user32 = FakeUser32(set_clipboard_failures=100)
        kernel32 = FakeKernel32()

        with self.assertRaises(OSError) as raised:
            _windows_write_with_retry(
                user32,
                kernel32,
                payload("hola"),
                attempts=3,
                backoff=0.0,
                sleep=no_sleep,
            )

        self.assertEqual(user32.set_calls, 3)
        self.assertIn("3 attempts", str(raised.exception))
        self.assertIn("SetClipboardData", str(raised.exception))
        self.assertIn("Win32 error 5", str(raised.exception))

    def test_failed_attempt_frees_allocation_and_closes_clipboard(self):
        user32 = FakeUser32(set_clipboard_failures=2)
        kernel32 = FakeKernel32()

        _windows_write_with_retry(
            user32, kernel32, payload("hola"), attempts=5, backoff=0.0, sleep=no_sleep
        )

        self.assertEqual(len(kernel32.freed), 2)
        self.assertEqual(len(kernel32.allocations), 3)

    def test_single_attempt_success_transfers_payload(self):
        user32 = FakeUser32()
        kernel32 = FakeKernel32()
        wire = payload("hola tú")

        transferred = _windows_write_once(user32, kernel32, wire)

        self.assertTrue(transferred)
        self.assertEqual(user32.close_calls, 1)
        self.assertEqual(kernel32.freed, [])
        self.assertEqual(
            ctypes.string_at(kernel32.allocations[-1], len(wire)), wire
        )


if __name__ == "__main__":
    unittest.main()
