import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _append_log(path, message):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(f"{_timestamp()} {message}\n")
    except OSError:
        pass


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _read_state(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _read_state(path)
    state.update(values)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_state_safely(path, values, log_path):
    try:
        _write_state(path, values)
    except OSError as exc:
        _append_log(log_path, f"Could not write update state: {exc}")


def _wait_for_process_windows(pid, timeout_seconds):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == 87
    try:
        result = kernel32.WaitForSingleObject(
            handle,
            max(0, int(timeout_seconds * 1000)),
        )
        return result == 0
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_process(pid, timeout_seconds):
    if os.name == "nt":
        return _wait_for_process_windows(pid, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def _remove_with_retry(path, timeout_seconds=12):
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.15)


def _replace_with_retry(source, destination, timeout_seconds=20):
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            os.replace(source, destination)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


def _health_confirmed(path, token, version):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return (
            isinstance(value, dict)
            and value.get("token") == token
            and value.get("version") == version
        )
    except (OSError, ValueError):
        return False


def _launch(path, token=None):
    command = [str(path)]
    if token:
        command.extend(("--cloudplayer-update-token", token))
    return subprocess.Popen(
        command,
        cwd=str(path.parent),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _rollback(target, backup, failed, state_path, previous_version, log_path):
    _remove_with_retry(failed)
    if target.exists() and not _replace_with_retry(target, failed):
        _append_log(log_path, "Could not preserve the failed executable")
    if not backup.exists() or not _replace_with_retry(backup, target):
        _write_state_safely(
            state_path,
            {
                "last_update_status": "rollback_failed",
                "last_update_error": "The previous executable could not be restored",
                "last_update_date": _timestamp(),
            },
            log_path,
        )
        return False
    _write_state_safely(
        state_path,
        {
            "installed_version": previous_version,
            "pending_update": None,
            "last_update_status": "rolled_back",
            "last_update_error": "The updated application exited before startup confirmation",
            "last_update_date": _timestamp(),
        },
        log_path,
    )
    _append_log(log_path, "Rolled back to the previous executable")
    try:
        _launch(target)
    except OSError as exc:
        _append_log(log_path, f"Could not restart the previous version: {exc}")
    return True


def run_update(arguments):
    source = Path(arguments.source).resolve(strict=True)
    target = Path(arguments.target).resolve(strict=False)
    state_path = Path(arguments.state).resolve(strict=False)
    health_path = Path(arguments.health).resolve(strict=False)
    log_path = Path(arguments.log).resolve(strict=False)
    token = str(arguments.token)
    expected_sha256 = str(arguments.sha256).lower()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("The update token is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise RuntimeError("The expected SHA-256 digest is invalid")
    if source.suffix.casefold() != ".exe":
        raise RuntimeError("The downloaded update is not an executable")
    if target.suffix.casefold() != ".exe" or source == target:
        raise RuntimeError("The installation target is invalid")
    if _sha256(source) != expected_sha256:
        raise RuntimeError("The downloaded update failed SHA-256 verification")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.{token}.new")
    backup = target.with_name(target.name + ".old")
    failed = target.with_name(target.name + ".failed")
    _remove_with_retry(staged)
    _remove_with_retry(health_path)
    shutil.copy2(source, staged)
    if _sha256(staged) != expected_sha256:
        _remove_with_retry(staged)
        raise RuntimeError("The staged update failed SHA-256 verification")
    _append_log(log_path, f"Staged version {arguments.version}")
    if not _wait_for_process(arguments.parent_pid, 180):
        _remove_with_retry(staged)
        raise RuntimeError("CloudPlayer did not close within 180 seconds")
    _remove_with_retry(backup)
    had_previous = target.exists()
    if had_previous and not _replace_with_retry(target, backup):
        _remove_with_retry(staged)
        raise RuntimeError("The current executable could not be backed up")
    if not _replace_with_retry(staged, target):
        if had_previous and backup.exists():
            _replace_with_retry(backup, target)
        raise RuntimeError("The new executable could not be installed")
    _write_state_safely(
        state_path,
        {
            "pending_update": {
                "token": token,
                "version": arguments.version,
                "previous_version": arguments.previous_version,
                "target": str(target),
                "backup": str(backup) if had_previous else "",
                "started_at": _timestamp(),
            },
            "last_update_status": "starting",
            "last_update_error": None,
            "last_update_date": _timestamp(),
        },
        log_path,
    )
    _append_log(log_path, "Installed the staged executable")
    try:
        process = _launch(target, token)
    except OSError as exc:
        _append_log(log_path, f"Could not launch the update: {exc}")
        if had_previous:
            _rollback(
                target,
                backup,
                failed,
                state_path,
                arguments.previous_version,
                log_path,
            )
        raise
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _health_confirmed(health_path, token, arguments.version):
            _write_state_safely(
                state_path,
                {
                    "installed_version": arguments.version,
                    "acknowledged_version": arguments.version,
                    "downloaded_version": None,
                    "downloaded_path": None,
                    "downloaded_helper_path": None,
                    "deferred_version": None,
                    "pending_update": None,
                    "last_update_status": "success",
                    "last_update_error": None,
                    "last_update_date": _timestamp(),
                },
                log_path,
            )
            _remove_with_retry(backup)
            _remove_with_retry(health_path)
            _append_log(log_path, "The updated application confirmed startup")
            return 0
        if process.poll() is not None:
            if had_previous:
                _rollback(
                    target,
                    backup,
                    failed,
                    state_path,
                    arguments.previous_version,
                    log_path,
                )
            else:
                _write_state_safely(
                    state_path,
                    {
                        "pending_update": None,
                        "last_update_status": "failed",
                        "last_update_error": "The updated application did not start",
                        "last_update_date": _timestamp(),
                    },
                    log_path,
                )
            return 1
        time.sleep(0.2)
    _write_state_safely(
        state_path,
        {
            "last_update_status": "awaiting_confirmation",
            "last_update_error": None,
            "last_update_date": _timestamp(),
        },
        log_path,
    )
    _append_log(log_path, "Startup confirmation timed out; backup was preserved")
    return 0


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--previous-version", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--health", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--log", required=True)
    return parser


def main():
    arguments = _parser().parse_args()
    log_path = Path(arguments.log).resolve(strict=False)
    try:
        return run_update(arguments)
    except Exception as exc:
        _append_log(log_path, f"Update failed: {exc}")
        _write_state_safely(
            Path(arguments.state).resolve(strict=False),
            {
                "pending_update": None,
                "last_update_status": "failed",
                "last_update_error": str(exc),
                "last_update_date": _timestamp(),
            },
            log_path,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
