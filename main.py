import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)
from qasync import QEventLoop, asyncClose

from config import *
from group_sessions import GroupSessionWidget
from hotkeys import GlobalHotkeyThread
from p2p_sync_manager import P2PSyncManager
from player_widgets import PlaylistView
from recommendation_widgets import FlowLayout, RecommendationCard
from threads import BackgroundDownloader, RecommendationFetcher, SearchWorker
from utils import rounded_cover_pixmap
import discord_rpc

FONT_WEIGHT = QFont.Weight.Bold
MENU_STYLE = f"""
QMenu {{
    background-color: {PANEL_BG}; color: {TEXT_COLOR};
    border: 1px solid {BUTTON_BORDER}; border-radius: 10px;
    padding: 20px; font-size: 28px; font-weight: 700;
}}
QMenu::item {{
    background-color: transparent; padding: 24px 76px 24px 48px;
    margin: 6px; border-radius: 8px;
}}
QMenu::item:selected {{ background-color: {ACCENT_COLOR}; color: #ffffff; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 2px; margin: 16px 24px; background: {BUTTON_BORDER}; }}
"""


def make_menu(parent):
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_STYLE)
    return menu


def _polish(widget):
    font = widget.font()
    font.setWeight(FONT_WEIGHT)
    font.setBold(True)
    widget.setFont(font)
    style = re.sub(
        r"font-weight\s*:\s*(?:bold|normal|[1-9]00)",
        "font-weight:700", widget.styleSheet(), flags=re.I,
    )
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
        self._prepare_paths()
        self._build()
        self.load_playlists()
        self.refresh_recommendation()
        self._start_hotkeys()
        polish_tree(self)
        discord_rpc.connect()

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
        self.rec_scroll = QScrollArea()
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
        return page

    def _style(self):
        self.setStyleSheet(f"""
        QMainWindow,QWidget{{background:{BG_COLOR};color:{TEXT_COLOR};font-family:'Montserrat','Segoe UI',sans-serif;font-weight:700}}
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
        QScrollBar:vertical{{border:none;background:{PANEL_BG};width:10px}}
        QScrollBar::handle:vertical{{background:#3A3A3A;min-height:30px;border-radius:5px}}
        {MENU_STYLE}
        TrackListItemWidget,TrackListItemWidget QWidget,TrackListItemWidget QLabel,TrackRow,TrackRow QWidget,TrackRow QLabel{{background:transparent;border:none}}
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
        effect.setOpacity(0)
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
        catalog = []
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            url = data.get("source_url") or data.get("download_url")
            if url and str(url).startswith(("http://", "https://")):
                catalog.append({
                    "playlist": sidecar.parent.parent.name,
                    "title": data.get("title") or sidecar.stem,
                    "artist": data.get("artist") or "Unknown Artist",
                    "source_url": url,
                })
        return catalog

    def _download_missing_tracks(self, catalog):
        existing_urls, existing_names = set(), set()
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            url = data.get("source_url") or data.get("download_url")
            if url:
                existing_urls.add(str(url))
            existing_names.add((str(data.get("artist", "")).casefold(), str(data.get("title", "")).casefold()))
        missing = [
            track for track in catalog
            if str(track.get("source_url") or "") not in existing_urls
            and (str(track.get("artist", "")).casefold(), str(track.get("title", "")).casefold()) not in existing_names
        ]
        if not missing:
            self.group_view.status.setText("P2P connected. Library is already in sync.")
            return
        self.group_view.status.setText(f"Downloading {len(missing)} missing track(s)...")
        for track in missing:
            playlist = str(track.get("playlist") or "Listen Together")
            self._ensure_playlist(playlist)
            worker = BackgroundDownloader(track["source_url"], PLAYLISTS_PATH / playlist / "songs", self)
            self.workers.append(worker)
            worker.finished_signal.connect(
                lambda ok, message, current=worker, name=playlist:
                self._sync_download_done(ok, message, current, name)
            )
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
            card.play_requested.connect(
                lambda data, current=card:
                self._download_recommendation(data, current, "Recommendations", True)
            )
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
            QMessageBox.warning(self, "No Playlists", "Create a playlist first.")
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
        worker.finished_signal.connect(
            lambda ok, message, current=worker:
            self._recommendation_done(ok, message, current, card, playlist, autoplay)
        )
        worker.start()

    def _recommendation_done(self, ok, message, worker, card, playlist, autoplay):
        if worker in self.workers:
            self.workers.remove(worker)
        if card:
            card.set_loading(False)
        self.refresh_playlist_item(playlist)
        if not ok:
            QMessageBox.critical(self, "SoundCloud Download Error", message)
            return
        if autoplay and worker.last_downloaded_path:
            self.playlist_view.load_playlist(playlist)
            polish_tree(self.playlist_view)
            self._switch(1)
            self.playlist_view.play_file(worker.last_downloaded_path)

    def _ensure_playlist(self, name):
        (PLAYLISTS_PATH / name / "songs").mkdir(parents=True, exist_ok=True)
        metadata = PLAYLISTS_PATH / f"{name}.json"
        if not metadata.exists():
            metadata.write_text(json.dumps({"name": name, "songs": []}), encoding="utf-8")
        exists = any(self.playlist_list.item(i).data(Qt.UserRole) == name for i in range(self.playlist_list.count()))
        if not exists:
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
        polish_tree(self.playlist_view)
        self._switch(1)

    def _refresh_playlist_item(self, item):
        name = item.data(Qt.UserRole)
        songs_dir = PLAYLISTS_PATH / name / "songs"
        songs = [file for file in songs_dir.glob("*") if file.suffix.lower() in AUDIO_EXTENSIONS]
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
            custom = folder / f"cover{extension}"
            if custom.exists():
                pixmap = QPixmap(str(custom))
                if not pixmap.isNull():
                    return pixmap
        if songs:
            first = sorted(songs)[0]
            for extension in (".jpg", ".jpeg", ".png", ".webp"):
                candidate = first.with_suffix(extension)
                if candidate.exists():
                    pixmap = QPixmap(str(candidate))
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
        await self.p2p.close()
        discord_rpc.close()
        event.accept()


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    for filename in ("Montserrat-Regular.ttf", "Montserrat-Bold.ttf"):
        path = SCRIPT_DIR / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    font = app.font()
    font.setWeight(FONT_WEIGHT)
    font.setBold(True)
    app.setFont(font)
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
