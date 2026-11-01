# -*- coding: utf-8 -*-
"""Итоги недели: что больше всего слушал — карточкой в личные сообщения Discord
(бот пишет тебе сам) и/или уведомлением Windows.

Бот — обычное приложение Discord с ботом (discord.com/developers). Ему нужны только
токен и твой ID; бот и ты должны быть на одном сервере (хоть на своём пустом).
Шлём через REST API: открыть личку (POST /users/@me/channels) и отправить
сообщение (POST /channels/{id}/messages) — без постоянного подключения."""
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/SevereClaw/MP3-Player, 1.0) MP3Player"
DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "нояб", "дек"]


# ===================================================================== подсчёт
def _artist_of(path, t):
    a = (t or {}).get("artist") or ""
    if not a:
        stem = os.path.splitext(os.path.basename(path))[0]
        a = stem.split(" - ", 1)[0].strip() if " - " in stem else ""
    return a


def _title_of(path, t):
    title = (t or {}).get("title")
    if title:
        return title
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.split(" - ", 1)[1].strip() if " - " in stem else stem


def week_report(data, end=None):
    """Итоги за 7 дней, заканчивая днём end (включительно). data — Stats.data."""
    end = end or dt.date.today()
    start = end - dt.timedelta(days=6)
    daily = data.get("daily") or {}
    meta = data.get("tracks") or {}
    tracks, artists, days = {}, {}, []
    for i in range(7):
        d = start + dt.timedelta(days=i)
        sec = float((data.get("days") or {}).get(d.isoformat(), 0.0))
        days.append((d, sec))
        for path, v in (daily.get(d.isoformat()) or {}).items():
            t = tracks.setdefault(path, [0, 0.0])
            t[0] += int(v.get("p", 0))
            t[1] += float(v.get("s", 0.0))
    for path, (p, s) in tracks.items():
        a = _artist_of(path, meta.get(path))
        if a:
            x = artists.setdefault(a, [0, 0.0])
            x[0] += p
            x[1] += s
    prev_total = sum(float((data.get("days") or {}).get((start - dt.timedelta(days=i)).isoformat(), 0.0))
                     for i in range(1, 8))
    top = sorted(tracks.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)
    best_day = max(days, key=lambda x: x[1]) if days else (end, 0)
    return {
        "start": start, "end": end,
        "total": sum(s for _, s in days), "prev_total": prev_total,
        "plays": sum(p for p, _ in tracks.values()),
        "distinct": sum(1 for p, s in tracks.values() if s > 30),
        "top_tracks": [(_title_of(pth, meta.get(pth)), _artist_of(pth, meta.get(pth)), p, s)
                       for pth, (p, s) in top[:5] if s > 0],
        "top_artists": sorted(((a, p, s) for a, (p, s) in artists.items()),
                              key=lambda x: (x[2], x[1]), reverse=True)[:3],
        "days": days, "best_day": best_day,
    }


def hm(sec):
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин" if m else f"{sec} с"


def period(r):
    s, e = r["start"], r["end"]
    if s.month == e.month:
        return f"{s.day}–{e.day} {MONTHS[e.month - 1]}"
    return f"{s.day} {MONTHS[s.month - 1]} – {e.day} {MONTHS[e.month - 1]}"


def plays_word(n):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "раз"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "раза"
    return "раз"


# ===================================================================== карточка
def build_embed(r, thumb=None):
    if r["total"] < 60:
        return {"title": f"🎧 Итоги недели · {period(r)}",
                "description": "На этой неделе музыки почти не было. Самое время что-нибудь включить 🙂",
                "color": 0x96ABA4, "footer": {"text": "MP3 Плеер"}}
    diff = r["total"] - r["prev_total"]
    cmp = ""
    if r["prev_total"] >= 60:
        cmp = f" ({'+' if diff >= 0 else '−'}{hm(abs(diff))} к прошлой неделе)"
    desc = (f"Слушал **{hm(r['total'])}**{cmp}\n"
            f"Включений: **{r['plays']}** · разных треков: **{r['distinct']}**")
    fields = []
    if r["top_tracks"]:
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        lines = []
        for i, (title, artist, p, s) in enumerate(r["top_tracks"]):
            who = f" — {artist}" if artist else ""
            lines.append(f"{medals[i]} **{title[:60]}**{who[:40]}\n {p} {plays_word(p)} · {hm(s)}")
        fields.append({"name": "Треки недели", "value": "\n".join(lines)[:1024], "inline": False})
    if r["top_artists"]:
        lines = [f"**{a[:50]}** — {hm(s)}" for a, p, s in r["top_artists"]]
        fields.append({"name": "Исполнители", "value": "\n".join(lines)[:1024], "inline": True})
    mx = max((s for _, s in r["days"]), default=0) or 1
    bars = []
    for d, s in r["days"]:
        n = int(round(8 * s / mx)) if s else 0
        bars.append(f"`{DAYS_SHORT[d.weekday()]} {'█' * n}{'·' * (8 - n)}` {hm(s) if s else '—'}")
    fields.append({"name": "По дням", "value": "\n".join(bars)[:1024], "inline": True})
    bd, bs = r["best_day"]
    if bs > 0:
        fields.append({"name": "Самый музыкальный день",
                       "value": f"{DAYS[bd.weekday()]}, {bd.day} {MONTHS[bd.month - 1]} — {hm(bs)}",
                       "inline": False})
    e = {"title": f"🎧 Итоги недели · {period(r)}", "description": desc, "color": 0x96ABA4,
         "fields": fields, "footer": {"text": "MP3 Плеер"}}
    if thumb:
        e["thumbnail"] = {"url": thumb}
    return e


def short_text(r):
    """Для уведомления Windows."""
    if r["total"] < 60:
        return "На этой неделе музыки почти не было."
    t = f"Слушал {hm(r['total'])}, включений {r['plays']}."
    if r["top_tracks"]:
        title, artist, p, _s = r["top_tracks"][0]
        t += f"\nТрек недели: {title}" + (f" — {artist}" if artist else "") + f" ({p} {plays_word(p)})"
    if r["top_artists"]:
        t += f"\nИсполнитель недели: {r['top_artists'][0][0]}"
    return t


def itunes_art(artist, title):
    """Обложка трека недели для карточки (iTunes) или None."""
    try:
        q = urllib.parse.quote(f"{artist} {title}".strip())
        with urllib.request.urlopen(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1",
                                    timeout=6) as r:
            res = json.loads(r.read().decode()).get("results") or []
        if res and res[0].get("artworkUrl100"):
            return res[0]["artworkUrl100"].replace("100x100", "300x300")
    except Exception:
        pass
    return None


# ===================================================================== отправка
class DiscordError(Exception):
    pass


def _call(method, path, token, body=None, proxy=None, timeout=15):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"Authorization": "Bot " + token.strip(), "User-Agent": UA,
                                          "Content-Type": "application/json"})
    opener = urllib.request.build_opener(
        *( [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        hints = {400: "Discord не принял твой ID — проверь его (и что бот с тобой на одном сервере)",
                 401: "неверный токен бота",
                 403: "бот не может тебе написать: добавь его на сервер, где есть ты, "
                      "и проверь, что в настройках приватности сервера разрешены личные сообщения",
                 404: "не нашёл пользователя — проверь свой ID",
                 429: "Discord просит подождать — попробуй через минуту"}
        raise DiscordError(hints.get(e.code, f"Discord ответил {e.code}") + (f" ({msg})" if msg else ""))


def send_dm(token, user_id, embed, content="", proxy=None):
    """Открыть личку с пользователем и отправить карточку."""
    uid = str(user_id).strip()
    if not token.strip() or not uid.isdigit():
        raise DiscordError("нужны токен бота и твой ID (только цифры)")
    ch = _call("POST", "/users/@me/channels", token, {"recipient_id": uid}, proxy)
    if not ch.get("id"):
        raise DiscordError("Discord не открыл личные сообщения")
    _call("POST", f"/channels/{ch['id']}/messages", token,
          {"content": content, "embeds": [embed], "allowed_mentions": {"parse": []}}, proxy)


# ===================================================================== когда отправлять
def last_due(now, weekday, hour, minute):
    """Самый поздний момент «день недели + время», не позже now."""
    d = now.date() - dt.timedelta(days=(now.weekday() - weekday) % 7)
    t = dt.datetime.combine(d, dt.time(hour, minute))
    if t > now:
        t -= dt.timedelta(days=7)
    return t


# ===================================================================== токен (шифруем средствами Windows)
def protect(text):
    """Зашифровать для текущего пользователя Windows (DPAPI). Не Windows — base64."""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            enc = _dpapi(raw, True)
            return "dpapi:" + base64.b64encode(enc).decode()
        except Exception:
            pass
    return "b64:" + base64.b64encode(raw).decode()


def unprotect(stored):
    if not stored:
        return ""
    try:
        kind, data = stored.split(":", 1)
        raw = base64.b64decode(data)
        if kind == "dpapi":
            raw = _dpapi(raw, False)
        return raw.decode("utf-8")
    except Exception:
        return ""


def _dpapi(data, encrypt):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
