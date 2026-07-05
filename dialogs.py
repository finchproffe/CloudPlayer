import os
import json
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QInputDialog, QFileDialog, QMessageBox,
    QProgressDialog
)
from PySide6.QtCore import Qt

from config import PLAYLISTS_PATH, DOWNLOADS_PATH, FFMPEG_PATH
from utils import extract_sc_meta


class AddSongDialog(QDialog):
    def __init__(self, parent=None, playlist_name=None):
        super().__init__(parent)
        self.playlist_name = playlist_name
        self.playlist_path = (PLAYLISTS_PATH / playlist_name / "songs") if playlist_name else DOWNLOADS_PATH
        self.setup_ui()
        self.playlist_path.mkdir(parents=True, exist_ok=True)

    def setup_ui(self):
        self.setWindowTitle("Add Song (SoundCloud)")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search on SoundCloud...")
        self.search_input.returnPressed.connect(self.search_songs)
        search_btn = QPushButton("Search")
        search_btn.setFixedWidth(100)
        search_btn.clicked.connect(self.search_songs)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)

        self.results_list = QListWidget()
        self.results_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.results_list.setMinimumHeight(300)

        url_btn = QPushButton("Add by URL")
        file_btn = QPushButton("Add Local File")
        for btn in (url_btn, file_btn):
            btn.setFixedHeight(40)

        layout.addLayout(search_layout)
        layout.addWidget(self.results_list)
        layout.addWidget(url_btn)
        layout.addWidget(file_btn)

        url_btn.clicked.connect(self.add_from_url)
        file_btn.clicked.connect(self.add_from_file)
        self.results_list.itemDoubleClicked.connect(self.download_selected)

    def search_songs(self):
        query = self.search_input.text().strip()
        if not query:
            return

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'skip_download': True,
            'windows_creation_flags': 0x08000000,
        }

        entries = []
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch15:{query}", download=False)
                entries = info.get('entries', []) if info else []
        except Exception as ex:
            print(f"[SoundCloud Search] failed: {ex}")

        self.results_list.clear()
        if not entries:
            self.results_list.addItem(QListWidgetItem("No results found."))
            return

        for entry in entries:
            if not entry:
                continue
            url = entry.get('url')
            if not url:
                continue
            title = (entry.get('title') or 'Unknown')[:60]
            uploader = (entry.get('uploader') or 'SoundCloud Artist')[:30]
            dur = entry.get('duration_string', '??:??')

            item = QListWidgetItem(f"{title} • {uploader} ({dur})")
            item.setData(Qt.UserRole, url)
            self.results_list.addItem(item)

    def download_selected(self):
        items = self.results_list.selectedItems()
        if not items:
            return

        total = len(items)
        progress = QProgressDialog("Downloading from SoundCloud...", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        success = 0
        for i, item in enumerate(items):
            if progress.wasCanceled():
                break
            url = item.data(Qt.UserRole)
            if url and self._download_one(url):
                success += 1
            progress.setValue(i + 1)
            progress.setLabelText(f"Downloaded: {success}/{total}")

        progress.close()
        if success > 0:
            self.accept()

    def _download_one(self, url_or_path):
        try:
            if os.path.isfile(url_or_path):
                src = Path(url_or_path)
                dst = self.playlist_path / src.name
                shutil.copy2(src, dst)
                return True

            import yt_dlp
            ydl_opts = {
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}
                ],
                'outtmpl': str(self.playlist_path / '%(title).200s.%(ext)s'),
                'ffmpeg_location': str(FFMPEG_PATH),
                'quiet': True,
                'no_warnings': True,
                'windows_creation_flags': 0x08000000,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url_or_path, download=True)
                entries = info.get('entries', [info]) if 'entries' in info else [info]

                for entry in entries:
                    if not entry: continue
                    meta = extract_sc_meta(entry)

                    filepath = entry.get('requested_downloads', [{}])[0].get('filepath')
                    if not filepath:
                        filepath = ydl.prepare_filename(entry)

                    if filepath:
                        stem = Path(filepath).stem
                        json_path = self.playlist_path / f"{stem}.json"
                        with open(json_path, 'w', encoding='utf-8') as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)
                return True
        except Exception as e:
            QMessageBox.critical(self, "Download Failed", str(e)[:400])
            return False

    def add_from_url(self):
        url, ok = QInputDialog.getText(self, "URL", "Paste SoundCloud link:")
        if ok and url.strip():
            if self._download_one(url.strip()):
                self.accept()

    def add_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio (*.mp3 *.wav *.m4a *.flac *.ogg)")
        if files:
            if any(self._download_one(f) for f in files):
                self.accept()