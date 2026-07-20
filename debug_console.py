from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import traceback
from typing import TextIO


_CONSOLE_TITLE = "CloudPlayer Debug Console"
_DEBUG_HANDLER_MARKER = "_cloudplayer_debug_console_handler"
_lock = threading.RLock()
_enabled = False
_console_created = False
_stdout_stream: TextIO | None = None
_stderr_stream: TextIO | None = None
_stdin_stream: TextIO | None = None
_null_stream: TextIO | None = None
_logging_handler: logging.Handler | None = None
_previous_logging_level: int | None = None
_original_stdout = sys.stdout
_original_stderr = sys.stderr
_original_stdin = sys.stdin
_previous_excepthook = sys.excepthook
_previous_threading_excepthook = getattr(threading, "excepthook", None)
_qt_capture_available = False
_qt_handler_installed = False
_previous_qt_handler = None
_qt_install_function = None


def _safe_fallback_stream() -> TextIO:
    global _null_stream
    if _null_stream is None or _null_stream.closed:
        _null_stream = open(
            os.devnull,
            "w",
            encoding="utf-8",
            errors="replace",
        )
    return _null_stream


def _allocate_windows_console() -> bool:
    global _console_created, _stdout_stream, _stderr_stream, _stdin_stream
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    kernel32.AllocConsole.restype = ctypes.c_int
    kernel32.SetConsoleTitleW.argtypes = [ctypes.c_wchar_p]
    kernel32.SetConsoleTitleW.restype = ctypes.c_int
    user32.GetSystemMenu.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetSystemMenu.restype = ctypes.c_void_p
    user32.DeleteMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    user32.DeleteMenu.restype = ctypes.c_int
    user32.DrawMenuBar.argtypes = [ctypes.c_void_p]
    user32.DrawMenuBar.restype = ctypes.c_int
    console_exists = bool(kernel32.GetConsoleWindow())
    if not console_exists:
        if not kernel32.AllocConsole():
            return False
        _console_created = True
    kernel32.SetConsoleTitleW(_CONSOLE_TITLE)
    kernel32.SetConsoleOutputCP(65001)
    kernel32.SetConsoleCP(65001)
    try:
        _stdout_stream = open(
            "CONOUT$",
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        _stderr_stream = open(
            "CONOUT$",
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        _stdin_stream = open(
            "CONIN$",
            "r",
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        if _console_created:
            kernel32.FreeConsole()
            _console_created = False
        return False
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        system_menu = user32.GetSystemMenu(hwnd, False)
        if system_menu:
            user32.DeleteMenu(system_menu, 0xF060, 0)
            user32.DrawMenuBar(hwnd)
    return True


def _close_owned_windows_console() -> None:
    global _console_created
    if os.name != "nt" or not _console_created:
        return
    import ctypes

    ctypes.windll.kernel32.FreeConsole()
    _console_created = False


def _uncaught_exception_hook(exc_type, exc_value, exc_traceback) -> None:
    stream = sys.stderr or _safe_fallback_stream()
    print("\n[Uncaught exception]", file=stream, flush=True)
    traceback.print_exception(
        exc_type,
        exc_value,
        exc_traceback,
        file=stream,
    )


def _thread_exception_hook(args) -> None:
    stream = sys.stderr or _safe_fallback_stream()
    thread_name = getattr(getattr(args, "thread", None), "name", "unknown")
    print(
        f"\n[Uncaught thread exception: {thread_name}]",
        file=stream,
        flush=True,
    )
    traceback.print_exception(
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
        file=stream,
    )


def _install_logging() -> None:
    global _logging_handler, _previous_logging_level
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if getattr(handler, _DEBUG_HANDLER_MARKER, False):
            _logging_handler = handler
            return
    _previous_logging_level = root_logger.level
    handler = logging.StreamHandler(sys.stderr or _safe_fallback_stream())
    setattr(handler, _DEBUG_HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    logging.captureWarnings(True)
    _logging_handler = handler


def _remove_logging() -> None:
    global _logging_handler, _previous_logging_level
    root_logger = logging.getLogger()
    if _logging_handler is not None:
        root_logger.removeHandler(_logging_handler)
        try:
            _logging_handler.close()
        except Exception:
            pass
    if _previous_logging_level is not None:
        root_logger.setLevel(_previous_logging_level)
    logging.captureWarnings(False)
    _logging_handler = None
    _previous_logging_level = None


def enable_debug_console() -> bool:
    global _enabled
    with _lock:
        if _enabled:
            return True
        if not _allocate_windows_console():
            return False
        if os.name == "nt":
            sys.stdout = _stdout_stream or _safe_fallback_stream()
            sys.stderr = _stderr_stream or _safe_fallback_stream()
            if _stdin_stream is not None:
                sys.stdin = _stdin_stream
        else:
            sys.stdout = sys.stdout or _safe_fallback_stream()
            sys.stderr = sys.stderr or _safe_fallback_stream()
        _install_logging()
        sys.excepthook = _uncaught_exception_hook
        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_exception_hook
        try:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        except (OSError, RuntimeError):
            pass
        _enabled = True
        _enable_qt_message_capture()
        print("=" * 68, flush=True)
        print("CloudPlayer debug console enabled", flush=True)
        print(
            "stdout, stderr, Python logging, warnings and exceptions are shown.",
            flush=True,
        )
        print("=" * 68, flush=True)
        return True


def disable_debug_console() -> bool:
    global _enabled, _stdout_stream, _stderr_stream, _stdin_stream
    with _lock:
        if not _enabled:
            return True
        try:
            print("[Debug] Debug console disabled.", flush=True)
        except Exception:
            pass
        _disable_qt_message_capture()
        try:
            faulthandler.disable()
        except RuntimeError:
            pass
        _remove_logging()
        sys.excepthook = _previous_excepthook
        if _previous_threading_excepthook is not None:
            threading.excepthook = _previous_threading_excepthook
        fallback = _safe_fallback_stream()
        sys.stdout = _original_stdout or fallback
        sys.stderr = _original_stderr or fallback
        sys.stdin = _original_stdin
        for stream in (_stdout_stream, _stderr_stream, _stdin_stream):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        _stdout_stream = None
        _stderr_stream = None
        _stdin_stream = None
        _close_owned_windows_console()
        _enabled = False
        return True


def set_debug_console(enabled: bool) -> bool:
    return enable_debug_console() if bool(enabled) else disable_debug_console()


def is_debug_console_enabled() -> bool:
    return _enabled


def _qt_message_handler(message_type, context, message) -> None:
    location = ""
    source_file = getattr(context, "file", None)
    line = getattr(context, "line", 0)
    if source_file:
        location = f" ({source_file}:{line})"
    stream = sys.stderr or _safe_fallback_stream()
    print(
        f"[Qt {message_type!s}] {message}{location}",
        file=stream,
        flush=True,
    )


def _enable_qt_message_capture() -> None:
    global _qt_handler_installed, _previous_qt_handler
    if (
        not _qt_capture_available
        or _qt_handler_installed
        or _qt_install_function is None
    ):
        return
    _previous_qt_handler = _qt_install_function(_qt_message_handler)
    _qt_handler_installed = True


def _disable_qt_message_capture() -> None:
    global _qt_handler_installed, _previous_qt_handler
    if not _qt_handler_installed or _qt_install_function is None:
        return
    _qt_install_function(_previous_qt_handler)
    _previous_qt_handler = None
    _qt_handler_installed = False


def install_qt_message_capture() -> None:
    global _qt_capture_available, _qt_install_function
    if not _qt_capture_available:
        try:
            from PySide6.QtCore import qInstallMessageHandler
        except ImportError:
            return
        _qt_install_function = qInstallMessageHandler
        _qt_capture_available = True
    if _enabled:
        _enable_qt_message_capture()
