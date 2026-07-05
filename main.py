import sys
import json
import shutil

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QStackedWidget, QInputDialog, QMessageBox,
    QGraphicsOpacityEffect
)
from PySide6.QtGui import QIcon, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QObject, Signal, QPropertyAnimation, QEasingCurve

from config import (
    SCRIPT_DIR, DOCS_PATH, DOWNLOADS_PATH, PLAYLISTS_PATH,
    BG_COLOR, PANEL_BG, BUTTON_BG, BUTTON_HOVER, BUTTON_BORDER,
    ACCENT_COLOR, TEXT_COLOR, TEXT_MUTED
)
from threads import RecommendationFetcher, BackgroundDownloader
from player_widgets import PlaylistView
import discord_rpc


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

        self._view_anim = None
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

        self.playlist_view.back_btn.clicked.connect(lambda: self._animated_switch(0))

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

        rec_header = QLabel("Recommendations for You")
        rec_header.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffffff;")
        rec_outer.addWidget(rec_header)

        self.rec_rows = []
        for i in range(RecommendationFetcher.TARGET_COUNT):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            lbl = QLabel("Finding tracks...")
            lbl.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {TEXT_MUTED};")

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
            QPushButton:pressed {{ 
                background-color: {ACCENT_COLOR}; 
                border: 1px solid #444444;
            }}
            QPushButton:focus {{
                outline: none;
                border: 1px solid {ACCENT_COLOR};
            }}
            QLineEdit {{
                background-color: {PANEL_BG};
                border: 1px solid {BUTTON_BORDER};
                border-radius: 4px;
                padding: 10px;
                color: {TEXT_COLOR};
            }}
            QLineEdit:focus {{
                border: 1px solid {ACCENT_COLOR};
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

    def _animated_switch(self, idx):
        if self.stack.currentIndex() == idx:
            return
        self.stack.setCurrentIndex(idx)
        target = self.stack.widget(idx)
        eff = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(eff)
        eff.setOpacity(0.0)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._on_view_anim_finished)
        self._view_anim = anim
        anim.start()

    def _on_view_anim_finished(self):
        for i in range(self.stack.count()):
            self.stack.widget(i).setGraphicsEffect(None)
        if self._view_anim is not None:
            self._view_anim.deleteLater()
            self._view_anim = None

    def display_recommendation(self, recommendations):
        """Receives a list [(artist, title), ...] and fills the UI rows."""
        self.rec_data = recommendations or []

        if not self.rec_data:
            for lbl, btn, _ in self.rec_rows:
                lbl.setText("Add a track to a playlist to get a recommendation.")
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
        """Restarts the recommendation thread to re-read the artists
        from the user's playlists (relevant after adding tracks).
        """
        if self.rec_thread.isRunning():
            return
        self.rec_thread.start()

    def add_recommendation_to_playlist(self, row_index: int):
        """Adds the recommendation at the given row index to a playlist."""
        if not self.rec_data or row_index >= len(self.rec_data):
            return
        artist, title = self.rec_data[row_index]

        playlists = [self.playlist_list.item(i).text() for i in range(self.playlist_list.count())]
        if not playlists:
            QMessageBox.warning(self, "Oops", "Please create at least one playlist first.")
            return

        target, ok = QInputDialog.getItem(self, "Add Recommendation", "Select a playlist:", playlists, 0, False)
        if not (ok and target):
            return

        dest_dir = PLAYLISTS_PATH / target / "songs"
        dest_dir.mkdir(parents=True, exist_ok=True)

        query = f"{artist} {title}"
        lbl, btn, _ = self.rec_rows[row_index]
        old_text = lbl.text()
        lbl.setText(f"⏳ Adding: {old_text} ...")
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
            QMessageBox.information(self, "Done", f"Track successfully pulled from SoundCloud into '{playlist_name}'!")
            if self.stack.currentIndex() == 1 and self.playlist_view.current_playlist == playlist_name:
                self.playlist_view.update_songs_list()
            self.refresh_recommendation()
        else:
            lbl, btn, _ = self.rec_rows[row_index]
            lbl.setText(old_text)
            lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: #ffffff;")
            btn.setEnabled(True)
            QMessageBox.critical(self, "Download Error", f"Failed to download:\n{msg}")

    def open_playlist(self, item):
        name = item.text()
        self.playlist_view.load_playlist(name)
        self._animated_switch(1)

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