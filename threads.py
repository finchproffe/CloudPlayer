import json
import re
import random
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from config import GENIUS_TOKEN, FFMPEG_PATH, PLAYLISTS_PATH, AUDIO_EXTENSIONS
from utils import GeniusLyricsParser, extract_sc_meta


class TrackMetaFetcher(QThread):
    meta_ready = Signal(dict)

    def __init__(self, song_path):
        super().__init__()
        self.song_path = Path(song_path)

    @staticmethod
    def _normalize(text):
        s = (text or "").lower().strip()
        s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)
        s = re.sub(r'\bfeat\.?\b|\bft\.?\b|\bfeaturing\b', ' ', s)
        s = re.sub(r'[^a-zа-яё0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @staticmethod
    def _is_match(expected, actual, min_len=3):
        if not expected or not actual:
            return False
        if expected == actual:
            return True
        if min(len(expected), len(actual)) < min_len:
            return False
        return expected in actual or actual in expected

    def run(self):
        base_name = self.song_path.stem
        target_dir = self.song_path.parent

        raw_name = base_name
        raw_name = re.sub(r'^\d+[\.\s\-]*', '', raw_name)
        raw_name = re.sub(r'\(.*?\)|\[.*?\]', '', raw_name).strip()

        artist = "Неизвестен"
        title = raw_name
        duration = None

        if " - " in raw_name:
            parts = raw_name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif "-" in raw_name:
            parts = raw_name.split("-", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        result_data = {
            "title": title,
            "artist": artist,
            "prod": "",
            "lyrics": "Текст не найден.",
            "cover_bytes": None,
            "cover_url": None,
            "duration": duration
        }

        sidecar_path = target_dir / f"{base_name}.json"
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sidecar_data = json.load(f)
                artist = sidecar_data.get("artist", artist)
                title = sidecar_data.get("title", title)
                duration = sidecar_data.get("duration")

                result_data["artist"] = artist
                result_data["title"] = title
                result_data["duration"] = duration
            except Exception as e:
                print(f"[Sidecar error] {e}")

        local_cover_jpg = target_dir / f"{base_name}.jpg"
        local_cover_png = target_dir / f"{base_name}.png"
        local_cover_webp = target_dir / f"{base_name}.webp"

        chosen_local_path = None
        for path in [local_cover_jpg, local_cover_png, local_cover_webp]:
            if path.exists():
                chosen_local_path = path
                break

        if chosen_local_path:
            try:
                with open(chosen_local_path, "rb") as f:
                    result_data["cover_bytes"] = f.read()
            except Exception as e:
                print(f"[Local cover error] {e}")

        if not title:
            self.meta_ready.emit(result_data)
            return

        search_query = f"{artist} {title}".strip()
        search_url = f"https://api.genius.com/search?q={urllib.parse.quote(search_query)}"
        headers = {
            "Authorization": f"Bearer {GENIUS_TOKEN}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))

            hits = data.get("response", {}).get("hits", [])
            if hits:
                exp_art_lower = self._normalize(artist)
                exp_tit_lower = self._normalize(title)

                hit_result = None
                for hit in hits:
                    item = hit.get("result", {})
                    hit_artist = self._normalize(item.get("primary_artist", {}).get("name", ""))
                    hit_title = self._normalize(item.get("title", ""))

                    artist_ok = self._is_match(exp_art_lower, hit_artist)
                    title_ok = self._is_match(exp_tit_lower, hit_title)

                    if artist_ok and title_ok:
                        hit_result = item
                        break

                if hit_result is None:
                    self.meta_ready.emit(result_data)
                    return

                if not result_data["cover_bytes"]:
                    cover_url = hit_result.get("song_art_image_thumbnail_url")
                    if cover_url:
                        result_data["cover_url"] = cover_url
                        try:
                            img_req = urllib.request.Request(cover_url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(img_req, timeout=4) as img_res:
                                result_data["cover_bytes"] = img_res.read()
                        except Exception:
                            pass

                song_web_url = hit_result.get("url")
                if song_web_url:
                    page_req = urllib.request.Request(song_web_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Accept": "text/html"
                    })
                    with urllib.request.urlopen(page_req, timeout=5) as page_response:
                        html_content = page_response.read().decode('utf-8')

                    parser = GeniusLyricsParser()
                    parser.feed(html_content)
                    lyrics = "".join(parser.lyrics).strip()

                    if lyrics:
                        lyrics = re.sub(r'^.*?Lyrics\s*', '', lyrics, count=1, flags=re.IGNORECASE)
                        lyrics = re.sub(r'\[Текст песни.*?\]\s*', '', lyrics, count=1, flags=re.IGNORECASE)
                        result_data["lyrics"] = re.sub(r'\n{3,}', '\n\n', lyrics).strip()

        except Exception as e:
            if not result_data["cover_bytes"]:
                result_data["lyrics"] = f"Ошибка загрузки метаданных: {e}"

        self.meta_ready.emit(result_data)


class RecommendationFetcher(QThread):
    """Подбирает 3 конкретных трека от исполнителей из плейлистов пользователя.

    Алгоритм:
      1. Собираем пул исполнителей пользователя (sidecar .json -> имя файла).
      2. Собираем множество «уже имеющихся» треков, чтобы их исключить.
      3. Случайно перебираем артистов и берём у каждого случайный трек из
         Genius до тех пор, пока не наберём 3 уникальные рекомендации.
    Сигнал отдаёт список кортежей [(artist, title), ...].
    """
    rec_ready = Signal(list)

    TARGET_COUNT = 3

    @staticmethod
    def _normalize_key(text: str) -> str:
        """Грубая нормализация для сравнения названий (artist+title)."""
        s = (text or "").lower().strip()
        s = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)
        s = re.sub(r'[^a-zа-яё0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @staticmethod
    def _collect_user_artists():
        """Собирает исполнителей ТОЛЬКО из плейлистов пользователя.

        Приоритет:
          1. Sidecar-файл <song>.json рядом с треком — там лежат точные
             метаданные, полученные с SoundCloud при скачивании.
          2. Разбор имени файла по шаблону 'Artist - Title'.
        Возвращает уникальный список (порядок не гарантирован).
        """
        artists = []
        seen = set()
        if not PLAYLISTS_PATH.exists():
            return artists
        try:
            for p_dir in PLAYLISTS_PATH.iterdir():
                songs_dir = p_dir / "songs"
                if not songs_dir.exists():
                    continue
                for f in songs_dir.glob("*.*"):
                    if f.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue

                    artist = None
                    sidecar = f.parent / f"{f.stem}.json"
                    if sidecar.exists():
                        try:
                            with open(sidecar, "r", encoding="utf-8") as fh:
                                data = json.load(fh)
                            candidate = (data.get("artist") or "").strip()
                            if candidate:
                                artist = candidate
                        except Exception:
                            pass

                    if not artist and "-" in f.stem:
                        artist = f.stem.split("-", 1)[0].strip()

                    if not artist:
                        continue
                    key = artist.lower()
                    if key not in seen:
                        seen.add(key)
                        artists.append(artist)
        except Exception:
            pass
        return artists

    @staticmethod
    def _collect_user_track_keys():
        """Множество нормализованных 'artist title' ключей уже имеющихся
        треков. Используется, чтобы не рекомендовать то, что уже скачано.
        """
        keys = set()
        if not PLAYLISTS_PATH.exists():
            return keys
        try:
            for p_dir in PLAYLISTS_PATH.iterdir():
                songs_dir = p_dir / "songs"
                if not songs_dir.exists():
                    continue
                for f in songs_dir.glob("*.*"):
                    if f.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    artist, title = None, None

                    sidecar = f.parent / f"{f.stem}.json"
                    if sidecar.exists():
                        try:
                            with open(sidecar, "r", encoding="utf-8") as fh:
                                data = json.load(fh)
                            artist = (data.get("artist") or "").strip() or None
                            title = (data.get("title") or "").strip() or None
                        except Exception:
                            pass

                    if not artist or not title:
                        if " - " in f.stem:
                            parts = f.stem.split(" - ", 1)
                        elif "-" in f.stem:
                            parts = f.stem.split("-", 1)
                        else:
                            parts = None
                        if parts:
                            artist = (artist or parts[0].strip())
                            title = (title or parts[1].strip() if len(parts) > 1 else None)

                    if artist and title:
                        keys.add(
                            RecommendationFetcher._normalize_key(artist)
                            + " "
                            + RecommendationFetcher._normalize_key(title)
                        )
        except Exception:
            pass
        return keys

    def _fetch_tracks_for_artist(self, artist: str, headers: dict) -> list:
        """Возвращает список {artist, title} для одного исполнителя из Genius."""
        try:
            search_url = f"https://api.genius.com/search?q={urllib.parse.quote(artist)}"
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
            hits = data.get("response", {}).get("hits", [])
            results = []
            for hit in hits:
                item = hit.get("result", {})
                hit_artist = item.get("primary_artist", {}).get("name", artist)
                hit_title = item.get("title", "")
                if hit_title:
                    results.append({"artist": hit_artist, "title": hit_title})
            return results
        except Exception:
            return []

    def run(self):
        artists = self._collect_user_artists()

        if not artists:
            self.rec_ready.emit([])
            return

        existing = self._collect_user_track_keys()
        headers = {
            "Authorization": f"Bearer {GENIUS_TOKEN}",
            "User-Agent": "Mozilla/5.0"
        }

        recommendations = []
        seen_titles = set()
        shuffled = artists[:]
        random.shuffle(shuffled)

        max_artist_attempts = max(10, len(shuffled) * 2)

        for artist in shuffled:
            if len(recommendations) >= self.TARGET_COUNT:
                break
            if max_artist_attempts <= 0:
                break
            max_artist_attempts -= 1

            tracks = self._fetch_tracks_for_artist(artist, headers)
            if not tracks:
                continue
            random.shuffle(tracks)

            for tr in tracks:
                if len(recommendations) >= self.TARGET_COUNT:
                    break
                a, t = tr["artist"], tr["title"]
                key = self._normalize_key(a) + " " + self._normalize_key(t)
                if key in existing or key in seen_titles:
                    continue
                seen_titles.add(key)
                recommendations.append((a, t))

        self.rec_ready.emit(recommendations)


class BackgroundDownloader(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, query, dest_path):
        super().__init__()
        self.query = query
        self.dest_path = dest_path

    def run(self):
        try:
            import yt_dlp
            ydl_opts = {
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}
                ],
                'outtmpl': str(self.dest_path / '%(title).200s.%(ext)s'),
                'ffmpeg_location': str(FFMPEG_PATH),
                'quiet': True,
                'no_warnings': True,
                'windows_creation_flags': 0x08000000,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch1:{self.query}", download=True)
                if info and 'entries' in info and info['entries']:
                    entry = info['entries'][0]
                    meta = extract_sc_meta(entry)

                    filepath = entry.get('requested_downloads', [{}])[0].get('filepath')
                    if not filepath:
                        filepath = ydl.prepare_filename(entry)

                    if filepath:
                        stem = Path(filepath).stem
                        json_path = self.dest_path / f"{stem}.json"
                        with open(json_path, 'w', encoding='utf-8') as f:
                            json.dump(meta, f, ensure_ascii=False, indent=2)

            self.finished_signal.emit(True, "Успешно добавлено!")
        except Exception as e:
            self.finished_signal.emit(False, str(e))