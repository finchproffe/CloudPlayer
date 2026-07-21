

import json
import os
import random
import tempfile
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QThread, Signal

from config import PLAYLISTS_PATH, genius_credentials_ready
from lyrics_service import (
    _download_bytes, _find_genius_song, _genius_json, _lyrics_from_html,
    _normalize, _read_identity, _request, cache_lyrics, read_cached_lyrics,
)
from worker_http import HTTP_POOL_SIZE


def _preview_stream_details(info):
    """Choose a Qt-friendly audio stream and the headers needed to open it."""

    candidates = []
    base_headers = dict(info.get("http_headers") or {})
    for item in list(info.get("formats") or []) + [info]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        protocol = str(item.get("protocol") or "").casefold()
        if protocol and protocol not in {
            "http", "https", "m3u8", "m3u8_native",
        }:
            continue
        vcodec = str(item.get("vcodec") or "none").casefold()
        acodec = str(item.get("acodec") or "none").casefold()
        has_audio = acodec not in {"", "none"}
        if not has_audio and item is not info:
            continue
        extension = str(item.get("ext") or "").casefold()
        audio_only = vcodec in {"", "none"}
        container_score = {
            "m4a": 40,
            "mp4": 38,
            "aac": 36,
            "mp3": 34,
            "webm": 20,
            "opus": 18,
        }.get(extension, 10)
        protocol_score = 16 if protocol in {"http", "https"} else 8
        headers = dict(base_headers)
        headers.update(item.get("http_headers") or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36",
        )
        candidates.append((
            50 if audio_only else 0,
            container_score,
            protocol_score,
            float(item.get("abr") or item.get("tbr") or 0),
            {
                "url": url,
                "headers": headers,
                "extension": extension,
            },
        ))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[:-1])[-1]


class _ChunkedPreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ChunkedDemoBuffer:
    """Progressively download audio and expose it through a local HTTP stream.

    A small prefix is buffered before QMediaPlayer receives the local URL. The
    rest of the track is downloaded in 64 KiB chunks while the player reads the
    growing temporary file. Range requests are supported because Windows media
    backends commonly probe different parts of a stream before playback.
    """

    CHUNK_SIZE = 64 * 1024
    START_BUFFER_BYTES = 128 * 1024
    START_BUFFER_TIMEOUT = 2.5

    def __init__(self, stream_url, headers=None, extension=""):
        self.stream_url = str(stream_url or "").strip()
        self.headers = dict(headers or {})
        suffix = f".{extension}" if extension else ".audio"
        handle = tempfile.NamedTemporaryFile(
            prefix="cloudplayer_demo_", suffix=suffix, delete=False
        )
        self.path = Path(handle.name)
        handle.close()

        self.token = uuid.uuid4().hex
        self.condition = threading.Condition()
        self.downloaded = 0
        self.total_size = None
        self.content_type = "audio/mp4"
        self.done = False
        self.stopped = False
        self.error = ""
        self._response = None
        self._download_thread = None
        self._server = None
        self._server_thread = None
        self.local_url = ""

    def start(self):
        self._download_thread = threading.Thread(
            target=self._download_loop,
            name="CloudPlayerDemoDownload",
            daemon=True,
        )
        self._download_thread.start()

        deadline = time.monotonic() + self.START_BUFFER_TIMEOUT
        with self.condition:
            while (
                self.downloaded < self.START_BUFFER_BYTES
                and not self.done
                and not self.error
                and not self.stopped
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(0.15, remaining))

            if self.error and self.downloaded == 0:
                raise RuntimeError(self.error)

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def do_HEAD(self):
                owner._serve(self, send_body=False)

            def do_GET(self):
                owner._serve(self, send_body=True)

        self._server = _ChunkedPreviewHTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        self.local_url = f"http://127.0.0.1:{port}/demo/{self.token}"
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="CloudPlayerDemoHTTP",
            daemon=True,
        )
        self._server_thread.start()
        return self.local_url

    def _download_loop(self):
        try:
            request = urllib.request.Request(self.stream_url, headers=self.headers)
            response = urllib.request.urlopen(request, timeout=20)
            self._response = response
            content_length = response.headers.get("Content-Length")
            content_type = response.headers.get("Content-Type")
            with self.condition:
                if content_length:
                    try:
                        self.total_size = int(content_length)
                    except (TypeError, ValueError):
                        self.total_size = None
                if content_type:
                    self.content_type = content_type.split(";", 1)[0].strip()

            with self.path.open("wb", buffering=0) as output:
                while not self.stopped:
                    chunk = response.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    with self.condition:
                        self.downloaded += len(chunk)
                        self.condition.notify_all()

            with self.condition:
                self.done = True
                if self.total_size is None:
                    self.total_size = self.downloaded
                self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                if not self.stopped:
                    self.error = str(exc)[:300]
                self.done = True
                self.condition.notify_all()
        finally:
            response = self._response
            self._response = None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    @staticmethod
    def _parse_range(value, total_size):
        if not value or not value.startswith("bytes="):
            return None
        value = value[6:].split(",", 1)[0].strip()
        if "-" not in value:
            return None
        start_text, end_text = value.split("-", 1)
        try:
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else None
            elif end_text and total_size:
                suffix = int(end_text)
                start = max(0, total_size - suffix)
                end = total_size - 1
            else:
                return None
        except ValueError:
            return None
        if start < 0:
            return None
        if total_size is not None:
            if start >= total_size:
                return (start, start - 1)
            if end is None or end >= total_size:
                end = total_size - 1
        return start, end

    def _serve(self, handler, send_body):
        if handler.path.split("?", 1)[0] != f"/demo/{self.token}":
            handler.send_error(404)
            return

        with self.condition:
            while self.downloaded == 0 and not self.done and not self.stopped:
                self.condition.wait(0.1)
            total = self.total_size
            downloaded = self.downloaded
            error = self.error

        if self.stopped:
            handler.send_error(410)
            return
        if error and downloaded == 0:
            handler.send_error(502)
            return

        requested = self._parse_range(handler.headers.get("Range"), total)
        if requested is not None:
            start, end = requested
            if end is not None and end < start:
                handler.send_response(416)
                if total is not None:
                    handler.send_header("Content-Range", f"bytes */{total}")
                handler.end_headers()
                return
            status = 206
        else:
            start, end, status = 0, None, 200

        handler.send_response(status)
        handler.send_header("Content-Type", self.content_type or "application/octet-stream")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        if total is not None:
            final_end = total - 1 if end is None else min(end, total - 1)
            if status == 206:
                handler.send_header(
                    "Content-Range", f"bytes {start}-{final_end}/{total}"
                )
            handler.send_header("Content-Length", str(max(0, final_end - start + 1)))
        handler.end_headers()
        if not send_body:
            return

        position = start
        try:
            while not self.stopped:
                with self.condition:
                    while (
                        self.downloaded <= position
                        and not self.done
                        and not self.error
                        and not self.stopped
                    ):
                        self.condition.wait(0.15)
                    available_end = self.downloaded
                    finished = self.done or bool(self.error)

                if end is not None:
                    available_end = min(available_end, end + 1)
                if available_end > position:
                    amount = min(self.CHUNK_SIZE, available_end - position)
                    with self.path.open("rb") as source:
                        source.seek(position)
                        data = source.read(amount)
                    if not data:
                        if finished:
                            break
                        time.sleep(0.02)
                        continue
                    handler.wfile.write(data)
                    handler.wfile.flush()
                    position += len(data)
                    if end is not None and position > end:
                        break
                    continue

                if finished:
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def close(self):
        with self.condition:
            if self.stopped:
                return
            self.stopped = True
            self.condition.notify_all()

        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        server = self._server
        if server is not None:
            def shutdown_server():
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
            threading.Thread(target=shutdown_server, daemon=True).start()

        def cleanup():
            thread = self._download_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

        threading.Thread(target=cleanup, daemon=True).start()


class DemoStreamResolver(QThread):
    """Resolve a catalogue URL and prepare a progressively buffered demo."""

    resolved = Signal(str, str, object)
    failed = Signal(str, str)

    def __init__(self, page_url, parent=None):
        super().__init__(parent)
        self.page_url = str(page_url or "").strip()

    def run(self):
        buffer = None
        try:
            import yt_dlp

            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                # Prefer progressive AAC/M4A because its headers are usually
                # available at the beginning and start quickly on Windows.
                "format": "bestaudio[ext=m4a][protocol^=http]/"
                          "bestaudio[protocol^=http]/bestaudio/best",
            }
            if os.name == "nt":
                options["windows_creation_flags"] = 0x08000000

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self.page_url, download=False) or {}

            entries = info.get("entries") or []
            if entries and isinstance(entries[0], dict):
                info = entries[0]
            details = _preview_stream_details(info)
            if not details:
                raise RuntimeError("No playable audio stream was returned")

            buffer = ChunkedDemoBuffer(
                details["url"],
                headers=details.get("headers"),
                extension=details.get("extension") or "",
            )
            local_url = buffer.start()
            self.resolved.emit(self.page_url, local_url, buffer)
        except Exception as exc:
            if buffer is not None:
                buffer.close()
            self.failed.emit(self.page_url, str(exc)[:300])


def fetch_track_metadata(song_path, should_stop=None):

    song_path = Path(song_path)
    should_stop = should_stop or (lambda: False)
    if should_stop():
        return None
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
        if should_stop():
            return None
        cover = song_path.with_suffix(suffix)
        if cover.exists():
            try:
                result["cover_bytes"] = cover.read_bytes()
            except OSError:
                pass
            break

    cached = read_cached_lyrics(artist, title)
    if should_stop():
        return None
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
        if should_stop():
            return None
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
                if result["cover_bytes"] and not should_stop():
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

        if should_stop():
            return None

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
        result = fetch_track_metadata(
            self.song_path, self.isInterruptionRequested
        )
        if result is not None and not self.isInterruptionRequested():
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
        "album": entry.get("album") or "",
        "duration": entry.get("duration_string") or "",
        "source": "SoundCloud",
        "source_key": "soundcloud",
        "url": url,
        "source_url": url,
        "cover_url": cover_url,
        "cover_bytes": _download_bytes(cover_url),
    }


def _youtube_music_artist(row):
    artists = row.get("artists") or []
    names = [
        str(artist.get("name") or "").strip()
        for artist in artists
        if isinstance(artist, dict)
    ]
    names = [name for name in names if name]
    return ", ".join(names) or str(row.get("artist") or "Unknown Artist")


def _largest_thumbnail(row):
    thumbnails = [
        item for item in (row.get("thumbnails") or [])
        if isinstance(item, dict) and item.get("url")
    ]
    if not thumbnails:
        return ""
    return max(
        thumbnails,
        key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
    ).get("url") or ""


def _youtube_music_result(row):
    video_id = str(row.get("videoId") or "").strip()
    if not video_id:
        return None
    album = row.get("album") or {}
    if isinstance(album, dict):
        album = album.get("name") or ""
    cover_url = _largest_thumbnail(row)
    return {
        "title": str(row.get("title") or "Unknown Title").strip(),
        "artist": _youtube_music_artist(row),
        "album": str(album or "").strip(),
        "duration": str(row.get("duration") or "").strip(),
        "source": "YouTube Music",
        "source_key": "youtube_music",
        "url": f"https://music.youtube.com/watch?v={video_id}",
        "source_url": f"https://music.youtube.com/watch?v={video_id}",
        "source_id": video_id,
        "cover_url": cover_url,
        "cover_bytes": None,
    }


_NOISE_TERMS = (
    "live", "cover", "karaoke", "nightcore", "slowed", "reverb",
    "sped up", "8d", "instrumental", "remix", "reaction", "shorts",
)


def _search_tokens(value):
    return {
        token for token in _normalize(value).split()
        if len(token) > 1
    }


def _music_result_score(row, query):
    title = str(row.get("title") or "")
    artist = _youtube_music_artist(row)
    haystack = _normalize(f"{title} {artist}")
    normalized_query = _normalize(query)
    query_tokens = _search_tokens(query)
    result_tokens = _search_tokens(haystack)
    score = 0
    overlap = query_tokens & result_tokens
    if query_tokens and not overlap:
        score -= 100
    if normalized_query and normalized_query in haystack:
        score += 100
    score += len(overlap) * 18
    if query_tokens and query_tokens.issubset(result_tokens):
        score += 45
    video_type = str(row.get("videoType") or "").casefold()
    if "official_source_music" in video_type or "atv" in video_type:
        score += 30
    if row.get("album"):
        score += 8
    for term in _NOISE_TERMS:
        if term in haystack and term not in normalized_query:
            score -= 35
    duration_seconds = row.get("duration_seconds") or row.get("durationSeconds")
    try:
        duration_seconds = int(duration_seconds)
    except (TypeError, ValueError):
        duration_seconds = 0
    if duration_seconds and (duration_seconds < 45 or duration_seconds > 900):
        score -= 18
    return score


def _youtube_music_fallback_search(query, limit):
    import yt_dlp

    candidate_limit = max(24, limit * 4)
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
        data = ydl.extract_info(
            f"ytsearch{candidate_limit}:{query} official audio",
            download=False,
        ) or {}

    rows = []
    for entry in data.get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not video_id or not title:
            continue
        uploader = str(
            entry.get("channel") or entry.get("uploader") or "Unknown Artist"
        ).strip()
        artist = uploader[:-8].rstrip() if uploader.casefold().endswith(" - topic") else uploader
        rows.append({
            "videoId": video_id,
            "title": title,
            "artists": [{"name": artist}],
            "album": {"name": entry.get("album") or ""},
            "duration": entry.get("duration_string") or "",
            "duration_seconds": entry.get("duration") or 0,
            "thumbnails": entry.get("thumbnails") or [],
            "resultType": "song",
            "videoType": (
                "MUSIC_VIDEO_TYPE_ATV"
                if uploader.casefold().endswith(" - topic")
                else ""
            ),
        })
    return rows


def _youtube_music_search(query, limit):
    candidate_limit = max(20, limit * 3)
    try:
        from ytmusicapi import YTMusic

        rows = YTMusic().search(
            query,
            filter="songs",
            limit=candidate_limit,
            ignore_spelling=False,
        )
    except Exception as exc:
        print(f"[YouTube Music Search] Catalogue fallback: {exc}")
        rows = _youtube_music_fallback_search(query, limit)

    rows = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("videoId")
        and str(row.get("resultType") or "song").casefold() == "song"
    ]
    rows.sort(key=lambda row: _music_result_score(row, query), reverse=True)

    results = []
    seen = set()
    for row in rows:
        result = _youtube_music_result(row)
        if not result:
            continue
        identity = (
            _normalize(result["artist"]),
            _normalize(result["title"]),
        )
        if identity in seen:
            continue
        seen.add(identity)
        results.append(result)
        if len(results) >= limit:
            break
    return results


def _balanced_results(rows_by_source, limit):
    active = [key for key, rows in rows_by_source.items() if rows]
    if len(active) <= 1:
        rows = rows_by_source.get(active[0], []) if active else []
        return rows[:limit]

    result = []
    positions = {key: 0 for key in active}
    while len(result) < limit:
        added = False
        for key in active:
            position = positions[key]
            rows = rows_by_source[key]
            if position >= len(rows):
                continue
            result.append(rows[position])
            positions[key] += 1
            added = True
            if len(result) >= limit:
                break
        if not added:
            break
    return result


class SearchWorker(QThread):
    results_ready = Signal(list)
    source_errors = Signal(dict)

    def __init__(self, query, parent=None, limit=12, sources=None):
        super().__init__(parent)
        self.query = query
        self.limit = limit
        normalized_sources = []
        for source in sources or ("soundcloud",):
            source = str(source or "").strip().casefold()
            if source in {"soundcloud", "youtube_music"} and source not in normalized_sources:
                normalized_sources.append(source)
        self.sources = normalized_sources or ["soundcloud"]

    def _search_source(self, source):
        if source == "youtube_music":
            return _youtube_music_search(self.query, self.limit)
        entries = _soundcloud_search(self.query, self.limit)
        with ThreadPoolExecutor(max_workers=HTTP_POOL_SIZE) as pool:
            return list(pool.map(_soundcloud_result, entries))

    def run(self):
        rows_by_source = {}
        errors = {}
        workers = min(len(self.sources), 2)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                source: pool.submit(self._search_source, source)
                for source in self.sources
            }
            for source, future in futures.items():
                try:
                    rows_by_source[source] = future.result()
                except Exception as exc:
                    label = (
                        "YouTube Music"
                        if source == "youtube_music"
                        else "SoundCloud"
                    )
                    print(f"[{label} Search] {exc}")
                    rows_by_source[source] = []
                    errors[source] = str(exc)[:300]

        rows = _balanced_results(rows_by_source, self.limit)
        self.results_ready.emit(rows)
        if errors:
            self.source_errors.emit(errors)


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
