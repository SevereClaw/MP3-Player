# -*- coding: utf-8 -*-
"""Яндекс Музыка и Spotify: плейлисты по ссылке, вход в Яндекс, загрузка треков.

Яндекс — через тот же API, что у их приложений (неофициальный: может однажды поменяться).
Spotify свою музыку не отдаёт — у него берём только список песен плейлиста/альбома
(со встраиваемой страницы, без ключей), а сами песни ищем у себя на диске или в Яндексе.
"""
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

API = "https://api.music.yandex.net"
CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"            # приложение Яндекс Музыки
AUTH_URL = f"https://oauth.yandex.ru/authorize?response_type=token&client_id={CLIENT_ID}"
UA = "MP3Player (desktop music player; https://github.com/SevereClaw/MP3-Player)"
SIGN_SALT = "XGRlBW9FXlekgbPrRHuSiA"


class YError(Exception):
    pass


class NeedLogin(YError):
    pass


def token_from_text(text):
    """Токен из вставленного адреса «…#access_token=XXX&…» или сам токен."""
    text = (text or "").strip()
    m = re.search(r"access_token=([^&\s#]+)", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_\-.]{20,}", text) else ""


def _http(url, data=None, headers=None, timeout=15):
    h = {"User-Agent": UA, "X-Yandex-Music-Client": "WindowsPhone/3.20"}
    h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NeedLogin("нужно войти в Яндекс Музыку") from e
        if e.code == 451:
            raise YError("Яндекс Музыка недоступна из этой страны. Если включён VPN — пусти "
                         "music.yandex.ru и api.music.yandex.net мимо него") from e
        raise


# ------------------------------------------------------------------ трек → словарь
def track_info(t):
    """Трек из ответа API → {id, title, artist, album, duration, cover, available}."""
    if not isinstance(t, dict):
        return None
    if "track" in t and isinstance(t["track"], dict):
        t = t["track"]
    title = (t.get("title") or "").strip()
    if t.get("version"):
        title += f" ({t['version'].strip()})"
    arts = ", ".join(a.get("name", "") for a in (t.get("artists") or []) if a.get("name"))
    albums = t.get("albums") or []
    al = albums[0] if albums else {}
    cover = t.get("coverUri") or al.get("coverUri") or ""
    tid = str(t.get("id") or t.get("realId") or "")
    if al.get("id") and ":" not in tid:
        tid_full = f"{tid}:{al['id']}"
    else:
        tid_full = tid
    return {"id": tid, "key": tid_full, "title": title, "artist": arts, "album": al.get("title") or "",
            "duration": (t.get("durationMs") or 0) / 1000.0,
            "cover": ("https://" + cover.replace("%%", "600x600")) if cover else "",
            "available": t.get("available", True) is not False, "src": "yandex"}


# ------------------------------------------------------------------ клиент
class Client:
    def __init__(self, token=""):
        self.token = token or ""
        self._uid = None

    def _get(self, path, params=None, data=None, auth=True):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = "OAuth " + self.token
        raw = _http(url, data=data, headers=headers)
        d = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(d, dict) and d.get("error"):
            err = d["error"]
            raise YError(err.get("message") if isinstance(err, dict) else str(err))
        return d.get("result") if isinstance(d, dict) else d

    # --- аккаунт
    def me(self):
        if not self.token:
            raise NeedLogin("нужно войти в Яндекс Музыку")
        acc = (self._get("/account/status") or {}).get("account") or {}
        if not acc.get("uid"):
            raise NeedLogin("токен не подходит — войди заново")
        self._uid = acc["uid"]
        return {"uid": acc["uid"], "login": acc.get("login") or "",
                "name": acc.get("displayName") or acc.get("fullName") or acc.get("login") or ""}

    def uid(self):
        if self._uid is None:
            self.me()
        return self._uid

    # --- списки
    def tracks(self, ids):
        out = []
        ids = [str(i) for i in ids]
        for i in range(0, len(ids), 200):
            res = self._get("/tracks", data={"track-ids": ",".join(ids[i:i + 200]), "with-positions": "false"})
            out += [x for x in (track_info(t) for t in res or []) if x]
        return out

    def liked(self):
        lib = (self._get(f"/users/{self.uid()}/likes/tracks") or {}).get("library") or {}
        ids = [f"{t['id']}:{t['albumId']}" if t.get("albumId") else str(t["id"]) for t in lib.get("tracks") or []]
        return self.tracks(ids)

    def playlists(self):
        res = self._get(f"/users/{self.uid()}/playlists/list") or []
        out = []
        for p in res:
            co = p.get("cover") if isinstance(p.get("cover"), dict) else {}
            cov = co.get("uri") or (co.get("itemsUri") or [""])[0] or p.get("ogImage") or ""
            out.append({"owner": (p.get("owner") or {}).get("uid") or self.uid(), "kind": p.get("kind"),
                        "title": p.get("title") or "Плейлист", "count": p.get("trackCount") or 0,
                        "cover": ("https://" + cov.replace("%%", "400x400")) if cov else ""})
        return out

    def _playlist_tracks(self, res):
        items = res.get("tracks") or []
        full = [x for x in (track_info(t) for t in items) if x and x["title"]]
        if len(full) < len(items):              # иногда приходят только id — дозапросить
            ids = [f"{t.get('id')}:{t.get('albumId')}" if t.get("albumId") else str(t.get("id"))
                   for t in items if t.get("id")]
            full = self.tracks(ids)
        return {"title": res.get("title") or "Плейлист", "tracks": full}

    def playlist(self, owner, kind):
        return self._playlist_tracks(self._get(f"/users/{owner}/playlists/{kind}") or {})

    def playlist_uuid(self, uuid):
        return self._playlist_tracks(self._get(f"/playlist/{uuid}") or {})

    def album(self, aid):
        res = self._get(f"/albums/{aid}/with-tracks") or {}
        tracks = [x for vol in res.get("volumes") or [] for x in (track_info(t) for t in vol) if x]
        arts = ", ".join(a.get("name", "") for a in res.get("artists") or [])
        return {"title": f"{arts} — {res.get('title', 'Альбом')}" if arts else res.get("title", "Альбом"),
                "tracks": tracks}

    def search(self, text):
        res = self._get("/search", {"text": text, "type": "track", "page": 0, "nocorrect": "false"}) or {}
        return [x for x in (track_info(t) for t in ((res.get("tracks") or {}).get("results") or [])) if x]

    def wave(self, last_id=None):
        """«Моя волна» Яндекса — следующая пачка треков."""
        params = {"settings2": "true"}
        if last_id:
            params["queue"] = last_id
        res = self._get("/rotor/station/user:onyourwave/tracks", params) or {}
        return [x for x in (track_info(s.get("track")) for s in res.get("sequence") or []) if x]

    # --- скачивание
    def download_url(self, track_id):
        tid = str(track_id).split(":")[0]
        infos = self._get(f"/tracks/{tid}/download-info") or []
        mp3 = [i for i in infos if i.get("codec") == "mp3"] or infos
        if not mp3:
            raise YError("трек недоступен")
        best = max(mp3, key=lambda i: (not i.get("preview"), i.get("bitrateInKbps") or 0))
        url = best["downloadInfoUrl"]
        d = json.loads(_http(url + ("&" if "?" in url else "?") + "format=json").decode("utf-8", "replace"))
        path = d["path"]
        sign = hashlib.md5((SIGN_SALT + path[1:] + d["s"]).encode("utf-8")).hexdigest()
        return f"https://{d['host']}/get-mp3/{sign}/{d['ts']}{path}", bool(best.get("preview"))


def safe_name(s, limit=80):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s or "").strip(" .")
    return re.sub(r"\s+", " ", s)[:limit].strip(" .") or "трек"


# ------------------------------------------------------------------ кэш: одна папка
# Треки лежат прямо в <кэш>\Исполнитель - Название.mp3, а какой файл — какой трек Яндекса,
# помнит <кэш>_index.json рядом (не внутри папки с музыкой).
import threading as _th
_idx_lock = _th.Lock()


def _index_file(cache_dir):
    return cache_dir.rstrip("\\/") + "_index.json"


def load_index(cache_dir):
    try:
        with open(_index_file(cache_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_index(cache_dir, idx):
    f = _index_file(cache_dir)
    try:
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(idx, fh, ensure_ascii=False)
        os.replace(f + ".tmp", f)
    except OSError:
        pass


def cache_path(cache_dir, info, idx=None):
    """Куда положить трек: <кэш>/Исполнитель - Название.mp3 (занято другим треком — с номером)."""
    stem = safe_name(f"{info.get('artist') or 'Неизвестный'} - {info.get('title') or 'трек'}", 120)
    taken = {v.lower() for k, v in (idx or {}).items() if k != str(info["id"])}
    name = stem + ".mp3"
    n = 2
    while name.lower() in taken or os.path.exists(os.path.join(cache_dir, name)):
        name = f"{stem} ({n}).mp3"
        n += 1
    return os.path.join(cache_dir, name)


def cached(cache_dir, info):
    name = load_index(cache_dir).get(str(info.get("id")))
    if name:
        p = os.path.join(cache_dir, name)
        try:
            if os.path.getsize(p) > 50_000:
                return p
        except OSError:
            pass
    return None


def migrate_cache(cache_dir):
    """Сборка 48 клала каждый трек в свою папку <кэш>/<id>/… — переносим всё в одну папку.
    Возвращает {старый путь: новый}."""
    moved = {}
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return moved
    with _idx_lock:
        idx = load_index(cache_dir)
        for d in names:
            sub = os.path.join(cache_dir, d)
            if not (d.isdigit() and os.path.isdir(sub)):
                continue
            for n in os.listdir(sub):
                old = os.path.join(sub, n)
                if not n.lower().endswith(".mp3") or str(d) in idx:
                    continue
                stem = n[:-4]
                info = {"id": d, "artist": stem.split(" - ", 1)[0] if " - " in stem else "",
                        "title": stem.split(" - ", 1)[1] if " - " in stem else stem}
                new = cache_path(cache_dir, info, idx)
                try:
                    os.replace(old, new)
                except OSError:
                    continue
                idx[str(d)] = os.path.basename(new)
                moved[os.path.normpath(old)] = os.path.normpath(new)
            try:
                os.rmdir(sub)
            except OSError:
                pass
        save_index(cache_dir, idx)
    return moved


def download(client, info, cache_dir, progress=None):
    """Скачать трек (с тегами и обложкой) → путь к .mp3. Уже скачан — сразу путь."""
    have = cached(cache_dir, info)
    if have:
        return have
    url, preview = client.download_url(info["id"])
    if preview and not client.token:
        raise NeedLogin("без входа Яндекс даёт только 30 секунд — войди в аккаунт")
    os.makedirs(cache_dir, exist_ok=True)
    tmp_dir = cache_dir.rstrip("\\/") + "_tmp"              # не внутри медиатеки: недокачанное не попадёт в список
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, safe_name(str(info["id"]), 40) + ".mp3")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)                  # может бросить исключение — тогда отмена
    cover = None
    if info.get("cover"):
        try:
            cover = _http(info["cover"], timeout=10)
        except Exception:
            cover = None
    try:
        import library
        library.write_tags(tmp, info.get("title"), info.get("artist"), info.get("album"), cover=cover)
    except Exception:
        pass
    with _idx_lock:                                   # имя выбираем в самом конце: параллельные загрузки
        idx = load_index(cache_dir)
        target = cache_path(cache_dir, info, idx)
        os.replace(tmp, target)
        idx[str(info["id"])] = os.path.basename(target)
        save_index(cache_dir, idx)
    return target


# ------------------------------------------------------------------ ссылки
def parse_link(url):
    """Ссылка → (тип, данные) или None. Типы: ym_playlist, ym_uuid, ym_album, ym_track, sp_playlist,
    sp_album, sp_track."""
    u = (url or "").strip()
    m = re.search(r"music\.yandex\.\w+/users/([^/\s]+)/playlists/(\d+)", u)
    if m:
        return "ym_playlist", (urllib.parse.unquote(m.group(1)), m.group(2))
    m = re.search(r"music\.yandex\.\w+/playlists/([\w\-.]+)", u)
    if m:
        return "ym_uuid", m.group(1)
    m = re.search(r"music\.yandex\.\w+/album/(\d+)/track/(\d+)", u)
    if m:
        return "ym_track", f"{m.group(2)}:{m.group(1)}"
    m = re.search(r"music\.yandex\.\w+/album/(\d+)", u)
    if m:
        return "ym_album", m.group(1)
    m = re.search(r"music\.yandex\.\w+/track/(\d+)", u)
    if m:
        return "ym_track", m.group(1)
    m = re.search(r"open\.spotify\.com/(?:intl-\w+/)?(?:embed/)?(playlist|album|track)/([A-Za-z0-9]+)", u) \
        or re.search(r"spotify:(playlist|album|track):([A-Za-z0-9]+)", u)
    if m:
        return "sp_" + m.group(1), m.group(2)
    return None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def spotify_list(kind, sid):
    """Песни плейлиста / альбома / трека Spotify (со встраиваемой страницы, без входа)."""
    html = _http(f"https://open.spotify.com/embed/{kind}/{sid}",
                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                          "Accept-Language": "ru,en;q=0.8"}).decode("utf-8", "replace")
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise YError("Spotify не отдал список песен")
    data = json.loads(m.group(1))
    ent = _find_key(data, "entity") or {}
    items = ent.get("trackList") or []
    out = []
    if not items and kind == "track" and ent.get("name"):
        items = [{"title": ent.get("name"),
                  "subtitle": ", ".join(a.get("name", "") for a in ent.get("artists") or []),
                  "duration": ent.get("duration")}]
    for it in items:
        title = (it.get("title") or "").strip()
        artist = re.sub(r"\s+", " ", (it.get("subtitle") or "").replace(" ", " ")).strip()
        if title:
            out.append({"id": "", "title": title, "artist": artist, "album": "",
                        "duration": (it.get("duration") or 0) / 1000.0, "cover": "", "src": "spotify"})
    name = ent.get("name") or ent.get("title") or "Spotify"
    return {"title": name, "tracks": out}


def text_list(text):
    """Список песен из текста / CSV (Exportify): «Исполнитель - Название» по строке."""
    out = []
    lines = (text or "").splitlines()
    if lines and "track name" in lines[0].lower() and "," in lines[0]:
        import csv
        rows = list(csv.reader(lines))
        head = [h.strip().lower() for h in rows[0]]
        ti = head.index("track name")
        ai = next((i for i, h in enumerate(head) if h.startswith("artist name")), None)
        for r in rows[1:]:
            if len(r) > ti and r[ti].strip():
                out.append({"id": "", "title": r[ti].strip(),
                            "artist": r[ai].replace(";", ", ").strip() if ai is not None and len(r) > ai else "",
                            "album": "", "duration": 0, "cover": "", "src": "text"})
        return out
    for ln in lines:
        ln = re.sub(r"^\s*\d+[.)]\s*", "", ln).strip()
        if not ln:
            continue
        m = re.split(r"\s+[-–—]\s+", ln, maxsplit=1)
        a, t = (m[0], m[1]) if len(m) == 2 else ("", ln)
        out.append({"id": "", "title": t.strip(), "artist": a.strip(), "album": "", "duration": 0,
                    "cover": "", "src": "text"})
    return out


def fetch_list(link, client=None):
    """Ссылка → {title, tracks:[…]} (Яндекс или Spotify)."""
    p = parse_link(link)
    if not p:
        raise YError("не похоже на ссылку Яндекс Музыки или Spotify")
    kind, val = p
    c = client or Client()
    if kind == "ym_playlist":
        return c.playlist(*val)
    if kind == "ym_uuid":
        return c.playlist_uuid(val)
    if kind == "ym_album":
        return c.album(val)
    if kind == "ym_track":
        tr = c.tracks([val])
        return {"title": tr[0]["title"] if tr else "Трек", "tracks": tr}
    return spotify_list(kind[3:], val)


# ------------------------------------------------------------------ сопоставление с диском
def _key(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"\b(feat|ft)\.?\b.*", " ", s)
    return re.sub(r"[^\w]+", "", s)


def _artists(s):
    return {_key(x) for x in re.split(r",|&|\bfeat\.?|\bft\.?|\bи\b| x ", s or "", flags=re.I) if _key(x)}


class Matcher:
    """Ищет песни у пользователя на диске по исполнителю и названию."""

    def __init__(self, items):
        import library
        self.by_title = {}
        for path, it in items.items():
            title, artist, _al = library.track_view(path, it)
            k = _key(title)
            if k:
                self.by_title.setdefault(k, []).append((path, _artists(artist), it.get("d") or 0))

    def find(self, info):
        k = _key(info.get("title"))
        cands = self.by_title.get(k) or []
        if not cands:
            return None
        want = _artists(info.get("artist"))
        dur = info.get("duration") or 0
        best, score = None, -1
        for path, arts, d in cands:
            s = 0
            if want and arts and (want & arts or any(a in b or b in a for a in want for b in arts if len(a) > 2 and len(b) > 2)):
                s += 4
            elif want and arts:
                continue                                     # другой исполнитель с тем же названием
            if dur and d and abs(dur - d) <= 4:
                s += 2
            if s > score:
                best, score = path, s
        return best


def resolve(client, info):
    """Песня без id Яндекса (из Spotify или списка) → такой же трек в Яндекс Музыке или None."""
    if info.get("id"):
        return info
    want_t, want_a = _key(info.get("title")), _artists(info.get("artist"))
    if not want_t:
        return None
    res = client.search(f"{info.get('artist', '')} {info.get('title', '')}".strip())
    if not res and info.get("artist"):
        res = client.search(info.get("title"))
    loose = None
    for r in res[:15]:
        rt = _key(r["title"])
        same = rt == want_t or (len(want_t) > 3 and want_t in rt)
        if not r.get("available", True) or not same:
            continue
        arts = _artists(r["artist"])
        if not want_a or want_a & arts:
            return r
        loose = loose or (r if not want_a else None)
    return loose
