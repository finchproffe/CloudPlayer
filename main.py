import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from font_config import setup_hidpi_scaling

setup_hidpi_scaling()

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRectF, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from qasync import QEventLoop, asyncClose

from config import *
from account_sync import (
    AccountPanel,
    CloudRequestWorker,
    LoginDialog,
    clear_account_session,
    collect_playlist_tracks,
    load_account_session,
    local_playlist_urls,
    save_account_session,
)
from app_updater import (
    APP_VERSION, UPDATE_DOWNLOAD_PATH, ReleaseChecker, UpdateDialog,
    UpdateDownloader, file_sha256, read_update_state, version_parts,
    write_update_state,
)
from ui_polish import polish_tree
from font_config import setup_application_fonts
from group_sessions import GroupSessionWidget
from hotkeys import GlobalHotkeyThread
from network_sync_manager import NetworkSyncManager
from player_widgets import PlaylistView
from playlist_index import PlaylistSummaryLoader, flush_playlist_writes
from recommendation_widgets import FlowLayout, RecommendationCard
from smooth_scroll import SmoothScrollArea
from threads import BackgroundDownloader, RecommendationFetcher, SearchWorker
from utils import colored_icon, rounded_cover_pixmap
import discord_rpc
import config as config_module
from settings_dialog import SettingsDialog

MENU_ICON_SIZE = 28
MENU_TEXT_SIZE = 14
MENU_STYLE = f"""
QMenu {{background-color:{PANEL_BG};color:{TEXT_COLOR};border:1px solid {BUTTON_BORDER};border-radius:4px;padding:4px;font-size:{MENU_TEXT_SIZE}px;font-weight:700}}
QMenu::item {{background-color:transparent;padding:3px 10px 3px 8px;margin:0;border-radius:3px;min-height:18px}}
QMenu::item:selected {{background-color:{ACCENT_COLOR};color:#ffffff}}
QMenu::item:disabled {{color:{TEXT_MUTED}}}
QMenu::separator {{height:1px;margin:4px 6px;background:{BUTTON_BORDER}}}
QMenu::icon {{width:{MENU_ICON_SIZE}px;height:{MENU_ICON_SIZE}px}}
"""
GITHUB_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#ffffff" d="M12 .297a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.23c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.11-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.65 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.69.82.57A12 12 0 0 0 12 .297z"/></svg>"""
TELEGRAM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 496 512"><path fill="#ffffff" d="M248 8C111.033 8 0 119.033 0 256s111.033 248 248 248 248-111.033 248-248S384.967 8 248 8zm114.124 169.466-40.7 191.817c-3.07 13.666-11.08 17.036-22.477 10.602l-62-45.74-29.905 28.768c-3.312 3.312-6.089 6.089-12.488 6.089l4.451-63.196 115.007-103.886c5.003-4.451-1.092-6.935-7.77-2.484l-142.124 89.467-61.2-19.123c-13.304-4.147-13.564-13.304 2.777-19.702l239.093-92.203c11.08-4.147 20.774 2.484 17.336 19.591z"/></svg>"""


def make_menu(_parent=None):


    menu = QMenu()
    menu.setStyleSheet(MENU_STYLE)
    return menu


def make_svg_icon(source, logical_size=22):
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    size = max(1, round(logical_size * ratio))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class MusicPlayer(QMainWindow):
    PLAYLIST_COVER = 93
    PLAYLIST_GRID = QSize(124, 143)
    RECOMMENDATION_HEIGHT = 132

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CloudPlayer")
        self.resize(1100, 750)
        if (SCRIPT_DIR / "icon.ico").is_file():
            self.setWindowIcon(QIcon(str(SCRIPT_DIR / "icon.ico")))
        self.workers = []
        self.rec_cards = []
        self._active_download_keys = set()
        self._download_progress_dialogs = {}
        self._animation = None
        self.release_checker = None
        self.update_downloader = None
        self.update_progress = None
        self.update_state = read_update_state()
        self.latest_release = None
        self._manual_update_check = False
        self.account_user = None
        self._account_stats_worker = None
        self._account_stats_refresh_pending = False
        self._cloud_worker = None
        self._cloud_progress = None
        self._cloud_download_worker = None
        self._cloud_load_queue = []
        self._cloud_load_index = 0
        self._cloud_load_failures = []
        self._cloud_load_cancelled = False
        self._playlist_items = {}
        self._known_playlist_names = set()
        self._playlist_summaries = {}
        self._playlist_summary_loaders = set()
        self._playlist_list_generation = 0
        self._prepare_paths()
        self._build()
        self._restore_account()
        self.load_playlists()
        self.refresh_recommendation()
        self._start_hotkeys()
        polish_tree(self)
        discord_rpc.connect(self.playlist_view)
        QTimer.singleShot(900, self._check_for_updates)

    def _build(self):
        self.stack = QStackedWidget()
        self.playlist_view = PlaylistView(self)
        self.p2p = NetworkSyncManager(self.playlist_view.player, self)
        self.p2p.set_catalog_provider(self._track_catalog)
        self.p2p.catalog_received.connect(self._download_missing_tracks)
        self.group_view = GroupSessionWidget(self.p2p, self)
        self.home_view = self._home()
        for view in (self.home_view, self.playlist_view, self.group_view):
            self.stack.addWidget(view)
        self.setCentralWidget(self.stack)
        self.playlist_view.back_requested.connect(lambda: self._switch(0))
        self.group_view.back_requested.connect(lambda: self._switch(0))
        self.playlist_view.sync_requested.connect(self._send_sync)
        self.playlist_view.playlist_updated.connect(
            self.refresh_playlist_item
        )
        self._style()

    def _home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 34, 34, 34)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Your Playlists")
        title.setStyleSheet("font-size:28px;font-weight:700")
        account_title = QLabel("Account")
        account_title.setMinimumWidth(260)
        account_title.setStyleSheet("font-size:28px;font-weight:700")
        header.addWidget(title, 2)
        header.addWidget(account_title, 1)
        header.setStretch(0, 2)
        header.setStretch(1, 1)

        content = QHBoxLayout()
        content.setSpacing(10)
        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(10)
        self.playlist_list = QListWidget()
        self.playlist_list.setViewMode(QListWidget.IconMode)
        self.playlist_list.setIconSize(QSize(self.PLAYLIST_COVER, self.PLAYLIST_COVER))
        self.playlist_list.setGridSize(self.PLAYLIST_GRID)
        self.playlist_list.setResizeMode(QListWidget.Adjust)
        self.playlist_list.setMovement(QListWidget.Static)
        self.playlist_list.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.playlist_list.setWrapping(True)
        self.playlist_list.setWordWrap(True)
        self.playlist_list.setUniformItemSizes(True)
        self.playlist_list.setSpacing(9)
        self.playlist_list.itemDoubleClicked.connect(self.open_playlist)
        self.playlist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_list.customContextMenuRequested.connect(self._playlist_menu)
        self.rec_header = QLabel("Recommendations for You")
        self.rec_header.setStyleSheet("font-size:15px;font-weight:700;color:#ffffff;margin-top:6px")
        self.rec_scroll = SmoothScrollArea(duration=320, wheel_step=110)
        self.rec_scroll.setWidgetResizable(True)
        self.rec_scroll.setFixedHeight(self.RECOMMENDATION_HEIGHT)
        self.rec_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rec_container = QWidget()
        self.rec_flow = FlowLayout(self.rec_container, margin=0, spacing=12)
        self.rec_scroll.setWidget(self.rec_container)
        actions = QHBoxLayout()
        new_playlist = QPushButton("New Playlist")
        remove_playlist = QPushButton("Remove Playlist")
        search = QPushButton("Search")
        together = QPushButton("Listen Together")
        for button in (new_playlist, remove_playlist, search, together):
            button.setMinimumSize(130, 44)
            button.setMaximumWidth(170)
            actions.addWidget(button)
        actions.addStretch()
        new_playlist.clicked.connect(self.create_playlist)
        remove_playlist.clicked.connect(self.remove_playlist)
        search.clicked.connect(self.run_search)
        together.clicked.connect(lambda: self._switch(2))
        library_layout.addWidget(self.playlist_list, 3)
        library_layout.addWidget(self.rec_header)
        library_layout.addWidget(self.rec_scroll)
        library_layout.addSpacing(8)
        library_layout.addLayout(actions)

        self.account_panel = AccountPanel()
        self.account_panel.login_requested.connect(self._show_login)
        self.account_panel.synchronize_requested.connect(
            self._synchronize_playlist
        )
        self.account_panel.load_requested.connect(self._load_cloud_playlist)
        self.account_panel.unsync_requested.connect(
            self._unsynchronize_tracks
        )
        self.account_panel.logout_requested.connect(self._logout)
        content.addWidget(library, 2)
        content.addWidget(self.account_panel, 1)
        content.setStretch(0, 2)
        content.setStretch(1, 1)
        self.account_panel.setVisible(True)

        layout.addLayout(header)
        layout.addLayout(content, 1)
        layout.addLayout(self._social_layout())
        return page

    def _social_layout(self):
        row = QHBoxLayout()
        row.addStretch()
        style = f"QPushButton{{background:{BUTTON_BG};border:1px solid {BUTTON_BORDER};border-radius:8px;padding:0}}QPushButton:hover{{background:{BUTTON_HOVER};border-color:{ACCENT_COLOR}}}"
        self.settings_button = QPushButton()
        self.settings_button.setIcon(
            colored_icon("settings.svg", "#ffffff", 22)
        )
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.clicked.connect(self._show_settings_dialog)
        support = QPushButton()
        support.setIcon(colored_icon("money.svg", "#ffffff", 22))
        support.setToolTip("Support CloudPlayer")
        support.setAccessibleName("Support CloudPlayer")
        support.clicked.connect(self._show_donation_dialog)
        github = QPushButton()
        github.setIcon(make_svg_icon(GITHUB_SVG))
        github.setToolTip("Open GitHub")
        github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/finchproffe")))
        telegram = QPushButton()
        telegram.setIcon(make_svg_icon(TELEGRAM_SVG))
        telegram.setToolTip("Open Telegram")
        telegram.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://t.me/finchreleases")))
        self.download_button = QPushButton()
        self.download_button.setIcon(colored_icon("download.svg", "#ffffff", 22))
        self.download_button.setToolTip("Check for CloudPlayer updates")
        self.download_button.clicked.connect(self._manual_check_for_updates)
        for button in (
            self.settings_button,
            support,
            github,
            telegram,
            self.download_button,
        ):
            button.setFixedSize(42, 42)
            button.setIconSize(QSize(22, 22))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(style)
            row.addWidget(button)
        self._donation_button = support
        return row

    def _show_settings_dialog(self):
        username = str((self.account_user or {}).get("username") or "")
        dialog = SettingsDialog(
            ACCENT_COLOR,
            self,
            account_username=username,
        )
        dialog.delete_account_requested.connect(
            lambda: self._delete_account(dialog)
        )
        polish_tree(dialog)
        if dialog.exec() != QDialog.Accepted:
            return
        if not self._apply_accent_color(dialog.selected_color):
            QMessageBox.warning(
                self,
                "Settings",
                "The accent color could not be saved.",
            )

    def _delete_account(self, settings_dialog):
        if not self.account_user:
            return
        if (
            (self._cloud_worker and self._cloud_worker.isRunning())
            or (
                self._cloud_download_worker
                and self._cloud_download_worker.isRunning()
            )
        ):
            QMessageBox.information(
                self,
                "Delete Account",
                "Wait for the current cloud operation to finish.",
            )
            return
        username = self.account_user["username"]
        if (
            QMessageBox.question(
                settings_dialog,
                "Delete Account",
                f"Permanently delete '{username}' and all synchronized "
                "tracks? This cannot be undone.",
            )
            != QMessageBox.Yes
        ):
            return
        user_id = self.account_user["id"]
        self.account_user = None
        self._account_stats_refresh_pending = False
        clear_account_session()
        self.account_panel.set_logged_out()
        settings_dialog.reject()
        self._start_cloud_request(
            "delete_account",
            (user_id,),
            "Deleting account...",
            self._delete_account_finished,
        )

    def _delete_account_finished(self, ok, result):
        if not ok or result is not True:
            QMessageBox.critical(
                self,
                "Delete Account",
                f"You were signed out, but Supabase deletion failed:\n{result}",
            )
            return
        QMessageBox.information(
            self,
            "Delete Account",
            "Your account and synchronized tracks were deleted.",
        )

    def _apply_accent_color(self, color):
        global ACCENT_COLOR

        old_color = str(ACCENT_COLOR)
        new_color = config_module.save_accent_color(color)
        if not new_color:
            return False
        ACCENT_COLOR = new_color
        if old_color.casefold() == new_color.casefold():
            return True

        color_pattern = re.compile(re.escape(old_color), re.IGNORECASE)
        project_root = SCRIPT_DIR.resolve()
        for module in list(sys.modules.values()):
            module_file = getattr(module, "__file__", None)
            if not module_file:
                continue
            try:
                module_path = Path(module_file).resolve()
            except (OSError, TypeError):
                continue
            if project_root != module_path.parent and project_root not in module_path.parents:
                continue
            namespace = vars(module)
            if "ACCENT_COLOR" in namespace:
                namespace["ACCENT_COLOR"] = new_color
            for name, value in list(namespace.items()):
                if (
                    not name.isupper()
                    or name.startswith("DEFAULT_")
                    or not isinstance(value, str)
                    or not color_pattern.search(value)
                ):
                    continue
                namespace[name] = color_pattern.sub(new_color, value)

        for widget in QApplication.allWidgets():
            style = widget.styleSheet()
            if not style or not color_pattern.search(style):
                continue
            widget.setStyleSheet(color_pattern.sub(new_color, style))
            widget.update()

        self._style()
        polish_tree(self)
        self.update()
        return True

    def _show_donation_dialog(self):
        from dialogs import DonationDialog

        if not getattr(self, "_donation_dialog", None):
            self._donation_dialog = DonationDialog(self)
        self._donation_dialog.show()
        self._donation_dialog.raise_()
        self._donation_dialog.activateWindow()

    def _show_login(self):
        if self.account_user:
            return
        dialog = LoginDialog(self)
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted and dialog.authenticated_user:
            self._account_authenticated(dialog.authenticated_user)

    def _account_authenticated(self, user):
        self.account_user = {
            "id": str(user.get("id") or ""),
            "username": str(user.get("username") or ""),
        }
        self.account_panel.set_user(self.account_user)
        self.account_panel.set_song_count(None)
        self.account_panel.set_tracks([])
        self.account_panel.setVisible(True)
        self.account_panel.updateGeometry()
        self.account_panel.update()
        if self.home_view.layout():
            self.home_view.layout().activate()
        polish_tree(self.account_panel)
        save_account_session(self.account_user)
        self._refresh_account_stats()

    def _restore_account(self):
        user = load_account_session()
        if user:
            self._account_authenticated(user)

    def _refresh_account_stats(self):
        if not self.account_user:
            return
        if self._account_stats_worker is not None:
            self._account_stats_refresh_pending = True
            return
        user_id = self.account_user["id"]
        self._account_stats_refresh_pending = False
        worker = CloudRequestWorker("load_links", user_id, parent=self)
        self._account_stats_worker = worker
        worker.completed.connect(
            lambda ok, result, current=worker, owner=user_id: (
                self._account_stats_loaded(current, owner, ok, result)
            )
        )
        worker.finished.connect(
            lambda current=worker: self._account_stats_worker_finished(current)
        )
        worker.start()

    def _account_stats_loaded(self, worker, user_id, ok, result):
        if worker is not self._account_stats_worker:
            return
        self._account_stats_worker = None
        if not self.account_user or self.account_user["id"] != user_id:
            return
        if self._account_stats_refresh_pending:
            QTimer.singleShot(0, self._refresh_account_stats)
            return
        rows = result if ok and isinstance(result, list) else []
        self.account_panel.set_song_count(len(rows) if ok else None)
        self.account_panel.set_tracks(rows)

    def _account_stats_worker_finished(self, worker):
        worker.deleteLater()

    def _logout(self):
        if self._cloud_worker and self._cloud_worker.isRunning():
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Wait for the current cloud operation to finish.",
            )
            return
        if self._cloud_download_worker and self._cloud_download_worker.isRunning():
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Cancel or finish the current playlist download first.",
            )
            return
        self.account_user = None
        self._account_stats_refresh_pending = False
        clear_account_session()
        self.account_panel.set_logged_out()

    def _playlist_names(self):
        return sorted(self._known_playlist_names, key=str.casefold)

    def _choose_playlist(self, title, label, names):
        if not names:
            return None
        current_name = (
            str(self.playlist_list.currentItem().data(Qt.UserRole))
            if self.playlist_list.currentItem()
            else ""
        )
        current = names.index(current_name) if current_name in names else 0
        chosen, accepted = QInputDialog.getItem(
            self, title, label, names, current, False
        )
        return str(chosen) if accepted and chosen else None

    def _start_cloud_request(self, operation, arguments, label, callback):
        if (
            (self._cloud_worker and self._cloud_worker.isRunning())
            or (
                self._cloud_download_worker
                and self._cloud_download_worker.isRunning()
            )
        ):
            return
        self.account_panel.set_busy(True)
        progress = QProgressDialog(label, "", 0, 0, self)
        progress.setWindowTitle("Cloud Sync")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._cloud_progress = progress
        worker = CloudRequestWorker(operation, *arguments, parent=self)
        self._cloud_worker = worker
        worker.completed.connect(
            lambda ok, result, current=worker: self._cloud_request_done(
                current, callback, ok, result
            )
        )
        worker.finished.connect(worker.deleteLater)
        polish_tree(progress)
        progress.show()
        worker.start()

    def _cloud_request_done(self, worker, callback, ok, result):
        if worker is not self._cloud_worker:
            return
        self._cloud_worker = None
        if self._cloud_progress:
            self._cloud_progress.close()
            self._cloud_progress.deleteLater()
            self._cloud_progress = None
        self.account_panel.set_busy(False)
        callback(ok, result)

    def _synchronize_playlist(self):
        if not self.account_user:
            self._show_login()
            return
        playlist = self._choose_playlist(
            "Synchronize Playlist",
            "Playlist to synchronize:",
            self._playlist_names(),
        )
        if not playlist:
            return
        tracks, without_url = collect_playlist_tracks(playlist)
        if not tracks:
            message = "This playlist has no downloadable track links."
            if without_url:
                message += " Local files cannot be synchronized."
            QMessageBox.information(self, "Cloud Sync", message)
            return
        self._sync_without_url = without_url
        self._start_cloud_request(
            "synchronize",
            (self.account_user["id"], tracks),
            f"Synchronizing {playlist}...",
            self._synchronize_finished,
        )

    def _synchronize_finished(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Cloud Sync", str(result))
            return
        inserted = int(result.get("inserted") or 0)
        existing = int(result.get("already_synced") or 0)
        skipped = int(getattr(self, "_sync_without_url", 0) or 0)
        synchronized_links = result.get("links")
        if isinstance(synchronized_links, list):
            self.account_panel.set_song_count(
                int(result.get("total_synced") or len(synchronized_links))
            )
            self.account_panel.set_tracks(synchronized_links)
        else:
            self.account_panel.set_song_count(
                self.account_panel.song_count + inserted
            )
        parts = [
            f"Added: {inserted}",
            f"Already synchronized: {existing}",
        ]
        if skipped:
            parts.append(f"Skipped local files without a source link: {skipped}")
        self._refresh_account_stats()
        QMessageBox.information(self, "Cloud Sync", "\n".join(parts))

    def _unsynchronize_tracks(self, rows):
        if not self.account_user:
            return
        link_ids = [
            row.get("id")
            for row in rows or []
            if isinstance(row, dict) and row.get("id") is not None
        ]
        if not link_ids:
            return
        count = len(link_ids)
        description = (
            "this synchronized track"
            if count == 1
            else f"these {count} synchronized tracks"
        )
        if (
            QMessageBox.question(
                self,
                "Unsync",
                f"Unsync {description}?",
            )
            != QMessageBox.Yes
        ):
            return
        self._start_cloud_request(
            "unsynchronize",
            (self.account_user["id"], link_ids),
            f"Unsyncing {count} track(s)...",
            self._unsynchronize_finished,
        )

    def _unsynchronize_finished(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Unsync", str(result))
            return
        rows = result if isinstance(result, list) else []
        self.account_panel.set_song_count(len(rows))
        self.account_panel.set_tracks(rows)
        self._refresh_account_stats()

    def _load_cloud_playlist(self):
        if not self.account_user:
            self._show_login()
            return
        self._start_cloud_request(
            "load_links",
            (self.account_user["id"],),
            "Loading synchronized playlists...",
            self._cloud_links_loaded,
        )

    def _cloud_links_loaded(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Cloud Sync", str(result))
            return
        self.account_panel.set_song_count(len(result))
        self.account_panel.set_tracks(result)
        rows = [row for row in result if isinstance(row, dict) and row.get("url")]
        if not rows:
            QMessageBox.information(
                self, "Cloud Sync", "There are no synchronized tracks yet."
            )
            return
        playlist_names = sorted(
            {
                str(row.get("playlist_name") or "Cloud Playlist")
                for row in rows
            },
            key=str.casefold,
        )
        selected = self._choose_playlist(
            "Load Playlist",
            "Synchronized playlist to download:",
            playlist_names,
        )
        if not selected:
            return
        destination = self._ensure_playlist(selected)
        existing_urls = local_playlist_urls(destination)
        seen = set(existing_urls)
        queue = []
        for row in rows:
            remote_playlist = str(row.get("playlist_name") or "Cloud Playlist")
            url = str(row.get("url") or "").strip()
            if remote_playlist != selected or not url or url in seen:
                continue
            seen.add(url)
            queue.append(row)
        if not queue:
            QMessageBox.information(
                self,
                "Cloud Sync",
                "All tracks from this playlist are already downloaded.",
            )
            return
        self._cloud_load_playlist = destination
        self._cloud_load_queue = queue
        self._cloud_load_index = 0
        self._cloud_load_failures = []
        self._cloud_load_cancelled = False
        self.account_panel.set_busy(True)
        progress = QProgressDialog(
            "Preparing playlist download...", "Cancel", 0, 100, self
        )
        progress.setWindowTitle("Loading Playlist")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_cloud_load)
        self._cloud_progress = progress
        polish_tree(progress)
        progress.show()
        self._start_next_cloud_track()

    def _cancel_cloud_load(self):
        self._cloud_load_cancelled = True
        if self._cloud_progress:
            self._cloud_progress.setLabelText(
                "Cancelling after the current track..."
            )

    def _start_next_cloud_track(self):
        if self._cloud_load_cancelled:
            self._finish_cloud_load(cancelled=True)
            return
        if self._cloud_load_index >= len(self._cloud_load_queue):
            self._finish_cloud_load()
            return
        row = self._cloud_load_queue[self._cloud_load_index]
        worker = BackgroundDownloader(
            row["url"],
            PLAYLISTS_PATH / self._cloud_load_playlist / "songs",
            self,
        )
        self._cloud_download_worker = worker
        self.workers.append(worker)
        worker.progress_signal.connect(
            lambda percent, status, current=worker: self._cloud_track_progress(
                current, percent, status
            )
        )
        worker.finished_signal.connect(
            lambda ok, message, current=worker: self._cloud_track_done(
                current, ok, message
            )
        )
        worker.start()

    def _cloud_track_progress(self, worker, percent, status):
        if worker is not self._cloud_download_worker or not self._cloud_progress:
            return
        total = max(1, len(self._cloud_load_queue))
        track_number = self._cloud_load_index + 1
        row = self._cloud_load_queue[self._cloud_load_index]
        title = str(row.get("song_title") or "Track")
        self._cloud_progress.setLabelText(
            f"{track_number}/{total} — {title}\n{status}"
        )
        track_percent = max(0, min(100, int(percent or 0)))
        overall = round(
            (self._cloud_load_index + track_percent / 100) * 100 / total
        )
        self._cloud_progress.setValue(overall)

    def _cloud_track_done(self, worker, ok, message):
        if worker in self.workers:
            self.workers.remove(worker)
        if worker is not self._cloud_download_worker:
            return
        self._cloud_download_worker = None
        if not ok:
            row = self._cloud_load_queue[self._cloud_load_index]
            self._cloud_load_failures.append(
                (str(row.get("song_title") or row.get("url")), str(message))
            )
        self._cloud_load_index += 1
        if self._cloud_progress:
            self._cloud_progress.setValue(
                round(
                    self._cloud_load_index
                    * 100
                    / max(1, len(self._cloud_load_queue))
                )
            )
        QTimer.singleShot(0, self._start_next_cloud_track)

    def _finish_cloud_load(self, cancelled=False):
        downloaded = self._cloud_load_index - len(self._cloud_load_failures)
        total = len(self._cloud_load_queue)
        playlist = getattr(self, "_cloud_load_playlist", "")
        if self._cloud_progress:
            self._cloud_progress.close()
            self._cloud_progress.deleteLater()
            self._cloud_progress = None
        if playlist:
            self.refresh_playlist_item(playlist)
        self.account_panel.set_busy(False)
        self._cloud_load_queue = []
        if cancelled:
            QMessageBox.information(
                self,
                "Cloud Sync",
                f"Download cancelled. Completed: {downloaded}/{total}.",
            )
        elif self._cloud_load_failures:
            first_title, first_error = self._cloud_load_failures[0]
            QMessageBox.warning(
                self,
                "Cloud Sync",
                f"Downloaded: {downloaded}/{total}.\n"
                f"Failed: {len(self._cloud_load_failures)}.\n"
                f"First error ({first_title}): {first_error[:220]}",
            )
        else:
            QMessageBox.information(
                self,
                "Cloud Sync",
                f"Downloaded {downloaded} tracks to {playlist}.",
            )

    def _release_is_downloaded(self, release):
        return self.update_state.get("downloaded_version") == release["version"] and UPDATE_DOWNLOAD_PATH.is_file() and UPDATE_DOWNLOAD_PATH.stat().st_size == release["size"] and file_sha256(UPDATE_DOWNLOAD_PATH) == release["sha256"]

    def _release_is_acknowledged(self, release):
        known = max(version_parts(APP_VERSION), version_parts(self.update_state.get("acknowledged_version") or "0"), version_parts(self.update_state.get("downloaded_version") or "0"))
        return version_parts(release["version"]) <= known

    def _manual_check_for_updates(self):
        self._manual_update_check = True
        self._check_for_updates()

    def _check_for_updates(self):
        if self.release_checker and self.release_checker.isRunning():
            return
        self.release_checker = ReleaseChecker(self)
        self.release_checker.checked.connect(self._update_check_finished)
        self.release_checker.failed.connect(self._update_check_failed)
        self.release_checker.start()

    def _update_check_finished(self, release):
        self.latest_release = release
        self.update_state.update({"installed_version": APP_VERSION, "last_check_date": datetime.now(timezone.utc).isoformat(), "latest_version": release["version"], "latest_release_date": release.get("published_at")})
        write_update_state(self.update_state)
        if self._release_is_acknowledged(release):
            if self._manual_update_check:
                QMessageBox.information(self, "CloudPlayer Update", "You have the latest version installed.")
        elif self._release_is_downloaded(release):
            if self._manual_update_check:
                self._open_download_folder(UPDATE_DOWNLOAD_PATH)
        else:
            self._show_update_dialog(release)
        self._manual_update_check = False

    def _update_check_failed(self, message):
        if self._manual_update_check:
            QMessageBox.warning(self, "CloudPlayer Update", f"Could not check for updates.\n{message[:220]}")
        self._manual_update_check = False

    def _show_update_dialog(self, release):
        dialog = UpdateDialog(release, self)
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted:
            self._download_update(release)

    def _download_update(self, release):
        if self.update_downloader and self.update_downloader.isRunning():
            return
        self.update_progress = QProgressDialog("Downloading and verifying the update...", "Cancel", 0, 100, self)
        self.update_progress.setWindowModality(Qt.WindowModal)
        self.update_downloader = UpdateDownloader(release, self)
        self.update_downloader.progress.connect(self.update_progress.setValue)
        self.update_downloader.completed.connect(self._update_downloaded)
        self.update_downloader.failed.connect(self._update_download_failed)
        self.update_progress.canceled.connect(self.update_downloader.requestInterruption)
        self.update_downloader.start()

    def _update_downloaded(self, filename):
        if self.update_progress:
            self.update_progress.close()
        version = self.latest_release["version"] if self.latest_release else APP_VERSION
        self.update_state.update({"downloaded_version": version, "acknowledged_version": version, "downloaded_path": filename, "downloaded_date": datetime.now(timezone.utc).isoformat()})
        write_update_state(self.update_state)
        self._open_download_folder(filename)

    def _update_download_failed(self, message):
        if self.update_progress:
            self.update_progress.close()
        if message != "Download canceled":
            QMessageBox.critical(self, "CloudPlayer Update", f"The update was not downloaded.\n{message[:240]}")

    def _open_download_folder(self, filename):
        path = Path(filename).resolve()
        if not path.is_file():
            return
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def _style(self):
        self.setStyleSheet(f"""
        QMainWindow,QWidget{{background:{BG_COLOR};color:{TEXT_COLOR};font-family:'Segoe UI','Inter',sans-serif;font-weight:700}}
        QLabel{{font-weight:700}}
        QPushButton{{background:{BUTTON_BG};border:1px solid {BUTTON_BORDER};border-radius:4px;padding:10px 20px;font-size:15px;font-weight:700}}
        QPushButton:hover{{background:{BUTTON_HOVER};border-color:#444444}}
        QPushButton:pressed{{background:{ACCENT_COLOR}}}
        QLineEdit{{background:{PANEL_BG};border:1px solid {BUTTON_BORDER};border-radius:4px;padding:12px;color:{TEXT_COLOR}}}
        QListWidget{{background:{PANEL_BG};border:1px solid {BUTTON_BORDER};border-radius:4px;outline:0}}
        QListWidget::item{{background:transparent;border-radius:4px;padding:6px}}
        QListWidget::item:hover{{background:{BUTTON_HOVER}}}
        QListWidget::item:selected{{background:{ACCENT_COLOR};color:#ffffff}}
        QTextEdit{{background:{PANEL_BG};color:#cccccc;border:1px solid {BUTTON_BORDER};border-radius:4px;font-size:14px}}
        QSlider::groove:horizontal{{height:4px;background:{BUTTON_BORDER};border-radius:2px}}
        QSlider::handle:horizontal{{background:{ACCENT_COLOR};border-radius:6px;width:12px;margin:-4px 0}}
        QScrollArea{{border:none;background:transparent}}
        {MENU_STYLE}
        """)

    @staticmethod
    def _prepare_paths():
        DOCS_PATH.mkdir(parents=True, exist_ok=True)
        DOWNLOADS_PATH.mkdir(exist_ok=True)
        PLAYLISTS_PATH.mkdir(exist_ok=True)
        TEMP_PATH.mkdir(exist_ok=True)
        LYRICS_CACHE_PATH.mkdir(exist_ok=True)

    def _switch(self, index):
        if self.stack.currentIndex() == index:
            return
        self.stack.setCurrentIndex(index)
        target = self.stack.widget(index)
        effect = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(220)
        animation.setStartValue(0)
        animation.setEndValue(1)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(lambda: target.setGraphicsEffect(None))
        self._animation = animation
        animation.start()
        polish_tree(target)

    def _start_hotkeys(self):
        self.hotkeys = GlobalHotkeyThread(self)
        self.hotkeys.play_pause.connect(self.playlist_view.toggle_playback)
        self.hotkeys.previous.connect(self.playlist_view.play_prev_track)
        self.hotkeys.next.connect(self.playlist_view.play_next_track)
        self.hotkeys.start()

    def _send_sync(self, action, position):
        if self.p2p.role == "host" and self.p2p.is_connected:
            self.p2p.send(action, position)

    def _track_catalog(self):
        rows = []
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            url = data.get("source_url") or data.get("download_url")
            if url and str(url).startswith(("http://", "https://")):
                rows.append({"playlist": sidecar.parent.parent.name, "title": data.get("title") or sidecar.stem, "artist": data.get("artist") or "Unknown Artist", "source_url": url})
        return rows

    def _download_missing_tracks(self, catalog):


        self._room_catalog = [
            dict(track) for track in catalog if isinstance(track, dict)
        ]
        self.group_view.status.setText(
            "Catalog received. Tracks will stream and cache on demand."
        )

    def _sync_download_done(self, ok, message, worker, playlist):
        if worker in self.workers:
            self.workers.remove(worker)
        self.refresh_playlist_item(playlist)
        self.group_view.status.setText("P2P connected. Track downloaded." if ok else f"Track sync failed: {message[:140]}")

    def run_search(self):
        query, accepted = QInputDialog.getText(self, "Search SoundCloud", "Search SoundCloud:")
        if not accepted or not query.strip():
            return
        self.rec_header.setText(f"SoundCloud Results: {query.strip()}")
        self._clear_cards()
        self.rec_flow.addWidget(self._message("Searching SoundCloud..."))
        worker = SearchWorker(query.strip(), self)
        self.workers.append(worker)
        worker.results_ready.connect(lambda rows, current=worker: self._show_cards(rows, current))
        worker.start()

    def refresh_recommendation(self):
        self.rec_header.setText("Recommendations for You")
        self._clear_cards()
        self.rec_flow.addWidget(self._message("Finding Genius recommendations..."))
        worker = RecommendationFetcher(self)
        self.workers.append(worker)
        worker.rec_ready.connect(lambda rows, current=worker: self._show_cards(rows, current))
        worker.start()

    def _show_cards(self, rows, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        self._clear_cards()
        if not rows:
            self.rec_flow.addWidget(self._message("No results found."))
            return
        for row in rows:
            card = RecommendationCard(row)
            card.play_requested.connect(lambda data, current=card: self._download_recommendation(data, current, "Recommendations", True))
            card.add_requested.connect(self._add_menu)
            self.rec_flow.addWidget(card)
            self.rec_cards.append(card)
            polish_tree(card)

    def _clear_cards(self):
        while self.rec_flow.count():
            item = self.rec_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rec_cards.clear()

    @staticmethod
    def _message(text):
        label = QLabel(text)
        label.setStyleSheet(f"color:{TEXT_MUTED};font-size:13px;padding:12px")
        return label

    def _add_menu(self, recommendation, button):
        if not self.playlist_list.count():
            return
        menu = make_menu(self)
        for index in range(self.playlist_list.count()):
            menu.addAction(self.playlist_list.item(index).data(Qt.UserRole))
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen:
            card = next((card for card in self.rec_cards if card.rec is recommendation), None)
            self._download_recommendation(recommendation, card, chosen.text(), False)

    def _download_recommendation(self, recommendation, card, playlist, autoplay):
        self._ensure_playlist(playlist)
        query = recommendation.get("source_url") or recommendation.get("url") or f"{recommendation.get('artist', '')} {recommendation.get('title', '')}"
        key = str(query).strip().casefold()
        if key in self._active_download_keys:
            return
        self._active_download_keys.add(key)
        if card:
            self._remove_recommendation_card(card)
        worker = BackgroundDownloader(query, PLAYLISTS_PATH / playlist / "songs", self)
        self.workers.append(worker)
        progress = QProgressDialog(
            "Preparing download...", "", 0, 100, self
        )
        progress.setWindowTitle("Downloading Track")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setRange(0, 0)
        self._download_progress_dialogs[worker] = progress
        worker.progress_signal.connect(
            lambda percent, status, dialog=progress: self._set_download_progress(
                dialog, percent, status
            )
        )
        worker.finished_signal.connect(
            lambda ok, message, current=worker: self._recommendation_done(
                ok,
                message,
                current,
                key,
                playlist,
                autoplay,
            )
        )
        polish_tree(progress)
        progress.show()
        worker.start()

    def _remove_recommendation_card(self, card):
        for index in range(self.rec_flow.count()):
            item = self.rec_flow.itemAt(index)
            if item and item.widget() is card:
                self.rec_flow.takeAt(index)
                break
        if card in self.rec_cards:
            self.rec_cards.remove(card)
        card.hide()
        card.deleteLater()
        self.rec_container.updateGeometry()

    @staticmethod
    def _set_download_progress(dialog, percent, status):
        dialog.setLabelText(status)
        if percent <= 0:
            dialog.setRange(0, 0)
            return
        if dialog.maximum() == 0:
            dialog.setRange(0, 100)
        dialog.setValue(percent)

    def _recommendation_done(
        self, ok, message, worker, key, playlist, autoplay
    ):
        if worker in self.workers:
            self.workers.remove(worker)
        self._active_download_keys.discard(key)
        progress = self._download_progress_dialogs.pop(worker, None)
        if progress:
            progress.close()
            progress.deleteLater()
        self.refresh_playlist_item(playlist)
        if not ok:
            QMessageBox.critical(self, "SoundCloud Download Error", message)
        elif autoplay and worker.last_downloaded_path:
            self.playlist_view.load_playlist(playlist)
            self._switch(1)
            self.playlist_view.play_file(worker.last_downloaded_path)

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
        prompt = (
            f"Remove '{names[0]}' and its tracks?"
            if len(names) == 1
            else f"Remove {len(names)} selected playlists and all their tracks?"
        )
        if QMessageBox.question(self, "Remove Playlist", prompt) != QMessageBox.Yes:
            return
        failures = []
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
        for _attempt in range(5):
            try:
                shutil.rmtree(folder)
                return
            except PermissionError as exc:
                last_error = exc
                QApplication.processEvents()
                time.sleep(0.04)
        if last_error:
            raise last_error

    @asyncClose
    async def closeEvent(self, event):
        cloud_request_running = (
            self._cloud_worker and self._cloud_worker.isRunning()
        )
        cloud_download_running = (
            self._cloud_download_worker
            and self._cloud_download_worker.isRunning()
        )
        stats_request_running = (
            self._account_stats_worker
            and self._account_stats_worker.isRunning()
        )
        if (
            cloud_request_running
            or cloud_download_running
            or stats_request_running
        ):
            if cloud_download_running:
                self._cloud_load_cancelled = True
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Cloud sync is still running. It will be safe to close "
                "after the current track or request finishes.",
            )
            event.ignore()
            return
        self.playlist_view.persist_volume()
        self.playlist_view.cancel_playlist_loading()
        for loader in tuple(self.playlist_view._playlist_loaders):
            loader.requestInterruption()
            loader.wait(1000)
        for loader in tuple(self._playlist_summary_loaders):
            loader.requestInterruption()
            loader.wait(1000)
        flush_playlist_writes()
        self.hotkeys.stop()
        self.hotkeys.wait(1000)
        if self.release_checker and self.release_checker.isRunning():
            self.release_checker.requestInterruption()
            self.release_checker.wait(1000)
        if self.update_downloader and self.update_downloader.isRunning():
            self.update_downloader.requestInterruption()
            self.update_downloader.wait(3000)
        await self.p2p.close()
        discord_rpc.close()
        event.accept()


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    setup_application_fonts(app)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MusicPlayer()
    polish_tree(window)
    window.show()
    app.aboutToQuit.connect(loop.stop)
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    run()
