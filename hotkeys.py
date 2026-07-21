from __future__ import annotations

import configparser
import ctypes
import os
import time
from collections import OrderedDict
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QItemSelectionModel, QObject, QPoint, QThread, QTimer, Qt, Signal,
)
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFocusFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTextEdit,
    QWidget,
)

from config import ACCENT_COLOR, DOCS_PATH
from main_common import make_menu


BINDS_PATH = Path(
    os.getenv(
        "CLOUDPLAYER_BINDS_FILE",
        str(DOCS_PATH / "binds.cfg"),
    )
).expanduser()

DEFAULT_BINDINGS = OrderedDict(
    (
        (
            "navigation",
            OrderedDict(
                (
                    ("focus_next", "Tab"),
                    ("focus_previous", "Shift+Tab"),
                    ("activate", "Enter"),
                    ("cancel", "Escape"),
                    ("back", "Backspace"),
                    ("context_menu", "RightCtrl"),
                    ("context_menu_fallback", "Shift+F10"),
                )
            ),
        ),
        (
            "playback",
            OrderedDict(
                (
                    ("repeat", "F5"),
                    ("shuffle", "F6"),
                    ("previous", "F7"),
                    ("play_pause", "F8"),
                    ("next", "F9"),
                    ("mute", "F10"),
                    ("volume_down", "F11"),
                    ("volume_up", "F12"),
                    ("play_pause_window", "Space"),
                )
            ),
        ),
        (
            "playlist",
            OrderedDict(
                (
                    ("add_song", "Alt"),
                    ("multi_select_up", "Ctrl+Up"),
                    ("multi_select_down", "Ctrl+Down"),
                    ("demo_selected", "Space"),
                    ("delete_selected", "Delete"),
                    ("play_selected", "Ctrl+Enter"),
                    ("new_playlist", "Ctrl+N"),
                    ("search", "Ctrl+F"),
                )
            ),
        ),
        (
            "sections",
            OrderedDict(
                (
                    ("home", "Ctrl+1"),
                    ("playlist", "Ctrl+2"),
                    ("search", "Ctrl+3"),
                    ("queue", "Ctrl+4"),
                    ("listen_together", "Ctrl+5"),
                    ("settings", "Ctrl+6"),
                    ("now_playing", "Ctrl+0"),
                )
            ),
        ),
    )
)

_KEY_ALIASES = {
    "ESC": "ESCAPE",
    "RETURN": "ENTER",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PGUP": "PAGEUP",
    "PGDOWN": "PAGEDOWN",
    "PAGE UP": "PAGEUP",
    "PAGE DOWN": "PAGEDOWN",
    "CONTROL": "CTRL",
    "RIGHTCONTROL": "RIGHTCTRL",
    "RIGHT CONTROL": "RIGHTCTRL",
    "RCTRL": "RIGHTCTRL",
    "LEFTCONTROL": "LEFTCTRL",
    "LEFT CONTROL": "LEFTCTRL",
    "LCTRL": "LEFTCTRL",
    "APPLICATION": "MENU",
    "APPS": "MENU",
    "CONTEXTMENU": "MENU",
    "CONTEXT MENU": "MENU",
    "MEDIA PREVIOUS": "MEDIAPREVIOUS",
    "PREVIOUS TRACK": "MEDIAPREVIOUS",
    "MEDIA PLAY PAUSE": "MEDIAPLAYPAUSE",
    "PLAY/PAUSE MEDIA": "MEDIAPLAYPAUSE",
    "MEDIA NEXT": "MEDIANEXT",
    "NEXT TRACK": "MEDIANEXT",
    "VOLUME MUTE": "VOLUMEMUTE",
    "VOLUME DOWN": "VOLUMEDOWN",
    "VOLUME UP": "VOLUMEUP",
}

_MODIFIER_ORDER = ("CTRL", "ALT", "SHIFT", "META")


def _canonical_token(value: str) -> str:
    token = " ".join(str(value or "").strip().upper().split())
    return _KEY_ALIASES.get(token, token.replace(" ", ""))


def canonical_sequence(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    pieces = [_canonical_token(piece) for piece in text.split("+")]
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return ""
    if len(pieces) == 1:
        return pieces[0]
    modifiers = []
    key = ""
    for piece in pieces:
        if piece in _MODIFIER_ORDER and piece not in modifiers:
            modifiers.append(piece)
        else:
            key = piece
    ordered = [modifier for modifier in _MODIFIER_ORDER if modifier in modifiers]
    if key:
        ordered.append(key)
    return "+".join(ordered)


def _default_config_text() -> str:
    lines = [
        "# CloudPlayer keyboard bindings",
        "# Edit values and restart CloudPlayer to apply changes.",
        "# RightCtrl means the physical right Ctrl key only.",
        "# Fn is handled by the keyboard itself and is not visible to Windows.",
        "",
    ]
    for section, values in DEFAULT_BINDINGS.items():
        lines.append(f"[{section}]")
        for name, value in values.items():
            lines.append(f"{name} = {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class HotkeyBindings:
    def __init__(self, path: Path | str = BINDS_PATH):
        self.path = Path(path)
        self._values: dict[tuple[str, str], str] = {}
        self._ensure_file()
        self.reload()

    def _ensure_file(self) -> None:
        if self.path.exists():
            self._append_missing_defaults()
            return
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(_default_config_text(), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _append_missing_defaults(self) -> None:
        """Add newly introduced bindings without replacing user choices."""
        try:
            original = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return

        lines = original.splitlines()
        sections = {}
        current = None
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if current is not None:
                    sections[current]["end"] = index
                current = stripped[1:-1].strip().casefold()
                sections[current] = {
                    "start": index,
                    "end": len(lines),
                    "options": set(),
                }
                continue
            if current is None or not stripped or stripped.startswith(("#", ";")):
                continue
            if "=" in stripped:
                name = stripped.split("=", 1)[0].strip().casefold()
                if name:
                    sections[current]["options"].add(name)
        if current is not None:
            sections[current]["end"] = len(lines)

        insertions = []
        missing_sections = []
        for section, defaults in DEFAULT_BINDINGS.items():
            record = sections.get(section.casefold())
            if record is None:
                missing_sections.append((section, defaults))
                continue
            missing = [
                (name, value)
                for name, value in defaults.items()
                if name.casefold() not in record["options"]
            ]
            if missing:
                insertion_index = record["end"]
                while (
                    insertion_index > record["start"] + 1
                    and not lines[insertion_index - 1].strip()
                ):
                    insertion_index -= 1
                insertions.append((
                    insertion_index,
                    [f"{name} = {value}" for name, value in missing],
                ))

        changed = bool(insertions or missing_sections)
        if not changed:
            return
        for index, additions in sorted(insertions, reverse=True):
            lines[index:index] = additions
        if missing_sections:
            if lines and lines[-1].strip():
                lines.append("")
            for section, defaults in missing_sections:
                lines.append(f"[{section}]")
                lines.extend(
                    f"{name} = {value}" for name, value in defaults.items()
                )
                lines.append("")

        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                "\n".join(lines).rstrip() + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def reset_to_defaults(self) -> bool:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(_default_config_text(), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            return False
        self.reload()
        return True

    def reload(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str.lower
        try:
            parser.read(self.path, encoding="utf-8")
        except (OSError, configparser.Error, UnicodeError):
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str.lower

        values: dict[tuple[str, str], str] = {}
        for section, defaults in DEFAULT_BINDINGS.items():
            for name, default in defaults.items():
                value = default
                if parser.has_option(section, name):
                    candidate = str(parser.get(section, name, fallback="")).strip()
                    if candidate:
                        value = candidate
                values[(section, name)] = canonical_sequence(value)
        self._values = values

    def get(self, section: str, name: str) -> str:
        default = DEFAULT_BINDINGS.get(section, {}).get(name, "")
        return self._values.get(
            (section.lower(), name.lower()), canonical_sequence(default)
        )


_SPECIAL_QT_KEYS = {
    Qt.Key_Escape: "ESCAPE",
    Qt.Key_Tab: "TAB",
    Qt.Key_Backtab: "TAB",
    Qt.Key_Backspace: "BACKSPACE",
    Qt.Key_Return: "ENTER",
    Qt.Key_Enter: "ENTER",
    Qt.Key_Insert: "INSERT",
    Qt.Key_Delete: "DELETE",
    Qt.Key_Pause: "PAUSE",
    Qt.Key_Print: "PRINT",
    Qt.Key_Home: "HOME",
    Qt.Key_End: "END",
    Qt.Key_Left: "LEFT",
    Qt.Key_Up: "UP",
    Qt.Key_Right: "RIGHT",
    Qt.Key_Down: "DOWN",
    Qt.Key_PageUp: "PAGEUP",
    Qt.Key_PageDown: "PAGEDOWN",
    Qt.Key_Space: "SPACE",
    Qt.Key_Menu: "MENU",
}


def _is_right_control(event: QKeyEvent) -> bool:
    if event.key() != Qt.Key_Control:
        return False
    if os.name != "nt":
        return False
    try:
        native_key = int(event.nativeVirtualKey())
        native_scan = int(event.nativeScanCode())
    except (AttributeError, TypeError, ValueError):
        return False
    return native_key == 0xA3 or native_scan in {0x11D, 0xE01D, 285}


def _event_key_name(event: QKeyEvent) -> str:
    key = Qt.Key(event.key())
    if key == Qt.Key_Control:
        return "RIGHTCTRL" if _is_right_control(event) else "LEFTCTRL"
    if key == Qt.Key_Alt:
        return "ALT"
    if key == Qt.Key_Shift:
        return "SHIFT"
    if key == Qt.Key_Meta:
        return "META"
    if key in _SPECIAL_QT_KEYS:
        return _SPECIAL_QT_KEYS[key]
    if Qt.Key_F1 <= key <= Qt.Key_F35:
        return f"F{int(key) - int(Qt.Key_F1) + 1}"
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(int(key))
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(int(key))
    text = str(event.text() or "").strip()
    if len(text) == 1:
        return text.upper()
    return ""


def event_sequence(event: QKeyEvent) -> str:
    key_name = _event_key_name(event)
    if not key_name:
        return ""
    if key_name in {"RIGHTCTRL", "LEFTCTRL", "ALT", "SHIFT", "META"}:
        return key_name
    modifiers = event.modifiers()
    pieces = []
    if modifiers & Qt.ControlModifier:
        pieces.append("CTRL")
    if modifiers & Qt.AltModifier:
        pieces.append("ALT")
    if modifiers & Qt.ShiftModifier:
        pieces.append("SHIFT")
    if modifiers & Qt.MetaModifier:
        pieces.append("META")
    pieces.append(key_name)
    return canonical_sequence("+".join(pieces))


_FALLBACK_LIST_BINDINGS = None


def _bindings_for_widget(widget):
    current = widget
    while current is not None:
        bindings = getattr(current, "hotkey_bindings", None)
        if bindings is not None:
            return bindings
        try:
            current = current.parentWidget()
        except (AttributeError, RuntimeError):
            current = None
    global _FALLBACK_LIST_BINDINGS
    if _FALLBACK_LIST_BINDINGS is None:
        _FALLBACK_LIST_BINDINGS = HotkeyBindings()
    return _FALLBACK_LIST_BINDINGS


def handle_list_multi_selection(widget: QListWidget, event: QKeyEvent) -> bool:
    """Select every item traversed with the configured up/down shortcuts."""
    bindings = _bindings_for_widget(widget)
    sequence = event_sequence(event)
    up_sequence = bindings.get("playlist", "multi_select_up")
    down_sequence = bindings.get("playlist", "multi_select_down")
    if sequence not in {up_sequence, down_sequence}:
        return False
    if widget.count() <= 0:
        event.accept()
        return True

    row = widget.currentRow()
    if row < 0:
        row = 0
    current = widget.item(row)
    if current is not None:
        current.setSelected(True)

    step = -1 if sequence == up_sequence else 1
    target_row = max(0, min(widget.count() - 1, row + step))
    target = widget.item(target_row)
    if target is not None:
        widget.setCurrentItem(target, QItemSelectionModel.NoUpdate)
        target.setSelected(True)
        widget.scrollToItem(target)
    event.accept()
    return True


def matches_widget_binding(
    widget: QWidget,
    event: QKeyEvent,
    section: str,
    action: str,
) -> bool:
    """Return whether an event matches the binding visible to this widget."""
    return event_sequence(event) == _bindings_for_widget(widget).get(
        section, action
    )


class KeyboardNavigationController(QObject):
    """Application-local keyboard navigation without replacing Qt Tab behavior."""

    def __init__(
        self,
        window,
        bindings: HotkeyBindings | None = None,
        parent=None,
    ):
        super().__init__(parent or window)
        self.window = window
        self.bindings = bindings or HotkeyBindings()
        self._alt_candidate = False
        self._right_ctrl_candidate = False
        # The extra focus frame is a keyboard-navigation aid only. It starts
        # disabled and is hidden again as soon as the user interacts by mouse.
        self._keyboard_navigation_active = False
        self._focus_frame = QFocusFrame(window)
        self._focus_frame.setStyleSheet(
            "QFocusFrame{"
            f"border:2px solid {ACCENT_COLOR};"
            "border-radius:7px;background:transparent;"
            "}"
        )
        self._focus_frame.hide()
        self._prepare_widgets()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        QTimer.singleShot(0, self.focus_current_page)

    def stop(self) -> None:
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
        self._focus_frame.hide()

    def _prepare_widgets(self) -> None:
        playlist_list = getattr(self.window, "playlist_list", None)
        if playlist_list is not None:
            playlist_list.setFocusPolicy(Qt.StrongFocus)
            playlist_list.setAccessibleName("Playlists")

        view = getattr(self.window, "playlist_view", None)
        if view is None:
            return
        for name, accessible_name in (
            ("songs_list", "Playlist tracks"),
            ("cover_label", "Track cover"),
            ("track_title", "Track title"),
            ("track_artist_prod", "Track artist"),
            ("lyrics_display", "Lyrics"),
        ):
            widget = getattr(view, name, None)
            if widget is None:
                continue
            widget.setFocusPolicy(Qt.StrongFocus)
            widget.setAccessibleName(accessible_name)
        for label_name in ("track_title", "track_artist_prod"):
            label = getattr(view, label_name, None)
            if isinstance(label, QLabel):
                label.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                    | Qt.TextSelectableByKeyboard
                )

    def focus_current_page(self) -> None:
        if not self.window.isVisible():
            return
        stack = getattr(self.window, "stack", None)
        if stack is None:
            return
        current = stack.currentWidget()
        preferred = None
        if current is getattr(self.window, "home_view", None):
            preferred = getattr(self.window, "playlist_list", None)
        elif current is getattr(self.window, "playlist_view", None):
            preferred = getattr(self.window.playlist_view, "songs_list", None)
        if preferred is None and current is not None:
            for child in current.findChildren(QWidget):
                if (
                    child.isVisibleTo(current)
                    and child.isEnabled()
                    and child.focusPolicy() & Qt.TabFocus
                ):
                    preferred = child
                    break
        if preferred is not None and preferred.isEnabled():
            preferred.setFocus(Qt.TabFocusReason)

    def _belongs_to_main_window(self, watched) -> bool:
        if not isinstance(watched, QWidget):
            return False
        if isinstance(watched, QMenu):
            return False
        try:
            return watched is self.window or self.window.isAncestorOf(watched)
        except RuntimeError:
            return False

    @staticmethod
    def _editing_widget(widget) -> bool:
        if isinstance(widget, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
            return True
        if isinstance(widget, QTextEdit):
            return not widget.isReadOnly()
        if isinstance(widget, QComboBox):
            return widget.isEditable()
        return False

    @staticmethod
    def _normalized_focus_widget(widget):
        current = widget
        while current is not None:
            if isinstance(
                current,
                (
                    QAbstractButton,
                    QAbstractItemView,
                    QLineEdit,
                    QTextEdit,
                    QPlainTextEdit,
                    QSlider,
                    QComboBox,
                    QAbstractSpinBox,
                    QLabel,
                ),
            ):
                return current
            current = current.parentWidget()
        return widget

    def _update_focus_frame(self, widget) -> None:
        if not self._keyboard_navigation_active:
            self._focus_frame.hide()
            return
        target = self._normalized_focus_widget(widget)
        if (
            target is None
            or not isinstance(target, QWidget)
            or isinstance(target, QMenu)
            or not target.isVisible()
            or not target.isEnabled()
        ):
            self._focus_frame.hide()
            return
        try:
            self._focus_frame.setWidget(target)
            self._focus_frame.show()
            self._focus_frame.raise_()
        except RuntimeError:
            self._focus_frame.hide()

    def _set_keyboard_navigation_active(self, active: bool) -> None:
        active = bool(active)
        if self._keyboard_navigation_active == active:
            if active:
                self._update_focus_frame(QApplication.focusWidget())
            return
        self._keyboard_navigation_active = active
        if not active:
            self._focus_frame.hide()
            return
        QTimer.singleShot(0, lambda: self._update_focus_frame(QApplication.focusWidget()))

    def _is_navigation_key(self, event: QKeyEvent) -> bool:
        key = Qt.Key(event.key())
        if key in {
            Qt.Key_Tab,
            Qt.Key_Backtab,
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
            Qt.Key_Return,
            Qt.Key_Enter,
            Qt.Key_Escape,
            Qt.Key_Backspace,
            Qt.Key_Menu,
        }:
            return True
        sequence = event_sequence(event)
        return sequence in {
            self.bindings.get("navigation", "context_menu"),
            self.bindings.get("navigation", "context_menu_fallback"),
            self.bindings.get("playlist", "play_selected"),
        }

    def eventFilter(self, watched, event):
        event_type = event.type()

        # QMenu normally handles Escape itself, but the application-wide
        # filter closes it explicitly as well. This keeps keyboard-only
        # navigation predictable for every context menu opened by CloudPlayer.
        if event_type == QEvent.KeyPress and isinstance(watched, QMenu):
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                watched.close()
                event.accept()
                return True

        if event_type in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonDblClick,
            QEvent.Wheel,
            QEvent.TouchBegin,
        ) and self._belongs_to_main_window(watched):
            # Mouse/touch use returns the UI to its normal appearance. Native
            # selection remains, but the keyboard-only focus frame disappears.
            self._set_keyboard_navigation_active(False)
            return False

        if event_type == QEvent.FocusIn and self._belongs_to_main_window(watched):
            self._update_focus_frame(watched)
            return False
        if event_type == QEvent.WindowDeactivate:
            self._focus_frame.hide()
            return False
        if event_type not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if not self._belongs_to_main_window(watched):
            return False
        app = QApplication.instance()
        if app is None or app.activeModalWidget() is not None:
            return False
        if QApplication.activeWindow() not in (None, self.window):
            return False
        if not isinstance(event, QKeyEvent):
            return False

        if event_type == QEvent.KeyPress and self._is_navigation_key(event):
            self._set_keyboard_navigation_active(True)

        if event_type == QEvent.KeyPress:
            return self._key_press(event)
        return self._key_release(event)

    def _key_press(self, event: QKeyEvent) -> bool:
        sequence = event_sequence(event)
        if not sequence:
            self._alt_candidate = False
            self._right_ctrl_candidate = False
            return False

        if sequence == "ALT":
            self._alt_candidate = not event.isAutoRepeat()
            if (
                self._alt_candidate
                and self.bindings.get("playlist", "add_song") == "ALT"
                and self._playlist_page_active()
            ):
                # Suppress the native Windows menu-bar activation. The action
                # is executed on key release so Alt can still be distinguished
                # from combinations such as Alt+Tab.
                event.accept()
                return True
            return False
        if sequence == "RIGHTCTRL":
            self._right_ctrl_candidate = not event.isAutoRepeat()
            return False

        if self._alt_candidate:
            self._alt_candidate = False
        if self._right_ctrl_candidate:
            self._right_ctrl_candidate = False

        if event.isAutoRepeat() and sequence not in {
            self.bindings.get("playback", "volume_down"),
            self.bindings.get("playback", "volume_up"),
        }:
            return False

        if sequence == self.bindings.get("navigation", "context_menu_fallback"):
            if self._open_context_menu():
                event.accept()
                return True

        configured_context = self.bindings.get("navigation", "context_menu")
        if configured_context not in {"RIGHTCTRL", "ALT"} and sequence == configured_context:
            if self._open_context_menu():
                event.accept()
                return True

        focus = self._normalized_focus_widget(QApplication.focusWidget())
        editing = self._editing_widget(focus)

        if sequence == self.bindings.get("navigation", "activate"):
            if editing:
                return False
            if self._activate_focused(focus):
                event.accept()
                return True

        if sequence == self.bindings.get("playlist", "play_selected"):
            if editing:
                return False
            if self._play_selected_track():
                event.accept()
                return True

        if sequence == self.bindings.get("navigation", "cancel"):
            if editing:
                return False
            if self._go_back():
                event.accept()
                return True

        if sequence == self.bindings.get("navigation", "back"):
            if editing:
                return False
            if self._go_back():
                event.accept()
                return True

        if sequence == self.bindings.get("playback", "play_pause_window"):
            if editing:
                return False
            self.window.playlist_view.toggle_playback()
            event.accept()
            return True

        if sequence == self.bindings.get("playback", "repeat"):
            if self._playlist_page_active():
                self.window.playlist_view.toggle_repeat()
                event.accept()
                return True

        if sequence == self.bindings.get("playback", "shuffle"):
            if self._playlist_page_active():
                self.window.playlist_view.toggle_shuffle()
                event.accept()
                return True

        if sequence == self.bindings.get("playlist", "delete_selected"):
            if editing:
                return False
            if self._delete_selected(focus):
                event.accept()
                return True

        if sequence == self.bindings.get("playlist", "new_playlist"):
            if editing:
                return False
            self.window.create_playlist()
            event.accept()
            return True

        if sequence == self.bindings.get("playlist", "search"):
            if editing:
                return False
            self.window.run_search()
            event.accept()
            return True

        section_actions = {
            self.bindings.get("sections", "home"): self._show_home,
            self.bindings.get("sections", "playlist"): self._show_playlist,
            self.bindings.get("sections", "search"): self.window.run_search,
            self.bindings.get("sections", "queue"): self.window.playlist_view.show_queue,
            self.bindings.get("sections", "listen_together"): self._show_listen_together,
            self.bindings.get("sections", "settings"): self.window._show_settings_dialog,
            self.bindings.get("sections", "now_playing"): self._show_now_playing,
        }
        section_action = section_actions.get(sequence)
        if section_action is not None and not editing:
            section_action()
            event.accept()
            return True

        # Tab, Shift+Tab, arrows, Home/End and PageUp/PageDown are deliberately
        # not consumed. Qt keeps its native, smooth focus/list navigation.
        return False

    def _key_release(self, event: QKeyEvent) -> bool:
        sequence = event_sequence(event)
        if sequence == "ALT":
            trigger = self._alt_candidate
            self._alt_candidate = False
            if (
                trigger
                and self.bindings.get("playlist", "add_song") == "ALT"
                and self._playlist_page_active()
            ):
                self.window.playlist_view.add_song()
                event.accept()
                return True
            return False
        if sequence == "RIGHTCTRL":
            trigger = self._right_ctrl_candidate
            self._right_ctrl_candidate = False
            if (
                trigger
                and self.bindings.get("navigation", "context_menu")
                == "RIGHTCTRL"
                and self._open_context_menu()
            ):
                event.accept()
                return True
        return False

    def _playlist_page_active(self) -> bool:
        stack = getattr(self.window, "stack", None)
        view = getattr(self.window, "playlist_view", None)
        return (
            stack is not None
            and view is not None
            and stack.currentWidget() is view
            and bool(view.current_playlist)
        )

    def _show_home(self) -> None:
        self.window._switch(0)

    def _show_playlist(self) -> None:
        view = self.window.playlist_view
        if view.current_playlist:
            self.window._switch(1)
        else:
            self.window._switch(0)
            QTimer.singleShot(0, self.focus_current_page)

    def _show_listen_together(self) -> None:
        self.window._switch(2)

    def _show_now_playing(self) -> None:
        view = self.window.playlist_view
        if view.current_playlist or view.current_track_path:
            self.window._switch(1)
            QTimer.singleShot(
                0,
                lambda: view.play_btn.setFocus(Qt.ShortcutFocusReason),
            )

    def _go_back(self) -> bool:
        stack = getattr(self.window, "stack", None)
        if stack is None or stack.currentIndex() == 0:
            return False
        if stack.currentWidget() is self.window.playlist_view:
            self.window.playlist_view.leave_playlist()
        else:
            self.window._switch(0)
        return True

    def _activate_focused(self, focus) -> bool:
        if focus is None:
            return False
        if focus is getattr(self.window, "playlist_list", None):
            item = focus.currentItem()
            if item is not None:
                self.window.open_playlist(item)
                return True
        songs = getattr(self.window.playlist_view, "songs_list", None)
        if focus is songs:
            item = songs.currentItem()
            if item is not None:
                self.window.playlist_view.play_song(item)
                return True
        if isinstance(focus, QAbstractButton) and focus.isEnabled():
            focus.click()
            return True
        return False

    def _play_selected_track(self) -> bool:
        songs = getattr(self.window.playlist_view, "songs_list", None)
        if songs is None or not self._playlist_page_active():
            return False
        item = songs.currentItem()
        if item is None:
            return False
        self.window.playlist_view.play_song(item)
        return True

    def _delete_selected(self, focus) -> bool:
        if focus is getattr(self.window, "playlist_list", None):
            self.window.remove_playlist()
            return True
        songs = getattr(self.window.playlist_view, "songs_list", None)
        if focus is songs and hasattr(self.window.playlist_view, "delete_selected_tracks"):
            return bool(self.window.playlist_view.delete_selected_tracks())
        return False

    @staticmethod
    def _list_context_position(widget: QListWidget) -> QPoint | None:
        item = widget.currentItem()
        if item is None and widget.count():
            widget.setCurrentRow(0)
            item = widget.currentItem()
        if item is None:
            return None
        if not item.isSelected():
            widget.clearSelection()
            item.setSelected(True)
        rectangle = widget.visualItemRect(item)
        return rectangle.center() if rectangle.isValid() else widget.rect().center()

    def _default_context_target(self):
        stack = getattr(self.window, "stack", None)
        if stack is None:
            return None
        if stack.currentWidget() is getattr(self.window, "home_view", None):
            return getattr(self.window, "playlist_list", None)
        if stack.currentWidget() is getattr(self.window, "playlist_view", None):
            return getattr(self.window.playlist_view, "songs_list", None)
        return None

    def _open_context_menu(self) -> bool:
        focus = self._normalized_focus_widget(QApplication.focusWidget())
        target = focus or self._default_context_target()
        if target is None:
            return False

        if isinstance(target, QListWidget):
            position = self._list_context_position(target)
            if position is None:
                return False
            if target.contextMenuPolicy() == Qt.CustomContextMenu:
                target.customContextMenuRequested.emit(position)
                return True

        if isinstance(target, QTextEdit):
            position = target.cursorRect().center()
            menu = target.createStandardContextMenu(position)
            if menu is None or menu.isEmpty():
                return False
            menu.exec(target.viewport().mapToGlobal(position))
            return True

        if isinstance(target, (QLineEdit, QPlainTextEdit)):
            menu = target.createStandardContextMenu()
            if menu is None or menu.isEmpty():
                return False
            menu.exec(target.mapToGlobal(target.rect().center()))
            return True

        if isinstance(target, QLabel):
            position = target.rect().center()
            if target.contextMenuPolicy() == Qt.CustomContextMenu:
                target.customContextMenuRequested.emit(position)
                return True
            text = target.selectedText() or target.text()
            if text:
                menu = make_menu(target)
                copy_action = menu.addAction("Copy")
                chosen = menu.exec(target.mapToGlobal(position))
                if chosen is copy_action:
                    QApplication.clipboard().setText(text)
                return True

        if isinstance(target, QWidget):
            position = target.rect().center()
            if target.contextMenuPolicy() == Qt.CustomContextMenu:
                target.customContextMenuRequested.emit(position)
                return True
            keyboard_reason = getattr(
                QContextMenuEvent, "Keyboard", None
            )
            if keyboard_reason is None:
                keyboard_reason = QContextMenuEvent.Reason.Keyboard
            context_event = QContextMenuEvent(
                keyboard_reason,
                position,
                target.mapToGlobal(position),
            )
            return bool(QApplication.sendEvent(target, context_event))
        return False


class GlobalHotkeyThread(QThread):
    play_pause = Signal()
    previous = Signal()
    next = Signal()
    mute = Signal()
    volume_down = Signal()
    volume_up = Signal()
    failed = Signal(str)

    DEBOUNCE_SECONDS = 0.20

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000

    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_STOP = 0xB2
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    def __init__(self, parent=None, bindings: HotkeyBindings | None = None):
        super().__init__(parent)
        self.bindings = bindings or HotkeyBindings()
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

    def _global_bindings(self):
        configured = (
            (self.bindings.get("playback", "previous"), "previous", self.previous),
            (self.bindings.get("playback", "play_pause"), "play_pause", self.play_pause),
            (self.bindings.get("playback", "next"), "next", self.next),
            (self.bindings.get("playback", "mute"), "mute", self.mute),
            (self.bindings.get("playback", "volume_down"), "volume_down", self.volume_down),
            (self.bindings.get("playback", "volume_up"), "volume_up", self.volume_up),
            ("MediaPrevious", "previous", self.previous),
            ("MediaPlayPause", "play_pause", self.play_pause),
            ("MediaNext", "next", self.next),
            ("VolumeMute", "mute", self.mute),
            ("VolumeDown", "volume_down", self.volume_down),
            ("VolumeUp", "volume_up", self.volume_up),
        )
        output = []
        seen = set()
        for sequence, name, signal in configured:
            canonical = canonical_sequence(sequence)
            key = (canonical, name)
            if not canonical or key in seen:
                continue
            seen.add(key)
            output.append((canonical, name, signal))
        return output

    def run(self) -> None:
        native_error = ""
        self.bindings.reload()

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

    @classmethod
    def _windows_hotkey(cls, sequence: str):
        pieces = canonical_sequence(sequence).split("+")
        if not pieces:
            return None
        modifiers = 0
        key_name = pieces[-1]
        for piece in pieces[:-1]:
            if piece == "ALT":
                modifiers |= cls.MOD_ALT
            elif piece == "CTRL":
                modifiers |= cls.MOD_CONTROL
            elif piece == "SHIFT":
                modifiers |= cls.MOD_SHIFT
            elif piece == "META":
                modifiers |= cls.MOD_WIN
            else:
                return None

        media_keys = {
            "MEDIAPREVIOUS": cls.VK_MEDIA_PREV_TRACK,
            "MEDIAPLAYPAUSE": cls.VK_MEDIA_PLAY_PAUSE,
            "MEDIANEXT": cls.VK_MEDIA_NEXT_TRACK,
            "VOLUMEMUTE": cls.VK_VOLUME_MUTE,
            "VOLUMEDOWN": cls.VK_VOLUME_DOWN,
            "VOLUMEUP": cls.VK_VOLUME_UP,
        }
        virtual_key = media_keys.get(key_name)
        if virtual_key is None and key_name.startswith("F"):
            try:
                number = int(key_name[1:])
            except ValueError:
                number = 0
            if 1 <= number <= 24:
                virtual_key = 0x70 + number - 1
        if virtual_key is None and len(key_name) == 1 and key_name.isalnum():
            virtual_key = ord(key_name)
        if virtual_key is None:
            return None
        return modifiers | cls.MOD_NOREPEAT, virtual_key

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

        bindings = {}
        registered: list[int] = []
        try:
            for hotkey_id, (sequence, name, signal) in enumerate(
                self._global_bindings(), start=1
            ):
                parsed = self._windows_hotkey(sequence)
                if parsed is None:
                    continue
                modifiers, virtual_key = parsed
                if user32.RegisterHotKey(
                    None,
                    hotkey_id,
                    modifiers,
                    virtual_key,
                ):
                    registered.append(hotkey_id)
                    bindings[hotkey_id] = (name, signal)

            if not registered:
                error_code = ctypes.get_last_error()
                raise OSError(
                    error_code,
                    "Windows did not register the configured media hotkeys",
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

                binding = bindings.get(int(message.wParam))
                if binding is None:
                    continue
                name, signal = binding
                self._emit_once(name, signal)
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self._win_thread_id = 0
            self._backend = ""

    @staticmethod
    def _keyboard_name(sequence: str) -> str:
        aliases = {
            "MEDIAPREVIOUS": "previous track",
            "MEDIAPLAYPAUSE": "play/pause media",
            "MEDIANEXT": "next track",
            "VOLUMEMUTE": "volume mute",
            "VOLUMEDOWN": "volume down",
            "VOLUMEUP": "volume up",
        }
        canonical = canonical_sequence(sequence)
        return aliases.get(canonical, canonical.lower().replace("meta", "windows"))

    def _run_keyboard_backend(self) -> None:
        import keyboard

        self._keyboard = keyboard
        self._backend = "keyboard"

        self._keyboard_handles.clear()
        for sequence, name, signal in self._global_bindings():
            try:
                handle = keyboard.add_hotkey(
                    self._keyboard_name(sequence),
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
