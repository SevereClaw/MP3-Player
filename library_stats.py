# -*- coding: utf-8 -*-
"""Медиатека, часть 3: страница «Итоги» с графиками и поиск дубликатов."""
import datetime as _dt
import os

from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QPixmap, QPen, QLinearGradient, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame, QToolTip,
                               QMessageBox)

import library as libmod
from library_ui import (P3Chip, P3Action, pfont, skew_poly, fmt_ms, fmt_total, placeholder, cover_pixmap, sfx,
                        NAVY, NAVY2, DEEP, CYAN, CYAN_D, ICE, WHITE, DIM)

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


def human_time(sec):
    sec = int(sec or 0)
    h, m = sec // 3600, (sec % 3600) // 60
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин" if m else f"{sec} с"


def _scroll():
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
    return sc


# ---------------------------------------------------------------- графики
class DayChart(QWidget):
    """Минуты музыки по дням — скошенные столбики, как шкалы в P3R."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.days = []                 # [(date, sec)]
        self.hover = -1

    def set_days(self, days):
        self.days = days
        self.update()

    def _bars(self):
        n = max(1, len(self.days))
        left, right, top, bottom = 46, self.width() - 10, 14, self.height() - 28
        step = (right - left) / n
        bw = max(3.0, step * 0.72)
        mx = max([s for _d, s in self.days] + [60])
        out = []
        for i, (d, s) in enumerate(self.days):
            h = (bottom - top) * (s / mx)
            out.append(QRectF(left + i * step + (step - bw) / 2, bottom - h, bw, h))
        return out, mx, top, bottom, left, right

    def mouseMoveEvent(self, e):
        bars, *_ = self._bars()
        x = e.position().x()
        h = next((i for i, r in enumerate(bars) if r.left() - 2 <= x <= r.right() + 2), -1)
        if h != self.hover:
            self.hover = h
            self.update()
            if h >= 0:
                d, s = self.days[h]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{WD[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}: {human_time(s)}", self)

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bars, mx, top, bottom, left, right = self._bars()
        # сетка: минуты
        p.setFont(pfont(11, bold=False))
        for k in range(0, 5):
            y = bottom - (bottom - top) * k / 4
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.drawLine(QPointF(left, y), QPointF(right, y))
            p.setPen(DIM)
            p.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{int(mx * k / 4 / 60)}м")
        for i, r in enumerate(bars):
            d, s = self.days[i]
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            hot = i == self.hover
            g.setColorAt(0, WHITE if hot else ICE)
            g.setColorAt(1, CYAN if hot else CYAN_D)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            if r.height() > 1:
                p.drawPolygon(skew_poly(r, min(r.width() * 0.5, 6)))
            # подписи дней: у недели — все, у месяца — каждый 5-й и понедельники
            lab = len(self.days) <= 14 or d.day == 1 or (len(self.days) <= 40 and d.weekday() == 0)
            if lab:
                p.setFont(pfont(10, bold=d.weekday() >= 5))
                p.setPen(CYAN if d.weekday() >= 5 else DIM)
                txt = WD[d.weekday()] if len(self.days) <= 7 else f"{d.day}.{d.month:02d}"
                p.drawText(QRectF(r.center().x() - 24, bottom + 4, 48, 18), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class RankList(QWidget):
    """Топ исполнителей / треков: место, картинка, имя и полоса «сколько времени»."""
    clicked = Signal(object)

    ROW = 50

    def __init__(self, win, round_art=False):
        super().__init__()
        self.win = win
        self.round_art = round_art
        self.rows = []                 # [{"title","sub","sec","plays","cover_path","artist","path"}]
        self.setMouseTracking(True)
        self.hover = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_rows(self, rows):
        self.rows = rows
        self.setFixedHeight(max(40, len(rows) * self.ROW + 4))
        self.update()

    def mouseMoveEvent(self, e):
        h = int(e.position().y() // self.ROW)
        h = h if 0 <= h < len(self.rows) else -1
        if h != self.hover:
            self.hover = h
            if h >= 0:
                sfx("hover")
            self.update()

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        if 0 <= self.hover < len(self.rows):
            sfx("select")
            self.clicked.emit(self.rows[self.hover])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.rows:
            p.setFont(pfont(13, bold=False))
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "  пока пусто")
            return
        mx = max(r["sec"] for r in self.rows) or 1
        w = self.width()
        for i, r in enumerate(self.rows):
            y = i * self.ROW
            row = QRectF(0, y + 3, w - 6, self.ROW - 6)
            if i == self.hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(47, 211, 255, 35))
                p.drawPolygon(skew_poly(row, 10))
            p.setFont(pfont(20))
            p.setPen(CYAN if i < 3 else DIM)
            p.drawText(QRectF(0, y, 34, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(i + 1))
            art = QRectF(44, y + 5, self.ROW - 10, self.ROW - 10)
            img = self.win.covers.get_artist(r["artist"]) if self.round_art and r.get("artist") else None
            pm = (QPixmap.fromImage(img.scaled(int(art.width()), int(art.height()), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                               Qt.TransformationMode.SmoothTransformation)).copy(0, 0, int(art.width()), int(art.height()))
                  if img is not None else cover_pixmap(self.win.covers, r.get("cover_path"), int(art.width()), r["title"]))
            p.drawPixmap(art.toRect(), pm)
            x = art.right() + 14
            tw = w * 0.42
            p.setFont(pfont(15))
            p.setPen(WHITE)
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(x, y + 4, tw, 24), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(r["title"], Qt.TextElideMode.ElideRight, int(tw)))
            p.setFont(pfont(12, bold=False))
            p.setPen(DIM)
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(x, y + 26, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(r.get("sub", ""), Qt.TextElideMode.ElideRight, int(tw)))
            bx = x + tw + 12
            bw = max(10.0, w - bx - 120)
            bar = QRectF(bx, y + self.ROW / 2 - 7, bw * r["sec"] / mx, 14)
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            if bar.width() > 2:
                p.drawPolygon(skew_poly(bar, 6))
            p.setFont(pfont(13))
            p.setPen(ICE)
            p.drawText(QRectF(w - 116, y, 108, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       human_time(r["sec"]))
        p.end()


class StatCard(QWidget):
    def __init__(self, title):
        super().__init__()
        self.title, self.value, self.sub = title, "—", ""
        self.setFixedHeight(96)
        self.setMinimumWidth(150)

    def set(self, value, sub=""):
        self.value, self.sub = value, sub
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 4, -8, -4)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(47, 211, 255, 50))
        g.setColorAt(1, QColor(11, 34, 87, 120))
        p.setBrush(g)
        p.setPen(QPen(CYAN_D, 1.5))
        p.drawPolygon(skew_poly(r, 14))
        p.setFont(pfont(12))
        p.setPen(CYAN)
        p.drawText(QRectF(r.left() + 22, r.top() + 8, r.width() - 30, 18), Qt.AlignmentFlag.AlignLeft, self.title)
        f = pfont(28)
        fm = QFontMetrics(f)
        while fm.horizontalAdvance(self.value) > r.width() - 34 and f.pixelSize() > 14:
            f.setPixelSize(f.pixelSize() - 2)
            fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(WHITE)
        p.drawText(QRectF(r.left() + 18, r.top() + 26, r.width() - 30, 38), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.value, Qt.TextElideMode.ElideRight, int(r.width() - 34)))
        p.setFont(pfont(11, bold=False))
        p.setPen(DIM)
        p.drawText(QRectF(r.left() + 14, r.top() + 64, r.width() - 30, 18), Qt.AlignmentFlag.AlignLeft, self.sub)
        p.end()


# ---------------------------------------------------------------- страница «Итоги»
class StatsPage(QWidget):
    PERIODS = (("week", "НЕДЕЛЯ", 7), ("month", "МЕСЯЦ", 30), ("year", "ГОД", 365), ("all", "ВСЁ ВРЕМЯ", 0))

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.o = win.owner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 6, 0)
        outer.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel("ИТОГИ")
        t.setObjectName("h1")
        head.addWidget(t)
        head.addStretch(1)
        self.chips = {}
        for key, name, _d in self.PERIODS:
            c = P3Chip(name)
            c.clicked.connect(lambda _=False, k=key: self.set_period(k))
            head.addWidget(c)
            self.chips[key] = c
        outer.addLayout(head)
        sc = _scroll()
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 18, 24)
        lay.setSpacing(8)
        sc.setWidget(body)
        outer.addWidget(sc, 1)

        cards = QHBoxLayout()
        cards.setSpacing(6)
        self.c_time = StatCard("СЛУШАЛ")
        self.c_tracks = StatCard("ТРЕКОВ")
        self.c_artists = StatCard("ИСПОЛНИТЕЛЕЙ")
        self.c_best = StatCard("САМЫЙ МУЗЫКАЛЬНЫЙ ДЕНЬ")
        for c in (self.c_time, self.c_tracks, self.c_artists, self.c_best):
            cards.addWidget(c)
        lay.addLayout(cards)

        def sec(title):
            lb = QLabel(title)
            lb.setObjectName("sec")
            lay.addSpacing(8)
            lay.addWidget(lb)
            return lb
        self.days_title = sec("ПО ДНЯМ")
        self.chart = DayChart()
        lay.addWidget(self.chart)
        sec("ТОП ИСПОЛНИТЕЛЕЙ")
        self.artists = RankList(win, round_art=True)
        self.artists.clicked.connect(self._open_artist)
        lay.addWidget(self.artists)
        sec("ТОП ТРЕКОВ")
        self.tracks = RankList(win)
        self.tracks.clicked.connect(lambda r: r.get("path") and self.win.play_paths(
            [x["path"] for x in self.tracks.rows if x.get("path")], r["path"]))
        lay.addWidget(self.tracks)
        row = QHBoxLayout()
        b = P3Action("📊 ИТОГИ НЕДЕЛИ В DISCORD…")
        b.setFixedWidth(280)
        b.clicked.connect(lambda: getattr(self.o, "open_weekly", lambda: None)())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    def set_period(self, key):
        self.o.cfg["stats_period"] = key
        self.refresh()

    def _artist_of(self, path, t):
        it = self.win.items.get(path)
        if it is not None:
            return libmod.track_view(path, it)[1]
        if t.get("artist"):
            return t["artist"]
        ga, _gt = libmod.guess_from_name(path)
        return ga or libmod.UNKNOWN_ARTIST

    def refresh(self):
        key = self.o.cfg.get("stats_period", "month")
        for k, c in self.chips.items():
            c.setChecked(k == key)
        n = dict((k, d) for k, _nm, d in self.PERIODS)[key]
        st = getattr(self.o, "stats", None)
        data = st.data if st is not None else {}
        tracks = data.get("tracks") or {}
        days_sec = data.get("days") or {}
        daily = data.get("daily") or {}
        today = _dt.date.today()
        if n:
            start = today - _dt.timedelta(days=n - 1)
        else:
            start = min([_dt.date.fromisoformat(d) for d in days_sec] or [today])
        # по дням (для «года» и «всего» — по неделям, чтобы столбики не были нитками)
        span = (today - start).days + 1
        days = []
        if span <= 62:
            for k in range(span):
                d = start + _dt.timedelta(days=k)
                days.append((d, float(days_sec.get(d.isoformat(), 0.0))))
            self.days_title.setText("ПО ДНЯМ")
        else:
            wk = start - _dt.timedelta(days=start.weekday())
            while wk <= today:
                s = sum(float(days_sec.get((wk + _dt.timedelta(days=j)).isoformat(), 0.0)) for j in range(7))
                days.append((wk, s))
                wk += _dt.timedelta(days=7)
            self.days_title.setText("ПО НЕДЕЛЯМ")
        self.chart.set_days(days)

        # что слушал за период: по трекам
        per = {}
        if n:
            for k in range(n):
                for path, d in (daily.get((today - _dt.timedelta(days=k)).isoformat()) or {}).items():
                    v = per.setdefault(path, [0.0, 0])
                    v[0] += float(d.get("s", 0.0))
                    v[1] += int(d.get("p", 0))
        else:
            per = {p: [float(t.get("sec") or 0), int(t.get("plays") or 0)] for p, t in tracks.items()}
        per = {p: v for p, v in per.items() if v[0] >= 5}
        total = sum(float(days_sec.get((start + _dt.timedelta(days=k)).isoformat(), 0.0)) for k in range(span))
        arts = {}
        for path, (s, pl) in per.items():
            a = self._artist_of(path, tracks.get(path) or {})
            v = arts.setdefault(a, [0.0, 0, path])
            v[0] += s
            v[1] += pl
        best = max(days, key=lambda x: x[1]) if days else None
        self.c_time.set(human_time(total), f"в среднем {human_time(total / max(1, span))} в день")
        self.c_tracks.set(str(len(per)), f"{sum(v[1] for v in per.values())} прослушиваний")
        self.c_artists.set(str(len(arts)), "разных")
        if best and best[1] > 0 and span <= 62:
            d = best[0]
            self.c_best.set(f"{d.day} {MONTHS[d.month - 1]}", human_time(best[1]))
        elif best and best[1] > 0:
            self.c_best.set(f"неделя {best[0].day} {MONTHS[best[0].month - 1]}", human_time(best[1]))
        else:
            self.c_best.set("—", "")
        top_a = sorted(arts.items(), key=lambda kv: -kv[1][0])[:10]
        self.artists.set_rows([{"title": a, "artist": a, "sec": v[0], "sub": f"{v[1]} прослушиваний",
                                "cover_path": v[2]} for a, v in top_a])
        top_t = sorted(per.items(), key=lambda kv: -kv[1][0])[:10]
        rows = []
        for path, (s, pl) in top_t:
            t = tracks.get(path) or {}
            if path.startswith("radio:"):
                title, artist = (t.get("title") or path[6:]), (t.get("artist") or "радио")
            else:
                it = self.win.items.get(path, {})
                title, artist, _al = libmod.track_view(path, it) if it else (t.get("title") or os.path.basename(path),
                                                                             t.get("artist") or "", "")
            rows.append({"title": title, "sub": f"{artist} · {pl} раз", "sec": s,
                         "cover_path": None if path.startswith("radio:") else path,
                         "path": path if os.path.isfile(path) else None})
        self.tracks.set_rows(rows)

    def _open_artist(self, r):
        ar = next((a for a in self.win.artists if a["name"] == r["artist"]), None)
        if ar:
            self.win.open_artist(ar)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)


# ---------------------------------------------------------------- дубликаты
QUALITY = {".flac": 6, ".wav": 5, ".aiff": 5, ".aif": 5, ".m4a": 3, ".opus": 3, ".ogg": 3, ".mp3": 2, ".wma": 1, ".aac": 2}


def find_duplicates(items):
    """Группы одинаковых песен: тот же исполнитель и название, длительность ±3 с.
    В каждой группе первым идёт файл, который лучше оставить (лучше формат, крупнее файл)."""
    by = {}
    for p, it in items.items():
        t, a, _al = libmod.track_view(p, it)
        k = (libmod.norm(a), libmod.norm(t))
        if not k[1]:
            continue
        by.setdefault(k, []).append(p)
    groups = []
    for k, paths in by.items():
        if len(paths) < 2:
            continue
        # разбить по длительности: «Intro» на разных альбомах — не дубликаты
        paths.sort(key=lambda p: items[p].get("d") or 0)
        cur = [paths[0]]
        for p in paths[1:]:
            d0, d1 = items[cur[-1]].get("d") or 0, items[p].get("d") or 0
            if d0 and d1 and abs(d0 - d1) > 3:
                if len(cur) > 1:
                    groups.append(cur)
                cur = [p]
            else:
                cur.append(p)
        if len(cur) > 1:
            groups.append(cur)
    out = []
    for g in groups:
        g.sort(key=lambda p: (-QUALITY.get(os.path.splitext(p)[1].lower(), 0), -(items[p].get("s") or 0)))
        out.append(g)
    out.sort(key=lambda g: libmod.norm(libmod.track_view(g[0], items[g[0]])[1]))
    return out


class DupRow(QWidget):
    def __init__(self, page, path, best):
        super().__init__()
        self.page, self.path, self.best = page, path, best
        it = page.win.items.get(path, {})
        lay = QHBoxLayout(self)
        lay.setContentsMargins(30, 0, 4, 0)
        lay.setSpacing(6)
        ext = os.path.splitext(path)[1][1:].upper()
        size = (it.get("s") or 0) / 1048576
        d = it.get("d") or 0
        kbps = int((it.get("s") or 0) * 8 / d / 1000) if d else 0
        tag = QLabel(("★ ОСТАВИТЬ  " if best else "") + f"{ext} · {size:.1f} МБ" + (f" · ~{kbps} кбит/с" if kbps else ""))
        tag.setObjectName("dupbest" if best else "dupext")
        tag.setFixedWidth(300)
        lay.addWidget(tag)
        pl = QLabel(path)
        pl.setObjectName("shint")
        pl.setToolTip(path)
        pl.setMinimumWidth(80)
        lay.addWidget(pl, 1)
        for text, fn in (("▶", lambda: page.win.play_paths([path], path)),
                         ("📂", lambda: __import__("subprocess").Popen(["explorer", "/select,", path])),
                         ("🗑 В КОРЗИНУ", lambda: page.trash([path]))):
            b = P3Action(text)
            if len(text) < 3:
                b.setFixedWidth(44)
            b.clicked.connect(fn)
            lay.addWidget(b)
        self.setFixedHeight(36)


class DuplicatesPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 10, 6, 0)
        outer.setSpacing(6)
        top = QHBoxLayout()
        back = P3Action("‹  НАЗАД")
        back.clicked.connect(win.go_back)
        top.addWidget(back)
        top.addStretch(1)
        self.all_btn = P3Action("🗑 ВСЕ ЛИШНИЕ КОПИИ — В КОРЗИНУ")
        self.all_btn.clicked.connect(self.trash_all)
        top.addWidget(self.all_btn)
        outer.addLayout(top)
        t = QLabel("ДУБЛИКАТЫ")
        t.setObjectName("h1")
        outer.addWidget(t)
        self.info = QLabel("")
        self.info.setObjectName("shint")
        self.info.setWordWrap(True)
        outer.addWidget(self.info)
        sc = _scroll()
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 18, 24)
        self.lay.setSpacing(2)
        sc.setWidget(body)
        outer.addWidget(sc, 1)
        self.groups = []

    def refresh(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        items = self.win.items
        self.groups = find_duplicates(items)
        extra = sum(len(g) - 1 for g in self.groups)
        mb = sum((items[p].get("s") or 0) for g in self.groups for p in g[1:]) / 1048576
        if not self.groups:
            self.info.setText("Дубликатов не нашлось — каждая песня в одном экземпляре. 👍")
        else:
            self.info.setText(f"Нашлось {len(self.groups)} песен в нескольких копиях: лишних файлов {extra}, "
                              f"это {mb:.0f} МБ. Одинаковыми считаются треки с тем же исполнителем и названием "
                              f"и почти той же длиной. ★ — копия, которую лучше оставить (лучше формат и качество). "
                              f"Удалённое уходит в Корзину Windows — его можно вернуть.")
        self.all_btn.setVisible(bool(self.groups))
        for g in self.groups[:300]:
            t, a, _al = libmod.track_view(g[0], items[g[0]])
            head = QLabel(f"{t}  —  {a}   ·  {len(g)} копии")
            head.setObjectName("stitle")
            self.lay.addSpacing(8)
            self.lay.addWidget(head)
            for i, p in enumerate(g):
                self.lay.addWidget(DupRow(self, p, i == 0))
        self.lay.addStretch(1)

    def trash(self, paths):
        from PySide6.QtCore import QFile
        done = []
        for p in paths:
            cur = getattr(self.win.owner, "loaded_path", None)
            if cur and os.path.normcase(cur) == os.path.normcase(p):
                continue                                     # то, что играет, не трогаем
            try:
                r = QFile.moveToTrash(p)
                if (r[0] if isinstance(r, tuple) else bool(r)):
                    done.append(p)
            except Exception:
                pass
        if done:
            self._forget(done)
        self.win.toast(f"🗑 В КОРЗИНЕ: {len(done)}" + (f" (не вышло: {len(paths) - len(done)})" if len(done) < len(paths) else ""))
        self.refresh()

    def _forget(self, done):
        """Убрать удалённые файлы из медиатеки; в плейлистах заменить на оставленную копию."""
        keep = {}
        for g in self.groups:
            for p in g[1:]:
                keep[os.path.normcase(p)] = g[0]
        lib = self.win.lib
        with lib.lock:
            for p in done:
                lib.items.pop(os.path.normpath(p), None)
            lib._rebuild_hay()
        lib.save()
        changed = False
        for pl in self.win.playlists.items:
            new = []
            for p in pl["tracks"]:
                k = os.path.normcase(p)
                if any(os.path.normcase(d) == k for d in done):
                    rep = keep.get(k)
                    if rep and rep not in new:
                        new.append(rep)
                    changed = True
                else:
                    new.append(p)
            pl["tracks"] = new
        if changed:
            self.win.playlists.save()
            self.win.playlists_changed()
        self.win.rebuild(force=True)

    def trash_all(self):
        extra = [p for g in self.groups for p in g[1:]]
        if not extra:
            return
        if QMessageBox.question(self, "Дубликаты", f"Отправить в Корзину {len(extra)} лишних копий?\n"
                                "В каждой группе останется отмеченная ★. Из Корзины всё можно вернуть.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.trash(extra)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)
