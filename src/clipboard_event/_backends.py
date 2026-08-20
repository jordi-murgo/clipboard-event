from __future__ import annotations

import platform
import subprocess
import threading
import time


class _PollingRevision:
    backend_name = "polling"

    def __init__(self, reader, interval: float = 0.1) -> None:
        self._reader = reader
        self._interval = interval
        self._value = reader()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def wait_for_revision(self, after: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while self._revision <= after:
            value = self._reader()
            if value != self._value:
                self._value = value
                self._revision += 1
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._interval, remaining))
        return self._revision

    def close(self) -> None:
        pass


class _MacChangeCount:
    backend_name = "macos"

    def __init__(self) -> None:
        import ctypes
        import ctypes.util

        objc_path = ctypes.util.find_library("objc")
        appkit_path = ctypes.util.find_library("AppKit")
        if not objc_path or not appkit_path:
            raise RuntimeError("Objective-C runtime unavailable")
        self._appkit = ctypes.CDLL(appkit_path)
        self._objc = ctypes.CDLL(objc_path)
        self._objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self._objc.objc_getClass.restype = ctypes.c_void_p
        self._objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self._objc.sel_registerName.restype = ctypes.c_void_p
        send_address = ctypes.cast(
            self._objc.objc_msgSend, ctypes.c_void_p
        ).value
        if send_address is None:
            raise RuntimeError("objc_msgSend unavailable")
        send_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(send_address)
        self._send_integer = ctypes.CFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p
        )(send_address)
        pasteboard_class = self._objc.objc_getClass(b"NSPasteboard")
        general_pasteboard = self._objc.sel_registerName(b"generalPasteboard")
        self._pasteboard = send_id(pasteboard_class, general_pasteboard)
        if not self._pasteboard:
            raise RuntimeError("NSPasteboard unavailable")
        self._change_count = self._objc.sel_registerName(b"changeCount")
        self._interval = 0.05

    @property
    def revision(self) -> int:
        return int(self._send_integer(self._pasteboard, self._change_count))

    def wait_for_revision(self, after: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        revision = self.revision
        while revision <= after:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._interval, remaining))
            revision = self.revision
        return revision

    def close(self) -> None:
        pass


class _MacBackend:
    def __init__(self) -> None:
        self._monitor = None

    @property
    def backend_name(self) -> str:
        return "polling" if isinstance(self._monitor, _PollingRevision) else "macos"

    def read(self) -> str | None:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return result.stdout if result.returncode == 0 else None

    def write(self, value: str) -> None:
        subprocess.run(["pbcopy"], input=value, text=True, check=True)

    def start_monitor(self) -> None:
        if self._monitor is not None:
            return
        try:
            self._monitor = _MacChangeCount()
        except Exception:
            self._monitor = _PollingRevision(self.read)

    @property
    def revision(self) -> int:
        self.start_monitor()
        return self._monitor.revision

    def wait_for_revision(self, after: int, timeout: float) -> int:
        self.start_monitor()
        return self._monitor.wait_for_revision(after, timeout)

    def close_monitor(self) -> None:
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.close()


class _WindowsListener:
    backend_name = "win32"

    def __init__(self) -> None:
        import ctypes

        self._condition = threading.Condition()
        self._close_lock = threading.Lock()
        self._revision = 0
        self._ready = threading.Event()
        self._failed = None
        self._hwnd = None
        self._class_name = None
        self._class_registered = False
        self._listener_added = False
        self._closed = False
        self._user32 = ctypes.windll.user32
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.close()
            raise RuntimeError("clipboard listener startup timed out")
        if self._failed is not None:
            raise RuntimeError("clipboard listener unavailable") from self._failed

    def _message_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        wm_destroy = 0x0002
        wm_close = 0x0010
        wm_clipboard_update = 0x031D
        hwnd_message = -3
        wndproc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WndClass(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32 = self._user32
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WndClass)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.AddClipboardFormatListener.restype = wintypes.BOOL
        user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t

        def window_proc(hwnd, message, wparam, lparam):
            if message == wm_clipboard_update:
                with self._condition:
                    self._revision += 1
                    self._condition.notify_all()
                return 0
            if message == wm_close:
                if self._listener_added:
                    user32.RemoveClipboardFormatListener(hwnd)
                    self._listener_added = False
                user32.DestroyWindow(hwnd)
                return 0
            if message == wm_destroy:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        try:
            self._window_proc = wndproc(window_proc)
            self._class_name = f"ClipboardEventMonitor{id(self)}"
            window_class = WndClass(
                lpfnWndProc=self._window_proc,
                lpszClassName=self._class_name,
            )
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("RegisterClassW failed")
            self._class_registered = True
            self._hwnd = user32.CreateWindowExW(
                0,
                self._class_name,
                self._class_name,
                0,
                0,
                0,
                0,
                0,
                hwnd_message,
                0,
                0,
                None,
            )
            if not self._hwnd:
                raise OSError("CreateWindowExW failed")
            if not user32.AddClipboardFormatListener(self._hwnd):
                raise OSError("AddClipboardFormatListener failed")
            self._listener_added = True
            self._ready.set()
            with self._close_lock:
                close_requested = self._closed
            if close_requested:
                user32.PostMessageW(self._hwnd, wm_close, 0, 0)
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), 0, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._failed = exc
            self._ready.set()
        finally:
            if self._listener_added and self._hwnd:
                user32.RemoveClipboardFormatListener(self._hwnd)
                self._listener_added = False
            if self._hwnd and user32.IsWindow(self._hwnd):
                user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            if self._class_registered and self._class_name:
                user32.UnregisterClassW(self._class_name, 0)
                self._class_registered = False
            self._class_name = None
            self._ready.set()

    @property
    def revision(self) -> int:
        with self._condition:
            return self._revision

    def wait_for_revision(self, after: int, timeout: float) -> int:
        with self._condition:
            if self._revision <= after:
                self._condition.wait_for(lambda: self._revision > after, timeout)
            return self._revision

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            hwnd = self._hwnd
        if hwnd:
            self._user32.PostMessageW(hwnd, 0x0010, 0, 0)
        self._thread.join(timeout=1.0)


class _WindowsBackend:
    def __init__(self) -> None:
        self._monitor = None

    @property
    def backend_name(self) -> str:
        return "polling" if isinstance(self._monitor, _PollingRevision) else "win32"

    def read(self) -> str | None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        for _ in range(5):
            if user32.OpenClipboard(0):
                try:
                    handle = user32.GetClipboardData(13)
                    if not handle:
                        return None
                    pointer = kernel32.GlobalLock(handle)
                    if not pointer:
                        return None
                    try:
                        return ctypes.wstring_at(pointer)
                    finally:
                        kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
            time.sleep(0.05)
        return None

    def write(self, value: str) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
        kernel32.GlobalFree.restype = wintypes.HANDLE
        buffer = (value + "\0").encode("utf-16-le")
        for _ in range(5):
            if user32.OpenClipboard(0):
                try:
                    user32.EmptyClipboard()
                    handle = kernel32.GlobalAlloc(0x0002, len(buffer))
                    if not handle:
                        raise OSError("GlobalAlloc failed")
                    transferred = False
                    try:
                        pointer = kernel32.GlobalLock(handle)
                        if not pointer:
                            raise OSError("GlobalLock failed")
                        try:
                            ctypes.memmove(pointer, buffer, len(buffer))
                        finally:
                            kernel32.GlobalUnlock(handle)
                        if not user32.SetClipboardData(13, handle):
                            raise OSError("SetClipboardData failed")
                        transferred = True
                        return
                    finally:
                        if not transferred:
                            kernel32.GlobalFree(handle)
                finally:
                    user32.CloseClipboard()
            time.sleep(0.05)
        raise OSError("clipboard is busy")

    def start_monitor(self) -> None:
        if self._monitor is not None:
            return
        try:
            self._monitor = _WindowsListener()
        except Exception:
            self._monitor = _PollingRevision(self.read)

    @property
    def revision(self) -> int:
        self.start_monitor()
        return self._monitor.revision

    def wait_for_revision(self, after: int, timeout: float) -> int:
        self.start_monitor()
        return self._monitor.wait_for_revision(after, timeout)

    def close_monitor(self) -> None:
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.close()


class _UnsupportedBackend:
    backend_name = "unsupported"

    @property
    def revision(self) -> int:
        return 0

    def read(self) -> str | None:
        raise RuntimeError(f"clipboard-event does not support {platform.system()}")

    def write(self, value: str) -> None:
        raise RuntimeError(f"clipboard-event does not support {platform.system()}")

    def wait_for_revision(self, after: int, timeout: float) -> int:
        raise RuntimeError(f"clipboard-event does not support {platform.system()}")

    def close_monitor(self) -> None:
        pass


def create_backend():
    system = platform.system()
    if system == "Windows":
        return _WindowsBackend()
    if system == "Darwin":
        return _MacBackend()
    return _UnsupportedBackend()
