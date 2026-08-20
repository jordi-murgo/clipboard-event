from __future__ import annotations

import queue
import threading
import weakref
from collections.abc import Callable
from typing import Protocol


Callback = Callable[[str | None], None]
_STOP = object()


class Backend(Protocol):
    @property
    def backend_name(self) -> str: ...

    @property
    def revision(self) -> int: ...

    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...

    def wait_for_revision(self, after: int, timeout: float) -> int: ...

    def close_monitor(self) -> None: ...


class Subscription:
    """A cancellable clipboard change subscription."""

    def __init__(self, clipboard: Clipboard, subscription_id: int) -> None:
        self._clipboard = weakref.ref(clipboard)
        self._subscription_id = subscription_id
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        """Cancel this subscription. Repeated calls have no effect."""
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
        clipboard = self._clipboard()
        if clipboard is not None:
            clipboard._cancel(self._subscription_id)


class Clipboard:
    """Clipboard access with sequential Python-thread change callbacks."""

    _WATCHDOG_INTERVAL = 1.0

    def __init__(self, backend: Backend | None = None) -> None:
        if backend is None:
            from ._backends import create_backend

            backend = create_backend()
        self._backend = backend
        self._lock = threading.RLock()
        self._subscriptions: dict[int, Callback] = {}
        self._next_subscription_id = 1
        self._stop = threading.Event()
        self._events: queue.Queue[object] | None = None
        self._monitor_thread: threading.Thread | None = None
        self._dispatcher_thread: threading.Thread | None = None
        self._monitor_running = False

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    def read(self) -> str | None:
        return self._backend.read()

    def write(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("clipboard value must be str")
        self._backend.write(value)

    def on_change(
        self, callback: Callback, emit_initial: bool = False
    ) -> Subscription:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            self._ensure_monitor_locked()
            subscription_id = self._next_subscription_id
            self._next_subscription_id += 1
            self._subscriptions[subscription_id] = callback
            subscription = Subscription(self, subscription_id)
            if emit_initial:
                self._enqueue((subscription_id, self.read()))
            return subscription

    def close(self) -> None:
        """Stop monitoring and cancel subscriptions; read/write remain usable."""
        with self._lock:
            if not self._monitor_running:
                self._subscriptions.clear()
                return
            self._subscriptions.clear()
            self._monitor_running = False
            stop = self._stop
            events = self._events
            monitor = self._monitor_thread
            dispatcher = self._dispatcher_thread
            stop.set()
        self._backend.close_monitor()
        if events is not None:
            self._put_latest(events, _STOP)
        current = threading.current_thread()
        if monitor is not None and monitor is not current:
            monitor.join(timeout=self._WATCHDOG_INTERVAL + 0.5)
        if dispatcher is not None and dispatcher is not current:
            dispatcher.join(timeout=self._WATCHDOG_INTERVAL + 0.5)
        with self._lock:
            if self._events is events:
                self._monitor_thread = None
                self._dispatcher_thread = None
                self._events = None

    def _cancel(self, subscription_id: int) -> None:
        with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def _ensure_monitor_locked(self) -> None:
        if self._monitor_running:
            return
        start_monitor = getattr(self._backend, "start_monitor", None)
        if start_monitor is not None:
            start_monitor()
        stop = threading.Event()
        events: queue.Queue[object] = queue.Queue(maxsize=1)
        initial_revision = self._backend.revision
        initial_value = self._backend.read()
        self._stop = stop
        self._events = events
        self._monitor_running = True
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            args=(stop, events),
            name="clipboard-event-dispatcher",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(stop, events, initial_revision, initial_value),
            name="clipboard-event-monitor",
            daemon=True,
        )
        self._dispatcher_thread.start()
        self._monitor_thread.start()

    def _monitor_loop(
        self,
        stop: threading.Event,
        events: queue.Queue[object],
        revision: int,
        last_value: str | None,
    ) -> None:
        while not stop.is_set():
            new_revision = self._backend.wait_for_revision(
                revision, self._WATCHDOG_INTERVAL
            )
            if stop.is_set():
                break
            value = self._backend.read()
            if stop.is_set():
                break
            if new_revision > revision or value != last_value:
                self._enqueue_generation(stop, events, (None, value))
            revision = max(revision, new_revision)
            last_value = value

    def _dispatch_loop(
        self, stop: threading.Event, events: queue.Queue[object]
    ) -> None:
        while True:
            event = events.get()
            if event is _STOP or stop.is_set():
                return
            target, value = event
            with self._lock:
                if target is None:
                    callbacks = list(self._subscriptions.values())
                else:
                    callback = self._subscriptions.get(target)
                    callbacks = [] if callback is None else [callback]
            for callback in callbacks:
                try:
                    callback(value)
                except Exception:
                    pass
                if stop.is_set():
                    break

    def _enqueue(self, event: object) -> None:
        with self._lock:
            stop = self._stop
            events = self._events
        if events is not None:
            self._enqueue_generation(stop, events, event)

    def _enqueue_generation(
        self,
        stop: threading.Event,
        events: queue.Queue[object],
        event: object,
    ) -> None:
        if not stop.is_set():
            self._put_latest(events, event)

    @staticmethod
    def _put_latest(events: queue.Queue[object], event: object) -> None:
        try:
            events.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            events.get_nowait()
        except queue.Empty:
            pass
        try:
            events.put_nowait(event)
        except queue.Full:
            pass
