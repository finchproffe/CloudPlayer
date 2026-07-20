import atexit
import json
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import AUDIO_EXTENSIONS, PLAYLISTS_PATH


def _read_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default
    return value


def load_playlist_snapshot(playlists_path, name, should_stop=None):
    playlists_path = Path(playlists_path)
    playlist_folder = playlists_path / str(name)
    songs_path = playlist_folder / "songs"
    metadata_path = playlists_path / f"{name}.json"
    order_path = playlist_folder / "order.json"

    metadata = _read_json(metadata_path, {})
    if not isinstance(metadata, dict):
        metadata = {}

    stored_order = _read_json(order_path, None)
    if isinstance(stored_order, dict):
        stored_order = stored_order.get("songs")
    if not isinstance(stored_order, list):
        stored_order = metadata.get("songs", [])
    if not isinstance(stored_order, list):
        stored_order = []

    files_by_name = {}
    try:
        with os.scandir(songs_path) as entries:
            for index, entry in enumerate(entries):
                if index % 256 == 0 and should_stop and should_stop():
                    return None
                if (
                    entry.is_file()
                    and Path(entry.name).suffix.lower() in AUDIO_EXTENSIONS
                ):
                    files_by_name[entry.name] = Path(entry.path)
    except OSError:
        pass

    ordered_names = []
    seen = set()
    for value in stored_order:
        filename = str(value)
        if filename in files_by_name and filename not in seen:
            ordered_names.append(filename)
            seen.add(filename)
    ordered_names.extend(
        sorted(
            (filename for filename in files_by_name if filename not in seen),
            key=str.casefold,
        )
    )

    needs_write = (
        ordered_names != stored_order
        or not order_path.exists()
        or metadata.get("song_count") != len(ordered_names)
        or "songs" in metadata
    )
    return ordered_names, metadata, needs_write


class PlaylistSnapshotLoader(QThread):
    loaded = Signal(int, str, object, object, bool)

    def __init__(self, generation, name, parent=None, playlists_path=None):
        super().__init__(parent)
        self.generation = int(generation)
        self.name = str(name)
        self.playlists_path = Path(playlists_path or PLAYLISTS_PATH)

    def run(self):
        snapshot = load_playlist_snapshot(
            self.playlists_path,
            self.name,
            self.isInterruptionRequested,
        )
        if snapshot is None or self.isInterruptionRequested():
            return
        order, metadata, needs_write = snapshot
        self.loaded.emit(
            self.generation,
            self.name,
            order,
            metadata,
            needs_write,
        )


class PlaylistSummaryLoader(QThread):
    names_ready = Signal(object)
    summary_ready = Signal(str, int, str)

    def __init__(self, names, parent=None, playlists_path=None):
        super().__init__(parent)
        self.discover_names = names is None
        self.names = [] if names is None else [str(name) for name in names]
        self.playlists_path = Path(playlists_path or PLAYLISTS_PATH)

    def run(self):
        if self.discover_names:
            try:
                self.names = sorted(
                    (
                        path.stem
                        for path in self.playlists_path.glob("*.json")
                    ),
                    key=str.casefold,
                )
            except OSError:
                self.names = []
            self.names_ready.emit(self.names)
        for name in self.names:
            if self.isInterruptionRequested():
                return
            count = 0
            first_track = ""
            songs_path = self.playlists_path / name / "songs"
            try:
                with os.scandir(songs_path) as entries:
                    for index, entry in enumerate(entries):
                        if index % 256 == 0 and self.isInterruptionRequested():
                            return
                        if (
                            entry.is_file()
                            and Path(entry.name).suffix.lower()
                            in AUDIO_EXTENSIONS
                        ):
                            count += 1
                            if not first_track or entry.name.casefold() < Path(
                                first_track
                            ).name.casefold():
                                first_track = entry.path
            except OSError:
                pass
            self.summary_ready.emit(name, count, first_track)


class _PlaylistMetadataWriter:
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = {}
        self._generation = {}
        self._busy_key = None
        self._thread = threading.Thread(
            target=self._run,
            name="playlist-metadata-writer",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _key(playlists_path, name):
        return str(Path(playlists_path) / str(name))

    def schedule(self, playlists_path, name, order, metadata=None):
        playlists_path = Path(playlists_path)
        name = str(name)
        key = self._key(playlists_path, name)
        with self._condition:
            generation = self._generation.get(key, 0) + 1
            self._generation[key] = generation
            self._pending[key] = (
                generation,
                playlists_path,
                name,
                order,
                dict(metadata or {}),
            )
            self._condition.notify()

    def cancel(self, playlists_path, name):
        key = self._key(playlists_path, name)
        with self._condition:
            self._generation[key] = self._generation.get(key, 0) + 1
            self._pending.pop(key, None)
            while self._busy_key == key:
                self._condition.wait(0.05)
            self._condition.notify_all()

    def _is_current(self, key, generation):
        with self._condition:
            return self._generation.get(key) == generation

    @staticmethod
    def _write_temporary(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        return temporary

    def _write(self, key, payload):
        generation, playlists_path, name, order, metadata = payload
        playlist_folder = playlists_path / name
        order_path = playlist_folder / "order.json"
        metadata_path = playlists_path / f"{name}.json"
        metadata["name"] = name
        metadata["song_count"] = len(order)
        metadata.pop("songs", None)

        order_text = json.dumps(
            {"songs": order}, ensure_ascii=False, separators=(",", ":")
        )
        metadata_text = json.dumps(
            metadata, ensure_ascii=False, indent=2
        )
        order_temporary = None
        metadata_temporary = None
        try:
            if not self._is_current(key, generation):
                return
            order_temporary = self._write_temporary(order_path, order_text)
            metadata_temporary = self._write_temporary(
                metadata_path, metadata_text
            )
            if not self._is_current(key, generation):
                return
            order_temporary.replace(order_path)
            metadata_temporary.replace(metadata_path)
        except OSError as exc:
            print(f"[Playlist index] Failed to save {name}: {exc}")
        finally:
            if order_temporary is not None:
                order_temporary.unlink(missing_ok=True)
            if metadata_temporary is not None:
                metadata_temporary.unlink(missing_ok=True)

    def _run(self):
        while True:
            with self._condition:
                while not self._pending:
                    self._condition.wait()
                key, payload = self._pending.popitem()
                self._busy_key = key
            self._write(key, payload)
            with self._condition:
                self._busy_key = None
                self._condition.notify_all()

    def flush(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._pending or self._busy_key is not None
            ) and time.monotonic() < deadline:
                self._condition.wait(max(0.01, deadline - time.monotonic()))


_METADATA_WRITER = _PlaylistMetadataWriter()


def schedule_playlist_write(name, order, metadata=None, playlists_path=None):
    _METADATA_WRITER.schedule(
        playlists_path or PLAYLISTS_PATH,
        name,
        order,
        metadata,
    )


def cancel_playlist_writes(name, playlists_path=None):
    _METADATA_WRITER.cancel(playlists_path or PLAYLISTS_PATH, name)


def flush_playlist_writes(timeout=5.0):
    _METADATA_WRITER.flush(timeout)


atexit.register(flush_playlist_writes)
