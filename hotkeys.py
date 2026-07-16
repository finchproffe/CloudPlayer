import time

from PySide6.QtCore import QThread, Signal


class GlobalHotkeyThread(QThread):
    play_pause = Signal()
    previous = Signal()
    next = Signal()
    failed = Signal(str)

    DEBOUNCE_SECONDS = 0.28

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_emit = {}
        self._keyboard = None

    def _emit_once(self, name, signal):
        now = time.monotonic()
        if now - self._last_emit.get(name, 0.0) < self.DEBOUNCE_SECONDS:
            return
        self._last_emit[name] = now
        signal.emit()

    def run(self):
        try:
            import keyboard

            self._keyboard = keyboard
            bindings = (
                ("f7", "previous", self.previous),
                ("f8", "play_pause", self.play_pause),
                ("f9", "next", self.next),
                ("previous track", "previous", self.previous),
                ("play/pause media", "play_pause", self.play_pause),
                ("next track", "next", self.next),
            )
            installed = 0
            for key, name, signal in bindings:
                try:
                    keyboard.add_hotkey(
                        key,
                        lambda current=name, output=signal: self._emit_once(
                            current, output
                        ),
                        suppress=False,
                        trigger_on_release=False,
                    )
                    installed += 1
                except Exception:
                    pass
            if not installed:
                raise RuntimeError("No global media hotkeys could be registered")
            while not self.isInterruptionRequested():
                self.msleep(50)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if self._keyboard is not None:
                try:
                    self._keyboard.unhook_all_hotkeys()
                except Exception:
                    pass
                self._keyboard = None

    def stop(self):
        self.requestInterruption()