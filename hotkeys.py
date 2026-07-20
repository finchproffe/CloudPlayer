from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal


class GlobalHotkeyThread(QThread):
    play_pause = Signal()
    previous = Signal()
    next = Signal()
    failed = Signal(str)

    DEBOUNCE_SECONDS = 0.20

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_NOREPEAT = 0x4000

    VK_F7 = 0x76
    VK_F8 = 0x77
    VK_F9 = 0x78
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_emit: dict[str, float] = {}
        self._keyboard = None
        self._keyboard_handles: list[object] = []
        self._win_thread_id = 0
        self._backend = ""

    def _emit_once(self, name: str, signal: Signal) -> None:
        now = time.monotonic()
        if now - self._last_emit.get(name, 0.0) < self.DEBOUNCE_SECONDS:
            return
        self._last_emit[name] = now
        signal.emit()

    def run(self) -> None:
        native_error = ""

        if os.name == "nt":
            try:
                self._run_windows_backend()
                return
            except Exception as exc:
                native_error = str(exc)

        try:
            self._run_keyboard_backend()
        except Exception as exc:
            parts = []
            if native_error:
                parts.append(f"Windows backend: {native_error}")
            parts.append(f"keyboard backend: {exc}")
            self.failed.emit("; ".join(parts))

    def _run_windows_backend(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.RegisterHotKey.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = (
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.GetMessageW.restype = wintypes.BOOL

        self._win_thread_id = int(kernel32.GetCurrentThreadId())
        self._backend = "win32"

        bindings = {
            1: (self.VK_F7, "previous", self.previous),
            2: (self.VK_F8, "play_pause", self.play_pause),
            3: (self.VK_F9, "next", self.next),
            4: (self.VK_MEDIA_PREV_TRACK, "previous", self.previous),
            5: (self.VK_MEDIA_PLAY_PAUSE, "play_pause", self.play_pause),
            6: (self.VK_MEDIA_NEXT_TRACK, "next", self.next),
        }

        registered: list[int] = []
        try:
            for hotkey_id, (virtual_key, _name, _signal) in bindings.items():
                if user32.RegisterHotKey(
                    None,
                    hotkey_id,
                    self.MOD_NOREPEAT,
                    virtual_key,
                ):
                    registered.append(hotkey_id)

            if not registered:
                error_code = ctypes.get_last_error()
                raise OSError(
                    error_code,
                    "Windows did not register F7/F8/F9 or the media keys",
                )

            message = wintypes.MSG()
            while not self.isInterruptionRequested():
                result = user32.GetMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                )
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                if message.message != self.WM_HOTKEY:
                    continue

                hotkey_id = int(message.wParam)
                binding = bindings.get(hotkey_id)
                if binding is None:
                    continue
                _virtual_key, name, signal = binding
                self._emit_once(name, signal)
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self._win_thread_id = 0
            self._backend = ""

    def _run_keyboard_backend(self) -> None:
        import keyboard

        self._keyboard = keyboard
        self._backend = "keyboard"

        bindings = (
            ("f7", "previous", self.previous),
            ("f8", "play_pause", self.play_pause),
            ("f9", "next", self.next),
            ("previous track", "previous", self.previous),
            ("play/pause media", "play_pause", self.play_pause),
            ("next track", "next", self.next),
            ("media previous", "previous", self.previous),
            ("media play pause", "play_pause", self.play_pause),
            ("media next", "next", self.next),
        )

        self._keyboard_handles.clear()
        for key, name, signal in bindings:
            try:
                handle = keyboard.add_hotkey(
                    key,
                    lambda current=name, output=signal: self._emit_once(
                        current,
                        output,
                    ),
                    suppress=False,
                    trigger_on_release=False,
                )
                self._keyboard_handles.append(handle)
            except Exception:
                continue

        if not self._keyboard_handles:
            raise RuntimeError("No global media hotkeys could be registered")

        try:
            while not self.isInterruptionRequested():
                self.msleep(50)
        finally:
            for handle in self._keyboard_handles:
                try:
                    keyboard.remove_hotkey(handle)
                except Exception:
                    pass
            self._keyboard_handles.clear()
            self._keyboard = None
            self._backend = ""

    def stop(self) -> None:
        self.requestInterruption()

        if os.name == "nt" and self._win_thread_id:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW(
                    self._win_thread_id,
                    self.WM_QUIT,
                    0,
                    0,
                )
            except Exception:
                pass
