

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from config import AUDIO_EXTENSIONS, PLAYLISTS_PATH
from threads import fetch_track_metadata
from network_protocol import (
    FILE_CHUNK_SIZE, MAX_COVER_SIZE, MAX_LYRICS_SIZE, MAX_TRACK_SIZE,
    STREAM_BUFFER_AHEAD_SECONDS, STREAM_INITIAL_SEGMENTS,
    STREAM_MAX_BUFFER_SECONDS, STREAM_MIN_BUFFER_SECONDS,
    STREAM_POSITION_POLL_INTERVAL, STREAM_PREPARE_TIMEOUT,
    _audio_segment_size, _duration_seconds, _probe_duration, _safe_name,
)
from network_connection import NetworkConnectionMixin
from network_stream import NetworkStreamMixin


class NetworkTransferMixin(NetworkConnectionMixin, NetworkStreamMixin):
    def _find_local_track(self, track: dict) -> Path | None:
        view = self.player.parent() if self.player is not None else None
        if view is not None and hasattr(view, "_find_local_track"):
            try:
                path = view._find_local_track(track)
                if path and Path(path).is_file():
                    return Path(path)
            except Exception:
                pass
        filename = _safe_name(track.get("filename"), "track")
        for candidate in PLAYLISTS_PATH.glob(f"*/songs/{filename}"):
            if (
                candidate.is_file()
                and candidate.suffix.lower() in AUDIO_EXTENSIONS
            ):
                return candidate
        return None

    @staticmethod
    def _recover_source_url(
        track: dict, local_path: Path | None
    ) -> str:
        for key in (
            "source_url",
            "download_url",
            "webpage_url",
            "original_url",
        ):
            value = str(track.get(key) or "")
            if value.startswith(("http://", "https://")):
                return value
        if local_path:
            sidecar = local_path.with_suffix(".json")
            if sidecar.is_file():
                try:
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    for key in (
                        "source_url",
                        "download_url",
                        "webpage_url",
                        "original_url",
                        "url",
                    ):
                        value = str(data.get(key) or "")
                        if value.startswith(("http://", "https://")):
                            return value
                except Exception:
                    pass
        return ""

    async def _upload_local_track(
        self, path: Path, track: dict, queue: list[dict], index: int
    ):
        transfer_id = ""
        context = None
        try:
            size = path.stat().st_size
            if size <= 0 or size > MAX_TRACK_SIZE:
                raise RuntimeError("Track file size is invalid")
            transfer_id = uuid.uuid4().hex
            track = dict(track)
            track["filename"] = path.name
            self.connection_state_changed.emit(
                "Preparing title, artwork, lyrics and first audio segment..."
            )
            metadata = await asyncio.to_thread(fetch_track_metadata, path)
            lyrics = str(metadata.get("lyrics") or "")
            if len(lyrics.encode("utf-8")) > MAX_LYRICS_SIZE:
                lyrics = lyrics.encode("utf-8")[:MAX_LYRICS_SIZE].decode(
                    "utf-8", "ignore"
                )
            duration = metadata.get("duration") or track.get("duration") or ""
            if not _duration_seconds(duration):
                duration = await asyncio.to_thread(_probe_duration, path) or ""
            track.update({
                "title": metadata.get("title") or track.get("title") or path.stem,
                "artist": metadata.get("artist") or track.get("artist") or "Unknown Artist",
                "lyrics": lyrics,
                "cover_url": metadata.get("cover_url") or track.get("cover_url") or "",
                "duration": duration,
                "genius_url": metadata.get("genius_url") or "",
                "stream_id": transfer_id,
            })
            queue = [dict(row) for row in queue]
            queue[index] = dict(track)
            segment_size, segment_seconds = _audio_segment_size(
                size, track.get("duration")
            )
            self._outgoing_metadata[transfer_id] = {
                **track,
                "cover_bytes": metadata.get("cover_bytes"),
            }
            cover = bytes(metadata.get("cover_bytes") or b"")
            if len(cover) > MAX_COVER_SIZE:
                cover = b""
            context = {
                "transfer_id": transfer_id,
                "path": path,
                "size": size,
                "track": track,
                "queue": queue,
                "index": index,
                "segment_size": segment_size,
                "segment_seconds": segment_seconds,
                "cover": cover,
                "cover_offset": 0,
                "offset": 0,
                "resume_token": "",
                "cancelled": False,
                "suppress_abort": False,
            }
            self._outgoing_transfers[transfer_id] = context
            self._active_upload_id = transfer_id
            self.connection_state_changed.emit(
                "Sending metadata and the first audio segment..."
            )
            await self._send_frame_locked(
                {
                    "type": "upload_begin",
                    "transfer_id": transfer_id,
                    "size": size,
                    "track": track,
                    "queue": queue,
                    "index": index,
                    "segment_size": segment_size,
                    "segment_seconds": segment_seconds,
                }
            )
            for offset in range(0, len(cover), FILE_CHUNK_SIZE):
                cover_chunk = cover[offset : offset + FILE_CHUNK_SIZE]
                await self._send_frame_locked(
                    {
                        "type": "upload_cover",
                        "transfer_id": transfer_id,
                        "offset": offset,
                        "total": len(cover),
                    },
                    cover_chunk,
                )
                context["cover_offset"] = offset + len(cover_chunk)
            await self._send_upload_context(context, 0)
        except asyncio.CancelledError:
            if context is None or not context.get("suppress_abort"):
                await self._abort_outgoing_transfer(transfer_id)
        except Exception as exc:
            connection_lost = (
                not self.is_connected or isinstance(exc, ConnectionError)
            )
            if (
                context is not None
                and self._auto_reconnect
                and connection_lost
            ):
                context["offset"] = max(0, int(context.get("offset") or 0))
                self.connection_state_changed.emit(
                    "Track upload paused; it will resume after reconnect."
                )
            else:
                await self._abort_outgoing_transfer(transfer_id)
                self.error_occurred.emit(f"Track transfer failed: {exc}")

    async def _send_upload_context(self, context: dict, start_offset: int):
        transfer_id = str(context["transfer_id"])
        path = Path(context["path"])
        size = int(context["size"])
        segment_size = int(context["segment_size"])
        segment_seconds = float(context["segment_seconds"])
        track = context["track"]
        offset = max(0, min(int(start_offset), size))
        segment_index = offset // max(1, segment_size)
        context["offset"] = offset
        with path.open("rb") as source:
            source.seek(offset)
            while offset < size:
                if (
                    context.get("cancelled")
                    or self._outgoing_transfers.get(transfer_id) is not context
                ):
                    raise asyncio.CancelledError
                if segment_index >= STREAM_INITIAL_SEGMENTS:
                    duration_seconds = _duration_seconds(track.get("duration"))
                    sent_seconds = (
                        float(duration_seconds) * offset / size
                        if duration_seconds
                        else segment_index * segment_seconds
                    )
                    await self._wait_for_stream_window(
                        transfer_id, sent_seconds, segment_seconds
                    )
                chunk = await asyncio.to_thread(source.read, segment_size)
                if not chunk:
                    break
                await self._send_frame_locked(
                    {
                        "type": "upload_chunk",
                        "transfer_id": transfer_id,
                        "offset": offset,
                        "segment_index": segment_index,
                    },
                    chunk,
                )
                offset += len(chunk)
                segment_index += 1
                context["offset"] = offset
                if segment_index == 1:
                    self.connection_state_changed.emit(
                        "Playing from RAM while the rest downloads..."
                    )
        await self._send_frame_locked(
            {"type": "upload_end", "transfer_id": transfer_id}
        )
        self._outgoing_transfers.pop(transfer_id, None)
        self._stream_targets.pop(transfer_id, None)
        if self._active_upload_id == transfer_id:
            self._active_upload_id = ""
        self.connection_state_changed.emit(
            "Track buffered in RAM; saving to disk in background..."
        )

    async def _abort_outgoing_transfer(self, transfer_id: str):
        transfer_id = str(transfer_id or "")
        context = self._outgoing_transfers.pop(transfer_id, None)
        if context is not None:
            context["cancelled"] = True
        self._stream_targets.pop(transfer_id, None)
        self._outgoing_metadata.pop(transfer_id, None)
        if self._active_upload_id == transfer_id:
            self._active_upload_id = ""
        if transfer_id and self.is_connected:
            try:
                await asyncio.shield(self._send_frame_locked({
                    "type": "upload_abort",
                    "transfer_id": transfer_id,
                }))
            except Exception:
                pass

    async def _wait_for_stream_window(
        self,
        transfer_id: str,
        sent_seconds: float,
        segment_seconds: float,
    ):

        prepare_deadline = time.monotonic() + STREAM_PREPARE_TIMEOUT
        playback_started = False
        while self.is_connected:
            active_id = self._committed_stream_id
            if active_id == transfer_id:
                playback_started = True
                position_seconds = (
                    max(0, int(self.player.position())) / 1000
                    if self.player is not None
                    else sent_seconds
                )
                target_seconds = max(
                    STREAM_MIN_BUFFER_SECONDS,
                    min(
                        STREAM_MAX_BUFFER_SECONDS,
                        float(
                            self._stream_targets.get(
                                transfer_id, STREAM_BUFFER_AHEAD_SECONDS
                            )
                        ),
                    ),
                )
                maximum_before_next = max(
                    segment_seconds,
                    target_seconds - segment_seconds,
                )
                if sent_seconds - position_seconds <= maximum_before_next:
                    return
            elif playback_started:
                raise asyncio.CancelledError
            elif time.monotonic() >= prepare_deadline:
                raise RuntimeError("Timed out waiting for room playback")
            await asyncio.sleep(STREAM_POSITION_POLL_INTERVAL)
        raise ConnectionError("Room disconnected during track streaming")

    def _upload_requested_track(self, packet: dict):
        track = packet.get("track") or {}
        queue = packet.get("queue") or []
        if (
            not isinstance(track, dict)
            or not isinstance(queue, list)
            or not queue
        ):
            return
        index = max(
            0, min(int(packet.get("index") or 0), len(queue) - 1)
        )
        path = self._find_local_track(track)
        if not path:
            self._send_packet({
                "type": "upload_unavailable",
                "message": "The queue owner no longer has this track",
            })
            return
        self._discard_outgoing_transfers()
        if self._upload_task and not self._upload_task.done():
            self._upload_task.cancel()
        self._upload_task = asyncio.create_task(
            self._upload_local_track(path, track, queue, index)
        )

    def _discard_outgoing_transfers(self):
        for transfer_id, context in list(self._outgoing_transfers.items()):
            context["cancelled"] = True
            if self.is_connected:
                self._send_packet({
                    "type": "upload_abort",
                    "transfer_id": transfer_id,
                })
            self._outgoing_metadata.pop(transfer_id, None)
        self._outgoing_transfers.clear()
        self._stream_targets.clear()
        self._active_upload_id = ""

    @staticmethod
    def _same_local_path(first, second):
        try:
            return Path(first).resolve() == Path(second).resolve()
        except OSError:
            return Path(first) == Path(second)

    @staticmethod
    def _inside_local_folder(path, folder):
        try:
            return Path(path).resolve().parent == Path(folder).resolve()
        except OSError:
            return Path(path).parent == Path(folder)

    def _release_local_sources(self, matches):
        released = False
        for transfer_id, context in list(self._outgoing_transfers.items()):
            if not matches(context.get("path")):
                continue
            released = True
            context["cancelled"] = True
            self._outgoing_transfers.pop(transfer_id, None)
            self._outgoing_metadata.pop(transfer_id, None)
            self._stream_targets.pop(transfer_id, None)
            if self.is_connected:
                self._send_packet({
                    "type": "upload_abort",
                    "transfer_id": transfer_id,
                })
            if self._active_upload_id == transfer_id:
                self._active_upload_id = ""
        if released and self._upload_task and not self._upload_task.done():
            self._upload_task.cancel()
        return released

    def release_local_path(self, path):
        target = Path(path)
        return self._release_local_sources(
            lambda source: source is not None
            and self._same_local_path(source, target)
        )

    def release_local_folder(self, folder):
        target = Path(folder)
        return self._release_local_sources(
            lambda source: source is not None
            and self._inside_local_folder(source, target)
        )

    def _resume_requested_upload(self, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        context = self._outgoing_transfers.get(transfer_id)
        if context is None or context.get("cancelled"):
            self._send_packet({
                "type": "upload_unavailable",
                "message": "The interrupted track is no longer available",
            })
            return
        offset = max(0, int(packet.get("offset") or 0))
        cover_offset = max(0, int(packet.get("cover_offset") or 0))
        if offset > int(context.get("size") or 0):
            return
        if cover_offset > len(context.get("cover") or b""):
            return
        context["resume_token"] = str(packet.get("resume_token") or "")
        previous = (
            self._upload_task
            if self._upload_task and not self._upload_task.done()
            else None
        )
        context["suppress_abort"] = True
        self._active_upload_id = transfer_id
        self._upload_task = asyncio.create_task(
            self._restart_upload_context(
                context, offset, cover_offset, previous
            )
        )

    async def _restart_upload_context(
        self,
        context: dict,
        offset: int,
        cover_offset: int,
        previous: asyncio.Task | None,
    ):
        if previous is not None:
            previous.cancel()
            try:
                await previous
            except (asyncio.CancelledError, Exception):
                pass
        if (
            context.get("cancelled")
            or self._outgoing_transfers.get(context["transfer_id"])
            is not context
        ):
            return
        context["suppress_abort"] = False
        await self._resume_upload_context(context, offset, cover_offset)

    async def _resume_upload_context(
        self, context: dict, offset: int, cover_offset: int
    ):
        transfer_id = str(context["transfer_id"])
        try:
            await self._send_frame_locked({
                "type": "upload_resume",
                "transfer_id": transfer_id,
                "offset": offset,
                "cover_offset": cover_offset,
                "resume_token": context.get("resume_token") or "",
            })
            cover = bytes(context.get("cover") or b"")
            for current in range(
                cover_offset, len(cover), FILE_CHUNK_SIZE
            ):
                chunk = cover[current : current + FILE_CHUNK_SIZE]
                await self._send_frame_locked(
                    {
                        "type": "upload_cover",
                        "transfer_id": transfer_id,
                        "offset": current,
                        "total": len(cover),
                    },
                    chunk,
                )
                context["cover_offset"] = current + len(chunk)
            self.connection_state_changed.emit(
                f"Resuming track upload from {offset * 100 // max(1, int(context['size']))}%..."
            )
            await self._send_upload_context(context, offset)
        except asyncio.CancelledError:
            if context.get("suppress_abort"):
                return
            if context.get("cancelled"):
                await self._abort_outgoing_transfer(transfer_id)
        except Exception as exc:
            connection_lost = (
                not self.is_connected or isinstance(exc, ConnectionError)
            )
            if (
                self._auto_reconnect
                and connection_lost
                and not context.get("cancelled")
            ):
                context["offset"] = offset
                self.connection_state_changed.emit(
                    "Track upload paused again; waiting for reconnect."
                )
            else:
                await self._abort_outgoing_transfer(transfer_id)
                self.error_occurred.emit(
                    f"Track resume failed: {exc}"
                )

    def _cancel_requested_upload(self, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        context = self._outgoing_transfers.pop(transfer_id, None)
        if context is not None:
            context["cancelled"] = True
        self._stream_targets.pop(transfer_id, None)
        self._outgoing_metadata.pop(transfer_id, None)
        if self._active_upload_id == transfer_id:
            self._active_upload_id = ""
            if self._upload_task and not self._upload_task.done():
                self._upload_task.cancel()
