

import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import PLAYLISTS_PATH, genius_credentials_ready
from lyrics_service import (
    _download_bytes, _find_genius_song, _genius_json, _lyrics_from_html,
    _normalize, _read_identity, _request, cache_lyrics, read_cached_lyrics,
)
from worker_http import HTTP_POOL_SIZE

def fetch_track_metadata(song_path):

    song_path = Path(song_path)
    title, artist, sidecar = _read_identity(song_path)
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
        cover = song_path.with_suffix(suffix)
        if cover.exists():
            try:
                result["cover_bytes"] = cover.read_bytes()
            except OSError:
                pass
            break

    cached = read_cached_lyrics(artist, title)
    if cached:
        result["lyrics"] = cached
        return result

    if not genius_credentials_ready():
        result["lyrics"] = (
            "Lyrics unavailable: Genius Client ID, Client Secret, "
            "or Access Token is missing."
        )
        return result

    try:
        song = _find_genius_song(artist, title)
        if not song:
            result["lyrics"] = "Lyrics not found on Genius."
            return result

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
                if result["cover_bytes"]:
                    try:
                        song_path.with_suffix(".jpg").write_bytes(
                            result["cover_bytes"]
                        )
                    except OSError:
                        pass

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
        song_path.with_suffix(".json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cache_lyrics(
            result["artist"], result["title"], result["lyrics"]
        )
    except Exception as exc:
        result["lyrics"] = f"Lyrics loading failed: {exc}"

    return result


class TrackMetaFetcher(QThread):
    meta_ready = Signal(dict)

    def __init__(self, song_path, parent=None):
        super().__init__(parent)
        self.song_path = Path(song_path)

    def run(self):
        self.meta_ready.emit(fetch_track_metadata(self.song_path))


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
            entries = _soundcloud_search(self.query, self.limit)
            with ThreadPoolExecutor(max_workers=HTTP_POOL_SIZE) as pool:
                rows = list(pool.map(_soundcloud_result, entries))
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
                        "cover_bytes": None,
                    })
            except Exception as exc:
                print(f"[Genius Recommendations] {exc}")

        random.shuffle(candidates)
        selected = candidates[: self.limit]

        def load_cover(row):
            row = dict(row)
            row["cover_bytes"] = _download_bytes(row.get("cover_url"))
            return row

        with ThreadPoolExecutor(max_workers=HTTP_POOL_SIZE) as pool:
            selected = list(pool.map(load_cover, selected))
        self.rec_ready.emit(selected)
