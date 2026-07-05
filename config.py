from pathlib import Path

DOCS_PATH = Path.home() / "Documents" / "CloudPlayer"
DOWNLOADS_PATH = DOCS_PATH / "downloads"
PLAYLISTS_PATH = DOCS_PATH / "playlists"
SCRIPT_DIR = Path(__file__).parent
FFMPEG_PATH = SCRIPT_DIR / "ffmpeg.exe"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm"}

BG_COLOR = "#121212"
PANEL_BG = "#1A1A1A"
BUTTON_BG = "#222222"
BUTTON_HOVER = "#2D2D2D"
BUTTON_BORDER = "#333333"
ACCENT_COLOR = "#0D47A1"
TEXT_COLOR = "#E0E0E0"
TEXT_MUTED = "#888888"
ICON_COLOR = "#FFFFFF"

GENIUS_TOKEN = "7FBtGwlCeRvyuPf1fxFdR5_qTy3ARuxdbcaAHenQ1VXBXaHJJoJhyxB-MSVlhGqk"