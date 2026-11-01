# -*- coding: utf-8 -*-
"""Тексты песен (.lrc): поиск файла для трека и разбор таймкодов.

Где ищем (по порядку):
  1. ручная привязка «трек → .lrc» (меню или перетаскивание .lrc на плеер);
  2. рядом с треком и в его подпапке Lyrics/lyrics — то же имя;
  3. в «папках с текстами» (с подпапками) — по имени: регистр, ё/е, номер трека,
     скобки «(Reload)», знаки препинания не мешают;
  4. там же — по тегам [ar:] / [ti:] внутри .lrc.
"""
import bisect
import os
import re
import time

_TS = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_WORD_TS = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")       # расширенный LRC: <00:12.34>
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
_TAG = re.compile(r"^\[(ar|ti):(.*)\]\s*$", re.IGNORECASE | re.MULTILINE)


class Lyrics:
    def __init__(self, times, lines, source=""):
        self.times = times          # мс, по возрастанию
        self.lines = lines
        self.source = source        # откуда взят текст (путь к .lrc)

    def index_at(self, ms):
        return bisect.bisect_right(self.times, ms) - 1

    def __len__(self):
        return len(self.times)


# ------------------------------------------------------------------ чтение
def _read_text(path, limit=None):
    with open(path, "rb") as f:
        raw = f.read(limit) if limit else f.read()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):     # русские .lrc часто в Windows-1251
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_lrc(text, extra_offset_ms=0, source=""):
    offset = 0
    m = _OFFSET.search(text)
    if m:
        offset = int(m.group(1))
    offset += extra_offset_ms              # свой сдвиг: + — текст раньше
    pairs = []
    for raw in text.splitlines():
        stamps = _TS.findall(raw)
        if not stamps:
            continue
        line = _TS.sub("", raw)
        line = _WORD_TS.sub("", line).strip()
        for mm, ss in stamps:
            ss = ss.replace(":", ".")
            ms = int(mm) * 60000 + round(float(ss) * 1000)
            pairs.append((max(0, ms - offset), line))
    if not pairs:
        return None
    pairs.sort(key=lambda t: t[0])
    return Lyrics([t for t, _ in pairs], [l for _, l in pairs], source)


# ------------------------------------------------------------------ сравнение имён
def norm(s):
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[\W_]+", "", s)


def _no_brackets(s):
    return re.sub(r"\s*[\(\[\{].*?[\)\]\}]\s*", " ", s or "").strip()


def _no_number(s):
    return re.sub(r"^\s*\d{1,3}\s*[\.\-_)]?\s*", "", s or "")


def name_keys(stem, title="", artist=""):
    """Варианты имени трека для сравнения (от точного к свободному)."""
    keys = []
    for base in (stem, _no_number(stem)):
        keys += [norm(base), norm(_no_brackets(base))]
        if " - " in base:                                   # «Исполнитель - Название»
            a, t = base.split(" - ", 1)
            keys += [norm(t), norm(_no_brackets(t)), norm(t + a)]
    if title:
        keys += [norm(artist + title), norm(title), norm(_no_brackets(title)),
                 norm(artist + _no_brackets(title))]
    out = []
    for k in keys:
        if k and len(k) >= 2 and k not in out:
            out.append(k)
    return out


# ------------------------------------------------------------------ индекс папок
class LyricsIndex:
    def __init__(self):
        self.dirs = ()
        self.by_name = {}        # норм. имя -> путь
        self.by_tags = None      # норм. (исполнитель+название) / название -> путь; строится по требованию
        self.files = []
        self.built = 0.0

    def ensure(self, dirs, max_age=60.0):
        dirs = tuple(d for d in dirs if d and os.path.isdir(d))
        if dirs != self.dirs or time.monotonic() - self.built > max_age:
            self.build(dirs)

    def build(self, dirs):
        self.dirs = dirs
        self.by_name, self.by_tags, self.files = {}, None, []
        for d in dirs:
            for root, _sub, names in os.walk(d):
                for n in names:
                    stem, ext = os.path.splitext(n)
                    if ext.lower() != ".lrc":
                        continue
                    p = os.path.join(root, n)
                    self.files.append(p)
                    for k in name_keys(stem):
                        self.by_name.setdefault(k, p)
        self.built = time.monotonic()

    def _tags(self):
        if self.by_tags is None:
            self.by_tags = {}
            for p in self.files:
                try:
                    head = _read_text(p, 4096)
                except OSError:
                    continue
                tags = {k.lower(): v.strip() for k, v in _TAG.findall(head)}
                ti, ar = tags.get("ti", ""), tags.get("ar", "")
                if ti:
                    for k in (norm(ar + ti), norm(ar + _no_brackets(ti)), norm(ti), norm(_no_brackets(ti))):
                        if k:
                            self.by_tags.setdefault(k, p)
        return self.by_tags

    def find(self, stem, title="", artist=""):
        keys = name_keys(stem, title, artist)
        for k in keys:
            if k in self.by_name:
                return self.by_name[k]
        tags = self._tags()
        for k in keys:
            if k in tags:
                return tags[k]
        return None


INDEX = LyricsIndex()


def find_lrc(track_path, links=None, dirs=(), title="", artist=""):
    """Путь к .lrc для трека или None."""
    if links:
        key = os.path.normcase(os.path.normpath(track_path))
        for t, p in links.items():
            if os.path.normcase(os.path.normpath(t)) == key and p and os.path.isfile(p):
                return p
    folder = os.path.dirname(track_path)
    stem = os.path.splitext(os.path.basename(track_path))[0]
    low = stem.lower()
    for d in (folder, os.path.join(folder, "Lyrics"), os.path.join(folder, "lyrics")):
        if not os.path.isdir(d):
            continue
        try:
            for name in os.listdir(d):
                n, ext = os.path.splitext(name)
                if ext.lower() == ".lrc" and n.lower() == low:
                    return os.path.join(d, name)
        except OSError:
            pass
    if dirs:
        INDEX.ensure(dirs)
        return INDEX.find(stem, title, artist)
    return None


def load_for_track(track_path, links=None, dirs=(), title="", artist="", offset_ms=0):
    p = find_lrc(track_path, links, dirs, title, artist)
    if not p:
        return None
    return load_file(p, offset_ms)


def load_file(path, offset_ms=0):
    try:
        return parse_lrc(_read_text(path), offset_ms, path)
    except Exception as e:
        print("lrc:", e)
        return None
