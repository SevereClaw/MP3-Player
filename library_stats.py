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


def plays_word(n):
    n = int(n or 0)
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} раза"
    return f"{n} раз"


def _scroll():
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
    return sc


# ---------------------------------------------------------------- графики
class DayChart(QWidget):
    """Столбики «сколько слушал» — скошенные, как шкалы в P3R. Клик по столбику — внутрь (год → месяц → неделя → день)."""
    picked = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.items = []                # [{"label", "sec", "tip", "weekend", "child"}]
        self.sel = -1
        self.hover = -1

    def set_items(self, items, sel=-1):
        self.items, self.sel = items, sel
        self.update()

    def _bars(self):
        n = max(1, len(self.items))
        left, right, top, bottom = 46, self.width() - 10, 14, self.height() - 30
        step = (right - left) / n
        bw = max(3.0, min(step * 0.72, 120))
        mx = max([it["sec"] for it in self.items] + [60])
        out = []
        for i, it in enumerate(self.items):
            h = (bottom - top) * (it["sec"] / mx)
            out.append((QRectF(left + i * step + (step - bw) / 2, bottom - h, bw, h),
                        QRectF(left + i * step, top, step, bottom - top)))
        return out, mx, top, bottom, left, right

    def _at(self, x):
        bars, *_ = self._bars()
        return next((i for i, (_r, col) in enumerate(bars) if col.left() <= x <= col.right()), -1)

    def mouseMoveEvent(self, e):
        h = self._at(e.position().x())
        if h != self.hover:
            self.hover = h
            self.update()
            if h >= 0:
                it = self.items[h]
                sfx("hover")
                QToolTip.showText(e.globalPosition().toPoint(), it["tip"] + ("\nклик — подробнее" if it.get("child") else ""), self)
        self.setCursor(Qt.CursorShape.PointingHandCursor if h >= 0 and self.items[h].get("child")
                       else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        h = self._at(e.position().x())
        if h >= 0 and self.items[h].get("child"):
            sfx("select")
            self.picked.emit(h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bars, mx, top, bottom, left, right = self._bars()
        p.setFont(pfont(11, bold=False))
        for k in range(0, 5):
            y = bottom - (bottom - top) * k / 4
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.drawLine(QPointF(left, y), QPointF(right, y))
            p.setPen(DIM)
            v = mx * k / 4
            p.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v / 3600:.0f}ч" if mx >= 4 * 3600 else f"{int(v / 60)}м")
        many = len(self.items) > 16
        for i, (r, col) in enumerate(bars):
            it = self.items[i]
            hot = i == self.hover
            chosen = i == self.sel
            if hot or chosen:                                    # подсветка колонки
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(47, 211, 255, 40 if chosen else 22))
                p.drawRect(col)
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, WHITE if (hot or chosen) else ICE)
            g.setColorAt(1, CYAN if (hot or chosen) else CYAN_D)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            if r.height() > 1:
                p.drawPolygon(skew_poly(r, min(r.width() * 0.4, 8)))
            if not many or i % max(1, len(self.items) // 12) == 0 or chosen:
                p.setFont(pfont(11, bold=it.get("weekend", False) or chosen))
                p.setPen(CYAN if (it.get("weekend") or chosen) else DIM)
                p.drawText(QRectF(col.left() - 10, bottom + 4, col.width() + 20, 20), Qt.AlignmentFlag.AlignCenter,
                           it["label"])
        p.end()


class RankList(QWidget):
    """Топ треков / альбомов / исполнителей: место, картинка, имя и полоса «сколько времени» (или «сколько раз»)."""
    clicked = Signal(object)

    ROW = 50

    def __init__(self, win, round_art=False):
        super().__init__()
        self.win = win
        self.round_art = round_art
        self.rows = []                 # [{"title","sub","sec","cover_path","artist","path"; "val","val_text" — своя шкала}]
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
        mx = max(r.get("val", r["sec"]) for r in self.rows) or 1
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
            bar = QRectF(bx, y + self.ROW / 2 - 7, bw * r.get("val", r["sec"]) / mx, 14)
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
                       r.get("val_text") or human_time(r["sec"]))
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
MONTHS_FULL = ["ЯНВАРЬ", "ФЕВРАЛЬ", "МАРТ", "АПРЕЛЬ", "МАЙ", "ИЮНЬ", "ИЮЛЬ", "АВГУСТ", "СЕНТЯБРЬ", "ОКТЯБРЬ",
               "НОЯБРЬ", "ДЕКАБРЬ"]


def _month_end(d):
    nxt = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return nxt - _dt.timedelta(days=1)


class StatsPage(QWidget):
    """Итоги: всё время → год → месяц → неделя → день (клик по столбику — глубже, «крошки» сверху — назад)."""
    PERIODS = (("week", "НЕДЕЛЯ"), ("month", "МЕСЯЦ"), ("year", "ГОД"), ("all", "ВСЁ ВРЕМЯ"))
    # категории итогов: ключ, кнопка, заголовок списка
    CATS = (("tracks", "ПОПУЛЯРНОЕ", "ПОПУЛЯРНАЯ МУЗЫКА · что слушал дольше всего"),
            ("repeat", "НА ПОВТОРЕ", "НА ПОВТОРЕ · что включал чаще всего"),
            ("albums", "АЛЬБОМЫ", "ТОП АЛЬБОМОВ"),
            ("artists", "ИСПОЛНИТЕЛИ", "ТОП ИСПОЛНИТЕЛЕЙ"))

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
        for key, name in self.PERIODS:
            c = P3Chip(name)
            c.clicked.connect(lambda _=False, k=key: self.set_period(k))
            head.addWidget(c)
            self.chips[key] = c
        outer.addLayout(head)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        outer.addLayout(self.crumbs)
        sc = _scroll()
        self.sc = sc
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
        self.c_hit = StatCard("ГЛАВНЫЙ ТРЕК")
        self.c_best = StatCard("САМЫЙ МУЗЫКАЛЬНЫЙ")
        for c in (self.c_time, self.c_tracks, self.c_hit, self.c_best):
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
        self.chart.picked.connect(self._drill)
        lay.addWidget(self.chart)
        lay.addSpacing(8)
        cats = QHBoxLayout()
        cats.setSpacing(4)
        self.cat_chips = {}
        for key, name, _full in self.CATS:
            c = P3Chip(name)
            c.clicked.connect(lambda _=False, k=key: self.set_cat(k))
            cats.addWidget(c)
            self.cat_chips[key] = c
        cats.addStretch(1)
        lay.addLayout(cats)
        self.tracks_title = QLabel("")
        self.tracks_title.setObjectName("sec")
        lay.addWidget(self.tracks_title)
        self.tracks = RankList(win)                         # один список — что в нём, решает категория
        self.tracks.clicked.connect(self._row_clicked)
        lay.addWidget(self.tracks)
        row = QHBoxLayout()
        b = P3Action("📊 ИТОГИ НЕДЕЛИ В DISCORD…")
        b.setFixedWidth(280)
        b.clicked.connect(lambda: getattr(self.o, "open_weekly", lambda: None)())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.level, self.anchor = None, None
        self._items = []

    # ---------- уровни
    def set_period(self, key):
        self.o.cfg["stats_period"] = key
        today = _dt.date.today()
        if key == "week":
            self.level, self.anchor = "week", today - _dt.timedelta(days=today.weekday())
        elif key == "month":
            self.level, self.anchor = "month", today.replace(day=1)
        elif key == "year":
            self.level, self.anchor = "year", today.replace(month=1, day=1)
        else:
            self.level, self.anchor = "all", None
        self.refresh()

    def set_cat(self, key):
        self.o.cfg["stats_cat"] = key
        getattr(self.o, "save_later", lambda: None)()
        self.refresh()

    def _cat(self):
        c = self.o.cfg.get("stats_cat", "tracks")
        return c if c in self.cat_chips else "tracks"

    def _row_clicked(self, r):
        kind = r.get("kind")
        if kind == "artist":
            self._open_artist(r)
        elif kind == "album":
            if r.get("album") is not None:
                self.win.open_album(r["album"])
            elif r.get("paths"):
                self.win.play_paths(r["paths"], r["paths"][0])
        elif r.get("path"):
            self.win.play_paths([x["path"] for x in self.tracks.rows if x.get("path")], r["path"])

    def _go(self, level, anchor):
        self.level, self.anchor = level, anchor
        self.refresh()

    def _drill(self, i):
        it = self._items[i]
        self._go(*it["child"])

    def _range(self):
        """[начало, конец] текущего уровня (даты включительно)."""
        lv, a = self.level, self.anchor
        today = _dt.date.today()
        if lv == "all":
            days = self._days()
            first = min([_dt.date.fromisoformat(d) for d in days] or [today])
            return first, today
        if lv == "year":
            return a, a.replace(month=12, day=31)
        if lv == "month":
            return a, _month_end(a)
        if lv == "week":
            return a, a + _dt.timedelta(days=6)
        return a, a                                         # день

    def _days(self):
        st = getattr(self.o, "stats", None)
        return (st.data.get("days") or {}) if st is not None else {}

    def _sum(self, d0, d1):
        days = self._days()
        tot, d = 0.0, d0
        while d <= d1:
            tot += float(days.get(d.isoformat(), 0.0))
            d += _dt.timedelta(days=1)
        return tot

    def _build_items(self):
        lv, a = self.level, self.anchor
        today = _dt.date.today()
        items, sel = [], -1
        if lv == "all":
            first, _ = self._range()
            for y in range(first.year, today.year + 1):
                y0 = _dt.date(y, 1, 1)
                s = self._sum(y0, _dt.date(y, 12, 31))
                items.append({"label": str(y), "sec": s, "tip": f"{y} год: {human_time(s)}", "child": ("year", y0)})
            title = "ПО ГОДАМ"
        elif lv == "year":
            for m in range(1, 13):
                m0 = _dt.date(a.year, m, 1)
                s = self._sum(m0, _month_end(m0))
                items.append({"label": MONTHS[m - 1], "sec": s, "tip": f"{MONTHS_FULL[m - 1].capitalize()} {a.year}: "
                              f"{human_time(s)}", "child": ("month", m0)})
            title = f"{a.year} · ПО МЕСЯЦАМ"
        elif lv == "month":
            end = _month_end(a)
            wk = a - _dt.timedelta(days=a.weekday())
            while wk <= end:
                w0, w1 = max(wk, a), min(wk + _dt.timedelta(days=6), end)
                s = self._sum(w0, w1)
                items.append({"label": f"{w0.day}–{w1.day}", "sec": s,
                              "tip": f"{w0.day}–{w1.day} {MONTHS[a.month - 1]}: {human_time(s)}", "child": ("week", wk)})
                wk += _dt.timedelta(days=7)
            title = f"{MONTHS_FULL[a.month - 1]} {a.year} · ПО НЕДЕЛЯМ"
        else:                                               # неделя или день — 7 дней
            wk = a - _dt.timedelta(days=a.weekday())
            for k in range(7):
                d = wk + _dt.timedelta(days=k)
                s = self._sum(d, d)
                items.append({"label": f"{WD[k]} {d.day}", "sec": s, "weekend": k >= 5,
                              "tip": f"{WD[k]}, {d.day} {MONTHS[d.month - 1]}: {human_time(s)}",
                              "child": ("day", d) if d <= today else None})
                if lv == "day" and d == a:
                    sel = k
            title = f"НЕДЕЛЯ {wk.day} {MONTHS[wk.month - 1]} – {(wk + _dt.timedelta(days=6)).day} " \
                    f"{MONTHS[(wk + _dt.timedelta(days=6)).month - 1]} · ПО ДНЯМ"
        return items, sel, title

    def _crumbs(self):
        while self.crumbs.count():
            it = self.crumbs.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        lv, a = self.level, self.anchor
        chain = [("ВСЁ ВРЕМЯ", ("all", None))]
        if lv in ("year", "month", "week", "day"):
            chain.append((str(a.year), ("year", a.replace(month=1, day=1))))
        if lv in ("month", "week", "day"):
            chain.append((MONTHS_FULL[a.month - 1], ("month", a.replace(day=1))))
        if lv in ("week", "day"):
            wk = a - _dt.timedelta(days=a.weekday())
            we = wk + _dt.timedelta(days=6)
            chain.append((f"{wk.day} {MONTHS[wk.month - 1]} – {we.day} {MONTHS[we.month - 1]}", ("week", wk)))
        if lv == "day":
            chain.append((f"{WD[a.weekday()]}, {a.day} {MONTHS[a.month - 1]}", ("day", a)))
        for i, (text, target) in enumerate(chain):
            if i:
                arrow = QLabel("›")
                arrow.setObjectName("sec")
                self.crumbs.addWidget(arrow)
            b = P3Chip(text)
            b.setChecked(i == len(chain) - 1)
            b.clicked.connect(lambda _=False, t=target: self._go(*t))
            self.crumbs.addWidget(b)
        self.crumbs.addStretch(1)

    def _artist_of(self, path, t):
        it = self.win.items.get(path)
        if it is not None:
            return libmod.track_view(path, it)[1]
        if t.get("artist"):
            return t["artist"]
        ga, _gt = libmod.guess_from_name(path)
        return ga or libmod.UNKNOWN_ARTIST

    def _title_of(self, path, t):
        if path.startswith("radio:"):
            return t.get("title") or path[6:]
        it = self.win.items.get(path)
        if it is not None:
            return libmod.track_view(path, it)[0]
        return t.get("title") or libmod.guess_from_name(path)[1]

    def refresh(self):
        if self.level is None:
            self.set_period(self.o.cfg.get("stats_period", "month"))
            return
        top_key = {"week": "week", "month": "month", "year": "year", "all": "all"}.get(self.level)
        for k, c in self.chips.items():
            c.setChecked(k == top_key and self._is_current())
        self._crumbs()
        st = getattr(self.o, "stats", None)
        data = st.data if st is not None else {}
        tracks = data.get("tracks") or {}
        daily = data.get("daily") or {}
        items, sel, title = self._build_items()
        self._items = items
        self.chart.set_items(items, sel)
        self.days_title.setText(title + ("  ·  клик по столбику — подробнее" if any(i.get("child") for i in items) else ""))

        d0, d1 = self._range()
        per = {}
        if self.level == "all":
            per = {p: [float(t.get("sec") or 0), int(t.get("plays") or 0)] for p, t in tracks.items()}
        else:
            d = d0
            while d <= d1:
                for path, x in (daily.get(d.isoformat()) or {}).items():
                    v = per.setdefault(path, [0.0, 0])
                    v[0] += float(x.get("s", 0.0))
                    v[1] += int(x.get("p", 0))
                d += _dt.timedelta(days=1)
        per = {p: v for p, v in per.items() if v[0] >= 5}
        total = self._sum(d0, d1)
        span = (d1 - d0).days + 1
        arts = {}
        for path, (sc, pl) in per.items():
            a = self._artist_of(path, tracks.get(path) or {})
            v = arts.setdefault(a, {"sec": 0.0, "plays": 0, "tracks": []})
            v["sec"] += sc
            v["plays"] += pl
            v["tracks"].append((sc, path))
        self.c_time.set(human_time(total), f"в среднем {human_time(total / max(1, span))} в день" if span > 1 else "за этот день")
        self.c_tracks.set(str(len(per)), f"{sum(v[1] for v in per.values())} прослушиваний")
        hit = max(per.items(), key=lambda kv: kv[1][0], default=None)
        if hit:
            ht = tracks.get(hit[0]) or {}
            who = (ht.get("artist") or "радио") if hit[0].startswith("radio:") else self._artist_of(hit[0], ht)
            self.c_hit.set(self._title_of(hit[0], ht), f"{who} · {human_time(hit[1][0])}")
        else:
            self.c_hit.set("—", "")
        best = max(items, key=lambda x: x["sec"]) if items else None
        if best and best["sec"] > 0:
            self.c_best.set(best["label"], human_time(best["sec"]))
        else:
            self.c_best.set("—", "")
        cat = self._cat()
        for k, c in self.cat_chips.items():
            c.setChecked(k == cat)
        day = self.level == "day"
        self.tracks.round_art = cat == "artists"
        rows = []
        if cat == "artists":
            for a, v in sorted(arts.items(), key=lambda kv: -kv[1]["sec"])[:15]:
                v["tracks"].sort(reverse=True)
                names = [self._title_of(p, tracks.get(p) or {}) for _s, p in v["tracks"][:3]]
                rows.append({"kind": "artist", "title": a, "artist": a, "sec": v["sec"], "cover_path": v["tracks"][0][1],
                             "sub": f"{plays_word(v['plays'])} · чаще всего: " + ", ".join(names),
                             "paths": [p for _s, p in v["tracks"]]})
        elif cat == "albums":
            by_path = {p: al for al in self.win.albums for p in al["tracks"]}
            albs = {}
            for path, (sc, pl) in per.items():
                al = by_path.get(path)
                if al is None:                              # радио и то, чего уже нет в медиатеке
                    continue
                v = albs.setdefault(al["key"], {"al": al, "sec": 0.0, "plays": 0, "tracks": []})
                v["sec"] += sc
                v["plays"] += pl
                v["tracks"].append((sc, path))
            for v in sorted(albs.values(), key=lambda v: -v["sec"])[:15]:
                v["tracks"].sort(reverse=True)
                al = v["al"]
                rows.append({"kind": "album", "title": al["title"], "sec": v["sec"], "cover_path": al["cover_path"],
                             "sub": f"{al['artist']} · {len(v['tracks'])} из {len(al['tracks'])} трек. · "
                                    f"{plays_word(v['plays'])}",
                             "album": al, "paths": [p for _s, p in v["tracks"] if os.path.isfile(p)]})
        else:
            by_plays = cat == "repeat"
            order = sorted(per.items(), key=(lambda kv: (-kv[1][1], -kv[1][0])) if by_plays else (lambda kv: -kv[1][0]))
            for path, (sc, pl) in order[:(30 if day else 20)]:
                if by_plays and pl <= 0:
                    continue
                t = tracks.get(path) or {}
                artist = self._artist_of(path, t) if not path.startswith("radio:") else (t.get("artist") or "радио")
                if path.startswith("ext:"):                 # слушал в Spotify / Яндекс Музыке
                    artist += " · " + ("Spotify" if path.startswith("ext:spotify") else "Яндекс Музыка")
                row = {"kind": "track", "title": self._title_of(path, t), "sec": sc,
                       "sub": f"{artist} · {human_time(sc) if by_plays else plays_word(pl)}",
                       "cover_path": None if path.startswith("radio:") else path,
                       "path": path if os.path.isfile(path) else None}
                if by_plays:
                    row["val"], row["val_text"] = pl, plays_word(pl)
                rows.append(row)
        full = next(f for k, _n, f in self.CATS if k == cat)
        self.tracks_title.setText("ЧТО СЛУШАЛ В ЭТОТ ДЕНЬ" if day and cat == "tracks" else full)
        self.tracks.set_rows(rows)

    def _is_current(self):
        today = _dt.date.today()
        lv, a = self.level, self.anchor
        return (lv == "all" or (lv == "year" and a.year == today.year)
                or (lv == "month" and a == today.replace(day=1))
                or (lv == "week" and a == today - _dt.timedelta(days=today.weekday())))

    def _open_artist(self, r):
        ar = next((a for a in self.win.artists if a["name"] == r["artist"]), None)
        if ar and r["artist"] != libmod.UNKNOWN_ARTIST:
            self.win.open_artist(ar)
        elif r.get("paths"):                                # «неизвестный» — сразу его треки
            paths = [p for p in r["paths"] if os.path.isfile(p)]
            if paths:
                self.win.play_paths(paths, paths[0])

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Escape) and self.level not in ("all", None):
            up = {"day": "week", "week": "month", "month": "year", "year": "all"}[self.level]
            a = self.anchor
            anchor = {"week": a - _dt.timedelta(days=a.weekday()) if a else None, "month": a.replace(day=1) if a else None,
                      "year": a.replace(month=1, day=1) if a else None, "all": None}[up]
            sfx("back")
            self._go(up, anchor)
            return
        super().keyPressEvent(e)

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
