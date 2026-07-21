

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from config import DOCS_PATH, DOWNLOADS_PATH, TEMP_PATH, TEXT_MUTED
from dropdown_ui import QDialog

APP_VERSION = "1.6.2"
RELEASE_API_URL = "https://api.github.com/repos/finchproffe/CloudPlayer/releases/latest"
UPDATE_STATE_PATH = DOCS_PATH / "update_state.json"
UPDATE_DOWNLOAD_PATH = DOWNLOADS_PATH / "CloudPlayer.update.exe"
UPDATE_HELPER_PATH = DOWNLOADS_PATH / "CloudPlayerUpdater.exe"
UPDATE_LOG_PATH = DOCS_PATH / "update.log"


def _release_asset(payload, filename, required=True):
    asset = next(
        (
            item
            for item in payload.get("assets") or []
            if str(item.get("name") or "").casefold() == filename.casefold()
        ),
        None,
    )
    if asset is None:
        if required:
            raise RuntimeError(f"{filename} is missing from the latest release")
        return None
    url = str(asset.get("browser_download_url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }:
        raise RuntimeError(f"{filename} has an invalid download address")
    digest = str(asset.get("digest") or "")
    if not digest.lower().startswith("sha256:"):
        raise RuntimeError(f"GitHub did not provide a SHA-256 digest for {filename}")
    sha256 = digest.split(":", 1)[1].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RuntimeError(f"GitHub provided an invalid SHA-256 digest for {filename}")
    size = int(asset.get("size") or 0)
    if size <= 0 or size > 1024 * 1024 * 1024:
        raise RuntimeError(f"{filename} has an invalid file size")
    return {
        "name": filename,
        "download_url": url,
        "sha256": sha256,
        "size": size,
    }

def version_parts(value):
    parts = [int(part) for part in re.findall(r"\d+", str(value))]
    return tuple((parts + [0, 0, 0, 0])[:4])


def write_update_state(data):
    DOCS_PATH.mkdir(parents=True, exist_ok=True)
    temporary = UPDATE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(UPDATE_STATE_PATH)


def read_update_state():
    value = None
    if UPDATE_STATE_PATH.is_file():
        try:
            value = json.loads(UPDATE_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            value = None
    if not isinstance(value, dict):
        value = {}
    defaults = {
        "first_run_date": datetime.now(timezone.utc).isoformat(),
        "installed_version": APP_VERSION,
        "last_check_date": None,
        "latest_version": APP_VERSION,
        "latest_release_date": None,
        "downloaded_version": None,
        "downloaded_path": None,
        "downloaded_helper_path": None,
        "acknowledged_version": APP_VERSION,
        "deferred_version": None,
        "pending_update": None,
        "last_update_status": None,
        "last_update_error": None,
    }
    for key, default in defaults.items():
        value.setdefault(key, default)
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
            application = _release_asset(payload, "CloudPlayer.exe")
            updater = _release_asset(
                payload,
                "CloudPlayerUpdater.exe",
                required=False,
            )
            self.checked.emit(
                {
                    "version": tag.lstrip("vV") or APP_VERSION,
                    "published_at": payload.get("published_at"),
                    "download_url": application["download_url"],
                    "sha256": application["sha256"],
                    "size": application["size"],
                    "updater": updater,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDownloader(QThread):
    progress = Signal(int)
    completed = Signal(str, str)
    failed = Signal(str)

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release

    def _download_asset(self, asset, target, received_before, total_expected):
        temporary = target.with_name(target.name + ".part")
        temporary.unlink(missing_ok=True)
        request = urllib.request.Request(
            asset["download_url"],
            headers={"User-Agent": f"CloudPlayer/{APP_VERSION}"},
        )
        digest = hashlib.sha256()
        received = 0
        expected = int(asset["size"])
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response, temporary.open("wb") as output:
            while True:
                if self.isInterruptionRequested():
                    raise RuntimeError("Download canceled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > expected:
                    raise RuntimeError(f"{asset['name']} is too large")
                digest.update(chunk)
                output.write(chunk)
                self.progress.emit(
                    min(
                        100,
                        round(
                            (received_before + received)
                            * 100
                            / total_expected
                        ),
                    )
                )
        if received != expected:
            raise RuntimeError(f"{asset['name']} is incomplete")
        if digest.hexdigest().lower() != asset["sha256"]:
            raise RuntimeError(f"{asset['name']} failed SHA-256 verification")
        temporary.replace(target)
        return received

    def run(self):
        active_temporary = None
        try:
            DOWNLOADS_PATH.mkdir(parents=True, exist_ok=True)
            application = {
                "name": "CloudPlayer.exe",
                "download_url": self.release["download_url"],
                "sha256": self.release["sha256"],
                "size": int(self.release["size"]),
            }
            updater = self.release.get("updater")
            assets = [(application, UPDATE_DOWNLOAD_PATH)]
            if updater:
                assets.append((updater, UPDATE_HELPER_PATH))
            total_expected = sum(int(asset["size"]) for asset, _path in assets)
            received = 0
            for asset, target in assets:
                active_temporary = target.with_name(target.name + ".part")
                received += self._download_asset(
                    asset,
                    target,
                    received,
                    total_expected,
                )
            self.completed.emit(
                str(UPDATE_DOWNLOAD_PATH),
                str(UPDATE_HELPER_PATH) if updater else "",
            )
        except Exception as exc:
            if active_temporary is not None:
                active_temporary.unlink(missing_ok=True)
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


class UpdateReadyDialog(QDialog):
    def __init__(
        self,
        release,
        automatic_install,
        unavailable_reason="",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("CloudPlayer Update")
        self.setModal(True)
        self.setFixedWidth(470)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)
        title = QLabel("Update Is Ready")
        title.setStyleSheet("font-size:22px;font-weight:700;color:#ffffff")
        if automatic_install:
            message = (
                f"CloudPlayer {release['version']} has been verified. "
                "The application will close, install the update, restart "
                "and automatically restore the previous version if startup fails."
            )
        else:
            message = (
                f"CloudPlayer {release['version']} has been verified. "
                + (
                    unavailable_reason
                    or "Automatic replacement is not available for this build."
                )
            )
        text = QLabel(message)
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size:14px;color:{TEXT_MUTED}")
        buttons = QHBoxLayout()
        buttons.addStretch()
        later = QPushButton("Later")
        install = QPushButton(
            "Update and Restart" if automatic_install else "Open Download Folder"
        )
        later.clicked.connect(self.reject)
        install.clicked.connect(self.accept)
        buttons.addWidget(later)
        buttons.addWidget(install)
        root.addWidget(title)
        root.addWidget(text)
        root.addLayout(buttons)


def current_install_target():
    if os.name != "nt":
        return None
    candidates = [Path(sys.executable)]
    if sys.argv:
        candidates.append(Path(sys.argv[0]))
    downloaded = os.path.normcase(str(UPDATE_DOWNLOAD_PATH.resolve()))
    for candidate in candidates:
        try:
            target = candidate.resolve()
        except OSError:
            continue
        name = target.name.casefold()
        interpreter = re.fullmatch(
            r"(?:pythonw?|pypy|pypy3|py)(?:\d+(?:\.\d+)*)?\.exe",
            name,
        )
        if (
            target.suffix.casefold() != ".exe"
            or interpreter
            or not target.is_file()
            or os.path.normcase(str(target)) == downloaded
        ):
            continue
        return target
    return None


def downloaded_update_is_valid(release):
    if not isinstance(release, dict):
        return False
    if not UPDATE_DOWNLOAD_PATH.is_file() or (
        UPDATE_DOWNLOAD_PATH.stat().st_size != int(release["size"])
        or file_sha256(UPDATE_DOWNLOAD_PATH) != release["sha256"]
    ):
        return False
    updater = release.get("updater") if isinstance(release, dict) else None
    if not updater:
        return True
    return (
        UPDATE_HELPER_PATH.is_file()
        and UPDATE_HELPER_PATH.stat().st_size == int(updater["size"])
        and file_sha256(UPDATE_HELPER_PATH) == updater["sha256"]
    )


def automatic_update_status(release):
    if not isinstance(release, dict):
        return False, "The release metadata is invalid."
    if not release.get("updater"):
        return False, (
            "This GitHub Release does not contain CloudPlayerUpdater.exe."
        )
    if not downloaded_update_is_valid(release):
        return False, (
            "The downloaded application or updater is missing or failed verification."
        )
    if current_install_target() is None:
        return False, (
            "The running CloudPlayer executable could not be identified. "
            "Start CloudPlayer.exe directly instead of main.py."
        )
    return (
        True,
        "",
    )


def update_health_path(token):
    return TEMP_PATH / f"update-health-{token}.json"


def launch_update_installer(release):
    target = current_install_target()
    if target is None:
        return False, (
            "CloudPlayer could not identify its running executable. "
            "Start the installed CloudPlayer.exe directly instead of main.py."
        )
    updater = release.get("updater") if isinstance(release, dict) else None
    if not updater:
        return False, "CloudPlayerUpdater.exe is missing from this release."
    if not UPDATE_DOWNLOAD_PATH.is_file() or (
        UPDATE_DOWNLOAD_PATH.stat().st_size != int(release["size"])
        or file_sha256(UPDATE_DOWNLOAD_PATH) != release["sha256"]
    ):
        return False, "CloudPlayer.exe failed verification before installation."
    if not UPDATE_HELPER_PATH.is_file() or (
        UPDATE_HELPER_PATH.stat().st_size != int(updater["size"])
        or file_sha256(UPDATE_HELPER_PATH) != updater["sha256"]
    ):
        return False, "CloudPlayerUpdater.exe failed verification."
    token = secrets.token_hex(16)
    health_path = update_health_path(token)
    TEMP_PATH.mkdir(parents=True, exist_ok=True)
    health_path.unlink(missing_ok=True)
    command = [
        str(UPDATE_HELPER_PATH),
        "--parent-pid",
        str(os.getpid()),
        "--source",
        str(UPDATE_DOWNLOAD_PATH),
        "--target",
        str(target),
        "--sha256",
        str(release["sha256"]),
        "--version",
        str(release["version"]),
        "--previous-version",
        APP_VERSION,
        "--state",
        str(UPDATE_STATE_PATH),
        "--health",
        str(health_path),
        "--token",
        token,
        "--log",
        str(UPDATE_LOG_PATH),
    ]
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    try:
        subprocess.Popen(
            command,
            cwd=str(target.parent),
            close_fds=True,
            creationflags=creation_flags,
        )
    except OSError as exc:
        return False, f"CloudPlayerUpdater.exe could not start: {exc}"
    state = read_update_state()
    state.update(
        {
            "last_update_status": "waiting_for_exit",
            "last_update_error": None,
            "install_requested_version": release["version"],
            "install_requested_date": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_update_state(state)
    return True, ""


def consume_update_token(arguments):
    cleaned = list(arguments)
    token = ""
    try:
        index = cleaned.index("--cloudplayer-update-token")
    except ValueError:
        return cleaned, token
    if index + 1 < len(cleaned):
        candidate = str(cleaned[index + 1]).strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", candidate):
            token = candidate
        del cleaned[index:index + 2]
    else:
        del cleaned[index]
    return cleaned, token


def acknowledge_update_startup(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        return False
    TEMP_PATH.mkdir(parents=True, exist_ok=True)
    health_path = update_health_path(token)
    temporary = health_path.with_name(health_path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "token": token,
                "version": APP_VERSION,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(health_path)
    return True
