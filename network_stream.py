

from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import re
import time
import urllib.parse
from pathlib import Path

from config import AUDIO_EXTENSIONS, PLAYLISTS_PATH
from threads import cache_lyrics
from network_protocol import (
    HTTP_STREAM_CHUNK_SIZE, MAX_COVER_SIZE, MAX_TRACK_SIZE,
    STREAM_BUFFER_AHEAD_SECONDS, STREAM_MAX_BUFFER_SECONDS,
    STREAM_MIN_BUFFER_SECONDS, STREAM_REPORT_INTERVAL,
    TARGET_SEGMENT_SECONDS, _PLAYLIST_CACHE_LOCK, _cover_suffix,
    _duration_seconds, _safe_name,
)


class NetworkStreamMixin:
    def _receive_file_begin(self, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        track = packet.get("track") or {}
        size = int(packet.get("size") or 0)
        resume_offset = max(0, int(packet.get("resume_offset") or 0))
        cover_offset = max(0, int(packet.get("cover_offset") or 0))
        if (
            not transfer_id
            or not isinstance(track, dict)
            or size <= 0
            or size > MAX_TRACK_SIZE
            or resume_offset > size
        ):
            return
        for stale_id in list(self._incoming_files):
            if stale_id != transfer_id:
                self._abort_incoming_file(stale_id)
        existing = self._incoming_files.get(transfer_id)
        if existing is not None:
            can_resume = (
                int(existing.get("size") or 0) == size
                and int(existing.get("received") or 0) == resume_offset
                and len(existing.get("cover") or b"") == cover_offset
            )
            if can_resume:
                existing["track"] = dict(track)
                existing["segment_seconds"] = float(
                    packet.get("segment_seconds") or 0
                )
                existing["event"].set()
                self.stream_buffer_progress_changed.emit(
                    resume_offset, size
                )
                self.connection_state_changed.emit(
                    f"Resuming current track from {resume_offset * 100 // size}%..."
                )
                return
            self._abort_incoming_file(transfer_id)
        playlist = _safe_name(
            track.get("playlist"), "Listen Together"
        )
        filename = _safe_name(track.get("filename"), "track.mp3")
        if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
            filename += ".mp3"
        folder = PLAYLISTS_PATH / playlist / "songs"
        folder.mkdir(parents=True, exist_ok=True)
        final_path = folder / filename
        temporary = final_path.with_suffix(
            final_path.suffix + ".roompart"
        )
        temporary.unlink(missing_ok=True)
        state = {
            "temporary": temporary,
            "final": final_path,
            "received": 0,
            "size": size,
            "track": track,
            "buffer": io.BytesIO(),
            "cover": bytearray(),
            "cover_total": 0,
            "segment_seconds": float(packet.get("segment_seconds") or 0),
            "event": asyncio.Event(),
            "complete": False,
            "persisted": False,
            "error": "",
            "report_time": time.monotonic(),
            "report_received": 0,
            "throughput_bps": 0.0,
        }
        self._incoming_files[transfer_id] = state
        self._streams[transfer_id] = state
        self.stream_buffer_progress_changed.emit(0, size)
        self.connection_state_changed.emit(
            "Receiving metadata and the first audio segment..."
        )

    def _receive_file_cover(self, packet: dict, payload: bytes):
        transfer_id = str(packet.get("transfer_id") or "")
        state = self._incoming_files.get(transfer_id)
        if not state:
            return
        try:
            offset = int(packet.get("offset") or 0)
            total = int(packet.get("total") or 0)
            if (
                not payload
                or total <= 0
                or total > MAX_COVER_SIZE
                or offset != len(state["cover"])
                or offset + len(payload) > total
            ):
                raise RuntimeError("Invalid cover segment")
            state["cover"].extend(payload)
            state["cover_total"] = total
        except Exception as exc:
            self.error_occurred.emit(f"Cover receive failed: {exc}")

    def _receive_file_chunk(self, packet: dict, payload: bytes):
        transfer_id = str(packet.get("transfer_id") or "")
        state = self._incoming_files.get(transfer_id)
        if not state:
            return
        try:
            if not payload:
                raise RuntimeError("Empty track chunk")
            offset = int(packet.get("offset") or 0)
            received = int(state["received"])
            if offset < received:
                overlap = min(len(payload), received - offset)
                if offset + len(payload) <= received:
                    self._report_stream_buffer(transfer_id, state)
                    return
                payload = payload[overlap:]
                offset += overlap
            if offset != state["received"]:
                raise RuntimeError("Track chunks arrived out of order")
            if state["received"] + len(payload) > state["size"]:
                raise RuntimeError(
                    "Received track is larger than announced"
                )
            state["buffer"].write(payload)
            state["received"] += len(payload)
            state["event"].set()
            self._report_stream_buffer(transfer_id, state)
            if state["received"] == len(payload):
                seconds = state["segment_seconds"]
                suffix = f" ({seconds:.0f}s ready)" if seconds else ""
                self.connection_state_changed.emit(
                    f"First audio segment buffered{suffix}; starting playback..."
                )
        except Exception as exc:
            self._abort_incoming_file(transfer_id)
            self.error_occurred.emit(f"Track receive failed: {exc}")

    def _report_stream_buffer(self, transfer_id: str, state: dict):
        received = int(state.get("received") or 0)
        size = int(state.get("size") or 0)
        self.stream_buffer_progress_changed.emit(received, size)
        now = time.monotonic()
        elapsed = now - float(state.get("report_time") or now)
        previous = int(state.get("report_received") or 0)
        if (
            previous > 0
            and elapsed < STREAM_REPORT_INTERVAL
            and received < size
        ):
            return
        instant = max(0.0, received - previous) / max(0.001, elapsed)
        old_speed = float(state.get("throughput_bps") or 0.0)
        throughput = instant if old_speed <= 0 else old_speed * 0.55 + instant * 0.45
        state["throughput_bps"] = throughput
        state["report_time"] = now
        state["report_received"] = received

        duration = _duration_seconds((state.get("track") or {}).get("duration"))
        segment_seconds = max(
            1.0, float(state.get("segment_seconds") or TARGET_SEGMENT_SECONDS)
        )
        target = STREAM_BUFFER_AHEAD_SECONDS
        buffered_seconds = 0.0
        position_seconds = 0.0
        ratio = 0.0
        if duration and size:
            bitrate = size / duration
            ratio = throughput / max(1.0, bitrate)
            buffered_seconds = duration * received / size
            if (
                self.player is not None
                and self._committed_stream_id == transfer_id
            ):
                position_seconds = max(0, int(self.player.position())) / 1000
            ahead = max(0.0, buffered_seconds - position_seconds)
            if ratio >= 7.0:
                target = STREAM_MIN_BUFFER_SECONDS
            elif ratio >= 4.0:
                target = 20.0
            elif ratio >= 2.25:
                target = 28.0
            elif ratio >= 1.35:
                target = 36.0
            else:
                target = STREAM_MAX_BUFFER_SECONDS
            if ahead < segment_seconds * 1.25 and received < size:
                target = max(target, 40.0)
        target = max(
            STREAM_MIN_BUFFER_SECONDS,
            min(STREAM_MAX_BUFFER_SECONDS, target),
        )
        self._send_packet({
            "type": "buffer_report",
            "transfer_id": transfer_id,
            "received": received,
            "buffered_seconds": buffered_seconds,
            "position_seconds": position_seconds,
            "throughput_ratio": ratio,
            "target_seconds": target,
        })

    def _receive_file_end(self, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        state = self._incoming_files.pop(transfer_id, None)
        if not state:
            return
        try:
            if state["received"] != state["size"]:
                raise RuntimeError("Track transfer is incomplete")
            state["complete"] = True
            state["event"].set()
            self.stream_buffer_progress_changed.emit(
                state["size"], state["size"]
            )
            task = asyncio.create_task(self._persist_received_stream(state))
            self._persist_tasks.add(task)
            task.add_done_callback(self._persist_tasks.discard)
            self.connection_state_changed.emit(
                "Track is in RAM; saving it to disk in the background..."
            )
        except Exception as exc:
            state["complete"] = True
            state["error"] = str(exc)
            state["event"].set()
            state["temporary"].unlink(missing_ok=True)
            self.error_occurred.emit(f"Track receive failed: {exc}")

    async def _persist_received_stream(self, state: dict):
        try:
            await asyncio.to_thread(self._write_stream_to_disk, state)
            state["persisted"] = True
            if str((self._room_track or {}).get("stream_id")) == str(
                (state.get("track") or {}).get("stream_id")
            ):
                self.connection_state_changed.emit("Track cached on disk")
        except Exception as exc:
            state["error"] = str(exc)
            self.error_occurred.emit(f"Track cache write failed: {exc}")
        finally:
            stream_id = str(
                (state.get("track") or {}).get("stream_id") or ""
            )
            active_id = str(
                (self._room_track or {}).get("stream_id") or ""
            )
            if stream_id and stream_id != active_id:
                self._streams.pop(stream_id, None)

    @staticmethod
    def _write_stream_to_disk(state: dict):
        final_path = Path(state["final"])
        temporary = Path(state["temporary"])
        final_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb", buffering=HTTP_STREAM_CHUNK_SIZE) as output:
            view = state["buffer"].getbuffer()
            try:
                for offset in range(0, len(view), HTTP_STREAM_CHUNK_SIZE):
                    output.write(
                        view[offset : offset + HTTP_STREAM_CHUNK_SIZE]
                    )
            finally:
                view.release()
        temporary.replace(final_path)

        track = dict(state.get("track") or {})
        sidecar = {
            key: track.get(key) or ""
            for key in (
                "title",
                "artist",
                "source_url",
                "cover_url",
                "duration",
                "genius_url",
            )
        }
        sidecar["title"] = sidecar["title"] or final_path.stem
        sidecar["artist"] = sidecar["artist"] or "Unknown Artist"
        sidecar_path = final_path.with_suffix(".json")
        sidecar_temp = sidecar_path.with_suffix(".json.roompart")
        sidecar_temp.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sidecar_temp.replace(sidecar_path)

        cover = bytes(state.get("cover") or b"")
        if cover:
            final_path.with_suffix(_cover_suffix(cover)).write_bytes(cover)
        cache_lyrics(sidecar["artist"], sidecar["title"], track.get("lyrics"))

        playlist = _safe_name(track.get("playlist"), "Listen Together")
        playlist_meta = PLAYLISTS_PATH / f"{playlist}.json"
        with _PLAYLIST_CACHE_LOCK:
            try:
                playlist_data = json.loads(
                    playlist_meta.read_text(encoding="utf-8")
                )
            except Exception:
                playlist_data = {}
            order = playlist_data.get("songs")
            order = list(order) if isinstance(order, list) else []
            if final_path.name not in order:
                order.append(final_path.name)
            playlist_data.update({"name": playlist, "songs": order})
            playlist_meta.write_text(
                json.dumps(playlist_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _abort_incoming_file(self, transfer_id: str):
        state = self._incoming_files.pop(transfer_id, None)
        if not state:
            return
        state["complete"] = True
        state["error"] = "Transfer aborted"
        state["event"].set()
        state["temporary"].unlink(missing_ok=True)
        self._streams.pop(transfer_id, None)
        if transfer_id == self._committed_stream_id:
            self.stream_buffer_progress_changed.emit(0, 0)

    async def _ensure_stream_server(self):
        if self._stream_server is not None:
            return
        self._stream_server = await asyncio.start_server(
            self._handle_stream_http,
            "127.0.0.1",
            0,
            limit=64 * 1024,
        )
        sockets = self._stream_server.sockets or []
        if not sockets:
            raise RuntimeError("Could not start the local audio stream")
        self._stream_port = int(sockets[0].getsockname()[1])

    async def _handle_stream_http(self, reader, writer):
        try:
            raw = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=10
            )
            if len(raw) > 64 * 1024:
                raise ValueError("HTTP header is too large")
            lines = raw.decode("iso-8859-1").split("\r\n")
            request = lines[0].split()
            if len(request) != 3 or request[0] not in {"GET", "HEAD"}:
                await self._write_http_error(writer, 405, "Method Not Allowed")
                return
            path = urllib.parse.urlsplit(request[1]).path
            parts = path.split("/")
            transfer_id = parts[2] if len(parts) >= 3 else ""
            state = self._streams.get(transfer_id)
            if not state:
                await self._write_http_error(writer, 404, "Not Found")
                return

            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().casefold()] = value.strip()
            total = int(state["size"])
            start, end = 0, total - 1
            partial = False
            match = re.match(
                r"bytes=(\d*)-(\d*)", headers.get("range", ""), re.I
            )
            if match:
                partial = True
                if not match.group(1) and match.group(2):
                    suffix_length = int(match.group(2))
                    start = max(0, total - suffix_length)
                    end = total - 1
                elif match.group(1):
                    start = int(match.group(1))
                if match.group(1) and match.group(2):
                    end = min(end, int(match.group(2)))
            if start < 0 or start >= total or end < start:
                await self._write_http_error(
                    writer, 416, "Range Not Satisfiable"
                )
                return

            mime = mimetypes.guess_type(str(state["final"]))[0]
            mime = mime or "application/octet-stream"
            status = "206 Partial Content" if partial else "200 OK"
            response_headers = [
                f"HTTP/1.1 {status}",
                f"Content-Type: {mime}",
                f"Content-Length: {end - start + 1}",
                "Accept-Ranges: bytes",
                "Cache-Control: no-store",
                "Connection: close",
            ]
            if partial:
                response_headers.append(
                    f"Content-Range: bytes {start}-{end}/{total}"
                )
            writer.write(("\r\n".join(response_headers) + "\r\n\r\n").encode())
            await writer.drain()
            if request[0] == "HEAD":
                return

            offset = start
            while offset <= end:
                available = int(state["received"])
                if offset < available:
                    stop = min(
                        available, end + 1, offset + HTTP_STREAM_CHUNK_SIZE
                    )
                    view = state["buffer"].getbuffer()
                    try:
                        writer.write(bytes(view[offset:stop]))
                    finally:
                        view.release()
                    await writer.drain()
                    offset = stop
                    continue
                if state.get("complete"):
                    break
                event = state["event"]
                event.clear()
                if offset < int(state["received"]) or state.get("complete"):
                    continue
                await event.wait()
        except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
            pass
        except Exception:
            try:
                await self._write_http_error(writer, 500, "Stream Error")
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _write_http_error(writer, status: int, reason: str):
        body = f"{status} {reason}\n".encode("utf-8")
        writer.write(
            (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()
