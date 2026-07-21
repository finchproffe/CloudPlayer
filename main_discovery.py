import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from config import PLAYLISTS_PATH, TEXT_MUTED
from dropdown_ui import QInputDialog, QMessageBox, QProgressDialog
from main_common import make_menu
from recommendation_widgets import RecommendationCard
from threads import BackgroundDownloader, RecommendationFetcher, SearchWorker
from ui_polish import polish_tree


class DiscoveryMixin:
    def _send_sync(self, action, position):
        if self.p2p.role == "host" and self.p2p.is_connected:
            self.p2p.send(action, position)

    def _track_catalog(self):
        rows = []
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            url = data.get("source_url") or data.get("download_url")
            if url and str(url).startswith(("http://", "https://")):
                rows.append({"playlist": sidecar.parent.parent.name, "title": data.get("title") or sidecar.stem, "artist": data.get("artist") or "Unknown Artist", "source_url": url})
        return rows

    def _download_missing_tracks(self, catalog):


        self._room_catalog = [
            dict(track) for track in catalog if isinstance(track, dict)
        ]
        self.group_view.status.setText(
            "Catalog received. Tracks will stream and cache on demand."
        )

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
            card.play_requested.connect(lambda data, current=card: self._download_recommendation(data, current, "Recommendations", True))
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
        query = recommendation.get("source_url") or recommendation.get("url") or f"{recommendation.get('artist', '')} {recommendation.get('title', '')}"
        key = str(query).strip().casefold()
        if key in self._active_download_keys:
            return
        self._active_download_keys.add(key)
        if card:
            self._remove_recommendation_card(card)
        worker = BackgroundDownloader(query, PLAYLISTS_PATH / playlist / "songs", self)
        self.workers.append(worker)
        progress = QProgressDialog(
            "Preparing download...", "", 0, 100, self
        )
        progress.setWindowTitle("Downloading Track")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setRange(0, 0)
        self._download_progress_dialogs[worker] = progress
        worker.progress_signal.connect(
            lambda percent, status, dialog=progress: self._set_download_progress(
                dialog, percent, status
            )
        )
        worker.finished_signal.connect(
            lambda ok, message, current=worker: self._recommendation_done(
                ok,
                message,
                current,
                key,
                playlist,
                autoplay,
            )
        )
        polish_tree(progress)
        progress.show()
        worker.start()

    def _remove_recommendation_card(self, card):
        for index in range(self.rec_flow.count()):
            item = self.rec_flow.itemAt(index)
            if item and item.widget() is card:
                self.rec_flow.takeAt(index)
                break
        if card in self.rec_cards:
            self.rec_cards.remove(card)
        card.hide()
        card.deleteLater()
        self.rec_container.updateGeometry()

    @staticmethod
    def _set_download_progress(dialog, percent, status):
        dialog.setLabelText(status)
        if percent <= 0:
            dialog.setRange(0, 0)
            return
        if dialog.maximum() == 0:
            dialog.setRange(0, 100)
        dialog.setValue(percent)

    def _recommendation_done(
        self, ok, message, worker, key, playlist, autoplay
    ):
        if worker in self.workers:
            self.workers.remove(worker)
        self._active_download_keys.discard(key)
        progress = self._download_progress_dialogs.pop(worker, None)
        if progress:
            progress.close()
            progress.deleteLater()
        if ok and worker.last_downloaded_path:
            self.playlist_view.register_added_tracks(
                playlist, [worker.last_downloaded_path]
            )
        else:
            self.refresh_playlist_item(playlist)
        if not ok:
            QMessageBox.critical(self, "SoundCloud Download Error", message)
        elif autoplay and worker.last_downloaded_path:
            self.playlist_view.load_playlist(playlist)
            self._switch(1)
            self.playlist_view.play_file(worker.last_downloaded_path)
