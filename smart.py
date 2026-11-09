# -*- coding: utf-8 -*-
"""Умные плейлисты — собираются сами по статистике и по папкам.

  «Часто слушаю»       — больше всего включений, свежие включения весят больше;
  «Любимое за месяц»   — что слушал дольше всего за последние 30 дней;
  «Давно не слушал»    — когда-то слушал не раз, а последние 30 дней — нет;
  «Добавлено недавно»  — самые новые файлы в «Моих папках».
"""
import datetime as dt
import os

LISTS = (
    ("often", "🔥  Часто слушаю"),
    ("month", "💜  Любимое за месяц"),
    ("forgotten", "🕰  Давно не слушал"),
    ("recent", "🆕  Добавлено недавно"),
)
NAMES = dict(LISTS)
LIMIT = 50


def _recent(data, days, today=None):
    """{путь: [включения, секунды]} за последние days дней."""
    today = today or dt.date.today()
    out = {}
    daily = data.get("daily") or {}
    for i in range(days):
        for path, v in (daily.get((today - dt.timedelta(days=i)).isoformat()) or {}).items():
            x = out.setdefault(path, [0, 0.0])
            x[0] += int(v.get("p", 0))
            x[1] += float(v.get("s", 0.0))
    return out


def last_played(data, path):
    """Дата последнего прослушивания (date) или None."""
    t = (data.get("tracks") or {}).get(path) or {}
    if t.get("last"):
        try:
            return dt.date.fromisoformat(t["last"])
        except ValueError:
            pass
    days = [d for d, items in (data.get("daily") or {}).items() if path in items]
    return dt.date.fromisoformat(max(days)) if days else None


def often(data, exists=os.path.isfile, today=None):
    rec = _recent(data, 30, today)
    rows = []
    for path, t in (data.get("tracks") or {}).items():
        plays = int(t.get("plays") or 0)
        if plays < 2 or not exists(path):
            continue
        score = plays + 2 * rec.get(path, [0])[0]
        rows.append((score, float(t.get("sec") or 0), path))
    rows.sort(reverse=True)
    return [p for _s, _sec, p in rows[:LIMIT]]


def month(data, exists=os.path.isfile, today=None):
    rec = _recent(data, 30, today)
    rows = [(s + 60 * p, path) for path, (p, s) in rec.items() if (s >= 60 or p >= 1) and exists(path)]
    rows.sort(reverse=True)
    return [p for _s, p in rows[:LIMIT]]


def forgotten(data, exists=os.path.isfile, today=None):
    today = today or dt.date.today()
    rec = _recent(data, 30, today)
    rows = []
    for path, t in (data.get("tracks") or {}).items():
        plays = int(t.get("plays") or 0)
        if plays < 2 or path in rec or not exists(path):
            continue
        last = last_played(data, path)
        if last and (today - last).days < 30:
            continue
        rows.append((plays, float(t.get("sec") or 0), path))
    rows.sort(reverse=True)                       # самые любимые из забытых — первыми
    return [path for _p, _s, path in rows[:LIMIT]]


def recent(folders, audio_ext, limit=LIMIT):
    """Самые новые файлы в папках (по дате появления файла на диске)."""
    found = []
    for root_dir in folders:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in audio_ext:
                    p = os.path.join(root, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    # на Windows st_ctime — когда файл появился в папке (скачали / скопировали)
                    found.append((max(st.st_ctime, st.st_mtime), os.path.normpath(p)))
    found.sort(reverse=True)
    seen, out = set(), []
    for _t, p in found:
        k = os.path.normcase(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
        if len(out) >= limit:
            break
    return out


def build(kind, data, folders, audio_ext):
    if kind == "often":
        return often(data)
    if kind == "month":
        return month(data)
    if kind == "forgotten":
        return forgotten(data)
    if kind == "recent":
        return recent(folders, audio_ext)
    return []
