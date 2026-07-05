from pypresence import Presence
import time

CLIENT_ID = "1523054636016341112"
rpc_client = None
start_time = None


def connect():
    global rpc_client
    try:
        rpc_client = Presence(CLIENT_ID)
        rpc_client.connect()
        print("[RPC] Successfully connected to Discord!")
    except Exception as e:
        print(f"[RPC] Connection error (is Discord closed?): {e}")
        rpc_client = None


def update_now_playing(title, artist, cover_url):
    global rpc_client, start_time
    if not rpc_client:
        return

    start_time = int(time.time())

    if cover_url and (str(cover_url).startswith("http://") or str(cover_url).startswith("https://")):
        image_key = cover_url
    else:
        image_key = "logo"

    try:
        rpc_client.update(
            state=f"by {artist}",
            details=f"Listening to: {title}",
            large_image=image_key,
            large_text="CloudPlayer",
            start=start_time,
            buttons=[{"label": "Get CloudPlayer", "url": "https://finchproffe.github.io"}]
        )
    except Exception as e:
        print(f"[RPC] Status update error: {e}")


def update_paused():
    global rpc_client
    if not rpc_client:
        return
    try:
        rpc_client.update(
            state="Paused",
            details="CloudPlayer",
            large_image="logo",
            buttons=[{"label": "Get CloudPlayer", "url": "https://finchproffe.github.io"}]
        )
    except Exception:
        pass