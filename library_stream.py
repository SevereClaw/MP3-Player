# -*- coding: utf-8 -*-
"""Медиатека → «Яндекс и Spotify»: вход в Яндекс Музыку, «Мне нравится», плейлисты, поиск,
«Моя волна» Яндекса и импорт плейлистов по ссылке (Яндекс или Spotify)."""
import os
import random
import threading

from PySide6.QtCore import Qt, QObject, QRectF, QPointF, QSize, QUrl, Signal
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
        text(36 + w * 0.71, w * 0.14, r.get("album"), pfont(12, bold=False), dim)
        mark = "✓ НА ДИСКЕ" if r.get("path") else ("✖ НЕДОСТУПЕН" if not r.get("available", True) else "")
        text(w * 0.86 - 20, w * 0.09, mark, pfont(10, bold=True), QColor("#6fe3a3") if r.get("path") else DIM)
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
        if j == -1:                                       # ход докачки
            self.progress(*res)
            return
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
        for t in tracks:                                  # что уже есть у тебя — играет с диска
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
        if not self.tracks or self._importing:
            return
        tracks = list(self.tracks)
        logged = bool(self.o.cfg.get("ym_token"))
        pls = self.win.playlists
        pl = pls.create(self.list_title or "Импорт", [t["path"] for t in tracks if t.get("path")])
        pid = pl["id"]
        missing = [t for t in tracks if not t.get("path")]
        if not missing or not logged:
            pl["missing"] = [f"{t['artist']} - {t['title']}" for t in missing]
            pls.save()
            self.win.sync_playlists()
            self.status.setText(f"Плейлист «{pl['name']}»: {len(tracks) - len(missing)} из {len(tracks)}." +
                                (f" Остальные {len(missing)} — после входа в Яндекс." if missing else ""))
            self.win.toast("ПЛЕЙЛИСТ СОЗДАН")
            return
        self._importing = True
        cl, cache = self.client(), self.o.ystream.cache_dir
        prog = {"n": 0}
        self.win.toast("ПЛЕЙЛИСТ СОЗДАН — ДОКАЧИВАЮ")

        def work():
            got, lost = {}, []
            for t in missing:
                try:
                    found = ymusic.resolve(cl, t)
                    if found:
                        got[id(t)] = ymusic.download(cl, found, cache)
                    else:
                        lost.append(t)
                except Exception:
                    lost.append(t)
                prog["n"] += 1
                self.sig.done.emit(-1, (len(missing), prog["n"]))
            return got, lost

        def ok(res):
            got, lost = res
            self._importing = False
            for p in got.values():
                try:
                    self.o.library.update(p)
                except Exception:
                    pass
            p = pls.get(pid)
            if p is not None:
                p["tracks"] = [t["path"] if t.get("path") else got[id(t)] for t in tracks
                               if t.get("path") or id(t) in got]
                p["missing"] = [f"{t['artist']} - {t['title']}" for t in lost]
                pls.save()
            self.win.sync_playlists()
            self.show_tracks(self.list_title, tracks, self.source)
            self.status.setText(f"Готово: в плейлисте {len(tracks) - len(lost)} из {len(tracks)}." +
                                (f" Не нашлось в Яндексе: {len(lost)}." if lost else ""))
            self.win.toast("ПЛЕЙЛИСТ ГОТОВ")
        self.run(work, ok, f"Докачиваю из Яндекса: 0 из {len(missing)}…")

    # ------------------------------------------------------------------
    def progress(self, total, n):
        self.status.setText(f"Докачиваю из Яндекса: {n} из {total}…")

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_login()
        if self.o.cfg.get("ym_token") and self.account is None:
            self.load_account()
