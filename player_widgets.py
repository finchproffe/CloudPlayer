import os
import sys
import json
import random
import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSlider, QTextEdit, QMenu, QMessageBox, QInputDialog,
    QGraphicsOpacityEffect, QFileDialog, QDialog, QScrollArea,
    QAbstractItemView
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import (
    Qt, QTimer, QUrl, QSize, QPoint, QPropertyAnimation, QEasingCurve, QEvent
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from config import (
    BG_COLOR, PANEL_BG, BUTTON_BG, BUTTON_HOVER, BUTTON_BORDER, ACCENT_COLOR,
    TEXT_COLOR, TEXT_MUTED, AUDIO_EXTENSIONS, PLAYLISTS_PATH
)
from utils import format_time, colored_icon
from threads import TrackMetaFetcher
from dialogs import AddSongDialog
import discord_rpc


class PlayerControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        time_layout = QHBoxLayout()
        self.current_time = QLabel("0:00")
        self.total_time = QLabel("0:00")
        self.progress_bar = QSlider(Qt.Horizontal)

        for label in [self.current_time, self.total_time]:
            label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; font-weight: 500;")

        time_layout.addWidget(self.current_time)
        time_layout.addWidget(self.progress_bar)
        time_layout.addWidget(self.total_time)

        layout.addLayout(time_layout)
        self.progress_bar.sliderMoved.connect(self.on_seek)

    def on_seek(self, position):
        if hasattr(self.parent(), 'seek_position'):
            self.parent().seek_position(position)


class MiniControlBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)
        layout.addStretch()

        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.next_btn = QPushButton()

        prev_icon = colored_icon("prev.svg")
        if not prev_icon.isNull():
            self.prev_btn.setIcon(prev_icon)
        play_icon = colored_icon("play.svg")
        if not play_icon.isNull():
            self.play_btn.setIcon(play_icon)
        next_icon = colored_icon("next.svg")
        if not next_icon.isNull():
            self.next_btn.setIcon(next_icon)

        for btn in (self.prev_btn, self.next_btn):
            btn.setFixedSize(36, 36)
            btn.setIconSize(QSize(16, 16))

        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setIconSize(QSize(18, 18))
        self.play_btn.setStyleSheet(f"background-color: {ACCENT_COLOR}; border: none;")

        layout.addWidget(self.prev_btn)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.next_btn)
        layout.addStretch()

    def set_playing(self, playing: bool):
        filename = "pause.svg" if playing else "play.svg"
        icon = colored_icon(filename)
        if not icon.isNull():
            self.play_btn.setIcon(icon)


class TrackListItemWidget(QWidget):
    ROW_HEIGHT = 72
    COVER_SIZE = 56

    def __init__(self, index, title, artist, cover_pixmap=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("TrackListItemWidget { background: transparent; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(14)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(self.COVER_SIZE, self.COVER_SIZE)
        self.cover_label.setAlignment(Qt.AlignCenter)
        if cover_pixmap is not None and not cover_pixmap.isNull():
            self.cover_label.setStyleSheet(f"background-color: {PANEL_BG}; border-radius: 6px;")
            self.cover_label.setPixmap(cover_pixmap)
        else:
            self.cover_label.setText("♪")
            self.cover_label.setStyleSheet(
                f"background-color: {PANEL_BG}; border-radius: 6px;"
                f" color: {TEXT_MUTED}; font-size: 22px;"
            )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addStretch()

        self.title_label = QLabel(f"{index}. {title}")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.title_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; color: #ffffff; background: transparent;"
        )

        self.artist_label = QLabel(artist)
        self.artist_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.artist_label.setStyleSheet(
            f"font-size: 12px; color: {TEXT_MUTED}; background: transparent;"
        )

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.artist_label)
        text_layout.addStretch()

        layout.addWidget(self.cover_label)
        layout.setAlignment(self.cover_label, Qt.AlignVCenter)
        layout.addLayout(text_layout, stretch=1)
        layout.setAlignment(text_layout, Qt.AlignVCenter)

    def sizeHint(self):
        return QSize(0, self.ROW_HEIGHT)


class CoverPreviewDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cover — Full Size")
        self.resize(720, 720)
        self.setMinimumSize(420, 420)
        self.original_pixmap = pixmap
        self.scale_factor = 1.0
        self._dragging = False
        self._last_pos = QPoint()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet(f"background-color: {BG_COLOR}; border: none;")
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background: transparent;")
        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area)

        hint = QLabel("Scroll to zoom · Drag with left mouse button to pan")
        hint.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 12px; padding: 10px;"
            f" background-color: {BG_COLOR};"
        )
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.setMouseTracking(True)
        self.scroll_area.viewport().setMouseTracking(True)

        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._render_image()

    def showEvent(self, event):
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        self._fade_anim.stop()
        self._fade_anim.start()

    def _render_image(self):
        if self.original_pixmap is None or self.original_pixmap.isNull():
            self.image_label.setText("No cover image")
            return
        new_size = self.original_pixmap.size() * self.scale_factor
        if new_size.width() < 1 or new_size.height() < 1:
            return
        scaled = self.original_pixmap.scaled(
            new_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())

    def eventFilter(self, obj, event):
        if obj is self.scroll_area.viewport():
            etype = event.type()
            if etype == QEvent.Wheel:
                self._handle_wheel(event)
                return True
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._last_pos = event.position().toPoint()
                self.scroll_area.viewport().setCursor(Qt.ClosedHandCursor)
                return True
            elif etype == QEvent.MouseMove and self._dragging:
                delta = event.position().toPoint() - self._last_pos
                self._last_pos = event.position().toPoint()
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
                return True
            elif (etype == QEvent.MouseButtonRelease
                  and event.button() == Qt.LeftButton
                  and self._dragging):
                self._dragging = False
                self.scroll_area.viewport().setCursor(Qt.ArrowCursor)
                return True
        return super().eventFilter(obj, event)

    def _handle_wheel(self, event):
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return
        delta = event.angleDelta().y()
        if delta > 0:
            factor = 1.15
        elif delta < 0:
            factor = 1 / 1.15
        else:
            return
        new_scale = max(0.1, min(self.scale_factor * factor, 8.0))
        if new_scale == self.scale_factor:
            return

        viewport = self.scroll_area.viewport()
        cursor_pos = event.position().toPoint()
        cursor_in_image = self.image_label.mapFrom(viewport, cursor_pos)
        img_w = max(1, self.image_label.width())
        img_h = max(1, self.image_label.height())
        ratio_x = cursor_in_image.x() / img_w
        ratio_y = cursor_in_image.y() / img_h

        self.scale_factor = new_scale
        self._render_image()

        new_img_x = ratio_x * self.image_label.width()
        new_img_y = ratio_y * self.image_label.height()

        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        target_h = int(new_img_x - cursor_pos.x())
        target_v = int(new_img_y - cursor_pos.y())
        target_h = max(h_bar.minimum(), min(target_h, h_bar.maximum()))
        target_v = max(v_bar.minimum(), min(target_v, v_bar.maximum()))
        h_bar.setValue(target_h)
        v_bar.setValue(target_v)


class PlaylistView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.current_track_index = -1
        self.meta_thread = None
        self.is_shuffled = False
        self.hidden_tracks = set()
        self.current_cover_pixmap = None
        self.current_track_filename = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(90, 36)

        self.playlist_name = QLabel("Playlist")
        self.playlist_name.setStyleSheet("font-size: 20px; font-weight: bold;")

        self.now_playing = QLabel("Now Playing — None")
        self.now_playing.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 500;")

        self.volume_btn = QPushButton()
        self.volume_btn.setFixedSize(30, 30)
        self.volume_btn.setFlat(True)
        volume_icon = colored_icon("volume-on.svg")
        if not volume_icon.isNull():
            self.volume_btn.setIcon(volume_icon)
        self.volume_btn.setStyleSheet("border: none; background: transparent;")

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setValue(70)

        header_layout.addWidget(self.back_btn)
        header_layout.addSpacing(15)
        header_layout.addWidget(self.playlist_name)
        header_layout.addStretch()
        header_layout.addWidget(self.now_playing)
        header_layout.addStretch()
        header_layout.addWidget(self.volume_btn)
        header_layout.addWidget(self.volume_slider)
        layout.addLayout(header_layout)

        center_layout = QHBoxLayout()
        center_layout.setSpacing(20)

        self.songs_list = QListWidget()
        self.songs_list.setUniformItemSizes(True)
        self.songs_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.songs_list.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.songs_list.verticalScrollBar().setSingleStep(8)
        self.songs_list.verticalScrollBar().setPageStep(56)
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(self._show_track_context_menu)
        center_layout.addWidget(self.songs_list, stretch=25)

        self.right_sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.right_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(12)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid {BUTTON_BORDER}; border-radius: 4px;")
        self.cover_label.setScaledContents(True)
        self.cover_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cover_label.customContextMenuRequested.connect(self._show_cover_context_menu)

        self.cover_opacity_effect = QGraphicsOpacityEffect(self.cover_label)
        self.cover_opacity_effect.setOpacity(0.0)
        self.cover_label.setGraphicsEffect(self.cover_opacity_effect)
        self.cover_anim = QPropertyAnimation(self.cover_opacity_effect, b"opacity", self)
        self.cover_anim.setDuration(150)
        self.cover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.track_title = QLabel("Track Title")
        self.track_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        self.track_title.setWordWrap(True)

        self.track_artist_prod = QLabel("Artist")
        self.track_artist_prod.setStyleSheet(f"font-size: 13px; color: {TEXT_MUTED};")
        self.track_artist_prod.setWordWrap(True)

        self.lyrics_display = QTextEdit()
        self.lyrics_display.setReadOnly(True)
        self.lyrics_display.setPlaceholderText("Play a track...")

        sidebar_layout.addWidget(self.cover_label, alignment=Qt.AlignCenter)
        sidebar_layout.addWidget(self.track_title)
        sidebar_layout.addWidget(self.track_artist_prod)
        sidebar_layout.addWidget(self.lyrics_display)

        center_layout.addWidget(self.right_sidebar, stretch=10)
        layout.addLayout(center_layout)

        self.player_controls = PlayerControls()
        layout.addWidget(self.player_controls)

        self.mini_bar = MiniControlBar()
        layout.addWidget(self.mini_bar)

        bottom_actions = QHBoxLayout()
        self.shuffle_btn = QPushButton(" Shuffle Mode")
        shuffle_icon = colored_icon("shuffle.svg")
        if not shuffle_icon.isNull():
            self.shuffle_btn.setIcon(shuffle_icon)
        self.shuffle_btn.setFixedHeight(45)

        self.add_song_btn = QPushButton("Add Song")
        self.add_song_btn.setFixedHeight(45)

        bottom_actions.addWidget(self.shuffle_btn)
        bottom_actions.addWidget(self.add_song_btn)
        layout.addLayout(bottom_actions)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self.songs_list.itemDoubleClicked.connect(self.play_song)
        self.add_song_btn.clicked.connect(self.add_song)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)

        self.mini_bar.play_btn.clicked.connect(self.toggle_playback)
        self.mini_bar.next_btn.clicked.connect(self.play_next_track)
        self.mini_bar.prev_btn.clicked.connect(self.play_prev_track)

        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.volume_btn.clicked.connect(self.toggle_mute)

        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        if self.is_shuffled:
            self.shuffle_btn.setStyleSheet(f"background-color: {ACCENT_COLOR}; border: 1px solid #444;")
        else:
            self.shuffle_btn.setStyleSheet("")

    def set_playback_state(self, is_playing: bool):
        self.mini_bar.set_playing(is_playing)

    def update_position(self, pos):
        self.player_controls.current_time.setText(format_time(pos))
        self.player_controls.progress_bar.setValue(pos)

    def update_duration(self, dur):
        self.player_controls.total_time.setText(format_time(dur))
        self.player_controls.progress_bar.setRange(0, dur)

    def _read_track_display_info(self, file_path):
        title = None
        artist = None
        sidecar = file_path.parent / f"{file_path.stem}.json"
        if sidecar.exists():
            try:
                with open(sidecar, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                title = (data.get("title") or "").strip() or None
                artist = (data.get("artist") or "").strip() or None
            except Exception:
                pass

        if not artist or not title:
            stem = file_path.stem
            if " - " in stem:
                parts = stem.split(" - ", 1)
                f_artist, f_title = parts[0].strip(), parts[1].strip()
            elif "-" in stem:
                parts = stem.split("-", 1)
                f_artist, f_title = parts[0].strip(), parts[1].strip()
            else:
                f_artist, f_title = None, stem
            artist = artist or f_artist or "Unknown Artist"
            title = title or f_title or stem
        return title, artist

    def _load_track_cover_thumbnail(self, file_path):
        target_size = TrackListItemWidget.COVER_SIZE
        for ext in ['.jpg', '.png', '.webp']:
            cover_path = file_path.parent / f"{file_path.stem}{ext}"
            if cover_path.exists():
                pm = QPixmap(str(cover_path))
                if not pm.isNull():
                    return pm.scaled(
                        target_size, target_size,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
        return None

    def update_songs_list(self):
        self.songs_list.clear()
        if self.current_playlist_path and self.current_playlist_path.exists():
            files = sorted(self.current_playlist_path.glob("*.*"))
            index = 1
            for f in files:
                if f.suffix.lower() in AUDIO_EXTENSIONS:
                    title, artist = self._read_track_display_info(f)
                    cover_pixmap = self._load_track_cover_thumbnail(f)

                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, f.name)

                    widget = TrackListItemWidget(index, title, artist, cover_pixmap)
                    if f.name in self.hidden_tracks:
                        eff = QGraphicsOpacityEffect(widget)
                        eff.setOpacity(0.35)
                        widget.setGraphicsEffect(eff)

                    item.setSizeHint(QSize(0, TrackListItemWidget.ROW_HEIGHT))
                    self.songs_list.addItem(item)
                    self.songs_list.setItemWidget(item, widget)
                    index += 1

    def seek_position(self, pos):
        self.player.setPosition(pos)

    def toggle_mute(self):
        muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not muted)
        fn = "volume-off.svg" if not muted else "volume-on.svg"
        icon = colored_icon(fn)
        if not icon.isNull():
            self.volume_btn.setIcon(icon)

    def play_song(self, item):
        try:
            idx = self.songs_list.row(item)
            filename = item.data(Qt.UserRole)
            path = self.current_playlist_path / filename

            self.current_track_filename = filename
            self.current_cover_pixmap = None

            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()
            self.set_playback_state(True)

            stem = Path(filename).stem
            self.now_playing.setText(f"Now Playing — {stem}")
            self.current_track_index = idx

            self.track_title.setText(stem)
            self.track_artist_prod.setText("Loading info...")
            self.cover_label.clear()
            self.lyrics_display.setText("Loading lyrics...")

            if self.meta_thread and self.meta_thread.isRunning():
                self.meta_thread.terminate()

            self.meta_thread = TrackMetaFetcher(path)
            self.meta_thread.meta_ready.connect(self.apply_track_metadata)
            self.meta_thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Can't play:\n{e}")

    def apply_track_metadata(self, data):
        self.track_title.setText(data["title"])

        artist_text = data["artist"]
        if data.get("duration"):
            artist_text += f" • {data['duration']}"
        self.track_artist_prod.setText(artist_text)

        self.lyrics_display.setText(data["lyrics"])

        if data["cover_bytes"]:
            px = QPixmap()
            px.loadFromData(data["cover_bytes"])
            self.current_cover_pixmap = px
            self.cover_label.setPixmap(px)
            self.cover_opacity_effect.setOpacity(0.0)
            self.cover_anim.setStartValue(0.0)
            self.cover_anim.setEndValue(1.0)
            self.cover_anim.stop()
            self.cover_anim.start()
        else:
            self.current_cover_pixmap = None
            self.cover_label.setText(" No Cover")
            self.cover_opacity_effect.setOpacity(1.0)

        discord_rpc.update_now_playing(
            title=data.get("title", "Unknown"),
            artist=data.get("artist", "Unknown"),
            cover_url=data.get("cover_url")
        )

    def _menu_stylesheet(self):
        return f"""
            QMenu {{
                background-color: {PANEL_BG};
                border: 1px solid {BUTTON_BORDER};
                border-radius: 4px;
                padding: 4px;
                color: {TEXT_COLOR};
                font-size: 13px;
            }}
            QMenu::item {{
                background-color: transparent;
                border-radius: 3px;
                padding: 7px 22px 7px 14px;
                margin: 1px 2px;
            }}
            QMenu::item:selected {{
                background-color: {ACCENT_COLOR};
                color: #ffffff;
            }}
            QMenu::item:disabled {{
                color: {TEXT_MUTED};
            }}
            QMenu::separator {{
                height: 1px;
                background: {BUTTON_BORDER};
                margin: 4px 8px;
            }}
        """

    def _show_track_context_menu(self, pos: QPoint):
        """Track list context menu (right-click).
        Styled to match the player's strict dark theme.
        """
        item = self.songs_list.itemAt(pos)
        if not item:
            return

        self.songs_list.setCurrentItem(item)
        filename = item.data(Qt.UserRole)

        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        act_play = menu.addAction("Play")
        menu.addSeparator()
        act_rename = menu.addAction("Rename")
        act_duplicate = menu.addAction("Duplicate")
        act_delete = menu.addAction("Delete")
        menu.addSeparator()
        act_hide = menu.addAction(
            "Unhide from Queue" if filename in self.hidden_tracks else "Hide from Queue"
        )
        act_open_folder = menu.addAction("Open in Folder")

        play_icon = colored_icon("play.svg", size=32)
        rename_icon = colored_icon("rename.svg", size=32)
        dup_icon = colored_icon("copy.svg", size=32)
        del_icon = colored_icon("delete.svg", size=32)
        hide_icon = colored_icon("hide.svg", size=32)
        folder_icon = colored_icon("folder.svg", size=32)
        if not play_icon.isNull():
            act_play.setIcon(play_icon)
        if not rename_icon.isNull():
            act_rename.setIcon(rename_icon)
        if not dup_icon.isNull():
            act_duplicate.setIcon(dup_icon)
        if not del_icon.isNull():
            act_delete.setIcon(del_icon)
        if not hide_icon.isNull():
            act_hide.setIcon(hide_icon)
        if not folder_icon.isNull():
            act_open_folder.setIcon(folder_icon)

        chosen = menu.exec(self.songs_list.viewport().mapToGlobal(pos))
        if chosen is act_play:
            self.play_song(item)
        elif chosen is act_rename:
            self.rename_current_track()
        elif chosen is act_duplicate:
            self.duplicate_current_track()
        elif chosen is act_delete:
            self.delete_current_track()
        elif chosen is act_hide:
            self.toggle_hide_track(item)
        elif chosen is act_open_folder:
            self.open_track_in_folder(item)

    def _show_cover_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        act_view = menu.addAction("View Full Size")
        act_download = menu.addAction("Download Cover")

        view_icon = colored_icon("view.svg", size=32)
        download_icon = colored_icon("download.svg", size=32)
        if not view_icon.isNull():
            act_view.setIcon(view_icon)
        if not download_icon.isNull():
            act_download.setIcon(download_icon)

        has_cover = self.current_cover_pixmap is not None and not self.current_cover_pixmap.isNull()
        act_view.setEnabled(has_cover)
        act_download.setEnabled(has_cover)

        chosen = menu.exec(self.cover_label.mapToGlobal(pos))
        if chosen is act_view:
            self._view_cover_full_size()
        elif chosen is act_download:
            self._download_current_cover()

    def _view_cover_full_size(self):
        if not self.current_cover_pixmap or self.current_cover_pixmap.isNull():
            return
        dlg = CoverPreviewDialog(self.current_cover_pixmap, self)
        dlg.exec()

    def _download_current_cover(self):
        if not self.current_cover_pixmap or self.current_cover_pixmap.isNull():
            QMessageBox.information(self, "No Cover", "There is no cover image to save.")
            return
        default_name = "cover.png"
        if self.current_track_filename:
            default_name = Path(self.current_track_filename).stem + ".png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Cover As", default_name, "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            if not self.current_cover_pixmap.save(path):
                QMessageBox.critical(self, "Error", "Failed to save the cover image.")

    def toggle_hide_track(self, item):
        filename = item.data(Qt.UserRole)
        if filename in self.hidden_tracks:
            self.hidden_tracks.discard(filename)
            now_hidden = False
        else:
            self.hidden_tracks.add(filename)
            now_hidden = True

        widget = self.songs_list.itemWidget(item)
        if widget is not None:
            if now_hidden:
                eff = QGraphicsOpacityEffect(widget)
                eff.setOpacity(0.35)
                widget.setGraphicsEffect(eff)
            else:
                widget.setGraphicsEffect(None)

    def open_track_in_folder(self, item):
        filename = item.data(Qt.UserRole)
        path = self.current_playlist_path / filename
        if not path.exists():
            QMessageBox.warning(self, "Not Found", "The file could not be found on disk.")
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Can't open folder:\n{e}")

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.set_playback_state(False)
            discord_rpc.update_paused()
        else:
            item = self.songs_list.currentItem()
            if item:
                if not self.player.source().isValid():
                    self.play_song(item)
                else:
                    self.player.play()
                    self.set_playback_state(True)
                    discord_rpc.update_now_playing(self.track_title.text(), self.track_artist_prod.text().split(' • ')[0], None)
            elif self.songs_list.count() > 0:
                self.songs_list.setCurrentRow(0)
                self.play_song(self.songs_list.item(0))

    def _step_track_index(self, start, count, direction):
        if count == 0:
            return None
        idx = start
        for _ in range(count):
            idx = (idx + direction) % count
            if self.songs_list.item(idx).data(Qt.UserRole) not in self.hidden_tracks:
                return idx
        return None

    def play_next_track(self):
        count = self.songs_list.count()
        if count == 0:
            return
        if self.is_shuffled:
            candidates = [
                i for i in range(count)
                if self.songs_list.item(i).data(Qt.UserRole) not in self.hidden_tracks
            ]
            if not candidates:
                return
            next_idx = random.choice(candidates)
        else:
            next_idx = self._step_track_index(self.current_track_index, count, +1)
            if next_idx is None:
                return

        item = self.songs_list.item(next_idx)
        self.songs_list.setCurrentItem(item)
        self.play_song(item)

    def play_prev_track(self):
        count = self.songs_list.count()
        if count == 0:
            return
        prev_idx = self._step_track_index(self.current_track_index, count, -1)
        if prev_idx is None:
            return
        item = self.songs_list.item(prev_idx)
        self.songs_list.setCurrentItem(item)
        self.play_song(item)

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next_track()

    def duplicate_current_track(self):
        item = self.songs_list.currentItem()
        if not item: return
        name = item.data(Qt.UserRole)
        src = self.current_playlist_path / name
        base, ext = os.path.splitext(name)
        counter = 1
        while True:
            new_name = f"{base} ({counter}){ext}"
            if not (self.current_playlist_path / new_name).exists(): break
            counter += 1
        shutil.copy2(src, self.current_playlist_path / new_name)

        for extra_ext in ['.json', '.jpg', '.png', '.webp']:
            extra_src = self.current_playlist_path / f"{base}{extra_ext}"
            if extra_src.exists():
                shutil.copy2(extra_src, self.current_playlist_path / f"{base} ({counter}){extra_ext}")

        self.update_songs_list()
        self.save_playlist()

    def delete_current_track(self):
        item = self.songs_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        path = self.current_playlist_path / name

        is_playing_this = self.player.source().toLocalFile() == str(path)

        def try_remove(attempt=0):
            try:
                if path.exists():
                    os.remove(path)
                base = path.stem
                for extra_ext in ['.json', '.jpg', '.png', '.webp']:
                    extra_file = path.parent / f"{base}{extra_ext}"
                    if extra_file.exists():
                        os.remove(extra_file)

                row = self.songs_list.row(item)
                if row >= 0:
                    self.songs_list.takeItem(row)
                self.save_playlist()
                self.update_songs_list()
            except PermissionError:
                if attempt < 10:
                    QTimer.singleShot(200, lambda: try_remove(attempt + 1))
                else:
                    QMessageBox.critical(
                        self, "Error",
                        "Can't delete:\nThe file is still in use by another process. Please try again."
                    )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Can't delete:\n{e}")

        if is_playing_this:
            self.player.stop()
            self.player.setSource(QUrl())
            self.mini_bar.set_playing(False)
            self.now_playing.setText("Now Playing — None")
            self.current_track_index = -1
            self.current_track_filename = None
            self.current_cover_pixmap = None
            self.lyrics_display.clear()
            self.cover_label.clear()
            QTimer.singleShot(250, try_remove)
        else:
            try_remove()

    def rename_current_track(self):
        item = self.songs_list.currentItem()
        if not item: return
        old_name = item.data(Qt.UserRole)
        old_path = self.current_playlist_path / old_name
        old_stem = Path(old_name).stem
        ext = Path(old_name).suffix

        new_stem, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_stem)
        if ok and new_stem.strip():
            new_name = new_stem.strip() + ext
            try:
                is_current = self.player.source().toLocalFile() == str(old_path)
                if is_current:
                    self.player.stop()
                    self.player.setSource(QUrl())
                os.rename(old_path, self.current_playlist_path / new_name)

                for extra_ext in ['.json', '.jpg', '.png', '.webp']:
                    old_extra = self.current_playlist_path / f"{old_stem}{extra_ext}"
                    if old_extra.exists():
                        os.rename(old_extra, self.current_playlist_path / f"{new_stem.strip()}{extra_ext}")

                self.save_playlist()
                self.update_songs_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Can't rename:\n{e}")

    def add_song(self):
        if not self.current_playlist: return
        dlg = AddSongDialog(self, self.current_playlist)
        if dlg.exec():
            self.update_songs_list()
            self.save_playlist()

    def save_playlist(self):
        if not self.current_playlist: return
        songs = [self.songs_list.item(i).data(Qt.UserRole) for i in range(self.songs_list.count())]
        data = {'name': self.current_playlist, 'songs': songs}
        with open(PLAYLISTS_PATH / f"{self.current_playlist}.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = PLAYLISTS_PATH / name / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(name)
        self.hidden_tracks = set()
        self.update_songs_list()