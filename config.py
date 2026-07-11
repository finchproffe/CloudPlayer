import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DOCS_PATH = Path.home() / "Documents" / "CloudPlayer"
DOWNLOADS_PATH = DOCS_PATH / "downloads"
PLAYLISTS_PATH = DOCS_PATH / "playlists"
FFMPEG_PATH = Path(os.getenv("CLOUDPLAYER_FFMPEG", SCRIPT_DIR / "ffmpeg.exe"))
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm"}

BG_COLOR = "#121212"
PANEL_BG = "#1A1A1A"
ELEVATED_BG = "#222222"
BUTTON_BG = "#222222"
BUTTON_HOVER = "#2D2D2D"
BUTTON_BORDER = "#333333"
ACCENT_COLOR = "#0D47A1"
TEXT_COLOR = "#E0E0E0"
TEXT_MUTED = "#888888"
ICON_COLOR = "#FFFFFF"
UI_SCALE = 1.0

# Genius credentials. The API request itself uses GENIUS_ACCESS_TOKEN as Bearer.
GENIUS_CLIENT_ID = os.getenv(
    "GENIUS_CLIENT_ID",
    "ZXMAKZRDudKza7rlJZHx07AfaQJmohD2XA7gOcE6IGgx9NudM0wrCmMcaXnKQKmL",
).strip()
GENIUS_CLIENT_SECRET = os.getenv(
    "GENIUS_CLIENT_SECRET",
    "PdgNekdgkVrcS-dxbzeo2Ngumk7m3VIXmhu-jx_JrPCfMRB0AZ2P-UUMqmlerdg45ZlJ6IT5p0D3J_1GgLg3xw",
).strip()
GENIUS_ACCESS_TOKEN = os.getenv(
    "GENIUS_ACCESS_TOKEN",
    "JOSmMOPRSz0Ha5PgwB_Oyqa5eMuGY7dcxI3jdF3pmZOAxNApARfpszE1YJ5PpPfI",
).strip()

# Backward compatibility for code that still imports GENIUS_TOKEN.
GENIUS_TOKEN = GENIUS_ACCESS_TOKEN

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1523054636016341112")

TURN_URLS = [
    value.strip()
    for value in os.getenv(
        "CLOUDPLAYER_TURN_URLS",
        "turn:openrelay.metered.ca:80,turn:openrelay.metered.ca:443,turn:openrelay.metered.ca:443?transport=tcp",
    ).split(",")
    if value.strip()
]
TURN_USERNAME = os.getenv("CLOUDPLAYER_TURN_USERNAME", "openrelayproject")
TURN_PASSWORD = os.getenv("CLOUDPLAYER_TURN_PASSWORD", "openrelayproject")


def genius_credentials_ready():
    return bool(GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET and GENIUS_ACCESS_TOKEN)


def px(value):
    return max(1, round(value * UI_SCALE))
