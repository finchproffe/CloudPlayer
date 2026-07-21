from PySide6.QtCore import QTimer, Qt

from account_sync import (
    CloudRequestWorker,
    collect_playlist_tracks,
    local_playlist_urls,
)
from config import PLAYLISTS_PATH
from dropdown_ui import QInputDialog, QMessageBox, QProgressDialog
from threads import BackgroundDownloader
from ui_polish import polish_tree


class CloudSyncMixin:
    def _playlist_names(self):
        return sorted(self._known_playlist_names, key=str.casefold)

    def _choose_playlist(self, title, label, names):
        if not names:
            return None
        current_name = (
            str(self.playlist_list.currentItem().data(Qt.UserRole))
            if self.playlist_list.currentItem()
            else ""
        )
        current = names.index(current_name) if current_name in names else 0
        chosen, accepted = QInputDialog.getItem(
            self, title, label, names, current, False
        )
        return str(chosen) if accepted and chosen else None

    def _start_cloud_request(self, operation, arguments, label, callback):
        if (
            (self._cloud_worker and self._cloud_worker.isRunning())
            or (
                self._cloud_download_worker
                and self._cloud_download_worker.isRunning()
            )
        ):
            return
        self.account_panel.set_busy(True)
        progress = QProgressDialog(label, "", 0, 0, self)
        progress.setWindowTitle("Cloud Sync")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._cloud_progress = progress
        worker = CloudRequestWorker(operation, *arguments, parent=self)
        self._cloud_worker = worker
        worker.completed.connect(
            lambda ok, result, current=worker: self._cloud_request_done(
                current, callback, ok, result
            )
        )
        worker.finished.connect(worker.deleteLater)
        polish_tree(progress)
        progress.show()
        worker.start()

    def _cloud_request_done(self, worker, callback, ok, result):
        if worker is not self._cloud_worker:
            return
        self._cloud_worker = None
        if self._cloud_progress:
            self._cloud_progress.close()
            self._cloud_progress.deleteLater()
            self._cloud_progress = None
        self.account_panel.set_busy(False)
        callback(ok, result)
        QTimer.singleShot(0, self._flush_deleted_cloud_entries)

    def _synchronize_playlist(self):
        if not self.account_user:
            self._show_login()
            return
        playlist = self._choose_playlist(
            "Synchronize Playlist",
            "Playlist to synchronize:",
            self._playlist_names(),
        )
        if not playlist:
            return
        tracks, without_url = collect_playlist_tracks(playlist)
        if not tracks:
            message = "This playlist has no downloadable track links."
            if without_url:
                message += " Local files cannot be synchronized."
            QMessageBox.information(self, "Cloud Sync", message)
            return
        self._sync_without_url = without_url
        self._start_cloud_request(
            "synchronize",
            (self.account_user["id"], tracks),
            f"Synchronizing {playlist}...",
            self._synchronize_finished,
        )

    def _synchronize_finished(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Cloud Sync", str(result))
            return
        inserted = int(result.get("inserted") or 0)
        existing = int(result.get("already_synced") or 0)
        skipped = int(getattr(self, "_sync_without_url", 0) or 0)
        synchronized_links = result.get("links")
        if isinstance(synchronized_links, list):
            self.account_panel.set_song_count(
                int(result.get("total_synced") or len(synchronized_links))
            )
            self.account_panel.set_tracks(synchronized_links)
        else:
            self.account_panel.set_song_count(
                self.account_panel.song_count + inserted
            )
        parts = [
            f"Added: {inserted}",
            f"Already synchronized: {existing}",
        ]
        if skipped:
            parts.append(f"Skipped local files without a source link: {skipped}")
        self._refresh_account_stats()
        QMessageBox.information(self, "Cloud Sync", "\n".join(parts))

    def _unsynchronize_tracks(self, rows):
        if not self.account_user:
            return
        link_ids = [
            row.get("id")
            for row in rows or []
            if isinstance(row, dict) and row.get("id") is not None
        ]
        if not link_ids:
            return
        count = len(link_ids)
        description = (
            "this synchronized track"
            if count == 1
            else f"these {count} synchronized tracks"
        )
        if (
            QMessageBox.question(
                self,
                "Unsync",
                f"Unsync {description}?",
            )
            != QMessageBox.Yes
        ):
            return
        self._start_cloud_request(
            "unsynchronize",
            (self.account_user["id"], link_ids),
            f"Unsyncing {count} track(s)...",
            self._unsynchronize_finished,
        )

    def _unsynchronize_finished(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Unsync", str(result))
            return
        rows = result if isinstance(result, list) else []
        self.account_panel.set_song_count(len(rows))
        self.account_panel.set_tracks(rows)
        self._refresh_account_stats()

    def _local_tracks_deleted(self, playlist_name, urls):
        self._queue_deleted_cloud_entries(
            tracks=[(playlist_name, url) for url in urls or []]
        )

    def _queue_deleted_cloud_entries(
        self, playlist_names=None, tracks=None
    ):
        if not self.account_user:
            return
        user_id = str(self.account_user.get("id") or "")
        if not user_id:
            return
        if (
            self._pending_deleted_cloud_user_id
            and self._pending_deleted_cloud_user_id != user_id
        ):
            self._pending_deleted_playlists.clear()
            self._pending_deleted_tracks.clear()
        self._pending_deleted_cloud_user_id = user_id
        self._pending_deleted_playlists.update(
            str(name or "").strip()
            for name in playlist_names or []
            if str(name or "").strip()
        )
        self._pending_deleted_tracks.update(
            (str(track[0] or "").strip(), str(track[1] or "").strip())
            for track in tracks or []
            if isinstance(track, (list, tuple))
            and len(track) >= 2
            and str(track[0] or "").strip()
            and str(track[1] or "").strip()
        )
        QTimer.singleShot(0, self._flush_deleted_cloud_entries)

    def _flush_deleted_cloud_entries(self):
        if not self.account_user:
            self._pending_deleted_playlists.clear()
            self._pending_deleted_tracks.clear()
            self._pending_deleted_cloud_user_id = None
            return
        user_id = str(self.account_user.get("id") or "")
        if user_id != self._pending_deleted_cloud_user_id:
            self._pending_deleted_playlists.clear()
            self._pending_deleted_tracks.clear()
            self._pending_deleted_cloud_user_id = None
            return
        if not self._pending_deleted_playlists and not self._pending_deleted_tracks:
            self._pending_deleted_cloud_user_id = None
            return
        if (
            (self._cloud_worker and self._cloud_worker.isRunning())
            or (
                self._cloud_download_worker
                and self._cloud_download_worker.isRunning()
            )
        ):
            return
        playlists = sorted(self._pending_deleted_playlists)
        tracks = sorted(self._pending_deleted_tracks)
        self._pending_deleted_playlists.clear()
        self._pending_deleted_tracks.clear()
        self._pending_deleted_cloud_user_id = None
        self._start_cloud_request(
            "unsynchronize_matching",
            (user_id, playlists, tracks),
            "Removing synchronized tracks...",
            lambda ok, result, owner=user_id: (
                self._deleted_cloud_entries_finished(owner, ok, result)
            ),
        )

    def _deleted_cloud_entries_finished(self, user_id, ok, result):
        if not self.account_user or str(self.account_user.get("id") or "") != user_id:
            return
        if not ok:
            QMessageBox.warning(
                self,
                "Cloud Sync",
                "The local files were deleted, but their synchronized "
                f"copies could not be removed:\n{result}",
            )
            return
        rows = result if isinstance(result, list) else []
        self.account_panel.set_song_count(len(rows))
        self.account_panel.set_tracks(rows)
        self._refresh_account_stats()

    def _load_cloud_playlist(self):
        if not self.account_user:
            self._show_login()
            return
        self._start_cloud_request(
            "load_links",
            (self.account_user["id"],),
            "Loading synchronized playlists...",
            self._cloud_links_loaded,
        )

    def _cloud_links_loaded(self, ok, result):
        if not ok:
            QMessageBox.critical(self, "Cloud Sync", str(result))
            return
        self.account_panel.set_song_count(len(result))
        self.account_panel.set_tracks(result)
        rows = [row for row in result if isinstance(row, dict) and row.get("url")]
        if not rows:
            QMessageBox.information(
                self, "Cloud Sync", "There are no synchronized tracks yet."
            )
            return
        playlist_names = sorted(
            {
                str(row.get("playlist_name") or "Cloud Playlist")
                for row in rows
            },
            key=str.casefold,
        )
        selected = self._choose_playlist(
            "Load Playlist",
            "Synchronized playlist to download:",
            playlist_names,
        )
        if not selected:
            return
        destination = self._ensure_playlist(selected)
        existing_urls = local_playlist_urls(destination)
        seen = set(existing_urls)
        queue = []
        for row in rows:
            remote_playlist = str(row.get("playlist_name") or "Cloud Playlist")
            url = str(row.get("url") or "").strip()
            if remote_playlist != selected or not url or url in seen:
                continue
            seen.add(url)
            queue.append(row)
        if not queue:
            QMessageBox.information(
                self,
                "Cloud Sync",
                "All tracks from this playlist are already downloaded.",
            )
            return
        self._cloud_load_playlist = destination
        self._cloud_load_queue = queue
        self._cloud_load_index = 0
        self._cloud_load_failures = []
        self._cloud_load_cancelled = False
        self.account_panel.set_busy(True)
        progress = QProgressDialog(
            "Preparing playlist download...", "Cancel", 0, 100, self
        )
        progress.setWindowTitle("Loading Playlist")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_cloud_load)
        self._cloud_progress = progress
        polish_tree(progress)
        progress.show()
        self._start_next_cloud_track()

    def _cancel_cloud_load(self):
        self._cloud_load_cancelled = True
        if self._cloud_progress:
            self._cloud_progress.setLabelText(
                "Cancelling after the current track..."
            )

    def _start_next_cloud_track(self):
        if self._cloud_load_cancelled:
            self._finish_cloud_load(cancelled=True)
            return
        if self._cloud_load_index >= len(self._cloud_load_queue):
            self._finish_cloud_load()
            return
        row = self._cloud_load_queue[self._cloud_load_index]
        worker = BackgroundDownloader(
            row["url"],
            PLAYLISTS_PATH / self._cloud_load_playlist / "songs",
            self,
        )
        self._cloud_download_worker = worker
        self.workers.append(worker)
        worker.progress_signal.connect(
            lambda percent, status, current=worker: self._cloud_track_progress(
                current, percent, status
            )
        )
        worker.finished_signal.connect(
            lambda ok, message, current=worker: self._cloud_track_done(
                current, ok, message
            )
        )
        worker.start()

    def _cloud_track_progress(self, worker, percent, status):
        if worker is not self._cloud_download_worker or not self._cloud_progress:
            return
        total = max(1, len(self._cloud_load_queue))
        track_number = self._cloud_load_index + 1
        row = self._cloud_load_queue[self._cloud_load_index]
        title = str(row.get("song_title") or "Track")
        self._cloud_progress.setLabelText(
            f"{track_number}/{total} — {title}\n{status}"
        )
        track_percent = max(0, min(100, int(percent or 0)))
        overall = round(
            (self._cloud_load_index + track_percent / 100) * 100 / total
        )
        self._cloud_progress.setValue(overall)

    def _cloud_track_done(self, worker, ok, message):
        if worker in self.workers:
            self.workers.remove(worker)
        if worker is not self._cloud_download_worker:
            return
        self._cloud_download_worker = None
        if ok and worker.last_downloaded_path:
            self.playlist_view.register_added_tracks(
                self._cloud_load_playlist, [worker.last_downloaded_path]
            )
        if not ok:
            row = self._cloud_load_queue[self._cloud_load_index]
            self._cloud_load_failures.append(
                (str(row.get("song_title") or row.get("url")), str(message))
            )
        self._cloud_load_index += 1
        if self._cloud_progress:
            self._cloud_progress.setValue(
                round(
                    self._cloud_load_index
                    * 100
                    / max(1, len(self._cloud_load_queue))
                )
            )
        QTimer.singleShot(0, self._start_next_cloud_track)

    def _finish_cloud_load(self, cancelled=False):
        downloaded = self._cloud_load_index - len(self._cloud_load_failures)
        total = len(self._cloud_load_queue)
        playlist = getattr(self, "_cloud_load_playlist", "")
        if self._cloud_progress:
            self._cloud_progress.close()
            self._cloud_progress.deleteLater()
            self._cloud_progress = None
        if playlist:
            self.refresh_playlist_item(playlist)
        self.account_panel.set_busy(False)
        self._cloud_load_queue = []
        QTimer.singleShot(0, self._flush_deleted_cloud_entries)
        if cancelled:
            QMessageBox.information(
                self,
                "Cloud Sync",
                f"Download cancelled. Completed: {downloaded}/{total}.",
            )
        elif self._cloud_load_failures:
            first_title, first_error = self._cloud_load_failures[0]
            QMessageBox.warning(
                self,
                "Cloud Sync",
                f"Downloaded: {downloaded}/{total}.\n"
                f"Failed: {len(self._cloud_load_failures)}.\n"
                f"First error ({first_title}): {first_error[:220]}",
            )
        else:
            QMessageBox.information(
                self,
                "Cloud Sync",
                f"Downloaded {downloaded} tracks to {playlist}.",
            )
