# -*- coding: utf-8 -*-
"""Вся музыка из папок плеера: поиск по паре букв и правка тегов (название, исполнитель,
альбом, обложка).

Теги читает mutagen. Чтобы не перечитывать тысячи файлов при каждом запуске, индекс
хранится в library.json рядом с настройками: путь → размер, время изменения, теги.
"""
import base64
import json
import os
import re
import threading
import time

AUDIO_EXT = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a",
             ".aac", ".wma", ".aiff", ".aif", ".mka"}


# ------------------------------------------------------------------ теги
def _first(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v).strip()


def read_tags(path):
    """{'title', 'artist', 'album', 'cover': bytes|None, 'cover_mime'} — что есть в файле."""
    out = {"title": "", "artist": "", "album": "", "cover": None, "cover_mime": "", "track": 0, "length": 0.0}
    try:
        import mutagen
        f = mutagen.File(path)
    except Exception:
        return out
    if f is None:
        return out
    try:
        out["length"] = float(getattr(f.info, "length", 0) or 0)
    except Exception:
        pass
    tags = f.tags
    ext = os.path.splitext(path)[1].lower()
    try:
        if tags is not None and hasattr(tags, "getall") and ext in (".mp3", ".wav", ".aiff", ".aif"):   # ID3
            for key, k in (("title", "TIT2"), ("artist", "TPE1"), ("album", "TALB")):
                fr = tags.get(k)
                out[key] = str(fr.text[0]).strip() if fr is not None and fr.text else ""
            if not out["artist"] and tags.get("TPE2") is not None:
                out["artist"] = str(tags.get("TPE2").text[0]).strip()
            if tags.get("TRCK") is not None and tags.get("TRCK").text:
                out["track"] = _num(tags.get("TRCK").text[0])
            pics = tags.getall("APIC")
            if pics:
                pic = next((p for p in pics if p.type == 3), pics[0])
                out["cover"], out["cover_mime"] = pic.data, pic.mime
        elif ext in (".m4a", ".aac") and tags is not None:                                  # MP4
            for key, k in (("title", "\xa9nam"), ("artist", "\xa9ART"), ("album", "\xa9alb")):
                out[key] = _first(tags.get(k))
            if not out["artist"]:
                out["artist"] = _first(tags.get("aART"))
            trkn = tags.get("trkn")
            if trkn:
                out["track"] = _num(trkn[0][0] if isinstance(trkn[0], tuple) else trkn[0])
            covr = tags.get("covr")
            if covr:
                c = covr[0]
                out["cover"] = bytes(c)
                out["cover_mime"] = "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
        elif tags is not None:                                                             # FLAC / Ogg / Opus / прочие
            for key, k in (("title", "title"), ("artist", "artist"), ("album", "album")):
                out[key] = _first(tags.get(k))
            if not out["artist"]:
                out["artist"] = _first(tags.get("albumartist"))
            out["track"] = _num(_first(tags.get("tracknumber")))
            pics = list(getattr(f, "pictures", []) or [])
            if not pics and tags.get("metadata_block_picture"):
                from mutagen.flac import Picture
                for b64 in tags.get("metadata_block_picture"):
                    try:
                        pics.append(Picture(base64.b64decode(b64)))
                    except Exception:
                        pass
            if pics:
                pic = next((p for p in pics if p.type == 3), pics[0])
                out["cover"], out["cover_mime"] = pic.data, pic.mime
    except Exception:
        pass
    return out


def _num(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def _mime_of(data):
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def write_tags(path, title, artist, album, cover=None, remove_cover=False):
    """Записать теги в файл. cover — байты картинки (jpeg/png) или None (не трогать).
    Бросает исключение, если формат не поддерживается или файл занят."""
    import mutagen
    ext = os.path.splitext(path)[1].lower()
    title, artist, album = (title or "").strip(), (artist or "").strip(), (album or "").strip()

    if ext == ".mp3" or ext in (".wav", ".aiff", ".aif"):
        from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, APIC
        if ext == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            target = None
        else:
            f = mutagen.File(path)
            if f.tags is None:
                f.add_tags()
            tags, target = f.tags, f
        for cls, key, val in ((TIT2, "TIT2", title), (TPE1, "TPE1", artist), (TALB, "TALB", album)):
            tags.delall(key)
            if val:
                tags.add(cls(encoding=3, text=val))
        if remove_cover or cover:
            tags.delall("APIC")
        if cover:
            tags.add(APIC(encoding=3, mime=_mime_of(cover), type=3, desc="Cover", data=cover))
        if target is None:
            tags.save(path, v2_version=3)            # ID3 2.3 — его понимает и Проводник Windows
        else:
            target.save()
        return

    if ext in (".m4a", ".aac"):
        from mutagen.mp4 import MP4, MP4Cover
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        for key, val in (("\xa9nam", title), ("\xa9ART", artist), ("\xa9alb", album)):
            if val:
                f.tags[key] = [val]
            else:
                f.tags.pop(key, None)
        if remove_cover:
            f.tags.pop("covr", None)
        if cover:
            fmt = MP4Cover.FORMAT_PNG if _mime_of(cover) == "image/png" else MP4Cover.FORMAT_JPEG
            f.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
        f.save()
        return

    f = mutagen.File(path)
    if f is None:
        raise ValueError("этот формат не поддерживается")
    if f.tags is None:
        f.add_tags()
    if not hasattr(f.tags, "__setitem__") or hasattr(f.tags, "getall"):
        raise ValueError("в этом формате теги не поддерживаются")
    for key, val in (("title", title), ("artist", artist), ("album", album)):
        if val:
            f.tags[key] = [val]
        elif key in f.tags:
            del f.tags[key]
    if ext == ".flac":
        from mutagen.flac import Picture
        if remove_cover or cover:
            f.clear_pictures()
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, _mime_of(cover), "Cover", cover
            f.add_picture(pic)
    elif remove_cover or cover:                         # Ogg Vorbis / Opus
        from mutagen.flac import Picture
        if "metadata_block_picture" in f.tags:
            del f.tags["metadata_block_picture"]
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, _mime_of(cover), "Cover", cover
            f.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    f.save()


def can_write(path):
    return os.path.splitext(path)[1].lower() in {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a",
                                                 ".aac", ".wav", ".aiff", ".aif"}


def guess_from_name(path):
    """«Кино - Группа крови.mp3» → ('Кино', 'Группа крови'); номер в начале отбрасывается."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"^\s*\d{1,3}\s*[.\-_)]\s*", "", stem)
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return "", stem.strip()


# ------------------------------------------------------------------ поиск
def norm(s):
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[\s_\-–—.,()\[\]{}!?'\"«»:;/\\]+", " ", s).strip()


def _entry(p, size, mtime):
    t = read_tags(p)
    return {"s": size, "m": mtime, "t": t["title"], "a": t["artist"], "al": t["album"],
            "n": t["track"], "d": round(t["length"], 1)}


# ------------------------------------------------------------------ альбомы и исполнители
UNKNOWN_ARTIST = "Неизвестный исполнитель"
VARIOUS = "Разные исполнители"


def track_view(path, it):
    """Что показывать для трека: название, исполнитель, альбом (пустые теги — из имени файла и папки)."""
    ga, gt = guess_from_name(path)
    title = it.get("t") or gt
    artist = it.get("a") or ga or UNKNOWN_ARTIST
    album = it.get("al") or os.path.basename(os.path.dirname(path)) or "Без альбома"
    return title, artist, album


def build_albums(items):
    """[{'key','title','artist','tracks':[path…],'cover_path','duration'}] — альбомы по тегу «альбом»
    (нет тега — по папке). Треки — по номеру, потом по имени файла."""
    groups = {}
    for p, it in items.items():
        title, artist, album = track_view(p, it)
        key = (norm(album), os.path.normcase(os.path.dirname(p)) if not it.get("al") else norm(album))
        g = groups.setdefault(key, {"key": "|".join(key), "title": album, "artists": {}, "tracks": [],
                                    "duration": 0.0})
        g["tracks"].append(p)
        g["artists"][artist] = g["artists"].get(artist, 0) + 1
        g["duration"] += it.get("d") or 0
    out = []
    for g in groups.values():
        arts = sorted(g.pop("artists").items(), key=lambda kv: -kv[1])
        n = len(g["tracks"])
        g["artist"] = arts[0][0] if arts and (len(arts) == 1 or arts[0][1] * 2 > n) else VARIOUS
        g["tracks"].sort(key=lambda p: (items[p].get("n") or 9999, _natural(os.path.basename(p))))
        g["cover_path"] = g["tracks"][0]
        out.append(g)
    out.sort(key=lambda g: (norm(g["artist"]) if g["artist"] != VARIOUS else "яяя", norm(g["title"])))
    return out


def build_artists(items, albums=None):
    """[{'name','tracks':[…],'albums':[album…],'cover_path'}] — по алфавиту."""
    albums = albums if albums is not None else build_albums(items)
    by = {}
    for p, it in items.items():
        _t, artist, _al = track_view(p, it)
        a = by.setdefault(norm(artist), {"name": artist, "tracks": [], "albums": []})
        a["tracks"].append(p)
    for al in albums:
        seen = set()
        for p in al["tracks"]:
            k = norm(track_view(p, items[p])[1])
            if k in by and k not in seen:
                seen.add(k)
                by[k]["albums"].append(al)
    out = []
    for a in by.values():
        a["tracks"].sort(key=lambda p: (norm(track_view(p, items[p])[2]), items[p].get("n") or 9999,
                                        _natural(os.path.basename(p))))
        a["cover_path"] = a["albums"][0]["cover_path"] if a["albums"] else a["tracks"][0]
        out.append(a)
    out.sort(key=lambda a: (a["name"] == UNKNOWN_ARTIST, norm(a["name"])))
    return out


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class Library:
    """Индекс всех треков из папок плеера. Скан — в фоне, поиск — мгновенный."""

    def __init__(self, cache_path):
        self.cache_path = cache_path
        self.items = {}                 # путь → {"s": размер, "m": mtime, "t", "a", "al"}
        self.lock = threading.Lock()
        self.scanning = False
        self.scanned_at = 0.0
        self._hay = {}                  # путь → строка для поиска
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                self.items = json.load(f).get("items", {})
        except Exception:
            self.items = {}
        self._rebuild_hay()

    def _rebuild_hay(self):
        hay = {}
        for p, it in self.items.items():
            folder = os.path.basename(os.path.dirname(p))
            stem = os.path.splitext(os.path.basename(p))[0]
            hay[p] = " " + norm(" ".join((it.get("t", ""), it.get("a", ""), it.get("al", ""), stem, folder)))
        self._hay = hay

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with self.lock:
                data = {"items": self.items}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def scan(self, folders, done=None):
        """Пройти папки в фоне: новые и изменённые файлы — прочитать теги, удалённые — забыть."""
        if self.scanning:
            return
        self.scanning = True

        def work():
            try:
                seen = {}
                for root_dir in folders:
                    if not os.path.isdir(root_dir):
                        continue
                    for root, _dirs, files in os.walk(root_dir):
                        for n in files:
                            if os.path.splitext(n)[1].lower() not in AUDIO_EXT:
                                continue
                            p = os.path.normpath(os.path.join(root, n))
                            try:
                                st = os.stat(p)
                            except OSError:
                                continue
                            seen[p] = (st.st_size, int(st.st_mtime))
                fresh = {}
                with self.lock:
                    old = dict(self.items)
                for p, (size, mtime) in seen.items():
                    it = old.get(p)
                    if it and it.get("s") == size and it.get("m") == mtime and "d" in it:
                        fresh[p] = it
                        continue
                    fresh[p] = _entry(p, size, mtime)
                with self.lock:
                    self.items = fresh
                    self._rebuild_hay()
                self.scanned_at = time.time()
                self.save()
            finally:
                self.scanning = False
                if done:
                    done()

        threading.Thread(target=work, daemon=True).start()

    def update(self, path):
        """Перечитать один файл (после правки тегов)."""
        p = os.path.normpath(path)
        try:
            st = os.stat(p)
        except OSError:
            return
        with self.lock:
            self.items[p] = _entry(p, st.st_size, int(st.st_mtime))
            self._rebuild_hay()
        self.save()

    def __len__(self):
        return len(self.items)

    def info(self, path):
        it = self.items.get(os.path.normpath(path)) or {}
        return it.get("t", ""), it.get("a", ""), it.get("al", "")

    def search(self, query, limit=300):
        """Треки, где есть все слова запроса (в названии, исполнителе, альбоме, имени файла
        или папки). Совпадения с начала слова — выше."""
        words = norm(query).split()
        if not words:
            return []
        scored = []
        with self.lock:
            hay = self._hay
            for p, h in hay.items():
                score = 0
                for w in words:
                    i = h.find(w)
                    if i < 0:
                        break
                    score += 2 if h[i - 1] == " " else 1
                else:
                    scored.append((-score, len(h), p))
        scored.sort()
        return [p for _s, _l, p in scored[:limit]]
