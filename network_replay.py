

from __future__ import annotations

import asyncio
import time
import uuid

from network_protocol import (
    DEFAULT_SEGMENT_SIZE, FILE_CHUNK_SIZE, REPLAY_DRAIN_BYTES,
)


class NetworkReplayMixin:
    def _send_room_snapshot(self, member: _Member, resume_streams=None):
        writer = member.writer
        self._write_packet(writer, {
            "type": "clock_hint",
            "server_time": time.time(),
        })
        self._write_packet(
            writer, {"type": "repeat", "enabled": self._repeat}
        )
        owner_upload = next(
            (
                upload
                for upload in self._server_uploads.values()
                if upload.get("member_id") == member.id
                and not upload.get("complete")
            ),
            None,
        )
        if self._pending_request or self._room_track or owner_upload:
            self._write_packet(writer, {"type": "snapshot_wait"})
        resume_map = {}
        if isinstance(resume_streams, list):
            for row in resume_streams:
                if not isinstance(row, dict):
                    continue
                transfer_id = str(row.get("transfer_id") or "")
                if transfer_id:
                    resume_map[transfer_id] = row

        if owner_upload is not None:
            owner_transfer_id = str(
                (owner_upload.get("track") or {}).get("stream_id") or ""
            )
            self._write_packet(writer, {
                "type": "resume_upload",
                "transfer_id": owner_transfer_id,
                "offset": int(owner_upload.get("received") or 0),
                "cover_offset": int(
                    owner_upload.get("cover_received") or 0
                ),
                "resume_token": owner_upload.get("resume_token") or "",
            })

        transfer_id = str((self._room_track or {}).get("stream_id") or "")
        upload = self._server_uploads.get(transfer_id)
        if upload is not None and upload.get("member_id") == member.id:
            self._send_playback_snapshot(writer)
            return
        if upload is not None:
            resume = resume_map.get(transfer_id) or {}
            if (
                bool(resume.get("complete"))
                and int(resume.get("size") or 0) == int(upload["size"])
                and int(resume.get("received") or 0) == int(upload["size"])
            ):
                self._send_playback_snapshot(writer)
                return
            self._start_transfer_replay(
                member, upload, resume
            )
            return
        self._send_playback_snapshot(writer)

    def _start_transfer_replay(
        self, member: _Member, upload: dict, resume: dict
    ):
        writer = member.writer
        previous = self._replay_tasks.pop(writer, None)
        if previous is not None:
            previous.cancel()
        upload["catching_up"].add(writer)
        task = asyncio.create_task(
            self._replay_transfer(member, upload, resume)
        )
        self._replay_tasks[writer] = task

        def clear(done):
            if self._replay_tasks.get(writer) is done:
                self._replay_tasks.pop(writer, None)

        task.add_done_callback(clear)

    async def _replay_transfer(
        self, member: _Member, upload: dict, resume: dict
    ):
        writer = member.writer
        transfer_id = str((upload.get("track") or {}).get("stream_id") or "")
        try:
            requested = max(0, int(resume.get("received") or 0))
            requested_cover = max(
                0, int(resume.get("cover_received") or 0)
            )
            if (
                int(resume.get("size") or 0) != int(upload["size"])
                or requested > int(upload["received"])
                or requested_cover > int(upload["cover_received"])
            ):
                requested = 0
                requested_cover = 0
            self._write_packet(writer, {
                "type": "file_begin",
                "transfer_id": transfer_id,
                "size": int(upload["size"]),
                "track": upload["track"],
                "segment_size": int(upload.get("segment_size") or 0),
                "segment_seconds": float(upload.get("segment_seconds") or 0),
                "resume_offset": requested,
                "cover_offset": requested_cover,
            })

            cover = upload.get("cover") or bytearray()
            total_cover = int(upload.get("cover_total") or 0)
            for offset in range(
                requested_cover, len(cover), FILE_CHUNK_SIZE
            ):
                self._write_packet(
                    writer,
                    {
                        "type": "file_cover",
                        "transfer_id": transfer_id,
                        "offset": offset,
                        "total": total_cover,
                    },
                    bytes(cover[offset : offset + FILE_CHUNK_SIZE]),
                )
                await writer.drain()

            cursor = requested
            segment_size = max(
                32 * 1024,
                min(
                    FILE_CHUNK_SIZE,
                    int(upload.get("segment_size") or DEFAULT_SEGMENT_SIZE),
                ),
            )
            pending_drain = 0
            while True:
                available = int(upload.get("received") or 0)
                while cursor < available:
                    stop = min(available, cursor + segment_size)
                    view = upload["buffer"].getbuffer()
                    try:
                        chunk = bytes(view[cursor:stop])
                    finally:
                        view.release()
                    self._write_packet(
                        writer,
                        {
                            "type": "file_chunk",
                            "transfer_id": transfer_id,
                            "offset": cursor,
                            "segment_index": cursor // segment_size,
                            "segment_seconds": float(
                                upload.get("segment_seconds") or 0
                            ),
                        },
                        chunk,
                    )
                    cursor = stop
                    pending_drain += len(chunk)
                    if pending_drain >= REPLAY_DRAIN_BYTES:
                        await writer.drain()
                        pending_drain = 0
                        break
                if cursor < int(upload.get("received") or 0):
                    continue
                if pending_drain:
                    await writer.drain()
                    pending_drain = 0
                    continue
                if upload.get("complete"):
                    self._write_packet(writer, {
                        "type": "file_end",
                        "transfer_id": transfer_id,
                    })
                    await writer.drain()
                upload["catching_up"].discard(writer)
                break
            if (
                self._members.get(writer) is member
                and self._server_uploads.get(transfer_id) is upload
            ):
                self._send_playback_snapshot(writer)
        except asyncio.CancelledError:
            pass
        except Exception:
            try:
                writer.close()
            except Exception:
                pass
        finally:
            upload.get("catching_up", set()).discard(writer)

    def _send_playback_snapshot(self, writer):
        if writer not in self._members:
            return
        if self._pending_request:
            self._write_packet(writer, self._pending_request)
            return
        if not self._room_track:
            return
        request_id = self._playback_request_id or uuid.uuid4().hex
        self._playback_request_id = request_id
        self._write_packet(writer, {
            "type": "prepare_track",
            "request_id": request_id,
            "track": self._room_track,
            "queue_index": self._room_queue_index,
            "owner_id": self._queue_owner,
            "snapshot": True,
        })
        now = time.time()
        self._write_packet(writer, {
            "type": "commit_track",
            "request_id": request_id,
            "track": self._room_track,
            "queue_index": self._room_queue_index,
            "owner_id": self._queue_owner,
            "position": self._current_position(),
            "start_at": now,
            "repeat": self._repeat,
            "playing": self._playing,
            "snapshot": True,
        })
