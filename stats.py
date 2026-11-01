# -*- coding: utf-8 -*-
"""Статистика прослушивания: сколько слушал каждый трек и каждый день."""
import datetime as _dt
import json
import os

DAY_NAMES = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


def fmt_dur(sec):
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    if h:
        return f"{h}Ч {m:02d}М"
    if m:
        return f"{m}М"
    return f"{sec}С"


class Stats:
    def __init__(self, path):
        self.path = path
        self.data = {"tracks": {}, "days": {}, "daily": {}, "since": _dt.date.today().isoformat()}
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update(d)
        except Exception:
            pass
        self.dirty = False

    # ------------------------------------------------------------ запись
    def add_time(self, track, sec, title="", artist="", today=None):
        if not track or sec <= 0:
            return
        t = self.data["tracks"].setdefault(track, {"sec": 0.0, "plays": 0})
        t["sec"] = t.get("sec", 0.0) + sec
        if title:
            t["title"] = title
        if artist:
            t["artist"] = artist
        day = (today or _dt.date.today()).isoformat()
        self.data["days"][day] = self.data["days"].get(day, 0.0) + sec
        d = self._daily(day, track)                  # для итогов недели: что слушал в этот день
        d["s"] = round(d.get("s", 0.0) + sec, 1)
        self.dirty = True

    def _daily(self, day, track):
        return self.data.setdefault("daily", {}).setdefault(day, {}).setdefault(track, {})

    def add_play(self, track):
        if track:
            t = self.data["tracks"].setdefault(track, {"sec": 0.0, "plays": 0})
            t["plays"] = t.get("plays", 0) + 1
            d = self._daily(_dt.date.today().isoformat(), track)
            d["p"] = d.get("p", 0) + 1
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        days = self.data["days"]
        for k in sorted(days)[:-120]:            # хватит последних 4 месяцев
            days.pop(k, None)
        daily = self.data.get("daily") or {}
        for k in sorted(daily)[:-70]:            # потреково — последние 10 недель
            daily.pop(k, None)
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
        except Exception as e:
            print("stats save:", e)

    # ------------------------------------------------------------ итоги
    def week(self, today=None):
        """[(подпись дня, секунды, это сегодня?)] за последние 7 дней."""
        today = today or _dt.date.today()
        out = []
        for i in range(6, -1, -1):
            d = today - _dt.timedelta(days=i)
            out.append((DAY_NAMES[d.weekday()], self.data["days"].get(d.isoformat(), 0.0), i == 0))
        return out

    def week_total(self, today=None):
        return sum(s for _, s, _ in self.week(today))

    def _name(self, path, t):
        title = t.get("title") or os.path.splitext(os.path.basename(path))[0]
        return title

    def top_tracks(self, n=6):
        items = sorted(self.data["tracks"].items(),
                       key=lambda kv: (kv[1].get("plays", 0), kv[1].get("sec", 0)), reverse=True)
        return [(self._name(p, t), t.get("artist", ""), t.get("plays", 0), t.get("sec", 0.0))
                for p, t in items[:n] if t.get("sec", 0) > 0]

    def top_artists(self, n=3):
        agg = {}
        for p, t in self.data["tracks"].items():
            a = t.get("artist") or ""
            if not a:
                stem = os.path.splitext(os.path.basename(p))[0]
                a = stem.split(" - ", 1)[0].strip() if " - " in stem else ""
            if a:
                s = agg.setdefault(a, [0, 0.0])
                s[0] += t.get("plays", 0)
                s[1] += t.get("sec", 0.0)
        items = sorted(agg.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)
        return [(a, v[0], v[1]) for a, v in items[:n]]

    def totals(self, today=None):
        today = today or _dt.date.today()
        tr = self.data["tracks"]
        return {
            "all_sec": sum(t.get("sec", 0.0) for t in tr.values()),
            "plays": sum(t.get("plays", 0) for t in tr.values()),
            "tracks": sum(1 for t in tr.values() if t.get("plays", 0) > 0),
            "today": self.data["days"].get(today.isoformat(), 0.0),
            "since": self.data.get("since", ""),
        }
