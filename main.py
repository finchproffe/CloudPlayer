import sys
import os
import json
import urllib.parse
import urllib.request
import shutil
import re
import random
from pathlib import Path
from html.parser import HTMLParser
import discord_rpc

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QLineEdit, QDialog, QInputDialog,
    QFileDialog, QMessageBox, QProgressDialog, QSlider, QStyle, QStackedWidget, QTextEdit,
    QMenu, QGraphicsOpacityEffect
)
from PySide6.QtGui import (
    QIcon, QPixmap, QFontDatabase, QMovie, QKeyEvent, QKeySequence,
    QPalette, QColor, QCursor, QScreen, QPainter, QShortcut, QAction
)
from PySide6.QtCore import (
    Qt, QTimer, QUrl, QSize, QPoint, QRect, QFileInfo, QStandardPaths, QThread, Signal, QObject,
    QPropertyAnimation, QEasingCurve
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput


DOCS_PATH = Path.home() / "Documents" / "CloudPlayer"
DOWNLOADS_PATH = DOCS_PATH / "downloads"
PLAYLISTS_PATH = DOCS_PATH / "playlists"
SCRIPT_DIR = Path(__file__).parent
FFMPEG_PATH = SCRIPT_DIR / "ffmpeg.exe"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm"}

BG_COLOR = "#121212"
PANEL_BG = "#1A1A1A"
BUTTON_BG = "#222222"
BUTTON_HOVER = "#2D2D2D"
BUTTON_BORDER = "#333333"
ACCENT_COLOR = "#0D47A1"
TEXT_COLOR = "#E0E0E0"
TEXT_MUTED = "#888888"
ICON_COLOR = "#FFFFFF"

GENIUS_TOKEN = "7FBtGwlCeRvyuPf1fxFdR5_qTy3ARuxdbcaAHenQ1VXBXaHJJoJhyxB-MSVlhGqk"

def format_time(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"

def extract_sc_meta(info: dict) -> dict:
    raw_title = info.get('title', 'Unknown Title')
    uploader = info.get('uploader', 'Unknown Artist')
    
    artist = info.get('artist') or info.get('creator')
    title = info.get('track')

    if not artist or not title:
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif "-" in raw_title:
            parts = raw_title.split("-", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = artist or uploader
            title = title or raw_title

    duration = info.get('duration')
    if isinstance(duration, (int, float)):
        duration_str = format_time(int(duration * 1000))
    else:
        duration_str = info.get('duration_string', '??:??')

    return {
        "artist": str(artist).strip(),
        "title": str(title).strip(),
        "duration": duration_str
    }

def colored_icon(filename, color=ICON_COLOR, size=64):
    path = SCRIPT_DIR / filename
    if not path.is_file():
        return QIcon()

    source = QPixmap(str(path))
    if source.isNull():
        return QIcon()

    source = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    colored = QPixmap(source.size())
    colored.fill(Qt.transparent)

    painter = QPainter(colored)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(colored.rect(), QColor(color))
    painter.end()

    return QIcon(colored)


class GeniusLyricsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.recording = False
        self.lyrics = []
        self.div_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if attrs_dict.get('data-lyrics-container') == 'true':
            self.recording = True
            self.div_depth = 0
        if self.recording:
            if tag == 'div':
                self.div_depth += 1
            elif tag == 'br':
                self.lyrics.append('\n')

    def handle_endtag(self, tag):
        if self.recording:
            if tag == 'div':
                self.div_depth -= 1
                if self.div_depth <= 0:
                    self.recording = False

    def handle_data(self, data):
        if self.recording:
            self.lyrics.append(data)


class TrackMetaFetcher(QThread):
    meta_ready = Signal(dict)

    def __init__(self, song_path):
        super().__init__()
        self.song_path = Path(song_path)

    @staticmethod
    def _normalize(text):
        s = (text or "").lower().strip()
        s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)
        s = re.sub(r'\bfeat\.?\b|\bft\.?\b|\bfeaturing\b', ' ', s)
        s = re.sub(r'[^a-zа-яё0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @staticmethod
    def _is_match(expected, actual, min_len=3):
        if not expected or not actual:
            return False
        if expected == actual:
            return True
        if min(len(expected), len(actual)) < min_len:
            return False
        return expected in actual or actual in expected

    def run(self):
        base_name = self.song_path.stem
        target_dir = self.song_path.parent
        
        raw_name = base_name
        raw_name = re.sub(r'^\d+[\.\s\-]*', '', raw_name)
        raw_name = re.sub(r'\(.*?\)|\[.*?\]', '', raw_name).strip()

        artist = "Неизвестен"
        title = raw_name
        duration = None
        
        if " - " in raw_name:
            parts = raw_name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif "-" in raw_name:
            parts = raw_name.split("-", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        
        result_data = {
            "title": title,
            "artist": artist,
            "prod": "",
            "lyrics": "Текст не найден.",
            "cover_bytes": None,
            "cover_url": None,
            "duration": duration
        }

        sidecar_path = target_dir / f"{base_name}.json"
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sidecar_data = json.load(f)
                artist = sidecar_data.get("artist", artist)
                title = sidecar_data.get("title", title)
                duration = sidecar_data.get("duration")
                
                result_data["artist"] = artist
                result_data["title"] = title
                result_data["duration"] = duration
            except Exception as e:
                pass

        local_cover_jpg = target_dir / f"{base_name}.jpg"
        local_cover_png = target_dir / f"{base_name}.png"
        local_cover_webp = target_dir / f"{base_name}.webp"

        chosen_local_path = None
        for path in [local_cover_jpg, local_cover_png, local_cover_webp]:
            if path.exists():
                chosen_local_path = path
                break

        if chosen_local_path:
            try:
                with open(chosen_local_path, "rb") as f:
                    result_data["cover_bytes"] = f.read()
            except Exception as e:
                pass

        if not title:
            self.meta_ready.emit(result_data)
            return

        search_query = f"{artist} {title}".strip()
        search_url = f"https://api.genius.com/search?q={urllib.parse.quote(search_query)}"
        headers = {
            "Authorization": f"Bearer {GENIUS_TOKEN}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            hits = data.get("response", {}).get("hits", [])
            if hits:
                exp_art_lower = self._normalize(artist)
                exp_tit_lower = self._normalize(title)

                hit_result = None
                for hit in hits:
                    item = hit.get("result", {})
                    hit_artist = self._normalize(item.get("primary_artist", {}).get("name", ""))
                    hit_title = self._normalize(item.get("title", ""))

                    artist_ok = self._is_match(exp_art_lower, hit_artist)
                    title_ok = self._is_match(exp_tit_lower, hit_title)

                    if artist_ok and title_ok:
                        hit_result = item
                        break

                if hit_result is None:
                    self.meta_ready.emit(result_data)
                    return

                if not result_data["cover_bytes"]:
                    cover_url = hit_result.get("song_art_image_thumbnail_url")
                    if cover_url:
                        result_data["cover_url"] = cover_url
                        try:
                            img_req = urllib.request.Request(cover_url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(img_req, timeout=4) as img_res:
                                result_data["cover_bytes"] = img_res.read()
                        except Exception:
                            pass

                song_web_url = hit_result.get("url")
                if song_web_url:
                    page_req = urllib.request.Request(song_web_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Accept": "text/html"
                    })
                    with urllib.request.urlopen(page_req, timeout=5) as page_response:
                        html_content = page_response.read().decode('utf-8')

                    parser = GeniusLyricsParser()
                    parser.feed(html_content)
                    lyrics = "".join(parser.lyrics).strip()

                    if lyrics:
                        lyrics = re.sub(r'^.*?Lyrics\s*', '', lyrics, count=1, flags=re.IGNORECASE)
                        lyrics = re.sub(r'\[Текст песни.*?\]\s*', '', lyrics, count=1, flags=re.IGNORECASE)
                        result_data["lyrics"] = re.sub(r'\n{3,}', '\n\n', lyrics).strip()

        except Exception as e:
            if not result_data["cover_bytes"]:
                result_data["lyrics"] = f"Ошибка загрузки метаданных: {e}"

        self.meta_ready.emit(result_data)

class RecommendationFetcher(QThread):
    rec_ready = Signal(list)
    TARGET_COUNT = 3

    @staticmethod
    def _normalize_key(text: str) -> str:
        s = (text or "").lower().strip()
        s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)
        s = re.sub(r'[^a-zа-яё0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @staticmethod
    def _collect_user_artists():
        artists = []
        seen = set()
        if not PLAYLISTS_PATH.exists():
            return artists
        try:
            for p_dir in PLAYLISTS_PATH.iterdir():
                songs_dir = p_dir / "songs"
                if not songs_dir.exists():
                    continue
                for f in songs_dir.glob("*.*"):
                    if f.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue

                    artist = None
                    sidecar = f.parent / f"{f.stem}.json"
                    if sidecar.exists():
                        try:
                            with open(sidecar, "r", encoding="utf-8") as fh:
                                data = json.load(fh)
                            candidate = (data.get("artist") or "").strip()
                            if candidate:
                                artist = candidate
                        except Exception:
                            pass

                    if not artist and "-" in f.stem:
                        artist = f.stem.split("-", 1)[0].strip()

                    if not artist:
                        continue
                    key = artist.lower()
                    if key not in seen:
                        seen.add(key)
                        artists.append(artist)
        except Exception:
            pass
        return artists

    @staticmethod
    def _collect_user_track_keys():
        keys = set()
        if not PLAYLISTS_PATH.exists():
            return keys
        try:
            for p_dir in PLAYLISTS_PATH.iterdir():
                songs_dir = p_dir / "songs"
                if not songs_dir.exists():
                    continue
                for f in songs_dir.glob("*.*"):
                    if f.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    artist, title = None, None

                    sidecar = f.parent / f"{f.stem}.json"
                    if sidecar.exists():
                        try:
                            with open(sidecar, "r", encoding="utf-8") as fh:
                                data = json.load(fh)
                            artist = (data.get("artist") or "").strip() or None
                            title = (data.get("title") or "").strip() or None
                        except Exception:
                            pass

                    if not artist or not title:
                        if " - " in f.stem:
                            parts = f.stem.split(" - ", 1)
                        elif "-" in f.stem:
                            parts = f.stem.split("-", 1)
                        else:
                            parts = None
                        if parts:
                            artist = (artist or parts[0].strip())
                            title = (title or parts[1].strip() if len(parts) > 1 else None)

                    if artist and title:
                        keys.add(
                            RecommendationFetcher._normalize_key(artist)
                            + " "
                            + RecommendationFetcher._normalize_key(title)
                        )
        except Exception:
            pass
        return keys

    def _fetch_tracks_for_artist(self, artist: str, headers: dict) -> list:
        try:
            search_url = f"https://api.genius.com/search?q={urllib.parse.quote(artist)}"
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
            hits = data.get("response", {}).get("hits", [])
            results = []
            for hit in hits:
                item = hit.get("result", {})
                hit_artist = item.get("primary_artist", {}).get("name", artist)
                hit_title = item.get("title", "")
                if hit_title:
                    results.append({"artist": hit_artist, "title": hit_title})
            return results
        except Exception:
            return []

    def run(self):
        artists = self._collect_user_artists()

        if not artists:
            self.rec_ready.emit([])
            return

        existing = self._collect_user_track_keys()
        headers = {
            "Authorization": f"Bearer {GENIUS_TOKEN}",
            "User-Agent": "Mozilla/5.0"
        }

        recommendations = []
        seen_titles = set()
        shuffled = artists[:]
        random.shuffle(shuffled)

        max_artist_attempts = max(10, len(shuffled) * 2)

        for artist in shuffled:
            if len(recommendations) >= self.TARGET_COUNT:
                break
            if max_artist_attempts <= 0:
                break
            max_artist_attempts -= 1

            tracks = self._fetch_tracks_for_artist(artist, headers)
            if not tracks:
                continue
            random.shuffle(tracks)

            for tr in tracks:
                if len(recommendations) >= self.TARGET_COUNT:
                    break
                a, t = tr["artist"], tr["title"]
                key = self._normalize_key(a) + " " + self._normalize_key(t)
                if key in existing or key in seen_titles:
                    continue
                seen_titles.add(key)
                recommendations.append((a, t))

        self.rec_ready.emit(recommendations)

class BackgroundDownloader(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, query, dest_path):
        super().__init__()
        self.query = query
        self.dest_path = dest_path

    def run(self):
        try:
            import yt_dlp
            ydl_opts = {
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}
                ],
                'outtmpl': str(self.dest_path / '%(title).200s.%(ext)s'),
                'ffmpeg_location': str(FFMPEG_PATH),
                'quiet': True,
                'no_warnings': True,
                'windows_creation_flags': 0x08000000,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch1:{self.query}", download=True)
                if info and 'entries' in info and info['entries']:
                    entry = info['entries'][0]
                    meta = extract_sc_meta(entry)
                    
                    filepath = entry.get('requested_downloads', [{}])[0].get('filepath')
                    if not filepath:
                        filepath = ydl.prepare_filename(entry)
                        
                    if filepath:
                        stem = Path(filepath).stem
                        json_path = self.dest_path / f"{stem}.json"
                        with open(json_path, 'w', encoding='utf-8') as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)

            self.finished_signal.emit(True, "Успешно добавлено!")
        except Exception as e:
            self.finished_signal.emit(False, str(e))

class PlayerControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        time_layout = QHBoxLayout()
        self.current_time = QLabel("0:00")
        self.total_time = QLabel("0:00")
        self.progress_bar = QSlider(Qt.Horizontal)

        for label in [self.current_time, self.total_time]:
            label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; font-weight: 500;")

        time_layout.addWidget(self.current_time)
        time_layout.addWidget(self.progress_bar)
        time_layout.addWidget(self.total_time)

        layout.addLayout(time_layout)
        self.progress_bar.sliderMoved.connect(self.on_seek)

    def on_seek(self, position):
        if hasattr(self.parent(), 'seek_position'):
            self.parent().seek_position(position)

class MiniControlBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)
        layout.addStretch()

        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.next_btn = QPushButton()

        prev_icon = colored_icon("prev.svg")
        if not prev_icon.isNull():
            self.prev_btn.setIcon(prev_icon)
        play_icon = colored_icon("play.svg")
        if not play_icon.isNull():
            self.play_btn.setIcon(play_icon)
        next_icon = colored_icon("next.svg")
        if not next_icon.isNull():
            self.next_btn.setIcon(next_icon)

        for btn in (self.prev_btn, self.next_btn):
            btn.setFixedSize(36, 36)
            btn.setIconSize(QSize(16, 16))

        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setIconSize(QSize(18, 18))
        self.play_btn.setStyleSheet(f"background-color: {ACCENT_COLOR}; border: none;")

        layout.addWidget(self.prev_btn)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.next_btn)
        layout.addStretch()

    def set_playing(self, playing: bool):
        filename = "pause.svg" if playing else "play.svg"
        icon = colored_icon(filename)
        if not icon.isNull():
            self.play_btn.setIcon(icon)

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
            print(f"failed: {ex}")

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

class PlaylistView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.current_track_index = -1
        self.meta_thread = None
        self.is_shuffled = False
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(90, 36)
        
        self.playlist_name = QLabel("Playlist")
        self.playlist_name.setStyleSheet("font-size: 20px; font-weight: bold;")

        self.now_playing = QLabel("Now Playing — None")
        self.now_playing.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 500;")
        
        self.volume_btn = QPushButton()
        self.volume_btn.setFixedSize(30, 30)
        self.volume_btn.setFlat(True)
        volume_icon = colored_icon("volume-on.svg")
        if not volume_icon.isNull():
            self.volume_btn.setIcon(volume_icon)
        self.volume_btn.setStyleSheet("border: none; background: transparent;")
        
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setValue(70)

        header_layout.addWidget(self.back_btn)
        header_layout.addSpacing(15)
        header_layout.addWidget(self.playlist_name)
        header_layout.addStretch()
        header_layout.addWidget(self.now_playing)
        header_layout.addStretch()
        header_layout.addWidget(self.volume_btn)
        header_layout.addWidget(self.volume_slider)
        layout.addLayout(header_layout)

        center_layout = QHBoxLayout()
        center_layout.setSpacing(20)

        self.songs_list = QListWidget()
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(self._show_track_context_menu)
        center_layout.addWidget(self.songs_list, stretch=25)

        self.right_sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.right_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(12)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid {BUTTON_BORDER}; border-radius: 4px;")
        self.cover_label.setScaledContents(True)

        self.cover_opacity_effect = QGraphicsOpacityEffect(self.cover_label)
        self.cover_opacity_effect.setOpacity(0.0)
        self.cover_label.setGraphicsEffect(self.cover_opacity_effect)
        self.cover_anim = QPropertyAnimation(self.cover_opacity_effect, b"opacity", self)
        self.cover_anim.setDuration(150)
        self.cover_anim.setEasingCurve(QEasingCurve.OutCubic)
        
        self.track_title = QLabel("Название песни")
        self.track_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        self.track_title.setWordWrap(True)

        self.track_artist_prod = QLabel("Исполнитель")
        self.track_artist_prod.setStyleSheet(f"font-size: 13px; color: {TEXT_MUTED};")
        self.track_artist_prod.setWordWrap(True)

        self.lyrics_display = QTextEdit()
        self.lyrics_display.setReadOnly(True)
        self.lyrics_display.setPlaceholderText("Включите трек...")

        sidebar_layout.addWidget(self.cover_label, alignment=Qt.AlignCenter)
        sidebar_layout.addWidget(self.track_title)
        sidebar_layout.addWidget(self.track_artist_prod)
        sidebar_layout.addWidget(self.lyrics_display)
        
        center_layout.addWidget(self.right_sidebar, stretch=10)
        layout.addLayout(center_layout)

        self.player_controls = PlayerControls()
        layout.addWidget(self.player_controls)

        self.mini_bar = MiniControlBar()
        layout.addWidget(self.mini_bar)

        bottom_actions = QHBoxLayout()
        self.shuffle_btn = QPushButton(" Shuffle Mode")
        shuffle_icon = colored_icon("shuffle.svg")
        if not shuffle_icon.isNull():
            self.shuffle_btn.setIcon(shuffle_icon)
        self.shuffle_btn.setFixedHeight(45)
        
        self.add_song_btn = QPushButton("Add Song")
        self.add_song_btn.setFixedHeight(45)
        
        bottom_actions.addWidget(self.shuffle_btn)
        bottom_actions.addWidget(self.add_song_btn)
        layout.addLayout(bottom_actions)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self.songs_list.itemDoubleClicked.connect(self.play_song)
        self.add_song_btn.clicked.connect(self.add_song)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        
        self.mini_bar.play_btn.clicked.connect(self.toggle_playback)
        self.mini_bar.next_btn.clicked.connect(self.play_next_track)
        self.mini_bar.prev_btn.clicked.connect(self.play_prev_track)
        
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.volume_btn.clicked.connect(self.toggle_mute)
        
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

    def toggle_shuffle(self):
        self.is_shuffled = not self.is_shuffled
        if self.is_shuffled:
            self.shuffle_btn.setStyleSheet(f"background-color: {ACCENT_COLOR}; border: 1px solid #444;")
        else:
            self.shuffle_btn.setStyleSheet("")

    def set_playback_state(self, is_playing: bool):
        self.mini_bar.set_playing(is_playing)

    def update_position(self, pos):
        self.player_controls.current_time.setText(format_time(pos))
        self.player_controls.progress_bar.setValue(pos)

    def update_duration(self, dur):
        self.player_controls.total_time.setText(format_time(dur))
        self.player_controls.progress_bar.setRange(0, dur)

    def update_songs_list(self):
        self.songs_list.clear()
        if self.current_playlist_path and self.current_playlist_path.exists():
            files = sorted(self.current_playlist_path.glob("*.*"))
            index = 1
            for f in files:
                if f.suffix.lower() in AUDIO_EXTENSIONS:
                    item = QListWidgetItem(f"{index}. {f.stem}")
                    item.setData(Qt.UserRole, f.name)
                    self.songs_list.addItem(item)
                    index += 1

    def seek_position(self, pos):
        self.player.setPosition(pos)

    def toggle_mute(self):
        muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not muted)
        fn = "volume-off.svg" if not muted else "volume-on.svg"
        icon = colored_icon(fn)
        if not icon.isNull():
            self.volume_btn.setIcon(icon)

    def play_song(self, item):
        try:
            idx = self.songs_list.row(item)
            filename = item.data(Qt.UserRole)
            path = self.current_playlist_path / filename
            
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()
            self.set_playback_state(True)
            
            stem = Path(filename).stem
            self.now_playing.setText(f"Now Playing — {stem}")
            self.current_track_index = idx

            self.track_title.setText(stem)
            self.track_artist_prod.setText("Загрузка инфо...")
            self.cover_label.clear()
            self.lyrics_display.setText("Загрузка текста...")

            if self.meta_thread and self.meta_thread.isRunning():
                self.meta_thread.terminate()
                
            self.meta_thread = TrackMetaFetcher(path)
            self.meta_thread.meta_ready.connect(self.apply_track_metadata)
            self.meta_thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Can't play:\n{e}")

    def apply_track_metadata(self, data):
        self.track_title.setText(data["title"])
        
        artist_text = data["artist"]
        if data.get("duration"):
            artist_text += f" • {data['duration']}"
        self.track_artist_prod.setText(artist_text)
        
        self.lyrics_display.setText(data["lyrics"])
        
        if data["cover_bytes"]:
            px = QPixmap()
            px.loadFromData(data["cover_bytes"])
            self.cover_label.setPixmap(px)
            self.cover_opacity_effect.setOpacity(0.0)
            self.cover_anim.setStartValue(0.0)
            self.cover_anim.setEndValue(1.0)
            self.cover_anim.stop()
            self.cover_anim.start()
        else:
            self.cover_label.setText(" No Cover")
            self.cover_opacity_effect.setOpacity(1.0)

        discord_rpc.update_now_playing(
            title=data.get("title", "Неизвестно"),
            artist=data.get("artist", "Неизвестно"),
            cover_url=data.get("cover_url") 
        )

    def _show_track_context_menu(self, pos: QPoint):
        item = self.songs_list.itemAt(pos)
        if not item:
            return

        self.songs_list.setCurrentItem(item)

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {PANEL_BG};
                border: 1px solid {BUTTON_BORDER};
                border-radius: 4px;
                padding: 4px;
                color: {TEXT_COLOR};
                font-size: 13px;
            }}
            QMenu::item {{
                background-color: transparent;
                border-radius: 3px;
                padding: 7px 22px 7px 14px;
                margin: 1px 2px;
            }}
            QMenu::item:selected {{
                background-color: {ACCENT_COLOR};
                color: #ffffff;
            }}
            QMenu::separator {{
                height: 1px;
                background: {BUTTON_BORDER};
                margin: 4px 8px;
            }}
        """)

        act_play = menu.addAction("Воспроизвести")
        menu.addSeparator()
        act_rename = menu.addAction("Переименовать")
        act_duplicate = menu.addAction("Дублировать")
        act_delete = menu.addAction("Удалить")

        play_icon = colored_icon("play.svg", size=32)
        rename_icon = colored_icon("rename.svg", size=32)
        dup_icon = colored_icon("copy.svg", size=32)
        del_icon = colored_icon("delete.svg", size=32)
        if not play_icon.isNull():
            act_play.setIcon(play_icon)
        if not rename_icon.isNull():
            act_rename.setIcon(rename_icon)
        if not dup_icon.isNull():
            act_duplicate.setIcon(dup_icon)
        if not del_icon.isNull():
            act_delete.setIcon(del_icon)

        chosen = menu.exec(self.songs_list.viewport().mapToGlobal(pos))
        if chosen is act_play:
            self.play_song(item)
        elif chosen is act_rename:
            self.rename_current_track()
        elif chosen is act_duplicate:
            self.duplicate_current_track()
        elif chosen is act_delete:
            self.delete_current_track()

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.set_playback_state(False)
            discord_rpc.update_paused()
        else:
            item = self.songs_list.currentItem()
            if item:
                if not self.player.source().isValid():
                    self.play_song(item)
                else:
                    self.player.play()
                    self.set_playback_state(True)
                    discord_rpc.update_now_playing(self.track_title.text(), self.track_artist_prod.text().split(' • ')[0], None)
            elif self.songs_list.count() > 0:
                self.songs_list.setCurrentRow(0)
                self.play_song(self.songs_list.item(0))

    def play_next_track(self):
        count = self.songs_list.count()
        if count == 0:
            return
        if self.is_shuffled:
            next_idx = random.randint(0, count - 1)
        else:
            next_idx = (self.current_track_index + 1) % count
            
        item = self.songs_list.item(next_idx)
        self.songs_list.setCurrentItem(item)
        self.play_song(item)

    def play_prev_track(self):
        count = self.songs_list.count()
        if count == 0:
            return
        prev_idx = (self.current_track_index - 1) % count
        item = self.songs_list.item(prev_idx)
        self.songs_list.setCurrentItem(item)
        self.play_song(item)

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next_track()

    def duplicate_current_track(self):
        item = self.songs_list.currentItem()
        if not item: return
        name = item.data(Qt.UserRole)
        src = self.current_playlist_path / name
        base, ext = os.path.splitext(name)
        counter = 1
        while True:
            new_name = f"{base} ({counter}){ext}"
            if not (self.current_playlist_path / new_name).exists(): break
            counter += 1
        shutil.copy2(src, self.current_playlist_path / new_name)
        
        for extra_ext in ['.json', '.jpg', '.png', '.webp']:
            extra_src = self.current_playlist_path / f"{base}{extra_ext}"
            if extra_src.exists():
                shutil.copy2(extra_src, self.current_playlist_path / f"{base} ({counter}){extra_ext}")
                
        self.update_songs_list()
        self.save_playlist()

    def delete_current_track(self):
        item = self.songs_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        path = self.current_playlist_path / name

        is_playing_this = self.player.source().toLocalFile() == str(path)

        def try_remove(attempt=0):
            try:
                if path.exists():
                    os.remove(path)
                base = path.stem
                for extra_ext in ['.json', '.jpg', '.png', '.webp']:
                    extra_file = path.parent / f"{base}{extra_ext}"
                    if extra_file.exists():
                        os.remove(extra_file)
                        
                row = self.songs_list.row(item)
                if row >= 0:
                    self.songs_list.takeItem(row)
                self.save_playlist()
                self.update_songs_list()
            except PermissionError:
                if attempt < 10:
                    QTimer.singleShot(200, lambda: try_remove(attempt + 1))
                else:
                    QMessageBox.critical(
                        self, "Error",
                        "Can't delete:\nФайл всё ещё занят другим процессом. Попробуйте ещё раз."
                    )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Can't delete:\n{e}")

        if is_playing_this:
            self.player.stop()
            self.player.setSource(QUrl())
            self.mini_bar.set_playing(False)
            self.now_playing.setText("Now Playing — None")
            self.current_track_index = -1
            self.lyrics_display.clear()
            self.cover_label.clear()
            QTimer.singleShot(250, try_remove)
        else:
            try_remove()

    def rename_current_track(self):
        item = self.songs_list.currentItem()
        if not item: return
        old_name = item.data(Qt.UserRole)
        old_path = self.current_playlist_path / old_name
        old_stem = Path(old_name).stem
        ext = Path(old_name).suffix
        
        new_stem, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_stem)
        if ok and new_stem.strip():
            new_name = new_stem.strip() + ext
            try:
                is_current = self.player.source().toLocalFile() == str(old_path)
                if is_current:
                    self.player.stop()
                    self.player.setSource(QUrl())
                os.rename(old_path, self.current_playlist_path / new_name)
                
                for extra_ext in ['.json', '.jpg', '.png', '.webp']:
                    old_extra = self.current_playlist_path / f"{old_stem}{extra_ext}"
                    if old_extra.exists():
                        os.rename(old_extra, self.current_playlist_path / f"{new_stem.strip()}{extra_ext}")
                        
                self.save_playlist()
                self.update_songs_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Can't rename:\n{e}")

    def add_song(self):
        if not self.current_playlist: return
        dlg = AddSongDialog(self, self.current_playlist)
        if dlg.exec():
            self.update_songs_list()
            self.save_playlist()

    def save_playlist(self):
        if not self.current_playlist: return
        songs = [self.songs_list.item(i).data(Qt.UserRole) for i in range(self.songs_list.count())]
        data = {'name': self.current_playlist, 'songs': songs}
        with open(PLAYLISTS_PATH / f"{self.current_playlist}.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = PLAYLISTS_PATH / name / "songs"
        self.current_playlist_path.mkdir(parents=True, exist_ok=True)
        self.playlist_name.setText(name)
        self.update_songs_list()

class HotkeySignals(QObject):
    play_pause = Signal()
    next_track = Signal()
    prev_track = Signal()

class MusicPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CloudPlayer")
        self.resize(1100, 750)

        if (SCRIPT_DIR / "icon.ico").is_file():
            self.setWindowIcon(QIcon(str(SCRIPT_DIR / "icon.ico")))

        self.setup_ui()
        self.load_playlists()

        self.rec_title = ""
        self.rec_artist = ""
        self.rec_data = []
        self.rec_thread = RecommendationFetcher()
        self.rec_thread.rec_ready.connect(self.display_recommendation)
        self.refresh_recommendation()

        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.play_pause.connect(lambda: self.stack.widget(1).toggle_playback())
        self.hotkey_signals.next_track.connect(lambda: self.stack.widget(1).play_next_track())
        self.hotkey_signals.prev_track.connect(lambda: self.stack.widget(1).play_prev_track())

        self.shortcut_prev = QShortcut(QKeySequence("F7"), self)
        self.shortcut_play_pause = QShortcut(QKeySequence("F8"), self)
        self.shortcut_next = QShortcut(QKeySequence("F9"), self)
        for shortcut in (self.shortcut_prev, self.shortcut_play_pause, self.shortcut_next):
            shortcut.setContext(Qt.ApplicationShortcut)
        self.shortcut_prev.activated.connect(self.playlist_view.play_prev_track)
        self.shortcut_play_pause.activated.connect(self.playlist_view.toggle_playback)
        self.shortcut_next.activated.connect(self.playlist_view.play_next_track)
        
        discord_rpc.connect()
        
    def setup_ui(self):
        self.stack = QStackedWidget()
        main_view = QWidget()
        self.playlist_view = PlaylistView(self)

        self.playlist_view.back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))

        main_layout = QVBoxLayout(main_view)
        main_layout.setContentsMargins(30, 30, 30, 30)
        
        label = QLabel("Your Playlists")
        label.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 10px;")
        
        self.playlist_list = QListWidget()
        self.playlist_list.setStyleSheet("QListWidget::item { font-size: 16px; padding: 12px; }")
        self.playlist_list.itemDoubleClicked.connect(self.open_playlist)

        self.rec_box = QWidget()
        self.rec_box.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid {BUTTON_BORDER}; border-radius: 4px; margin-top: 5px;")
        rec_outer = QVBoxLayout(self.rec_box)
        rec_outer.setContentsMargins(15, 10, 15, 10)
        rec_outer.setSpacing(6)

        rec_header = QLabel("Рекомендации для вас")
        rec_header.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffffff;")
        rec_outer.addWidget(rec_header)

        self.rec_rows = []
        for i in range(RecommendationFetcher.TARGET_COUNT):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            lbl = QLabel("Подбираем трек...")
            lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: {TEXT_MUTED};")

            btn = QPushButton("+")
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"background-color: {BUTTON_BG}; border: 1px solid {BUTTON_BORDER}; "
                f"font-weight: bold; font-size: 14px; padding:0;"
            )
            btn.clicked.connect(lambda _checked=False, idx=i: self.add_recommendation_to_playlist(idx))

            row_layout.addWidget(lbl, stretch=1)
            row_layout.addWidget(btn)
            rec_outer.addWidget(row_widget)

            self.rec_rows.append((lbl, btn, i))

            btn.setEnabled(False)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("New Playlist")
        remove_btn = QPushButton("Remove Playlist")
        for btn in (add_btn, remove_btn):
            btn.setFixedHeight(40)
            btn.setFixedWidth(150)
            
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()

        main_layout.addWidget(label)
        main_layout.addWidget(self.playlist_list, stretch=1)
        main_layout.addWidget(self.rec_box)
        main_layout.addSpacing(15)
        main_layout.addLayout(btn_layout)

        self.stack.addWidget(main_view)
        self.stack.addWidget(self.playlist_view)
        self.setCentralWidget(self.stack)

        add_btn.clicked.connect(self.create_playlist)
        remove_btn.clicked.connect(self.remove_playlist)

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ 
                background-color: {BG_COLOR}; 
                color: {TEXT_COLOR}; 
                font-family: 'Segoe UI', sans-serif; 
            }}
            QPushButton {{
                background-color: {BUTTON_BG};
                border: 1px solid {BUTTON_BORDER}; 
                border-radius: 4px;
                padding: 8px 15px; 
                font-size: 13px; 
            }}
            QPushButton:hover {{ 
                background-color: {BUTTON_HOVER}; 
                border: 1px solid #444444;
            }}
            QLineEdit {{
                background-color: {PANEL_BG};
                border: 1px solid {BUTTON_BORDER};
                border-radius: 4px;
                padding: 10px;
                color: {TEXT_COLOR};
            }}
            QListWidget {{
                background-color: {PANEL_BG}; 
                border: 1px solid {BUTTON_BORDER}; 
                border-radius: 4px; 
                outline: 0;
            }}
            QListWidget::item {{
                background-color: transparent; 
                border-radius: 4px;
                padding: 8px 10px;
            }}
            QListWidget::item:hover {{ 
                background-color: {BUTTON_HOVER}; 
            }}
            QListWidget::item:selected {{ 
                background-color: {ACCENT_COLOR}; 
                color: #ffffff;
            }}
            QTextEdit {{
                background-color: {PANEL_BG};
                color: #cccccc;
                border: 1px solid {BUTTON_BORDER};
                border-radius: 4px;
                font-size: 13px;
            }}
            QSlider::groove:horizontal {{ 
                height: 4px; background: {BUTTON_BORDER}; border-radius: 2px; 
            }}
            QSlider::handle:horizontal {{ 
                background: {ACCENT_COLOR}; border-radius: 6px; width: 12px; margin: -4px 0; 
            }}
            QScrollBar:vertical {{
                border: none; background: {PANEL_BG}; width: 10px;
            }}
            QScrollBar::handle:vertical {{
                background: #3A3A3A; min-height: 30px; border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #555555; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ background: none; height: 0; }}
        """)

    def display_recommendation(self, recommendations):
        self.rec_data = recommendations or []

        if not self.rec_data:
            for lbl, btn, _ in self.rec_rows:
                lbl.setText("Добавьте трек в плейлист, чтобы получить рекомендацию.")
                lbl.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {TEXT_MUTED};")
                btn.setEnabled(False)
            return

        for i, (lbl, btn, _idx) in enumerate(self.rec_rows):
            if i < len(self.rec_data):
                artist, title = self.rec_data[i]
                lbl.setText(f"{artist} — {title}")
                lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: #ffffff;")
                btn.setEnabled(True)
            else:
                lbl.setText("—")
                lbl.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {TEXT_MUTED};")
                btn.setEnabled(False)

    def refresh_recommendation(self):
        if self.rec_thread.isRunning():
            return
        self.rec_thread.start()

    def add_recommendation_to_playlist(self, row_index: int):
        if not self.rec_data or row_index >= len(self.rec_data):
            return
        artist, title = self.rec_data[row_index]

        playlists = [self.playlist_list.item(i).text() for i in range(self.playlist_list.count())]
        if not playlists:
            QMessageBox.warning(self, "Упс", "Создай сначала хотя бы один плейлист.")
            return

        target, ok = QInputDialog.getItem(self, "Добавить рекомендацию", "Выберите плейлист:", playlists, 0, False)
        if not (ok and target):
            return

        dest_dir = PLAYLISTS_PATH / target / "songs"
        dest_dir.mkdir(parents=True, exist_ok=True)

        query = f"{artist} {title}"
        lbl, btn, _ = self.rec_rows[row_index]
        old_text = lbl.text()
        lbl.setText(f"⏳ Добавляем: {old_text} ...")
        lbl.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {TEXT_MUTED};")
        btn.setEnabled(False)

        self.bg_down = BackgroundDownloader(query, dest_dir)
        self.bg_down.finished_signal.connect(
            lambda status, msg, name=target, ot=old_text, ri=row_index:
                self.on_rec_downloaded(status, msg, name, ot, ri)
        )
        self.bg_down.start()

    def on_rec_downloaded(self, status, msg, playlist_name, old_text, row_index):
        if status:
            QMessageBox.information(self, "Готово", f"Трек успешно стянут с SoundCloud в '{playlist_name}'!")
            if self.stack.currentIndex() == 1 and self.playlist_view.current_playlist == playlist_name:
                self.playlist_view.update_songs_list()
            self.refresh_recommendation()
        else:
            lbl, btn, _ = self.rec_rows[row_index]
            lbl.setText(old_text)
            lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: #ffffff;")
            btn.setEnabled(True)
            QMessageBox.critical(self, "Ошибка скачивания", f"Не удалось загрузить:\n{msg}")

    def open_playlist(self, item):
        name = item.text()
        self.playlist_view.load_playlist(name)
        self.stack.setCurrentIndex(1)

    def create_playlist(self):
        name, ok = QInputDialog.getText(self, "New Playlist", "Name:")
        if ok and name.strip():
            name = name.strip()
            self.playlist_list.addItem(name)
            (PLAYLISTS_PATH / name / "songs").mkdir(parents=True, exist_ok=True)
            with open(PLAYLISTS_PATH / f"{name}.json", 'w') as f:
                json.dump({'name': name, 'songs': []}, f)

    def load_playlists(self):
        if PLAYLISTS_PATH.exists():
            for p in PLAYLISTS_PATH.glob("*.json"):
                self.playlist_list.addItem(p.stem)

    def remove_playlist(self):
        item = self.playlist_list.currentItem()
        if not item: return
        name = item.text()
        try:
            shutil.rmtree(PLAYLISTS_PATH / name)
            (PLAYLISTS_PATH / f"{name}.json").unlink(missing_ok=True)
        except Exception as e:
            pass
        self.playlist_list.takeItem(self.playlist_list.row(item))


if __name__ == "__main__":
    DOCS_PATH.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_PATH.mkdir(exist_ok=True)
    PLAYLISTS_PATH.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MusicPlayer()
    window.show()
    sys.exit(app.exec())
