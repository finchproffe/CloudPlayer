import json
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QMenu,
)

from config import ACCENT_COLOR, BUTTON_BORDER, PANEL_BG, TEXT_COLOR, TEXT_MUTED
from dialogs import AddSongDialog
from dropdown_ui import QFileDialog, QInputDialog, QMessageBox
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
        if not self.current_playlist:
            return
        playlist_name = self.current_playlist
        dialog = AddSongDialog(self, playlist_name)
        if dialog.exec():
            self.register_added_tracks(
                playlist_name, dialog.downloaded_paths
            )

    def delete_selected_tracks(self, items=None):
        items = list(items or self.songs_list.selectedItems())
        if not items and self.songs_list.currentItem() is not None:
            items = [self.songs_list.currentItem()]
        items = [item for item in items if item.data(Qt.UserRole)]
        if not items or not self.current_playlist_path:
            return False

        paths = [
            self.current_playlist_path / item.data(Qt.UserRole)
            for item in items
        ]
        count = len(paths)
        prompt = (
            f"Delete {paths[0].name}? Its synchronized copy in this "
            "playlist will also be removed if present."
            if count == 1
            else f"Delete {count} selected tracks? Their synchronized "
            "copies in this playlist will also be removed if present."
        )
        if QMessageBox.question(self, "Delete", prompt) != QMessageBox.Yes:
            return True

        failures = []
        deleted = []
        deleted_urls = []
        for selected_path in paths:
            deleted_name = selected_path.name
            source_url = str(
                self._track_descriptor(selected_path).get("source_url")
                or ""
            ).strip()
            self.release_track(selected_path)
            try:
                self._unlink_track(selected_path)
                self._delete_sidecars(selected_path)
            except OSError as exc:
                failures.append(f"{deleted_name}: {exc}")
                continue
            deleted.append(deleted_name)
            if source_url.startswith(("http://", "https://")):
                deleted_urls.append(source_url)
        self._remove_songs_from_order(deleted)
        self.playlist_updated.emit(self.current_playlist)
        if deleted:
            self.tracks_deleted.emit(
                str(self.current_playlist or ""), deleted_urls
            )
        if failures:
            QMessageBox.critical(
                self,
                "Delete Track",
                "Some tracks could not be deleted:\n"
                + "\n".join(failures[:3]),
            )
        return True

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
            "Folder Path",
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
                clean_name = re.sub(
                    r'[<>:"/\\|?*\x00-\x1f]', "_", name
                ).strip(" .")[:180]
                if not clean_name:
                    QMessageBox.warning(
                        self,
                        "Rename Track",
                        "Enter a valid track name.",
                    )
                    return
                destination = path.with_name(
                    clean_name + path.suffix
                )
                if destination == path:
                    return
                if destination.exists() and not self._same_file(path, destination):
                    QMessageBox.warning(
                        self,
                        "Rename Track",
                        "A track with that name already exists.",
                    )
                    return
                old_name = path.name
                playback = self._prepare_track_rename(path)
                try:
                    artist, title = self._rename_track_files(
                        path, destination, clean_name
                    )
                except OSError as exc:
                    self._restore_track_after_rename(path, playback)
                    QMessageBox.critical(
                        self,
                        "Rename Track",
                        f"The track could not be renamed:\n{exc}",
                    )
                    return
                if playback is not None:
                    self.current_track_filename = destination.name
                    self.current_track_path = destination.resolve()
                    metadata = dict(self._current_metadata)
                    metadata["artist"] = artist
                    metadata["title"] = title
                    self.now_playing.setText(
                        f"Now Playing: {title} • {artist}"
                    )
                    self.apply_metadata(metadata)
                self._replace_song_in_order(
                    old_name, destination.name
                )
                self._restore_track_after_rename(destination, playback)
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
            self.delete_selected_tracks(items)
        elif chosen is folder:
            path_menu = make_menu(self)
            location = path_menu.addAction(str(path))
            location.setEnabled(False)
            path_menu.addSeparator()
            copy_path = path_menu.addAction(
                colored_icon("copy.svg", size=MENU_ICON_SIZE), "Copy Path"
            )
            selected = path_menu.exec(
                self.songs_list.viewport().mapToGlobal(position)
            )
            if selected is copy_path:
                QGuiApplication.clipboard().setText(str(path))

    @staticmethod
    def _unlink_track(path):
        last_error = None
        for _attempt in range(20):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError as exc:
                last_error = exc
                QApplication.processEvents()
                time.sleep(0.05)
        if last_error:
            raise last_error

    @staticmethod
    def _same_file(first, second):
        try:
            return Path(first).samefile(second)
        except OSError:
            try:
                return Path(first).resolve() == Path(second).resolve()
            except OSError:
                return Path(first) == Path(second)

    def _prepare_track_rename(self, path):
        release = getattr(self._network_manager, "release_local_path", None)
        if release is not None:
            release(path)
        if self.current_track_path is None or not self._same_file(
            self.current_track_path, path
        ):
            return None
        state = {
            "position": self.player.position(),
            "playing": (
                self.player.playbackState() == QMediaPlayer.PlayingState
            ),
        }
        self.player.stop()
        self.player.setSource(QUrl())
        QApplication.processEvents()
        return state

    def _restore_track_after_rename(self, path, state):
        if state is None:
            return
        self.current_track_path = Path(path).resolve()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.setPosition(max(0, int(state["position"])))
        if state["playing"]:
            self.player.play()
        else:
            self.player.pause()

    @staticmethod
    def _renamed_identity(source, clean_name):
        artist = "Unknown Artist"
        title = clean_name
        metadata_path = source.with_suffix(".json")
        metadata = {}
        if metadata_path.is_file():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    metadata = payload
            except (OSError, ValueError):
                metadata = {}
        artist = str(metadata.get("artist") or artist).strip() or artist
        if " - " in clean_name:
            entered_artist, entered_title = clean_name.split(" - ", 1)
            artist = entered_artist.strip() or artist
            title = entered_title.strip() or clean_name
        metadata["artist"] = artist
        metadata["title"] = title
        return artist, title, metadata

    @staticmethod
    def _rename_path(source, destination):
        last_error = None
        for _attempt in range(20):
            try:
                source.rename(destination)
                return
            except PermissionError as exc:
                last_error = exc
                QApplication.processEvents()
                time.sleep(0.05)
        if last_error:
            raise last_error

    @classmethod
    def _rename_track_files(cls, source, destination, clean_name):
        artist, title, metadata = cls._renamed_identity(source, clean_name)
        moves = [(source, destination)]
        for extension in (
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ):
            old = source.with_suffix(extension)
            if old.exists():
                moves.append((old, destination.with_suffix(extension)))
        for old, new in moves:
            if new.exists() and not cls._same_file(old, new):
                raise FileExistsError(f"{new.name} already exists")
        moved = []
        temporary = None
        try:
            for old, new in moves:
                cls._rename_path(old, new)
                moved.append((old, new))
            metadata_path = destination.with_suffix(".json")
            if any(new == metadata_path for _old, new in moved):
                temporary = metadata_path.with_name(
                    f".{metadata_path.name}.rename.tmp"
                )
                temporary.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(metadata_path)
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            for old, new in reversed(moved):
                if new.exists() and not old.exists():
                    try:
                        cls._rename_path(new, old)
                    except OSError:
                        pass
            raise
        return artist, title

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
