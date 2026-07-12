import html
import json
import os
import random
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import (
    AUDIO_EXTENSIONS,
    FFMPEG_PATH,
    GENIUS_ACCESS_TOKEN,
    GENIUS_CLIENT_ID,
    GENIUS_CLIENT_SECRET,
    PLAYLISTS_PATH,
    genius_credentials_ready,
)
from utils import extract_sc_meta

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CloudPlayer/3"
GENIUS_API = "https://api.genius.com"


class GeniusLyricsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.recording = False
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-lyrics-container") == "true":
            self.recording = True
            self.depth = 0
        if self.recording:
            if tag == "div":
                self.depth += 1
            elif tag == "br":
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.recording and tag == "div":
            self.depth -= 1
            if self.depth <= 0:
                self.recording = False
                self.parts.append("\n")

    def handle_data(self, data):
        if self.recording:
            self.parts.append(data)


def _genius_headers():
    """Genius API authenticates with the access token.

    Client ID and Client Secret identify the configured application and are
    validated separately before any Genius operation starts.
    """
    return {
        "Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "X-CloudPlayer-Client-ID": GENIUS_CLIENT_ID,
    }


def _request(url, headers=None, timeout=10):
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        **(headers or {}),
    }
    request = urllib.request.Request(url, headers=merged)
    return urllib.request.urlopen(request, timeout=timeout)


def _request_json(url, headers=None, timeout=10):
    with _request(url, headers, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _genius_json(path, params=None):
    if not genius_credentials_ready():
        raise RuntimeError(
            "Genius Client ID, Client Secret, and Access Token are required."
        )
    query = urllib.parse.urlencode(params or {})
    url = f"{GENIUS_API}{path}"
    if query:
        url += f"?{query}"
    try:
        return _request_json(url, _genius_headers())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                "Genius authorization failed: check GENIUS_ACCESS_TOKEN."
            ) from exc
        if exc.code == 403:
            raise RuntimeError(
                "Genius access was denied: verify all three credentials."
            ) from exc
        raise


def _normalize(text):
    value = html.unescape(str(text or "")).casefold().strip()
    value = re.sub(r"\([^)]*\)|\[[^]]*]", " ", value)
    value = re.sub(r"\b(?:feat|ft|featuring)\.?\b.*$", " ", value)
    value = re.sub(r"[^a-zа-яё0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _matches(expected, actual):
    expected = _normalize(expected)
    actual = _normalize(actual)
    return bool(expected and actual) and (
        expected == actual or expected in actual or actual in expected
    )


def _clean_lyrics(raw):
    value = html.unescape(raw or "").replace("\r", "")
    value = re.sub(r"^.*?Lyrics\s*", "", value, count=1, flags=re.I | re.S)
    value = re.sub(r"\d+Embed\s*$", "", value, flags=re.I)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _lyrics_from_html(page_html):
    parser = GeniusLyricsParser()
    parser.feed(page_html)
    lyrics = _clean_lyrics("".join(parser.parts))
    if lyrics:
        return lyrics

    blocks = re.findall(
        r'data-lyrics-container=["\']true["\'][^>]*>(.*?)</div>',
        page_html,
        flags=re.I | re.S,
    )
    if not blocks:
        return ""
    text = "\n".join(blocks)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return _clean_lyrics(text)


def _read_identity(song_path):
    sidecar_data = {}
    sidecar = song_path.with_suffix(".json")
    if sidecar.exists():
        try:
            sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[Lyrics] Sidecar read failed: {exc}")

    stem = re.sub(r"^\d+[.\s-]*", "", song_path.stem).strip()
    artist = str(sidecar_data.get("artist") or "").strip()
    title = str(sidecar_data.get("title") or "").strip()

    if not artist or not title:
        if " - " in stem:
            parsed_artist, parsed_title = stem.split(" - ", 1)
        elif "-" in stem:
            parsed_artist, parsed_title = stem.split("-", 1)
        else:
            parsed_artist, parsed_title = "Unknown Artist", stem
        artist = artist or parsed_artist.strip()
        title = title or parsed_title.strip()

    return title, artist, sidecar_data


def _find_genius_song(artist, title):
    payload = _genius_json("/search", {"q": f"{artist} {title}"})
    best = None
    best_score = -1

    for hit in payload.get("response", {}).get("hits", []):
        result = hit.get("result") or {}
        hit_artist = result.get("primary_artist", {}).get("name", "")
        hit_title = result.get("title", "")
        score = 0
        if _matches(artist, hit_artist):
            score += 2
        if _matches(title, hit_title):
            score += 3
        if score > best_score:
            best = result
            best_score = score

    return best if best_score >= 3 else None


def _download_bytes(url, limit=8 * 1024 * 1024):
    if not url:
        return None
    try:
        with _request(url, timeout=8) as response:
            return response.read(limit)
    except Exception:
        return None


class TrackMetaFetcher(QThread):
    meta_ready = Signal(dict)

    def __init__(self, song_path, parent=None):
        super().__init__(parent)
        self.song_path = Path(song_path)

    def run(self):
        title, artist, sidecar = _read_identity(self.song_path)
        result = {
            "title": title,
            "artist": artist,
            "prod": "",
            "lyrics": "Lyrics not found.",
            "cover_bytes": None,
            "cover_url": sidecar.get("cover_url") or "",
            "duration": sidecar.get("duration") or "",
            "genius_url": sidecar.get("genius_url") or "",
        }

        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            cover = self.song_path.with_suffix(suffix)
            if cover.exists():
                try:
                    result["cover_bytes"] = cover.read_bytes()
                except Exception:
                    pass
                break

        if not genius_credentials_ready():
            result["lyrics"] = (
                "Lyrics unavailable: Genius Client ID, Client Secret, "
                "or Access Token is missing."
            )
            self.meta_ready.emit(result)
            return

        try:
            song = _find_genius_song(artist, title)
            if not song:
                result["lyrics"] = "Lyrics not found on Genius."
                self.meta_ready.emit(result)
                return

            result["title"] = song.get("title") or title
            result["artist"] = (
                song.get("primary_artist", {}).get("name") or artist
            )
            result["genius_url"] = song.get("url") or ""
            cover_url = (
                song.get("song_art_image_thumbnail_url")
                or song.get("header_image_thumbnail_url")
            )

            if cover_url:
                result["cover_url"] = cover_url
                if not result["cover_bytes"]:
                    result["cover_bytes"] = _download_bytes(cover_url)

            if result["genius_url"]:
                with _request(
                    result["genius_url"],
                    {"Accept": "text/html,application/xhtml+xml"},
                    timeout=12,
                ) as response:
                    page_html = response.read().decode("utf-8", "ignore")
                result["lyrics"] = (
                    _lyrics_from_html(page_html)
                    or "Lyrics not found on the Genius page."
                )

            sidecar.update({
                "title": result["title"],
                "artist": result["artist"],
                "cover_url": result["cover_url"],
                "genius_url": result["genius_url"],
            })
            self.song_path.with_suffix(".json").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            result["lyrics"] = f"Lyrics loading failed: {exc}"

        self.meta_ready.emit(result)


def _soundcloud_search(query, limit):
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if os.name == "nt":
        options["windows_creation_flags"] = 0x08000000

    with yt_dlp.YoutubeDL(options) as ydl:
        data = ydl.extract_info(f"scsearch{limit}:{query}", download=False) or {}
    return [entry for entry in data.get("entries", []) if entry]


def _soundcloud_result(entry):
    url = entry.get("webpage_url") or entry.get("url") or ""
    thumbnails = [
        item for item in (entry.get("thumbnails") or []) if item.get("url")
    ]
    cover_url = entry.get("thumbnail")
    if thumbnails:
        cover_url = max(
            thumbnails,
            key=lambda item: (item.get("width") or 0)
            * (item.get("height") or 0),
        )["url"]

    return {
        "title": entry.get("track") or entry.get("title") or "Unknown Title",
        "artist": (
            entry.get("artist")
            or entry.get("uploader")
            or "Unknown Artist"
        ),
        "source": "SoundCloud",
        "url": url,
        "source_url": url,
        "cover_url": cover_url,
        "cover_bytes": _download_bytes(cover_url),
    }


class SearchWorker(QThread):
    results_ready = Signal(list)

    def __init__(self, query, parent=None, limit=12):
        super().__init__(parent)
        self.query = query
        self.limit = limit

    def run(self):
        try:
            rows = [
                _soundcloud_result(entry)
                for entry in _soundcloud_search(self.query, self.limit)
            ]
        except Exception as exc:
            print(f"[SoundCloud Search] {exc}")
            rows = []
        self.results_ready.emit(rows[: self.limit])


class RecommendationFetcher(QThread):
    rec_ready = Signal(list)

    def __init__(self, parent=None, limit=8):
        super().__init__(parent)
        self.limit = limit

    @staticmethod
    def _artists():
        artists = []
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                artist = json.loads(
                    sidecar.read_text(encoding="utf-8")
                ).get("artist")
                if artist and artist.casefold() != "unknown artist":
                    artists.append(artist.strip())
            except Exception:
                pass
        return list(dict.fromkeys(artists))

    @staticmethod
    def _existing_tracks():
        tracks = set()
        for sidecar in PLAYLISTS_PATH.glob("*/songs/*.json"):
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                tracks.add((
                    _normalize(data.get("artist")),
                    _normalize(data.get("title")),
                ))
            except Exception:
                pass
        return tracks

    def run(self):
        if not genius_credentials_ready():
            print(
                "[Genius] Client ID, Client Secret, or Access Token is missing."
            )
            self.rec_ready.emit([])
            return

        candidates = []
        seen = set()
        existing = self._existing_tracks()

        for artist in self._artists()[:8]:
            try:
                search = _genius_json("/search", {"q": artist})
                hits = search.get("response", {}).get("hits", [])
                artist_id = next(
                    (
                        hit["result"]["primary_artist"]["id"]
                        for hit in hits
                        if hit.get("result")
                    ),
                    None,
                )
                if not artist_id:
                    continue

                payload = _genius_json(
                    f"/artists/{artist_id}/songs",
                    {"sort": "popularity", "per_page": 12},
                )
                songs = payload.get("response", {}).get("songs", [])

                for song in songs:
                    song_artist = (
                        song.get("primary_artist", {}).get("name") or artist
                    )
                    title = song.get("title") or ""
                    key = (_normalize(song_artist), _normalize(title))
                    if not title or key in seen or key in existing:
                        continue
                    seen.add(key)
                    cover_url = (
                        song.get("song_art_image_thumbnail_url")
                        or song.get("header_image_thumbnail_url")
                    )
                    candidates.append({
                        "title": title,
                        "artist": song_artist,
                        "source": "Genius",
                        "url": "",
                        "source_url": "",
                        "genius_url": song.get("url") or "",
                        "cover_url": cover_url,
                        "cover_bytes": _download_bytes(cover_url),
                    })
            except Exception as exc:
                print(f"[Genius Recommendations] {exc}")

        random.shuffle(candidates)
        self.rec_ready.emit(candidates[: self.limit])


class BackgroundDownloader(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, query_or_url, destination, parent=None):
        super().__init__(parent)
        self.query_or_url = str(query_or_url)
        self.destination = Path(destination)
        self.last_downloaded_path = None

    def run(self):
        try:
            import yt_dlp

            self.destination.mkdir(parents=True, exist_ok=True)
            requested = self.query_or_url
            target = (
                requested
                if requested.startswith(("http://", "https://"))
                else f"scsearch1:{requested}"
            )
            options = {
                "format": "bestaudio/best",
                "noplaylist": True,
                "writethumbnail": True,
                "outtmpl": str(
                    self.destination
                    / "%(artist,uploader)s - %(title)s.%(ext)s"
                ),
                "ffmpeg_location": str(FFMPEG_PATH),
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "320",
                    },
                    {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                ],
            }
            if os.name == "nt":
                options["windows_creation_flags"] = 0x08000000

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(target, download=True)
                if info and info.get("entries"):
                    info = next(
                        (entry for entry in info["entries"] if entry), info
                    )

                downloads = (info or {}).get("requested_downloads") or []
                raw_path = (
                    downloads[0].get("filepath")
                    if downloads
                    else ydl.prepare_filename(info)
                )
                raw_path = Path(raw_path)
                mp3_path = raw_path.with_suffix(".mp3")
                self.last_downloaded_path = (
                    mp3_path if mp3_path.exists() else raw_path
                )

                source_url = (
                    (info or {}).get("webpage_url")
                    or (info or {}).get("original_url")
                    or requested
                )
                metadata = extract_sc_meta(info or {})
                metadata.update({
                    "source": "SoundCloud",
                    "source_url": source_url,
                    "download_url": source_url,
                    "source_id": (info or {}).get("id") or "",
                    "extractor": (
                        (info or {}).get("extractor_key") or "SoundCloud"
                    ),
                })
                self.last_downloaded_path.with_suffix(".json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            self.finished_signal.emit(True, str(self.last_downloaded_path))
        except Exception as exc:
            self.finished_signal.emit(False, str(exc)[:1000])
