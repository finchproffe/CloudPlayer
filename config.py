import json
import os
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
KEYS_PATH = Path(
    os.getenv("CLOUDPLAYER_KEYS_FILE", SCRIPT_DIR / "keys.json")
)


def _read_keys():
    try:
        payload = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


_KEYS = _read_keys()


def _secret(name, *environment_names, default=""):
    for environment_name in environment_names:
        value = os.getenv(environment_name)
        if value is not None:
            return value
    return _KEYS.get(name, default)


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
DEFAULT_DEBUG = False
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


def normalize_debug(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return DEFAULT_DEBUG


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
        "debug": normalize_debug(payload.get("debug", DEFAULT_DEBUG)),
    }


def _write_ui_settings(settings):
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    payload = {
        "accent_color": (
            normalize_accent_color(settings.get("accent_color"))
            or DEFAULT_ACCENT_COLOR
        ),
        "volume": normalize_volume(settings.get("volume", DEFAULT_VOLUME)),
        "debug": normalize_debug(settings.get("debug", DEFAULT_DEBUG)),
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


def save_debug(value):
    global DEBUG_ENABLED
    enabled = normalize_debug(value)
    settings = read_ui_settings()
    settings["debug"] = enabled
    if not _write_ui_settings(settings):
        return False
    DEBUG_ENABLED = enabled
    return True


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
DEBUG_ENABLED = _UI_SETTINGS["debug"]


GENIUS_CLIENT_ID = str(
    _secret("genius_client_id", "GENIUS_CLIENT_ID")
).strip()
GENIUS_CLIENT_SECRET = str(
    _secret("genius_client_secret", "GENIUS_CLIENT_SECRET")
).strip()
GENIUS_ACCESS_TOKEN = str(
    _secret("genius_access_token", "GENIUS_ACCESS_TOKEN")
).strip()

GENIUS_TOKEN = GENIUS_ACCESS_TOKEN

DISCORD_CLIENT_ID = str(
    _secret("discord_client_id", "DISCORD_CLIENT_ID")
).strip()

SUPABASE_URL = str(
    _secret("supabase_url", "CLOUDPLAYER_SUPABASE_URL")
).rstrip("/")
SUPABASE_API_KEY = str(
    _secret(
        "supabase_api_key",
        "CLOUDPLAYER_SUPABASE_KEY",
        "SUPABASE_ANON_KEY",
    )
).strip()
SUPABASE_ADMIN_API_KEY = str(
    _secret(
        "supabase_admin_api_key",
        "CLOUDPLAYER_SUPABASE_ADMIN_KEY",
        "SUPABASE_SECRET_KEY",
    )
).strip()

TURNSTILE_SITE_KEY = str(
    _secret("turnstile_site_key", "CLOUDPLAYER_TURNSTILE_SITE_KEY")
).strip()
TURNSTILE_SECRET_KEY = str(
    _secret("turnstile_secret_key", "CLOUDPLAYER_TURNSTILE_SECRET_KEY")
).strip()
TURNSTILE_VERIFY_URL = str(
    _secret("turnstile_verify_url", "CLOUDPLAYER_TURNSTILE_VERIFY_URL")
).strip()
CAPTCHA_HTML_PATH = SCRIPT_DIR / "captcha.html"

_turn_urls = _secret("turn_urls", "CLOUDPLAYER_TURN_URLS", default=[])
if isinstance(_turn_urls, str):
    TURN_URLS = [
        value.strip() for value in _turn_urls.split(",") if value.strip()
    ]
elif isinstance(_turn_urls, (list, tuple)):
    TURN_URLS = [str(value).strip() for value in _turn_urls if str(value).strip()]
else:
    TURN_URLS = []

TURN_USERNAME = str(
    _secret("turn_username", "CLOUDPLAYER_TURN_USERNAME")
).strip()
TURN_PASSWORD = str(
    _secret("turn_password", "CLOUDPLAYER_TURN_PASSWORD")
).strip()


def genius_credentials_ready():
    return bool(GENIUS_CLIENT_ID and GENIUS_CLIENT_SECRET and GENIUS_ACCESS_TOKEN)


def px(value):
    return max(1, round(value * UI_SCALE))
