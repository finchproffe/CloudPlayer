

import hashlib
import html
import json
import re
import threading
import urllib.parse
from html.parser import HTMLParser

import requests

from config import (
    GENIUS_ACCESS_TOKEN, GENIUS_CLIENT_ID, LYRICS_CACHE_PATH,
    genius_credentials_ready,
)
from worker_http import HTTP_SESSION, USER_AGENT, _SessionResponse

GENIUS_API = "https://api.genius.com"
_CACHE_WRITE_LOCK = threading.Lock()

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

    return {
        "Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "X-CloudPlayer-Client-ID": GENIUS_CLIENT_ID,
    }


def _request(url, headers=None, timeout=10):
    response = HTTP_SESSION.get(
        url,
        headers=headers or None,
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    return _SessionResponse(response)


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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 401:
            raise RuntimeError(
                "Genius authorization failed: check GENIUS_ACCESS_TOKEN."
            ) from exc
        if status == 403:
            raise RuntimeError(
                "Genius access was denied: verify all three credentials."
            ) from exc
        raise


def _normalize(text):
    value = html.unescape(str(text or "")).casefold().strip()
    value = re.sub(r"\([^)]*\)|\[[^]]*]", " ", value)
    value = re.sub(r"\b(?:feat|ft|featuring)\.?\b.*$", " ", value)
    value = re.sub(r"[^a-z\u0430-\u044f\u04510-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _matches(expected, actual):
    expected = _normalize(expected)
    actual = _normalize(actual)
    return bool(expected and actual) and (
        expected == actual or expected in actual or actual in expected
    )


def _lyrics_cache_file(artist, title):
    identity = "\0".join(
        re.sub(r"\s+", " ", html.unescape(str(value or "")).casefold()).strip()
        for value in (artist, title)
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    label = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]+',
        "_",
        f"{artist} - {title}",
    ).strip(" ._")
    label = re.sub(r"\s+", " ", label)[:100] or "lyrics"
    return LYRICS_CACHE_PATH / f"{label} [{digest}].txt"


def read_cached_lyrics(artist, title):
    path = _lyrics_cache_file(artist, title)
    try:
        lyrics = path.read_text(encoding="utf-8").strip()
        return lyrics or None
    except (OSError, UnicodeError):
        return None


def cache_lyrics(artist, title, lyrics):
    lyrics = str(lyrics or "").strip()
    unavailable = (
        "lyrics not found",
        "lyrics unavailable",
        "lyrics loading failed",
    )
    if not lyrics or lyrics.casefold().startswith(unavailable):
        return None
    path = _lyrics_cache_file(artist, title)
    with _CACHE_WRITE_LOCK:
        LYRICS_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
        temporary.write_text(lyrics, encoding="utf-8")
        temporary.replace(path)
    return path


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
