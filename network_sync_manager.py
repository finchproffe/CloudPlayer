from __future__ import annotations

import asyncio
import socket
import time
import urllib.parse
import urllib.request
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaPlayer

from network_protocol import (
    DRIFT_LIMIT_MS, SOCKET_BUFFER_SIZE, _Member, _detect_public_location,
    _encode_frame, _normalize_room_host,
)
from network_server import NetworkServerMixin
from network_transfer import NetworkTransferMixin


class NetworkSyncManager(QObject, NetworkTransferMixin, NetworkServerMixin):


    connected = Signal()
    disconnected = Signal()
    connection_state_changed = Signal(str)
    sync_received = Signal(str, int)
    catalog_received = Signal(list)
    roster_updated = Signal(list)
    error_occurred = Signal(str)
    track_prepare_received = Signal(dict)
    track_committed = Signal(dict)
    repeat_received = Signal(bool)
    stream_buffer_progress_changed = Signal(int, int)

    VALID_CONTROLS = frozenset({"play", "pause", "seek"})

    def __init__(self, player: QMediaPlayer | None = None, parent=None):
        super().__init__(parent)
        self.player = player
        self.role: str | None = None
        self._catalog_provider = lambda: []
        self._generation = 0
        self._applying_remote = False

        self._server = None
        self._members: dict[object, _Member] = {}
        self._ping_task: asyncio.Task | None = None
        self._server_uploads: dict[str, dict] = {}

        self._writer = None
        self._reader_task: asyncio.Task | None = None
        self._upload_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._write_lock: asyncio.Lock | None = None
        self._connection_target: tuple[str, int] | None = None
        self._auto_reconnect = False
        self._reconnecting = False
        self._connection_serial = 0
        self._server_clock_offset = 0.0
        self._clock_synced = False
        self._incoming_files: dict[str, dict] = {}
        self._streams: dict[str, dict] = {}
        self._outgoing_metadata: dict[str, dict] = {}
        self._outgoing_transfers: dict[str, dict] = {}
        self._active_upload_id = ""
        self._stream_targets: dict[str, float] = {}
        self._persist_tasks: set[asyncio.Task] = set()
        self._replay_tasks: dict[object, asyncio.Task] = {}
        self._geo_tasks: set[asyncio.Task] = set()
        self._country_cache: dict[str, tuple[str, float]] = {}
        self._stream_server = None
        self._stream_port = 0

        self.local_name = socket.gethostname()
        self.local_public_ip = ""
        self.local_country = "??"
        self.local_id = uuid.uuid4().hex

        self._room_track: dict | None = None
        self._committed_stream_id = ""
        self._room_queue: list[dict] = []
        self._room_queue_index = -1
        self._queue_owner: str | None = None
        self._repeat = False
        self._playing = False
        self._position_ms = 0
        self._started_at: float | None = None
        self._pending_request: dict | None = None
        self._ready_members: set[str] = set()
        self._last_end_request: str | None = None
        self._playback_request_id: str | None = None
        self._client_preparing_request: str | None = None
        self._play_token = 0

        view = player.parent() if player is not None else None
        if view is not None and hasattr(view, "set_network_manager"):
            view.set_network_manager(self)

    @property
    def is_connected(self):
        return self._writer is not None and not self._writer.is_closing()

    @property
    def is_applying_remote(self):
        return self._applying_remote

    def set_catalog_provider(self, provider):
        self._catalog_provider = provider

    def stream_url(self, track: dict) -> str:
        transfer_id = str(track.get("stream_id") or "")
        state = self._streams.get(transfer_id)
        if not state or not self._stream_port:
            return ""
        filename = urllib.parse.quote(str(state["final"].name))
        return (
            f"http://127.0.0.1:{self._stream_port}/stream/"
            f"{transfer_id}/{filename}"
        )

    def track_metadata(self, track: dict) -> dict:
        transfer_id = str(track.get("stream_id") or "")
        state = self._streams.get(transfer_id)
        if state:
            metadata = dict(state.get("track") or track)
            cover = bytes(state.get("cover") or b"")
            if cover:
                metadata["cover_bytes"] = cover
            return metadata
        metadata = self._outgoing_metadata.get(transfer_id)
        return dict(metadata or track)

    def release_streams_except(self, transfer_id: str | None):
        keep = str(transfer_id or "")
        for stream_id, state in list(self._streams.items()):
            if stream_id == keep or stream_id in self._incoming_files:
                continue
            if state.get("complete") and (
                state.get("persisted") or state.get("error")
            ):
                self._streams.pop(stream_id, None)
        for stream_id in list(self._outgoing_metadata):
            if stream_id != keep:
                self._outgoing_metadata.pop(stream_id, None)

    def _start_local_location_lookup(self, generation):
        async def resolve():
            public_ip, country = await asyncio.to_thread(
                _detect_public_location
            )
            if generation == self._generation:
                self.local_public_ip = public_ip
                self.local_country = country

        task = asyncio.create_task(resolve())
        self._geo_tasks.add(task)
        task.add_done_callback(self._geo_tasks.discard)

    async def host(
        self, host_url: str, port: int, local_port: int | None = None
    ):
        self._auto_reconnect = False
        await self._reset(False)
        await self._ensure_stream_server()
        self.role = "host"
        try:
            advertised_host = _normalize_room_host(host_url)
        except ValueError as exc:
            self.error_occurred.emit(str(exc))
            self.role = None
            return
        local_port = int(local_port or port)
        self._connection_target = ("127.0.0.1", local_port)
        self._auto_reconnect = True
        self._generation += 1
        generation = self._generation
        self._start_local_location_lookup(generation)
        try:
            self._server = await asyncio.start_server(
                self._handle_server_client,
                None,
                local_port,
                limit=4 * SOCKET_BUFFER_SIZE,
            )
        except Exception as exc:
            self.error_occurred.emit(
                f"Could not bind local port {local_port}: {exc}"
            )
            self._auto_reconnect = False
            self._connection_target = None
            self.role = None
            return
        self.connection_state_changed.emit(
            f"Room available at {advertised_host}:{port}. Connecting locally..."
        )
        self._ping_task = asyncio.create_task(
            self._server_tick_loop(generation)
        )
        if not await self._connect_as_client(
            "127.0.0.1", local_port, generation
        ):
            self._start_reconnect(generation)

    async def join(self, host_url: str, port: int):
        self._auto_reconnect = False
        await self._reset(False)
        await self._ensure_stream_server()
        self.role = "guest"
        try:
            host_url = _normalize_room_host(host_url)
        except ValueError as exc:
            self.error_occurred.emit(str(exc))
            self.role = None
            return
        self._connection_target = (host_url, int(port))
        self._auto_reconnect = True
        self._generation += 1
        generation = self._generation
        self._start_local_location_lookup(generation)
        if not await self._connect_as_client(host_url, port, generation):
            self._start_reconnect(generation)

    def select_track(self, track: dict, queue: list[dict], index: int):
        if not self.is_connected:
            return False
        track = dict(track or {})
        queue = [dict(row) for row in queue if isinstance(row, dict)]
        if not queue:
            return False
        index = max(0, min(int(index), len(queue) - 1))
        local_path = self._find_local_track(track)
        if not local_path:
            self.error_occurred.emit(
                "Selected track is unavailable locally"
            )
            return False
        self._discard_outgoing_transfers()
        if self._upload_task and not self._upload_task.done():
            self._upload_task.cancel()
        self._upload_task = asyncio.create_task(
            self._upload_local_track(local_path, track, queue, index)
        )
        return True

    def send(self, action, position_ms=None):
        return self.control(action, position_ms)

    def control(self, action: str, position_ms=None):
        if not self.is_connected or action not in self.VALID_CONTROLS:
            return False
        position = (
            self.player.position()
            if position_ms is None and self.player is not None
            else int(position_ms or 0)
        )
        position = max(0, position)



        if self.player is not None:
            self._applying_remote = True
            try:
                if action == "pause":
                    self.player.pause()
                elif action == "play":
                    self.player.setPosition(position)
                    self.player.play()
                else:
                    self.player.setPosition(position)
            finally:
                self._applying_remote = False
            self.sync_received.emit(action, position)

        return self._send_packet(
            {"type": "control", "action": action, "position": position}
        )

    def set_repeat(self, enabled: bool):
        return self._send_packet(
            {"type": "repeat", "enabled": bool(enabled)}
        )

    def skip(self, direction: int):
        return self._send_packet(
            {"type": "skip", "direction": 1 if direction >= 0 else -1}
        )

    def track_ended(self, request_id: str | None):
        return self._send_packet(
            {"type": "ended", "request_id": request_id}
        )

    def track_ready(self, request_id: str, ok: bool, message: str = ""):
        return self._send_packet(
            {
                "type": "track_ready",
                "request_id": str(request_id),
                "ok": bool(ok),
                "message": str(message)[:300],
            }
        )

    def send_catalog(self):
        if self.role == "host" and self.is_connected:
            self._send_packet(
                {
                    "type": "catalog",
                    "tracks": list(self._catalog_provider() or []),
                }
            )

    async def close(self):
        self._auto_reconnect = False
        self._reconnecting = False
        self._connection_target = None
        await self._reset(True)
        if self._persist_tasks:
            await asyncio.gather(
                *list(self._persist_tasks), return_exceptions=True
            )
        self.role = None

    def _send_packet(self, packet, payload: bytes = b""):
        if not self._writer:
            return False
        try:
            self._writer.write(_encode_frame(packet, payload))
            return True
        except Exception as exc:
            self.error_occurred.emit(f"Send failed: {exc}")
            return False

    async def _send_frame_locked(
        self, packet: dict, payload: bytes = b""
    ):
        writer = self._writer
        lock = self._write_lock
        if not writer or not lock:
            raise ConnectionError("Not connected")
        frame = _encode_frame(packet, payload)
        async with lock:
            if self._writer is not writer or self._write_lock is not lock:
                raise ConnectionError("Connection changed while sending")
            writer.write(frame)
            await writer.drain()

    def _apply_track_commit(self, packet):
        self._room_track = packet.get("track") or self._room_track
        self._committed_stream_id = str(
            (self._room_track or {}).get("stream_id") or ""
        )
        view = self.player.parent() if self.player is not None else None
        if view is not None and hasattr(view, "commit_remote_track"):
            view.commit_remote_track(packet)
        self._repeat = bool(packet.get("repeat", False))
        self.repeat_received.emit(self._repeat)
        outgoing = self._outgoing_transfers.get(
            self._committed_stream_id
        )
        if outgoing is not None:
            size = int(outgoing.get("size") or 0)
            self.stream_buffer_progress_changed.emit(size, size)
        position = int(packet.get("position", 0))
        if bool(packet.get("playing", True)):
            self._schedule_play(
                position,
                float(packet.get("start_at", time.time())),
            )
        elif self.player is not None:
            self._play_token += 1
            self._applying_remote = True
            try:
                self.player.pause()
                self.player.setPosition(max(0, position))
            finally:
                self._applying_remote = False
            self.sync_received.emit("pause", position)

    def _schedule_play(self, position: int, start_at: float):
        if self.player is None:
            return
        self._play_token += 1
        play_token = self._play_token
        stream_id = self._committed_stream_id
        local_start_at = start_at + (
            self._server_clock_offset if self._clock_synced else 0.0
        )
        delay_ms = max(
            0, round((local_start_at - time.time()) * 1000)
        )
        self._applying_remote = True
        self.player.pause()
        self.player.setPosition(max(0, int(position)))
        self._applying_remote = False

        def start():
            if (
                self.player is None
                or play_token != self._play_token
                or stream_id != self._committed_stream_id
            ):
                return
            target = max(
                0,
                int(
                    position
                    + max(0.0, time.time() - local_start_at) * 1000
                ),
            )
            self._applying_remote = True
            self.player.setPosition(target)
            self.player.play()
            self._applying_remote = False

        QTimer.singleShot(delay_ms, start)

    def _apply_control_packet(self, packet):
        if self.player is None:
            return
        action = packet.get("action")
        if action not in self.VALID_CONTROLS:
            return
        position = max(0, int(packet.get("position", 0)))
        effective_at = float(
            packet.get("effective_at", time.time())
        )
        if action == "play":
            self._schedule_play(position, effective_at)
        else:
            self._play_token += 1
            self._applying_remote = True
            self.player.setPosition(position)
            if action == "pause":
                self.player.pause()
            self._applying_remote = False
        self.sync_received.emit(action, position)

    def _apply_state_packet(self, packet):
        if (
            self.player is None
            or packet.get("pending")
            or self._client_preparing_request
        ):
            return
        playing = bool(packet.get("playing"))
        position = max(0, int(packet.get("position", 0)))
        timestamp = float(packet.get("timestamp", time.time()))
        local_timestamp = timestamp + (
            self._server_clock_offset if self._clock_synced else 0.0
        )
        if playing:
            position += max(
                0, round((time.time() - local_timestamp) * 1000)
            )
        playback_changed = False
        self._applying_remote = True
        try:
            if abs(self.player.position() - position) > DRIFT_LIMIT_MS:
                self.player.setPosition(position)
            if (
                playing
                and self.player.playbackState()
                != QMediaPlayer.PlayingState
            ):
                self.player.play()
                playback_changed = True
            elif (
                not playing
                and self.player.playbackState()
                == QMediaPlayer.PlayingState
            ):
                self.player.pause()
                playback_changed = True
        finally:
            self._applying_remote = False
        if playback_changed:
            self.sync_received.emit("play" if playing else "pause", position)

    async def _reset(self, notify=False):
        self._auto_reconnect = False
        self._reconnecting = False
        self._generation += 1
        self._connection_serial += 1
        self._discard_outgoing_transfers()
        for task_attr in (
            "_reconnect_task",
            "_upload_task",
            "_ping_task",
            "_reader_task",
        ):
            task = getattr(self, task_attr)
            if task and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, task_attr, None)
        for transfer_id in list(self._incoming_files):
            self._abort_incoming_file(transfer_id)
        writer, self._writer = self._writer, None
        self._write_lock = None
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        await self._stop_server()
        if self._stream_server is not None:
            self._stream_server.close()
            try:
                await self._stream_server.wait_closed()
            except Exception:
                pass
            self._stream_server = None
            self._stream_port = 0
        self._streams.clear()
        self._outgoing_metadata.clear()
        self._outgoing_transfers.clear()
        self._stream_targets.clear()
        self._active_upload_id = ""
        self._room_track = None
        self._committed_stream_id = ""
        self._room_queue = []
        self._room_queue_index = -1
        self._queue_owner = None
        self._repeat = False
        self._playing = False
        self._position_ms = 0
        self._started_at = None
        self._playback_request_id = None
        self._client_preparing_request = None
        self._server_clock_offset = 0.0
        self._clock_synced = False
        self._play_token += 1
        self.stream_buffer_progress_changed.emit(0, 0)
        if notify:
            self.disconnected.emit()
