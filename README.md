# clipboard-event

[![CI](https://github.com/jordi-murgo/clipboard-event/actions/workflows/ci.yml/badge.svg)](https://github.com/jordi-murgo/clipboard-event/actions/workflows/ci.yml)

`clipboard-event` provides clipboard text access and change callbacks for Python on Windows and macOS. It uses native change notifications where available and has no runtime dependencies.

- **Windows:** reads and writes Unicode text through Win32 and listens for `WM_CLIPBOARDUPDATE` on a message-only window.
- **macOS:** reads and writes through `pbpaste` and `pbcopy`, then observes `NSPasteboard.changeCount` without polling clipboard content continuously.
- If native monitoring cannot start on a supported platform, the library falls back to polling. Other platforms fail explicitly.

## Installation

**Once the package is published on PyPI:**

```console
pip install clipboard-event
```

The project requires Python 3.10 or newer.

## Quick start

The module facade creates its default clipboard instance only when first used:

```python
import clipboard_event

print(clipboard_event.read())
clipboard_event.write("Hello from Python")

subscription = clipboard_event.on_change(
    lambda value: print(f"Clipboard changed: {value!r}"),
    emit_initial=True,
)

# Later:
subscription.cancel()
clipboard_event.close()
```

## Explicit instances

Use `Clipboard` when you need an independent lifecycle:

```python
from clipboard_event import Clipboard, Subscription

clipboard = Clipboard()

def changed(value: str | None) -> None:
    print(value)

subscription: Subscription = clipboard.on_change(changed)
clipboard.write("This write also triggers the callback")

subscription.cancel()
clipboard.close()
```

`Subscription.cancel()` and `Clipboard.close()` are idempotent. Closing a `Clipboard` cancels its subscriptions and releases monitor threads and native watcher resources. `read()` and `write()` remain usable after close, and a later `on_change()` call starts monitoring again.

Callbacks run sequentially on one Python dispatcher thread, never on a native watcher thread. One callback's exception does not stop other subscriptions. Rapid changes may coalesce, so a callback can receive only the latest value. A callback receives `str` for text or `None` when no text is available. Writes made through the library are observed like external changes.

For diagnostics, inspect the selected backend:

```python
print(clipboard.backend_name)  # "win32", "macos", or "polling"
```

## Support

| Platform | Python | Clipboard I/O | Change monitoring |
| --- | --- | --- | --- |
| Windows | 3.10–3.14 | Win32 Unicode text | `WM_CLIPBOARDUPDATE`, with polling fallback |
| macOS | 3.10–3.14 | `pbcopy` / `pbpaste` | `NSPasteboard.changeCount`, with polling fallback |
| Other platforms | — | Unsupported | Unsupported |

The package is pure Python. Its wheel is universal because platform integration happens at runtime through the standard library and system tools; no compiled extension is included.

## Development

Create and activate a virtual environment, then install the project in editable mode:

```console
python -m pip install -e .
python -m unittest discover -s tests -t .
python tests/native_smoke.py
```

Build and inspect distribution artifacts with:

```console
python -m pip install build twine
python -m build
python -m twine check dist/*
```

## License

[MIT](LICENSE) © 2026 Jordi Murgó.
