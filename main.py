import asyncio
import sys

import config as config_module
from debug_console import (
    install_qt_message_capture,
    set_debug_console,
)

set_debug_console(config_module.DEBUG_ENABLED)

from font_config import setup_hidpi_scaling

setup_hidpi_scaling()

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QSize, QTimer, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QAbstractItemView, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from qasync import QEventLoop, asyncClose

install_qt_message_capture()

from config import (
    ACCENT_COLOR,
    BG_COLOR,
    BUTTON_BG,
    BUTTON_BORDER,
    BUTTON_HOVER,
    DOCS_PATH,
    DOWNLOADS_PATH,
    LYRICS_CACHE_PATH,
    PANEL_BG,
    PLAYLISTS_PATH,
    SCRIPT_DIR,
    TEMP_PATH,
    TEXT_COLOR,
)
from dropdown_ui import QMessageBox
from account_sync import AccountPanel
from app_updater import (
    acknowledge_update_startup,
    consume_update_token,
    read_update_state,
)
from ui_polish import polish_tree
from font_config import setup_application_fonts
from group_sessions import GroupSessionWidget
from hotkeys import GlobalHotkeyThread
from network_sync_manager import NetworkSyncManager
from player_widgets import PlaylistView
from playlist_index import flush_playlist_writes
from recommendation_widgets import FlowLayout
from smooth_scroll import SmoothScrollArea
from thumbnail_toolbar import ThumbnailToolbar
from utils import colored_icon
import discord_rpc
from main_account import AccountMixin
from main_cloud import CloudSyncMixin
from main_common import MENU_ICON_SIZE, MENU_STYLE, make_menu
from main_discovery import DiscoveryMixin
from main_library import LibraryMixin
from main_updates import UpdateMixin

GITHUB_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#ffffff" d="M12 .297a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.23c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.21.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.11-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.65 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.69.82.57A12 12 0 0 0 12 .297z"/></svg>"""
TELEGRAM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 496 512"><path fill="#ffffff" d="M248 8C111.033 8 0 119.033 0 256s111.033 248 248 248 248-111.033 248-248S384.967 8 248 8zm114.124 169.466-40.7 191.817c-3.07 13.666-11.08 17.036-22.477 10.602l-62-45.74-29.905 28.768c-3.312 3.312-6.089 6.089-12.488 6.089l4.451-63.196 115.007-103.886c5.003-4.451-1.092-6.935-7.77-2.484l-142.124 89.467-61.2-19.123c-13.304-4.147-13.564-13.304 2.777-19.702l239.093-92.203c11.08-4.147 20.774 2.484 17.336 19.591z"/></svg>"""


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


class MusicPlayer(
    AccountMixin,
    CloudSyncMixin,
    UpdateMixin,
    DiscoveryMixin,
    LibraryMixin,
    QMainWindow,
):
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
        self._pending_deleted_cloud_user_id = None
        self._pending_deleted_playlists = set()
        self._pending_deleted_tracks = set()
        self._playlist_items = {}
        self._known_playlist_names = set()
        self._playlist_summaries = {}
        self._playlist_summary_loaders = set()
        self._playlist_list_generation = 0
        self._prepare_paths()
        self._build()
        self.thumbnail_toolbar = ThumbnailToolbar(self, self.playlist_view)
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
        self.playlist_view.tracks_deleted.connect(
            self._local_tracks_deleted
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
        github.setToolTip("GitHub")
        github.clicked.connect(
            lambda: self._show_link_menu(
                github, "GitHub", "https://github.com/finchproffe"
            )
        )
        telegram = QPushButton()
        telegram.setIcon(make_svg_icon(TELEGRAM_SVG))
        telegram.setToolTip("Telegram")
        telegram.clicked.connect(
            lambda: self._show_link_menu(
                telegram, "Telegram", "https://t.me/finchreleases"
            )
        )
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

    def _show_link_menu(self, button, title, url):
        menu = make_menu(button)
        heading = menu.addAction(title)
        heading.setEnabled(False)
        address = menu.addAction(url)
        address.setEnabled(False)
        menu.addSeparator()
        copy_link = menu.addAction(
            colored_icon("copy.svg", size=MENU_ICON_SIZE), "Copy Link"
        )
        chosen = menu.exec(button.mapToGlobal(button.rect().topRight()))
        if chosen is copy_link:
            QApplication.clipboard().setText(url)



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
        if self.thumbnail_toolbar is not None:
            self.thumbnail_toolbar.close()
        discord_rpc.close()
        event.accept()


def run():
    arguments, update_token = consume_update_token(sys.argv)
    sys.argv = arguments
    app = QApplication(arguments)
    app.setStyle("Fusion")
    setup_application_fonts(app)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MusicPlayer()
    polish_tree(window)
    window.show()
    if update_token:
        QTimer.singleShot(
            750,
            lambda token=update_token: acknowledge_update_startup(token),
        )
    app.aboutToQuit.connect(loop.stop)
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    run()
