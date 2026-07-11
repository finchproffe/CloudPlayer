import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSlider,
    QTextEdit, QVBoxLayout, QWidget,
)

from config import (
    ACCENT_COLOR, AUDIO_EXTENSIONS, BG_COLOR, BUTTON_BORDER, PANEL_BG,
    PLAYLISTS_PATH, TEXT_COLOR, TEXT_MUTED,
)
from dialogs import AddSongDialog
from smooth_scroll import SmoothScrollArea
from threads import TrackMetaFetcher
from utils import colored_icon, format_time, rounded_cover_pixmap
import discord_rpc


MENU_ICON_SIZE = 28
MENU_TEXT_SIZE = 14
MENU_STYLE = f"""
QMenu {{
 background-color: {PANEL_BG}; color: {TEXT_COLOR};
 border: 1px solid {BUTTON_BORDER}; border-radius: 4px;
 padding: 4px; font-size: {MENU_TEXT_SIZE}px; font-weight: 700;
}}
QMenu::item {{
 background-color: transparent; padding: 3px 10px 3px 8px;
 margin: 0px; border-radius: 3px; min-height: 18px;
}}
QMenu::item:selected {{ background-color: {ACCENT_COLOR}; color: #ffffff; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; margin: 4px 6px; background: {BUTTON_BORDER}; }}
QMenu::icon {{ width: {MENU_ICON_SIZE}px; height: {MENU_ICON_SIZE}px; }}
"""


def make_menu(parent):
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_STYLE)
    return menu


class BoundedSongList(QListWidget):
    """Internal drag with an exact clipped preview and smooth edge scrolling."""

    reorder_started = Signal(list)
    EDGE_ZONE = 72
    MAX_SCROLL_SPEED = 22.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_item = None
        self._drag_hotspot_y = 0
        self._last_cursor_y = 0
        self._scroll_speed = 0.0
        self._target_scroll_speed = 0.0

        self._drag_preview = QLabel(self.viewport())
        self._drag_preview.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag_preview.setStyleSheet("background:transparent;border:none")
        self._drag_preview.hide()

        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(16)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        item_rect = self.visualRect(self.indexFromItem(item))
        if not item_rect.isValid():
            return

        self.reorder_started.emit([
            self.item(row).data(Qt.UserRole) for row in range(self.count())
        ])
        cursor = self.viewport().mapFromGlobal(self.cursor().pos())
        self._drag_item = item
        self._last_cursor_y = cursor.y()
        self._drag_hotspot_y = max(
            0, min(item_rect.height() - 1, cursor.y() - item_rect.top())
        )

        preview = self.viewport().grab(item_rect)
        self._drag_preview.setPixmap(preview)
        self._drag_preview.setFixedHeight(item_rect.height())
        self._move_preview(cursor.y())
        self._drag_preview.show()
        self._drag_preview.raise_()
        self._auto_scroll_timer.start()

        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
        transparent = QPixmap(1, 1)
        transparent.fill(Qt.transparent)
        drag.setPixmap(transparent)
        drag.exec(Qt.MoveAction)
        self._finish_drag_preview()

    def dragEnterEvent(self, event):
        super().dragEnterEvent(event)
        if event.isAccepted() and self._drag_item is not None:
            self._drag_preview.show()
            self._drag_preview.raise_()

    def dragMoveEvent(self, event):
        self._last_cursor_y = event.position().toPoint().y()
        self._update_auto_scroll_target(self._last_cursor_y)
        self._move_preview(self._last_cursor_y)
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._target_scroll_speed = 0.0
        self._drag_preview.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        super().dropEvent(event)
        self._finish_drag_preview()

    def _update_auto_scroll_target(self, y):
        height = self.viewport().height()
        if y < self.EDGE_ZONE:
            strength = 1.0 - max(0, y) / self.EDGE_ZONE
            self._target_scroll_speed = -self.MAX_SCROLL_SPEED * strength
        elif y > height - self.EDGE_ZONE:
            strength = 1.0 - max(0, height - y) / self.EDGE_ZONE
            self._target_scroll_speed = self.MAX_SCROLL_SPEED * strength
        else:
            self._target_scroll_speed = 0.0

    def _auto_scroll_tick(self):
        if self._drag_item is None:
            self._auto_scroll_timer.stop()
            return
        self._scroll_speed += (
            self._target_scroll_speed - self._scroll_speed
        ) * 0.22
        if abs(self._scroll_speed) < 0.15 and self._target_scroll_speed == 0:
            self._scroll_speed = 0.0
            return
        bar = self.verticalScrollBar()
        old_value = bar.value()
        bar.setValue(round(old_value + self._scroll_speed))
        if bar.value() != old_value:
            self._move_preview(self._last_cursor_y)
            self.viewport().update()

    def _move_preview(self, cursor_y):
        if self._drag_item is None:
            return
        height = self._drag_preview.height()
        max_y = max(0, self.viewport().height() - height)
        y = max(0, min(cursor_y - self._drag_hotspot_y, max_y))
        self._drag_preview.setGeometry(0, y, self.viewport().width(), height)

    def _finish_drag_preview(self):
        self._auto_scroll_timer.stop()
        self._scroll_speed = 0.0
        self._target_scroll_speed = 0.0
        self._drag_preview.hide()
        self._drag_preview.clear()
        self._drag_item = None


class TrackListItemWidget(QWidget):
    ROW_HEIGHT = 75
    COVER_SIZE = 64

    def __init__(self, index, title, artist, cover_pixmap=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;border:none")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(10)

        cover = QLabel()
        cover.setFixedSize(self.COVER_SIZE, self.COVER_SIZE)
        cover.setAlignment(Qt.AlignCenter)
        rendered = rounded_cover_pixmap(cover_pixmap, self.COVER_SIZE, 6)
        if rendered:
            cover.setPixmap(rendered)
        else:
            cover.setText("♪")
            cover.setStyleSheet(
                f"background:{PANEL_BG};border-radius:6px;color:{TEXT_MUTED};font-size:22px"
            )

        labels = QVBoxLayout()
        labels.setSpacing(2)
        labels.addStretch()
        title_label = QLabel(f"{index}. {title}")
        title_label.setStyleSheet(
            f"background:transparent;font-size:14px;font-weight:700;color:{TEXT_COLOR}"
        )
        artist_label = QLabel(artist)
        artist_label.setStyleSheet(
            f"background:transparent;font-size:11px;color:{TEXT_MUTED}"
        )
        labels.addWidget(title_label)
        labels.addWidget(artist_label)
        labels.addStretch()
        layout.addWidget(cover)
        layout.addLayout(labels, 1)


class CoverPreviewDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cover: Full Size")
        self.resize(720, 720)
        self.original_pixmap = pixmap
        self.scale_factor = 1.0
        self.dragging = False
        self.last_pos = QPoint()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = SmoothScrollArea(self, duration=300, wheel_step=100)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setStyleSheet(f"background:{BG_COLOR};border:none")
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.image)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.viewport().setMouseTracking(True)
        layout.addWidget(self.scroll)
        hint = QLabel("Wheel to zoom • Hold left mouse button and drag to pan")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color:{TEXT_MUTED};padding:10px")
        layout.addWidget(hint)
        self._render()

    def _render(self):
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        scaled = self.original_pixmap.scaled(
            self.original_pixmap.size() * self.scale_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image.setPixmap(scaled)
        self.image.resize(scaled.size())

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport():
            if event.type() == QEvent.Wheel:
                factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
                self.scale_factor = max(0.1, min(8, self.scale_factor * factor))
                self._render()
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.dragging = True
                self.last_pos = event.position().toPoint()
                self.scroll.viewport().setCursor(Qt.ClosedHandCursor)
                return True
            if event.type() == QEvent.MouseMove and self.dragging:
                delta = event.position().toPoint() - self.last_pos
                self.last_pos = event.position().toPoint()
                horizontal = self.scroll.horizontalScrollBar()
                vertical = self.scroll.verticalScrollBar()
                horizontal.setValue(horizontal.value() - delta.x())
                vertical.setValue(vertical.value() - delta.y())
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.dragging = False
                self.scroll.viewport().setCursor(Qt.ArrowCursor)
                return True
        return super().eventFilter(obj, event)


class PlaylistView(QWidget):
    back_requested = Signal()
    sync_requested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.current_track_index = -1
        self.current_cover_pixmap = None
        self.current_track_filename = None
        self.is_shuffled = False
        self.repeat_track = False
        self.meta_thread = None
        self._reorder_refresh_pending = False
        self._drag_order_snapshot = None
        self._order_undo_stack = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        header = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(110, 42)
        self.back_btn.clicked.connect(self.back_requested)
        self.playlist_name = QLabel("Playlist")
        self.playlist_name.setStyleSheet("font-size:24px;font-weight:700")
        self.now_playing = QLabel("Now Playing: None")
        self.now_playing.setStyleSheet(f"color:{TEXT_MUTED};font-size:15px")
        self.volume_btn = QPushButton()
        self.volume_btn.setIcon(colored_icon("volume-on.svg"))
        self.volume_btn.setFixedSize(34, 34)
        self.volume_btn.setFlat(True)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.setValue(70)
        header.addWidget(self.back_btn)
        header.addSpacing(15)
        header.addWidget(self.playlist_name)
        header.addStretch()
        header.addWidget(self.now_playing)
        header.addStretch()
        header.addWidget(self.volume_btn)
        header.addWidget(self.volume_slider)
        root.addLayout(header)

        center = QHBoxLayout()
        self.songs_list = BoundedSongList()
        self.songs_list.setUniformItemSizes(True)
        self.songs_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.songs_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.songs_list.setDragEnabled(True)
        self.songs_list.viewport().setAcceptDrops(True)
        self.songs_list.setDropIndicatorShown(True)
        self.songs_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.songs_list.setDefaultDropAction(Qt.MoveAction)
        self.songs_list.setDragDropOverwriteMode(False)
        self.songs_list.reorder_started.connect(self._capture_reorder_start)
        self.songs_list.model().rowsMoved.connect(self._songs_reordered)
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(self._track_menu)
        self.songs_list.itemDoubleClicked.connect(self.play_song)
        center.addWidget(self.songs_list, 25)

        sidebar = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setScaledContents(True)
        self.cover_label.setStyleSheet(
            f"background:{PANEL_BG};border:1px solid {BUTTON_BORDER};border-radius:4px"
        )
        self.cover_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cover_label.customContextMenuRequested.connect(self._cover_menu)
        self.track_title = QLabel("Track Title")
        self.track_title.setWordWrap(True)
        self.track_title.setStyleSheet("font-size:20px;font-weight:700")
        self.track_artist_prod = QLabel("Artist")
        self.track_artist_prod.setStyleSheet(f"color:{TEXT_MUTED}")
        self.lyrics_display = QTextEdit()
        self.lyrics_display.setReadOnly(True)
        sidebar.addWidget(self.cover_label, 0, Qt.AlignCenter)
        sidebar.addWidget(self.track_title)
        sidebar.addWidget(self.track_artist_prod)
        sidebar.addWidget(self.lyrics_display, 1)
        side = QWidget()
        side.setLayout(sidebar)
        center.addWidget(side, 10)
        root.addLayout(center, 1)

        timeline = QHBoxLayout()
        self.current_time = QLabel("0:00")
        self.position = QSlider(Qt.Horizontal)
        self.total_time = QLabel("0:00")
        self.position.sliderReleased.connect(self.seek_from_slider)
        timeline.addWidget(self.current_time)
        timeline.addWidget(self.position, 1)
        timeline.addWidget(self.total_time)
        root.addLayout(timeline)

        controls = QHBoxLayout()
        controls.addStretch()
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(colored_icon("prev.svg"))
        self.play_btn = QPushButton()
        self.play_btn.setIcon(colored_icon("play.svg"))
        self.play_btn.setStyleSheet(f"background:{ACCENT_COLOR}")
        self.next_btn = QPushButton()
        self.next_btn.setIcon(colored_icon("next.svg"))
        self.repeat_btn = QPushButton()
        self.repeat_btn.setIcon(colored_icon("repeat-off.svg"))
        for button in (self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn):
            button.setFixedSize(40, 40)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.repeat_btn)
        controls.addStretch()
        root.addLayout(controls)

        actions = QHBoxLayout()
        self.shuffle_btn = QPushButton(" Shuffle Mode")
        self.shuffle_btn.setIcon(colored_icon("shuffle.svg"))
        add_song = QPushButton("Add Song")
        actions.addWidget(self.shuffle_btn)
        actions.addWidget(add_song)
        root.addLayout(actions)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)
        self.prev_btn.clicked.connect(self.play_prev_track)
        self.play_btn.clicked.connect(self.toggle_playback)
        self.next_btn.clicked.connect(self.play_next_track)
        self.repeat_btn.clicked.connect(self.toggle_repeat)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        add_song.clicked.connect(self.add_song)
        self.volume_slider.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        self.volume_btn.clicked.connect(self.toggle_mute)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.playbackStateChanged.connect(
            lambda state: self.play_btn.setIcon(colored_icon(
                "pause.svg" if state == QMediaPlayer.PlayingState else "play.svg"
            ))
        )
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo_song_reorder)

    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = PLAYLISTS_PATH / name / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(name)
        self._order_undo_stack.clear()
        self._drag_order_snapshot = None
        self.refresh()

    def _playlist_metadata_path(self):
        return PLAYLISTS_PATH / f"{self.current_playlist}.json" if self.current_playlist else None

    def _read_playlist_metadata(self):
        path = self._playlist_metadata_path()
        if not path or not path.exists():
            return {"name": self.current_playlist or "", "songs": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {"name": self.current_playlist or "", "songs": []}

    def _write_song_order(self, filenames):
        path = self._playlist_metadata_path()
        if not path:
            return
        data = self._read_playlist_metadata()
        data["name"] = self.current_playlist
        data["songs"] = list(filenames)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ordered_song_files(self):
        if not self.current_playlist_path:
            return []
        files_by_name = {
            file.name: file for file in self.current_playlist_path.iterdir()
            if file.is_file() and file.suffix.lower() in AUDIO_EXTENSIONS
        }
        saved_order = self._read_playlist_metadata().get("songs", [])
        if not isinstance(saved_order, list):
            saved_order = []
        ordered_names, seen = [], set()
        for name in saved_order:
            if isinstance(name, str) and name in files_by_name and name not in seen:
                ordered_names.append(name)
                seen.add(name)
        for name in sorted(files_by_name, key=str.casefold):
            if name not in seen:
                ordered_names.append(name)
                seen.add(name)
        if ordered_names != saved_order:
            self._write_song_order(ordered_names)
        return [files_by_name[name] for name in ordered_names]

    def refresh(self):
        selected = self.songs_list.currentItem()
        selected_name = selected.data(Qt.UserRole) if selected else None
        self.songs_list.clear()
        if not self.current_playlist_path:
            return
        for number, file in enumerate(self._ordered_song_files(), 1):
            title, artist, _ = self._metadata(file)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, file.name)
            item.setSizeHint(QSize(0, TrackListItemWidget.ROW_HEIGHT))
            item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.songs_list.addItem(item)
            self.songs_list.setItemWidget(
                item, TrackListItemWidget(number, title, artist, self._cover(file))
            )
            if file.name == selected_name:
                self.songs_list.setCurrentItem(item)
        self._sync_current_track_index()

    update_songs_list = refresh

    def _capture_reorder_start(self, order):
        self._drag_order_snapshot = list(order)

    def _songs_reordered(self, *_args):
        if self._reorder_refresh_pending:
            return
        self._reorder_refresh_pending = True
        QTimer.singleShot(0, self._finish_song_reorder)

    def _finish_song_reorder(self):
        self._reorder_refresh_pending = False
        filenames = [
            self.songs_list.item(row).data(Qt.UserRole)
            for row in range(self.songs_list.count())
        ]
        previous = self._drag_order_snapshot
        self._drag_order_snapshot = None
        if previous and previous != filenames:
            self._order_undo_stack.append(previous)
            self._order_undo_stack = self._order_undo_stack[-50:]
        self._write_song_order(filenames)
        self._sync_current_track_index()
        self.refresh()

    def undo_song_reorder(self):
        if not self._order_undo_stack or not self.current_playlist_path:
            return
        previous = self._order_undo_stack.pop()
        existing = {
            file.name for file in self.current_playlist_path.iterdir()
            if file.is_file() and file.suffix.lower() in AUDIO_EXTENSIONS
        }
        restored = [name for name in previous if name in existing]
        restored.extend(sorted(existing.difference(restored), key=str.casefold))
        self._drag_order_snapshot = None
        self._write_song_order(restored)
        self.refresh()

    def _sync_current_track_index(self):
        if not self.current_track_filename:
            self.current_track_index = -1
            return
        self.current_track_index = next((
            row for row in range(self.songs_list.count())
            if self.songs_list.item(row).data(Qt.UserRole) == self.current_track_filename
        ), -1)

    def _replace_song_in_order(self, old_name, new_name):
        order = self._read_playlist_metadata().get("songs", [])
        order = order if isinstance(order, list) else []
        self._write_song_order([new_name if name == old_name else name for name in order])

    def _insert_song_after(self, source_name, new_name):
        order = self._read_playlist_metadata().get("songs", [])
        order = order if isinstance(order, list) else []
        order = [name for name in order if name != new_name]
        try:
            index = order.index(source_name) + 1
        except ValueError:
            index = len(order)
        order.insert(index, new_name)
        self._write_song_order(order)

    def _remove_song_from_order(self, filename):
        order = self._read_playlist_metadata().get("songs", [])
        order = order if isinstance(order, list) else []
        self._write_song_order([name for name in order if name != filename])

    def _metadata(self, file):
        data = {}
        try:
            sidecar = file.with_suffix(".json")
            if sidecar.exists():
                data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
        title, artist = data.get("title"), data.get("artist")
        if (not title or not artist) and " - " in file.stem:
            parsed_artist, parsed_title = file.stem.split(" - ", 1)
            title, artist = title or parsed_title, artist or parsed_artist
        return title or file.stem, artist or "Unknown Artist", data

    @staticmethod
    def _cover(file):
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            path = file.with_suffix(extension)
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap
        return None

    def play_song(self, item):
        self.play_file(
            self.current_playlist_path / item.data(Qt.UserRole),
            item.data(Qt.UserRole), self.songs_list.row(item)
        )

    def play_file(self, file, filename=None, index=-1, broadcast=True):
        path = Path(file)
        title, artist, _ = self._metadata(path)
        self.current_track_index = index
        self.current_track_filename = filename or path.name
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.now_playing.setText(f"Now Playing: {title} • {artist}")
        self.track_title.setText(title)
        self.track_artist_prod.setText(artist)
        self.lyrics_display.setText("Loading lyrics...")
        self.meta_thread = TrackMetaFetcher(path, self)
        self.meta_thread.meta_ready.connect(self.apply_metadata)
        self.meta_thread.start()
        if broadcast:
            self.sync_requested.emit("play", 0)

    def apply_metadata(self, data):
        self.track_title.setText(data.get("title", "Unknown"))
        self.track_artist_prod.setText(data.get("artist", "Unknown"))
        self.lyrics_display.setText(data.get("lyrics", ""))
        if data.get("cover_bytes"):
            pixmap = QPixmap()
            pixmap.loadFromData(data["cover_bytes"])
            self.current_cover_pixmap = pixmap
            self.cover_label.setPixmap(pixmap)
        discord_rpc.update_now_playing(
            data.get("title", "Unknown"), data.get("artist", "Unknown"),
            data.get("cover_url")
        )

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.sync_requested.emit("pause", self.player.position())
        else:
            if not self.player.source().isValid() and self.songs_list.count():
                self.play_song(self.songs_list.item(0))
                return
            self.player.play()
            self.sync_requested.emit("play", self.player.position())

    def seek_from_slider(self):
        self.player.setPosition(self.position.value())
        self.sync_requested.emit("seek", self.position.value())

    def _position_changed(self, value):
        self.current_time.setText(format_time(value))
        if not self.position.isSliderDown():
            self.position.setValue(value)

    def _duration_changed(self, value):
        self.total_time.setText(format_time(value))
        self.position.setRange(0, value)

    def toggle_repeat(self):
        self.repeat_track = not self.repeat_track
        self.repeat_btn.setIcon(colored_icon(
            "repeat.svg" if self.repeat_track else "repeat-off.svg"
        ))

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        self.shuffle_btn.setStyleSheet(
            f"background:{ACCENT_COLOR}" if self.is_shuffled else ""
        )

    def play_next_track(self):
        count = self.songs_list.count()
        if not count:
            return
        row = random.randrange(count) if self.is_shuffled else (self.current_track_index + 1) % count
        self.play_song(self.songs_list.item(row))

    def play_prev_track(self):
        count = self.songs_list.count()
        if count:
            self.play_song(self.songs_list.item((self.current_track_index - 1) % count))

    play_next = play_next_track
    play_previous = play_prev_track

    def _media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            if self.repeat_track:
                self.player.setPosition(0)
                self.player.play()
            else:
                self.play_next_track()

    def toggle_mute(self):
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self.volume_btn.setIcon(colored_icon(
            "volume-off.svg" if self.audio_output.isMuted() else "volume-on.svg"
        ))

    def add_song(self):
        if self.current_playlist and AddSongDialog(self, self.current_playlist).exec():
            self.refresh()

    def _track_menu(self, position):
        item = self.songs_list.itemAt(position)
        if not item:
            return
        self.songs_list.setCurrentItem(item)
        menu = make_menu(self)
        play = menu.addAction(colored_icon("play.svg", size=MENU_ICON_SIZE), "Play")
        menu.addSeparator()
        rename = menu.addAction(colored_icon("rename.svg", size=MENU_ICON_SIZE), "Rename")
        duplicate = menu.addAction(colored_icon("copy.svg", size=MENU_ICON_SIZE), "Duplicate")
        delete = menu.addAction(colored_icon("delete.svg", size=MENU_ICON_SIZE), "Delete")
        menu.addSeparator()
        folder = menu.addAction(colored_icon("folder.svg", size=MENU_ICON_SIZE), "Open in Folder")
        chosen = menu.exec(self.songs_list.viewport().mapToGlobal(position))
        path = self.current_playlist_path / item.data(Qt.UserRole)
        if chosen is play:
            self.play_song(item)
        elif chosen is rename:
            name, accepted = QInputDialog.getText(
                self, "Rename Track", "New name:", text=path.stem
            )
            if accepted and name.strip():
                destination = path.with_name(name.strip() + path.suffix)
                old_name = path.name
                path.rename(destination)
                self._move_sidecars(path, destination)
                self._replace_song_in_order(old_name, destination.name)
                if self.current_track_filename == old_name:
                    self.current_track_filename = destination.name
                self.refresh()
        elif chosen is duplicate:
            destination = path.with_name(path.stem + " copy" + path.suffix)
            shutil.copy2(path, destination)
            self._copy_sidecars(path, destination)
            self._insert_song_after(path.name, destination.name)
            self.refresh()
        elif chosen is delete:
            if QMessageBox.question(self, "Delete", f"Delete {path.name}?") == QMessageBox.Yes:
                deleted_name = path.name
                path.unlink(missing_ok=True)
                self._delete_sidecars(path)
                self._remove_song_from_order(deleted_name)
                if self.current_track_filename == deleted_name:
                    self.current_track_filename = None
                    self.current_track_index = -1
                self.refresh()
        elif chosen is folder:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])

    @staticmethod
    def _move_sidecars(source, destination):
        for extension in (".json", ".jpg", ".jpeg", ".png", ".webp"):
            old = source.with_suffix(extension)
            if old.exists():
                old.rename(destination.with_suffix(extension))

    @staticmethod
    def _copy_sidecars(source, destination):
        for extension in (".json", ".jpg", ".jpeg", ".png", ".webp"):
            old = source.with_suffix(extension)
            if old.exists():
                shutil.copy2(old, destination.with_suffix(extension))

    @staticmethod
    def _delete_sidecars(source):
        for extension in (".json", ".jpg", ".jpeg", ".png", ".webp"):
            source.with_suffix(extension).unlink(missing_ok=True)

    def _cover_menu(self, position):
        menu = make_menu(self)
        view = menu.addAction(colored_icon("view.svg", size=MENU_ICON_SIZE), "View Full Size")
        save = menu.addAction(colored_icon("download.svg", size=MENU_ICON_SIZE), "Download Cover")
        has_cover = self.current_cover_pixmap is not None and not self.current_cover_pixmap.isNull()
        view.setEnabled(has_cover)
        save.setEnabled(has_cover)
        chosen = menu.exec(self.cover_label.mapToGlobal(position))
        if chosen is view:
            CoverPreviewDialog(self.current_cover_pixmap, self).exec()
        elif chosen is save:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Cover", "cover.jpg", "Images (*.jpg *.jpeg *.png)"
            )
            if path:
                self.current_cover_pixmap.save(path)
