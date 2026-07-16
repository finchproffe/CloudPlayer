

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import threading
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Final

from config import FFMPEG_PATH

PING_INTERVAL: Final[float] = 2.5
STATE_INTERVAL: Final[float] = 1.0
COUNTRY_LOOKUP_TIMEOUT: Final[float] = 4.0
COUNTRY_RESPONSE_LIMIT: Final[int] = 64 * 1024
COUNTRY_LOOKUP_USER_AGENT: Final[str] = "CloudPlayer/1.1"
COUNTRY_SELF_LOOKUP_URLS: Final[tuple[str, ...]] = (
    "https://api.country.is/",
    "https://ipapi.co/json/",
)
COUNTRY_IP_LOOKUP_URLS: Final[tuple[str, ...]] = (
    "https://api.country.is/{ip}",
    "https://ipapi.co/{ip}/json/",
)
START_DELAY: Final[float] = 0.35
DRIFT_LIMIT_MS: Final[int] = 220
FILE_CHUNK_SIZE: Final[int] = 2 * 1024 * 1024
SOCKET_BUFFER_SIZE: Final[int] = 2 * 1024 * 1024
HTTP_STREAM_CHUNK_SIZE: Final[int] = 1024 * 1024
DEFAULT_SEGMENT_SIZE: Final[int] = 256 * 1024
TARGET_SEGMENT_SECONDS: Final[float] = 8.0
MIN_SEGMENT_SECONDS: Final[float] = 5.0
MAX_SEGMENT_SECONDS: Final[float] = 15.0
MAX_COVER_SIZE: Final[int] = 16 * 1024 * 1024
MAX_LYRICS_SIZE: Final[int] = 512 * 1024
STREAM_INITIAL_SEGMENTS: Final[int] = 2
STREAM_BUFFER_AHEAD_SECONDS: Final[float] = 24.0
STREAM_MIN_BUFFER_SECONDS: Final[float] = 16.0
STREAM_MAX_BUFFER_SECONDS: Final[float] = 48.0
STREAM_REPORT_INTERVAL: Final[float] = 0.5
STREAM_POSITION_POLL_INTERVAL: Final[float] = 0.2
STREAM_PREPARE_TIMEOUT: Final[float] = 60.0
RECONNECT_INITIAL_DELAY: Final[float] = 0.5
RECONNECT_MAX_DELAY: Final[float] = 15.0
REPLAY_DRAIN_BYTES: Final[int] = 4 * 1024 * 1024
MAX_PEER_WRITE_BUFFER: Final[int] = 4 * SOCKET_BUFFER_SIZE
MAX_TRACK_SIZE: Final[int] = 500 * 1024 * 1024
MAX_HEADER_SIZE: Final[int] = 1024 * 1024
FRAME_PREFIX_SIZE: Final[int] = 4
_PLAYLIST_CACHE_LOCK = threading.Lock()


def _normalize_room_host(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Host is empty")
    unwrapped = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    try:
        return str(ipaddress.ip_address(unwrapped))
    except ValueError:
        pass
    parsed = urlsplit(text if "://" in text else f"//{text}")
    host = parsed.hostname
    if not host:
        raise ValueError("Enter a valid host name or IP address")
    host = host.strip().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Enter a valid host name or IP address") from exc


def _duration_seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds if seconds > 0 else None


def _audio_segment_size(size: int, duration) -> tuple[int, float]:
    seconds = _duration_seconds(duration)
    if seconds:
        bytes_per_second = max(1.0, size / seconds)
        chunk_size = round(bytes_per_second * TARGET_SEGMENT_SECONDS)
        chunk_size = max(32 * 1024, min(FILE_CHUNK_SIZE, chunk_size))
        estimated = chunk_size / bytes_per_second
        if estimated < MIN_SEGMENT_SECONDS:
            chunk_size = min(
                FILE_CHUNK_SIZE, round(bytes_per_second * MIN_SEGMENT_SECONDS)
            )
        elif estimated > MAX_SEGMENT_SECONDS:
            chunk_size = max(
                32 * 1024, round(bytes_per_second * MAX_SEGMENT_SECONDS)
            )
        return chunk_size, chunk_size / bytes_per_second
    return DEFAULT_SEGMENT_SIZE, TARGET_SEGMENT_SECONDS


def _probe_duration(path: Path) -> float | None:
    if not FFMPEG_PATH.is_file():
        return None
    creation_flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(FFMPEG_PATH), "-hide_banner", "-i", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
            creationflags=creation_flags,
        )
        match = re.search(
            rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr
        )
        if match:
            return (
                int(match.group(1)) * 3600
                + int(match.group(2)) * 60
                + float(match.group(3))
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def _tune_socket(writer) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        for option in (socket.SO_SNDBUF, socket.SO_RCVBUF):
            try:
                sock.setsockopt(socket.SOL_SOCKET, option, SOCKET_BUFFER_SIZE)
            except (OSError, AttributeError):
                pass
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except (OSError, AttributeError):
            pass
    transport = getattr(writer, "transport", None)
    if transport is not None:
        try:
            transport.set_write_buffer_limits(
                high=4 * SOCKET_BUFFER_SIZE, low=SOCKET_BUFFER_SIZE
            )
        except (AttributeError, NotImplementedError):
            pass


def _cover_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return ".tiff"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _normalize_country_code(value) -> str:
    code = str(value or "").strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        return code
    return "??"


def _normalize_public_ip(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    text = text.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return ""
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.compressed if address.is_global else ""


def _detect_public_location(ip="") -> tuple[str, str]:

    expected_ip = _normalize_public_ip(ip)
    if ip and not expected_ip:
        return "", "??"
    urls = COUNTRY_IP_LOOKUP_URLS if expected_ip else COUNTRY_SELF_LOOKUP_URLS
    for template in urls:
        url = template.format(ip=expected_ip)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": COUNTRY_LOOKUP_USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=COUNTRY_LOOKUP_TIMEOUT
            ) as response:
                raw = response.read(COUNTRY_RESPONSE_LIMIT + 1)
            if len(raw) > COUNTRY_RESPONSE_LIMIT:
                continue
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                continue
            resolved_ip = _normalize_public_ip(
                payload.get("ip") or expected_ip
            )
            if expected_ip and resolved_ip != expected_ip:
                continue
            country = _normalize_country_code(
                payload.get("country_code") or payload.get("country")
            )
            if resolved_ip and country != "??":
                return resolved_ip, country
        except Exception:
            continue
    return expected_ip, "??"


def _detect_country_for_ip(ip) -> str:
    return _detect_public_location(ip)[1]


def _detect_country() -> str:

    return _detect_public_location()[1]


def _safe_name(value: str, fallback: str) -> str:
    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")
    ).strip(" .")
    return value[:160] or fallback


def _encode_frame(packet: dict, payload: bytes = b"") -> bytes:
    header = dict(packet)
    header["payload_size"] = len(payload)
    encoded = json.dumps(
        header, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if not encoded or len(encoded) > MAX_HEADER_SIZE:
        raise ValueError("Frame header is too large")
    return struct.pack("!I", len(encoded)) + encoded + payload


async def _read_frame(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    prefix = await reader.readexactly(FRAME_PREFIX_SIZE)
    header_size = struct.unpack("!I", prefix)[0]
    if header_size <= 0 or header_size > MAX_HEADER_SIZE:
        raise ValueError("Invalid frame header size")
    raw_header = await reader.readexactly(header_size)
    packet = json.loads(raw_header.decode("utf-8"))
    if not isinstance(packet, dict):
        raise ValueError("Invalid frame header")
    payload_size = int(packet.pop("payload_size", 0) or 0)
    if payload_size < 0 or payload_size > FILE_CHUNK_SIZE:
        raise ValueError("Invalid frame payload size")
    payload = await reader.readexactly(payload_size) if payload_size else b""
    return packet, payload


class _Member:
    __slots__ = ("writer", "id", "name", "country", "ping_ms")

    def __init__(self, writer, member_id: str, name: str, country: str):
        self.writer = writer
        self.id = member_id
        self.name = name
        self.country = country
        self.ping_ms: int | None = None

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "ping": self.ping_ms,
            "is_self": False,
        }
