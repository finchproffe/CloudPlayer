

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListWidgetItem

from config import AUDIO_EXTENSIONS, PLAYLISTS_PATH
from playlist_components import TrackListItemWidget


class PlaylistStorageMixin:
    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = PLAYLISTS_PATH / name / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(name)
        self._order_undo_stack.clear()
        self.refresh()

    def _playlist_metadata_path(self):
        return (
            PLAYLISTS_PATH / f"{self.current_playlist}.json"
            if self.current_playlist
            else None
        )

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
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ordered_song_files(self):
        if not self.current_playlist_path:
            return []
        files_by_name = {
            file.name: file
            for file in self.current_playlist_path.iterdir()
            if file.is_file() and file.suffix.lower() in AUDIO_EXTENSIONS
        }
        saved_order = self._read_playlist_metadata().get("songs", [])
        if not isinstance(saved_order, list):
            saved_order = []
        ordered_names = [
            name for name in saved_order if name in files_by_name
        ]
        ordered_names.extend(
            sorted(
                (
                    name
                    for name in files_by_name
                    if name not in ordered_names
                ),
                key=str.casefold,
            )
        )
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
            self._append_song_row(
                file.name,
                number,
                title,
                artist,
                self._cover(file),
            )
            if file.name == selected_name:
                self.songs_list.setCurrentItem(
                    self.songs_list.item(self.songs_list.count() - 1)
                )
        self._sync_current_track_index()

    update_songs_list = refresh

    def _append_song_row(
        self, filename, index, title, artist, cover_pixmap=None
    ):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, filename)
        item.setSizeHint(QSize(0, TrackListItemWidget.ROW_HEIGHT))
        item.setFlags(
            (
                item.flags()
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsEnabled
            )
            & ~Qt.ItemIsDropEnabled
        )
        self.songs_list.addItem(item)
        self.songs_list.setItemWidget(
            item,
            TrackListItemWidget(
                index, title, artist, cover_pixmap
            ),
        )

    def _songs_reordered(self, before, after):
        if before == after:
            return
        snapshots = {}
        for row in range(self.songs_list.count()):
            item = self.songs_list.item(row)
            widget = self.songs_list.itemWidget(item)
            if item is not None and widget is not None:
                snapshots[item.data(Qt.UserRole)] = widget.snapshot()

        selected_name = (
            self.songs_list.currentItem().data(Qt.UserRole)
            if self.songs_list.currentItem()
            else None
        )
        scroll_value = self.songs_list.verticalScrollBar().value()
        self._order_undo_stack.append(list(before))
        self._order_undo_stack = self._order_undo_stack[-50:]
        self._write_song_order(after)

        self.songs_list.setUpdatesEnabled(False)
        self.songs_list.clear()
        for index, filename in enumerate(after, 1):
            data = snapshots.get(filename)
            if data is None:
                path = self.current_playlist_path / filename
                title, artist, _ = self._metadata(path)
                cover = self._cover(path)
            else:
                title = data["title"]
                artist = data["artist"]
                cover = data["cover"]
            self._append_song_row(
                filename, index, title, artist, cover
            )
            if filename == selected_name:
                self.songs_list.setCurrentItem(
                    self.songs_list.item(self.songs_list.count() - 1)
                )
        self.songs_list.verticalScrollBar().setValue(scroll_value)
        self.songs_list.setUpdatesEnabled(True)
        self.songs_list.viewport().update()
        self._sync_current_track_index()

    def undo_song_reorder(self):
        if not self._order_undo_stack:
            return
        previous = self._order_undo_stack.pop()
        current = self.songs_list.order()
        self._songs_reordered(current, previous)
        if self._order_undo_stack:
            self._order_undo_stack.pop()

    def _sync_current_track_index(self):
        self.current_track_index = next(
            (
                row
                for row in range(self.songs_list.count())
                if self.songs_list.item(row).data(Qt.UserRole)
                == self.current_track_filename
            ),
            -1,
        )

    def _replace_song_in_order(self, old_name, new_name):
        order = self._read_playlist_metadata().get("songs", [])
        if not isinstance(order, list):
            order = []
        self._write_song_order(
            [new_name if name == old_name else name for name in order]
        )

    def _insert_song_after(self, source_name, new_name):
        order = self._read_playlist_metadata().get("songs", [])
        if not isinstance(order, list):
            order = []
        order = [name for name in order if name != new_name]
        try:
            index = order.index(source_name) + 1
        except ValueError:
            index = len(order)
        order.insert(index, new_name)
        self._write_song_order(order)

    def _remove_song_from_order(self, filename):
        order = self._read_playlist_metadata().get("songs", [])
        if not isinstance(order, list):
            order = []
        self._write_song_order(
            [name for name in order if name != filename]
        )

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

    def _track_descriptor(self, path, playlist=None):
        path = Path(path)
        title, artist, data = self._metadata(path)
        return {
            "playlist": str(
                playlist or self.current_playlist or "Listen Together"
            ),
            "filename": path.name,
            "title": title,
            "artist": artist,
            "source_url": str(
                data.get("source_url") or data.get("download_url") or ""
            ),
        }

    def _room_queue(self):
        return [
            self._track_descriptor(path, self.current_playlist)
            for path in self._ordered_song_files()
        ]

    def play_song(self, item):
        self.play_file(
            self.current_playlist_path / item.data(Qt.UserRole),
            item.data(Qt.UserRole),
            self.songs_list.row(item),
        )
