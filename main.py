from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from pathlib import Path
import sys
import os
import json
import yt_dlp
import urllib.request

ROOT_PATH = "C:/PlayerRelease"
DOWNLOADS_PATH = f"{ROOT_PATH}/downloads"
PLAYLISTS_PATH = f"{ROOT_PATH}/playlists"
FFMPEG_PATH = "./ffmpeg.exe"

DOCS_PATH = str(Path.home() / "Documents" / "CloudPlayer")
DOWNLOADS_PATH = str(Path(DOCS_PATH) / "downloads")

THEME_COLOR = "#1a1b26"  
BUTTON_COLOR = "#2E3440"
BUTTON_HOVER = "#3B4252"
ACCENT_COLOR = "#0A84FF"
LIST_BG = "#1E1E2E"
ITEM_BG = "#24283B"

class ConnectionStatus(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        
        self.status_icon = QLabel()
        self.status_text = QLabel("Offline")
        self.net_icon = QLabel()
        
        self.status_icon.setFixedSize(12, 12)
        self.net_icon.setFixedSize(16, 16)
        
        self.net_icon.setPixmap(QPixmap("./icons/wifi.png").scaled(16, 16))
        self.update_status()
        
        layout.addWidget(self.status_icon)
        layout.addWidget(self.status_text)
        layout.addWidget(self.net_icon)
        layout.addStretch()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(5000)
        
    def update_status(self):
        try:
            urllib.request.urlopen('http://google.com', timeout=1)
            self.status_icon.setStyleSheet("""
                background-color: #50fa7b;
                border-radius: 6px;
            """)
            self.status_text.setText("Online")
        except:
            self.status_icon.setStyleSheet("""
                background-color: #ff5555;
                border-radius: 6px;
            """)
            self.status_text.setText("Offline")

class PlayerControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        time_layout = QHBoxLayout()
        self.current_time = QLabel("0:00")
        self.total_time = QLabel("0:00")
        self.progress_bar = QSlider(Qt.Horizontal)
        
        for label in [self.current_time, self.total_time]:
            label.setStyleSheet("color: #b3b3b3; font-size: 12px;")
            
        time_layout.addWidget(self.current_time)
        time_layout.addWidget(self.progress_bar)
        time_layout.addWidget(self.total_time)
        
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(20)
        
        self.prev_btn = QPushButton()
        self.play_btn = QPushButton()
        self.next_btn = QPushButton()
        self.duplicate_btn = QPushButton()
        self.delete_btn = QPushButton()
        self.rename_btn = QPushButton()

        self.prev_btn.setIcon(QIcon("./icons/prev.png"))
        self.play_btn.setIcon(QIcon("./icons/play.png"))
        self.next_btn.setIcon(QIcon("./icons/next.png"))
        self.duplicate_btn.setIcon(QIcon("./icons/copy.png"))
        self.delete_btn.setIcon(QIcon("./icons/delete.png"))
        self.rename_btn.setIcon(QIcon("./icons/rename.png"))
        
        self.prev_btn.setText("  PREV")
        self.play_btn.setText("  PLAY")
        self.next_btn.setText("  NEXT")
        self.duplicate_btn.setText("  DUPLICATE")
        self.delete_btn.setText("  DELETE")
        self.rename_btn.setText("  RENAME")

        button_style = f"""
            QPushButton {{
                background-color: {BUTTON_COLOR};
                border: none;
                border-radius: 20px;
                font-weight: bold;
                font-size: 12px;
                padding: 10px 15px;
                text-align: left;
                padding-left: 15px;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER};
            }}
        """
        
        self.prev_btn.setFixedSize(110, 40)
        self.play_btn.setFixedSize(110, 40)
        self.next_btn.setFixedSize(110, 40)
        self.duplicate_btn.setFixedSize(130, 40)
        self.delete_btn.setFixedSize(110, 40)
        self.rename_btn.setFixedSize(110, 40)

        for btn in [self.prev_btn, self.play_btn, self.next_btn, 
                   self.duplicate_btn, self.delete_btn, self.rename_btn]:
            btn.setStyleSheet(button_style)
            btn.setIconSize(QSize(16, 16))

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

        self.progress_bar.setTracking(True)
        self.progress_bar.sliderMoved.connect(self.on_seek)
        
    def on_seek(self, position):
        if self.parent():
            self.parent().seek_position(position)

class AddSongDialog(QDialog):
    def __init__(self, parent=None, playlist_name=None):
        super().__init__(parent)
        self.playlist_name = playlist_name
        self.playlist_path = f"{PLAYLISTS_PATH}/{playlist_name}/songs" if playlist_name else DOWNLOADS_PATH
        self.setup_ui()
        
    def setup_ui(self):
        self.setWindowTitle("Add Song")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search song...")
        
        search_soundcloud_btn = QPushButton("Search SoundCloud")
        search_youtube_btn = QPushButton("Search YouTube")
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_soundcloud_btn)
        search_layout.addWidget(search_youtube_btn)
        
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(300)
        self.results_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        
        url_btn = QPushButton("Add from URL")
        file_btn = QPushButton("Add from File")
        
        for btn in [url_btn, file_btn]:
            btn.setFixedHeight(50)
            
        layout.addLayout(search_layout)
        layout.addWidget(self.results_list)
        layout.addWidget(url_btn)
        layout.addWidget(file_btn)
        
        url_btn.clicked.connect(self.add_from_url)
        file_btn.clicked.connect(self.add_from_file)
        search_soundcloud_btn.clicked.connect(lambda: self.search_songs('scsearch'))
        search_youtube_btn.clicked.connect(lambda: self.search_songs('ytsearch'))
        self.results_list.itemDoubleClicked.connect(self.download_selected)
        
    def search_songs(self, platform='scsearch'):
        query = self.search_input.text()
        if query:
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'ffmpeg_location': FFMPEG_PATH,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    results = ydl.extract_info(f"{platform}10:song {query}", download=False)
                    
                    self.results_list.clear()
                    if results and 'entries' in results:
                        for entry in results['entries']:
                            title = entry.get('title', 'Unknown')
                            uploader = entry.get('uploader', 'Unknown')
                            duration = entry.get('duration_string', '')
                            item = QListWidgetItem(f"🎵 {title} - {uploader} ({duration})")
                            item.setData(Qt.UserRole, entry['url'])
                            self.results_list.addItem(item)
                    
                    results = ydl.extract_info(f"{platform}5:artist {query}", download=False)
                    if results and 'entries' in results:
                        for entry in results['entries']:
                            title = entry.get('title', 'Unknown')
                            uploader = entry.get('uploader', 'Unknown')
                            duration = entry.get('duration_string', '')
                            item = QListWidgetItem(f"👤 {title} - {uploader} ({duration})")
                            item.setData(Qt.UserRole, entry['url'])
                            self.results_list.addItem(item)
                    
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Search error: {str(e)}")
                
    def download_selected(self):
        selected_items = self.results_list.selectedItems()
        total = len(selected_items)
        
        if total > 0:
            progress = QProgressDialog("Downloading songs...", "Cancel", 0, total, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            
            for i, item in enumerate(selected_items):
                url = item.data(Qt.UserRole)
                if url:
                    progress.setValue(i)
                    progress.setLabelText(f"Downloading {i+1}/{total}: {item.text()}")
                    if progress.wasCanceled():
                        break
                    self.download_song(url)
            
            progress.setValue(total)
            self.accept()

    def download_song(self, url):
        try:
            os.makedirs(self.playlist_path, exist_ok=True)
            progress = QProgressDialog("Downloading...", None, 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()

            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                }],
                'outtmpl': f'{self.playlist_path}/%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                progress.setLabelText(f"Downloading: {info.get('title', 'Unknown')}")
                ydl.download([url])
            self.accept()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Download error: {str(e)}")
        finally:
            progress.close()

    def add_from_url(self):
        url, ok = QInputDialog.getText(self, "Add from URL", "Enter URL:")
        if ok and url:
            self.download_song(url)
            
    def add_from_file(self):
        file_dialog = QFileDialog(self)
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        file_dialog.setNameFilter("Audio Files (*.mp3 *.wav *.ogg *.flac);;All Files (*)")
        file_dialog.setViewMode(QFileDialog.ViewMode.List)
        
        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            for file in selected_files:
                self.download_song(file)

class PlaylistView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.songs = []
        self.current_track_index = -1
        self.setup_ui()
        self.update_songs_list()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        
        status_layout = QHBoxLayout()
        self.connection_status = ConnectionStatus()
        status_layout.addWidget(self.connection_status)
        status_layout.addStretch()
        
        header_layout = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(100, 40)
        self.playlist_name = QLabel("Playlist Name")
        
        volume_layout = QHBoxLayout()
        
        self.now_playing = QLabel("Now Playing - None")
        self.now_playing.setStyleSheet("color: #b3b3b3; font-size: 12px;")
        
        self.volume_btn = QPushButton()
        self.volume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.volume_btn.setFixedSize(24, 24)
        self.volume_btn.setFlat(True)
        self.volume_btn.clicked.connect(self.toggle_mute)
        
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        
        volume_layout.addWidget(self.now_playing)
        volume_layout.addStretch()
        volume_layout.addWidget(self.volume_btn)
        volume_layout.addWidget(self.volume_slider)
        
        header_layout.addWidget(self.back_btn)
        header_layout.addWidget(self.playlist_name)
        header_layout.addStretch()
        header_layout.addLayout(volume_layout)
        
        self.songs_list = QListWidget()
        self.songs_list.itemDoubleClicked.connect(self.play_song)  
        
        self.player_controls = PlayerControls()
        
        self.add_song_btn = QPushButton("+ Add Song")
        self.add_song_btn.setFixedHeight(50)
        self.add_song_btn.clicked.connect(self.add_song)  
        
        layout.addLayout(status_layout)
        layout.addLayout(header_layout)
        layout.addWidget(self.songs_list)
        layout.addWidget(self.player_controls)
        layout.addWidget(self.add_song_btn)
        
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        self.player_controls.play_btn.clicked.connect(self.toggle_playback)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        
        self.player_controls.next_btn.clicked.connect(self.play_next_track)
        self.player_controls.prev_btn.clicked.connect(self.play_prev_track)
        self.player_controls.duplicate_btn.clicked.connect(self.duplicate_current_track)
        self.player_controls.delete_btn.clicked.connect(self.delete_current_track)
        self.player_controls.rename_btn.clicked.connect(self.rename_current_track)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        
        self.player_controls.rename_btn.clicked.connect(self.rename_current_track)
        
    def update_position(self, position):
        self.player_controls.current_time.setText(self.format_time(position))
        self.player_controls.progress_bar.setValue(position)
        
    def update_duration(self, duration):
        self.player_controls.total_time.setText(self.format_time(duration))
        self.player_controls.progress_bar.setRange(0, duration)
        
    def format_time(self, ms):
        s = round(ms / 1000)
        return f"{s//60}:{s%60:02d}"
        
    def update_songs_list(self):
        self.songs_list.clear()
        if self.current_playlist_path and os.path.exists(self.current_playlist_path):
            for i, file in enumerate(sorted(Path(self.current_playlist_path).glob("*.*")), 1):
                item = QListWidgetItem(f"{i}. {file.name}")
                self.songs_list.addItem(item)
    
    def seek_position(self, position):
        self.player.setPosition(position)
    
    def toggle_mute(self):
        if self.audio_output.isMuted():
            self.audio_output.setMuted(False)
            self.volume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        else:
            self.audio_output.setMuted(True)
            self.volume_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted))
    
    def duplicate_current_track(self):
        current_item = self.songs_list.currentItem()
        if current_item:
            original_name = current_item.text().split('. ', 1)[1]
            base_name, ext = os.path.splitext(original_name)
            
            counter = 1
            while True:
                new_name = f"{base_name} ({counter}){ext}"
                if not os.path.exists(os.path.join(self.current_playlist_path, new_name)):
                    break
                counter += 1
            
            src_path = os.path.join(self.current_playlist_path, original_name)
            dst_path = os.path.join(self.current_playlist_path, new_name)
            import shutil
            shutil.copy2(src_path, dst_path)
            
            new_index = self.songs_list.count() + 1
            new_item = QListWidgetItem(f"{new_index}. {new_name}")
            self.songs_list.addItem(new_item)
            self.save_playlist()

    def delete_current_track(self):
        current_item = self.songs_list.currentItem()
        if current_item:
            filename = current_item.text().split('. ', 1)[1]
            file_path = os.path.join(self.current_playlist_path, filename)
            
            try:
                os.remove(file_path)
                self.songs_list.takeItem(self.songs_list.row(current_item))
                self.save_playlist()
                self.update_songs_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not delete file: {str(e)}")

    def play_song(self, item):
        try:
            self.current_track_index = self.songs_list.row(item)
            file_path = f"{self.current_playlist_path}/{item.text().split('. ', 1)[1]}"
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            self.player_controls.play_btn.setText("PAUSE")
            self.now_playing.setText(f"Now Playing - {item.text().split('. ', 1)[1]}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot play file: {str(e)}")
    
    def rename_current_track(self):
        current_item = self.songs_list.currentItem()
        if current_item:
            old_name = current_item.text().split('. ', 1)[1]
            old_path = os.path.join(self.current_playlist_path, old_name)
            
            new_name, ok = QInputDialog.getText(self, "Rename Track", "Enter new name:", text=old_name)
            if ok and new_name:
                try:
                    _, ext = os.path.splitext(old_name)
                    if not new_name.endswith(ext):
                        new_name += ext
                        
                    new_path = os.path.join(self.current_playlist_path, new_name)
                    os.rename(old_path, new_path)
                    
                    index = self.songs_list.row(current_item) + 1
                    current_item.setText(f"{index}. {new_name}")
                    
                    if self.current_track_index == self.songs_list.row(current_item):
                        self.now_playing.setText(f"Now Playing - {new_name}")
                        
                    self.save_playlist()
                    
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Could not rename file: {str(e)}")

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next_track()
    
    def play_next_track(self):
        if self.current_track_index < self.songs_list.count() - 1:
            next_item = self.songs_list.item(self.current_track_index + 1)
            if next_item:
                self.songs_list.setCurrentItem(next_item)
                self.play_song(next_item)
        elif self.songs_list.count() > 0:
            first_item = self.songs_list.item(0)
            self.songs_list.setCurrentItem(first_item)
            self.play_song(first_item)

    def play_prev_track(self):
        if self.current_track_index > 0:
            prev_item = self.songs_list.item(self.current_track_index - 1)
            if prev_item:
                self.songs_list.setCurrentItem(prev_item)
                self.play_song(prev_item)
        elif self.songs_list.count() > 0:
            last_item = self.songs_list.item(self.songs_list.count() - 1)
            self.songs_list.setCurrentItem(last_item)
            self.play_song(last_item)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.player_controls.play_btn.setText("PLAY")
        else:
            current_item = self.songs_list.currentItem()
            if current_item:
                if not self.player.source().isValid():
                    self.play_song(current_item)
                else:
                    self.player.play()
                    self.player_controls.play_btn.setText("PAUSE")
            elif self.songs_list.count() > 0:
                self.songs_list.setCurrentRow(0)
                self.play_song(self.songs_list.item(0))

    def add_song(self):
        if not self.current_playlist:
            QMessageBox.warning(self, "Warning", "Please select a playlist first")
            return
        dialog = AddSongDialog(self, self.current_playlist)
        if dialog.exec_():
            self.update_songs_list()  
            
    def save_playlist(self):
        if self.current_playlist:
            playlist_data = {
                'name': self.playlist_name.text(),
                'songs': [item.text() for item in self.songs_list.findItems("", Qt.MatchContains)]
            }
            with open(f"{PLAYLISTS_PATH}/{self.current_playlist}.json", 'w') as f:
                json.dump(playlist_data, f)

    def load_playlist(self, name):
        self.current_playlist = name
        self.current_playlist_path = f"{PLAYLISTS_PATH}/{name}/songs"
        os.makedirs(self.current_playlist_path, exist_ok=True)
        self.playlist_name.setText(name)
        self.update_songs_list()

class MusicPlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CloudPlayer")
        self.setMinimumSize(1000, 700)
        
        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        QFontDatabase.addApplicationFont("./fonts/Montserrat-Regular.ttf")
        QFontDatabase.addApplicationFont("./fonts//Montserrat-Bold.ttf")
        
        self.setup_ui()
        self.load_playlists()

    def setup_ui(self):
        self.stack = QStackedWidget()
        self.main_view = QWidget()
        self.playlist_view = PlaylistView()
        
        main_layout = QVBoxLayout(self.main_view)
        
        playlists_label = QLabel("Your Playlists")
        playlists_label.setStyleSheet("font-size: 24px; font-weight: bold; margin: 20px 0;")
        
        self.playlist_list = QListWidget()
        self.playlist_list.setStyleSheet("""
            QListWidget::item {
                height: 60px;
                font-size: 18px;
            }
        """)
        self.playlist_list.itemDoubleClicked.connect(self.open_playlist)
        
        playlist_controls = QHBoxLayout()
        add_playlist_btn = QPushButton("+ New Playlist")
        remove_playlist_btn = QPushButton("- Remove Playlist")
        
        for btn in [add_playlist_btn, remove_playlist_btn]:
            btn.setFixedHeight(40)
            playlist_controls.addWidget(btn)
        
        add_playlist_btn.clicked.connect(self.create_playlist)
        remove_playlist_btn.clicked.connect(self.remove_playlist)
        
        main_layout.addWidget(playlists_label)
        main_layout.addWidget(self.playlist_list)
        main_layout.addLayout(playlist_controls)
        
        self.stack.addWidget(self.main_view)
        self.stack.addWidget(self.playlist_view)
        self.setCentralWidget(self.stack)
        
        self.playlist_view.back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {THEME_COLOR};
                color: #ffffff;
                font-family: 'Montserrat';
            }}
            QPushButton {{
                background-color: {BUTTON_COLOR};
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
                color: white;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_HOVER};
            }}
            QLabel {{
                font-size: 16px;
            }}
            QListWidget {{
                background-color: {LIST_BG};
                border-radius: 10px;
                padding: 10px;
            }}
            QListWidget::item {{
                background-color: {ITEM_BG};
                border-radius: 5px;
                margin-bottom: 5px;
                padding: 10px;
                font-size: 14px;
            }}
            QListWidget::item:hover {{
                background-color: {BUTTON_HOVER};
            }}
            QListWidget::item:selected {{
                background-color: {ACCENT_COLOR};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {BUTTON_COLOR};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT_COLOR};
                border-radius: 7px;
                width: 14px;
                margin: -5px 0;
            }}
        """)
    
    def open_playlist(self, item):
        self.playlist_view.playlist_name.setText(item.text())
        self.stack.setCurrentIndex(1)
        self.playlist_view.load_playlist(item.text())  
    
    def create_playlist(self):
        name, ok = QInputDialog.getText(self, "New Playlist", "Enter playlist name:")
        if ok and name:
            self.playlist_list.addItem(name)
            
            playlist_path = f"{PLAYLISTS_PATH}/{name}"
            os.makedirs(f"{playlist_path}/songs", exist_ok=True)
            
            playlist_data = {
                'name': name,
                'songs': []
            }
            with open(f"{PLAYLISTS_PATH}/{name}.json", 'w') as f:
                json.dump(playlist_data, f)
    
    def load_playlists(self):
        if os.path.exists(PLAYLISTS_PATH):
            for file in os.listdir(PLAYLISTS_PATH):
                if file.endswith('.json'):
                    self.playlist_list.addItem(file[:-5])  
    
    def remove_playlist(self):
        current_item = self.playlist_list.currentItem()
        if current_item:
            name = current_item.text()
            try:
                import shutil
                shutil.rmtree(f"{PLAYLISTS_PATH}/{name}")
            except:
                pass
            self.playlist_list.takeItem(self.playlist_list.row(current_item))

if __name__ == "__main__":
    try:
        for path in [ROOT_PATH, DOWNLOADS_PATH, PLAYLISTS_PATH]:
            os.makedirs(path, exist_ok=True)
        
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        player = MusicPlayer()
        player.show()
        sys.exit(app.exec())
    except Exception as e:
        print(f"Error: {str(e)}")
        input("Press Enter to exit...")
