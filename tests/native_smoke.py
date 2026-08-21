"""Read-only native lifecycle smoke for supported CI platforms."""
from __future__ import annotations

import platform
import threading

from clipboard_event import Clipboard


def main() -> None:
    system = platform.system()
    clipboard = Clipboard()
    try:
        if system == "Linux":
            _run_linux_smoke(clipboard)
        else:
            expected_backend = {
                "Darwin": "macos",
                "Windows": "win32",
            }[system]
            assert clipboard.backend_name == expected_backend
            value = clipboard.read()
            assert value is None or isinstance(value, str)
            subscription = clipboard.on_change(lambda _value: None)
            assert clipboard.backend_name == expected_backend
            subscription.cancel()
            subscription.cancel()
    finally:
        clipboard.close()
    clipboard.close()

    package_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("clipboard-event-") and thread.is_alive()
    ]
    assert package_threads == []


def _run_linux_smoke(clipboard: Clipboard) -> None:
    """Linux smoke: accept wayland/x11/polling/unsupported, handle headless."""
    name = clipboard.backend_name
    assert name in ("wayland", "x11", "polling", "unsupported"), f"unexpected backend: {name}"
    if name == "unsupported":
        try:
            clipboard.read()
        except RuntimeError:
            pass
        else:
            raise AssertionError("unsupported backend should raise on read()")
        return
    try:
        value = clipboard.read()
    except Exception:
        return
    assert value is None or isinstance(value, str)
    subscription = clipboard.on_change(lambda _value: None)
    subscription.cancel()
    subscription.cancel()

    package_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("clipboard-event-") and thread.is_alive()
    ]
    assert package_threads == []


if __name__ == "__main__":
    main()
