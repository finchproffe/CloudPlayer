from PySide6.QtCore import QThread, Signal


class GlobalHotkeyThread(QThread):
    play_pause = Signal()
    previous = Signal()
    next = Signal()
    failed = Signal(str)

    def run(self):
        try:
            import keyboard
            bindings = {
                "f7": self.previous.emit,
                "f8": self.play_pause.emit,
                "f9": self.next.emit,
                "previous track": self.previous.emit,
                "play/pause media": self.play_pause.emit,
                "next track": self.next.emit,
            }
            for key, callback in bindings.items():
                try:
                    keyboard.add_hotkey(key, callback, suppress=False)
                except Exception:
                    pass
            while not self.isInterruptionRequested():
                self.msleep(100)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass

    def stop(self):
        self.requestInterruption()
