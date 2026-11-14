# -*- coding: utf-8 -*-
"""Слушать Яндекс Музыку в нашем плеере.

Треки скачиваются в кэш (%APPDATA%\\MP3Player\\yandex) и играют как обычные файлы — с текстами,
обложками, эквалайзером и «Итогами». Первый трек — сразу, следующие подкачиваются заранее
(по три вперёд). Кэш — обычная папка медиатеки: скачанное остаётся и играет без интернета."""
import os
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

import ymusic


class _Sig(QObject):
    ready = Signal(int, int, str, str)          # поколение, номер, путь, ошибка
    wave = Signal(int, object)                  # поколение, новые треки «Моей волны»


class Streamer(QObject):
    AHEAD = 3

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.gen = 0
        self.tracks = []                 # [{id, title, artist, …}]
        self.paths = {}                  # номер → путь
        self.next_dl = 0
        self.busy = False
        self.started = False
        self.wave_mode = False
        self._wave_busy = False
        self.sig = _Sig()
        self.sig.ready.connect(self._on_ready)
        self.sig.wave.connect(self._on_wave)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._pump)
        self._started_at = 0.0
        try:
            self._migrate()
        except Exception as e:
            print("yandex cache migrate:", e)

    def _migrate(self):
        """Из папок по одной песне (сборка 48) — всё в одну папку; плейлисты и «Итоги» — следом."""
        moved = ymusic.migrate_cache(self.cache_dir)
        if not moved:
            return
        low = {k.lower(): v for k, v in moved.items()}
        fix = lambda p: low.get(os.path.normpath(p).lower(), p) if isinstance(p, str) else p
        f = os.path.join(self.owner.data_dir, "playlists.json")
        try:
            import json
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for pl in d.get("playlists", []):
                pl["tracks"] = [fix(x) for x in pl.get("tracks", [])]
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
        except (OSError, ValueError):
            pass
        st = getattr(self.owner, "stats", None)
        if st is not None:
            tr = st.data.get("tracks") or {}
            for k in [k for k in tr if k.lower() in low]:
                tr[low[k.lower()]] = tr.pop(k)
            for day in (st.data.get("daily") or {}).values():
                for k in [k for k in day if k.lower() in low]:
                    day[low[k.lower()]] = day.pop(k)
            st.dirty = True
        lib = getattr(self.owner, "library", None)
        if lib is not None and hasattr(lib, "items"):
            try:
                with lib.lock:
                    for k in [k for k in lib.items if k.lower() in low]:
                        lib.items.pop(k, None)
                for new in moved.values():
                    lib.update(new)
            except Exception:
                pass

    # --------------------------------------------------------------- общее
    @property
    def cache_dir(self):
        return os.path.join(self.owner.data_dir, "yandex")

    def client(self):
        return ymusic.Client(self.owner.cfg.get("ym_token", ""))

    def logged_in(self):
        return bool(self.owner.cfg.get("ym_token"))

    def flash(self, text, sec=2.5):
        f = getattr(self.owner, "flash", None)
        if f:
            f(text, sec)
        win = self.owner.__dict__.get("library_win")
        if win is not None and win.isVisible():
            win.toast(text)

    # --------------------------------------------------------------- слушать
    def play(self, tracks, start=0, wave=False):
        """Играть список треков Яндекса (с номера start)."""
        tracks = [t for t in tracks if t and (t.get("id") or t.get("path") or t.get("title"))
                  and t.get("available", True)]
        if not tracks:
            self.flash("НЕЧЕГО ИГРАТЬ")
            return
        self.gen += 1
        start = max(0, min(start, len(tracks) - 1))
        self.tracks = tracks[start:]
        self.paths = {}
        self.next_dl = 0
        self.busy = False
        self.started = False
        self.wave_mode = wave
        self.flash("♪ ЯНДЕКС: ЗАГРУЖАЮ…", 3)
        self.timer.start()
        self._pump()

    def play_wave(self):
        self.gen += 1
        g = self.gen
        self.tracks, self.paths, self.next_dl, self.started = [], {}, 0, False
        self.wave_mode = True
        self.flash("♪ МОЯ ВОЛНА ЯНДЕКСА…", 3)
        self._fetch_wave(g)

    def stop(self):
        self.gen += 1
        self.timer.stop()

    def _fetch_wave(self, g):
        if self._wave_busy:
            return
        self._wave_busy = True
        last = self.tracks[-1]["key"] if self.tracks else None
        cl = self.client()

        def work():
            try:
                res = cl.wave(last)
            except Exception as e:
                res = str(e)
            self.sig.wave.emit(g, res)
        threading.Thread(target=work, daemon=True).start()

    def _on_wave(self, g, res):
        self._wave_busy = False
        if g != self.gen:
            return
        if isinstance(res, str):
            self.flash("ЯНДЕКС: " + res.upper()[:60], 4)
            return
        have = {t["id"] for t in self.tracks}
        self.tracks += [t for t in res if t["id"] not in have and t.get("available", True)]
        self.timer.start()
        self._pump()

    @staticmethod
    def _k(p):
        return os.path.normcase(os.path.normpath(p)) if p else ""

    def _current_index(self):
        lp = self._k(getattr(self.owner, "loaded_path", None))
        for i, p in self.paths.items():
            if self._k(p) == lp:
                return i
        return -1

    def _still_ours(self):
        """Играет ли ещё наш список (а не альбом, который включили потом)."""
        if time.monotonic() - self._started_at < 15:     # пока плеер переключается (затухание)
            return True
        files = {self._k(f) for f in (getattr(self.owner, "files", None) or [])}
        return any(self._k(p) in files for p in self.paths.values())

    def _pump(self):
        """Подкачивать следующие треки, пока впереди меньше трёх."""
        if self.busy:
            return
        if self.started and self.paths and not self._still_ours():
            self.timer.stop()                   # пользователь включил другое — дальше не качаем
            return
        if self.next_dl >= len(self.tracks):
            if self.wave_mode and self.tracks:
                self._fetch_wave(self.gen)
            return
        if self.started and self.next_dl - max(0, self._current_index()) > self.AHEAD:
            return
        i = self.next_dl
        self.next_dl += 1
        self.busy = True
        g, info, cl, cache = self.gen, self.tracks[i], self.client(), self.cache_dir

        def work():
            try:
                path = info.get("path") if info.get("path") and os.path.isfile(info["path"]) else None
                if not path:                                 # из Spotify / списка — найти в Яндексе
                    found = ymusic.resolve(cl, info)
                    if not found:
                        raise LookupError("нет в Яндекс Музыке")
                    path = ymusic.download(cl, found, cache)
                self.sig.ready.emit(g, i, path, "")
            except ymusic.NeedLogin as e:
                self.sig.ready.emit(g, i, "", "login:" + str(e))
            except Exception as e:
                self.sig.ready.emit(g, i, "", f"{type(e).__name__}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _ended(self):
        """Плеер остановился в конце нашего списка (следующий трек ещё качался)."""
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            o = self.owner
            return (not o.is_playing() and o.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
                    and self._current_index() >= 0)
        except Exception:
            return False

    def _on_ready(self, g, i, path, err):
        self.busy = False
        if g != self.gen:
            return
        if not path:
            if err.startswith("login:"):
                self.flash("ЯНДЕКС: ВОЙДИ В АККАУНТ (МЕДИАТЕКА → ЯНДЕКС)", 5)
                self.timer.stop()
                return
            t = self.tracks[i]
            print("yandex download:", t.get("artist"), t.get("title"), err)
            self._pump()
            return
        self.paths[i] = path
        o = self.owner
        lib = getattr(o, "library", None)
        if lib is not None and hasattr(lib, "update") and path.startswith(self.cache_dir):
            try:
                lib.update(path)
            except Exception:
                pass
        if not self.started:
            self.started = True
            self._started_at = time.monotonic()
            o.play_list([path], path)
        else:
            ended = self._ended()
            o.queue_insert([path], None)
            if ended:                           # плеер доиграл и встал, не дождавшись — едем дальше
                o.step(+1, user=False)
        QTimer.singleShot(50, self._pump)
