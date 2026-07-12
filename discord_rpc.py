import json
import queue
import threading
import time
from pathlib import Path

from pypresence import Presence
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer

from config import DISCORD_CLIENT_ID

_view = None
_sync_timer = None
_worker = None
_commands = queue.Queue()
_last_payload = None
_started_at = None
_running = False


def connect(view=None):
    global _view
    global _sync_timer
    global _worker
    global _running

    _view = view

    if not _running:
        _running = True
        _worker = threading.Thread(target=_rpc_worker, daemon=True)
        _worker.start()

    if _view is not None:
        _view.player.sourceChanged.connect(_schedule_sync)
        _view.player.playbackStateChanged.connect(_schedule_sync)
        _view.player.mediaStatusChanged.connect(_schedule_sync)

        if _sync_timer is None:
            _sync_timer = QTimer(_view)
            _sync_timer.setInterval(500)
            _sync_timer.timeout.connect(sync_now)
            _sync_timer.start()

    QTimer.singleShot(0, sync_now)


def _rpc_worker():
    global _running

    rpc = None

    try:
        rpc = Presence(DISCORD_CLIENT_ID)
        rpc.connect()
        print("[Discord RPC] Connected.")
    except Exception as exc:
        print(f"[Discord RPC] {exc}")

    while _running:
        try:
            command, payload = _commands.get(timeout=0.25)
        except queue.Empty:
            continue

        if command == "close":
            break

        if rpc is None:
            try:
                rpc = Presence(DISCORD_CLIENT_ID)
                rpc.connect()
                print("[Discord RPC] Reconnected.")
            except Exception as exc:
                print(f"[Discord RPC] reconnect: {exc}")
                rpc = None
                continue

        try:
            if command == "update":
                rpc.update(**payload)
            elif command == "clear":
                rpc.clear()
        except Exception as exc:
            print(f"[Discord RPC] {command}: {exc}")
            try:
                rpc.close()
            except Exception:
                pass
            rpc = None

    if rpc is not None:
        try:
            rpc.clear()
        except Exception:
            pass
        try:
            rpc.close()
        except Exception:
            pass


def _enqueue_update(payload):
    if not _running:
        return

    while True:
        try:
            old_command, old_payload = _commands.get_nowait()
            if old_command == "close":
                _commands.put((old_command, old_payload))
                return
        except queue.Empty:
            break

    _commands.put(("update", payload))


def _schedule_sync(*_args):
    QTimer.singleShot(0, sync_now)
    QTimer.singleShot(100, sync_now)


def _metadata_for_source():
    if _view is None:
        return None

    source = _view.player.source()
    if not source.isValid() or not source.isLocalFile():
        return None

    path = Path(source.toLocalFile())
    if not path.is_file():
        return None

    title = path.stem
    artist = "Unknown Artist"
    cover_url = ""
    sidecar = path.with_suffix(".json")

    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            title = str(data.get("title") or title)
            artist = str(data.get("artist") or artist)
            cover_url = str(data.get("cover_url") or "")
        except Exception:
            pass
    elif " - " in path.stem:
        artist, title = path.stem.split(" - ", 1)

    return str(path.resolve()), title, artist, cover_url


def sync_now(*_args):
    global _last_payload
    global _started_at

    if _view is None:
        return

    metadata = _metadata_for_source()
    if metadata is None:
        return

    identity, title, artist, cover_url = metadata
    state = _view.player.playbackState()
    mode = "playing" if state == QMediaPlayer.PlayingState else "paused"
    payload_key = (identity, mode, title, artist, cover_url)

    if payload_key == _last_payload:
        return

    previous_identity = _last_payload[0] if _last_payload else None
    if identity != previous_identity:
        _started_at = int(time.time())

    _last_payload = payload_key

    if mode == "playing":
        _enqueue_update(_playing_payload(title, artist, cover_url))
    else:
        _enqueue_update(_paused_payload(title, artist, cover_url))


def _image(cover_url):
    value = str(cover_url or "")
    return value if value.startswith(("http://", "https://")) else "logo"


def _playing_payload(title, artist, cover_url):
    return {
        "details": f"Listening to: {title}"[:128],
        "state": f"by {artist}"[:128],
        "start": _started_at or int(time.time()),
        "large_image": _image(cover_url),
        "large_text": "CloudPlayer",
        "buttons": [
            {
                "label": "Get CloudPlayer",
                "url": "https://finchproffe.github.io",
            }
        ],
    }


def _paused_payload(title, artist, cover_url):
    return {
        "details": f"Paused: {title}"[:128],
        "state": f"by {artist}"[:128],
        "large_image": _image(cover_url),
        "large_text": "CloudPlayer",
        "buttons": [
            {
                "label": "Get CloudPlayer",
                "url": "https://finchproffe.github.io",
            }
        ],
    }


def update_now_playing(title, artist, cover_url=None):
    global _last_payload
    global _started_at

    _started_at = int(time.time())
    _last_payload = None
    _enqueue_update(
        _playing_payload(
            str(title or "Unknown Track"),
            str(artist or "Unknown Artist"),
            cover_url,
        )
    )
    QTimer.singleShot(0, sync_now)


def update_paused():
    global _last_payload

    _last_payload = None
    sync_now()


def close():
    global _view
    global _sync_timer
    global _worker
    global _running
    global _last_payload
    global _started_at

    if _sync_timer is not None:
        _sync_timer.stop()
        _sync_timer.deleteLater()
        _sync_timer = None

    _running = False
    _commands.put(("close", None))

    if _worker is not None and _worker.is_alive():
        _worker.join(timeout=2)

    _worker = None
    _view = None
    _last_payload = None
    _started_at = None
