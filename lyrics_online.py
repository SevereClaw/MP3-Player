# -*- coding: utf-8 -*-
"""Синхронные тексты песен с lrclib.net (бесплатно, без ключа).

Сначала точный запрос (исполнитель + название + альбом + длительность), потом поиск.
Берём только текст с таймкодами: простой текст без времени на экране «караоке»
не покажешь. Найденное сохраняем обычным .lrc — дальше плеер (и Music → Discord)
берут его как свой файл, без интернета."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
UA = "MP3Player (https://github.com/SevereClaw/MP3-Player)"
_TS = re.compile(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")


def _get(path, params, timeout=8):
    url = f"{API}/{path}?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _norm(s):
    s = re.sub(r"[\(\[].*?[\)\]]", " ", (s or "").casefold()).replace("ё", "е")
    return re.sub(r"[^\w]+", " ", s).strip()


def _synced(item):
    t = (item or {}).get("syncedLyrics") or ""
    return t if _TS.search(t) else ""


def clean_title(title):
    """«01. Песня (Official Video) [HD]» → «Песня» — для повторного поиска."""
    t = re.sub(r"^\s*\d{1,3}\s*[-.)]\s*", "", title or "")
    t = re.sub(r"[\(\[][^\)\]]*(official|video|audio|lyric|hd|hq|remaster|клип|текст)[^\)\]]*[\)\]]",
               " ", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


def fetch(artist, title, album="", duration_s=0):
    """→ (текст LRC или None, пояснение)."""
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not title:
        return None, "нет названия трека"
    plain = False
    # 1. точное совпадение
    if artist:
        item = _get("get", {"artist_name": artist, "track_name": title, "album_name": album,
                            "duration": int(duration_s) if duration_s else None})
        if item and item.get("instrumental"):
            return None, "инструментал — без слов"
        if _synced(item):
            return _synced(item), "lrclib.net"
        plain = plain or bool(item and item.get("plainLyrics"))
    # 2. поиск: исполнитель + название, потом очищенное название
    tries = []
    for t in dict.fromkeys([title, clean_title(title)]):
        if t:
            tries.append({"track_name": t, "artist_name": artist})
            tries.append({"q": f"{artist} {t}".strip()})
    want_t, want_a = _norm(clean_title(title)), _norm(artist)
    for params in tries:
        res = _get("search", params) or []
        best, best_score = None, -1
        for it in res:
            if not _synced(it):
                plain = plain or bool(it.get("plainLyrics"))
                continue
            name, who = _norm(it.get("trackName")), _norm(it.get("artistName"))
            if want_t and not (want_t in name or name in want_t):
                continue
            score = 0
            if want_a and (want_a in who or who in want_a):
                score += 2
            if album and _norm(album) and _norm(album) in _norm(it.get("albumName")):
                score += 1
            if duration_s and it.get("duration"):
                diff = abs(float(it["duration"]) - duration_s)
                if diff > 8:
                    continue                      # другая версия трека — тайминги не совпадут
                score += 2 if diff <= 2 else 1
            if score > best_score:
                best, best_score = it, score
        if best:
            return _synced(best), "lrclib.net (поиск)"
    return None, ("есть только текст без таймкодов" if plain else "текст не найден")


def save(lrc_text, track_path, artist, title, album="", fallback_dir=None):
    """Сохранить рядом с треком («Трек.lrc»); если в папку писать нельзя — в fallback_dir.
    → путь к файлу."""
    head = []
    for tag, val in (("ar", artist), ("ti", title), ("al", album)):
        if val:
            head.append(f"[{tag}:{val}]")
    head.append("[re:lrclib.net]")
    body = "\n".join(head) + "\n" + lrc_text.strip() + "\n"
    stem = os.path.splitext(os.path.basename(track_path))[0]
    targets = [os.path.join(os.path.dirname(track_path), stem + ".lrc")]
    if fallback_dir:
        targets.append(os.path.join(fallback_dir, stem + ".lrc"))
    last = None
    for p in targets:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.exists(p):                 # свой .lrc не перезаписываем
                return p
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            return p
        except OSError as e:
            last = e
    raise last or OSError("не удалось сохранить .lrc")
