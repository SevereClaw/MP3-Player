# -*- coding: utf-8 -*-
"""Обложки альбомов и фото исполнителей из интернета — для музыки, у которой своих картинок нет.

Источники без ключей и регистрации: iTunes Search (обложки) и Deezer (обложки и фото
исполнителей). Совпадение проверяется по названию, чтобы не подставить чужую картинку.
"""
import json
import re
import time
import urllib.parse
import urllib.request

UA = "MP3Player (desktop music player; https://github.com/SevereClaw/MP3-Player)"
_last = [0.0]


def _get_json(url, timeout=8):
    wait = 0.25 - (time.monotonic() - _last[0])      # не чаще 4 запросов в секунду
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get_bytes(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Offline(IOError):
    """Сеть недоступна — не запоминать «не нашлось», попробовать в другой раз."""


def _try(fns):
    """Пробовать источники по очереди; если все упали по сети — Offline."""
    errors = 0
    for fn in fns:
        try:
            u = fn()
            if u:
                return u
        except (OSError, ValueError):
            errors += 1
        except Exception:
            pass
    if errors == len(fns):
        raise Offline("нет связи")
    return None


def norm(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)                       # (Remastered), [Deluxe]
    s = re.sub(r"\b(deluxe|remaster(ed)?|edition|version|expanded|bonus|ost|soundtrack)\b", " ", s)
    return re.sub(r"[^\w]+", " ", s).strip()


def similar(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ca, cb = a.replace(" ", ""), b.replace(" ", "")              # «Persona 3» = «PERSONA3»
    if ca == cb or (len(ca) > 4 and ca in cb) or (len(cb) > 4 and cb in ca):
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.6


def _cjk(s):
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", s or ""))


def _itunes(term, entity, ok, country="US"):
    q = {"term": term, "entity": entity, "limit": 10, "country": country}
    if country == "JP":
        q["lang"] = "ja_jp"
    d = _get_json("https://itunes.apple.com/search?" + urllib.parse.urlencode(q))
    for r in d.get("results", []):
        if ok(r) and r.get("artworkUrl100"):
            return r["artworkUrl100"].replace("100x100bb", "600x600bb")
    return None


def _deezer(kind, q, ok, pick):
    d = _get_json(f"https://api.deezer.com/search{kind}?" + urllib.parse.urlencode({"q": q, "limit": 10}))
    for r in d.get("data", []):
        if ok(r):
            u = pick(r)
            if u:
                return u
    return None


def album_cover_url(artist, album):
    """Ссылка на обложку альбома (600×600) или None. Нет сети — Offline."""
    if not album:
        return None
    term = f"{artist} {album}" if artist else album
    ok_it = lambda r: similar(r.get("collectionName"), album) and (not artist or similar(r.get("artistName"), artist))
    ok_dz = lambda r: similar(r.get("title"), album) and (not artist or similar((r.get("artist") or {}).get("name"), artist))
    q = f'artist:"{artist}" album:"{album}"' if artist else album
    # без исполнителя — только при точном совпадении названия альбома (саундтреки, сборники)
    exact = lambda name: norm(name).replace(" ", "") == norm(album).replace(" ", "") and len(norm(album)) > 6
    srcs = [lambda: _itunes(term, "album", ok_it),
            lambda: _deezer("/album", q, ok_dz, lambda r: r.get("cover_xl")),
            lambda: _deezer("/album", album, lambda r: exact(r.get("title")), lambda r: r.get("cover_xl")),
            lambda: _itunes(album, "album", lambda r: exact(r.get("collectionName")))]
    if _cjk(album) or _cjk(artist):
        srcs.insert(1, lambda: _itunes(term, "album", ok_it, "JP"))
    return _try(srcs)


def track_cover_url(artist, title):
    """Обложка по треку (когда у файла нет тега «альбом»)."""
    if not artist or not title:
        return None
    ok_it = lambda r: similar(r.get("trackName"), title) and similar(r.get("artistName"), artist)
    ok_dz = lambda r: similar(r.get("title"), title) and similar((r.get("artist") or {}).get("name"), artist)
    srcs = [lambda: _itunes(f"{artist} {title}", "song", ok_it),
            lambda: _deezer("", f'artist:"{artist}" track:"{title}"', ok_dz,
                            lambda r: (r.get("album") or {}).get("cover_xl")),
            lambda: _deezer("", f"{artist} {title}", ok_dz, lambda r: (r.get("album") or {}).get("cover_xl"))]
    if _cjk(title) or _cjk(artist):
        srcs.insert(0, lambda: _itunes(f"{artist} {title}", "song", ok_it, "JP"))     # японские названия
    return _try(srcs)


def artist_photo_url(name):
    """Фото исполнителя (Deezer) или None."""
    if not name:
        return None
    ok = lambda r: norm(r.get("name")) == norm(name) or (similar(r.get("name"), name) and len(norm(name)) > 3)

    def pick(r):
        u = r.get("picture_xl") or r.get("picture_big") or ""
        return u if u and "/artist//" not in u else None          # пустая картинка-заглушка Deezer
    return _try([lambda: _deezer("/artist", name, ok, pick)])
