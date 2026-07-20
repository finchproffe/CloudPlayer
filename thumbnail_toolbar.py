from __future__ import annotations

import ctypes
import os
import struct
import uuid
from ctypes import wintypes

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QBuffer,
    QIODevice,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from config import TEMP_PATH
from utils import colored_icon


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


class THUMBBUTTON(ctypes.Structure):
    _fields_ = (
        ("dwMask", wintypes.DWORD),
        ("iId", wintypes.UINT),
        ("iBitmap", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 260),
        ("dwFlags", wintypes.DWORD),
    )


def _guid(value):
    parsed = uuid.UUID(value)
    node = parsed.node.to_bytes(6, "big")
    data4 = (ctypes.c_ubyte * 8)(
        parsed.clock_seq_hi_variant,
        parsed.clock_seq_low,
        *node,
    )
    return GUID(parsed.time_low, parsed.time_mid, parsed.time_hi_version, data4)


class ThumbnailToolbar(QAbstractNativeEventFilter):
    BUTTON_PREVIOUS = 4101
    BUTTON_PLAY_PAUSE = 4102
    BUTTON_NEXT = 4103
    WM_COMMAND = 0x0111
    THBN_CLICKED = 0x1800
    THB_ICON = 0x00000002
    THB_TOOLTIP = 0x00000004
    THB_FLAGS = 0x00000008

    def __init__(self, window, playlist_view):
        super().__init__()
        self.window = window
        self.playlist_view = playlist_view
        self.enabled = os.name == "nt"
        self._taskbar = None
        self._added = False
        self._com_initialized = False
        self._taskbar_message = 0
        self._hwnd = 0
        if not self.enabled:
            return
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        self.user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        self.user32.RegisterWindowMessageW.restype = wintypes.UINT
        self.user32.LoadImageW.argtypes = (
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        self.user32.LoadImageW.restype = wintypes.HANDLE
        self.user32.DestroyIcon.argtypes = (wintypes.HICON,)
        self.user32.DestroyIcon.restype = wintypes.BOOL
        self._taskbar_message = int(
            self.user32.RegisterWindowMessageW("TaskbarButtonCreated")
        )
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self)
        playlist_view.player.playbackStateChanged.connect(
            self.update_playback_button
        )

    def nativeEventFilter(self, event_type, message):
        if not self.enabled:
            return False
        try:
            native = ctypes.cast(
                int(message),
                ctypes.POINTER(wintypes.MSG),
            ).contents
        except (TypeError, ValueError):
            return False
        hwnd = int(native.hWnd or 0)
        if native.message == self._taskbar_message:
            if self._hwnd and hwnd != self._hwnd:
                return False
            self._hwnd = hwnd
            self._added = False
            self._add_buttons()
            return False
        if not self._hwnd or hwnd != self._hwnd:
            return False
        if native.message != self.WM_COMMAND:
            return False
        command = int(native.wParam)
        if ((command >> 16) & 0xFFFF) != self.THBN_CLICKED:
            return False
        button_id = command & 0xFFFF
        if button_id == self.BUTTON_PREVIOUS:
            self.playlist_view.play_prev_track()
        elif button_id == self.BUTTON_PLAY_PAUSE:
            self.playlist_view.toggle_playback()
        elif button_id == self.BUTTON_NEXT:
            self.playlist_view.play_next_track()
        return False

    def _com_method(self, index, restype, *argtypes):
        vtable = ctypes.cast(
            self._taskbar,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        prototype = ctypes.WINFUNCTYPE(
            restype,
            ctypes.c_void_p,
            *argtypes,
        )
        return prototype(vtable[index])

    def _ensure_taskbar(self):
        if self._taskbar is not None:
            return True
        self.ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        self.ole32.CoInitializeEx.restype = ctypes.c_long
        result = int(self.ole32.CoInitializeEx(None, 2))
        self._com_initialized = result in (0, 1)
        self.ole32.CoCreateInstance.argtypes = (
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        self.ole32.CoCreateInstance.restype = ctypes.c_long
        clsid = _guid("56FDF344-FD6D-11D0-958A-006097C9A090")
        interface_id = _guid("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")
        pointer = ctypes.c_void_p()
        result = int(
            self.ole32.CoCreateInstance(
                ctypes.byref(clsid),
                None,
                1,
                ctypes.byref(interface_id),
                ctypes.byref(pointer),
            )
        )
        if result < 0 or not pointer.value:
            return False
        self._taskbar = pointer
        hr_init = self._com_method(3, ctypes.c_long)
        if int(hr_init(self._taskbar)) < 0:
            self._release_taskbar()
            return False
        return True

    def _icon_path(self, source):
        TEMP_PATH.mkdir(parents=True, exist_ok=True)
        target = TEMP_PATH / f"thumbnail-{source}.ico"
        icon = colored_icon(source, "#FFFFFF", 32)
        pixmap = icon.pixmap(32, 32)
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.WriteOnly)
        saved = pixmap.save(buffer, "PNG")
        buffer.close()
        if not saved:
            return None
        png = bytes(payload)
        header = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack(
            "<BBBBHHII",
            32,
            32,
            0,
            0,
            1,
            32,
            len(png),
            22,
        )
        target.write_bytes(header + entry + png)
        return target

    def _load_icon(self, source):
        path = self._icon_path(source)
        if path is None:
            return None
        return self.user32.LoadImageW(
            None,
            str(path),
            1,
            32,
            32,
            0x00000010,
        )

    def _button(self, button_id, source, tooltip):
        button = THUMBBUTTON()
        button.dwMask = self.THB_ICON | self.THB_TOOLTIP | self.THB_FLAGS
        button.iId = button_id
        button.hIcon = self._load_icon(source)
        button.szTip = tooltip[:259]
        button.dwFlags = 0
        return button

    def _is_playing(self):
        return (
            self.playlist_view.player.playbackState()
            == QMediaPlayer.PlayingState
        )

    def _add_buttons(self):
        if not self._hwnd or self._added or not self._ensure_taskbar():
            return
        middle_icon = "pause.svg" if self._is_playing() else "play.svg"
        middle_tip = "Pause" if self._is_playing() else "Play"
        buttons = (THUMBBUTTON * 3)(
            self._button(self.BUTTON_PREVIOUS, "prev.svg", "Previous"),
            self._button(self.BUTTON_PLAY_PAUSE, middle_icon, middle_tip),
            self._button(self.BUTTON_NEXT, "next.svg", "Next"),
        )
        add_buttons = self._com_method(
            15,
            ctypes.c_long,
            wintypes.HWND,
            wintypes.UINT,
            ctypes.POINTER(THUMBBUTTON),
        )
        result = int(
            add_buttons(
                self._taskbar,
                self._hwnd,
                3,
                buttons,
            )
        )
        for button in buttons:
            if button.hIcon:
                self.user32.DestroyIcon(button.hIcon)
        self._added = result >= 0

    def update_playback_button(self, _state=None):
        if not self._added or self._taskbar is None:
            return
        source = "pause.svg" if self._is_playing() else "play.svg"
        tooltip = "Pause" if self._is_playing() else "Play"
        button = self._button(self.BUTTON_PLAY_PAUSE, source, tooltip)
        update_buttons = self._com_method(
            16,
            ctypes.c_long,
            wintypes.HWND,
            wintypes.UINT,
            ctypes.POINTER(THUMBBUTTON),
        )
        update_buttons(
            self._taskbar,
            self._hwnd,
            1,
            ctypes.byref(button),
        )
        if button.hIcon:
            self.user32.DestroyIcon(button.hIcon)

    def _release_taskbar(self):
        if self._taskbar is None:
            return
        release = self._com_method(2, wintypes.ULONG)
        release(self._taskbar)
        self._taskbar = None

    def close(self):
        if not self.enabled:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self)
        self._release_taskbar()
        if self._com_initialized:
            self.ole32.CoUninitialize()
            self._com_initialized = False
