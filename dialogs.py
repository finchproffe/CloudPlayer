import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QProgressDialog, QVBoxLayout,
)

from config import AUDIO_EXTENSIONS, PLAYLISTS_PATH
from threads import BackgroundDownloader, SearchWorker


class AddSongDialog(QDialog):
    def __init__(self, parent=None, playlist_name=None):
        super().__init__(parent)
        self.playlist_path = PLAYLISTS_PATH / playlist_name / "songs"
        self.playlist_path.mkdir(parents=True, exist_ok=True)
        self.worker = None
        self.downloads = []
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Add Song from SoundCloud")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
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
        download_btn = QPushButton("Download Selected")
        file_btn = QPushButton("Add Local File")
        for button in (download_btn, file_btn):
            button.setFixedHeight(40)
        layout.addLayout(search_layout)
        layout.addWidget(self.results_list)
        layout.addWidget(download_btn)
        layout.addWidget(file_btn)
        download_btn.clicked.connect(self.download_selected)
        file_btn.clicked.connect(self.add_from_file)
        self.results_list.itemDoubleClicked.connect(lambda _item: self.download_selected())

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
            item = QListWidgetItem(f"{result['title']} • {result['artist']}")
            item.setData(Qt.UserRole, result)
            self.results_list.addItem(item)
        if not results:
            self.results_list.addItem("No SoundCloud results found.")

    def download_selected(self):
        items = [item for item in self.results_list.selectedItems() if item.data(Qt.UserRole)]
        if not items:
            return
        progress = QProgressDialog("Downloading from SoundCloud...", "Cancel", 0, len(items), self)
        progress.setWindowModality(Qt.WindowModal)
        for index, item in enumerate(items, 1):
            if progress.wasCanceled():
                break
            worker = BackgroundDownloader(item.data(Qt.UserRole)["url"], self.playlist_path, self)
            self.downloads.append(worker)
            worker.finished_signal.connect(
                lambda ok, message, current=worker: self._download_done(ok, message, current)
            )
            worker.start()
            progress.setValue(index)
        progress.close()

    def _download_done(self, ok, message, worker):
        if worker in self.downloads:
            self.downloads.remove(worker)
        if ok:
            self.accept()
        else:
            QMessageBox.critical(self, "Download Failed", message)

    def add_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Audio Files", "",
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
