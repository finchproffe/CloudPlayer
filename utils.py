import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from config import ICON_COLOR, SCRIPT_DIR


def format_time(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def extract_sc_meta(info):
    return {
        "title": info.get("track") or info.get("title") or "Unknown Title",
        "artist": info.get("artist") or info.get("uploader") or "Unknown Artist",
        "album": info.get("album") or "",
        "duration": info.get("duration_string") or "",
        "cover_url": info.get("thumbnail") or "",
        "source_url": info.get("webpage_url") or info.get("original_url") or "",
    }


def rounded_cover_pixmap(source, size, radius):
    if source is None:
        return None
    if isinstance(source, (str, Path)):
        source = QPixmap(str(source))
    if not isinstance(source, QPixmap) or source.isNull():
        return None


    scaled = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    output = QPixmap(size, size)
    output.fill(Qt.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(
        (size - scaled.width()) // 2,
        (size - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return output


def _asset_path(filename):

    filename = Path(filename).name
    candidates = (
        SCRIPT_DIR / filename,
        SCRIPT_DIR / "icons" / filename,
        Path.cwd() / filename,
        Path.cwd() / "icons" / filename,
    )
    return next((path for path in candidates if path.is_file()), None)


def svg_icon(svg_text, color=ICON_COLOR, size=24):

    svg_text = svg_text.replace("currentColor", color)
    svg_text = re.sub(r'(?i)(stroke|fill)="(?:#000000|#000|black|#ffffff|#fff|white)"',
                      lambda match: f'{match.group(1)}="{color}"', svg_text)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def colored_svg_renderer(filename, color=ICON_COLOR):
    path = _asset_path(filename)
    if path is None:
        print(f"[Icon] Missing: {filename} (searched project root and icons folder)")
        return QSvgRenderer()
    try:
        source = path.read_text(encoding="utf-8").replace(
            "currentColor",
            color,
        )
        source = re.sub(
            r'(?i)(stroke|fill)="(?:#000000|#000|black|#ffffff|#fff|white)"',
            lambda match: f'{match.group(1)}="{color}"',
            source,
        )
        return QSvgRenderer(QByteArray(source.encode("utf-8")))
    except Exception as exc:
        print(f"[Icon] Failed to load {path}: {exc}")
        return QSvgRenderer()


def colored_icon(filename, color=ICON_COLOR, size=24):
    path = _asset_path(filename)
    if path is None:
        print(f"[Icon] Missing: {filename} (searched project root and icons folder)")
        return QIcon()
    try:
        if path.suffix.lower() == ".svg":
            return svg_icon(path.read_text(encoding="utf-8"), color, size)
        return QIcon(str(path))
    except Exception as exc:
        print(f"[Icon] Failed to load {path}: {exc}")
        return QIcon()
