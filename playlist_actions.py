

import os
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox,
)

from config import ACCENT_COLOR, BUTTON_BORDER, PANEL_BG, TEXT_COLOR, TEXT_MUTED
from dialogs import AddSongDialog
from playlist_components import CoverPreviewDialog
from utils import colored_icon

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


class PlaylistActionsMixin:
    def add_song(self):
        if (
            self.current_playlist
            and AddSongDialog(self, self.current_playlist).exec()
        ):
            playlist_name = self.current_playlist
            self.refresh()
            self.playlist_updated.emit(playlist_name)

    def _track_menu(self, position):
        item = self.songs_list.itemAt(position)
        if not item:
            return
        if not item.isSelected():
            self.songs_list.clearSelection()
            item.setSelected(True)
        items = [
            selected
            for selected in self.songs_list.selectedItems()
            if selected.data(Qt.UserRole)
        ]
        if not items:
            return
        paths = [
            self.current_playlist_path / selected.data(Qt.UserRole)
            for selected in items
        ]
        single_selection = len(items) == 1
        menu = make_menu(self)
        play = menu.addAction(
            colored_icon("play.svg", size=MENU_ICON_SIZE), "Play"
        )
        menu.addSeparator()
        rename = menu.addAction(
            colored_icon("rename.svg", size=MENU_ICON_SIZE), "Rename"
        )
        duplicate = menu.addAction(
            colored_icon("copy.svg", size=MENU_ICON_SIZE), "Duplicate"
        )
        delete = menu.addAction(
            colored_icon("delete.svg", size=MENU_ICON_SIZE), "Delete"
        )
        menu.addSeparator()
        folder = menu.addAction(
            colored_icon("folder.svg", size=MENU_ICON_SIZE),
            "Open in Folder",
        )
        for action in (play, rename, folder):
            action.setEnabled(single_selection)
        chosen = menu.exec(
            self.songs_list.viewport().mapToGlobal(position)
        )
        path = self.current_playlist_path / item.data(Qt.UserRole)

        if chosen is play:
            self.play_song(item)
        elif chosen is rename:
            name, accepted = QInputDialog.getText(
                self,
                "Rename Track",
                "New name:",
                text=path.stem,
            )
            if accepted and name.strip():
                destination = path.with_name(
                    name.strip() + path.suffix
                )
                if destination.exists() and destination != path:
                    QMessageBox.warning(
                        self,
                        "Rename Track",
                        "A track with that name already exists.",
                    )
                    return
                old_name = path.name
                path.rename(destination)
                self._move_sidecars(path, destination)
                self._replace_song_in_order(
                    old_name, destination.name
                )
                if self.current_track_filename == old_name:
                    self.current_track_filename = destination.name
                    self.current_track_path = destination.resolve()
                self.songs_list.viewport().update()
                self.playlist_updated.emit(self.current_playlist)
        elif chosen is duplicate:
            failures = []
            inserted = []
            for source in paths:
                destination = source.with_name(
                    source.stem + " copy" + source.suffix
                )
                counter = 2
                while destination.exists():
                    destination = source.with_name(
                        f"{source.stem} copy {counter}{source.suffix}"
                    )
                    counter += 1
                try:
                    shutil.copy2(source, destination)
                    self._copy_sidecars(source, destination)
                    inserted.append((source.name, destination.name))
                except OSError as exc:
                    failures.append(f"{source.name}: {exc}")
            self._insert_songs_after(inserted)
            self.playlist_updated.emit(self.current_playlist)
            if failures:
                QMessageBox.critical(
                    self,
                    "Duplicate Track",
                    "Some tracks could not be duplicated:\n"
                    + "\n".join(failures[:3]),
                )
        elif chosen is delete:
            count = len(paths)
            prompt = (
                f"Delete {paths[0].name}?"
                if count == 1
                else f"Delete {count} selected tracks?"
            )
            if (
                QMessageBox.question(
                    self, "Delete", prompt
                )
                == QMessageBox.Yes
            ):
                failures = []
                deleted = []
                for selected_path in paths:
                    deleted_name = selected_path.name
                    self.release_track(selected_path)
                    try:
                        self._unlink_track(selected_path)
                        self._delete_sidecars(selected_path)
                    except OSError as exc:
                        failures.append(f"{deleted_name}: {exc}")
                        continue
                    deleted.append(deleted_name)
                    if self.current_track_filename == deleted_name:
                        self.current_track_filename = None
                        self.current_track_index = -1
                self._remove_songs_from_order(deleted)
                self.playlist_updated.emit(self.current_playlist)
                if failures:
                    QMessageBox.critical(
                        self,
                        "Delete Track",
                        "Some tracks could not be deleted:\n"
                        + "\n".join(failures[:3]),
                    )
        elif chosen is folder:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])

    @staticmethod
    def _unlink_track(path):
        last_error = None
        for _attempt in range(5):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError as exc:
                last_error = exc
                QApplication.processEvents()
                time.sleep(0.04)
        if last_error:
            raise last_error

    @staticmethod
    def _move_sidecars(source, destination):
        for extension in (
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ):
            old = source.with_suffix(extension)
            if old.exists():
                old.rename(destination.with_suffix(extension))

    @staticmethod
    def _copy_sidecars(source, destination):
        for extension in (
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ):
            old = source.with_suffix(extension)
            if old.exists():
                shutil.copy2(
                    old, destination.with_suffix(extension)
                )

    @staticmethod
    def _delete_sidecars(source):
        for extension in (
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ):
            source.with_suffix(extension).unlink(missing_ok=True)

    def _cover_menu(self, position):
        menu = make_menu(self)
        view = menu.addAction(
            colored_icon("view.svg", size=MENU_ICON_SIZE),
            "View Full Size",
        )
        save = menu.addAction(
            colored_icon("download.svg", size=MENU_ICON_SIZE),
            "Download Cover",
        )
        copy = menu.addAction(
            colored_icon("copy.svg", size=MENU_ICON_SIZE),
            "Copy Cover",
        )
        has_cover = (
            self.current_cover_pixmap is not None
            and not self.current_cover_pixmap.isNull()
        )
        view.setEnabled(has_cover)
        save.setEnabled(has_cover)
        copy.setEnabled(has_cover)
        chosen = menu.exec(self.cover_label.mapToGlobal(position))
        if chosen is view:
            CoverPreviewDialog(
                self.current_cover_pixmap, self
            ).exec()
        elif chosen is save:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Cover",
                "cover.jpg",
                "Images (*.jpg *.jpeg *.png)",
            )
            if path:
                self.current_cover_pixmap.save(path)
        elif chosen is copy:
            QGuiApplication.clipboard().setPixmap(
                self.current_cover_pixmap
            )
