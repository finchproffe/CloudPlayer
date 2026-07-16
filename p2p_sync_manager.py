from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Final, Literal

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer

from config import TURN_PASSWORD, TURN_URLS, TURN_USERNAME

SyncAction = Literal["play", "pause", "seek"]


class P2PSyncManager(QObject):
    offer_ready = Signal(str)
    answer_ready = Signal(str)
    connected = Signal()
    disconnected = Signal()
    connection_state_changed = Signal(str)
    sync_received = Signal(str, int)
    catalog_received = Signal(list)
    error_occurred = Signal(str)

    CHANNEL: Final[str] = "cloudplayer-sync-v3"
    VALID_ACTIONS = frozenset({"play", "pause", "seek"})

    def __init__(self, player: QMediaPlayer | None = None, parent=None):
        super().__init__(parent)
        self.player = player
        self.role = None
        self._pc = None
        self._channel = None
        self._generation = 0
        self._applying_remote = False
        self._catalog_provider = lambda: []
        ice_servers = [
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun.cloudflare.com:3478"]),
        ]
        if TURN_URLS and TURN_USERNAME and TURN_PASSWORD:
            ice_servers.append(
                RTCIceServer(
                    urls=TURN_URLS,
                    username=TURN_USERNAME,
                    credential=TURN_PASSWORD,
                )
            )
        self._configuration = RTCConfiguration(iceServers=ice_servers)

    @property
    def is_connected(self):
        return self._channel is not None and self._channel.readyState == "open"

    @property
    def is_applying_remote(self):
        return self._applying_remote

    def set_catalog_provider(self, provider):
        self._catalog_provider = provider

    async def create_host_offer(self):
        await self._reset(False)
        self.role = "host"
        self._generation += 1
        generation = self._generation
        self._pc = self._new_peer(generation)
        self._attach_channel(self._pc.createDataChannel(self.CHANNEL, ordered=True), generation)
        await self._pc.setLocalDescription(await self._pc.createOffer())
        await self._wait_for_ice(self._pc)
        bundle = self._encode(self._pc.localDescription)
        self.offer_ready.emit(bundle)
        return bundle

    async def accept_host_offer(self, bundle):
        offer = self._decode(bundle, "offer")
        await self._reset(False)
        self.role = "guest"
        self._generation += 1
        generation = self._generation
        self._pc = self._new_peer(generation)
        await self._pc.setRemoteDescription(offer)
        await self._pc.setLocalDescription(await self._pc.createAnswer())
        await self._wait_for_ice(self._pc)
        answer = self._encode(self._pc.localDescription)
        self.answer_ready.emit(answer)
        return answer

    async def accept_guest_answer(self, bundle):
        if self.role != "host" or self._pc is None:
            raise RuntimeError("Create a host offer first.")
        await self._pc.setRemoteDescription(self._decode(bundle, "answer"))

    def send(self, action, position_ms=None):
        if self._applying_remote or not self.is_connected:
            return False
        if action not in self.VALID_ACTIONS:
            return False
        position = self.player.position() if position_ms is None and self.player else position_ms or 0
        return self._send_packet({
            "type": "sync", "action": action,
            "position": max(0, int(position)), "timestamp": time.time(),
        })

    def send_catalog(self):
        if self.role == "host" and self.is_connected:
            self._send_packet({"type": "catalog", "tracks": list(self._catalog_provider() or [])})

    def _send_packet(self, packet):
        try:
            self._channel.send(json.dumps(packet, ensure_ascii=False, separators=(",", ":")))
            return True
        except Exception as exc:
            self.error_occurred.emit(f"Send failed: {exc}")
            return False

    def _new_peer(self, generation):
        pc = RTCPeerConnection(configuration=self._configuration)

        @pc.on("datachannel")
        def on_datachannel(channel):
            if generation == self._generation and channel.label == self.CHANNEL:
                self._attach_channel(channel, generation)
            else:
                channel.close()

        @pc.on("connectionstatechange")
        async def on_state():
            if generation != self._generation:
                return
            state = pc.connectionState
            self.connection_state_changed.emit(state)
            if state == "failed":
                self.error_occurred.emit(
                    "Connection failed. Create a fresh Offer TXT and Answer TXT, then exchange them again."
                )
            elif state == "closed":
                self.disconnected.emit()

        return pc

    def _attach_channel(self, channel, generation):
        self._channel = channel

        @channel.on("open")
        def on_open():
            if generation != self._generation:
                return
            self.connected.emit()
            if self.role == "host":
                self.send_catalog()

        @channel.on("close")
        def on_close():
            if generation == self._generation:
                self._channel = None
                self.disconnected.emit()

        @channel.on("message")
        def on_message(message):
            if generation == self._generation:
                self._receive(message)

    def _receive(self, raw):
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            packet = json.loads(raw)
            if packet.get("type") == "catalog":
                if self.role == "guest" and isinstance(packet.get("tracks"), list):
                    self.catalog_received.emit(packet["tracks"])
                return
            action = packet.get("action")
            if action not in self.VALID_ACTIONS:
                return
            position = int(packet.get("position", 0))
            if action == "play":
                position += max(0, round((time.time() - float(packet.get("timestamp", time.time()))) * 1000))
            self._apply(action, position)
            self.sync_received.emit(action, position)
        except Exception as exc:
            self.error_occurred.emit(f"Invalid P2P data: {exc}")

    def _apply(self, action, position):
        if not self.player:
            return
        self._applying_remote = True
        try:
            self.player.setPosition(max(0, position))
            if action == "play":
                self.player.play()
            elif action == "pause":
                self.player.pause()
        finally:
            self._applying_remote = False

    async def close(self):
        await self._reset(True)
        self.role = None

    async def _reset(self, notify=False):
        self._generation += 1
        channel, pc = self._channel, self._pc
        self._channel = self._pc = None
        if channel:
            try:
                channel.close()
            except Exception:
                pass
        if pc:
            await pc.close()
        if notify:
            self.disconnected.emit()

    @staticmethod
    def _encode(description):
        payload = {
            "app": "CloudPlayer",
            "version": 3,
            "type": description.type,
            "sdp": description.sdp,
        }
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    @staticmethod
    def _decode(bundle, expected_type):
        try:
            compact = "".join(bundle.strip().split())
            compact += "=" * (-len(compact) % 4)
            payload = json.loads(base64.urlsafe_b64decode(compact).decode("utf-8"))
        except Exception as exc:
            raise ValueError("This is not a valid CloudPlayer connection TXT.") from exc
        if payload.get("type") != expected_type:
            raise ValueError(f"Expected {expected_type} TXT, received {payload.get('type')}.")
        return RTCSessionDescription(sdp=payload["sdp"], type=expected_type)

    @staticmethod
    async def _wait_for_ice(pc, timeout=30):
        if pc.iceGatheringState == "complete":
            return
        complete = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def state_changed():
            if pc.iceGatheringState == "complete":
                complete.set()

        try:
            await asyncio.wait_for(complete.wait(), timeout)
        except asyncio.TimeoutError:

            pass