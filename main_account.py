import re
import sys
from pathlib import Path

import config as config_module
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from account_sync import (
    CloudRequestWorker,
    LoginDialog,
    clear_account_session,
    load_account_session,
    save_account_session,
)
from config import ACCENT_COLOR, SCRIPT_DIR
from debug_console import set_debug_console
from dropdown_ui import QDialog, QMessageBox
from settings_dialog import SettingsDialog
from ui_polish import polish_tree


class AccountMixin:
    def _show_settings_dialog(self):
        username = str((self.account_user or {}).get("username") or "")
        dialog = SettingsDialog(
            ACCENT_COLOR,
            config_module.DEBUG_ENABLED,
            self,
            account_username=username,
        )
        dialog.delete_account_requested.connect(
            lambda: self._delete_account(dialog)
        )
        polish_tree(dialog)
        if dialog.exec() != QDialog.Accepted:
            return
        errors = []
        if not self._apply_accent_color(dialog.selected_color):
            errors.append("The accent color could not be saved.")
        if not config_module.save_debug(dialog.debug_enabled):
            errors.append("The Debug setting could not be saved.")
        elif not set_debug_console(dialog.debug_enabled):
            config_module.save_debug(False)
            errors.append("The Debug console could not be opened.")
        if errors:
            QMessageBox.warning(
                self,
                "Settings",
                "\n".join(errors),
            )

    def _delete_account(self, settings_dialog):
        if not self.account_user:
            return
        if (
            (self._cloud_worker and self._cloud_worker.isRunning())
            or (
                self._cloud_download_worker
                and self._cloud_download_worker.isRunning()
            )
        ):
            QMessageBox.information(
                self,
                "Delete Account",
                "Wait for the current cloud operation to finish.",
            )
            return
        username = self.account_user["username"]
        if (
            QMessageBox.question(
                settings_dialog,
                "Delete Account",
                f"Permanently delete '{username}' and all synchronized "
                "tracks? This cannot be undone.",
            )
            != QMessageBox.Yes
        ):
            return
        user_id = self.account_user["id"]
        self.account_user = None
        self._account_stats_refresh_pending = False
        clear_account_session()
        self.account_panel.set_logged_out()
        settings_dialog.reject()
        self._start_cloud_request(
            "delete_account",
            (user_id,),
            "Deleting account...",
            self._delete_account_finished,
        )

    def _delete_account_finished(self, ok, result):
        if not ok or result is not True:
            QMessageBox.critical(
                self,
                "Delete Account",
                f"You were signed out, but Supabase deletion failed:\n{result}",
            )
            return
        QMessageBox.information(
            self,
            "Delete Account",
            "Your account and synchronized tracks were deleted.",
        )

    def _apply_accent_color(self, color):
        global ACCENT_COLOR

        old_color = str(ACCENT_COLOR)
        new_color = config_module.save_accent_color(color)
        if not new_color:
            return False
        ACCENT_COLOR = new_color
        if old_color.casefold() == new_color.casefold():
            return True

        color_pattern = re.compile(re.escape(old_color), re.IGNORECASE)
        project_root = SCRIPT_DIR.resolve()
        for module in list(sys.modules.values()):
            module_file = getattr(module, "__file__", None)
            if not module_file:
                continue
            try:
                module_path = Path(module_file).resolve()
            except (OSError, TypeError):
                continue
            if project_root != module_path.parent and project_root not in module_path.parents:
                continue
            namespace = vars(module)
            if "ACCENT_COLOR" in namespace:
                namespace["ACCENT_COLOR"] = new_color
            for name, value in list(namespace.items()):
                if (
                    not name.isupper()
                    or name.startswith("DEFAULT_")
                    or not isinstance(value, str)
                    or not color_pattern.search(value)
                ):
                    continue
                namespace[name] = color_pattern.sub(new_color, value)

        for widget in QApplication.allWidgets():
            style = widget.styleSheet()
            if not style or not color_pattern.search(style):
                continue
            widget.setStyleSheet(color_pattern.sub(new_color, style))
            widget.update()

        self._style()
        polish_tree(self)
        self.update()
        return True

    def _show_donation_dialog(self):
        from dialogs import DonationDialog

        if not getattr(self, "_donation_dialog", None):
            self._donation_dialog = DonationDialog(self)
        self._donation_dialog.show()
        self._donation_dialog.raise_()
        self._donation_dialog.activateWindow()

    def _show_login(self):
        if self.account_user:
            return
        dialog = LoginDialog(self)
        polish_tree(dialog)
        if dialog.exec() == QDialog.Accepted and dialog.authenticated_user:
            self._account_authenticated(dialog.authenticated_user)

    def _account_authenticated(self, user):
        self._pending_deleted_cloud_user_id = None
        self._pending_deleted_playlists.clear()
        self._pending_deleted_tracks.clear()
        self.account_user = {
            "id": str(user.get("id") or ""),
            "username": str(user.get("username") or ""),
        }
        self.account_panel.set_user(self.account_user)
        self.account_panel.set_song_count(None)
        self.account_panel.set_tracks([])
        self.account_panel.setVisible(True)
        self.account_panel.updateGeometry()
        self.account_panel.update()
        if self.home_view.layout():
            self.home_view.layout().activate()
        polish_tree(self.account_panel)
        save_account_session(self.account_user)
        self._refresh_account_stats()

    def _restore_account(self):
        user = load_account_session()
        if user:
            self._account_authenticated(user)

    def _refresh_account_stats(self):
        if not self.account_user:
            return
        if self._account_stats_worker is not None:
            self._account_stats_refresh_pending = True
            return
        user_id = self.account_user["id"]
        self._account_stats_refresh_pending = False
        worker = CloudRequestWorker("load_links", user_id, parent=self)
        self._account_stats_worker = worker
        worker.completed.connect(
            lambda ok, result, current=worker, owner=user_id: (
                self._account_stats_loaded(current, owner, ok, result)
            )
        )
        worker.finished.connect(
            lambda current=worker: self._account_stats_worker_finished(current)
        )
        worker.start()

    def _account_stats_loaded(self, worker, user_id, ok, result):
        if worker is not self._account_stats_worker:
            return
        self._account_stats_worker = None
        if not self.account_user or self.account_user["id"] != user_id:
            return
        if self._account_stats_refresh_pending:
            QTimer.singleShot(0, self._refresh_account_stats)
            return
        rows = result if ok and isinstance(result, list) else []
        self.account_panel.set_song_count(len(rows) if ok else None)
        self.account_panel.set_tracks(rows)

    def _account_stats_worker_finished(self, worker):
        worker.deleteLater()

    def _logout(self):
        if self._cloud_worker and self._cloud_worker.isRunning():
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Wait for the current cloud operation to finish.",
            )
            return
        if self._cloud_download_worker and self._cloud_download_worker.isRunning():
            QMessageBox.information(
                self,
                "Cloud Sync",
                "Cancel or finish the current playlist download first.",
            )
            return
        self.account_user = None
        self._account_stats_refresh_pending = False
        self._pending_deleted_cloud_user_id = None
        self._pending_deleted_playlists.clear()
        self._pending_deleted_tracks.clear()
        clear_account_session()
        self.account_panel.set_logged_out()
