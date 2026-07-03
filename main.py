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

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QLineEdit, QDialog, QInputDialog,
    QFileDialog, QMessageBox, QProgressDialog, QSlider, QStyle, QStackedWidget, QTextEdit
)
from PySide6.QtGui import (
    QIcon, QPixmap, QFontDatabase, QMovie, QKeyEvent, QKeySequence,
    QPalette, QColor, QCursor, QScreen, QPainter
)
from PySide6.QtCore import (
    Qt, QTimer, QUrl, QSize, QPoint, QRect, QFileInfo, QStandardPaths, QThread, Signal, QObject
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput


# === Пути ===
DOCS_PATH = Path.home() / "Documents" / "CloudPlayer"
DOWNLOADS_PATH = DOCS_PATH / "downloads"
PLAYLISTS_PATH = DOCS_PATH / "playlists"
SCRIPT_DIR = Path(__file__).parent
FFMPEG_PATH = SCRIPT_DIR / "ffmpeg.exe"

# === Цвета строгой плоской темы (Flat 2D Design) ===
BG_COLOR = "#121212"
PANEL_BG = "#1A1A1A"
BUTTON_BG = "#222222"
BUTTON_HOVER = "#2D2D2D"
BUTTON_BORDER = "#333333"
ACCENT_COLOR = "#0D47A1"  # Темно-синий цвет выделения
TEXT_COLOR = "#E0E0E0"
TEXT_MUTED = "#888888"
ICON_COLOR = "#FFFFFF"  # Цвет всех svg-иконок в интерфейсе

GENIUS_TOKEN = "7FBtGwlCeRvyuPf1fxFdR5_qTy3ARuxdbcaAHenQ1VXBXaHJJoJhyxB-MSVlhGqk"

def format_time(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def colored_icon(filename, color=ICON_COLOR, size=64):
    """Загружает svg-иконку и перекрашивает её в заданный цвет (по умолчанию белый),
    независимо от того, каким цветом залит исходный svg-файл."""
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


# === Кастомный парсер HTML для Genius ===
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


# === Поток для получения метаданных, обложки и текста из Genius ===
# === Поток для получения метаданных, обложки и текста из Genius ===
# === Обновленный поток метаданных с поддержкой обложек от yt-dlp ===
# === Обновленный поток метаданных с поддержкой обложек от yt-dlp ===
class TrackMetaFetcher(QThread):
    meta_ready = Signal(dict)

    def __init__(self, song_path):
        super().__init__()
        self.song_path = Path(song_path)

    def run(self):
        base_name = self.song_path.stem  # Название файла без .mp3
        target_dir = self.song_path.parent # Папка, где лежит трек (плейлист или загрузки)
        
        # Парсим название из имени файла
        raw_name = base_name
        raw_name = re.sub(r'^\d+[\.\s\-]*', '', raw_name)  # Убираем номер трека в начале
        raw_name = re.sub(r'\(.*?\)|\[.*?\]', '', raw_name).strip()  # Убираем скобки

        artist = "Неизвестен"
        title = raw_name
        
        # Пытаемся разделить по " - " или "-"
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
            "cover_bytes": None
        }

        # 1. ПОПЫТКА НАЙТИ ЛОКАЛЬНУЮ ОБЛОЖКУ ОТ YT-DLP В ПАПКЕ С ТРЕКОМ
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
                print(f"[Local cover error] {e}")

        # 2. РАБОТА С GENIUS (для текста и обложки, если не найдена локально)
        if not raw_name:
            self.meta_ready.emit(result_data)
            return

        expected_artist = artist
        expected_title = title
            
        search_query = f"{expected_artist} {expected_title}".strip()
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
                hit_result = hits[0]["result"]
                
                if expected_artist:
                    exp_art_lower = expected_artist.lower()
                    exp_tit_lower = expected_title.lower()
                    for hit in hits:
                        item = hit["result"]
                        hit_artist = item.get("primary_artist", {}).get("name", "").lower()
                        hit_title = item.get("title", "").lower()
                        if (exp_art_lower in hit_artist or hit_artist in exp_art_lower) and \
                           (exp_tit_lower in hit_title or hit_title in exp_tit_lower):
                            hit_result = item
                            break

                song_id = hit_result.get("id")
                
                # Получаем обложку, если не найдена локально
                if not result_data["cover_bytes"]:
                    cover_url = hit_result.get("song_art_image_thumbnail_url")
                    if cover_url:
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

# === Фоновый парсер рекомендаций для главного меню ===
class RecommendationFetcher(QThread):
    rec_ready = Signal(str, str) # artist, title

    def run(self):
        artists = ["White Punk", "Kizaru", "Big Baby Tape", "Aarne", "Bushido Zho", "Lovesomemama"]
        
        # Попробуем собрать реальных артистов из файлов пользователя
        try:
            found_artists = []
            if PLAYLISTS_PATH.exists():
                for p_dir in PLAYLISTS_PATH.iterdir():
                    songs_dir = p_dir / "songs"
                    if songs_dir.exists():
                        for f in songs_dir.glob("*.*"):
                            if "-" in f.stem:
                                parts = f.stem.split("-")
                                found_artists.append(parts[0].strip())
            if found_artists:
                artists = list(set(found_artists))
        except Exception:
            pass

        random_artist = random.choice(artists)
        headers = {
            "Authorization": f"Bearer {GENIUS_TOKEN}",
            "User-Agent": "Mozilla/5.0"
        }
        
        try:
            search_url = f"https://api.genius.com/search?q={urllib.parse.quote(random_artist)}"
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                hits = data.get("response", {}).get("hits", [])
                if hits:
                    random_hit = random.choice(hits)["result"]
                    self.rec_ready.emit(random_hit.get("primary_artist", {}).get("name", random_artist), random_hit.get("title", "Трек"))
                    return
        except Exception:
            pass
        
        self.rec_ready.emit(random_artist, "Популярный трек")


# === Фоновый поток для бесшумного скачивания рекомендации ===
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
                'format': 'bestaudio/best',  # Берем лучший исходник
                'writethumbnail': True,      # Скачиваем обложку с SoundCloud
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320', # МАКСИМАЛЬНОЕ КАЧЕСТВО ЗВУКА
                    },
                    {
                        'key': 'FFmpegThumbnailsConvertor',
                        'format': 'jpg', # Делаем обложку в формате JPG
                    }
                ],
                'outtmpl': str(self.dest_path / '%(title).200s.%(ext)s'),
                'ffmpeg_location': str(FFMPEG_PATH),
                'quiet': True,
                'no_warnings': True,
                'windows_creation_flags': 0x08000000,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(f"scsearch1:{self.query}", download=True)
            self.finished_signal.emit(True, "Успешно добавлено!")
        except Exception as e:
            self.finished_signal.emit(False, str(e))


# === PlayerControls ===
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

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)

        self.prev_btn = QPushButton(" PREV")
        self.play_btn = QPushButton(" PLAY")
        self.next_btn = QPushButton(" NEXT")
        self.duplicate_btn = QPushButton(" DUPLICATE")
        self.delete_btn = QPushButton(" DELETE")
        self.rename_btn = QPushButton(" RENAME")

        def load_svg(btn, filename):
            icon = colored_icon(filename)
            if not icon.isNull():
                btn.setIcon(icon)
                btn.setIconSize(QSize(14, 14))

        load_svg(self.prev_btn, "prev.svg")
        load_svg(self.play_btn, "play.svg")
        load_svg(self.next_btn, "next.svg")
        load_svg(self.duplicate_btn, "copy.svg")
        load_svg(self.delete_btn, "delete.svg")
        load_svg(self.rename_btn, "rename.svg")

        for btn in [self.prev_btn, self.play_btn, self.next_btn,
                    self.duplicate_btn, self.delete_btn, self.rename_btn]:
            btn.setFixedHeight(36)

        controls_layout.addStretch()
        controls_layout.addWidget(self.prev_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.next_btn)
        controls_layout.addWidget(self.duplicate_btn)
        controls_layout.addWidget(self.delete_btn)
        controls_layout.addWidget(self.rename_btn)
        controls_layout.addStretch()

        layout.addLayout(time_layout)
        layout.addLayout(controls_layout)
        self.progress_bar.sliderMoved.connect(self.on_seek)

    def on_seek(self, position):
        if hasattr(self.parent(), 'seek_position'):
            self.parent().seek_position(position)


# === MiniControlBar ===
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


# === AddSongDialog (SoundCloud Only) ===
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

        # Ищем строго по SoundCloud через scsearch
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
                'writethumbnail': True,      # Скачиваем обложку
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320', # МАКСИМАЛЬНОЕ КАЧЕСТВО ЗВУКА
                    },
                    {
                        'key': 'FFmpegThumbnailsConvertor',
                        'format': 'jpg',
                    }
                ],
                'outtmpl': str(self.playlist_path / '%(title).200s.%(ext)s'),
                'ffmpeg_location': str(FFMPEG_PATH),
                'quiet': True,
                'no_warnings': True,
                'windows_creation_flags': 0x08000000,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url_or_path, download=True)
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
        files, _ = QFileDialog.getOpenFileNames(self, "Select Audio Files", "", "Audio (*.mp3 *.wav *.m4a)")
        if files:
            if any(self._download_one(f) for f in files):
                self.accept()


# === PlaylistView ===
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

        # Header
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

        # Центр: Слева список треков, Справа (1/3.5) — Блок Информации Spotify-style
        center_layout = QHBoxLayout()
        center_layout.setSpacing(20)

        self.songs_list = QListWidget()
        # Пропорция 2.5 к 1 дает суммарно 3.5, правая часть занимает ровно 1/3.5 часть
        center_layout.addWidget(self.songs_list, stretch=25)

        # Spotify Right Sidebar (1 / 3.5)
        self.right_sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.right_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(12)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(240, 240)
        self.cover_label.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid {BUTTON_BORDER}; border-radius: 4px;")
        self.cover_label.setScaledContents(True)
        
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

        # Controls Layout
        self.player_controls = PlayerControls()
        layout.addWidget(self.player_controls)

        self.mini_bar = MiniControlBar()
        layout.addWidget(self.mini_bar)

        # Нижняя панель с кнопками Shuffle и Add Song
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

        # Media Setup
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        # Connections
        self.songs_list.itemDoubleClicked.connect(self.play_song)
        self.add_song_btn.clicked.connect(self.add_song)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        
        self.player_controls.play_btn.clicked.connect(self.toggle_playback)
        self.player_controls.next_btn.clicked.connect(self.play_next_track)
        self.player_controls.prev_btn.clicked.connect(self.play_prev_track)
        self.player_controls.duplicate_btn.clicked.connect(self.duplicate_current_track)
        self.player_controls.delete_btn.clicked.connect(self.delete_current_track)
        self.player_controls.rename_btn.clicked.connect(self.rename_current_track)
        
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
            for i, f in enumerate(files, 1):
                item = QListWidgetItem(f"{i}. {f.stem}")
                item.setData(Qt.UserRole, f.name)
                self.songs_list.addItem(item)

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
            self.player_controls.play_btn.setText(" PAUSE")
            self.mini_bar.set_playing(True)
            
            stem = Path(filename).stem
            self.now_playing.setText(f"Now Playing — {stem}")
            self.current_track_index = idx

            # Обнуляем UI до загрузки Genius
            self.track_title.setText(stem)
            self.track_artist_prod.setText("Загрузка инфо...")
            self.cover_label.clear()
            self.lyrics_display.setText("Загрузка текста...")

# ОБНОВЛЕНО: Передаем ПОЛНЫЙ ПУТЬ к треку, а не только stem
            if self.meta_thread and self.meta_thread.isRunning():
                self.meta_thread.terminate()
                
            self.meta_thread = TrackMetaFetcher(path)  # <- Изменение тут
            self.meta_thread.meta_ready.connect(self.apply_track_metadata)
            self.meta_thread.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Can't play:\n{e}")

    def apply_track_metadata(self, data):
        self.track_title.setText(data["title"])
        self.track_artist_prod.setText(data["artist"])
        self.lyrics_display.setText(data["lyrics"])
        
        if data["cover_bytes"]:
            px = QPixmap()
            px.loadFromData(data["cover_bytes"])
            self.cover_label.setPixmap(px)
        else:
            self.cover_label.setText(" No Cover")

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.player_controls.play_btn.setText(" PLAY")
            self.mini_bar.set_playing(False)
        else:
            item = self.songs_list.currentItem()
            if item:
                if not self.player.source().isValid():
                    self.play_song(item)
                else:
                    self.player.play()
                    self.player_controls.play_btn.setText(" PAUSE")
                    self.mini_bar.set_playing(True)
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
                row = self.songs_list.row(item)
                if row >= 0:
                    self.songs_list.takeItem(row)
                self.save_playlist()
                self.update_songs_list()
            except PermissionError:
                # Windows ещё не успел освободить дескриптор файла — пробуем ещё раз чуть позже
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
            # Полностью останавливаем и отвязываем плеер от файла перед удалением,
            # иначе Windows держит файл занятым и удаление падает с WinError 32.
            self.player.stop()
            self.player.setSource(QUrl())
            self.player_controls.play_btn.setText(" PLAY")
            self.mini_bar.set_playing(False)
            self.now_playing.setText("Now Playing — None")
            self.current_track_index = -1
            self.lyrics_display.clear()
            self.cover_label.clear()
            # Небольшая задержка, чтобы ОС успела освободить файловый дескриптор
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


# === HotkeySignals ===
class HotkeySignals(QObject):
    play_pause = Signal()
    next_track = Signal()
    prev_track = Signal()


# === MusicPlayer (Главное окно) ===
class MusicPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CloudPlayer")
        self.resize(1100, 750)

        if (SCRIPT_DIR / "icon.ico").is_file():
            self.setWindowIcon(QIcon(str(SCRIPT_DIR / "icon.ico")))

        self.setup_ui()
        self.load_playlists()

        # Запуск фонового генератора случайных рекомендаций
        self.rec_title = ""
        self.rec_artist = ""
        self.rec_thread = RecommendationFetcher()
        self.rec_thread.rec_ready.connect(self.display_recommendation)
        self.rec_thread.start()

        # Глобальные хоткеи
        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.play_pause.connect(lambda: self.stack.widget(1).toggle_playback())
        self.hotkey_signals.next_track.connect(lambda: self.stack.widget(1).play_next_track())
        self.hotkey_signals.prev_track.connect(lambda: self.stack.widget(1).play_prev_track())

    def setup_ui(self):
        self.stack = QStackedWidget()
        main_view = QWidget()
        self.playlist_view = PlaylistView(self)

        # Коннектим кнопку Назад правильно
        self.playlist_view.back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))

        main_layout = QVBoxLayout(main_view)
        main_layout.setContentsMargins(30, 30, 30, 30)
        
        label = QLabel("Your Playlists")
        label.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 10px;")
        
        self.playlist_list = QListWidget()
        self.playlist_list.setStyleSheet("QListWidget::item { font-size: 16px; padding: 12px; }")
        self.playlist_list.itemDoubleClicked.connect(self.open_playlist)

        # Рекомендация в самом низу блока плейлистов
        self.rec_box = QWidget()
        self.rec_box.setStyleSheet(f"background-color: {PANEL_BG}; border: 1px solid {BUTTON_BORDER}; border-radius: 4px; margin-top: 5px;")
        rec_layout = QHBoxLayout(self.rec_box)
        rec_layout.setContentsMargins(15, 10, 15, 10)
        
        self.rec_label = QLabel("Рекомендация: Подбираем трек...")
        self.rec_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        
        self.rec_add_btn = QPushButton("+")
        self.rec_add_btn.setFixedSize(28, 28)
        self.rec_add_btn.setStyleSheet(f"background-color: {BUTTON_BG}; border: 1px solid {BUTTON_BORDER}; font-weight: bold; font-size: 14px; padding:0;")
        self.rec_add_btn.clicked.connect(self.add_recommendation_to_playlist)
        
        rec_layout.addWidget(self.rec_label)
        rec_layout.addStretch()
        rec_layout.addWidget(self.rec_add_btn)

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

        # ГЛОБАЛЬНЫЙ СТИЛЬ (ПЛОСКИЙ 2D И ТЕМНО-СИНИЙ АКЦЕНТ)
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

    def display_recommendation(self, artist, title):
        self.rec_artist = artist
        self.rec_title = title
        self.rec_label.setText(f"Рекомендация: {artist} — {title}")

    def add_recommendation_to_playlist(self):
        if not self.rec_title:
            return
            
        playlists = [self.playlist_list.item(i).text() for i in range(self.playlist_list.count())]
        if not playlists:
            QMessageBox.warning(self, "Упс", "Создай сначала хотя бы один плейлист.")
            return

        target, ok = QInputDialog.getItem(self, "Добавить рекомендацию", "Выберите плейлист:", playlists, 0, False)
        if ok and target:
            dest_dir = PLAYLISTS_PATH / target / "songs"
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            query = f"{self.rec_artist} {self.rec_title}"
            self.rec_label.setText("Добавление в фоновом режиме...")
            self.rec_add_btn.setEnabled(False)
            
            self.bg_down = BackgroundDownloader(query, dest_dir)
            self.bg_down.finished_signal.connect(lambda status, msg: self.on_rec_downloaded(status, msg, target))
            self.bg_down.start()

    def on_rec_downloaded(self, status, msg, playlist_name):
        self.rec_add_btn.setEnabled(True)
        self.rec_label.setText(f"Рекомендация: {self.rec_artist} — {self.rec_title}")
        if status:
            QMessageBox.information(self, "Готово", f"Трек успешно стянут с SoundCloud в '{playlist_name}'!")
            if self.stack.currentIndex() == 1 and self.playlist_view.current_playlist == playlist_name:
                self.playlist_view.update_songs_list()
        else:
            QMessageBox.critical(self, "Ошибка скачивания", f"Не удалось загрузить: {msg}")

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
            print(f"[Remove error] {e}")
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
