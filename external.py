# -*- coding: utf-8 -*-
"""Пульт для Spotify и Яндекс Музыки: что у них играет (через медиасессии Windows),
управление ими из медиатеки и учёт прослушиваний в «Итогах».

Пока у нас ничего не играет, а Spotify / Яндекс Музыка играют — медиатека показывает их трек
(обложка, текст с lrclib.net), а кнопки плеера управляют ими."""
import asyncio
import datetime as dt
import hashlib
import os
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

try:
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as MediaManager
    HAS_SMTC = True
except Exception:
    HAS_SMTC = False

APPS = {"spotify": "Spotify", "yandex": "Яндекс Музыка"}


def app_kind(aumid):
    a = (aumid or "").lower()
    if "spotify" in a:
        return "spotify"
    if ("yandex" in a and "music" in a) or "yandexmusic" in a:
        return "yandex"
    return None


def _ibuffer_bytes(buf):
    n = int(buf.length)
    try:
        data = bytes(memoryview(buf))[:n]
        if len(data) == n:
            return data
    except Exception:
        pass
    try:
        from winrt.windows.security.cryptography import CryptographicBuffer
        return bytes(CryptographicBuffer.copy_to_byte_array(buf))
    except Exception:
        pass
    from winrt.windows.storage.streams import DataReader
    r = DataReader.from_buffer(buf)
    return bytes(r.read_byte() for _ in range(n))


async def _thumb(props):
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    ref = props.thumbnail
    if ref is None:
        return None
    stream = await ref.open_read_async()
    size = int(stream.size)
    if not 200 < size < 6_000_000:
        return None
    res = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
    return _ibuffer_bytes(res)


async def _read(want_thumb_for):
    mgr = await MediaManager.request_async()
    out = []
    for s in mgr.get_sessions():
        kind = app_kind(s.source_app_user_model_id)
        if not kind:
            continue
        try:
            status = int(s.get_playback_info().playback_status)      # 4 — играет, 5 — пауза
            if status not in (4, 5):
                continue
            props = await s.try_get_media_properties_async()
            title = (props.title or "").strip()
            if not title:
                continue
            artist = (props.artist or props.album_artist or "").strip()
            pos = length = 0.0
            try:
                tl = s.get_timeline_properties()
                length = (tl.end_time - tl.start_time).total_seconds()
                pos = tl.position.total_seconds()
                if status == 4 and tl.last_updated_time:
                    lag = (dt.datetime.now(dt.timezone.utc) - tl.last_updated_time).total_seconds()
                    if 0 <= lag < max(length, 1) + 60:
                        pos += lag
                if length <= 0 or pos > length + 5:
                    length = pos = 0.0
            except Exception:
                pass
            key = f"{kind}|{artist}|{title}"
            thumb = None
            if key in want_thumb_for:
                try:
                    thumb = await _thumb(props)
                except Exception:
                    thumb = None
            out.append({"kind": kind, "app": APPS[kind], "aumid": s.source_app_user_model_id,
                        "title": title, "artist": artist, "album": (props.album_title or "").strip(),
                        "playing": status == 4, "pos": max(0.0, pos), "length": max(0.0, length),
                        "key": key, "thumb": thumb})
        except Exception:
            continue
    return out


async def _command(aumid, cmd, arg=None):
    mgr = await MediaManager.request_async()
    for s in mgr.get_sessions():
        if s.source_app_user_model_id != aumid:
            continue
        if cmd == "toggle":
            await s.try_toggle_play_pause_async()
        elif cmd == "pause":
            await s.try_pause_async()
        elif cmd == "next":
            await s.try_skip_next_async()
        elif cmd == "prev":
            await s.try_skip_previous_async()
        elif cmd == "seek":
            await s.try_change_playback_position_async(int(arg * 10_000_000))     # тики по 100 нс
        return


def _run(aumid, cmd, arg=None):
    try:
        asyncio.run(_command(aumid, cmd, arg))
    except Exception:
        pass


class _FakePlayer:
    """То, что медиатека спрашивает у плеера, — но про трек в Spotify / Яндексе."""

    def __init__(self, ext, real):
        self._ext, self._real = ext, real

    def position(self):
        return int(self._ext.position() * 1000)

    def duration(self):
        return int((self._ext.state or {}).get("length", 0) * 1000)

    def setPosition(self, ms):
        self._ext.command("seek", ms / 1000.0)

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        st = self._ext.state
        return (QMediaPlayer.PlaybackState.PlayingState if st and st["playing"]
                else QMediaPlayer.PlaybackState.PausedState)

    def __getattr__(self, name):                      # громкость и прочее — нашего плеера
        return getattr(self._real, name)


class Proxy:
    """Подменяет плеер для медиатеки, пока показываем Spotify / Яндекс Музыку."""
    external = True
    radio = False
    spectrum = None

    def __init__(self, ext, owner):
        self.__dict__["_ext"] = ext
        self.__dict__["_owner"] = owner
        self.__dict__["player"] = _FakePlayer(ext, owner.player)

    @property
    def title(self):
        return (self._ext.state or {}).get("title", "")

    @property
    def artist(self):
        return (self._ext.state or {}).get("artist", "")

    @property
    def album(self):
        s = self._ext.state or {}
        return s.get("album") or s.get("app", "")

    @property
    def app(self):
        return (self._ext.state or {}).get("app", "")

    @property
    def loaded_path(self):
        s = self._ext.state
        return "ext:" + s["key"] if s else None

    @property
    def _cover(self):
        return self._ext.cover

    @property
    def lyrics(self):
        return self._ext.lyrics

    def is_playing(self):
        s = self._ext.state
        return bool(s and s["playing"])

    def toggle_play(self):
        self._ext.command("toggle")

    def step(self, delta, user=True):
        self._ext.command("next" if delta > 0 else "prev")

    def smooth_pos(self):
        return self._ext.position() * 1000

    def queue_upcoming(self, limit=300):
        return []

    def fetch_lyrics_online(self, manual=False):
        self._ext.find_lyrics(force=True)

    def link_lrc_file(self, lrc):
        pass

    def __getattr__(self, name):
        return getattr(self._owner, name)

    def __setattr__(self, name, value):
        setattr(self._owner, name, value)


class _Sig(QObject):
    got = Signal(object)
    lyr = Signal(str, object)
    cover = Signal(str, object)


class Watcher(QObject):
    """Следит за Spotify и Яндекс Музыкой. Живёт в главном потоке, опрос — в фоне."""
    changed = Signal()

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.state = None                   # трек, который показываем (или None)
        self.cover = None                   # QImage
        self.lyrics = None
        self._base = (0.0, time.monotonic())
        self._busy = False
        self._first = True
        self._own_was = False
        self._own_t = 0.0                   # когда последний раз играл наш плеер
        self._ext_t = 0.0                   # когда Spotify / Яндекс последний раз начали играть
        self._was_playing = {}
        self._thumb_want = set()
        self._covers = {}
        self._lyrics_cache = {}
        self._lyr_busy = set()
        self._stat_t = time.monotonic()
        self._listened, self._counted_key = 0.0, None
        self.proxy = Proxy(self, owner)
        self.sig = _Sig()
        self.sig.got.connect(self._on_poll)
        self.sig.lyr.connect(self._on_lyrics)
        self.sig.cover.connect(self._on_cover)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.poll)
        self.stat_timer = QTimer(self, interval=500)
        self.stat_timer.timeout.connect(self._count)
        if HAS_SMTC:
            self.timer.start()
            self.stat_timer.start()

    # --------------------------------------------------------------- настройки
    def enabled(self):
        return self.owner.cfg.get("ext_remote", True)

    def active(self):
        """Показывать ли в медиатеке Spotify / Яндекс вместо нашего плеера."""
        return bool(self.enabled() and self.state and not self.owner.is_playing()
                    and self._ext_t >= self._own_t - 0.5)

    def position(self):
        s = self.state
        if not s:
            return 0.0
        pos, t0 = self._base
        if s["playing"]:
            pos += time.monotonic() - t0
        return min(pos, s["length"]) if s["length"] else pos

    # --------------------------------------------------------------- опрос
    def poll(self):
        playing = self.owner.is_playing()
        if playing:
            if not self._own_was and self.enabled() and self.owner.cfg.get("ext_exclusive", True):
                self.pause_external()                        # включили наш плеер — Spotify на паузу
            self._own_t = time.monotonic()
        self._own_was = playing
        if self._busy or not (self.enabled() or self.owner.cfg.get("ext_stats", True)):
            return
        self._busy = True
        want = set(self._thumb_want)

        def work():
            try:
                res = asyncio.run(_read(want))
            except Exception:
                res = []
            self.sig.got.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def _on_poll(self, sessions):
        self._busy = False
        now = time.monotonic()
        first, self._first = self._first, False
        for s in sessions:
            if s["playing"] and not self._was_playing.get(s["aumid"]):
                self._ext_t = now                           # там только что нажали «играть»
                if (not first and self.owner.is_playing() and self.enabled()
                        and self.owner.cfg.get("ext_exclusive", True)):
                    self.owner.toggle_play()                 # включили Spotify — наш плеер на паузу
                    self._own_was = False
            self._was_playing[s["aumid"]] = s["playing"]
            if s.get("thumb"):
                self._thumb_want.discard(s["key"])
                img = QImage.fromData(s["thumb"])
                if not img.isNull():
                    self._covers[s["key"]] = img
                    if self.state and self.state["key"] == s["key"]:
                        self.cover = img
        pick = (next((s for s in sessions if s["playing"]), None)
                or next((s for s in sessions if self.state and s["aumid"] == self.state["aumid"]), None))
        old_key = self.state["key"] if self.state else None
        self.state = pick
        if pick:
            self._base = (pick["pos"], now)
            if pick["key"] != old_key:
                self._new_track(pick)
        else:
            self.cover = self.lyrics = None
        self.changed.emit()

    def _new_track(self, s):
        k = s["key"]
        self.cover = self._covers.get(k)
        if self.cover is None:
            self._thumb_want.add(k)
            self._online_cover(s)
        self.lyrics = self._lyrics_cache.get(k)
        if self.lyrics is None and self.owner.cfg.get("lyrics", True):
            self.find_lyrics()
        self._listened, self._counted_key = 0.0, None

    def _online_cover(self, s):
        k = s["key"]

        def work():
            try:
                import online_art
                url = online_art.track_cover_url(s["artist"], s["title"])
                data = online_art.get_bytes(url) if url else None
            except Exception:
                data = None
            self.sig.cover.emit(k, data)
        if self.owner.cfg.get("online_art", True):
            threading.Thread(target=work, daemon=True).start()

    def _on_cover(self, key, data):
        if not data or key in self._covers:
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        self._covers[key] = img
        if self.state and self.state["key"] == key and self.cover is None:
            self.cover = img

    # --------------------------------------------------------------- текст
    def _lrc_file(self, key):
        d = os.path.join(self.owner.data_dir, "lyrics_ext")
        return os.path.join(d, hashlib.md5(key.encode("utf-8")).hexdigest()[:20] + ".lrc")

    def find_lyrics(self, force=False):
        s = self.state
        if not s or s["key"] in self._lyr_busy:
            return
        k = s["key"]
        f = self._lrc_file(k)
        if not force and os.path.isfile(f):
            from lyrics import load_file
            ly = load_file(f)
            self._lyrics_cache[k] = ly
            self.lyrics = ly
            return
        self._lyr_busy.add(k)
        artist, title, album, length = s["artist"], s["title"], s["album"], s["length"]

        def work():
            text = None
            try:
                import lyrics_online
                text, _why = lyrics_online.fetch(artist, title, album, length)
            except Exception:
                text = None
            self.sig.lyr.emit(k, text)
        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics(self, key, text):
        self._lyr_busy.discard(key)
        if not text:
            return
        f = self._lrc_file(key)
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
        from lyrics import parse_lrc
        ly = parse_lrc(text, source=f)
        if not ly or not len(ly):
            return
        self._lyrics_cache[key] = ly
        if self.state and self.state["key"] == key:
            self.lyrics = ly

    # --------------------------------------------------------------- управление
    def command(self, cmd, arg=None):
        s = self.state
        if not s or not HAS_SMTC:
            return
        now = time.monotonic()
        if cmd == "toggle":                                  # сразу показать новое состояние
            pos = self.position()
            s["playing"] = not s["playing"]
            self._base = (pos, now)
            if s["playing"]:
                self._ext_t = now
        elif cmd == "seek":
            self._base = (float(arg), now)
        aumid = s["aumid"]
        threading.Thread(target=_run, args=(aumid, cmd, arg), daemon=True).start()

    def pause_external(self):
        """Наш плеер начал играть — поставить Spotify / Яндекс на паузу."""
        s = self.state
        if s and s["playing"] and HAS_SMTC:
            s["playing"] = False
            self._was_playing[s["aumid"]] = False
            threading.Thread(target=_run, args=(s["aumid"], "pause"), daemon=True).start()

    # --------------------------------------------------------------- «Итоги»
    def _count(self):
        now = time.monotonic()
        d = min(1.0, now - self._stat_t)
        self._stat_t = now
        s = self.state
        if not s or not s["playing"] or not self.owner.cfg.get("ext_stats", True):
            return
        stats = getattr(self.owner, "stats", None)
        if stats is None:
            return
        key = f"ext:{s['kind']}:{s['artist']} - {s['title']}"
        stats.add_time(key, d, s["title"], s["artist"])
        self._listened += d
        if self._counted_key != key and self._listened >= min(30.0, (s["length"] or 60) / 2):
            self._counted_key = key
            stats.add_play(key)
