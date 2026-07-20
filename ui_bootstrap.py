from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QPushButton,
)

from config import PLAYLISTS_PATH
from dropdown_ui import QFileDialog, QInputDialog, QMessageBox
from playlist_index import flush_playlist_writes

_INSTALLED = False
_ORIGINAL_APP_INIT = None


def install():
    global _INSTALLED, _ORIGINAL_APP_INIT
    if _INSTALLED:
        return
    _INSTALLED = True
    if QApplication.instance() is not None:
        _schedule()
        return
    _ORIGINAL_APP_INIT = QApplication.__init__

    def app_init(self, *args, **kwargs):
        _ORIGINAL_APP_INIT(self, *args, **kwargs)
        _schedule()

    QApplication.__init__ = app_init


def _schedule():
    QTimer.singleShot(0, _apply)
    QTimer.singleShot(700, _apply)


def _apply():
    _install_cover_menu()
    _inject_donation_buttons()
    _install_playlist_menus()


def _install_cover_menu():
    try:
        from player_widgets import CoverPreviewDialog, PlaylistView, make_menu
        from utils import colored_icon
    except Exception:
        return
    if getattr(PlaylistView, "_copy_cover_action_installed", False):
        return

    def cover_menu(self, position):
        menu = make_menu(self)
        view = menu.addAction(colored_icon("view.svg", size=28), "View Full Size")
        save = menu.addAction(colored_icon("download.svg", size=28), "Download Cover")
        copy = menu.addAction(colored_icon("copy.svg", size=28), "Copy Cover")
        pixmap = getattr(self, "current_cover_pixmap", None)
        has_cover = pixmap is not None and not pixmap.isNull()
        for action in (view, save, copy):
            action.setEnabled(has_cover)
        chosen = menu.exec(self.cover_label.mapToGlobal(position))
        if chosen is view:
            CoverPreviewDialog(pixmap, self).exec()
        elif chosen is save:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Cover", "cover.jpg", "Images (*.jpg *.jpeg *.png)"
            )
            if path:
                pixmap.save(path)
        elif chosen is copy:
            QGuiApplication.clipboard().setPixmap(pixmap)

    PlaylistView._cover_menu = cover_menu
    PlaylistView._copy_cover_action_installed = True


def _inject_donation_buttons():
    try:
        from dialogs import DonationDialog
        from utils import colored_icon
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    for window in app.topLevelWidgets():
        if getattr(window, "_donation_button", None):
            continue
        github = next((b for b in window.findChildren(QPushButton)
                       if b.toolTip() == "Open GitHub"), None)
        if github is None or github.parentWidget().layout() is None:
            continue
        layout = github.parentWidget().layout()
        index = layout.indexOf(github)
        if index < 0:
            continue
        button = QPushButton(github.parentWidget())
        button.setIcon(colored_icon("money.svg", "#ffffff", 22))
        button.setIconSize(github.iconSize())
        button.setFixedSize(github.size())
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Support CloudPlayer")
        button.setStyleSheet(github.styleSheet())
        layout.insertWidget(index, button)
        dialog = DonationDialog(window)
        button.clicked.connect(dialog.show)
        window._donation_button = button
        window._donation_dialog = dialog


def _clean_playlist_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100]


def _rename_playlist(window, item):
    old_name = str(item.data(Qt.UserRole) or "")
    new_name, accepted = QInputDialog.getText(
        window, "Rename Playlist", "New name:", text=old_name
    )
    if not accepted:
        return
    new_name = _clean_playlist_name(new_name)
    if not new_name or new_name == old_name:
        return
    old_folder = PLAYLISTS_PATH / old_name
    new_folder = PLAYLISTS_PATH / new_name
    old_metadata = PLAYLISTS_PATH / f"{old_name}.json"
    new_metadata = PLAYLISTS_PATH / f"{new_name}.json"
    if new_folder.exists() or new_metadata.exists():
        QMessageBox.warning(window, "Rename Playlist", "That playlist already exists.")
        return
    view = window.playlist_view
    try:
        flush_playlist_writes()
        view.forget_playlist(old_name)
        if old_folder.exists():
            old_folder.rename(new_folder)
        if old_metadata.exists():
            old_metadata.rename(new_metadata)
        data = {}
        if new_metadata.exists():
            try:
                data = json.loads(new_metadata.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["name"] = new_name
        if "songs" not in data:
            data.setdefault("song_count", 0)
        new_metadata.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        item.setData(Qt.UserRole, new_name)
        window._playlist_items.pop(old_name, None)
        window._playlist_items[new_name] = item
        window._known_playlist_names.discard(old_name)
        window._known_playlist_names.add(new_name)
        if old_name in window._playlist_summaries:
            count, first_track = window._playlist_summaries.pop(old_name)
            if first_track:
                first_track = str(
                    new_folder / "songs" / Path(first_track).name
                )
            window._playlist_summaries[new_name] = (count, first_track)
        window._refresh_playlist_item(item)
        if view.current_playlist == old_name:
            view.current_playlist = new_name
            view.current_playlist_path = new_folder / "songs"
            view._playlist_metadata["name"] = new_name
            view._write_song_order(list(view._playlist_order))
            view.playlist_name.setText(new_name)
    except Exception as exc:
        if new_folder.exists() and not old_folder.exists():
            try:
                new_folder.rename(old_folder)
            except Exception:
                pass
        if view.current_playlist == old_name:
            view._write_song_order(list(view._playlist_order))
        QMessageBox.critical(window, "Rename Playlist", str(exc))


def _install_playlist_menus():
    try:
        from main import make_menu
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    for window in app.topLevelWidgets():
        playlist_list = getattr(window, "playlist_list", None)
        if playlist_list is None or getattr(window, "_rename_menu_installed", False):
            continue
        try:
            playlist_list.customContextMenuRequested.disconnect()
        except Exception:
            pass

        def show_menu(position, current_window=window, widget=playlist_list):
            item = widget.itemAt(position) or widget.currentItem()
            if not item:
                return
            if not item.isSelected():
                widget.clearSelection()
                item.setSelected(True)
            items = widget.selectedItems()
            single_selection = len(items) == 1
            name = item.data(Qt.UserRole)
            menu = make_menu(current_window)
            open_action = menu.addAction("Open")
            rename_action = menu.addAction("Rename")
            set_cover = menu.addAction("Set Cover Image...")
            reset_cover = menu.addAction("Reset to Auto")
            menu.addSeparator()
            remove_action = menu.addAction("Remove Playlist")
            for action in (open_action, rename_action, set_cover):
                action.setEnabled(single_selection)
            chosen = menu.exec(widget.viewport().mapToGlobal(position))
            if chosen is open_action:
                current_window.open_playlist(item)
            elif chosen is rename_action:
                _rename_playlist(current_window, item)
            elif chosen is set_cover:
                filename, _ = QFileDialog.getOpenFileName(
                    current_window, "Choose Cover", "",
                    "Images (*.png *.jpg *.jpeg *.webp)"
                )
                if filename:
                    current_window._reset_cover(name)
                    shutil.copy2(
                        filename,
                        PLAYLISTS_PATH / name / f"cover{Path(filename).suffix.lower()}",
                    )
                    current_window._refresh_playlist_item(item)
            elif chosen is reset_cover:
                for selected in items:
                    selected_name = selected.data(Qt.UserRole)
                    current_window._reset_cover(selected_name)
                    current_window._refresh_playlist_item(selected)
            elif chosen is remove_action:
                current_window.remove_playlist(items)

        playlist_list.customContextMenuRequested.connect(show_menu)
        window._rename_menu_installed = True
