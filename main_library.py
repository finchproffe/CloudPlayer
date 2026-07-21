import json
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QListWidgetItem

from config import PANEL_BG, PLAYLISTS_PATH, TEXT_MUTED
from dropdown_ui import QFileDialog, QInputDialog, QMessageBox
from main_common import make_menu
from playlist_index import PlaylistSummaryLoader
from utils import rounded_cover_pixmap


class LibraryMixin:
    def _ensure_playlist(self, name):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:100] or "Playlist"
        (PLAYLISTS_PATH / name / "songs").mkdir(parents=True, exist_ok=True)
        metadata = PLAYLISTS_PATH / f"{name}.json"
        if not metadata.exists():
            metadata.write_text(
                json.dumps({"name": name, "song_count": 0}),
                encoding="utf-8",
            )
        self._known_playlist_names.add(name)
        if name not in self._playlist_items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            self.playlist_list.addItem(item)
            self._playlist_items[name] = item
            self._playlist_summaries.setdefault(name, (0, ""))
            self._refresh_playlist_item(item)
        return name

    def create_playlist(self):
        name, accepted = QInputDialog.getText(self, "New Playlist", "Name:")
        if accepted and name.strip():
            self._ensure_playlist(name.strip())

    def load_playlists(self):
        self._playlist_list_generation += 1
        generation = self._playlist_list_generation
        for loader in tuple(self._playlist_summary_loaders):
            loader.requestInterruption()
        self.playlist_list.clear()
        self._playlist_items.clear()
        self._known_playlist_names.clear()
        self._pending_playlist_names = []
        self._pending_playlist_offset = 0
        self._start_playlist_summary_load(None, generation)

    def _playlist_names_ready(self, generation, names):
        if generation != self._playlist_list_generation:
            return
        names = sorted(
            set(str(name) for name in names) | self._known_playlist_names,
            key=str.casefold,
        )
        self._known_playlist_names = set(names)
        self._pending_playlist_names = names
        self._pending_playlist_offset = 0
        self._append_playlist_batch(generation)

    def _append_playlist_batch(self, generation):
        if generation != self._playlist_list_generation:
            return
        start = self._pending_playlist_offset
        end = min(start + 24, len(self._pending_playlist_names))
        self.playlist_list.setUpdatesEnabled(False)
        for name in self._pending_playlist_names[start:end]:
            if name in self._playlist_items:
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            self.playlist_list.addItem(item)
            self._playlist_items[name] = item
            self._refresh_playlist_item(item)
        self.playlist_list.setUpdatesEnabled(True)
        self._pending_playlist_offset = end
        if end < len(self._pending_playlist_names):
            QTimer.singleShot(
                0, lambda token=generation: self._append_playlist_batch(token)
            )

    def _start_playlist_summary_load(self, names, discover_generation=None):
        if names is not None:
            names = [
                str(name)
                for name in names
                if str(name) in self._known_playlist_names
            ]
        if names is not None and not names:
            return
        loader = PlaylistSummaryLoader(names, self)
        self._playlist_summary_loaders.add(loader)
        if discover_generation is not None:
            loader.names_ready.connect(
                lambda discovered, token=discover_generation: (
                    self._playlist_names_ready(token, discovered)
                )
            )
        loader.summary_ready.connect(self._playlist_summary_ready)
        loader.finished.connect(
            lambda current=loader: self._playlist_summary_finished(current)
        )
        loader.start()

    def _playlist_summary_finished(self, loader):
        self._playlist_summary_loaders.discard(loader)
        loader.deleteLater()

    def _playlist_summary_ready(self, name, count, first_track):
        if name not in self._known_playlist_names:
            return
        self._playlist_summaries[name] = (int(count), str(first_track or ""))
        item = self._playlist_items.get(name)
        if item is not None:
            self._refresh_playlist_item(item)

    def open_playlist(self, item):
        self.playlist_view.load_playlist(item.data(Qt.UserRole))
        self._switch(1)

    def _refresh_playlist_item(self, item):
        name = item.data(Qt.UserRole)
        summary = self._playlist_summaries.get(name)
        count, first_track = summary if summary is not None else (None, "")
        cover = self._playlist_cover(name, first_track)
        rendered = rounded_cover_pixmap(cover, self.PLAYLIST_COVER, 9) if cover else self._placeholder(self.PLAYLIST_COVER)
        item.setIcon(QIcon(rendered))
        if count is None:
            item.setText(name)
        else:
            item.setText(
                f"{name}\n{count} {'song' if count == 1 else 'songs'}"
            )

    def refresh_playlist_item(self, name):
        name = str(name)
        if self.playlist_view.current_playlist == name:
            self._playlist_summaries[name] = self.playlist_view.playlist_summary()
            item = self._playlist_items.get(name)
            if item is not None:
                self._refresh_playlist_item(item)
            return
        self._start_playlist_summary_load([name])

    @staticmethod
    def _playlist_cover(name, first_track=""):
        folder = PLAYLISTS_PATH / name
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            path = folder / f"cover{extension}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap
        if first_track:
            first_track = Path(first_track)
            for extension in (".jpg", ".jpeg", ".png", ".webp"):
                path = first_track.with_suffix(extension)
                if path.exists():
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        return pixmap
        return None

    @staticmethod
    def _placeholder(size):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), 9, 9)
        painter.fillPath(path, QColor(PANEL_BG))
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "♪")
        painter.end()
        return pixmap

    def _playlist_menu(self, position):
        item = self.playlist_list.itemAt(position)
        if not item:
            return
        if not item.isSelected():
            self.playlist_list.clearSelection()
            item.setSelected(True)
        items = self.playlist_list.selectedItems()
        single_selection = len(items) == 1
        name = item.data(Qt.UserRole)
        menu = make_menu(self)
        open_action = menu.addAction("Open")
        set_cover = menu.addAction("Set Cover Image...")
        reset_cover = menu.addAction("Reset to Auto")
        menu.addSeparator()
        remove_action = menu.addAction("Remove Playlist")
        open_action.setEnabled(single_selection)
        set_cover.setEnabled(single_selection)
        chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(position))
        if chosen is open_action:
            self.open_playlist(item)
        elif chosen is set_cover:
            filename, _ = QFileDialog.getOpenFileName(self, "Choose Cover", "", "Images (*.png *.jpg *.jpeg *.webp)")
            if filename:
                self._reset_cover(name)
                shutil.copy2(filename, PLAYLISTS_PATH / name / f"cover{Path(filename).suffix.lower()}")
                self._refresh_playlist_item(item)
        elif chosen is reset_cover:
            for selected in items:
                selected_name = selected.data(Qt.UserRole)
                self._reset_cover(selected_name)
                self._refresh_playlist_item(selected)
        elif chosen is remove_action:
            self.remove_playlist(items)

    @staticmethod
    def _reset_cover(name):
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            (PLAYLISTS_PATH / name / f"cover{extension}").unlink(missing_ok=True)

    def remove_playlist(self, items=None):
        items = list(items or self.playlist_list.selectedItems())
        if not items and self.playlist_list.currentItem():
            items = [self.playlist_list.currentItem()]
        if not items:
            return
        names = [str(item.data(Qt.UserRole)) for item in items]
        cloud_suffix = (
            " Synchronized cloud copies will also be removed."
            if self.account_user
            else ""
        )
        prompt = (
            f"Remove '{names[0]}' and its tracks?{cloud_suffix}"
            if len(names) == 1
            else f"Remove {len(names)} selected playlists and all their "
            f"tracks?{cloud_suffix}"
        )
        if QMessageBox.question(self, "Remove Playlist", prompt) != QMessageBox.Yes:
            return
        failures = []
        removed_names = []
        for item, name in zip(items, names):
            self.playlist_view.release_playlist(name)
            folder = PLAYLISTS_PATH / name
            try:
                self._remove_tree(folder)
                (PLAYLISTS_PATH / f"{name}.json").unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"{name}: {exc}")
                continue
            self.playlist_list.takeItem(self.playlist_list.row(item))
            self._playlist_items.pop(name, None)
            self._playlist_summaries.pop(name, None)
            self._known_playlist_names.discard(name)
            removed_names.append(name)
        if removed_names:
            self._queue_deleted_cloud_entries(playlist_names=removed_names)
        if failures:
            QMessageBox.critical(
                self,
                "Remove Playlist",
                "Some playlists could not be removed:\n"
                + "\n".join(failures[:3]),
            )

    @staticmethod
    def _remove_tree(folder):
        if not folder.exists():
            return
        last_error = None
        for _attempt in range(20):
            try:
                shutil.rmtree(folder)
                return
            except PermissionError as exc:
                last_error = exc
                QApplication.processEvents()
                time.sleep(0.05)
        if last_error:
            raise last_error
