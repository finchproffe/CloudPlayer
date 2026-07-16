import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import (
    ACCENT_COLOR,
    AUDIO_EXTENSIONS,
    BG_COLOR,
    BUTTON_BG,
    BUTTON_BORDER,
    BUTTON_HOVER,
    PANEL_BG,
    PLAYLISTS_PATH,
    TEXT_COLOR,
    TEXT_MUTED,
)
from room_tcp_patch import install as install_room_tcp_patch
from threads import BackgroundDownloader, SearchWorker
from utils import colored_icon

install_room_tcp_patch()

USDT_BEP20_ADDRESS = "0x77F023d48271e6a7545265e91b8ac9862b6cD61E"


class AddSongDialog(QDialog):
    def __init__(self, parent=None, playlist_name=None):
        super().__init__(parent)
        self.playlist_path = PLAYLISTS_PATH / playlist_name / "songs"
        self.playlist_path.mkdir(parents=True, exist_ok=True)
        self.worker = None
        self.active_download = None
        self.download_queue = []
        self.download_index = 0
        self.download_successes = 0
        self.download_failures = []
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Add Song from SoundCloud")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        self.selection_panel = QWidget()
        selection_layout = QVBoxLayout(self.selection_panel)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search SoundCloud...")
        self.search_input.returnPressed.connect(self.search_songs)
        search_btn = QPushButton("Search")
        search_btn.setFixedWidth(100)
        search_btn.clicked.connect(self.search_songs)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)

        self.results_list = QListWidget()
        self.results_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.results_list.setMinimumHeight(300)

        url_btn = QPushButton("Add From URL")
        file_btn = QPushButton("Add Local File")
        for button in (url_btn, file_btn):
            button.setFixedHeight(40)

        selection_layout.addLayout(search_layout)
        selection_layout.addWidget(self.results_list)
        selection_layout.addWidget(url_btn)
        selection_layout.addWidget(file_btn)

        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(18, 28, 18, 28)
        progress_layout.setSpacing(14)
        self.progress_title = QLabel("Downloading track")
        self.progress_title.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:18px;font-weight:700"
        )
        self.progress_status = QLabel("Preparing download...")
        self.progress_status.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(
            f"QProgressBar{{background:{PANEL_BG};color:{TEXT_COLOR};"
            f"border:1px solid {BUTTON_BORDER};border-radius:6px;"
            "height:18px;text-align:center}}"
            f"QProgressBar::chunk{{background:{ACCENT_COLOR};border-radius:5px}}"
        )
        progress_layout.addStretch()
        progress_layout.addWidget(self.progress_title)
        progress_layout.addWidget(self.progress_status)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addStretch()
        self.progress_panel.hide()
        layout.addWidget(self.selection_panel)
        layout.addWidget(self.progress_panel)

        url_btn.clicked.connect(self.add_from_url)
        file_btn.clicked.connect(self.add_from_file)
        self.results_list.itemDoubleClicked.connect(self._download_item)

    def search_songs(self):
        query = self.search_input.text().strip()
        if not query:
            return
        self.results_list.clear()
        self.results_list.addItem("Searching SoundCloud...")
        self.worker = SearchWorker(query, self)
        self.worker.results_ready.connect(self.show_results)
        self.worker.start()

    def show_results(self, results):
        self.results_list.clear()
        for result in results:
            item = QListWidgetItem(
                f"{result['title']} • {result['artist']}"
            )
            item.setData(Qt.UserRole, result)
            self.results_list.addItem(item)
        if not results:
            self.results_list.addItem("No SoundCloud results found.")

    def download_selected(self):
        self._start_download_queue(self._selected_downloads())

    def _download_item(self, item):
        if self.active_download is not None:
            return
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            item.setSelected(True)
        self._start_download_queue(self._selected_downloads(item))

    def _selected_downloads(self, clicked_item=None):
        items = list(self.results_list.selectedItems())
        if clicked_item is not None and clicked_item not in items:
            items.append(clicked_item)

        downloads = []
        seen_urls = set()
        for item in items:
            data = item.data(Qt.UserRole)
            if not isinstance(data, dict):
                continue
            url = str(data.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = data.get("title") or "SoundCloud track"
            artist = data.get("artist") or "Unknown Artist"
            downloads.append({"url": url, "label": f"{title} - {artist}"})
        return downloads

    def add_from_url(self):
        url, accepted = QInputDialog.getText(
            self,
            "Add From URL",
            "SoundCloud URL:",
        )
        if not accepted:
            return
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(
                self,
                "Invalid URL",
                "Enter a complete http:// or https:// URL.",
            )
            return
        self._start_download(url, "SoundCloud track")

    def _start_download(self, url, title):
        self._start_download_queue([{"url": url, "label": title}])

    def _start_download_queue(self, downloads):
        if self.active_download is not None or not downloads:
            return
        self.download_queue = list(downloads)
        self.download_index = 0
        self.download_successes = 0
        self.download_failures = []
        self.selection_panel.hide()
        self.progress_panel.show()
        self._start_next_download()

    def _start_next_download(self):
        total = len(self.download_queue)
        if self.download_index >= total:
            self._finish_download_queue()
            return

        current = self.download_queue[self.download_index]
        self.progress_title.setText(
            f"Downloading {self.download_index + 1} of {total}: "
            f"{current['label']}"
        )
        self.progress_status.setText("Preparing download...")
        self.progress_bar.setRange(0, 0)
        worker = BackgroundDownloader(current["url"], self.playlist_path, self)
        self.active_download = worker
        worker.progress_signal.connect(self._download_progress)
        worker.finished_signal.connect(
            lambda ok, message, current=worker: self._download_done(ok, message, current)
        )
        worker.start()

    def _download_progress(self, percent, status):
        self.progress_status.setText(status)
        if percent <= 0:
            self.progress_bar.setRange(0, 0)
            return
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)

    def _download_done(self, ok, message, worker):
        if self.active_download is not worker:
            return
        if worker.isRunning():
            worker.wait(1000)
        self.active_download = None
        if ok:
            self.download_successes += 1
        else:
            label = self.download_queue[self.download_index]["label"]
            self.download_failures.append((label, message))
        self.download_index += 1
        worker.deleteLater()
        QTimer.singleShot(0, self._start_next_download)

    def _finish_download_queue(self):
        downloaded = self.download_successes
        failures = list(self.download_failures)
        self.download_queue = []
        self.download_index = 0
        self.download_successes = 0
        self.download_failures = []

        if downloaded:
            if failures:
                failed_titles = "\n".join(
                    f"• {title}: {message}" for title, message in failures[:3]
                )
                QMessageBox.warning(
                    self,
                    "Some Downloads Failed",
                    f"Downloaded {downloaded} track(s).\n"
                    f"Failed {len(failures)} track(s):\n{failed_titles}",
                )
            self.accept()
            return

        self.progress_panel.hide()
        self.selection_panel.show()
        message = failures[0][1] if failures else "No tracks were downloaded."
        QMessageBox.critical(self, "Download Failed", message)

    def reject(self):
        if self.download_queue or (
            self.active_download is not None and self.active_download.isRunning()
        ):
            return
        super().reject()

    def add_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Audio Files",
            "",
            "Audio (*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.webm)",
        )
        copied = 0
        for filename in files:
            source = Path(filename)
            if source.suffix.lower() in AUDIO_EXTENSIONS:
                shutil.copy2(source, self.playlist_path / source.name)
                copied += 1
        if copied:
            self.accept()


class DonationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Support CloudPlayer")
        self.setModal(False)
        self.setFixedWidth(520)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(14)

        title = QLabel("USDT (BEP20)")
        title.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:20px;font-weight:700"
        )
        hint = QLabel("Send only USDT on the BNB Smart Chain network.")
        hint.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")

        row = QHBoxLayout()
        row.setSpacing(8)
        address = QLineEdit(USDT_BEP20_ADDRESS)
        address.setReadOnly(True)
        address.setCursorPosition(0)
        address.setStyleSheet(
            f"background:{PANEL_BG};color:{TEXT_COLOR};"
            f"border:1px solid {BUTTON_BORDER};border-radius:7px;"
            "padding:10px;font-size:13px"
        )
        copy_btn = QPushButton()
        copy_btn.setIcon(colored_icon("copy.svg"))
        copy_btn.setToolTip("Copy address")
        copy_btn.setFixedSize(42, 42)
        copy_btn.clicked.connect(self._copy_address)
        row.addWidget(address, 1)
        row.addWidget(copy_btn)

        self.status = QLabel("")
        self.status.setStyleSheet(
            f"color:{ACCENT_COLOR};font-size:12px;font-weight:700"
        )
        root.addWidget(title)
        root.addWidget(hint)
        root.addLayout(row)
        root.addWidget(self.status)
        self.setStyleSheet(f"QDialog{{background:{BG_COLOR};color:{TEXT_COLOR}}}")

    def _copy_address(self):
        QGuiApplication.clipboard().setText(USDT_BEP20_ADDRESS)
        self.status.setText("Address copied")


def _copy_cover(view):
    pixmap = getattr(view, "current_cover_pixmap", None)
    if pixmap is None or pixmap.isNull():
        QMessageBox.information(view, "Copy Cover", "This track has no cover.")
        return
    QGuiApplication.clipboard().setPixmap(pixmap)


def _install_cover_action():
    try:
        from player_widgets import PlaylistView
    except Exception:
        return
    if getattr(PlaylistView, "_copy_cover_action_installed", False):
        return
    original = PlaylistView._cover_menu

    def cover_menu(self, position):
        menu = QMenuWithCopy(self, position, original)
        menu.exec_menu()

    PlaylistView._cover_menu = cover_menu
    PlaylistView._copy_cover_action_installed = True


class QMenuWithCopy:


    def __init__(self, view, position, original):
        self.view = view
        self.position = position
        self.original = original

    def exec_menu(self):
        from player_widgets import CoverPreviewDialog, make_menu

        menu = make_menu(self.view)
        view_action = menu.addAction(
            colored_icon("view.svg", size=28), "View Full Size"
        )
        save_action = menu.addAction(
            colored_icon("download.svg", size=28), "Download Cover"
        )
        copy_action = menu.addAction(
            colored_icon("copy.svg", size=28), "Copy Cover"
        )
        pixmap = getattr(self.view, "current_cover_pixmap", None)
        has_cover = pixmap is not None and not pixmap.isNull()
        view_action.setEnabled(has_cover)
        save_action.setEnabled(has_cover)
        copy_action.setEnabled(has_cover)
        chosen = menu.exec(
            self.view.cover_label.mapToGlobal(self.position)
        )
        if chosen is view_action:
            CoverPreviewDialog(pixmap, self.view).exec()
        elif chosen is save_action:
            path, _ = QFileDialog.getSaveFileName(
                self.view,
                "Save Cover",
                "cover.jpg",
                "Images (*.jpg *.jpeg *.png)",
            )
            if path:
                pixmap.save(path)
        elif chosen is copy_action:
            _copy_cover(self.view)


def _inject_donation_button():
    app = QApplication.instance()
    if app is None:
        return
    for window in app.topLevelWidgets():
        github = next(
            (
                button
                for button in window.findChildren(QPushButton)
                if button.toolTip() == "Open GitHub"
            ),
            None,
        )
        if github is None:
            continue
        parent = github.parentWidget()
        layout = parent.layout() if parent else None
        if layout is None or getattr(window, "_donation_button", None):
            continue
        button = QPushButton()
        button.setIcon(colored_icon("money.svg", "#ffffff", 22))
        button.setIconSize(github.iconSize())
        button.setFixedSize(github.size())
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Support CloudPlayer")
        button.setAccessibleName("Support CloudPlayer")
        button.setStyleSheet(github.styleSheet())
        index = layout.indexOf(github)
        layout.insertWidget(max(0, index), button)
        window._donation_button = button
        window._donation_dialog = DonationDialog(window)
        button.clicked.connect(window._donation_dialog.show)


def _install_ui_addons():
    _install_cover_action()
    _inject_donation_button()
