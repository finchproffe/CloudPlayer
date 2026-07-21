import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
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
    read_ui_settings,
    save_search_sources,
)
from dropdown_ui import QDialog, QFileDialog, QInputDialog, QMessageBox
from hotkeys import handle_list_multi_selection, matches_widget_binding
from room_tcp_patch import install as install_room_tcp_patch
from threads import BackgroundDownloader, DemoStreamResolver, SearchWorker
from utils import colored_icon

install_room_tcp_patch()

USDT_BEP20_ADDRESS = "0x77F023d48271e6a7545265e91b8ac9862b6cD61E"


class KeyboardMultiSelectListWidget(QListWidget):
    """List widget with configurable keyboard multi-selection."""

    demo_requested = Signal()

    def keyPressEvent(self, event):
        if handle_list_multi_selection(self, event):
            return
        if matches_widget_binding(self, event, "playlist", "demo_selected"):
            self.demo_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SearchResultRow(QWidget):
    """Compact result row with an independent streaming preview button."""

    selected = Signal()
    activated = Signal()
    demo_requested = Signal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName("searchResultRow")
        self.setFocusPolicy(Qt.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 9, 0)
        row.setSpacing(8)
        row.setAlignment(Qt.AlignVCenter)

        self.setFixedHeight(46)

        self.details = QLabel(text)
        self.details.setMinimumWidth(0)
        self.details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(False)
        self.details.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.details.setStyleSheet(
            f"QLabel {{ color: {TEXT_COLOR}; background: transparent; "
            "border: none; margin: 0px; padding: 0px; }"
        )
        self.details.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.demo_label = QLabel("Demo")
        self.demo_label.setAlignment(Qt.AlignCenter)
        self.demo_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:12px;background:transparent;"
            "margin:0px;padding:0px;"
        )
        self.demo_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.demo_button = QToolButton()
        self.demo_button.setFixedSize(32, 32)
        self.demo_button.setIconSize(QSize(16, 16))
        self.demo_button.setAutoRaise(False)
        self.demo_button.setIcon(colored_icon("play.svg", TEXT_COLOR, 16))
        self.demo_button.setToolTip("Play demo (Space)")
        self.demo_button.setAccessibleName("Play demo")
        self.demo_button.setFocusPolicy(Qt.NoFocus)
        self.demo_button.setStyleSheet(
            f"QToolButton {{ "
            f"background-color: {BUTTON_BG}; "
            f"border: 1px solid {BUTTON_BORDER}; "
            "border-radius: 5px; "
            "margin: 0px; "
            "padding: 0px; "
            "} "
            f"QToolButton:hover {{ background-color: {BUTTON_HOVER}; }}"
        )
        self.demo_button.clicked.connect(self.demo_requested.emit)

        row.addWidget(self.details, 1, Qt.AlignVCenter)
        row.addWidget(self.demo_label, 0, Qt.AlignVCenter)
        row.addWidget(self.demo_button, 0, Qt.AlignVCenter)

    def set_loading(self, loading):
        self.demo_button.setEnabled(not loading)
        self.demo_button.setToolTip(
            "Preparing demo stream..." if loading else "Play demo (Space)"
        )

    def set_playing(self, playing):
        self.demo_button.setEnabled(True)
        icon_name = "pause.svg" if playing else "play.svg"
        tooltip = "Pause demo (Space)" if playing else "Play demo (Space)"
        self.demo_button.setIcon(colored_icon(icon_name, TEXT_COLOR, 16))
        self.demo_button.setToolTip(tooltip)
        self.demo_button.setAccessibleName(tooltip)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.activated.emit()
        super().mouseDoubleClickEvent(event)


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
        self.downloaded_paths = []
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_player.playbackStateChanged.connect(
            self._preview_state_changed
        )
        self.preview_player.mediaStatusChanged.connect(
            self._preview_media_status_changed
        )
        self.preview_player.errorOccurred.connect(self._preview_error)
        self.preview_request_id = 0
        self.preview_page_url = ""
        self.preview_stream_url = ""
        self.preview_row = None
        self.preview_worker = None
        self.preview_buffer = None
        self.host_player = None
        self.host_audio_output = None
        self.host_player_was_playing = False
        self.search_sources = list(
            read_ui_settings().get("search_sources") or ["soundcloud"]
        )
        self.source_actions = {}
        self.setup_ui()
        self._connect_preview_volume()

    def setup_ui(self):
        self.setWindowTitle("Add Song")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        self.selection_panel = QWidget()
        selection_layout = QVBoxLayout(self.selection_panel)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(38)
        # The application-wide QLineEdit style uses 12 px padding on every
        # side. Inside a 38 px search field that leaves too little vertical
        # space and clips the text descenders. Keep the same outer size, but
        # use horizontal-only padding so text and placeholder are centred.
        self.search_input.setStyleSheet(
            f"QLineEdit {{ "
            f"background-color: {PANEL_BG}; "
            f"color: {TEXT_COLOR}; "
            f"border: 1px solid {BUTTON_BORDER}; "
            "border-radius: 4px; "
            "padding-left: 12px; "
            "padding-right: 12px; "
            "padding-top: 0px; "
            "padding-bottom: 0px; "
            "font-size: 14px; "
            "}"
        )
        self.search_input.setPlaceholderText("Search SoundCloud...")
        self.search_input.returnPressed.connect(self.search_songs)
        self.filter_btn = QToolButton()
        self.filter_btn.setIcon(colored_icon("filter.svg", TEXT_COLOR, 18))
        self.filter_btn.setIconSize(QSize(18, 18))
        self.filter_btn.setFixedSize(38, 38)
        self.filter_btn.setToolTip("Search sources")
        self.filter_btn.setAccessibleName("Search sources")
        self.filter_btn.setStyleSheet(
            f"QToolButton {{ "
            f"background-color: {BUTTON_BG}; "
            f"border: 1px solid {BUTTON_BORDER}; "
            "border-radius: 4px; "
            "padding: 0px; "
            "} "
            f"QToolButton:hover {{ background-color: {BUTTON_HOVER}; }} "
            "QToolButton::menu-indicator { image: none; width: 0px; height: 0px; }"
        )
        self.filter_btn.setPopupMode(QToolButton.InstantPopup)
        self.filter_menu = QMenu(self.filter_btn)
        self.filter_menu.setStyleSheet(
            f"QMenu{{background:{PANEL_BG};color:{TEXT_COLOR};"
            f"border:1px solid {BUTTON_BORDER};padding:6px}}"
            f"QMenu::item{{padding:8px 30px 8px 12px;border-radius:5px}}"
            f"QMenu::item:selected{{background:{BUTTON_HOVER}}}"
        )
        self.filter_btn.setMenu(self.filter_menu)
        self._build_source_menu()

        search_btn = QPushButton("Search")
        search_btn.setFixedSize(100, 38)
        search_btn.clicked.connect(self.search_songs)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.filter_btn)
        search_layout.addWidget(search_btn)

        self.results_list = KeyboardMultiSelectListWidget()
        self.results_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.results_list.setMinimumHeight(300)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_list.setUniformItemSizes(True)
        self.results_list.setSpacing(1)
        # The application-wide QListWidget item padding is useful for plain
        # text lists, but this list embeds a complete row widget. Applying that
        # padding a second time shrinks the row and pushes its controls toward
        # the bottom. The embedded row owns its spacing, so keep item padding at
        # zero here and centre everything inside the full item rectangle.
        self.results_list.setStyleSheet(
            f"QListWidget::item {{ padding:0px; margin:0px; "
            "border-radius:4px; }} "
            f"QListWidget::item:hover {{ background:{BUTTON_HOVER}; }} "
            f"QListWidget::item:selected {{ background:{ACCENT_COLOR}; "
            "color:#ffffff; }}"
        )
        self.results_list.demo_requested.connect(self._preview_current_item)
        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        self.source_status.setStyleSheet(f"color:{TEXT_MUTED};font-size:11px")
        self.source_status.hide()

        url_btn = QPushButton("Add From URL")
        file_btn = QPushButton("Add Local File")
        for button in (url_btn, file_btn):
            button.setFixedHeight(40)

        selection_layout.addLayout(search_layout)
        selection_layout.addWidget(self.results_list)
        selection_layout.addWidget(self.source_status)
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
            f"QProgressBar {{"
            f"background-color: {PANEL_BG};"
            f"color: {TEXT_COLOR};"
            f"border: 1px solid {BUTTON_BORDER};"
            f"border-radius: 6px;"
            f"min-height: 18px;"
            f"text-align: center;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"background-color: {ACCENT_COLOR};"
            f"border-radius: 5px;"
            f"}}"
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
        # QListWidget emits itemActivated for Enter/Return as well as the
        # platform activation gesture. This makes keyboard confirmation use
        # exactly the same download path as a mouse double-click.
        self.results_list.itemActivated.connect(self._download_item)

    def _build_source_menu(self):
        self.filter_menu.clear()
        self.source_actions = {}
        title = QAction("Search sources", self.filter_menu)
        title.setEnabled(False)
        self.filter_menu.addAction(title)
        self.filter_menu.addSeparator()

        blank = QPixmap(14, 14)
        blank.fill(Qt.transparent)
        self.blank_check_icon = QIcon(blank)

        for key, label in (
            ("soundcloud", "SoundCloud"),
            ("youtube_music", "YouTube Music"),
        ):
            action = QAction(label, self.filter_menu)
            action.setIcon(
                colored_icon("check.svg", ACCENT_COLOR, 14)
                if key in self.search_sources
                else self.blank_check_icon
            )
            action.triggered.connect(
                lambda _checked=False, current=key: self._source_toggled(current)
            )
            self.filter_menu.addAction(action)
            self.source_actions[key] = action
        self._update_search_placeholder()

    def _source_toggled(self, source):
        selected = list(self.search_sources)
        if source in selected:
            if len(selected) == 1:
                return
            selected.remove(source)
        else:
            selected.append(source)
        self.search_sources = selected
        save_search_sources(selected)
        for key, action in self.source_actions.items():
            action.setIcon(
                colored_icon("check.svg", ACCENT_COLOR, 14)
                if key in selected
                else self.blank_check_icon
            )
        self._update_search_placeholder()

    def _update_search_placeholder(self):
        selected = set(self.search_sources)
        if selected == {"youtube_music"}:
            text = "Search YouTube Music..."
        elif selected == {"soundcloud", "youtube_music"}:
            text = "Search SoundCloud and YouTube Music..."
        else:
            text = "Search SoundCloud..."
        self.search_input.setPlaceholderText(text)
        labels = []
        if "soundcloud" in selected:
            labels.append("SoundCloud")
        if "youtube_music" in selected:
            labels.append("YouTube Music")
        self.filter_btn.setToolTip("Search sources: " + ", ".join(labels))

    def _show_source_errors(self, errors):
        labels = []
        if "soundcloud" in errors:
            labels.append("SoundCloud")
        if "youtube_music" in errors:
            message = errors.get("youtube_music", "")
            if "ytmusicapi" in message.casefold():
                labels.append("YouTube Music (install ytmusicapi)")
            else:
                labels.append("YouTube Music")
        if labels:
            self.source_status.setText(
                "Unavailable source: " + ", ".join(labels)
                + ". Other selected sources still work."
            )
            self.source_status.show()

    def _focus_search_input(self):
        if not self.selection_panel.isVisible():
            return
        self.search_input.setFocus(Qt.ShortcutFocusReason)
        self.search_input.setCursorPosition(len(self.search_input.text()))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._focus_search_input)

    def search_songs(self):
        query = self.search_input.text().strip()
        if not query:
            return
        self._stop_preview()
        self.results_list.clear()
        self.source_status.hide()
        selected_names = []
        if "soundcloud" in self.search_sources:
            selected_names.append("SoundCloud")
        if "youtube_music" in self.search_sources:
            selected_names.append("YouTube Music")
        self.results_list.addItem(
            "Searching " + " and ".join(selected_names) + "..."
        )
        self.worker = SearchWorker(
            query, self, sources=self.search_sources
        )
        self.worker.results_ready.connect(self.show_results)
        self.worker.source_errors.connect(self._show_source_errors)
        self.worker.start()

    def show_results(self, results):
        self._stop_preview()
        self.results_list.clear()
        for result in results:
            source = result.get("source") or "Music"
            source_tag = "YTM" if source == "YouTube Music" else "SC"
            details = [
                str(result.get("title") or "Unknown Title"),
                str(result.get("artist") or "Unknown Artist"),
            ]
            if result.get("album"):
                details.append(str(result["album"]))
            if result.get("duration"):
                details.append(str(result["duration"]))
            display_text = f"[{source_tag}] " + " • ".join(details)
            # The visible text is painted by SearchResultRow. Keeping the same
            # text in QListWidgetItem makes Qt paint it a second time underneath
            # the custom widget, which creates the stacked/garbled result shown
            # on Windows. Store it only as accessibility/tooltip data.
            item = QListWidgetItem()
            item.setToolTip(f"{display_text}\nSource: {source}")
            item.setData(Qt.AccessibleTextRole, display_text)
            item.setData(Qt.UserRole, result)
            item.setSizeHint(QSize(0, 46))
            self.results_list.addItem(item)
            row = SearchResultRow(display_text, self.results_list)
            row.setToolTip(display_text)
            row.selected.connect(
                lambda current=item: self._select_result_item(current)
            )
            row.activated.connect(
                lambda current=item: self._download_item(current)
            )
            row.demo_requested.connect(
                lambda current=item, current_row=row: self._toggle_preview(
                    current, current_row
                )
            )
            self.results_list.setItemWidget(item, row)
        if not results:
            self.results_list.addItem("No results found in the selected sources.")
            return

        # Keyboard-first flow: once search results arrive, select and focus
        # the first real track. The user can immediately press Enter to start
        # the download, or move with the arrow keys first.
        self.results_list.setCurrentRow(0)
        first_item = self.results_list.item(0)
        if first_item is not None:
            first_item.setSelected(True)
        self.results_list.setFocus(Qt.ShortcutFocusReason)

    def _select_result_item(self, item):
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.ControlModifier:
            item.setSelected(not item.isSelected())
        else:
            self.results_list.clearSelection()
            item.setSelected(True)
        self.results_list.setCurrentItem(item)

    def _toggle_preview(self, item, row):
        result = item.data(Qt.UserRole)
        if not isinstance(result, dict):
            return
        if self.source_status.text().startswith("Demo "):
            self.source_status.hide()
        page_url = str(result.get("url") or "").strip()
        if not page_url:
            return

        if (
            self.preview_page_url == page_url
            and not self.preview_stream_url
            and self.preview_row is row
        ):
            return

        if self.preview_page_url == page_url and self.preview_stream_url:
            if self.preview_player.playbackState() == QMediaPlayer.PlayingState:
                self.preview_player.pause()
            else:
                self._sync_preview_volume()
                self._pause_host_player()
                self.preview_player.play()
            return

        self._stop_preview(resume_host=False)
        self.preview_request_id += 1
        request_id = self.preview_request_id
        self.preview_page_url = page_url
        self.preview_row = row
        row.set_loading(True)

        worker = DemoStreamResolver(page_url, QApplication.instance())
        self.preview_worker = worker
        worker.resolved.connect(
            lambda original, stream, buffer, current=request_id: self._preview_resolved(
                current, original, stream, buffer
            )
        )
        worker.failed.connect(
            lambda original, message, current=request_id: self._preview_failed(
                current, original, message
            )
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _preview_current_item(self):
        item = self.results_list.currentItem()
        if item is None:
            return
        row = self.results_list.itemWidget(item)
        if isinstance(row, SearchResultRow):
            self._toggle_preview(item, row)

    def _preview_resolved(self, request_id, page_url, stream_url, buffer):
        if (
            request_id != self.preview_request_id
            or page_url != self.preview_page_url
            or self.preview_row is None
        ):
            if buffer is not None:
                buffer.close()
            return
        self.preview_buffer = buffer
        self.preview_stream_url = stream_url
        self.preview_row.set_loading(False)
        self._sync_preview_volume()
        self._pause_host_player()
        self.preview_player.setSource(QUrl(stream_url))
        self.preview_player.play()

    def _preview_failed(self, request_id, page_url, message):
        if request_id != self.preview_request_id or page_url != self.preview_page_url:
            return
        row = self.preview_row
        self.preview_page_url = ""
        self.preview_stream_url = ""
        self.preview_row = None
        if row is not None:
            row.set_loading(False)
            row.set_playing(False)
            row.demo_button.setToolTip("Demo unavailable: " + message)
        self.source_status.setText("Demo unavailable for this track.")
        self.source_status.show()

    def _preview_state_changed(self, state):
        if self.preview_row is not None:
            self.preview_row.set_playing(state == QMediaPlayer.PlayingState)

    def _preview_media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self._stop_preview()

    def _preview_error(self, _error=None, message=""):
        if not self.preview_page_url:
            return
        row = self.preview_row
        self._stop_preview()
        if row is not None:
            row.demo_button.setToolTip(
                "Demo playback failed" + (f": {message}" if message else "")
            )
        self.source_status.setText("Demo playback failed for this track.")
        self.source_status.show()

    def _find_host_player(self):
        widget = self.parentWidget()
        while widget is not None:
            player = getattr(widget, "player", None)
            if isinstance(player, QMediaPlayer):
                return player
            widget = widget.parentWidget()
        return None

    def _find_host_audio_output(self):
        widget = self.parentWidget()
        while widget is not None:
            audio_output = getattr(widget, "audio_output", None)
            if isinstance(audio_output, QAudioOutput):
                return audio_output
            player = getattr(widget, "player", None)
            if isinstance(player, QMediaPlayer):
                try:
                    audio_output = player.audioOutput()
                except (AttributeError, RuntimeError):
                    audio_output = None
                if isinstance(audio_output, QAudioOutput):
                    return audio_output
            widget = widget.parentWidget()
        return None

    def _connect_preview_volume(self):
        self.host_audio_output = self._find_host_audio_output()
        if self.host_audio_output is not None:
            self.host_audio_output.volumeChanged.connect(
                self._sync_preview_volume
            )
            self.host_audio_output.mutedChanged.connect(
                self._sync_preview_volume
            )
        self._sync_preview_volume()

    def _sync_preview_volume(self, *_args):
        audio_output = self.host_audio_output
        if audio_output is None:
            audio_output = self._find_host_audio_output()
            self.host_audio_output = audio_output
        if audio_output is not None:
            self.preview_audio.setVolume(
                max(0.0, min(1.0, float(audio_output.volume())))
            )
            self.preview_audio.setMuted(bool(audio_output.isMuted()))
            return

        # Fallback for unusual parent hierarchies: use the persisted main
        # volume rather than a separate hard-coded demo loudness.
        volume = read_ui_settings().get("volume", 70)
        try:
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            volume = 70
        self.preview_audio.setVolume(volume / 100.0)
        self.preview_audio.setMuted(volume <= 0)

    def _pause_host_player(self):
        if self.host_player is None:
            self.host_player = self._find_host_player()
        if (
            self.host_player is not None
            and self.host_player.playbackState() == QMediaPlayer.PlayingState
        ):
            self.host_player_was_playing = True
            self.host_player.pause()

    def _resume_host_player(self):
        if (
            self.host_player_was_playing
            and self.host_player is not None
            and self.host_player.playbackState() != QMediaPlayer.PlayingState
        ):
            self.host_player.play()
        self.host_player_was_playing = False

    def _stop_preview(self, resume_host=True):
        self.preview_request_id += 1
        if self.preview_player.playbackState() != QMediaPlayer.StoppedState:
            self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        if self.preview_row is not None:
            self.preview_row.set_loading(False)
            self.preview_row.set_playing(False)
        self.preview_page_url = ""
        self.preview_stream_url = ""
        self.preview_row = None
        self.preview_worker = None
        buffer = self.preview_buffer
        self.preview_buffer = None
        if buffer is not None:
            buffer.close()
        if resume_host:
            self._resume_host_player()

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
            title = data.get("title") or "Music track"
            artist = data.get("artist") or "Unknown Artist"
            source = data.get("source") or "Music"
            downloads.append({
                "url": url,
                "label": f"{title} - {artist} [{source}]",
            })
        return downloads

    def add_from_url(self):
        url, accepted = QInputDialog.getText(
            self,
            "Add From URL",
            "SoundCloud or YouTube Music URL:",
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
        self._start_download(url, "Music track")

    def _start_download(self, url, title):
        self._start_download_queue([{"url": url, "label": title}])

    def _start_download_queue(self, downloads):
        if self.active_download is not None or not downloads:
            return
        self._stop_preview()
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
            if worker.last_downloaded_path:
                self.downloaded_paths.append(worker.last_downloaded_path)
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

    def accept(self):
        self._stop_preview()
        super().accept()

    def reject(self):
        if self.download_queue or (
            self.active_download is not None and self.active_download.isRunning()
        ):
            return
        self._stop_preview()
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
                destination = self.playlist_path / source.name
                shutil.copy2(source, destination)
                self.downloaded_paths.append(destination)
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
