

from __future__ import annotations

import asyncio
import time

from network_protocol import (
    RECONNECT_INITIAL_DELAY, RECONNECT_MAX_DELAY, SOCKET_BUFFER_SIZE,
    STREAM_BUFFER_AHEAD_SECONDS, STREAM_MAX_BUFFER_SECONDS,
    STREAM_MIN_BUFFER_SECONDS, _normalize_room_host, _read_frame, _tune_socket,
)


class NetworkConnectionMixin:
    def _resume_stream_descriptors(self) -> list[dict]:
        rows = []
        for transfer_id, state in self._streams.items():
            rows.append({
                "transfer_id": transfer_id,
                "received": int(state.get("received") or 0),
                "cover_received": len(state.get("cover") or b""),
                "size": int(state.get("size") or 0),
                "complete": bool(state.get("complete")),
            })
        return rows

    async def _connect_as_client(
        self, host_url, port, generation, reconnecting=False
    ) -> bool:
        try:
            host_url = _normalize_room_host(host_url)
        except ValueError as exc:
            self.error_occurred.emit(str(exc))
            return False
        verb = "Reconnecting" if reconnecting else "Connecting"
        self.connection_state_changed.emit(
            f"{verb} to {host_url}:{port}..."
        )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host_url,
                    port,
                    limit=4 * SOCKET_BUFFER_SIZE,
                    happy_eyeballs_delay=0.25,
                    interleave=1,
                ),
                timeout=12,
            )
        except Exception as exc:
            if generation == self._generation:
                if reconnecting:
                    self.connection_state_changed.emit(
                        f"Reconnect failed: {exc}"
                    )
                else:
                    self.error_occurred.emit(
                        f"Could not reach {host_url}:{port}: {exc}"
                    )
            return False
        if generation != self._generation:
            writer.close()
            return False
        _tune_socket(writer)
        self._connection_serial += 1
        connection_serial = self._connection_serial
        self._writer = writer
        self._write_lock = asyncio.Lock()
        try:
            await self._send_frame_locked(
                {
                    "type": "hello",
                    "id": self.local_id,
                    "name": self.local_name,
                    "country": self.local_country,
                    "public_ip": self.local_public_ip,
                    "resume_streams": self._resume_stream_descriptors(),
                }
            )
        except Exception as exc:
            if self._writer is writer:
                self._writer = None
                self._write_lock = None
            self._connection_serial += 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self.connection_state_changed.emit(
                f"Handshake failed: {exc}"
            )
            return False
        self._reconnecting = False
        self.connected.emit()
        self.connection_state_changed.emit(
            "Reconnected" if reconnecting else "Connected"
        )
        if self.role == "host":
            self.send_catalog()
        self._reader_task = asyncio.create_task(
            self._client_read_loop(
                reader, writer, generation, connection_serial
            )
        )
        return True

    async def _client_read_loop(
        self, reader, writer, generation, connection_serial
    ):
        try:
            while True:
                packet, payload = await _read_frame(reader)
                self._handle_incoming(
                    packet, payload, generation, connection_serial
                )
        except asyncio.IncompleteReadError:
            pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if (
                generation == self._generation
                and connection_serial == self._connection_serial
            ):
                self.error_occurred.emit(f"Connection error: {exc}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if (
                generation == self._generation
                and connection_serial == self._connection_serial
                and self._writer is writer
            ):
                self._writer = None
                self._write_lock = None
                self._play_token += 1
                self.roster_updated.emit([])
                if (
                    self._auto_reconnect
                    and self.role is not None
                    and self._connection_target is not None
                ):
                    self._reconnecting = True
                    self.connection_state_changed.emit(
                        "Connection lost. Reconnecting automatically..."
                    )
                    self._start_reconnect(generation)
                else:
                    self.disconnected.emit()
                    self.connection_state_changed.emit("Disconnected")

    def _start_reconnect(self, generation):
        if (
            not self._auto_reconnect
            or self._connection_target is None
            or generation != self._generation
            or (
                self._reconnect_task is not None
                and not self._reconnect_task.done()
            )
        ):
            return
        self._reconnecting = True
        task = asyncio.create_task(self._reconnect_loop(generation))
        self._reconnect_task = task

        def clear(done):
            if self._reconnect_task is done:
                self._reconnect_task = None
                if (
                    self._auto_reconnect
                    and self._connection_target is not None
                    and generation == self._generation
                    and not self.is_connected
                ):
                    self._start_reconnect(generation)

        task.add_done_callback(clear)

    async def _reconnect_loop(self, generation):
        delay = RECONNECT_INITIAL_DELAY
        while (
            self._auto_reconnect
            and self._connection_target is not None
            and self.role is not None
            and generation == self._generation
            and not self.is_connected
        ):
            self.connection_state_changed.emit(
                f"Reconnecting in {delay:g}s..."
            )
            await asyncio.sleep(delay)
            if (
                not self._auto_reconnect
                or self._connection_target is None
                or generation != self._generation
            ):
                return
            host_url, port = self._connection_target
            if await self._connect_as_client(
                host_url, port, generation, reconnecting=True
            ):
                return
            delay = min(RECONNECT_MAX_DELAY, delay * 2)

    def _handle_incoming(
        self, packet, payload, generation, connection_serial
    ):
        if (
            generation != self._generation
            or connection_serial != self._connection_serial
        ):
            return
        kind = packet.get("type")
        if kind == "ping":
            self._send_packet({
                "type": "pong",
                "ts": packet.get("ts"),
                "client_time": time.time(),
            })
        elif kind == "clock_hint":
            try:
                sample = (
                    time.time() - float(packet["server_time"])
                )
                if self._clock_synced:
                    self._server_clock_offset = (
                        self._server_clock_offset * 0.9 + sample * 0.1
                    )
                else:
                    self._server_clock_offset = sample
                self._clock_synced = True
            except (KeyError, TypeError, ValueError):
                pass
        elif kind == "clock_sync":
            try:
                sample = float(packet["offset"])
                if self._clock_synced:
                    self._server_clock_offset = (
                        self._server_clock_offset * 0.25 + sample * 0.75
                    )
                else:
                    self._server_clock_offset = sample
                self._clock_synced = True
            except (KeyError, TypeError, ValueError):
                pass
        elif kind == "roster" and isinstance(
            packet.get("members"), list
        ):
            members = packet["members"]
            for member in members:
                member["is_self"] = member.get("id") == self.local_id
            self.roster_updated.emit(members)
        elif (
            kind == "catalog"
            and self.role == "guest"
            and isinstance(packet.get("tracks"), list)
        ):
            self.catalog_received.emit(packet["tracks"])
        elif kind == "request_upload":
            self._upload_requested_track(packet)
        elif kind == "resume_upload":
            self._resume_requested_upload(packet)
        elif kind == "cancel_upload":
            self._cancel_requested_upload(packet)
        elif kind == "stream_target":
            transfer_id = str(packet.get("transfer_id") or "")
            if transfer_id:
                self._stream_targets[transfer_id] = max(
                    STREAM_MIN_BUFFER_SECONDS,
                    min(
                        STREAM_MAX_BUFFER_SECONDS,
                        float(packet.get("target_seconds") or STREAM_BUFFER_AHEAD_SECONDS),
                    ),
                )
        elif kind == "snapshot_wait":
            self._client_preparing_request = "snapshot"
            self._play_token += 1
            if self.player is not None:
                self._applying_remote = True
                try:
                    self.player.pause()
                finally:
                    self._applying_remote = False
        elif kind == "file_begin":
            self._receive_file_begin(packet)
        elif kind == "file_cover":
            self._receive_file_cover(packet, payload)
        elif kind == "file_chunk":
            self._receive_file_chunk(packet, payload)
        elif kind == "file_end":
            self._receive_file_end(packet)
        elif kind == "file_abort":
            transfer_id = str(packet.get("transfer_id") or "")
            self._abort_incoming_file(transfer_id)
            self._client_preparing_request = None
        elif kind == "prepare_track":
            keep_stream = str(
                (packet.get("track") or {}).get("stream_id") or ""
            )
            for stale_id in list(self._incoming_files):
                if stale_id != keep_stream:
                    self._abort_incoming_file(stale_id)
            self._client_preparing_request = str(
                packet.get("request_id") or ""
            )
            self._play_token += 1
            self.track_prepare_received.emit(packet)
        elif kind == "commit_track":
            self._client_preparing_request = None
            self._apply_track_commit(packet)
            self.track_committed.emit(packet)
        elif kind == "control":
            self._apply_control_packet(packet)
        elif kind == "state":
            self._apply_state_packet(packet)
        elif kind == "repeat":
            self._repeat = bool(packet.get("enabled"))
            self.repeat_received.emit(self._repeat)
        elif kind == "error":
            self._client_preparing_request = None
            self.error_occurred.emit(
                str(packet.get("message") or "Room error")
            )