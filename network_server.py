

from __future__ import annotations

import asyncio
import time
import uuid

from network_protocol import (
    MAX_PEER_WRITE_BUFFER, PING_INTERVAL, START_DELAY, STATE_INTERVAL,
    _Member, _detect_country_for_ip, _encode_frame,
    _normalize_country_code, _normalize_public_ip, _read_frame,
    _tune_socket,
)
from network_replay import NetworkReplayMixin
from network_server_upload import NetworkServerUploadMixin


class NetworkServerMixin(NetworkServerUploadMixin, NetworkReplayMixin):
    def _start_country_lookup(self, member: _Member, public_ip: str):
        if not public_ip:
            return
        cached = self._country_cache.get(public_ip)
        if cached and time.monotonic() - cached[1] < 6 * 60 * 60:
            member.country = cached[0]
            self._broadcast_roster()
            return
        self._country_cache.pop(public_ip, None)

        async def resolve():
            country = await asyncio.to_thread(
                _detect_country_for_ip, public_ip
            )
            if (
                country != "??"
                and self._members.get(member.writer) is member
            ):
                member.country = country
                self._country_cache[public_ip] = (
                    country,
                    time.monotonic(),
                )
                if len(self._country_cache) > 256:
                    oldest = min(
                        self._country_cache,
                        key=lambda ip: self._country_cache[ip][1],
                    )
                    self._country_cache.pop(oldest, None)
                self._broadcast_roster()

        task = asyncio.create_task(resolve())
        self._geo_tasks.add(task)
        task.add_done_callback(self._geo_tasks.discard)

    async def _handle_server_client(self, reader, writer):
        member = None
        _tune_socket(writer)
        try:
            while True:
                packet, payload = await _read_frame(reader)
                kind = packet.get("type")
                if kind == "hello" and member is None:
                    member_id = str(
                        packet.get("id") or uuid.uuid4().hex
                    )
                    for old_writer, old_member in list(
                        self._members.items()
                    ):
                        if old_member.id != member_id:
                            continue
                        self._members.pop(old_writer, None)
                        replay = self._replay_tasks.pop(old_writer, None)
                        if replay is not None:
                            replay.cancel()
                        for upload in self._server_uploads.values():
                            upload.get("catching_up", set()).discard(
                                old_writer
                            )
                            if upload.get("writer") is old_writer:
                                upload["writer"] = None
                        try:
                            old_writer.close()
                        except Exception:
                            pass
                    peer = writer.get_extra_info("peername")
                    peer_ip = (
                        peer[0]
                        if isinstance(peer, (tuple, list)) and peer
                        else peer
                    )
                    public_ip = (
                        _normalize_public_ip(peer_ip)
                        or _normalize_public_ip(packet.get("public_ip"))
                    )
                    member = _Member(
                        writer,
                        member_id,
                        str(packet.get("name") or "Unknown"),
                        _normalize_country_code(packet.get("country")),
                    )
                    self._ready_members.discard(member.id)
                    self._members[writer] = member
                    self._broadcast_roster()
                    self._start_country_lookup(member, public_ip)
                    self._send_room_snapshot(
                        member, packet.get("resume_streams")
                    )
                elif kind == "pong" and member is not None:
                    try:
                        server_now = time.time()
                        sent_at = float(packet["ts"])
                        member.ping_ms = max(
                            0,
                            round(
                                (server_now - sent_at) * 1000
                            ),
                        )
                        if packet.get("client_time") is not None:
                            client_time = float(packet["client_time"])
                            clock_offset = client_time - (
                                sent_at + (server_now - sent_at) / 2
                            )
                            self._write_packet(writer, {
                                "type": "clock_sync",
                                "offset": clock_offset,
                                "rtt_ms": member.ping_ms,
                            })
                        self._broadcast_roster()
                    except Exception:
                        pass
                elif member is not None:
                    self._handle_server_packet(
                        member, packet, payload
                    )
        except asyncio.IncompleteReadError:
            pass
        except Exception:
            pass
        finally:
            removed = self._members.pop(writer, None)
            replay = self._replay_tasks.pop(writer, None)
            if replay is not None:
                replay.cancel()
            for upload in self._server_uploads.values():
                upload.get("catching_up", set()).discard(writer)
                if upload.get("writer") is writer:
                    upload["writer"] = None
                    upload["event"].set()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if (
                removed is not None
                and not any(
                    current.id == removed.id
                    for current in self._members.values()
                )
            ):
                self._ready_members.discard(removed.id)
            self._broadcast_roster()
            self._maybe_commit_pending()

    def _handle_server_packet(
        self, member: _Member, packet: dict, payload: bytes
    ):
        kind = packet.get("type")
        if kind == "catalog":
            self._broadcast_packet(packet, exclude=member.writer)
        elif kind == "select_track":
            track, queue = packet.get("track"), packet.get("queue")
            if (
                isinstance(track, dict)
                and isinstance(queue, list)
                and queue
            ):
                clean_queue = [
                    row for row in queue if isinstance(row, dict)
                ]
                index = max(
                    0,
                    min(
                        int(packet.get("index", 0)),
                        len(clean_queue) - 1,
                    ),
                )
                self._queue_owner = member.id
                self._room_queue = clean_queue
                self._begin_prepare(clean_queue[index], index)
        elif kind == "upload_begin":
            self._server_upload_begin(member, packet)
        elif kind == "upload_resume":
            self._server_upload_resume(member, packet)
        elif kind == "upload_cover":
            self._server_upload_cover(member, packet, payload)
        elif kind == "upload_chunk":
            self._server_upload_chunk(member, packet, payload)
        elif kind == "upload_end":
            self._server_upload_end(member, packet)
        elif kind == "upload_abort":
            self._server_upload_abort(member, packet)
        elif kind == "buffer_report":
            self._server_buffer_report(member, packet)
        elif kind == "upload_unavailable":
            for transfer_id, upload in list(self._server_uploads.items()):
                if (
                    upload.get("member_id") == member.id
                    and not upload.get("complete")
                ):
                    self._drop_server_upload(transfer_id, notify=True)
            self._broadcast_packet({
                "type": "error",
                "message": str(
                    packet.get("message") or "The next track is unavailable"
                ),
            })
        elif (
            kind == "track_ready"
            and self._pending_request
            and str(packet.get("request_id"))
            == self._pending_request["request_id"]
        ):
            if bool(packet.get("ok")):
                self._ready_members.add(member.id)
                self._maybe_commit_pending()
            else:
                message = str(
                    packet.get("message") or "Track download failed"
                )
                self._broadcast_packet(
                    {
                        "type": "error",
                        "message": f"{member.name}: {message}",
                    }
                )
                self._pending_request = None
                self._ready_members.clear()
        elif kind == "control":
            self._server_control(packet)
        elif kind == "repeat":
            self._repeat = bool(packet.get("enabled"))
            self._broadcast_packet(
                {"type": "repeat", "enabled": self._repeat}
            )
        elif kind == "skip":
            self._advance_queue(
                1 if int(packet.get("direction", 1)) >= 0 else -1
            )
        elif kind == "ended":
            request_id = str(packet.get("request_id") or "")
            if request_id and request_id != self._last_end_request:
                self._last_end_request = request_id
                if self._repeat:
                    self._broadcast_control("play", 0)
                else:
                    self._advance_queue(1)

    def _begin_prepare(self, track: dict, index: int):
        request_id = uuid.uuid4().hex
        self._playback_request_id = request_id
        self._room_queue_index = index
        self._room_track = track
        self._committed_stream_id = ""
        self._playing = False
        self._position_ms = 0
        self._started_at = None
        self._pending_request = {
            "type": "prepare_track",
            "request_id": request_id,
            "track": track,
            "queue_index": index,
            "owner_id": self._queue_owner,
        }
        self._ready_members.clear()
        self._last_end_request = None
        self._broadcast_packet(self._pending_request)

    def _maybe_commit_pending(self):
        if not self._pending_request or not self._members:
            return
        required = {member.id for member in self._members.values()}
        if not required.issubset(self._ready_members):
            return
        start_at = time.time() + START_DELAY
        packet = {
            "type": "commit_track",
            "request_id": self._pending_request["request_id"],
            "track": self._room_track,
            "queue_index": self._room_queue_index,
            "owner_id": self._queue_owner,
            "position": 0,
            "start_at": start_at,
            "repeat": self._repeat,
            "playing": True,
        }
        self._pending_request = None
        self._ready_members.clear()
        self._playing = True
        self._position_ms = 0
        self._started_at = start_at
        self._broadcast_packet(packet)

    def _advance_queue(self, direction: int):
        if self._room_queue:
            index = (
                self._room_queue_index + direction
            ) % len(self._room_queue)
            owner = next(
                (
                    member
                    for member in self._members.values()
                    if member.id == self._queue_owner
                ),
                None,
            )
            if owner is None:
                self._broadcast_packet({
                    "type": "error",
                    "message": "The queue owner disconnected",
                })
                return
            self._write_packet(owner.writer, {
                "type": "request_upload",
                "track": self._room_queue[index],
                "queue": self._room_queue,
                "index": index,
            })

    def _current_position(self):
        if self._playing and self._started_at is not None:
            return max(
                0,
                self._position_ms
                + round((time.time() - self._started_at) * 1000),
            )
        return max(0, self._position_ms)

    def _server_control(self, packet: dict):
        action = packet.get("action")
        if action not in self.VALID_CONTROLS or self._pending_request:
            return
        position = max(
            0,
            int(packet.get("position", self._current_position())),
        )
        if action == "play":
            self._broadcast_control("play", position)
        elif action == "pause":
            self._position_ms = position
            self._playing = False
            self._started_at = None
            self._broadcast_packet(
                {
                    "type": "control",
                    "action": "pause",
                    "position": position,
                    "effective_at": time.time(),
                }
            )
        else:
            self._position_ms = position
            if self._playing:
                self._started_at = time.time()
            self._broadcast_packet(
                {
                    "type": "control",
                    "action": "seek",
                    "position": position,
                    "effective_at": time.time(),
                }
            )

    def _broadcast_control(self, action: str, position: int):
        effective_at = time.time() + START_DELAY
        self._position_ms = max(0, int(position))
        self._playing = True
        self._started_at = effective_at
        self._broadcast_packet(
            {
                "type": "control",
                "action": action,
                "position": self._position_ms,
                "effective_at": effective_at,
            }
        )

    def _write_packet(
        self, writer, packet: dict, payload: bytes = b""
    ):
        try:
            writer.write(_encode_frame(packet, payload))
        except Exception:
            pass

    def _broadcast_packet(
        self,
        packet: dict,
        payload: bytes = b"",
        exclude=None,
    ):
        frame = _encode_frame(packet, payload)
        if isinstance(exclude, (set, frozenset, list, tuple)):
            excluded = set(exclude)
        else:
            excluded = {exclude} if exclude is not None else set()
        for writer in list(self._members):
            if writer in excluded:
                continue
            try:
                transport = getattr(writer, "transport", None)
                if (
                    transport is not None
                    and transport.get_write_buffer_size()
                    > MAX_PEER_WRITE_BUFFER
                ):
                    writer.close()
                    continue
                writer.write(frame)
            except Exception:
                pass

    def _broadcast_roster(self):
        self._broadcast_packet(
            {
                "type": "roster",
                "members": [
                    member.as_dict()
                    for member in self._members.values()
                ],
            }
        )

    async def _server_tick_loop(self, generation):
        last_ping = 0.0
        try:
            while (
                generation == self._generation
                and self._server is not None
            ):
                await asyncio.sleep(STATE_INTERVAL)
                now = time.time()
                if now - last_ping >= PING_INTERVAL:
                    self._broadcast_packet(
                        {"type": "ping", "ts": now}
                    )
                    last_ping = now
                self._broadcast_packet(
                    {
                        "type": "state",
                        "playing": self._playing,
                        "position": self._current_position(),
                        "timestamp": now,
                        "pending": self._pending_request is not None,
                    }
                )
        except asyncio.CancelledError:
            pass

    async def _stop_server(self):
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        for writer in list(self._members):
            try:
                writer.close()
            except Exception:
                pass
        self._members.clear()
        for task in list(self._geo_tasks):
            task.cancel()
        self._geo_tasks.clear()
        for task in list(self._replay_tasks.values()):
            task.cancel()
        self._replay_tasks.clear()
        for upload in self._server_uploads.values():
            upload["complete"] = True
            upload["event"].set()
        self._pending_request = None
        self._ready_members.clear()
        self._server_uploads.clear()