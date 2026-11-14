# -*- coding: utf-8 -*-
"""Медиатека → «Яндекс и Spotify»: вход в Яндекс Музыку, «Мне нравится», плейлисты, поиск,
«Моя волна» Яндекса и импорт плейлистов по ссылке (Яндекс или Spotify)."""
import os
import random
import threading
import time

from PySide6.QtCore import Qt, QObject, QRectF, QPointF, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QFrame,
                               QStyledItemDelegate, QStyle, QAbstractItemView, QScrollArea)

import ymusic
from library_ui import (ListModel, P3Action, P3Chip, pfont, skew_poly, fmt_ms, sfx,
                        NAVY, CYAN, ICE, WHITE, DIM)


class _Sig(QObject):
    done = Signal(int, object)


class YTrackDelegate(QStyledItemDelegate):
    H = 44

    def __init__(self, win):
        super().__init__()
        self.win = win

    def sizeHint(self, option, index):
        return QSize(400, self.H)

    def paint(self, p, opt, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(opt.rect)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        cur = bool(r.get("path")) and r.get("path") == getattr(self.win.owner, "loaded_path", None)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = rect.adjusted(46, 3, -10, -3)
        if cur:
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(0.65, ICE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(bar, 12))
        elif hover or sel:
            c = QColor(CYAN)
            c.setAlpha(70 if sel else 40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(skew_poly(bar, 12))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(bar.left() + 10, rect.bottom()), QPointF(rect.right() - 8, rect.bottom()))
        p.setFont(pfont(17))
        p.setPen(CYAN)
        p.drawText(QRectF(rect.left(), rect.top(), 38, rect.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(index.row() + 1))
        ink = NAVY if cur else WHITE
        dim = QColor(30, 60, 110) if cur else DIM
        w = bar.width()

        def text(x, width, s, f, pen, align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft):
            p.setFont(f)
            p.setPen(pen)
            box = QRectF(bar.left() + x, bar.top(), width, bar.height())
            p.drawText(box, align, QFontMetrics(f).elidedText(s or "", Qt.TextElideMode.ElideRight, int(width)))
        text(22, w * 0.40, r.get("title"), pfont(15, weight=QFont.Weight.Bold), ink)
        text(30 + w * 0.42, w * 0.28, r.get("artist"), pfont(13, bold=False), dim)
        text(36 + w * 0.71, w * 0.12, r.get("album"), pfont(12, bold=False), dim)
        st = r.get("dl")
        mx = w * 0.86 - 20
        if st == "active":                                # качается прямо сейчас: полоска и проценты
            frac = max(0.0, min(1.0, r.get("dl_p", 0.0)))
            box = QRectF(bar.left() + mx, bar.center().y() - 4, w * 0.065, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40) if not cur else QColor(10, 30, 70, 60))
            p.drawPolygon(skew_poly(box, 3))
            if frac > 0:
                p.setBrush(QColor("#ffd34d"))
                p.drawPolygon(skew_poly(QRectF(box.left(), box.top(), max(6.0, box.width() * frac), box.height()), 3))
            text(mx + w * 0.07, w * 0.04, f"{int(frac * 100)}%", pfont(10, bold=True), QColor("#ffd34d"))
        else:
            mark, col = {"queue": ("В ОЧЕРЕДИ", DIM), "done": ("✓ СКАЧАН", QColor("#6fe3a3")),
                         "fail": ("✖ НЕТ В ЯНДЕКСЕ", QColor("#ff6f91"))}.get(st, ("", DIM))
            if not mark:
                if r.get("path"):
                    mark, col = "✓ НА ДИСКЕ", QColor("#6fe3a3")
                elif not r.get("available", True):
                    mark = "✖ НЕДОСТУПЕН"
            text(mx, w * 0.11, mark, pfont(10, bold=True), col)
        d = r.get("duration") or 0
        text(w - 70, 56, fmt_ms(d * 1000) if d else "", pfont(14, weight=QFont.Weight.Bold), ink if cur else DIM,
             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        p.restore()


class YandexPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.o = win.real_owner
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self._jobs = {}
        self._job = 0
        self.tracks = []
        self.source = ""                 # что открыто: liked / wave / search / pl:… / link
        self.list_title = ""
        self.account = None
        self._importing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 12, 0)
        outer.setSpacing(8)
        h = QLabel("ЯНДЕКС МУЗЫКА И SPOTIFY")
        h.setObjectName("h1")
        outer.addWidget(h)

        # --- вход
        self.login_box = QWidget()
        lb = QVBoxLayout(self.login_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(4)
        t = QLabel("Войди в Яндекс Музыку — и её треки будут играть здесь: с текстами, обложками и «Итогами».\n"
                   "1) Нажми «ОТКРЫТЬ ВХОД» и войди в браузере.  2) Скопируй адрес страницы, куда попадёшь "
                   "(music.yandex.ru/#access_token=…), и вставь сюда.")
        t.setObjectName("shint")
        t.setWordWrap(True)
        lb.addWidget(t)
        row = QHBoxLayout()
        b = P3Action("1 · ОТКРЫТЬ ВХОД")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ymusic.AUTH_URL)))
        row.addWidget(b)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("2 · вставь адрес после входа (или сам токен)…")
        self.token_edit.returnPressed.connect(self.login)
        row.addWidget(self.token_edit, 1)
        b2 = P3Action("ВОЙТИ")
        b2.clicked.connect(self.login)
        row.addWidget(b2)
        lb.addLayout(row)
        outer.addWidget(self.login_box)

        # --- аккаунт и разделы
        self.acc_row = QHBoxLayout()
        self.acc_lbl = QLabel("")
        self.acc_lbl.setObjectName("count")
        self.acc_row.addWidget(self.acc_lbl)
        self.acc_row.addStretch(1)
        self.logout_btn = P3Action("ВЫЙТИ")
        self.logout_btn.clicked.connect(self.logout)
        self.acc_row.addWidget(self.logout_btn)
        outer.addLayout(self.acc_row)

        self.chips_box = QHBoxLayout()
        self.chips_box.setSpacing(4)
        self.c_liked = P3Chip("♥ МНЕ НРАВИТСЯ")
        self.c_liked.clicked.connect(self.open_liked)
        self.c_wave = P3Chip("〰 МОЯ ВОЛНА ЯНДЕКСА")
        self.c_wave.clicked.connect(self.play_wave)
        self.chips_box.addWidget(self.c_liked)
        self.chips_box.addWidget(self.c_wave)
        self.pl_chips = QHBoxLayout()
        self.pl_chips.setSpacing(4)
        chips_w = QWidget()
        cl = QHBoxLayout(chips_w)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        cl.addLayout(self.chips_box)
        cl.addLayout(self.pl_chips)
        cl.addStretch(1)
        sc = QScrollArea()
        sc.setWidget(chips_w)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setFixedHeight(48)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
        sc.wheelEvent = lambda e: sc.horizontalScrollBar().setValue(
            sc.horizontalScrollBar().value() - e.angleDelta().y())
        self.chips_area = sc
        outer.addWidget(sc)

        # --- поиск и ссылка
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск в Яндекс Музыке…")
        self.search.returnPressed.connect(self.do_search)
        row.addWidget(self.search, 1)
        self.link = QLineEdit()
        self.link.setPlaceholderText("…или ссылка на плейлист / альбом Яндекса или Spotify")
        self.link.returnPressed.connect(self.open_link)
        row.addWidget(self.link, 1)
        b = P3Action("ОТКРЫТЬ")
        b.clicked.connect(self.open_link)
        row.addWidget(b)
        outer.addLayout(row)

        # --- список
        head = QHBoxLayout()
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("sec")
        head.addWidget(self.title_lbl)
        head.addStretch(1)
        self.b_play = P3Action("▶ ИГРАТЬ")
        self.b_play.clicked.connect(lambda: self.play(0))
        self.b_shuf = P3Action("⤮ ВПЕРЕМЕШКУ")
        self.b_shuf.clicked.connect(lambda: self.play(0, shuffle=True))
        self.b_import = P3Action("⇩ В МЕДИАТЕКУ ПЛЕЙЛИСТОМ")
        self.b_import.setToolTip("Создать плейлист: что есть на диске — возьмём оттуда, остальное скачаем из Яндекса")
        self.b_import.clicked.connect(self.import_playlist)
        for b in (self.b_play, self.b_shuf, self.b_import):
            head.addWidget(b)
        outer.addLayout(head)
        self.view = QListView()
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.model = ListModel()
        self.view.setModel(self.model)
        self.view.setItemDelegate(YTrackDelegate(win))
        self.view.doubleClicked.connect(lambda ix: self.play(ix.row()))
        self.view.activated.connect(lambda ix: self.play(ix.row()))
        outer.addWidget(self.view, 1)
        self.status = QLabel("")
        self.status.setObjectName("count")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.dl = DownloadPanel(self)
        outer.addWidget(self.dl)
        self._sync_login()

    # ------------------------------------------------------------------ фон
    def client(self):
        return ymusic.Client(self.o.cfg.get("ym_token", ""))

    def run(self, fn, done, busy_text=""):
        self._job += 1
        j = self._job
        self._jobs[j] = done
        if busy_text:
            self.status.setText(busy_text)

        def work():
            try:
                res = fn()
            except ymusic.NeedLogin as e:
                res = ymusic.NeedLogin(str(e))
            except Exception as e:
                res = RuntimeError(f"{e}" or type(e).__name__)
            self.sig.done.emit(j, res)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, j, res):
        fn = self._jobs.pop(j, None)
        if isinstance(res, ymusic.NeedLogin):
            self.status.setText("Яндекс просит войти: " + str(res))
            return
        if isinstance(res, Exception):
            self._importing = False
            self.status.setText("Не получилось: " + str(res)[:200] + ("  (нет интернета?)" if "urlopen" in str(res) else ""))
            return
        if fn:
            fn(res)

    # ------------------------------------------------------------------ вход
    def _sync_login(self):
        on = bool(self.o.cfg.get("ym_token"))
        self.login_box.setVisible(not on)
        self.logout_btn.setVisible(on)
        self.c_liked.setVisible(on)
        self.c_wave.setVisible(on)
        self.chips_area.setVisible(on)
        for b in (self.b_play, self.b_shuf, self.b_import):
            b.setVisible(bool(self.tracks))
        self.search.setEnabled(on)
        self.search.setPlaceholderText("Поиск в Яндекс Музыке…" if on else "Поиск — после входа в Яндекс")
        if not on:
            self.acc_lbl.setText("Без входа: можно открыть плейлист Spotify или открытый плейлист Яндекса по ссылке")
            self._clear_pl_chips()
        elif self.account:
            self.acc_lbl.setText(f"Яндекс Музыка: {self.account['name'] or self.account['login']}")

    def login(self):
        tok = ymusic.token_from_text(self.token_edit.text())
        if not tok:
            self.status.setText("Это не похоже на адрес после входа: в нём должно быть «access_token=…»")
            return
        cl = ymusic.Client(tok)

        def ok(acc):
            self.o.cfg["ym_token"] = tok
            self.o.save_later()
            self.account = acc
            self.token_edit.clear()
            sfx("select")
            self.win.toast("ЯНДЕКС: ВХОД ВЫПОЛНЕН")
            self.status.setText("")
            self._sync_login()
            self.load_account()
        self.run(cl.me, ok, "Проверяю вход…")

    def logout(self):
        self.o.cfg.pop("ym_token", None)
        self.o.save_later()
        self.account = None
        self._sync_login()

    def load_account(self):
        if not self.o.cfg.get("ym_token"):
            return
        cl = self.client()

        def work():
            acc = cl.me()
            return acc, cl.playlists()

        def ok(res):
            self.account, pls = res
            self._sync_login()
            self._clear_pl_chips()
            for pl in pls:
                c = P3Chip(f"♫ {pl['title'].upper()}  {pl['count']}")
                c.clicked.connect(lambda _=False, pl=pl: self.open_playlist(pl))
                self.pl_chips.addWidget(c)
            if not self.tracks:
                self.open_liked()
        self.run(work, ok)

    def _clear_pl_chips(self):
        while self.pl_chips.count():
            it = self.pl_chips.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().setParent(None)

    # ------------------------------------------------------------------ списки
    def show_tracks(self, title, tracks, source):
        items = getattr(self.win, "items", {}) or {}
        m = ymusic.Matcher(items) if items else None
        cache = self.o.ystream.cache_dir
        run = getattr(self, "dl", None) and self.dl._run
        if run:                                           # этот список сейчас качается — показать его ход
            live = {(x.get("id") or "") + "|" + x.get("artist", "") + "|" + x.get("title", ""): x for x in run["tracks"]}
            tracks = [live.get((t.get("id") or "") + "|" + t.get("artist", "") + "|" + t.get("title", ""), t)
                      for t in tracks]
        for t in tracks:
            if t.get("dl") in ("active", "queue", "done"):
                continue                                  # что уже есть у тебя — играет с диска
            t["path"] = (ymusic.cached(cache, t) if t.get("id") else None) or (m.find(t) if m else None)
        self.tracks, self.source, self.list_title = tracks, source, title
        self.model.set_rows(tracks)
        have = sum(1 for t in tracks if t.get("path"))
        self.title_lbl.setText(f"{title.upper()} · {len(tracks)}")
        self.status.setText(f"На диске уже есть {have} из {len(tracks)}." if tracks else "Пусто.")
        for b in (self.b_play, self.b_shuf):
            b.setVisible(bool(tracks))
        self.b_import.setVisible(bool(tracks) and source != "wave")

    def open_liked(self):
        self._check(self.c_liked)
        cl = self.client()
        self.run(cl.liked, lambda tr: self.show_tracks("Мне нравится", tr, "liked"), "Загружаю «Мне нравится»…")

    def open_playlist(self, pl):
        cl = self.client()
        self.run(lambda: cl.playlist(pl["owner"], pl["kind"]),
                 lambda res: self.show_tracks(res["title"], res["tracks"], f"pl:{pl['kind']}"),
                 f"Загружаю «{pl['title']}»…")

    def do_search(self):
        q = self.search.text().strip()
        if not q:
            return
        cl = self.client()
        self.run(lambda: cl.search(q), lambda tr: self.show_tracks(f"Поиск: {q}", tr, "search"), "Ищу…")

    def open_link(self, text=None):
        url = (text if isinstance(text, str) else self.link.text()).strip()
        if not url:
            return
        if not ymusic.parse_link(url):
            self.status.setText("Нужна ссылка вида music.yandex.ru/users/…/playlists/… или open.spotify.com/playlist/…")
            return
        cl = self.client()
        self.run(lambda: ymusic.fetch_list(url, cl),
                 lambda res: self.show_tracks(res["title"], res["tracks"], "link"), "Открываю ссылку…")

    def _check(self, chip):
        for c in (self.c_liked, self.c_wave):
            c.setChecked(c is chip)

    # ------------------------------------------------------------------ слушать
    def play(self, row=0, shuffle=False):
        tr = list(self.tracks)
        if not tr:
            return
        if shuffle:
            random.shuffle(tr)
            row = 0
        need_ya = [t for t in tr if not t.get("path")]
        if need_ya and not self.o.cfg.get("ym_token"):
            local = [t["path"] for t in tr if t.get("path")]
            if not local:
                self.status.setText("Этих песен нет на диске. Войди в Яндекс Музыку — и они будут играть отсюда.")
                return
            start = tr[row]["path"] if tr[row].get("path") else local[0]
            self.win.play_paths(local, start)
            self.status.setText(f"Играет то, что есть на диске ({len(local)}). Остальное — после входа в Яндекс.")
            return
        sfx("select")
        self.o.ystream.play(tr, row)

    def play_wave(self):
        self._check(self.c_wave)
        sfx("select")
        self.o.ystream.play_wave()
        self.win.go("now")

    # ------------------------------------------------------------------ импорт в медиатеку
    def import_playlist(self):
        if not self.tracks:
            return
        if self.dl.running():
            self.status.setText("Уже докачиваю другой плейлист — дождись или нажми «ОТМЕНА» на полосе загрузки.")
            return
        tracks = list(self.tracks)
        logged = bool(self.o.cfg.get("ym_token"))
        pls = self.win.playlists
        pl = pls.create(self.list_title or "Импорт", [t["path"] for t in tracks if t.get("path")])
        missing = [t for t in tracks if not t.get("path") and t.get("available", True)]
        if not missing or not logged:
            pl["missing"] = [f"{t['artist']} - {t['title']}" for t in missing]
            pls.save()
            self.win.sync_playlists()
            self.status.setText(f"Плейлист «{pl['name']}»: {len(tracks) - len(missing)} из {len(tracks)}." +
                                (f" Остальные {len(missing)} — после входа в Яндекс." if missing else ""))
            self.win.toast("ПЛЕЙЛИСТ СОЗДАН")
            return
        self.win.toast("ПЛЕЙЛИСТ СОЗДАН — ДОКАЧИВАЮ")
        self.dl.start(pl["id"], pl["name"], tracks, missing)

    def scroll_to(self, t):
        """Показать в списке строку трека (если открыт этот список)."""
        for i, x in enumerate(self.tracks):
            if x is t:
                self.view.scrollTo(self.model.index(i, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_login()
        if self.o.cfg.get("ym_token") and self.account is None:
            self.load_account()


# ====================================================================== полоса загрузки
class _DlSig(QObject):
    ev = Signal(object)


class _Bar(QWidget):
    """Скошенная полоса прогресса в стиле P3R: скачано / не нашлось."""

    def __init__(self, h=12):
        super().__init__()
        self.setFixedHeight(h)
        self.done = self.fail = 0.0

    def set(self, done, fail):
        self.done, self.fail = done, fail
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 1, -2, -1)
        sk = r.height() * 0.6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawPolygon(skew_poly(r, sk))
        if self.done > 0:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(0.5, ICE)
            g.setColorAt(1, CYAN)
            p.setBrush(g)
            p.drawPolygon(skew_poly(QRectF(r.left(), r.top(), max(sk * 2, r.width() * self.done), r.height()), sk))
        if self.fail > 0:
            x = r.left() + r.width() * self.done
            p.setBrush(QColor("#ff6f91"))
            p.drawPolygon(skew_poly(QRectF(x, r.top(), max(sk * 2, r.width() * self.fail), r.height()), sk))
        p.end()


def _mb(n):
    return f"{n / 1048576:.1f} МБ"


def _dur(sec):
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600} ч {sec % 3600 // 60} мин"
    if sec >= 60:
        return f"{sec // 60} мин"
    return f"{sec} с"


class DownloadPanel(QFrame):
    """Что, куда и когда качается из Яндекса: общий прогресс, скорость, сколько осталось,
    три текущих трека с процентами; пауза, отмена, папка."""
    WORKERS = 3

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.o = page.o
        self.setObjectName("dlpanel")
        self.setStyleSheet("#dlpanel { background: rgba(4, 14, 40, 200); border: 1px solid rgba(80, 200, 255, 90); }")
        self.sig = _DlSig()
        self.sig.ev.connect(self._on_ev)
        self._run = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = QLabel("")
        self.title.setObjectName("sec")
        top.addWidget(self.title)
        top.addStretch(1)
        self.nums = QLabel("")
        self.nums.setObjectName("count")
        self.b_show = P3Action("К ТЕКУЩЕЙ")
        self.b_show.setToolTip("Прокрутить список к песне, которая сейчас качается")
        self.b_show.clicked.connect(self.show_current)
        self.b_folder = P3Action("ПАПКА")
        self.b_folder.setToolTip("Открыть папку, куда падают песни")
        self.b_folder.clicked.connect(self.open_folder)
        self.b_pause = P3Action("ПАУЗА")
        self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = P3Action("ОТМЕНА")
        self.b_stop.setToolTip("Остановить: скачанное останется в плейлисте")
        self.b_stop.clicked.connect(self.cancel)
        self.b_close = P3Action("✕")
        self.b_close.clicked.connect(self.hide)
        for b in (self.b_show, self.b_folder, self.b_pause, self.b_stop, self.b_close):
            top.addWidget(b)
        lay.addLayout(top)
        lay.addWidget(self.nums)
        self.bar = _Bar(12)
        lay.addWidget(self.bar)
        self.where = QLabel("")
        self.where.setObjectName("shint")
        lay.addWidget(self.where)
        self.rows = []
        for _ in range(self.WORKERS):
            row = QHBoxLayout()
            row.setSpacing(8)
            name = QLabel("")
            name.setObjectName("count")
            b = _Bar(8)
            b.setFixedWidth(160)
            pct = QLabel("")
            pct.setObjectName("count")
            pct.setFixedWidth(130)
            row.addWidget(name, 1)
            row.addWidget(b)
            row.addWidget(pct)
            lay.addLayout(row)
            self.rows.append((name, b, pct))
        self.log = QLabel("")
        self.log.setObjectName("shint")
        self.log.setWordWrap(True)
        lay.addWidget(self.log)
        self.hide()

    # ---------------------------------------------------------------- запуск
    def running(self):
        return bool(self._run and not self._run["finished"])

    def start(self, pid, name, tracks, missing):
        for t in missing:
            t["dl"], t["dl_p"] = "queue", 0.0
        run = {"pid": pid, "name": name, "tracks": tracks, "missing": missing, "next": 0, "lock": threading.Lock(),
               "pause": threading.Event(), "cancel": False, "finished": False, "active": {}, "done": 0,
               "fail": [], "bytes": 0, "speed": [], "t0": time.monotonic(), "since_save": 0, "last_save": time.monotonic(),
               "alive": self.WORKERS, "recent": []}
        run["pause"].set()
        self._run = run
        self.b_pause.setText("ПАУЗА")
        self.b_pause.show()
        self.b_stop.show()
        self.b_show.show()
        self.title.setText(f"⇩ ДОКАЧИВАЮ «{name.upper()}»")
        self.where.setText(f"Куда: {self.o.ystream.cache_dir}   ·   плейлист «{name}» пополняется по ходу")
        self.show()
        cl, cache = self.page.client(), self.o.ystream.cache_dir
        for _ in range(self.WORKERS):
            threading.Thread(target=self._worker, args=(run, cl, cache), daemon=True).start()
        self._tick = QTimer(self, interval=500)
        self._tick.timeout.connect(self._refresh)
        self._tick.start()
        self._refresh()

    def _worker(self, run, cl, cache):
        emit = self.sig.ev.emit
        while True:
            run["pause"].wait()
            if run["cancel"]:
                break
            with run["lock"]:
                if run["next"] >= len(run["missing"]):
                    break
                t = run["missing"][run["next"]]
                run["next"] += 1
            emit(("start", run, t))
            last = [0.0, 0]

            def prog(got, total, t=t):
                if run["cancel"]:
                    raise InterruptedError("отменено")
                now = time.monotonic()
                if now - last[0] >= 0.15 or (total and got >= total):
                    emit(("prog", run, t, got, total, got - last[1]))
                    last[0], last[1] = now, got
            try:
                found = ymusic.resolve(cl, t)
                if not found:
                    emit(("fail", run, t, "нет в Яндекс Музыке"))
                    continue
                path = ymusic.download(cl, found, cache, progress=prog)
                emit(("done", run, t, path))
            except InterruptedError:
                emit(("fail", run, t, "отменено"))
                break
            except ymusic.NeedLogin as e:
                emit(("fail", run, t, str(e)))
                run["cancel"] = True
                break
            except Exception as e:
                emit(("fail", run, t, f"{e}"[:80] or type(e).__name__))
        emit(("exit", run))

    # ---------------------------------------------------------------- события
    def _on_ev(self, ev):
        kind, run = ev[0], ev[1]
        if run is not self._run:
            return
        now = time.monotonic()
        if kind == "start":
            t = ev[2]
            t["dl"], t["dl_p"] = "active", 0.0
            run["active"][id(t)] = [t, 0, 0, now]
        elif kind == "prog":
            t, got, total, delta = ev[2:]
            t["dl_p"] = got / total if total else 0.0
            a = run["active"].get(id(t))
            if a:
                a[1], a[2] = got, total
            run["bytes"] += max(0, delta)
            run["speed"].append((now, max(0, delta)))
        elif kind == "done":
            t, path = ev[2], ev[3]
            t["dl"], t["path"] = "done", path
            run["active"].pop(id(t), None)
            run["done"] += 1
            run["since_save"] += 1
            run["recent"].insert(0, f"✓ {t.get('artist', '')} — {t.get('title', '')}")
            try:
                self.o.library.update(path)
            except Exception:
                pass
            if run["since_save"] >= 10 or now - run["last_save"] > 15:
                self._save(run)
        elif kind == "fail":
            t, why = ev[2], ev[3]
            run["active"].pop(id(t), None)
            if why != "отменено":
                t["dl"] = "fail"
                run["fail"].append(t)
                run["recent"].insert(0, f"✖ {t.get('artist', '')} — {t.get('title', '')}: {why}")
            else:
                t["dl"] = "queue"
        elif kind == "exit":
            run["alive"] -= 1
            if run["alive"] <= 0:
                self._finish(run)
        del run["recent"][3:]
        self.page.view.viewport().update()

    def _save(self, run):
        run["since_save"], run["last_save"] = 0, time.monotonic()
        pls = self.page.win.playlists
        p = pls.get(run["pid"])
        if p is None:
            return
        p["tracks"] = [t["path"] for t in run["tracks"] if t.get("path")]
        p["missing"] = [f"{t['artist']} - {t['title']}" for t in run["tracks"] if not t.get("path")]
        pls.save()
        self.page.win.sync_playlists()

    def _finish(self, run):
        run["finished"] = True
        self._tick.stop()
        for t in run["missing"]:
            if t.get("dl") in ("queue", "active"):
                t.pop("dl", None)
        self._save(run)
        n, f = len(run["missing"]), len(run["fail"])
        took = _dur(time.monotonic() - run["t0"])
        if run["cancel"]:
            self.title.setText(f"■ ОСТАНОВЛЕНО «{run['name'].upper()}»")
        else:
            self.title.setText(f"✓ ГОТОВО «{run['name'].upper()}»")
            self.page.win.toast("ПЛЕЙЛИСТ ГОТОВ")
        self.nums.setText(f"скачано {run['done']} из {n}" + (f" · не нашлось {f}" if f else "") + f" · за {took}")
        self.b_pause.hide()
        self.b_stop.hide()
        self.b_show.hide()
        for name, b, pct in self.rows:
            name.setText("")
            b.set(0, 0)
            pct.setText("")
        if f:
            lost = ", ".join(f"{t.get('artist', '')} — {t.get('title', '')}" for t in run["fail"][:6])
            self.log.setText(f"Не нашлось в Яндексе: {lost}" + (f" и ещё {f - 6}" if f > 6 else ""))
        self.page.view.viewport().update()

    # ---------------------------------------------------------------- вид
    def _refresh(self):
        run = self._run
        if not run or run["finished"]:
            return
        now = time.monotonic()
        n = len(run["missing"])
        handled = run["done"] + len(run["fail"])
        run["speed"] = [x for x in run["speed"] if now - x[0] < 5]
        span = max(1.0, min(5.0, now - run["t0"]))
        bps = sum(x[1] for x in run["speed"]) / span
        parts = [f"{handled} / {n}", f"{int(handled * 100 / max(1, n))} %", f"скачано {_mb(run['bytes'])}"]
        if not run["pause"].is_set():
            parts.append("на паузе")
        else:
            parts.append(f"{bps / 1048576:.1f} МБ/с")
            if handled >= 3:
                left = (now - run["t0"]) / handled * (n - handled)
                parts.append(f"осталось ≈ {_dur(left)}")
        if run["fail"]:
            parts.append(f"не нашлось {len(run['fail'])}")
        self.nums.setText("  ·  ".join(parts))
        self.bar.set(run["done"] / max(1, n), len(run["fail"]) / max(1, n))
        act = list(run["active"].values())
        for k, (name, b, pct) in enumerate(self.rows):
            if k < len(act):
                t, got, total, t0 = act[k]
                name.setText(f"▸ {t.get('artist', '')} — {t.get('title', '')}")
                b.set(got / total if total else 0.0, 0)
                pct.setText(f"{int(got * 100 / total)} % · {_mb(got)}" if total else "ищу в Яндексе…")
            else:
                name.setText("")
                b.set(0, 0)
                pct.setText("")
        self.log.setText("   ".join(run["recent"]))

    # ---------------------------------------------------------------- кнопки
    def show_current(self):
        run = self._run
        act = list(run["active"].values()) if run else []
        if act and not self.page.scroll_to(act[0][0]):
            self.page.status.setText(f"Сейчас качается из «{run['name']}» — открой этот список, чтобы увидеть строку.")

    def open_folder(self):
        d = self.o.ystream.cache_dir
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def toggle_pause(self):
        run = self._run
        if not run or run["finished"]:
            return
        if run["pause"].is_set():
            run["pause"].clear()
            self.b_pause.setText("ДАЛЬШЕ")
        else:
            run["pause"].set()
            self.b_pause.setText("ПАУЗА")
        self._refresh()

    def cancel(self):
        run = self._run
        if not run or run["finished"]:
            return
        run["cancel"] = True
        run["pause"].set()
        self.title.setText("■ ОСТАНАВЛИВАЮ…")
