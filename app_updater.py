

import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from config import DOCS_PATH, DOWNLOADS_PATH, TEXT_MUTED
from dropdown_ui import QDialog

APP_VERSION = "1.1.0"
RELEASE_API_URL = "https://api.github.com/repos/finchproffe/CloudPlayer/releases/latest"
UPDATE_STATE_PATH = DOCS_PATH / "update_state.json"
UPDATE_DOWNLOAD_PATH = DOWNLOADS_PATH / "CloudPlayer.exe"

def version_parts(value):
    parts = [int(part) for part in re.findall(r"\d+", str(value))]
    return tuple((parts + [0, 0, 0, 0])[:4])


def write_update_state(data):
    DOCS_PATH.mkdir(parents=True, exist_ok=True)
    temporary = UPDATE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(UPDATE_STATE_PATH)


def read_update_state():
    if UPDATE_STATE_PATH.is_file():
        try:
            value = json.loads(UPDATE_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    value = {
        "first_run_date": datetime.now(timezone.utc).isoformat(),
        "installed_version": APP_VERSION,
        "last_check_date": None,
        "latest_version": APP_VERSION,
        "latest_release_date": None,
        "downloaded_version": None,
        "downloaded_path": None,
        "acknowledged_version": APP_VERSION,
    }
    write_update_state(value)
    return value


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower()
    except Exception:
        return ""


class ReleaseChecker(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            request = urllib.request.Request(RELEASE_API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"CloudPlayer/{APP_VERSION}", "X-GitHub-Api-Version": "2022-11-28"})
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            tag = str(payload.get("tag_name") or "").strip()
            asset = next((item for item in payload.get("assets") or [] if str(item.get("name") or "").casefold() == "cloudplayer.exe"), None)
            if not asset:
                raise RuntimeError("CloudPlayer.exe is missing from the latest release")
            url = str(asset.get("browser_download_url") or "")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
                raise RuntimeError("The release contains an invalid download address")
            digest = str(asset.get("digest") or "")
            if not digest.lower().startswith("sha256:"):
                raise RuntimeError("GitHub did not provide a SHA-256 digest")
            size = int(asset.get("size") or 0)
            if size <= 0 or size > 1024 * 1024 * 1024:
                raise RuntimeError("The release file size is invalid")
            self.checked.emit({"version": tag.lstrip("vV") or APP_VERSION, "published_at": payload.get("published_at"), "download_url": url, "sha256": digest.split(":", 1)[1].lower(), "size": size})
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDownloader(QThread):
    progress = Signal(int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self):
        temporary = UPDATE_DOWNLOAD_PATH.with_suffix(".part")
        try:
            DOWNLOADS_PATH.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            UPDATE_DOWNLOAD_PATH.unlink(missing_ok=True)
            request = urllib.request.Request(self.release["download_url"], headers={"User-Agent": f"CloudPlayer/{APP_VERSION}"})
            digest = hashlib.sha256()
            received = 0
            expected = int(self.release["size"])
            with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                while True:
                    if self.isInterruptionRequested():
                        raise RuntimeError("Download canceled")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected:
                        raise RuntimeError("The downloaded file is too large")
                    digest.update(chunk)
                    output.write(chunk)
                    self.progress.emit(min(100, round(received * 100 / expected)))
            if received != expected:
                raise RuntimeError("The downloaded file is incomplete")
            if digest.hexdigest().lower() != self.release["sha256"]:
                raise RuntimeError("SHA-256 verification failed")
            temporary.replace(UPDATE_DOWNLOAD_PATH)
            self.completed.emit(str(UPDATE_DOWNLOAD_PATH))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CloudPlayer Update")
        self.setModal(True)
        self.setFixedWidth(430)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)
        title = QLabel("A New Version Is Available")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#ffffff")
        text = QLabel(f"CloudPlayer {release['version']} is ready to download.")
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size:14px;color:{TEXT_MUTED}")
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        download = QPushButton("Download")
        cancel.clicked.connect(self.reject)
        download.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(download)
        root.addWidget(title)
        root.addWidget(text)
        root.addLayout(buttons)
