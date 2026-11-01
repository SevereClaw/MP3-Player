# -*- coding: utf-8 -*-
"""Интернет-радио: станции по умолчанию, разбор .m3u/.pls и название песни из потока.

Название песни радио передаёт прямо в потоке (ICY-метаданные «StreamTitle='…'»).
Раз в 10 секунд коротко подключаемся к потоку, читаем до первого блока метаданных
(обычно ~16 КБ) и отключаемся — так не надо качать поток второй раз целиком."""
import os
import re
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

DEFAULT_STATIONS = [
    {"name": "SomaFM — Groove Salad", "url": "https://ice.somafm.com/groovesalad"},
    {"name": "SomaFM — Lush", "url": "https://ice.somafm.com/lush"},
    {"name": "SomaFM — Drone Zone", "url": "https://ice.somafm.com/dronezone"},
    {"name": "SomaFM — Indie Pop Rocks!", "url": "https://ice.somafm.com/indiepop"},
    {"name": "Радио Рекорд", "url": "https://radiorecord.hostingradio.ru/rr_main96.aacp"},
    {"name": "Рекорд — Russian Hits", "url": "https://radiorecord.hostingradio.ru/russianhits64.aacp"},
    {"name": "Русское Радио", "url": "https://rusradio.hostingradio.ru/rusradio128.mp3"},
    {"name": "Европа Плюс", "url": "http://ep128.hostingradio.ru:8030/ep128"},
]


def is_url(s):
    return bool(re.match(r"^https?://", s or "", re.I))


def _decode(b):
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def parse_playlist_file(path):
    """.m3u/.m3u8/.pls → [{'name', 'url'}]."""
    try:
        with open(path, "rb") as f:
            text = _decode(f.read())
    except OSError:
        return []
    out = []
    if path.lower().endswith(".pls") or "[playlist]" in text.lower():
        files, titles = {}, {}
        for line in text.splitlines():
            m = re.match(r"^\s*(File|Title)(\d+)\s*=\s*(.+?)\s*$", line, re.I)
            if m:
                (files if m.group(1).lower() == "file" else titles)[m.group(2)] = m.group(3)
        for k in sorted(files, key=lambda x: int(x)):
            if is_url(files[k]):
                out.append({"name": titles.get(k) or files[k], "url": files[k]})
        return out
    name = None
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("#EXTINF"):
            name = line.split(",", 1)[1].strip() if "," in line else None
        elif is_url(line):
            out.append({"name": name or line, "url": line})
            name = None
    return out


def split_title(t):
    """«Исполнитель - Песня» → (исполнитель, песня)."""
    t = (t or "").strip()
    for sep in (" - ", " — ", " – "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip()
    return "", t


class RadioMeta(QObject):
    """Фоновое чтение названия песни из потока. Сигнал changed(title, station_name)."""
    changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self.url = None
        self.supported = None        # None — ещё не знаем, False — поток без названий
        self.last = ""
        self.error = ""

    def start(self, url):
        self._gen += 1
        self.url, self.supported, self.last, self.error = url, None, "", ""
        threading.Thread(target=self._loop, args=(self._gen, url), daemon=True).start()

    def stop(self):
        self._gen += 1
        self.url = None

    def _loop(self, gen, url):
        delay = 0.5
        while gen == self._gen:
            time.sleep(delay)
            if gen != self._gen:
                return
            try:
                title, name = self._read_once(url)
                self.error = ""
                if title is None:                       # метаданных нет — проверим ещё раз нескоро
                    self.supported = False
                    delay = 90
                    continue
                self.supported = True
                if gen == self._gen and title != self.last:
                    self.last = title
                    self.changed.emit(title, name or "")
                delay = 10
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:120]
                delay = 20

    @staticmethod
    def _read_once(url):
        req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": "MP3Player/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            metaint = int(r.headers.get("icy-metaint") or 0)
            name = r.headers.get("icy-name") or ""
            if not metaint or metaint > 512 * 1024:
                return None, name
            left = metaint
            while left > 0:                              # пропустить звук до метаданных
                chunk = r.read(min(left, 65536))
                if not chunk:
                    return None, name
                left -= len(chunk)
            n = r.read(1)
            if not n:
                return None, name
            size = n[0] * 16
            meta = r.read(size) if size else b""
        m = re.search(rb"StreamTitle='(.*?)';", meta, re.S)
        title = _decode(m.group(1)).strip() if m else ""
        return title, _decode(name.encode("latin-1", "ignore")) if name else ""
