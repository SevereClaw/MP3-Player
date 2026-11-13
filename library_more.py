# -*- coding: utf-8 -*-
"""Медиатека, часть 2: свои плейлисты, очередь «Далее» и главная страница.

Плейлисты хранятся в playlists.json рядом с настройками плеера; обложка — своя
картинка (playlists/<id>.jpg) или мозаика из обложек первых альбомов.
"""
import datetime as _dt
import json
import os
import time
import uuid

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QMimeData, QUrl, Signal
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QPixmap, QImage, QPen, QLinearGradient
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QFrame,
                               QListView, QAbstractItemView, QInputDialog, QMessageBox, QFileDialog, QMenu)

import library as libmod
from library_ui import (ListModel, TileDelegate, TrackView, TrackDelegate, NavButton, P3Action, P3Chip,
                        WavePanel, pfont, skew_poly, fmt_ms, fmt_total, placeholder,
                        NAVY, NAVY2, DEEP, CYAN, CYAN_D, ICE, WHITE, DIM)

MIME = "application/x-mp3p-paths"


def paths_from_mime(md):
    if md.hasFormat(MIME):
        return [p for p in bytes(md.data(MIME)).decode("utf-8").split("\n") if p]
    out = []
    for u in md.urls():
        if u.isLocalFile():
            p = os.path.normpath(u.toLocalFile())
            if os.path.isdir(p):
                for root, _d, files in os.walk(p):
                    out += [os.path.join(root, f) for f in sorted(files)
                            if os.path.splitext(f)[1].lower() in libmod.AUDIO_EXT]
            elif os.path.splitext(p)[1].lower() in libmod.AUDIO_EXT:
                out.append(p)
    return out


def make_mime(paths):
    md = QMimeData()
    md.setData(MIME, "\n".join(paths).encode("utf-8"))
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return md


# ---------------------------------------------------------------- хранилище плейлистов
class Playlists:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.path = os.path.join(data_dir, "playlists.json")
        self.items = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.items = [p for p in d.get("playlists", []) if isinstance(p, dict) and p.get("id")]
        except Exception:
            self.items = []
        self.version = 0                      # меняется при каждой правке — окна знают, что перерисовать

    def save(self):
        self.version += 1
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"playlists": self.items}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    def create(self, name, paths=()):
        pl = {"id": uuid.uuid4().hex[:10], "name": name.strip() or "Новый плейлист",
              "tracks": [], "created": time.time()}
        self.items.append(pl)
        self.add(pl["id"], paths)
        self.save()
        return pl

    def add(self, pid, paths):
        pl = self.get(pid)
        if not pl:
            return 0
        have = {os.path.normcase(p) for p in pl["tracks"]}
        n = 0
        for p in paths:
            p = os.path.normpath(p)
            if os.path.normcase(p) not in have:
                pl["tracks"].append(p)
                have.add(os.path.normcase(p))
                n += 1
        self.save()
        return n

    def remove(self, pid, paths):
        pl = self.get(pid)
        if pl:
            drop = {os.path.normcase(p) for p in paths}
            pl["tracks"] = [p for p in pl["tracks"] if os.path.normcase(p) not in drop]
            self.save()

    def move(self, pid, src, dst):
        pl = self.get(pid)
        if pl and 0 <= src < len(pl["tracks"]):
            t = pl["tracks"].pop(src)
            pl["tracks"].insert(max(0, min(dst, len(pl["tracks"]))), t)
            self.save()

    def contains(self, pid, path):
        pl = self.get(pid)
        return bool(pl) and os.path.normcase(path) in {os.path.normcase(p) for p in pl["tracks"]}

    def rename(self, pid, name):
        pl = self.get(pid)
        if pl and name.strip():
            pl["name"] = name.strip()
            self.save()

    def delete(self, pid):
        self.items = [p for p in self.items if p["id"] != pid]
        try:
            os.remove(self.cover_file(pid))
        except OSError:
            pass
        self.save()

    def cover_file(self, pid):
        return os.path.join(self.dir, "playlists", pid + ".jpg")

    def set_cover(self, pid, img: QImage):
        os.makedirs(os.path.join(self.dir, "playlists"), exist_ok=True)
        img = img.scaled(600, 600, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
        x, y = (img.width() - 600) // 2, (img.height() - 600) // 2
        img.copy(max(0, x), max(0, y), 600, 600).save(self.cover_file(pid), "JPG", 90)
        self.save()


_MOSAIC = {}


def playlist_image(win, pl):
    """Своя обложка плейлиста или мозаика 2×2 из обложек первых альбомов."""
    f = win.playlists.cover_file(pl["id"])
    if os.path.isfile(f):
        key = (f, os.path.getmtime(f))
        img = _MOSAIC.get(key)
        if img is None:
            img = QImage(f)
            _MOSAIC[key] = img
        return img if not img.isNull() else None
    picks, seen = [], set()
    for p in pl["tracks"]:
        d = os.path.normcase(os.path.dirname(p))
        if d in seen:
            continue
        seen.add(d)
        picks.append(p)
        if len(picks) == 4:
            break
    if not picks:
        return None
    imgs = [win.covers.get(p) for p in picks]
    key = tuple((p, im.cacheKey() if im is not None else 0) for p, im in zip(picks, imgs))
    if key in _MOSAIC:
        return _MOSAIC[key]
    S = 360
    out = QImage(S, S, QImage.Format.Format_RGB32)
    out.fill(NAVY)
    pt = QPainter(out)
    pt.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if len(picks) < 4:
        im = imgs[0]
        pm = (QPixmap.fromImage(im.scaled(S, S, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation))
              if im is not None else placeholder(S, picks[0]))
        pt.drawPixmap(0, 0, pm)
    else:
        h = S // 2
        for i, (p, im) in enumerate(zip(picks, imgs)):
            pm = (QPixmap.fromImage(im.scaled(h, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation)).copy(0, 0, h, h)
                  if im is not None else placeholder(h, p))
            pt.drawPixmap((i % 2) * h, (i // 2) * h, pm)
    pt.end()
    if len(_MOSAIC) > 300:
        _MOSAIC.clear()
    _MOSAIC[key] = out
    return out


def ask_name(parent, title, text=""):
    name, ok = QInputDialog.getText(parent, title, "Название плейлиста:", QLineEdit.EchoMode.Normal, text)
    return name.strip() if ok and name.strip() else None


# ---------------------------------------------------------------- «В плейлист…» (как Add Songs to Playlist)
class AddToPlaylist(QWidget):
    def __init__(self, win, paths):
        super().__init__(win, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.win = win
        self.paths = [os.path.normpath(p) for p in paths]
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)
        t = QLabel("ДОБАВИТЬ В ПЛЕЙЛИСТ")
        t.setObjectName("sec")
        lay.addWidget(t)
        sub = QLabel(f"{len(self.paths)} трек." if len(self.paths) > 1 else
                     libmod.track_view(self.paths[0], win.items.get(self.paths[0], {}))[0] if self.paths else "")
        sub.setObjectName("shint")
        lay.addWidget(sub)
        lay.addSpacing(6)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        box = QWidget()
        box.setLayout(self.rows)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(box)
        sc.setMaximumHeight(330)
        sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
        lay.addWidget(sc)
        new = P3Action("+  НОВЫЙ ПЛЕЙЛИСТ")
        new.setFixedWidth(220)
        new.clicked.connect(self.new_playlist)
        lay.addWidget(new)
        self.fill()

    def fill(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            wd = it.widget()
            if wd is not None:
                wd.hide()
                wd.setParent(None)
                wd.deleteLater()
        for pl in self.win.playlists.items:
            self.rows.addWidget(PlaylistRow(self.win, pl, self.paths, self.toggle))
        if not self.win.playlists.items:
            lb = QLabel("Плейлистов пока нет — создай первый ↓")
            lb.setObjectName("shint")
            self.rows.addWidget(lb)
        self.adjustSize()

    def toggle(self, pl):
        allin = all(self.win.playlists.contains(pl["id"], p) for p in self.paths)
        if allin:
            self.win.playlists.remove(pl["id"], self.paths)
        else:
            n = self.win.playlists.add(pl["id"], self.paths)
            self.win.toast(f"＋ {n} В «{pl['name']}»" if n else "УЖЕ ЕСТЬ")
        self.win.playlists_changed()
        self.fill()

    def new_playlist(self):
        name = ask_name(self.win, "Новый плейлист")
        if name:
            self.win.playlists.create(name, self.paths)
            self.win.playlists_changed()
            self.win.toast(f"ПЛЕЙЛИСТ «{name}» СОЗДАН")
        self.close()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, NAVY2)
        g.setColorAt(1, DEEP)
        p.setBrush(g)
        p.setPen(QPen(CYAN, 2))
        p.drawRect(r)
        p.end()


class PlaylistRow(QWidget):
    def __init__(self, win, pl, paths, on_click):
        super().__init__()
        self.win, self.pl, self.paths, self.on_click = win, pl, paths, on_click
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.on_click(self.pl)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(47, 211, 255, 40))
            p.drawPolygon(skew_poly(r.adjusted(0, 3, 0, -3), 10))
        img = playlist_image(self.win, self.pl)
        pm = (QPixmap.fromImage(img.scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation)).copy(0, 0, 42, 42)
              if img is not None else placeholder(42, self.pl["name"]))
        p.drawPixmap(8, 5, pm)
        p.setFont(pfont(15))
        p.setPen(WHITE)
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(62, 6, r.width() - 110, 22), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.pl["name"], Qt.TextElideMode.ElideRight, int(r.width() - 110)))
        p.setFont(pfont(12, bold=False))
        p.setPen(DIM)
        p.drawText(QRectF(62, 27, r.width() - 110, 18), Qt.AlignmentFlag.AlignVCenter,
                   f"{len(self.pl['tracks'])} трек.")
        allin = bool(self.paths) and all(self.win.playlists.contains(self.pl["id"], x) for x in self.paths)
        c = QPointF(r.right() - 22, r.center().y())
        p.setPen(QPen(CYAN, 2))
        p.setBrush(CYAN if allin else Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, 8, 8)
        p.end()


# ---------------------------------------------------------------- пункты плейлистов в боковой панели
class SidePlaylist(NavButton):
    """Плейлист слева: клик — открыть, перетащить сюда треки — добавить."""
    dropped = Signal(list)

    def __init__(self, text):
        super().__init__(text, small=True)
        self.setFixedWidth(215)
        self.setCheckable(True)
        self.setAcceptDrops(True)
        self._drop = False

    def dragEnterEvent(self, e):
        if paths_from_mime(e.mimeData()):
            self._drop = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._drop = False
        self.update()

    def dropEvent(self, e):
        self._drop = False
        self.update()
        paths = paths_from_mime(e.mimeData())
        if paths:
            self.dropped.emit(paths)
            e.acceptProposedAction()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(CYAN, 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(skew_poly(QRectF(self.rect()).adjusted(2, 3, -2, -3), 10))
            p.end()


# ---------------------------------------------------------------- очередь «Далее»
class QueuePanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.o = win.owner
        self.setObjectName("queue")
        self.setFixedWidth(360)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 16, 10, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel("ДАЛЕЕ")
        t.setObjectName("h2")
        head.addWidget(t)
        head.addStretch(1)
        clr = P3Action("ОЧИСТИТЬ")
        clr.clicked.connect(self.clear)
        head.addWidget(clr)
        x = P3Action("✕")
        x.setFixedWidth(40)
        x.clicked.connect(lambda: self.win.toggle_queue(False))
        head.addWidget(x)
        lay.addLayout(head)
        self.info = QLabel("")
        self.info.setObjectName("shint")
        lay.addWidget(self.info)
        self.now = QLabel("")
        self.now.setObjectName("qnow")
        self.now.setWordWrap(True)
        lay.addWidget(self.now)
        self.view = TrackView(win, show_album=False)
        self.view.reorder = self.moved
        self.view.external_drop = self.dropped
        lay.addWidget(self.view, 1)
        self.view.doubleClicked.connect(lambda ix: (self.o.queue_jump(ix.row()), self.refresh(True)))
        self.view.customContextMenuRequested.connect(self.menu)
        hint = QLabel("Перетаскивай треки: выше — раньше. Сюда можно бросить трек из списка.")
        hint.setObjectName("shint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._sig = None
        self.paths = []

    def refresh(self, force=False):
        up = self.o.queue_upcoming(300) if hasattr(self.o, "queue_upcoming") else []
        sig = (getattr(self.o, "loaded_path", None), tuple(up))
        if sig == self._sig and not force:
            return
        self._sig = sig
        self.paths = list(up)
        items = self.win.items
        rows = []
        dur = 0
        for i, p in enumerate(self.paths):
            it = items.get(p, {})
            t, a, al = libmod.track_view(p, it)
            dur += it.get("d") or 0
            rows.append({"path": p, "title": t, "artist": a, "album": al, "d": it.get("d"), "num": i + 1})
        self.view.model_.set_rows(rows)
        wave = self.o.cfg.get("smart") == "wave" if hasattr(self.o, "cfg") else False
        self.info.setText((f"{len(rows)} трек. · {fmt_total(dur)}" if rows else "Дальше ничего нет") +
                          ("  ·  🌊 волна добавит ещё" if wave else ""))
        cur = getattr(self.o, "title", "") or ""
        self.now.setText(f"▶  СЕЙЧАС:  {cur}" + (f"  —  {self.o.artist}" if getattr(self.o, "artist", "") else "")
                         if cur else "")

    def moved(self, src, dst):
        self.o.queue_move(src, dst)
        self.refresh(True)

    def dropped(self, paths, row):
        self.o.queue_insert(paths, row)
        self.refresh(True)
        self.win.toast(f"＋ {len(paths)} В ОЧЕРЕДЬ")

    def clear(self):
        self.o.queue_clear()
        self.refresh(True)

    def menu(self, pt):
        ix = self.view.indexAt(pt)
        if not ix.isValid():
            return
        row = ix.row()
        path = self.paths[row]
        m = QMenu(self)
        m.addAction("▶  Играть сейчас", lambda: (self.o.queue_jump(row), self.refresh(True)))
        m.addAction("⬆  Сделать следующим", lambda: self.moved(row, 0))
        m.addAction("✖  Убрать из очереди", lambda: (self.o.queue_remove(row), self.refresh(True)))
        m.addAction("➕  В плейлист…", lambda: self.win.add_to_playlist([path]))
        m.exec(self.view.viewport().mapToGlobal(pt))

    def dragEnterEvent(self, e):
        if paths_from_mime(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = paths_from_mime(e.mimeData())
        if paths:
            self.dropped(paths, len(self.paths))
            e.acceptProposedAction()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor(3, 10, 31, 235))
        p.setPen(QPen(CYAN_D, 2))
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.end()


# ---------------------------------------------------------------- главная
class Strip(QListView):
    """Горизонтальная полоса плиток (как ряды на главной у Spotify)."""

    def __init__(self, win, round_art=False):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.horizontalScrollBar().setSingleStep(40)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.model_ = ListModel()
        self.setModel(self.model_)
        self.tiles = TileDelegate(win, round_art)
        self.tiles.W, self.tiles.H = 170, 232
        self.setItemDelegate(self.tiles)
        self.setFixedHeight(self.tiles.H + 22)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def wheelEvent(self, e):
        # колесо — листает полосу вбок (если её можно листать), иначе — страницу
        sb = self.horizontalScrollBar()
        if sb.maximum() > 0 and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) and abs(e.angleDelta().y()) > 0:
            if (e.angleDelta().y() > 0 and sb.value() > 0) or (e.angleDelta().y() < 0 and sb.value() < sb.maximum()):
                sb.setValue(sb.value() - e.angleDelta().y())
                return
        e.ignore()


class HomePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.o = win.owner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 6, 0)
        outer.setSpacing(4)
        self.hello = QLabel("ГЛАВНАЯ")
        self.hello.setObjectName("h1")
        outer.addWidget(self.hello)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 18, 24)
        self.lay.setSpacing(4)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)

        # «Моя волна» — крупно
        self.hero = QFrame()
        self.hero.setObjectName("hero")
        self.hero.setFixedHeight(150)
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(6, 8, 6, 4)
        self.wave = WavePanel(win)
        hl.addWidget(self.wave)
        self.lay.addWidget(self.hero)

        self.sections = {}
        for key, title, kind in (("recent", "НЕДАВНО ИГРАЛО", "track"), ("top", "ТОП НЕДЕЛИ", "track"),
                                 ("albums", "ЛЮБИМЫЕ АЛЬБОМЫ", "album"), ("lists", "ТВОИ ПЛЕЙЛИСТЫ", "playlist"),
                                 ("old", "ДАВНО НЕ СЛУШАЛ", "track"), ("fresh", "НЕДАВНО ДОБАВЛЕНО", "track")):
            lb = QLabel(title)
            lb.setObjectName("sec")
            strip = Strip(win)
            strip.clicked.connect(lambda ix, k=key, s=strip: self.clicked(k, s, ix))
            self.lay.addSpacing(10)
            self.lay.addWidget(lb)
            self.lay.addWidget(strip)
            self.sections[key] = (lb, strip, kind)
        self.empty = QLabel("Пока пусто: добавь папки с музыкой в «Настройках» — и послушай что-нибудь.\n"
                            "Здесь появятся недавние треки, топ недели и любимые альбомы.")
        self.empty.setObjectName("shint")
        self.lay.addWidget(self.empty)
        self.lay.addStretch(1)

    def clicked(self, key, strip, ix):
        r = ix.data(Qt.ItemDataRole.UserRole)
        kind = self.sections[key][2]
        if kind == "album":
            self.win.open_album(r["album"])
        elif kind == "playlist":
            self.win.open_playlist(r["pid"])
        else:
            paths = [x["path"] for x in strip.model_.rows]
            self.win.play_paths(paths, r["path"])

    def _track_rows(self, paths):
        rows = []
        for p in paths:
            it = self.win.items.get(p)
            if it is None:
                continue
            t, a, _al = libmod.track_view(p, it)
            rows.append({"path": p, "title": t, "artist": a, "cover_path": p})
        return rows

    def refresh(self):
        h = _dt.datetime.now().hour
        self.hello.setText("ДОБРОЕ УТРО" if 5 <= h < 12 else "ДОБРЫЙ ДЕНЬ" if 12 <= h < 18
                           else "ДОБРЫЙ ВЕЧЕР" if 18 <= h < 23 else "ДОБРОЙ НОЧИ")
        items = self.win.items
        st = getattr(self.o, "stats", None)
        data = st.data if st is not None else {}
        tracks = data.get("tracks") or {}
        daily = data.get("daily") or {}
        today = _dt.date.today()

        recent = [p for p in (self.o.cfg.get("recent") or []) if p in items][:20]
        if not recent:
            recent = sorted((p for p in tracks if p in items and tracks[p].get("last")),
                            key=lambda p: (tracks[p].get("last"), tracks[p].get("sec", 0)), reverse=True)[:20]

        week = {}
        for k in range(7):
            for p, d in (daily.get((today - _dt.timedelta(days=k)).isoformat()) or {}).items():
                week[p] = week.get(p, 0.0) + float(d.get("s", 0.0))
        top = [p for p, s in sorted(week.items(), key=lambda kv: -kv[1]) if p in items and s > 20][:15]

        score = {}
        for al in self.win.albums:
            s = sum(float((tracks.get(p) or {}).get("sec") or 0) for p in al["tracks"])
            if s > 60:
                score[al["key"]] = (s, al)
        albums = [{"title": al["title"], "artist": al["artist"], "cover_path": al["cover_path"], "album": al}
                  for _s, al in sorted(score.values(), key=lambda x: -x[0])[:15]]

        limit = (today - _dt.timedelta(days=30)).isoformat()
        old = sorted((p for p, t in tracks.items() if p in items and int(t.get("plays") or 0) >= 2
                      and (t.get("last") or "9999") <= limit), key=lambda p: -float(tracks[p].get("sec") or 0))[:15]

        fresh = sorted(items, key=lambda p: -float(items[p].get("m") or 0))[:15]

        lists = [{"title": pl["name"], "artist": f"{len(pl['tracks'])} трек.", "pid": pl["id"],
                  "cover_img": playlist_image(self.win, pl)} for pl in self.win.playlists.items]

        content = {"recent": self._track_rows(recent), "top": self._track_rows(top), "albums": albums,
                   "lists": lists, "old": self._track_rows(old), "fresh": self._track_rows(fresh)}
        any_ = False
        for key, (lb, strip, _k) in self.sections.items():
            rows = content[key]
            strip.model_.set_rows(rows)
            vis = bool(rows)
            lb.setVisible(vis)
            strip.setVisible(vis)
            any_ = any_ or (vis and key != "fresh")
        self.empty.setVisible(not any_)
        self.wave.sync()

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)
