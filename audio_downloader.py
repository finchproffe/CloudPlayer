

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import FFMPEG_PATH
from lyrics_service import _download_bytes
from utils import extract_sc_meta
from worker_http import (
    HTTP_POOL_SIZE, HTTP_SESSION, NETWORK_BUFFER_SIZE,
    PARALLEL_DOWNLOAD_CONNECTIONS, PARALLEL_RANGE_RETRIES,
    ParallelDownloadError,
)

_DOWNLOAD_LOCK = threading.Lock()

def _direct_http_audio_format(info):

    candidates = []
    for item in info.get("formats") or []:
        protocol = str(item.get("protocol") or "").casefold()
        url = str(item.get("url") or "")
        if (
            protocol not in {"http", "https"}
            or not url.startswith(("http://", "https://"))
            or str(item.get("vcodec") or "none") != "none"
            or str(item.get("acodec") or "none") == "none"
        ):
            continue
        candidates.append(item)

    if not candidates:
        protocol = str(info.get("protocol") or "").casefold()
        url = str(info.get("url") or "")
        if protocol in {"http", "https"} and url.startswith(
            ("http://", "https://")
        ):
            candidates.append(info)

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            float(item.get("quality") or 0),
            float(item.get("abr") or item.get("tbr") or 0),
            int(item.get("filesize") or item.get("filesize_approx") or 0),
        ),
    )


def _audio_request_headers(info, audio_format):
    headers = {}
    headers.update(info.get("http_headers") or {})
    headers.update(audio_format.get("http_headers") or {})
    headers["Accept-Encoding"] = "identity"
    headers["Connection"] = "keep-alive"
    return {str(key): str(value) for key, value in headers.items() if value}


def _probe_range_size(url, headers):
    probe_headers = dict(headers)
    probe_headers["Range"] = "bytes=0-0"
    with HTTP_SESSION.get(
        url,
        headers=probe_headers,
        timeout=(10, 20),
        stream=True,
        allow_redirects=True,
    ) as response:
        if response.status_code != 206:
            raise ParallelDownloadError(
                f"CDN does not support byte ranges (HTTP {response.status_code})"
            )
        match = re.match(
            r"bytes\s+0-0/(\d+)",
            str(response.headers.get("Content-Range") or ""),
            re.I,
        )
        if not match:
            raise ParallelDownloadError("CDN did not return the audio size")
        total = int(match.group(1))
        if total <= 0:
            raise ParallelDownloadError("CDN returned an invalid audio size")
        return total


def _download_byte_range(
    url, path, headers, start, end, total, progress_callback=None
):
    expected = end - start + 1
    last_error = None
    for _attempt in range(PARALLEL_RANGE_RETRIES):
        received = 0
        try:
            range_headers = dict(headers)
            range_headers["Range"] = f"bytes={start}-{end}"
            with HTTP_SESSION.get(
                url,
                headers=range_headers,
                timeout=(10, 45),
                stream=True,
                allow_redirects=True,
            ) as response:
                if response.status_code != 206:
                    raise ParallelDownloadError(
                        f"Range {start}-{end}: HTTP {response.status_code}"
                    )
                content_range = str(
                    response.headers.get("Content-Range") or ""
                )
                match = re.match(
                    r"bytes\s+(\d+)-(\d+)/(\d+)", content_range, re.I
                )
                if (
                    not match
                    or int(match.group(1)) != start
                    or int(match.group(2)) != end
                    or int(match.group(3)) != total
                ):
                    raise ParallelDownloadError(
                        f"Range {start}-{end}: invalid Content-Range"
                    )
                with path.open("r+b", buffering=0) as output:
                    output.seek(start)
                    for chunk in response.iter_content(NETWORK_BUFFER_SIZE):
                        if not chunk:
                            continue
                        if received + len(chunk) > expected:
                            raise ParallelDownloadError(
                                f"Range {start}-{end}: too many bytes"
                            )
                        output.write(chunk)
                        received += len(chunk)
                        if progress_callback is not None:
                            progress_callback(len(chunk))
            if received != expected:
                raise ParallelDownloadError(
                    f"Range {start}-{end}: got {received} of {expected} bytes"
                )
            return received
        except Exception as exc:
            last_error = exc
    raise ParallelDownloadError(str(last_error or "Range download failed"))


def _parallel_http_download(url, path, headers, progress_callback=None):
    total = _probe_range_size(url, headers)
    connections = 8 if total >= 4 * 1024 * 1024 else 4
    connections = max(1, min(PARALLEL_DOWNLOAD_CONNECTIONS, connections, total))
    part_size = (total + connections - 1) // connections
    ranges = [
        (start, min(total - 1, start + part_size - 1))
        for start in range(0, total, part_size)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with path.open("wb") as output:
        output.truncate(total)
    received_total = 0
    progress_lock = threading.Lock()

    def report(delta):
        nonlocal received_total
        with progress_lock:
            received_total += delta
            if progress_callback is not None:
                progress_callback(received_total, total)

    try:
        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [
                pool.submit(
                    _download_byte_range,
                    url,
                    path,
                    headers,
                    start,
                    end,
                    total,
                    report,
                )
                for start, end in ranges
            ]
            received = sum(future.result() for future in futures)
        if received != total or path.stat().st_size != total:
            raise ParallelDownloadError(
                f"Parallel download is incomplete: {received} of {total} bytes"
            )
        return len(ranges), total
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _convert_parallel_audio(raw_path, final_path):
    final_path.unlink(missing_ok=True)
    if raw_path.suffixes[-2:-1] == [".mp3"] or raw_path.name.casefold().endswith(
        ".mp3.parallel.part"
    ):
        raw_path.replace(final_path)
        return
    creation_flags = 0x08000000 if os.name == "nt" else 0
    result = subprocess.run(
        [
            str(FFMPEG_PATH),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(raw_path),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            str(final_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
        creationflags=creation_flags,
    )
    raw_path.unlink(missing_ok=True)
    if result.returncode != 0 or not final_path.is_file():
        final_path.unlink(missing_ok=True)
        message = result.stderr.decode("utf-8", "ignore").strip()
        raise ParallelDownloadError(message or "FFmpeg audio conversion failed")


class BackgroundDownloader(QThread):
    finished_signal = Signal(bool, str)
    progress_signal = Signal(int, str)

    def __init__(self, query_or_url, destination, parent=None):
        super().__init__(parent)
        self.query_or_url = str(query_or_url)
        self.destination = Path(destination)
        self.last_downloaded_path = None
        self.download_mode = ""
        self.parallel_connections = 0
        self.download_mib_per_second = 0.0

    def run(self):
        self.progress_signal.emit(0, "Waiting for the download queue...")
        with _DOWNLOAD_LOCK:
            self.progress_signal.emit(0, "Preparing download...")
            self._download()

    def _progress_hook(self, data):
        status = str(data.get("status") or "")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
                or 0
            )
            percent = min(95, round(downloaded * 100 / total)) if total else 0
            self.progress_signal.emit(percent, "Downloading track...")
        elif status == "finished":
            self.progress_signal.emit(96, "Converting audio...")

    def _parallel_progress(self, received, total):
        percent = min(92, round(received * 92 / total)) if total else 0
        self.progress_signal.emit(percent, "Downloading track...")

    def _download(self):
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
                "buffersize": NETWORK_BUFFER_SIZE,
                "http_chunk_size": NETWORK_BUFFER_SIZE,
                "concurrent_fragment_downloads": HTTP_POOL_SIZE,
                "socket_timeout": 20,
                "retries": 4,
                "fragment_retries": 4,
                "http_headers": {"Connection": "keep-alive"},
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
                "progress_hooks": [self._progress_hook],
            }
            aria2c = shutil.which("aria2c")
            if aria2c:
                options.update({
                    "external_downloader": {"default": aria2c},
                    "external_downloader_args": {
                        "aria2c": [
                            "-x8",
                            "-s8",
                            "-k1M",
                            "--file-allocation=none",
                            "--summary-interval=0",
                        ]
                    },
                })
            if os.name == "nt":
                options["windows_creation_flags"] = 0x08000000

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(target, download=False)
                if info and info.get("entries"):
                    info = next(
                        (entry for entry in info["entries"] if entry), info
                    )
                audio_format = _direct_http_audio_format(info or {})
                if audio_format:
                    selected_info = {**(info or {}), **audio_format}
                    raw_path = Path(ydl.prepare_filename(selected_info))
                    final_path = raw_path.with_suffix(".mp3")
                    temporary = raw_path.with_suffix(
                        raw_path.suffix + ".parallel.part"
                    )
                    headers = _audio_request_headers(
                        info or {}, audio_format
                    )
                    try:
                        started_at = time.perf_counter()
                        connections, total = _parallel_http_download(
                            str(audio_format["url"]),
                            temporary,
                            headers,
                            self._parallel_progress,
                        )
                        elapsed = max(0.001, time.perf_counter() - started_at)
                        self.download_mib_per_second = (
                            total / (1024 * 1024) / elapsed
                        )
                        _convert_parallel_audio(temporary, final_path)
                        self.last_downloaded_path = final_path
                        self.download_mode = f"parallel-range-{connections}"
                        self.parallel_connections = connections
                        print(
                            "[SoundCloud Download] "
                            f"{connections} parallel connections, "
                            f"{total / (1024 * 1024):.1f} MiB at "
                            f"{self.download_mib_per_second:.1f} MiB/s"
                        )
                        cover = _download_bytes(
                            (info or {}).get("thumbnail") or ""
                        )
                        if cover:
                            try:
                                final_path.with_suffix(".jpg").write_bytes(
                                    cover
                                )
                            except OSError as exc:
                                print(f"[SoundCloud Cover] {exc}")
                    except Exception as exc:
                        temporary.unlink(missing_ok=True)
                        print(
                            "[SoundCloud Download] Parallel Range fallback: "
                            f"{exc}"
                        )

                if self.last_downloaded_path is None:
                    info = ydl.extract_info(target, download=True)
                    if info and info.get("entries"):
                        info = next(
                            (entry for entry in info["entries"] if entry),
                            info,
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
                    protocol = str((info or {}).get("protocol") or "")
                    fragmented = any(
                        marker in protocol
                        for marker in ("m3u8", "dash", "ism")
                    )
                    if aria2c:
                        self.download_mode = "aria2c"
                        self.parallel_connections = HTTP_POOL_SIZE
                    elif fragmented:
                        self.download_mode = "yt-dlp-fragments"
                        self.parallel_connections = HTTP_POOL_SIZE
                    else:
                        self.download_mode = "yt-dlp"
                        self.parallel_connections = 1

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
                    "download_mode": self.download_mode,
                    "parallel_connections": self.parallel_connections,
                    "download_mib_per_second": round(
                        self.download_mib_per_second, 2
                    ),
                })
                self.last_downloaded_path.with_suffix(".json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            self.progress_signal.emit(100, "Download complete")
            self.finished_signal.emit(True, str(self.last_downloaded_path))
        except Exception as exc:
            self.finished_signal.emit(False, str(exc)[:1000])