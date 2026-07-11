import time

from pypresence import Presence

from config import DISCORD_CLIENT_ID

_rpc = None
_started_at = None


def connect():
    global _rpc
    try:
        _rpc = Presence(DISCORD_CLIENT_ID)
        _rpc.connect()
        print("[Discord RPC] Connected.")
    except Exception as exc:
        print(f"[Discord RPC] {exc}")
        _rpc = None


def update_now_playing(title, artist, cover_url=None):
    global _started_at
    if not _rpc:
        return
    _started_at = int(time.time())
    image = cover_url if str(cover_url or "").startswith(("http://", "https://")) else "logo"
    try:
        _rpc.update(
            details=f"Listening to: {title}"[:128],
            state=f"by {artist}"[:128],
            start=_started_at,
            large_image=image,
            large_text="CloudPlayer",
            buttons=[{"label": "Get CloudPlayer", "url": "https://finchproffe.github.io"}],
        )
    except Exception as exc:
        print(f"[Discord RPC] update: {exc}")


def update_paused():
    if not _rpc:
        return
    try:
        _rpc.update(details="CloudPlayer", state="Paused", large_image="logo")
    except Exception:
        pass


def close():
    global _rpc
    if _rpc:
        try:
            _rpc.close()
        except Exception:
            pass
    _rpc = None
