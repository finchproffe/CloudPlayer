import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSlider, QTextEdit, QVBoxLayout, QWidget,
)

from config import ACCENT_COLOR, AUDIO_EXTENSIONS, BG_COLOR, BUTTON_BORDER, PANEL_BG, PLAYLISTS_PATH, TEXT_COLOR, TEXT_MUTED
from dialogs import AddSongDialog
from threads import TrackMetaFetcher
from utils import colored_icon, format_time, rounded_cover_pixmap
import discord_rpc

MENU_STYLE = f"""
QMenu {{
    background-color: {PANEL_BG};
    color: {TEXT_COLOR};
    border: 1px solid {BUTTON_BORDER};
    border-radius: 5px;
    padding: 10px;
    font-weight: 700;
}}
QMenu::item {{
    background-color: transparent;
    padding: 12px 38px 12px 24px;
    margin: 3px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background-color: {ACCENT_COLOR}; color: #ffffff; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; margin: 8px 12px; background: {BUTTON_BORDER}; }}
"""


def make_menu(parent):
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_STYLE)
    return menu


class TrackListItemWidget(QWidget):
    ROW_HEIGHT = 75
    COVER_SIZE = 64

    def __init__(self, index, title, artist, cover_pixmap=None, parent=None):
        super().__init__(parent)
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
            cover.setStyleSheet(f"background:{PANEL_BG};border-radius:6px;color:{TEXT_MUTED};font-size:22px")
        labels = QVBoxLayout()
        labels.setSpacing(2)
        labels.addStretch()
        title_label = QLabel(f"{index}. {title}")
        title_label.setStyleSheet(f"background:transparent;font-size:14px;font-weight:700;color:{TEXT_COLOR}")
        artist_label = QLabel(artist)
        artist_label.setStyleSheet(f"background:transparent;font-size:11px;color:{TEXT_MUTED}")
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
        self.scroll = QScrollArea()
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
                self.scroll.horizontalScrollBar().setValue(self.scroll.horizontalScrollBar().value() - delta.x())
                self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().value() - delta.y())
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
        self.songs_list = QListWidget()
        self.songs_list.setUniformItemSizes(True)
        self.songs_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(self._track_menu)
        self.songs_list.itemDoubleClicked.connect(self.play_song)
        center.addWidget(self.songs_list, 25)
        sidebar = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setScaledContents(True)
        self.cover_label.setStyleSheet(f"background:{PANEL_BG};border:1px solid {BUTTON_BORDER};border-radius:4px")
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
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        self.volume_btn.clicked.connect(self.toggle_mute)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.playbackStateChanged.connect(
            lambda state: self.play_btn.setIcon(colored_icon("pause.svg" if state == QMediaPlayer.PlayingState else "play.svg"))
        )

    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = PLAYLISTS_PATH / name / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(name)
        self.refresh()

    def refresh(self):
        self.songs_list.clear()
        if not self.current_playlist_path:
            return
        files = [file for file in sorted(self.current_playlist_path.iterdir()) if file.suffix.lower() in AUDIO_EXTENSIONS]
        for number, file in enumerate(files, 1):
            title, artist, _ = self._metadata(file)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, file.name)
            item.setSizeHint(QSize(0, TrackListItemWidget.ROW_HEIGHT))
            self.songs_list.addItem(item)
            self.songs_list.setItemWidget(item, TrackListItemWidget(number, title, artist, self._cover(file)))

    update_songs_list = refresh

    def _metadata(self, file):
        data = {}
        try:
            sidecar = file.with_suffix(".json")
            if sidecar.exists():
                data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
        title = data.get("title")
        artist = data.get("artist")
        if (not title or not artist) and " - " in file.stem:
            parsed_artist, parsed_title = file.stem.split(" - ", 1)
            title = title or parsed_title
            artist = artist or parsed_artist
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
        self.play_file(self.current_playlist_path / item.data(Qt.UserRole), item.data(Qt.UserRole), self.songs_list.row(item))

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
        discord_rpc.update_now_playing(data.get("title", "Unknown"), data.get("artist", "Unknown"), data.get("cover_url"))

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
        self.repeat_btn.setIcon(colored_icon("repeat.svg" if self.repeat_track else "repeat-off.svg"))

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        self.shuffle_btn.setStyleSheet(f"background:{ACCENT_COLOR}" if self.is_shuffled else "")

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
        self.volume_btn.setIcon(colored_icon("volume-off.svg" if self.audio_output.isMuted() else "volume-on.svg"))

    def add_song(self):
        if self.current_playlist and AddSongDialog(self, self.current_playlist).exec():
            self.refresh()

    def _track_menu(self, position):
        item = self.songs_list.itemAt(position)
        if not item:
            return
        self.songs_list.setCurrentItem(item)
        menu = make_menu(self)
        play = menu.addAction(colored_icon("play.svg", size=32), "Play")
        menu.addSeparator()
        rename = menu.addAction(colored_icon("rename.svg", size=32), "Rename")
        duplicate = menu.addAction(colored_icon("copy.svg", size=32), "Duplicate")
        delete = menu.addAction(colored_icon("delete.svg", size=32), "Delete")
        menu.addSeparator()
        folder = menu.addAction(colored_icon("folder.svg", size=32), "Open in Folder")
        chosen = menu.exec(self.songs_list.viewport().mapToGlobal(position))
        path = self.current_playlist_path / item.data(Qt.UserRole)
        if chosen is play:
            self.play_song(item)
        elif chosen is rename:
            name, accepted = QInputDialog.getText(self, "Rename Track", "New name:", text=path.stem)
            if accepted and name.strip():
                destination = path.with_name(name.strip() + path.suffix)
                path.rename(destination)
                self._move_sidecars(path, destination)
                self.refresh()
        elif chosen is duplicate:
            destination = path.with_name(path.stem + " copy" + path.suffix)
            shutil.copy2(path, destination)
            self._copy_sidecars(path, destination)
            self.refresh()
        elif chosen is delete:
            if QMessageBox.question(self, "Delete", f"Delete {path.name}?") == QMessageBox.Yes:
                path.unlink(missing_ok=True)
                self._delete_sidecars(path)
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
        view = menu.addAction(colored_icon("view.svg", size=32), "View Full Size")
        save = menu.addAction(colored_icon("download.svg", size=32), "Download Cover")
        has_cover = self.current_cover_pixmap is not None and not self.current_cover_pixmap.isNull()
        view.setEnabled(has_cover)
        save.setEnabled(has_cover)
        chosen = menu.exec(self.cover_label.mapToGlobal(position))
        if chosen is view:
            CoverPreviewDialog(self.current_cover_pixmap, self).exec()
        elif chosen is save:
            path, _ = QFileDialog.getSaveFileName(self, "Save Cover", "cover.jpg", "Images (*.jpg *.jpeg *.png)")
            if path:
                self.current_cover_pixmap.save(path)
