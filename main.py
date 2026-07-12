import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from font_config import setup_hidpi_scaling

setup_hidpi_scaling()

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from qasync import QEventLoop, asyncClose

from config import *
from font_config import setup_application_fonts
from group_sessions import GroupSessionWidget
from hotkeys import GlobalHotkeyThread
from p2p_sync_manager import P2PSyncManager
from player_widgets import PlaylistView
from recommendation_widgets import FlowLayout, RecommendationCard
from smooth_scroll import SmoothScrollArea
from threads import BackgroundDownloader, RecommendationFetcher, SearchWorker
from utils import colored_icon, rounded_cover_pixmap
import discord_rpc

APP_VERSION = "1.1.0"
RELEASE_API_URL = "https://api.github.com/repos/finchproffe/CloudPlayer/releases/latest"
UPDATE_STATE_PATH = DOCS_PATH / "update_state.json"
UPDATE_DOWNLOAD_PATH = DOWNLOADS_PATH / "CloudPlayer.exe"
FONT_WEIGHT = QFont.Weight.Bold
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


def make_menu(parent):
    menu = QMenu(parent)
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


def version_parts(value):
    parts = [int(part) for part in re.findall(r"\d+", str(value))]
    return tuple((parts + [0, 0, 0, 0])[:4])


def write_update_state(data):
    DOCS_PATH.mkdir(parents=True, exist_ok=True)
    temporary = UPDATE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(UPDATE_STATE_PATH)


def read_update_state():
    if UPDATE_STATE_PATH.is_file():
        try:
            value = json.loads(UPDATE_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    value = {
        "first_run_date": datetime.now(timezone.utc).isoformat(),
        "installed_version": APP_VERSION,
        "last_check_date": None,
        "latest_version": APP_VERSION,
        "latest_release_date": None,
        "downloaded_version": None,
        "downloaded_path": None,
        "acknowledged_version": APP_VERSION,
    }
    write_update_state(value)
    return value


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower()
    except Exception:
        return ""


class ReleaseChecker(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            request = urllib.request.Request(RELEASE_API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"CloudPlayer/{APP_VERSION}", "X-GitHub-Api-Version": "2022-11-28"})
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            tag = str(payload.get("tag_name") or "").strip()
            asset = next((item for item in payload.get("assets") or [] if str(item.get("name") or "").casefold() == "cloudplayer.exe"), None)
            if not asset:
                raise RuntimeError("CloudPlayer.exe is missing from the latest release")
            url = str(asset.get("browser_download_url") or "")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
                raise RuntimeError("The release contains an invalid download address")
            digest = str(asset.get("digest") or "")
            if not digest.lower().startswith("sha256:"):
                raise RuntimeError("GitHub did not provide a SHA-256 digest")
            size = int(asset.get("size") or 0)
            if size <= 0 or size > 1024 * 1024 * 1024:
                raise RuntimeError("The release file size is invalid")
            self.checked.emit({"version": tag.lstrip("vV") or APP_VERSION, "published_at": payload.get("published_at"), "download_url": url, "sha256": digest.split(":", 1)[1].lower(), "size": size})
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDownloader(QThread):
    progress = Signal(int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self):
        temporary = UPDATE_DOWNLOAD_PATH.with_suffix(".part")
        try:
            DOWNLOADS_PATH.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            UPDATE_DOWNLOAD_PATH.unlink(missing_ok=True)
            request = urllib.request.Request(self.release["download_url"], headers={"User-Agent": f"CloudPlayer/{APP_VERSION}"})
            digest = hashlib.sha256()
            received = 0
            expected = int(self.release["size"])
            with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                while True:
                    if self.isInterruptionRequested():
                        raise RuntimeError("Download canceled")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected:
                        raise RuntimeError("The downloaded file is too large")
                    digest.update(chunk)
                    output.write(chunk)
                    self.progress.emit(min(100, round(received * 100 / expected)))
            if received != expected:
                raise RuntimeError("The downloaded file is incomplete")
            if digest.hexdigest().lower() != self.release["sha256"]:
                raise RuntimeError("SHA-256 verification failed")
            temporary.replace(UPDATE_DOWNLOAD_PATH)
            self.completed.emit(str(UPDATE_DOWNLOAD_PATH))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CloudPlayer Update")
        self.setModal(True)
        self.setFixedWidth(430)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)
        title = QLabel("Доступна новая версия")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#ffffff")
        text = QLabel(f"CloudPlayer {release['version']} готов к скачиванию.")
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size:14px;color:{TEXT_MUTED}")
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        download = QPushButton("Скачать")
        cancel.clicked.connect(self.reject)
        download.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(download)
        root.addWidget(title)
        root.addWidget(text)
        root.addLayout(buttons)


def _polish(widget):
    font = widget.font()
    font.setWeight(FONT_WEIGHT)
    font.setBold(True)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    widget.setFont(font)
    style = re.sub(r"font-weight\s*:\s*(?:bold|normal|[1-9]00)", "font-weight:700", widget.styleSheet(), flags=re.I)
    if widget.metaObject().className() in {"TrackListItemWidget", "TrackRow"}:
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setAutoFillBackground(False)
        style += ";background:transparent;border:none"
        for child in widget.findChildren(QWidget):
            child.setAutoFillBackground(False)
            child.setStyleSheet(child.styleSheet() + ";background:transparent;border:none")
    widget.setStyleSheet(style)


class UiFilter(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildAdded and isinstance(event.child(), QWidget):
            QTimer.singleShot(0, lambda child=event.child(): polish_tree(child))
        return False


_ui_filter = UiFilter()


def polish_tree(root):
    if not isinstance(root, QWidget):
        return
    _polish(root)
    root.installEventFilter(_ui_filter)
    for widget in root.findChildren(QWidget):
        _polish(widget)
        widget.installEventFilter(_ui_filter)


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
        self._animation = None
        self.release_checker = None
        self.update_downloader = None
        self.update_progress = None
        self.update_state = read_update_state()
        self.latest_release = None
        self._manual_update_check = False
        self._prepare_paths()
        self._build()
        self.load_playlists()
        self.refresh_recommendation()
        self._start_hotkeys()
        polish_tree(self)
        discord_rpc.connect(self.playlist_view)
        QTimer.singleShot(900, self._check_for_updates)

    def _build(self):
        self.stack = QStackedWidget()
        self.playlist_view = PlaylistView(self)
        self.p2p = P2PSyncManager(self.playlist_view.player, self)
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
        self._style()

    def _home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 34, 34, 34)
        layout.setSpacing(10)
        title = QLabel("Your Playlists")
        title.setStyleSheet("font-size:28px;font-weight:700;margin-bottom:10px")
        self.playlist_list = QListWidget()
        self.playlist_list.setViewMode(QListWidget.IconMode)
        self.playlist_list.setIconSize(QSize(self.PLAYLIST_COVER, self.PLAYLIST_COVER))
        self.playlist_list.setGridSize(self.PLAYLIST_GRID)
        self.playlist_list.setResizeMode(QListWidget.Adjust)
        self.playlist_list.setMovement(QListWidget.Static)
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
        search = QPushButton("Search SoundCloud")
        together = QPushButton("Listen Together")
        for button in (new_playlist, remove_playlist, search, together):
            button.setFixedSize(170, 44)
            actions.addWidget(button)
        actions.addStretch()
        new_playlist.clicked.connect(self.create_playlist)
        remove_playlist.clicked.connect(self.remove_playlist)
        search.clicked.connect(self.run_search)
        together.clicked.connect(lambda: self._switch(2))
        layout.addWidget(title)
        layout.addWidget(self.playlist_list, 3)
        layout.addWidget(self.rec_header)
        layout.addWidget(self.rec_scroll)
        layout.addSpacing(8)
        layout.addLayout(actions)
        layout.addLayout(self._social_layout())
        return page

    def _social_layout(self):
        row = QHBoxLayout()
        row.addStretch()
        style = f"QPushButton{{background:{BUTTON_BG};border:1px solid {BUTTON_BORDER};border-radius:8px;padding:0}}QPushButton:hover{{background:{BUTTON_HOVER};border-color:{ACCENT_COLOR}}}"
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
        for button in (github, telegram, self.download_button):
            button.setFixedSize(42, 42)
            button.setIconSize(QSize(22, 22))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(style)
            row.addWidget(button)
        return row

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
                QMessageBox.information(self, "CloudPlayer Update", "У вас установлена последняя версия.")
        elif self._release_is_downloaded(release):
            if self._manual_update_check:
                self._open_download_folder(UPDATE_DOWNLOAD_PATH)
        else:
            self._show_update_dialog(release)
        self._manual_update_check = False

    def _update_check_failed(self, message):
        if self._manual_update_check:
            QMessageBox.warning(self, "CloudPlayer Update", f"Не удалось проверить обновления.\n{message[:220]}")
        self._manual_update_check = False

    def _show_update_dialog(self, release):
        dialog = UpdateDialog(release, self)
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted:
            self._download_update(release)

    def _download_update(self, release):
        if self.update_downloader and self.update_downloader.isRunning():
            return
        self.update_progress = QProgressDialog("Скачивание и проверка обновления...", "Отмена", 0, 100, self)
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
            QMessageBox.critical(self, "CloudPlayer Update", f"Обновление не скачано.\n{message[:240]}")

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
        existing = set()
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                existing.add(str(data.get("source_url") or data.get("download_url") or ""))
            except Exception:
                pass
        missing = [track for track in catalog if str(track.get("source_url") or "") not in existing]
        for track in missing:
            playlist = str(track.get("playlist") or "Listen Together")
            self._ensure_playlist(playlist)
            worker = BackgroundDownloader(track["source_url"], PLAYLISTS_PATH / playlist / "songs", self)
            self.workers.append(worker)
            worker.finished_signal.connect(lambda ok, message, current=worker, name=playlist: self._sync_download_done(ok, message, current, name))
            worker.start()

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
        if card:
            card.set_loading(True)
        query = recommendation.get("source_url") or recommendation.get("url") or f"{recommendation.get('artist', '')} {recommendation.get('title', '')}"
        worker = BackgroundDownloader(query, PLAYLISTS_PATH / playlist / "songs", self)
        self.workers.append(worker)
        worker.finished_signal.connect(lambda ok, message, current=worker: self._recommendation_done(ok, message, current, card, playlist, autoplay))
        worker.start()

    def _recommendation_done(self, ok, message, worker, card, playlist, autoplay):
        if worker in self.workers:
            self.workers.remove(worker)
        if card:
            card.set_loading(False)
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
            metadata.write_text(json.dumps({"name": name, "songs": []}), encoding="utf-8")
        if not any(self.playlist_list.item(i).data(Qt.UserRole) == name for i in range(self.playlist_list.count())):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            self.playlist_list.addItem(item)
            self._refresh_playlist_item(item)

    def create_playlist(self):
        name, accepted = QInputDialog.getText(self, "New Playlist", "Name:")
        if accepted and name.strip():
            self._ensure_playlist(name.strip())

    def load_playlists(self):
        self.playlist_list.clear()
        for metadata in sorted(PLAYLISTS_PATH.glob("*.json")):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, metadata.stem)
            self.playlist_list.addItem(item)
            self._refresh_playlist_item(item)

    def open_playlist(self, item):
        self.playlist_view.load_playlist(item.data(Qt.UserRole))
        self._switch(1)

    def _refresh_playlist_item(self, item):
        name = item.data(Qt.UserRole)
        songs = [file for file in (PLAYLISTS_PATH / name / "songs").glob("*") if file.suffix.lower() in AUDIO_EXTENSIONS]
        cover = self._playlist_cover(name, songs)
        rendered = rounded_cover_pixmap(cover, self.PLAYLIST_COVER, 9) if cover else self._placeholder(self.PLAYLIST_COVER)
        item.setIcon(QIcon(rendered))
        item.setText(f"{name}\n{len(songs)} {'song' if len(songs) == 1 else 'songs'}")

    def refresh_playlist_item(self, name):
        for index in range(self.playlist_list.count()):
            item = self.playlist_list.item(index)
            if item.data(Qt.UserRole) == name:
                self._refresh_playlist_item(item)
                break

    @staticmethod
    def _playlist_cover(name, songs):
        folder = PLAYLISTS_PATH / name
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            path = folder / f"cover{extension}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap
        if songs:
            for extension in (".jpg", ".jpeg", ".png", ".webp"):
                path = sorted(songs)[0].with_suffix(extension)
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
        self.playlist_list.setCurrentItem(item)
        name = item.data(Qt.UserRole)
        menu = make_menu(self)
        open_action = menu.addAction("Open")
        set_cover = menu.addAction("Set Cover Image...")
        reset_cover = menu.addAction("Reset to Auto")
        menu.addSeparator()
        remove_action = menu.addAction("Remove Playlist")
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
            self._reset_cover(name)
            self._refresh_playlist_item(item)
        elif chosen is remove_action:
            self.remove_playlist()

    @staticmethod
    def _reset_cover(name):
        for extension in (".jpg", ".jpeg", ".png", ".webp"):
            (PLAYLISTS_PATH / name / f"cover{extension}").unlink(missing_ok=True)

    def remove_playlist(self):
        item = self.playlist_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        if QMessageBox.question(self, "Remove Playlist", f"Remove '{name}' and its tracks?") != QMessageBox.Yes:
            return
        shutil.rmtree(PLAYLISTS_PATH / name, ignore_errors=True)
        (PLAYLISTS_PATH / f"{name}.json").unlink(missing_ok=True)
        self.playlist_list.takeItem(self.playlist_list.row(item))

    @asyncClose
    async def closeEvent(self, event):
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
