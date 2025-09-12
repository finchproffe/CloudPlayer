from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from pathlib import Path
import sys
import os
import json
import yt_dlp

ROOT_PATH = "C:/PlayerRelease"
DOWNLOADS_PATH = f"{ROOT_PATH}/downloads"
PLAYLISTS_PATH = f"{ROOT_PATH}/playlists"

DOCS_PATH = str(Path.home() / "Documents" / "CloudPlayer")
DOWNLOADS_PATH = str(Path(DOCS_PATH) / "downloads")

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
        
        self.prev_btn = QPushButton("PREV")
        self.play_btn = QPushButton("PLAY")
        self.next_btn = QPushButton("NEXT")
        
        for btn in [self.prev_btn, self.play_btn, self.next_btn]:
            btn.setFixedSize(80, 40)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1db954;
                    border: none;
                    border-radius: 20px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #1ed760;
                }
            """)
            
        self.duplicate_btn = QPushButton("DUPLICATE")
        self.duplicate_btn.setFixedSize(100, 40)
        self.duplicate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1db954, stop:1 #147d37);
                border: none;
                border-radius: 20px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1ed760, stop:1 #1db954);
            }
        """)
        
        controls_layout.addStretch()
        controls_layout.addWidget(self.prev_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.next_btn)
        controls_layout.addWidget(self.duplicate_btn)
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
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        
        url_btn = QPushButton("Add from URL (Spotify/SoundCloud)")
        file_btn = QPushButton("Add from File")
        
        for btn in [url_btn, file_btn]:
            btn.setFixedHeight(50)
            layout.addWidget(btn)
            
        url_btn.clicked.connect(self.add_from_url)
        file_btn.clicked.connect(self.add_from_file)
        
    def add_from_url(self):
        url, ok = QInputDialog.getText(self, "Add from URL", "Enter Spotify/SoundCloud URL:")
        if ok and url:
            try:
                os.makedirs(self.playlist_path, exist_ok=True)
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': f'{self.playlist_path}/%(title)s.%(ext)s',
                }
                
                progress = QProgressDialog("Downloading...", None, 0, 0, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.show()
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                    self.accept()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to download: {str(e)}")
                finally:
                    progress.close()
                    
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Download error: {str(e)}")

    def add_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio File", "", "Audio Files (*.mp3 *.wav *.ogg *.m4a)"
        )
        if file_path:
            os.makedirs(self.playlist_path, exist_ok=True)
            filename = Path(file_path).name
            new_path = f"{self.playlist_path}/{filename}"
            import shutil
            shutil.copy2(file_path, new_path)
            self.accept()

class PlaylistView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist = None
        self.current_playlist_path = None
        self.songs = []
        self.current_track_index = -1
        self.setup_ui()
        self.update_songs_list()  # Load songs immediately after UI setup
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        
        header_layout = QHBoxLayout()
        self.back_btn = QPushButton("← Back")
        self.back_btn.setFixedSize(100, 40)
        self.playlist_name = QLabel("Playlist Name")
        
        volume_layout = QHBoxLayout()
        volume_icon = QPushButton()
        volume_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        volume_icon.setFixedSize(24, 24)
        volume_icon.setFlat(True)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        
        volume_layout.addWidget(volume_icon)
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
        
        # Connect next/prev buttons
        self.player_controls.next_btn.clicked.connect(self.play_next_track)
        self.player_controls.prev_btn.clicked.connect(self.play_prev_track)
        
        self.player_controls.duplicate_btn.clicked.connect(self.duplicate_current_track)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        
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
    
    def duplicate_current_track(self):
        current_item = self.songs_list.currentItem()
        if current_item:
            filename = current_item.text().split('. ', 1)[1]
            new_index = self.songs_list.count() + 1
            new_item = QListWidgetItem(f"{new_index}. {filename}")
            self.songs_list.addItem(new_item)
            self.save_playlist()

    def play_song(self, item):
        try:
            self.current_track_index = self.songs_list.row(item)
            file_path = f"{self.current_playlist_path}/{item.text().split('. ', 1)[1]}"
            self.player.setSource(QUrl.fromLocalFile(file_path))
            self.player.play()
            self.player_controls.play_btn.setText("PAUSE")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot play file: {str(e)}")
    
    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next_track()
    
    def play_next_track(self):
        if self.current_track_index < self.songs_list.count() - 1:
            next_item = self.songs_list.item(self.current_track_index + 1)
            if next_item:
                self.songs_list.setCurrentItem(next_item)
                self.play_song(next_item)
        elif self.songs_list.count() > 0:  # Loop to beginning
            first_item = self.songs_list.item(0)
            self.songs_list.setCurrentItem(first_item)
            self.play_song(first_item)

    def play_prev_track(self):
        if self.current_track_index > 0:
            prev_item = self.songs_list.item(self.current_track_index - 1)
            if prev_item:
                self.songs_list.setCurrentItem(prev_item)
                self.play_song(prev_item)
        elif self.songs_list.count() > 0:  # Loop to end
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
        
        QFontDatabase.addApplicationFont("./Montserrat-Regular.ttf")
        QFontDatabase.addApplicationFont("./Montserrat-Bold.ttf")
        
        self.setup_ui()
        self.load_playlists()  # Load playlists at startup

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
        
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #121212;
                color: #ffffff;
                font-family: 'Montserrat';
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1db954, stop:1 #147d37);
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
                color: white;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1ed760, stop:1 #1db954);
            }
            QLabel {
                font-size: 16px;
            }
            QListWidget {
                background-color: #282828;
                border-radius: 10px;
                padding: 10px;
            }
            QListWidget::item {
                background-color: #383838;
                border-radius: 5px;
                margin-bottom: 5px;
                padding: 10px;
                font-size: 14px;
            }
            QListWidget::item:hover {
                background-color: #404040;
            }
            QListWidget::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1db954, stop:1 #147d37);
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #282828;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #1db954;
                border-radius: 7px;
                width: 14px;
                margin: -5px 0;
            }
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

    def toggle_playback(self):
        if self.playlist_view.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.playlist_view.player.pause()
            self.play_btn.setText("▶")
        else:
            self.playlist_view.player.play()
            self.play_btn.setText("⏸")
            
    def change_volume(self, value):
        self.playlist_view.audio_output.setVolume(value / 100.0)

if __name__ == "__main__":
    for path in [ROOT_PATH, DOWNLOADS_PATH, PLAYLISTS_PATH]:
        os.makedirs(path, exist_ok=True)
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion') 
    player = MusicPlayer()
    player.show()
    sys.exit(app.exec())
