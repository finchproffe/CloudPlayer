from html.parser import HTMLParser

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor

from config import SCRIPT_DIR, ICON_COLOR


def format_time(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def extract_sc_meta(info: dict) -> dict:
    """Extracts SoundCloud metadata using a strict priority chain."""
    raw_title = info.get('title', 'Unknown Title')
    uploader = info.get('uploader', 'Unknown Artist')

    artist = info.get('artist') or info.get('creator')
    title = info.get('track')

    if not artist or not title:
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif "-" in raw_title:
            parts = raw_title.split("-", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = artist or uploader
            title = title or raw_title

    duration = info.get('duration')
    if isinstance(duration, (int, float)):
        duration_str = format_time(int(duration * 1000))
    else:
        duration_str = info.get('duration_string', '??:??')

    return {
        "artist": str(artist).strip(),
        "title": str(title).strip(),
        "duration": duration_str
    }


def colored_icon(filename, color=ICON_COLOR, size=64):
    path = SCRIPT_DIR / filename
    if not path.is_file():
        return QIcon()

    source = QPixmap(str(path))
    if source.isNull():
        return QIcon()

    source = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    colored = QPixmap(source.size())
    colored.fill(Qt.transparent)

    painter = QPainter(colored)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(colored.rect(), QColor(color))
    painter.end()

    return QIcon(colored)


class GeniusLyricsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.recording = False
        self.lyrics = []
        self.div_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if attrs_dict.get('data-lyrics-container') == 'true':
            self.recording = True
            self.div_depth = 0
        if self.recording:
            if tag == 'div':
                self.div_depth += 1
            elif tag == 'br':
                self.lyrics.append('\n')

    def handle_endtag(self, tag):
        if self.recording:
            if tag == 'div':
                self.div_depth -= 1
                if self.div_depth <= 0:
                    self.recording = False

    def handle_data(self, data):
        if self.recording:
            self.lyrics.append(data)