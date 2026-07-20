import random
from pathlib import Path

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QSize, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from config import (
    ACCENT_COLOR, BG_COLOR, BUTTON_BG, BUTTON_BORDER, BUTTON_HOVER, PANEL_BG,
    PLAYLISTS_PATH, TEXT_COLOR, TEXT_MUTED, SAVED_VOLUME, save_volume,
)
from dropdown_ui import QDialog, QInputDialog
from threads import TrackMetaFetcher
from utils import colored_icon, format_time, rounded_cover_pixmap
import discord_rpc
from playlist_components import (
    BoundedSongList, BufferedPositionSlider, CoverPreviewDialog,
    DirectJumpSlider,
)
from playlist_storage import PlaylistStorageMixin
from playlist_actions import PlaylistActionsMixin

MENU_ICON_SIZE = 28
MENU_TEXT_SIZE = 14
MENU_STYLE = f"""
QMenu {{
 background-color:{PANEL_BG};color:{TEXT_COLOR};
 border:1px solid {BUTTON_BORDER};border-radius:4px;
 padding:4px;font-size:{MENU_TEXT_SIZE}px;font-weight:700;
}}
QMenu::item {{
 background-color:transparent;padding:3px 10px 3px 8px;
 margin:0;border-radius:3px;min-height:18px;
}}
QMenu::item:selected {{ background-color:{ACCENT_COLOR};color:#ffffff; }}
QMenu::item:disabled {{ color:{TEXT_MUTED}; }}
QMenu::separator {{ height:1px;margin:4px 6px;background:{BUTTON_BORDER}; }}
QMenu::icon {{ width:{MENU_ICON_SIZE}px;height:{MENU_ICON_SIZE}px; }}
"""


def make_menu(_parent=None):


    menu = QMenu()
    menu.setStyleSheet(MENU_STYLE)
    return menu


class QueueListWidget(QListWidget):
    reordered = Signal(list)

    def dropEvent(self, event):
        super().dropEvent(event)
        filenames = [
            self.item(row).data(Qt.UserRole)
            for row in range(self.count())
            if self.item(row).data(Qt.UserRole)
        ]
        QTimer.singleShot(
            0,
            lambda order=filenames: self.reordered.emit(order),
        )


class QueueDialog(QDialog):
    queue_reordered = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Up Next")
        self.setFixedWidth(480)
        self.setMinimumHeight(390)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 22)
        root.setSpacing(12)
        title = QLabel("Up Next")
        title.setStyleSheet("font-size:23px;font-weight:700")
        self.mode = QLabel("Normal Order")
        self.mode.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:12px;font-weight:700"
        )
        self.tracks = QueueListWidget()
        self.tracks.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tracks.setDragDropMode(QAbstractItemView.InternalMove)
        self.tracks.setDefaultDropAction(Qt.MoveAction)
        self.tracks.setDragDropOverwriteMode(False)
        self.tracks.setDropIndicatorShown(True)
        self.tracks.setSpacing(4)
        self.tracks.reordered.connect(self.queue_reordered.emit)
        root.addWidget(title)
        root.addWidget(self.mode)
        root.addWidget(self.tracks, 1)
        self.setStyleSheet(
            f"QDialog{{background:{BG_COLOR};color:{TEXT_COLOR}}}"
            f"QListWidget{{background:{PANEL_BG};color:{TEXT_COLOR};border:1px solid "
            f"{BUTTON_BORDER};border-radius:8px;padding:7px;outline:0}}"
            f"QListWidget::item{{background:{BUTTON_BG};border:1px solid {BUTTON_BORDER};"
            "border-radius:6px;padding:10px 12px;margin:2px}}"
            f"QListWidget::item:hover{{background:{BUTTON_HOVER};border-color:{ACCENT_COLOR}}}"
        )

    def set_queue(self, mode, rows, draggable=True):
        self.mode.setText(mode)
        self.tracks.clear()
        self.tracks.setDragEnabled(draggable)
        self.tracks.setAcceptDrops(draggable)
        self.tracks.setDragDropMode(
            QAbstractItemView.InternalMove
            if draggable
            else QAbstractItemView.NoDragDrop
        )
        self.tracks.setCursor(
            Qt.OpenHandCursor if draggable else Qt.ArrowCursor
        )
        if not rows:
            item = QListWidgetItem("No upcoming tracks")
            item.setForeground(Qt.gray)
            self.tracks.addItem(item)
            return
        for index, row in enumerate(rows, 1):
            title = str(row.get("title") or "Unknown Track")
            artist = str(row.get("artist") or "Unknown Artist")
            item = QListWidgetItem(f"{index}.  {title}\n     {artist}")
            item.setData(Qt.UserRole, row.get("filename"))
            self.tracks.addItem(item)


class AnimatedRepeatButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._slash_progress = 1.0
        self.setIcon(colored_icon("repeat.svg"))
        self.animation = QPropertyAnimation(self, b"slashProgress", self)
        self.animation.setDuration(380)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)

    def _get_slash_progress(self):
        return self._slash_progress

    def _set_slash_progress(self, value):
        self._slash_progress = max(0.0, min(1.0, float(value)))
        self.update()

    slashProgress = Property(
        float,
        _get_slash_progress,
        _set_slash_progress,
    )

    def set_repeat_enabled(self, enabled):
        self.animation.stop()
        self.animation.setStartValue(self._slash_progress)
        self.animation.setEndValue(0.0 if enabled else 1.0)
        self.animation.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._slash_progress <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setOpacity(min(1.0, self._slash_progress * 1.35))
        pen = QPen(QColor(TEXT_COLOR), 2.25)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        center = self.rect().center()
        span = 11.0 * self._slash_progress
        painter.drawLine(
            round(center.x() - span),
            round(center.y() - span),
            round(center.x() + span),
            round(center.y() + span),
        )
        painter.end()


class PlaylistView(PlaylistStorageMixin, PlaylistActionsMixin, QWidget):
    back_requested = Signal()
    sync_requested = Signal(str, int)
    playlist_updated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.current_track_index = -1
        self.current_cover_pixmap = None
        self.current_track_filename = None
        self.current_track_path = None
        self.playing_playlist = None
        self.playing_playlist_path = None
        self._playback_order = []
        self._playback_row_by_filename = {}
        self._current_metadata = {}
        self.is_shuffled = False
        self.repeat_track = False
        self._shuffle_upcoming = []
        self._shuffle_anchor = None
        self._queue_dialog = None
        self.meta_thread = None
        self._metadata_generation = 0
        self._order_undo_stack = []
        self._network_manager = None
        self._prepared_paths = {}
        self._active_room_request = None
        self._ensure_storage_state()
        self._build()

    def set_network_manager(self, manager):
        self._network_manager = manager
        manager.track_prepare_received.connect(self.prepare_remote_track)
        manager.repeat_received.connect(self.apply_remote_repeat)
        manager.sync_received.connect(self._remote_control_applied)
        manager.disconnected.connect(self._room_disconnected)
        progress_signal = getattr(
            manager, "stream_buffer_progress_changed", None
        )
        if progress_signal is not None:
            progress_signal.connect(self.set_stream_buffer_progress)

    def _room_connected(self):
        return (
            self._network_manager is not None
            and self._network_manager.is_connected
        )

    def _room_disconnected(self):
        self._prepared_paths.clear()
        self._active_room_request = None
        self.clear_stream_buffer_progress()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(110, 42)
        self.back_btn.clicked.connect(self.leave_playlist)
        self.playlist_name = QLabel("Playlist")
        self.playlist_name.setStyleSheet("font-size:24px;font-weight:700")
        self.now_playing = QLabel("Now Playing: None")
        self.now_playing.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:15px"
        )
        self.volume_btn = QPushButton()
        self.volume_btn.setIcon(
            colored_icon(
                "volume-on.svg" if SAVED_VOLUME > 0 else "volume-off.svg"
            )
        )
        self.volume_btn.setFixedSize(34, 34)
        self.volume_btn.setFlat(True)
        self.volume_slider = DirectJumpSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(SAVED_VOLUME)
        self.volume_percent = QPushButton(f"{SAVED_VOLUME}%")
        self.volume_percent.setFixedSize(52, 32)
        self.volume_percent.setToolTip("Set an exact volume")
        self.volume_percent.setStyleSheet(
            f"background:{PANEL_BG};color:{TEXT_COLOR};"
            f"border:1px solid {BUTTON_BORDER};border-radius:5px;"
            "padding:0;font-size:12px;font-weight:700"
        )
        header.addWidget(self.back_btn)
        header.addSpacing(15)
        header.addWidget(self.playlist_name)
        header.addStretch()
        header.addWidget(self.now_playing)
        header.addStretch()
        header.addWidget(self.volume_btn)
        header.addWidget(self.volume_slider)
        header.addWidget(self.volume_percent)
        root.addLayout(header)

        center = QHBoxLayout()
        self.songs_list = BoundedSongList()
        self.install_track_delegate()
        self.songs_list.setUniformItemSizes(True)
        self.songs_list.setVerticalScrollMode(
            QAbstractItemView.ScrollPerPixel
        )
        self.songs_list.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.songs_list.setDragEnabled(True)
        self.songs_list.setAcceptDrops(True)
        self.songs_list.viewport().setAcceptDrops(True)
        self.songs_list.setDropIndicatorShown(False)
        self.songs_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.songs_list.setDefaultDropAction(Qt.MoveAction)
        self.songs_list.setDragDropOverwriteMode(False)
        self.songs_list.reorder_finished.connect(self._songs_reordered)
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(
            self._track_menu
        )
        self.songs_list.itemDoubleClicked.connect(self.play_song)
        center.addWidget(self.songs_list, 25)

        sidebar = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setScaledContents(False)
        self.cover_label.setStyleSheet(
            f"background:{PANEL_BG};border:1px solid {BUTTON_BORDER};"
            "border-radius:4px"
        )
        self.cover_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cover_label.customContextMenuRequested.connect(
            self._cover_menu
        )
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
        self.position = BufferedPositionSlider()
        self.total_time = QLabel("0:00")
        self.position.value_committed.connect(self.seek_to_position)
        self.position.sliderMoved.connect(
            lambda value: self.current_time.setText(format_time(value))
        )
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
        self.repeat_btn = AnimatedRepeatButton()
        self.queue_btn = QPushButton()
        self.queue_btn.setIcon(colored_icon("queue.svg"))
        self.queue_btn.setToolTip("Show next 5 tracks")
        self.queue_btn.setAccessibleName("Playback queue")
        for button in (
            self.queue_btn,
            self.prev_btn,
            self.play_btn,
            self.next_btn,
            self.repeat_btn,
        ):
            button.setFixedSize(40, 40)
            button.setIconSize(QSize(24, 24))
            controls.addWidget(button)
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
        self.audio_output.setVolume(SAVED_VOLUME / 100)
        self._volume_save_timer = QTimer(self)
        self._volume_save_timer.setSingleShot(True)
        self._volume_save_timer.setInterval(300)
        self._volume_save_timer.timeout.connect(self.persist_volume)
        self.prev_btn.clicked.connect(self.play_prev_track)
        self.play_btn.clicked.connect(self.toggle_playback)
        self.next_btn.clicked.connect(self.play_next_track)
        self.repeat_btn.clicked.connect(self.toggle_repeat)
        self.queue_btn.clicked.connect(self.show_queue)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        add_song.clicked.connect(self.add_song)
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.volume_slider.sliderReleased.connect(self.persist_volume)
        self.volume_percent.clicked.connect(self._set_custom_volume)
        self.volume_btn.clicked.connect(self.toggle_mute)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.playbackStateChanged.connect(
            lambda state: self.play_btn.setIcon(
                colored_icon(
                    "pause.svg"
                    if state == QMediaPlayer.PlayingState
                    else "play.svg"
                )
            )
        )
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo_song_reorder)

    def _set_volume(self, value):
        self.audio_output.setVolume(value / 100)
        self.volume_percent.setText(f"{value}%")
        self._volume_save_timer.start()
        if self.audio_output.isMuted() and value > 0:
            self.audio_output.setMuted(False)
            self.volume_btn.setIcon(colored_icon("volume-on.svg"))

    def persist_volume(self):
        if hasattr(self, "_volume_save_timer"):
            self._volume_save_timer.stop()
        save_volume(self.volume_slider.value())

    def _set_custom_volume(self):
        value, accepted = QInputDialog.getInt(
            self,
            "Set Volume",
            "Volume percentage:",
            self.volume_slider.value(),
            0,
            100,
            1,
        )
        if accepted:
            self.volume_slider.setValue(value)

    def _show_idle_display(self, reset_timeline=False):
        self.now_playing.setText("Now Playing: None")
        self.track_title.setText("Track Title")
        self.track_artist_prod.setText("Artist")
        self.lyrics_display.clear()
        self.current_cover_pixmap = None
        self.cover_label.clear()
        if reset_timeline:
            self.current_time.setText("0:00")
            self.total_time.setText("0:00")
            self.position.setRange(0, 0)
            self.position.setValue(0)
            self.clear_stream_buffer_progress()

    def _restore_current_display(self):
        if not self._current_metadata:
            return
        title = self._current_metadata.get("title") or "Unknown Track"
        artist = self._current_metadata.get("artist") or "Unknown Artist"
        self.now_playing.setText(f"Now Playing: {title} • {artist}")
        self.apply_metadata(self._current_metadata)

    def _remote_control_applied(self, action, _position):
        if action == "pause":
            discord_rpc.update_paused()
        elif action == "play":
            self._restore_current_display()

    def leave_playlist(self):
        self.cancel_playlist_loading()
        self.current_playlist = None
        self.current_playlist_path = None
        self._playlist_order = []
        self._row_by_filename = {}
        self.playlist_name.setText("Playlist")
        self.songs_list.clear()
        self._order_undo_stack.clear()
        self.back_requested.emit()

    def reset_current_track(self):
        self._metadata_generation += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.current_track_index = -1
        self.current_track_filename = None
        self.current_track_path = None
        self.playing_playlist = None
        self.playing_playlist_path = None
        self._playback_order = []
        self._playback_row_by_filename = {}
        self._current_metadata = {}
        self._active_room_request = None
        self._reset_shuffle_queue()
        self._show_idle_display(True)
        discord_rpc.clear_activity()

    def release_track(self, path):
        path = Path(path)
        matches_path = False
        if self.current_track_path is not None:
            try:
                matches_path = (
                    Path(self.current_track_path).resolve() == path.resolve()
                )
            except OSError:
                matches_path = Path(self.current_track_path) == path
        if matches_path:
            self.reset_current_track()
            return True
        return False

    def release_playlist(self, name):
        self.forget_playlist(name)
        folder = PLAYLISTS_PATH / str(name) / "songs"
        if self.current_track_path is not None:
            try:
                if Path(self.current_track_path).resolve().parent == folder.resolve():
                    self.reset_current_track()
            except OSError:
                pass
        if self.current_playlist != name:
            return
        self.cancel_playlist_loading()
        self.current_playlist = None
        self.current_playlist_path = None
        self._playlist_order = []
        self._row_by_filename = {}
        self.playlist_name.setText("Playlist")
        self.songs_list.clear()
        self._order_undo_stack.clear()

    def _activate_playback_context(self, path, filename, index, preserve_queue):
        path = Path(path)
        current_path = Path(self.current_playlist_path) if self.current_playlist_path else None
        playback_path = Path(self.playing_playlist_path) if self.playing_playlist_path else None
        same_playback = playback_path is not None and path.parent == playback_path
        same_playlist = current_path is not None and path.parent == current_path
        if same_playback:
            playlist = self.playing_playlist
            order = list(self._playback_order)
        elif same_playlist:
            playlist = self.current_playlist
            order = list(self._playlist_order)
        else:
            playlist = path.parent.parent.name if path.parent.name == "songs" else None
            order = []
        if not order:
            order = [filename]
        elif filename not in order:
            insert_at = max(0, min(int(index), len(order))) if index >= 0 else len(order)
            order.insert(insert_at, filename)
        self.playing_playlist = playlist
        self.playing_playlist_path = path.parent
        self._playback_order = order
        self._playback_row_by_filename = {
            name: row for row, name in enumerate(order)
        }
        self.current_track_index = self._playback_row_by_filename.get(filename, 0)
        if preserve_queue:
            self._shuffle_anchor = filename
            self._refresh_queue_dialog()
        else:
            self._reset_shuffle_queue()

    def _playback_order_changed(self):
        if self.playing_playlist != self.current_playlist:
            return
        self._playback_order = list(self._playlist_order)
        self._playback_row_by_filename = {
            filename: row
            for row, filename in enumerate(self._playback_order)
        }
        self.current_track_index = self._playback_row_by_filename.get(
            self.current_track_filename, -1
        )

    def play_file(
        self,
        file,
        filename=None,
        index=-1,
        broadcast=True,
        autoplay=True,
        preserve_queue=False,
    ):
        path = Path(file)
        if broadcast and self._room_connected():
            queue = self._room_queue()
            if not queue:
                return
            selected = index
            if selected < 0:
                selected = next(
                    (
                        i
                        for i, row in enumerate(queue)
                        if row.get("filename") == path.name
                    ),
                    0,
                )
            self._network_manager.select_track(
                self._track_descriptor(path), queue, selected
            )
            self.now_playing.setText(
                "Waiting for everyone to download the track..."
            )
            return

        title, artist, _ = self._metadata(path)
        self.current_track_filename = filename or path.name
        self._activate_playback_context(
            path,
            self.current_track_filename,
            index,
            preserve_queue,
        )
        self.current_track_path = path.resolve()
        self._current_metadata = {
            "title": title,
            "artist": artist,
            "lyrics": "Loading lyrics...",
        }
        self.clear_stream_buffer_progress()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if autoplay:
            self.player.play()
        else:
            self.player.pause()
        self.now_playing.setText(f"Now Playing: {title} • {artist}")
        self.track_title.setText(title)
        self.track_artist_prod.setText(artist)
        self.lyrics_display.setText("Loading lyrics...")
        self._metadata_generation += 1
        metadata_generation = self._metadata_generation
        self.meta_thread = TrackMetaFetcher(path, self)
        self.meta_thread.meta_ready.connect(
            lambda data, generation=metadata_generation: (
                self.apply_metadata(data)
                if generation == self._metadata_generation
                else None
            )
        )
        self.meta_thread.start()
        if broadcast and not self._room_connected():
            self.sync_requested.emit("play", 0)
        self._refresh_queue_dialog()

    def _find_local_track(self, track):
        filename = str(track.get("filename") or "")
        preferred = (
            PLAYLISTS_PATH
            / str(track.get("playlist") or "Listen Together")
            / "songs"
            / filename
        )
        if preferred.is_file():
            return preferred
        for candidate in PLAYLISTS_PATH.glob(f"*/songs/{filename}"):
            if candidate.is_file():
                return candidate
        return None

    def prepare_remote_track(self, packet):
        request_id = str(packet.get("request_id") or "")
        track = packet.get("track") or {}
        self._metadata_generation += 1
        stream_url = self._network_manager.stream_url(track)
        local = None if stream_url else self._find_local_track(track)
        self.player.pause()
        if stream_url or local:
            source = (
                QUrl(stream_url)
                if stream_url
                else QUrl.fromLocalFile(str(local))
            )
            self.player.setSource(source)
            metadata = self._network_manager.track_metadata(track)
            self._prepared_paths[request_id] = {
                "source": source,
                "path": local,
                "track": dict(track),
                "metadata": metadata,
            }
            title = metadata.get("title") or track.get("title") or "Unknown"
            artist = metadata.get("artist") or track.get("artist") or "Unknown"
            self.now_playing.setText(f"Now Playing: {title} • {artist}")
            self.apply_metadata(metadata)
            self._network_manager.track_ready(request_id, True)
        else:
            self._network_manager.track_ready(
                request_id, False, "Track file was not received"
            )

    def commit_remote_track(self, packet):
        request_id = str(packet.get("request_id") or "")
        prepared = self._prepared_paths.pop(request_id, None)
        if not prepared:
            return
        self._prepared_paths.clear()
        self._active_room_request = request_id
        track = packet.get("track") or {}
        self.current_track_filename = str(
            track.get("filename") or "room-stream"
        )
        self.current_track_path = prepared.get("path")
        self.current_track_index = int(packet.get("queue_index", -1))
        self.player.pause()
        self._network_manager.release_streams_except(track.get("stream_id"))
        self._refresh_queue_dialog()

    def apply_remote_repeat(self, enabled):
        self.repeat_track = bool(enabled)
        self.repeat_btn.set_repeat_enabled(self.repeat_track)

    def apply_metadata(self, data):
        self._current_metadata = dict(data)
        self.track_title.setText(data.get("title", "Unknown"))
        self.track_artist_prod.setText(data.get("artist", "Unknown"))
        self.lyrics_display.setText(data.get("lyrics", ""))
        if data.get("cover_bytes"):
            pixmap = QPixmap()
            pixmap.loadFromData(data["cover_bytes"])
            self.current_cover_pixmap = pixmap
            rendered = rounded_cover_pixmap(pixmap, 240, 4)
            self.cover_label.setPixmap(rendered or pixmap)
        else:
            self.current_cover_pixmap = None
            self.cover_label.clear()
        discord_rpc.update_now_playing(
            data.get("title", "Unknown"),
            data.get("artist", "Unknown"),
            data.get("cover_url"),
        )

    def toggle_playback(self):
        if self._room_connected():
            action = (
                "pause"
                if self.player.playbackState() == QMediaPlayer.PlayingState
                else "play"
            )
            self._network_manager.control(
                action, self.player.position()
            )
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            position = self.player.position()
            self.player.pause()
            self.sync_requested.emit("pause", position)
            discord_rpc.update_paused()
        else:
            if not self.player.source().isValid() and self._playlist_order:
                item = self.songs_list.currentItem()
                if item is not None:
                    self.play_song(item)
                else:
                    filename = self._playlist_order[0]
                    self.play_file(
                        self.current_playlist_path / filename,
                        filename,
                        0,
                    )
                return
            self.player.play()
            self.sync_requested.emit("play", self.player.position())

    def seek_from_slider(self):
        self.seek_to_position(self.position.value())

    def seek_to_position(self, value):
        if self._room_connected():
            self._network_manager.control(
                "seek", value
            )
        else:
            self.player.setPosition(value)
            self.sync_requested.emit("seek", value)

    def _position_changed(self, value):
        self.current_time.setText(format_time(value))
        if not self.position.isSliderDown():
            self.position.setValue(value)

    @Slot(int, int)
    def set_stream_buffer_progress(self, received_bytes, total_bytes):

        self.position.set_buffered_progress(received_bytes, total_bytes)

    def clear_stream_buffer_progress(self):
        self.position.clear_buffered_progress()

    def _duration_changed(self, value):
        self.total_time.setText(format_time(value))
        self.position.setRange(0, value)

    def toggle_repeat(self):
        if self._room_connected():
            self._network_manager.set_repeat(not self.repeat_track)
        else:
            self.apply_remote_repeat(not self.repeat_track)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        self._reset_shuffle_queue()
        self.shuffle_btn.setStyleSheet(
            f"background:{ACCENT_COLOR}" if self.is_shuffled else ""
        )

    def _reset_shuffle_queue(self):
        self._shuffle_upcoming = []
        self._shuffle_anchor = self.current_track_filename
        self._refresh_queue_dialog()

    def _queue_order_changed(self):
        valid = set(self._playback_order)
        self._shuffle_upcoming = [
            filename
            for filename in self._shuffle_upcoming
            if filename in valid
        ]
        if self._shuffle_anchor not in valid:
            self._shuffle_anchor = self.current_track_filename
        self._refresh_queue_dialog()

    def _shuffle_filenames(self, limit):
        order = list(self._playback_order)
        if not order:
            return []
        if self._shuffle_anchor != self.current_track_filename:
            self._shuffle_upcoming = []
            self._shuffle_anchor = self.current_track_filename
        valid = set(order)
        self._shuffle_upcoming = [
            filename
            for filename in self._shuffle_upcoming
            if filename in valid and filename != self.current_track_filename
        ]
        available_count = len(order)
        if self.current_track_filename in valid and available_count > 1:
            available_count -= 1
        target_count = min(limit, max(1, available_count))
        while len(self._shuffle_upcoming) < target_count:
            candidates = [
                filename
                for filename in order
                if filename != self.current_track_filename
                and filename not in self._shuffle_upcoming
            ]
            if not candidates and len(order) == 1:
                candidates = list(order)
            if not candidates:
                break
            random.shuffle(candidates)
            needed = target_count - len(self._shuffle_upcoming)
            self._shuffle_upcoming.extend(candidates[:needed])
        return self._shuffle_upcoming[:target_count]

    def _normal_filenames(self, limit):
        count = len(self._playback_order)
        if count <= 1:
            return []
        current = self.current_track_index
        if current < 0 or current >= count:
            current = self._playback_row_by_filename.get(
                self.current_track_filename,
                0,
            )
        return [
            self._playback_order[(current + offset) % count]
            for offset in range(1, min(limit, count - 1) + 1)
        ]

    def _apply_queue_reorder(self, filenames):
        filenames = [str(filename) for filename in filenames if filename]
        if self._room_connected() or len(filenames) < 2:
            self._refresh_queue_dialog()
            return
        if self.is_shuffled:
            visible = set(filenames)
            existing = list(self._shuffle_upcoming)
            if visible != set(existing[:len(filenames)]):
                self._refresh_queue_dialog()
                return
            self._shuffle_upcoming = filenames + [
                filename
                for filename in existing
                if filename not in visible
            ]
            self._refresh_queue_dialog()
            return
        count = len(self._playback_order)
        if count <= 1:
            self._refresh_queue_dialog()
            return
        current = self.current_track_index
        if current < 0 or current >= count:
            current = self._playback_row_by_filename.get(
                self.current_track_filename,
                0,
            )
        positions = [
            (current + offset) % count
            for offset in range(1, min(len(filenames), count - 1) + 1)
        ]
        existing = [self._playback_order[position] for position in positions]
        if len(positions) != len(filenames) or set(existing) != set(filenames):
            self._refresh_queue_dialog()
            return
        for position, filename in zip(positions, filenames):
            self._playback_order[position] = filename
        self._playback_row_by_filename = {
            filename: row
            for row, filename in enumerate(self._playback_order)
        }
        self.current_track_index = self._playback_row_by_filename.get(
            self.current_track_filename,
            current,
        )
        self._refresh_queue_dialog()

    def _room_queue_rows(self, limit):
        manager = self._network_manager
        queue = list(getattr(manager, "_room_queue", []) or [])
        if not queue:
            return []
        current = int(getattr(manager, "_room_queue_index", -1))
        return [
            dict(queue[(current + offset) % len(queue)])
            for offset in range(1, min(limit, len(queue)) + 1)
        ]

    def _queue_rows(self, limit=5):
        if self._room_connected():
            return "Listen Together", self._room_queue_rows(limit)
        mode = "Shuffle Mode" if self.is_shuffled else "Normal Order"
        if not self.playing_playlist_path:
            return mode, []
        filenames = (
            self._shuffle_filenames(limit)
            if self.is_shuffled
            else self._normal_filenames(limit)
        )
        rows = []
        for filename in filenames:
            title, artist, _data = self._metadata(
                self.playing_playlist_path / filename
            )
            rows.append(
                {
                    "filename": filename,
                    "title": title,
                    "artist": artist,
                }
            )
        return mode, rows

    def _refresh_queue_dialog(self):
        if self._queue_dialog is None:
            return
        mode, rows = self._queue_rows()
        self._queue_dialog.set_queue(
            mode,
            rows,
            not self._room_connected() and len(rows) > 1,
        )

    def show_queue(self):
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self)
            self._queue_dialog.queue_reordered.connect(
                self._apply_queue_reorder
            )
        self._refresh_queue_dialog()
        self._queue_dialog.show()
        self._queue_dialog.raise_()

    def play_next_track(self):
        if self._room_connected():
            self._network_manager.skip(1)
            return
        count = len(self._playback_order)
        if not count:
            return
        if self.is_shuffled:
            upcoming = self._shuffle_filenames(1)
            if not upcoming:
                return
            filename = self._shuffle_upcoming.pop(0)
            row = self._playback_row_by_filename.get(filename, 0)
            self._shuffle_anchor = filename
        else:
            row = (self.current_track_index + 1) % count
            filename = self._playback_order[row]
        self.play_file(
            self.playing_playlist_path / filename,
            filename,
            row,
            preserve_queue=self.is_shuffled,
        )
        self._refresh_queue_dialog()

    def play_prev_track(self):
        if self._room_connected():
            self._network_manager.skip(-1)
            return
        count = len(self._playback_order)
        if count:
            row = (self.current_track_index - 1) % count
            filename = self._playback_order[row]
            self.play_file(
                self.playing_playlist_path / filename, filename, row
            )

    play_next = play_next_track
    play_previous = play_prev_track

    def _media_status(self, status):
        if status != QMediaPlayer.EndOfMedia:
            return
        if self._room_connected():
            self._network_manager.track_ended(
                self._active_room_request
            )
        elif self.repeat_track:
            self.player.setPosition(0)
            self.player.play()
        else:
            self.play_next_track()

    def toggle_mute(self):
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self.volume_btn.setIcon(
            colored_icon(
                "volume-off.svg"
                if self.audio_output.isMuted()
                else "volume-on.svg"
            )
        )
