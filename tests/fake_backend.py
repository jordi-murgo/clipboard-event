"""Deterministic backend for clipboard-event contract tests."""
from __future__ import annotations

import threading
import time


class FakeClipboardBackend:
    backend_name = "fake"

    def __init__(self, initial: str | None = None) -> None:
        self._condition = threading.Condition()
        self._value = initial
        self._revision = 0
        self.monitor_close_count = 0

    @property
    def revision(self) -> int:
        with self._condition:
            return self._revision

    def read(self) -> str | None:
        with self._condition:
            return self._value

    def write(self, value: str) -> None:
        self.external_change(value)

    def external_change(self, value: str | None) -> None:
        with self._condition:
            self._value = value
            self._revision += 1
            self._condition.notify_all()

    def wait_for_revision(self, after: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._revision <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._revision
                self._condition.wait(remaining)
            return self._revision

    def close_monitor(self) -> None:
        with self._condition:
            self.monitor_close_count += 1
            self._revision += 1
            self._condition.notify_all()

class CallbackLog:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.values: list[str | None] = []
        self.thread_ids: list[int] = []

    def __call__(self, value: str | None) -> None:
        with self._condition:
            self.values.append(value)
            self.thread_ids.append(threading.get_ident())
            self._condition.notify_all()

    def wait_for_value(self, value: str | None, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while value not in self.values:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"callback did not receive {value!r}")
                self._condition.wait(remaining)

    def wait_for_count(self, count: int, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.values) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"callback count did not reach {count}")
                self._condition.wait(remaining)


class UnsupportedBackend:
    backend_name = "unsupported"

    @property
    def revision(self) -> int:
        raise RuntimeError("unsupported clipboard")

    def read(self) -> str | None:
        raise RuntimeError("unsupported clipboard")

    def write(self, _value: str) -> None:
        raise RuntimeError("unsupported clipboard")

    def wait_for_revision(self, _after: int, _timeout: float) -> int:
        raise RuntimeError("unsupported clipboard")

    def close_monitor(self) -> None:
        pass


class CloseRaceBackend(FakeClipboardBackend):
    """Hold the monitor in its post-wakeup read until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.monitor_read_entered = threading.Event()
        self.release_monitor_read = threading.Event()
        self.close_called = threading.Event()
        self._reads = 0

    def read(self) -> str | None:
        with self._condition:
            self._reads += 1
            reads = self._reads
        if reads >= 2:
            self.monitor_read_entered.set()
            if not self.release_monitor_read.wait(1.0):
                raise AssertionError("monitor read was not released")
        return super().read()

    def close_monitor(self) -> None:
        self.close_called.set()
        super().close_monitor()
