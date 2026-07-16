

from __future__ import annotations

import asyncio
import io
import time
import uuid

from network_protocol import (
    MAX_COVER_SIZE, MAX_TRACK_SIZE, STREAM_BUFFER_AHEAD_SECONDS,
    STREAM_MAX_BUFFER_SECONDS, STREAM_MIN_BUFFER_SECONDS,
)


class NetworkServerUploadMixin:
    def _server_upload_begin(self, member: _Member, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        size = int(packet.get("size") or 0)
        track, queue = packet.get("track"), packet.get("queue")
        if (
            not transfer_id
            or size <= 0
            or size > MAX_TRACK_SIZE
            or not isinstance(track, dict)
            or not isinstance(queue, list)
        ):
            return
        clean_queue = [
            row for row in queue if isinstance(row, dict)
        ]
        if not clean_queue:
            return
        index = max(
            0,
            min(
                int(packet.get("index", 0)),
                len(clean_queue) - 1,
            ),
        )
        track = dict(track)
        track["stream_id"] = transfer_id
        clean_queue[index] = dict(track)
        for old_id, old_upload in list(self._server_uploads.items()):
            if old_id == transfer_id:
                continue
            old_writer = old_upload.get("writer")
            if old_writer is not None:
                self._write_packet(old_writer, {
                    "type": "cancel_upload",
                    "transfer_id": old_id,
                })
            self._drop_server_upload(old_id, notify=True)
        self._server_uploads[transfer_id] = {
            "writer": member.writer,
            "member_id": member.id,
            "size": size,
            "received": 0,
            "track": track,
            "queue": clean_queue,
            "index": index,
            "prepared": False,
            "cover_received": 0,
            "cover_total": 0,
            "cover": bytearray(),
            "buffer": io.BytesIO(),
            "complete": False,
            "event": asyncio.Event(),
            "catching_up": set(),
            "reports": {},
            "resume_token": uuid.uuid4().hex,
            "segment_size": int(packet.get("segment_size") or 0),
            "segment_seconds": float(packet.get("segment_seconds") or 0),
        }
        self._broadcast_packet(
            {
                "type": "file_begin",
                "transfer_id": transfer_id,
                "size": size,
                "track": track,
                "segment_size": int(packet.get("segment_size") or 0),
                "segment_seconds": float(packet.get("segment_seconds") or 0),
            },
            exclude=member.writer,
        )

    def _server_upload_resume(self, member: _Member, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        if (
            upload is None
            or upload.get("complete")
            or upload.get("member_id") != member.id
            or str(packet.get("resume_token") or "")
            != str(upload.get("resume_token") or "")
            or int(packet.get("offset") or 0) != int(upload.get("received") or 0)
            or int(packet.get("cover_offset") or 0)
            != int(upload.get("cover_received") or 0)
        ):
            return
        upload["writer"] = member.writer
        upload["event"].set()

    def _server_upload_cover(
        self, member: _Member, packet: dict, payload: bytes
    ):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        offset = int(packet.get("offset") or 0)
        total = int(packet.get("total") or 0)
        if (
            not upload
            or upload["writer"] is not member.writer
            or not payload
            or total <= 0
            or total > MAX_COVER_SIZE
            or offset != upload["cover_received"]
            or offset + len(payload) > total
        ):
            return
        upload["cover_received"] += len(payload)
        upload["cover_total"] = total
        upload["cover"].extend(payload)
        self._broadcast_packet(
            {
                "type": "file_cover",
                "transfer_id": transfer_id,
                "offset": offset,
                "total": total,
            },
            payload,
            exclude={member.writer, *upload["catching_up"]},
        )

    def _server_upload_chunk(
        self, member: _Member, packet: dict, payload: bytes
    ):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        if (
            not upload
            or upload["writer"] is not member.writer
            or not payload
        ):
            return
        offset = int(packet.get("offset") or 0)
        if offset != upload["received"]:
            self._drop_server_upload(transfer_id, notify=True)
            return
        if upload["received"] + len(payload) > upload["size"]:
            self._drop_server_upload(transfer_id, notify=True)
            return
        upload["buffer"].seek(upload["received"])
        upload["buffer"].write(payload)
        upload["received"] += len(payload)
        upload["event"].set()
        self._broadcast_packet(
            {
                "type": "file_chunk",
                "transfer_id": transfer_id,
                "offset": offset,
                "segment_index": int(packet.get("segment_index") or 0),
                "segment_seconds": upload["segment_seconds"],
            },
            payload,
            exclude={member.writer, *upload["catching_up"]},
        )
        if not upload["prepared"]:
            upload["prepared"] = True
            self._queue_owner = upload["member_id"]
            self._room_queue = upload["queue"]
            self._begin_prepare(upload["track"], upload["index"])

    def _server_upload_end(self, member: _Member, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        if (
            not upload
            or upload["writer"] is not member.writer
            or upload["received"] != upload["size"]
        ):
            self._broadcast_packet(
                {
                    "type": "error",
                    "message": "Track transfer was incomplete",
                }
            )
            return
        upload["complete"] = True
        upload["event"].set()
        self._broadcast_packet(
            {"type": "file_end", "transfer_id": transfer_id},
            exclude={member.writer, *upload["catching_up"]},
        )
        if not upload["prepared"]:
            self._queue_owner = upload["member_id"]
            self._room_queue = upload["queue"]
            self._begin_prepare(upload["track"], upload["index"])

    def _server_upload_abort(self, member: _Member, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        if not upload or upload.get("member_id") != member.id:
            return
        self._drop_server_upload(transfer_id, notify=True)
        if (
            self._pending_request
            and str((self._room_track or {}).get("stream_id")) == transfer_id
        ):
            self._pending_request = None
            self._ready_members.clear()

    def _server_buffer_report(self, member: _Member, packet: dict):
        transfer_id = str(packet.get("transfer_id") or "")
        upload = self._server_uploads.get(transfer_id)
        received = max(0, int(packet.get("received") or 0))
        if upload is None or received > int(upload.get("received") or 0):
            return
        target = max(
            STREAM_MIN_BUFFER_SECONDS,
            min(
                STREAM_MAX_BUFFER_SECONDS,
                float(packet.get("target_seconds") or STREAM_BUFFER_AHEAD_SECONDS),
            ),
        )
        if isinstance(member.ping_ms, int):
            if member.ping_ms >= 500:
                target = STREAM_MAX_BUFFER_SECONDS
            elif member.ping_ms >= 220:
                target = max(target, 36.0)
        now = time.monotonic()
        upload["reports"][member.id] = {
            "received": received,
            "target": target,
            "updated": now,
        }
        active_targets = [
            float(report["target"])
            for report in upload["reports"].values()
            if now - float(report["updated"]) <= 12.0
        ]
        room_target = (
            max(active_targets)
            if active_targets
            else STREAM_BUFFER_AHEAD_SECONDS
        )
        owner_writer = upload.get("writer")
        if owner_writer is not None:
            self._write_packet(owner_writer, {
                "type": "stream_target",
                "transfer_id": transfer_id,
                "target_seconds": room_target,
            })

    def _drop_server_upload(self, transfer_id: str, notify=False):
        upload = self._server_uploads.pop(transfer_id, None)
        if upload is None:
            return
        upload["complete"] = True
        upload["event"].set()
        for writer in list(upload.get("catching_up") or ()):
            task = self._replay_tasks.pop(writer, None)
            if task is not None:
                task.cancel()
        if notify:
            self._broadcast_packet({
                "type": "file_abort",
                "transfer_id": transfer_id,
            })
