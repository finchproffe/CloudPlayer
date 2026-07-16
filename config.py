import json
import os
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DOCS_PATH = Path.home() / "Documents" / "CloudPlayer"
DOWNLOADS_PATH = DOCS_PATH / "downloads"
PLAYLISTS_PATH = DOCS_PATH / "playlists"
TEMP_PATH = DOCS_PATH / "temp"
LYRICS_CACHE_PATH = TEMP_PATH / "lyrics"
FFMPEG_PATH = Path(os.getenv("CLOUDPLAYER_FFMPEG", SCRIPT_DIR / "ffmpeg.exe"))
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm"}

BG_COLOR = "#121212"
PANEL_BG = "#1A1A1A"
ELEVATED_BG = "#222222"
BUTTON_BG = "#222222"
BUTTON_HOVER = "#2D2D2D"
BUTTON_BORDER = "#333333"
TEXT_COLOR = "#E0E0E0"
TEXT_MUTED = "#888888"
ICON_COLOR = "#FFFFFF"
UI_SCALE = 1.0

DEFAULT_ACCENT_COLOR = "#0D47A1"
DEFAULT_VOLUME = 70
SETTINGS_PATH = DOCS_PATH / "settings.json"


def normalize_accent_color(value):
    value = str(value or "").strip().upper()
    if not value.startswith("#"):
        value = f"#{value}"
    return value if re.fullmatch(r"#[0-9A-F]{6}", value) else None


def normalize_volume(value):
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return DEFAULT_VOLUME


def read_ui_settings():
    try:
        payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    color = normalize_accent_color(
        payload.get("accent_color")
    )
    return {
        "accent_color": color or DEFAULT_ACCENT_COLOR,
        "volume": normalize_volume(payload.get("volume", DEFAULT_VOLUME)),
    }


def _write_ui_settings(settings):
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    payload = {
        "accent_color": (
            normalize_accent_color(settings.get("accent_color"))
            or DEFAULT_ACCENT_COLOR
        ),
        "volume": normalize_volume(settings.get("volume", DEFAULT_VOLUME)),
    }
    try:
        DOCS_PATH.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        temporary.replace(SETTINGS_PATH)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def save_accent_color(value):
    global ACCENT_COLOR
    color = normalize_accent_color(value)
    if not color:
        return None
    settings = read_ui_settings()
    settings["accent_color"] = color
    if not _write_ui_settings(settings):
        return None
    ACCENT_COLOR = color
    return color


def save_volume(value):
    global SAVED_VOLUME
    volume = normalize_volume(value)
    settings = read_ui_settings()
    settings["volume"] = volume
    if not _write_ui_settings(settings):
        return False
    SAVED_VOLUME = volume
    return True


_UI_SETTINGS = read_ui_settings()
ACCENT_COLOR = _UI_SETTINGS["accent_color"]
SAVED_VOLUME = _UI_SETTINGS["volume"]


GENIUS_CLIENT_ID = os.getenv(
    "GENIUS_CLIENT_ID",
    "",
).strip()
GENIUS_CLIENT_SECRET = os.getenv(
    "GENIUS_CLIENT_SECRET",
    "",
).strip()
GENIUS_ACCESS_TOKEN = os.getenv(
    "GENIUS_ACCESS_TOKEN",
    "",
).strip()


GENIUS_TOKEN = GENIUS_ACCESS_TOKEN

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")

SUPABASE_URL = os.getenv(
    "CLOUDPLAYER_SUPABASE_URL",
    "",
).rstrip("/")
SUPABASE_API_KEY = os.getenv(
    "CLOUDPLAYER_SUPABASE_KEY",
    os.getenv(
        "SUPABASE_ANON_KEY",
        "",
    ),
).strip()
SUPABASE_ADMIN_API_KEY = os.getenv(
    "CLOUDPLAYER_SUPABASE_ADMIN_KEY",
    os.getenv(
        "SUPABASE_SECRET_KEY",
        "",
    ),
).strip()

TURNSTILE_SITE_KEY = os.getenv(
    "CLOUDPLAYER_TURNSTILE_SITE_KEY",
    "",
).strip()
TURNSTILE_SECRET_KEY = os.getenv(
    "CLOUDPLAYER_TURNSTILE_SECRET_KEY",
    "",
).strip()
TURNSTILE_VERIFY_URL = (
    ""
)
CAPTCHA_HTML_PATH = SCRIPT_DIR / "captcha.html"

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
