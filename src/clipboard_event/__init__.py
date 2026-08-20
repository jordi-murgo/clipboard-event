from __future__ import annotations

import threading
from collections.abc import Callable

from ._core import Backend, Callback, Clipboard, Subscription

__all__ = [
    "Clipboard",
    "Subscription",
    "read",
    "write",
    "Backend",
    "Callback",
    "on_change",
    "close",
]

_default_lock = threading.Lock()
_default_instance: Clipboard | None = None


def _default() -> Clipboard:
    global _default_instance
    with _default_lock:
        if _default_instance is None:
            _default_instance = Clipboard()
        return _default_instance


def read() -> str | None:
    return _default().read()


def write(value: str) -> None:
    _default().write(value)


def on_change(
    callback: Callable[[str | None], None], emit_initial: bool = False
) -> Subscription:
    return _default().on_change(callback, emit_initial=emit_initial)


def close() -> None:
    with _default_lock:
        instance = _default_instance
    if instance is not None:
        instance.close()
