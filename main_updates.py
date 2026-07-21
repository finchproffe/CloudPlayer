from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from app_updater import (
    APP_VERSION,
    UPDATE_DOWNLOAD_PATH,
    ReleaseChecker,
    UpdateDialog,
    UpdateDownloader,
    UpdateReadyDialog,
    automatic_update_status,
    downloaded_update_is_valid,
    launch_update_installer,
    read_update_state,
    version_parts,
    write_update_state,
)
from dropdown_ui import QDialog, QMessageBox, QProgressDialog
from main_common import MENU_ICON_SIZE, make_menu
from ui_polish import polish_tree
from utils import colored_icon


class UpdateMixin:
    def _release_is_downloaded(self, release):
        return (
            self.update_state.get("downloaded_version") == release["version"]
            and downloaded_update_is_valid(release)
        )

    def _release_is_acknowledged(self, release):
        known = max(
            version_parts(APP_VERSION),
            version_parts(
                self.update_state.get("acknowledged_version") or "0"
            ),
        )
        return version_parts(release["version"]) <= known

    def _manual_check_for_updates(self):
        self._manual_update_check = True
        self._check_for_updates()

    def _check_for_updates(self):
        if self.release_checker and self.release_checker.isRunning():
            return
        self.release_checker = ReleaseChecker(self)
        self.release_checker.checked.connect(self._update_check_finished)
        self.release_checker.failed.connect(self._update_check_failed)
        self.release_checker.start()

    def _update_check_finished(self, release):
        self.latest_release = release
        self.update_state = read_update_state()
        self.update_state.update(
            {
                "installed_version": APP_VERSION,
                "last_check_date": datetime.now(timezone.utc).isoformat(),
                "latest_version": release["version"],
                "latest_release_date": release.get("published_at"),
            }
        )
        write_update_state(self.update_state)
        if version_parts(release["version"]) <= version_parts(APP_VERSION):
            if self._manual_update_check:
                QMessageBox.information(
                    self,
                    "CloudPlayer Update",
                    "You have the latest version installed.",
                )
        elif self._release_is_downloaded(release):
            deferred = self.update_state.get("deferred_version")
            if self._manual_update_check or deferred != release["version"]:
                self._show_ready_update(release)
        elif self._release_is_acknowledged(release):
            if self._manual_update_check:
                self._show_update_dialog(release)
        else:
            self._show_update_dialog(release)
        self._manual_update_check = False

    def _update_check_failed(self, message):
        if self._manual_update_check:
            QMessageBox.warning(
                self,
                "CloudPlayer Update",
                f"Could not check for updates.\n{message[:220]}",
            )
        self._manual_update_check = False

    def _show_update_dialog(self, release):
        dialog = UpdateDialog(release, self)
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted:
            self._download_update(release)

    def _download_update(self, release):
        if self.update_downloader and self.update_downloader.isRunning():
            return
        self.update_progress = QProgressDialog(
            "Downloading and verifying the update...",
            "Cancel",
            0,
            100,
            self,
        )
        self.update_progress.setWindowModality(Qt.WindowModal)
        self.update_progress.setMinimumDuration(0)
        self.update_progress.setAutoClose(False)
        self.update_progress.setAutoReset(False)
        self.update_downloader = UpdateDownloader(release, self)
        self.update_downloader.progress.connect(self.update_progress.setValue)
        self.update_downloader.completed.connect(self._update_downloaded)
        self.update_downloader.failed.connect(self._update_download_failed)
        self.update_progress.canceled.connect(
            self.update_downloader.requestInterruption
        )
        polish_tree(self.update_progress)
        self.update_progress.show()
        self.update_downloader.start()

    def _update_downloaded(self, filename, helper_filename):
        if self.update_progress:
            self.update_progress.close()
        version = (
            self.latest_release["version"]
            if self.latest_release
            else APP_VERSION
        )
        self.update_state.update(
            {
                "downloaded_version": version,
                "downloaded_path": filename,
                "downloaded_helper_path": helper_filename or None,
                "downloaded_date": datetime.now(timezone.utc).isoformat(),
                "deferred_version": None,
            }
        )
        write_update_state(self.update_state)
        if self.latest_release:
            self._show_ready_update(self.latest_release)

    def _show_ready_update(self, release):
        automatic, unavailable_reason = automatic_update_status(release)
        dialog = UpdateReadyDialog(
            release,
            automatic,
            unavailable_reason,
            self,
        )
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted:
            if automatic:
                self._install_downloaded_update(release)
            else:
                self.update_state = read_update_state()
                self.update_state["deferred_version"] = release["version"]
                write_update_state(self.update_state)
                self._open_download_folder(UPDATE_DOWNLOAD_PATH)
            return
        self.update_state = read_update_state()
        self.update_state["deferred_version"] = release["version"]
        write_update_state(self.update_state)

    def _install_downloaded_update(self, release):
        busy = any(
            worker is not None and worker.isRunning()
            for worker in (
                self._cloud_worker,
                self._cloud_download_worker,
                self._account_stats_worker,
            )
        )
        if busy:
            QMessageBox.information(
                self,
                "CloudPlayer Update",
                "Finish the current cloud operation before restarting.",
            )
            return
        started, message = launch_update_installer(release)
        if not started:
            QMessageBox.warning(
                self,
                "CloudPlayer Update",
                message,
            )
            self._open_download_folder(UPDATE_DOWNLOAD_PATH)
            return
        QTimer.singleShot(0, self.close)

    def _update_download_failed(self, message):
        if self.update_progress:
            self.update_progress.close()
        if message != "Download canceled":
            QMessageBox.critical(
                self,
                "CloudPlayer Update",
                f"The update was not downloaded.\n{message[:240]}",
            )

    def _open_download_folder(self, filename):
        path = Path(filename).resolve()
        if not path.is_file():
            return
        menu = make_menu(self.download_button)
        heading = menu.addAction("Downloaded Update")
        heading.setEnabled(False)
        location = menu.addAction(str(path))
        location.setEnabled(False)
        menu.addSeparator()
        copy_path = menu.addAction(
            colored_icon("copy.svg", size=MENU_ICON_SIZE),
            "Copy Path",
        )
        chosen = menu.exec(
            self.download_button.mapToGlobal(
                self.download_button.rect().topRight()
            )
        )
        if chosen is copy_path:
            QApplication.clipboard().setText(str(path))
