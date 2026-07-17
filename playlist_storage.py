import json
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QListWidgetItem

from config import PLAYLISTS_PATH
from playlist_components import TrackItemDelegate
from playlist_index import (
    PlaylistSnapshotLoader,
    cancel_playlist_writes,
    schedule_playlist_write,
)


class PlaylistStorageMixin:
    LOAD_BATCH_SIZE = 256
    METADATA_CACHE_SIZE = 2048
    ROW_CACHE_SIZE = 384

    def _ensure_storage_state(self):
        if hasattr(self, "_playlist_order"):
            return
        self._playlist_order = []
        self._row_by_filename = {}
        self._playlist_metadata = {}
        self._track_metadata_cache = OrderedDict()
        self._row_display_cache = OrderedDict()
        self._playlist_load_generation = 0
        self._population_generation = 0
        self._playlist_loaders = set()
        self._list_fully_loaded = True
        self._pending_selection_name = None

    def install_track_delegate(self):
        self._ensure_storage_state()
        self._track_delegate = TrackItemDelegate(
            self._row_display_data, self.songs_list
        )
        self.songs_list.setItemDelegate(self._track_delegate)

    def load_playlist(self, name):
        self._ensure_storage_state()
        self.current_playlist = str(name)
        self.current_playlist_path = PLAYLISTS_PATH / str(name) / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(str(name))
        self._order_undo_stack.clear()
        self._start_playlist_load(reset=True)

    def _start_playlist_load(self, selected_name=None, reset=False):
        self._ensure_storage_state()
        self._playlist_load_generation += 1
        generation = self._playlist_load_generation
        self._population_generation += 1
        self._list_fully_loaded = False
        self._pending_selection_name = selected_name
        self.songs_list.setDragEnabled(False)
        if reset:
            self._playlist_order = []
            self._row_by_filename = {}
            self._playlist_metadata = {}
            self._track_metadata_cache.clear()
            self._row_display_cache.clear()
            self.songs_list.clear()
        if not self.current_playlist:
            return

        loader = PlaylistSnapshotLoader(
            generation, self.current_playlist, self
        )
        self._playlist_loaders.add(loader)
        loader.loaded.connect(self._playlist_loaded)
        loader.finished.connect(
            lambda current=loader: self._playlist_loader_finished(current)
        )
        loader.start()

    def cancel_playlist_loading(self):
        self._ensure_storage_state()
        self._playlist_load_generation += 1
        self._population_generation += 1
        for loader in tuple(self._playlist_loaders):
            loader.requestInterruption()
        self._list_fully_loaded = True

    def _playlist_loader_finished(self, loader):
        self._playlist_loaders.discard(loader)
        loader.deleteLater()

    def _playlist_loaded(
        self, generation, name, order, metadata, needs_write
    ):
        if (
            generation != self._playlist_load_generation
            or name != self.current_playlist
        ):
            return
        self._playlist_metadata = dict(metadata or {})
        self._playlist_metadata.pop("songs", None)
        self._playlist_order = list(order)
        self._row_by_filename = {
            filename: row for row, filename in enumerate(self._playlist_order)
        }
        if needs_write:
            schedule_playlist_write(
                name, self._playlist_order, self._playlist_metadata
            )
        self._begin_population(
            self._playlist_order, self._pending_selection_name
        )
        self._sync_current_track_index()
        updated = getattr(self, "playlist_updated", None)
        if updated is not None:
            updated.emit(name)

    def _begin_population(self, order, selected_name=None):
        self._population_generation += 1
        generation = self._population_generation
        self._populate_order = order
        self._populate_offset = 0
        self._pending_selection_name = selected_name
        self._list_fully_loaded = False
        self.songs_list.setDragEnabled(False)
        self.songs_list.clear()
        self._append_population_batch(generation)

    def _append_population_batch(self, generation):
        if (
            generation != self._population_generation
            or not self.current_playlist_path
        ):
            return
        start = self._populate_offset
        end = min(start + self.LOAD_BATCH_SIZE, len(self._populate_order))
        self.songs_list.setUpdatesEnabled(False)
        try:
            for row in range(start, end):
                filename = self._populate_order[row]
                item = self._append_song_row(filename, row + 1)
                if filename == self._pending_selection_name:
                    self.songs_list.setCurrentItem(item)
        finally:
            self.songs_list.setUpdatesEnabled(True)
        self._populate_offset = end
        if end < len(self._populate_order):
            QTimer.singleShot(
                0, lambda token=generation: self._append_population_batch(token)
            )
            return
        self._list_fully_loaded = True
        self.songs_list.setDragEnabled(True)
        self.songs_list.viewport().update()

    def _playlist_metadata_path(self):
        return (
            PLAYLISTS_PATH / f"{self.current_playlist}.json"
            if self.current_playlist
            else None
        )

    def _read_playlist_metadata(self):
        self._ensure_storage_state()
        if self._playlist_metadata:
            return dict(self._playlist_metadata)
        path = self._playlist_metadata_path()
        if not path or not path.exists():
            return {"name": self.current_playlist or "", "song_count": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {"name": self.current_playlist or "", "song_count": 0}

    def _write_song_order(self, filenames):
        self._ensure_storage_state()
        order = filenames if isinstance(filenames, list) else list(filenames)
        self._playlist_order = order
        self._row_by_filename = {
            filename: row for row, filename in enumerate(order)
        }
        self._playlist_metadata["name"] = self.current_playlist
        self._playlist_metadata["song_count"] = len(order)
        if self.current_playlist:
            schedule_playlist_write(
                self.current_playlist, order, self._playlist_metadata
            )

    def _ordered_song_files(self):
        self._ensure_storage_state()
        if not self.current_playlist_path:
            return []
        return [
            self.current_playlist_path / filename
            for filename in self._playlist_order
        ]

    def refresh(self):
        selected = self.songs_list.currentItem()
        selected_name = selected.data(Qt.UserRole) if selected else None
        if not self.current_playlist_path:
            self.songs_list.clear()
            return
        self._start_playlist_load(selected_name, reset=False)

    update_songs_list = refresh

    def _new_song_item(self, filename):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, filename)
        item.setSizeHint(QSize(0, TrackItemDelegate.ROW_HEIGHT))
        item.setFlags(
            (
                item.flags()
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsEnabled
            )
            & ~Qt.ItemIsDropEnabled
        )
        return item

    def _append_song_row(
        self, filename, _index, _title=None, _artist=None, _cover_pixmap=None
    ):
        item = self._new_song_item(filename)
        self.songs_list.addItem(item)
        return item

    def _songs_reordered(self, before, after):
        if before == after:
            return
        self._order_undo_stack.append(list(before))
        self._order_undo_stack = self._order_undo_stack[-50:]
        self._write_song_order(list(after))
        self._sync_current_track_index()
        self.songs_list.viewport().update()

    def undo_song_reorder(self):
        if not self._order_undo_stack:
            return
        previous = self._order_undo_stack.pop()
        if not self.songs_list.apply_order(previous):
            self._begin_population(previous)
        self._write_song_order(list(previous))
        self._sync_current_track_index()
        self.songs_list.viewport().update()

    def _sync_current_track_index(self):
        self._ensure_storage_state()
        self.current_track_index = self._row_by_filename.get(
            self.current_track_filename, -1
        )

    def _replace_song_in_order(self, old_name, new_name):
        row = self._row_by_filename.get(old_name)
        if row is None:
            return
        order = list(self._playlist_order)
        order[row] = new_name
        if row < self.songs_list.count():
            self.songs_list.item(row).setData(Qt.UserRole, new_name)
        self._invalidate_track_cache(old_name)
        self._invalidate_track_cache(new_name)
        self._write_song_order(order)
        self.songs_list.viewport().update()

    def _insert_songs_after(self, pairs):
        pairs = [(str(source), str(new)) for source, new in pairs]
        if not pairs:
            return
        insertions = {}
        for source, new_name in pairs:
            insertions.setdefault(source, []).append(new_name)

        order = []
        inserted = set()
        for filename in self._playlist_order:
            order.append(filename)
            for new_name in insertions.get(filename, []):
                if new_name not in inserted:
                    order.append(new_name)
                    inserted.add(new_name)
        for _source, new_name in pairs:
            if new_name not in inserted:
                order.append(new_name)
                inserted.add(new_name)

        was_fully_loaded = self._list_fully_loaded
        self._write_song_order(order)
        if not was_fully_loaded:
            self._begin_population(order)
            return
        for _source, new_name in sorted(
            pairs, key=lambda pair: self._row_by_filename[pair[1]]
        ):
            row = self._row_by_filename[new_name]
            self.songs_list.insertItem(row, self._new_song_item(new_name))
        self.songs_list.viewport().update()

    def _insert_song_after(self, source_name, new_name):
        self._insert_songs_after([(source_name, new_name)])

    def _remove_songs_from_order(self, filenames):
        removed = {str(filename) for filename in filenames}
        if not removed:
            return
        old_rows = sorted(
            (
                self._row_by_filename[name]
                for name in removed
                if name in self._row_by_filename
            ),
            reverse=True,
        )
        order = [
            filename
            for filename in self._playlist_order
            if filename not in removed
        ]
        was_fully_loaded = self._list_fully_loaded
        if was_fully_loaded:
            for row in old_rows:
                self.songs_list.takeItem(row)
        self._write_song_order(order)
        for filename in removed:
            self._invalidate_track_cache(filename)
        if not was_fully_loaded:
            self._begin_population(order)
        self._sync_current_track_index()
        self.songs_list.viewport().update()

    def _remove_song_from_order(self, filename):
        self._remove_songs_from_order([filename])

    def _invalidate_track_cache(self, filename):
        if not filename:
            return
        key = str(filename)
        self._track_metadata_cache.pop(key, None)
        self._row_display_cache.pop(key, None)

    def _metadata(self, file):
        self._ensure_storage_state()
        file = Path(file)
        cache_key = file.name
        cached = self._track_metadata_cache.get(cache_key)
        if cached is not None:
            self._track_metadata_cache.move_to_end(cache_key)
            return cached

        data = {}
        try:
            sidecar = file.with_suffix(".json")
            if sidecar.exists():
                value = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    data = value
        except (OSError, ValueError):
            pass
        title, artist = data.get("title"), data.get("artist")
        if (not title or not artist) and " - " in file.stem:
            parsed_artist, parsed_title = file.stem.split(" - ", 1)
            title = title or parsed_title
            artist = artist or parsed_artist
        result = (title or file.stem, artist or "Unknown Artist", data)
        self._track_metadata_cache[cache_key] = result
        self._track_metadata_cache.move_to_end(cache_key)
        while len(self._track_metadata_cache) > self.METADATA_CACHE_SIZE:
            self._track_metadata_cache.popitem(last=False)
        return result

    @staticmethod
    def _cover_path(file):
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            path = Path(file).with_suffix(extension)
            if path.exists():
                return path
        return None

    @classmethod
    def _cover_thumbnail(cls, file):
        path = cls._cover_path(file)
        if path is None:
            return None
        reader = QImageReader(str(path))
        source_size = reader.size()
        target = TrackItemDelegate.COVER_SIZE
        if source_size.isValid() and (
            source_size.width() > target or source_size.height() > target
        ):
            reader.setScaledSize(source_size.scaled(
                target, target, Qt.KeepAspectRatio
            ))
        image = reader.read()
        if image.isNull():
            return None
        source = QPixmap.fromImage(image)
        source = source.scaled(
            target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        output = QPixmap(target, target)
        output.fill(Qt.transparent)
        painter = QPainter(output)
        painter.drawPixmap(
            (target - source.width()) // 2,
            (target - source.height()) // 2,
            source,
        )
        painter.end()
        return output

    def _row_display_data(self, filename):
        self._ensure_storage_state()
        filename = str(filename or "")
        cached = self._row_display_cache.get(filename)
        if cached is not None:
            self._row_display_cache.move_to_end(filename)
            return cached
        path = self.current_playlist_path / filename
        title, artist, _data = self._metadata(path)
        result = (title, artist, self._cover_thumbnail(path))
        self._row_display_cache[filename] = result
        self._row_display_cache.move_to_end(filename)
        while len(self._row_display_cache) > self.ROW_CACHE_SIZE:
            self._row_display_cache.popitem(last=False)
        return result

    @classmethod
    def _cover(cls, file):
        path = cls._cover_path(file)
        if path is None:
            return None
        pixmap = QPixmap(str(path))
        return pixmap if not pixmap.isNull() else None

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

    def playlist_summary(self):
        self._ensure_storage_state()
        first = (
            str(self.current_playlist_path / self._playlist_order[0])
            if self.current_playlist_path and self._playlist_order
            else ""
        )
        return len(self._playlist_order), first

    def forget_playlist(self, name):
        cancel_playlist_writes(str(name))

    def play_song(self, item):
        filename = item.data(Qt.UserRole)
        self.play_file(
            self.current_playlist_path / filename,
            filename,
            self._row_by_filename.get(filename, self.songs_list.row(item)),
        )
