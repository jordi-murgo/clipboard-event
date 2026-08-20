"""Read-only native lifecycle smoke for supported CI platforms."""
from __future__ import annotations

import platform
import threading

from clipboard_event import Clipboard


def main() -> None:
    expected_backend = {
        "Darwin": "macos",
        "Windows": "win32",
    }[platform.system()]
    clipboard = Clipboard()
    try:
        assert clipboard.backend_name == expected_backend
        value = clipboard.read()
        assert value is None or isinstance(value, str)
        subscription = clipboard.on_change(lambda _value: None)
        assert clipboard.backend_name == expected_backend
        subscription.cancel()
        subscription.cancel()
        clipboard.close()
        clipboard.close()
    finally:
        clipboard.close()

    package_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("clipboard-event-") and thread.is_alive()
    ]
    assert package_threads == []


if __name__ == "__main__":
    main()
