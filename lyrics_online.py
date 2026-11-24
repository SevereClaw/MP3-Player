# -*- coding: utf-8 -*-
"""Синхронные тексты песен с lrclib.net и lyricsify.com (бесплатно, без ключа).

lrclib.net: сначала точный запрос (исполнитель + название + альбом + длительность), потом поиск.
Не нашлось — lyricsify.com: у него поиск закрыт капчей, поэтому открываем страницу песни прямо
по адресу /lyrics/исполнитель/название. Сайт под защитой Cloudflare и иногда не пускает
программы — тогда он просто пропускается.
Берём только текст с таймкодами: простой текст без времени на экране «караоке»
не покажешь. Найденное сохраняем обычным .lrc — дальше плеер (и Music → Discord)
берут его как свой файл, без интернета."""
import html as htmlmod
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
UA = "MP3Player (https://github.com/SevereClaw/MP3-Player)"
_TS = re.compile(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")


def _get(path, params, timeout=10):
    url = f"{API}/{path}?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 or attempt == 2:          # сайт перегружен — пара повторов
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(1.5 * (attempt + 1))


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


# ------------------------------------------------------------------ lyricsify.com
LSY = "https://www.lyricsify.com/lyrics/{}/{}"
LSY_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def lyricsify_slug(s):
    """«Don't Stop (feat. X)» → «dont-stop-feat-x»: как адреса страниц lyricsify.com."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\'!?\"/.&,()\[\]:;*#@+]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def _lyricsify_page(url, timeout=10):
    """→ (HTML или None, код ответа). 404 — нет страницы; 403/503 — не пустил Cloudflare."""
    req = urllib.request.Request(url, headers=LSY_HDR)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        return None, e.code


def lyricsify_extract(page):
    """Текст с таймкодами из страницы песни или ''."""
    m = re.search(r'details">(.*?)</div', page or "", re.S)
    if not m:
        return ""
    body = re.sub(r"<br\s*/?>", "\n", m.group(1), flags=re.I)
    body = htmlmod.unescape(re.sub(r"<[^<]+?>", "", body))
    lines = [ln.strip() for ln in body.replace("\r", "").split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    return text if len(_TS.findall(text)) >= 3 else ""


def fetch_lyricsify(artist, title):
    """→ (текст LRC или None, почему не нашлось). Пробует пару вариантов адреса
    (с «feat.» и без, очищенное название). Поиска у сайта нет (капча), поэтому без исполнителя никак."""
    artist, title = (artist or "").strip(), (title or "").strip()
    if not title:
        return None, "нет названия"
    if not artist or artist.lower() in ("неизвестный исполнитель", "unknown artist", "unknown"):
        return None, "у трека не указан исполнитель — без него страницу не найти"
    artists = dict.fromkeys([artist, re.split(r"\s*,\s*|\s+(?:feat\.?|ft\.?|featuring|&|x)\s+", artist, flags=re.I)[0]])
    titles = dict.fromkeys([title, clean_title(title), re.sub(r"\s*[\(\[].*?[\)\]]", "", clean_title(title))])
    tried, codes = [], []
    for a in artists:
        for t in titles:
            url = LSY.format(lyricsify_slug(a), lyricsify_slug(t))
            if url in tried or not lyricsify_slug(a) or not lyricsify_slug(t):
                continue
            tried.append(url)
            page, code = _lyricsify_page(url)
            codes.append(code)
            if code in (403, 429, 503) or (page and ("Just a moment" in page or "challenge-platform" in page)):
                return None, f"Cloudflare не пустил программу ({code})"
            text = lyricsify_extract(page)
            if text:
                return text, ""
            if len(tried) >= 4:                     # не долбим сайт десятком запросов
                break
    if codes and all(c == 404 for c in codes):
        return None, "страницы песни нет (" + tried[0].split("/lyrics/", 1)[1] + ")"
    return None, "на странице нет текста с таймкодами"


def fetch(artist, title, album="", duration_s=0):
    """→ (текст LRC или None, пояснение). Сначала lrclib.net, потом lyricsify.com.
    Оба сайта недоступны (нет сети) — исключение от lrclib, как раньше."""
    err = None
    try:
        text, why = fetch_lrclib(artist, title, album, duration_s)
    except Exception as e:
        text, why, err = None, "", e
    if text or why == "инструментал — без слов":
        return text, why
    try:
        lsy, lwhy = fetch_lyricsify(artist, title)
    except Exception as e:
        lsy, lwhy = None, f"нет связи ({type(e).__name__})"
    if lsy:
        return lsy, "lyricsify.com"
    if err is not None:
        raise err
    return None, f"lrclib: {why or 'не найден'} · lyricsify: {lwhy}"


def fetch_lrclib(artist, title, album="", duration_s=0):
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


def save(lrc_text, track_path, artist, title, album="", fallback_dir=None, source="lrclib.net"):
    """Сохранить рядом с треком («Трек.lrc»); если в папку писать нельзя — в fallback_dir.
    → путь к файлу."""
    head = []
    for tag, val in (("ar", artist), ("ti", title), ("al", album)):
        if val:
            head.append(f"[{tag}:{val}]")
    head.append(f"[re:{source}]")
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
