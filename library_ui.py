# -*- coding: utf-8 -*-
"""Медиатека в стиле Persona 3 Reload: альбомы, исполнители и экран «Сейчас играет».

Большое окно поверх той же музыки, что у плеера-виджета: играет тот же плеер (owner),
те же теги, обложки, тексты .lrc и статистика. Окно только показывает и командует.
"""
import hashlib
import os
import threading

from PySide6.QtCore import (Qt, QTimer, QRect, QRectF, QPointF, QSize, QObject, Signal, QModelIndex,
                            QAbstractListModel, QBuffer, QIODevice, QEvent)
from PySide6.QtGui import (QPainter, QColor, QFont, QFontMetrics, QPixmap, QImage, QPolygonF, QLinearGradient,
                           QPainterPath, QPen, QBrush, QIcon)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QStackedWidget, QStyledItemDelegate, QStyle, QSlider, QAbstractItemView,
                               QPushButton, QMenu, QSizePolicy, QApplication)

import library as libmod

# ---------------------------------------------------------------- палитра P3R
NAVY = QColor("#061331")
NAVY2 = QColor("#0b2257")
DEEP = QColor("#030a1f")
CYAN = QColor("#2fd3ff")
CYAN_D = QColor("#1680c9")
ICE = QColor("#bff4ff")
WHITE = QColor("#f4f9ff")
DIM = QColor("#7f98c4")
FONT = "Segoe UI"


def pfont(size, bold=True, italic=True, weight=None):
    f = QFont(FONT)
    f.setPixelSize(int(size))
    f.setItalic(italic)
    f.setWeight(weight or (QFont.Weight.Black if bold else QFont.Weight.Normal))
    return f


def skew_poly(r: QRectF, k=None):
    """Параллелограмм с наклоном вправо — фирменная плашка меню P3R."""
    k = r.height() * 0.35 if k is None else k
    return QPolygonF([QPointF(r.left() + k, r.top()), QPointF(r.right(), r.top()),
                      QPointF(r.right() - k, r.bottom()), QPointF(r.left(), r.bottom())])


def fmt_ms(ms):
    s = max(0, int(ms // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_total(sec):
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m = rem // 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


def paint_bg(p: QPainter, r: QRect, t=0.0):
    """Фон: тёмно-синий градиент и косые полосы, как вода в меню P3R."""
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, NAVY2)
    g.setColorAt(0.55, NAVY)
    g.setColorAt(1, DEEP)
    p.fillRect(r, g)
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    for i, (x, w, a) in enumerate(((0.62, 0.22, 18), (0.78, 0.08, 26), (0.9, 0.3, 12), (0.15, 0.05, 10))):
        c = QColor(CYAN)
        c.setAlpha(a)
        p.setBrush(c)
        x0 = r.left() + r.width() * x
        ww = r.width() * w
        sk = r.height() * 0.45
        p.drawPolygon(QPolygonF([QPointF(x0 + sk, r.top()), QPointF(x0 + sk + ww, r.top()),
                                 QPointF(x0 + ww - sk, r.bottom()), QPointF(x0 - sk, r.bottom())]))
    p.restore()


def placeholder(size, seed=""):
    """Обложки нет — синяя плашка с нотой."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    h = int(hashlib.md5(seed.encode("utf-8", "replace")).hexdigest()[:2], 16)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor.fromHsv(200 + h % 30, 200, 120))
    g.setColorAt(1, QColor.fromHsv(225 + h % 20, 230, 45))
    p.fillRect(0, 0, size, size, g)
    p.setPen(QColor(255, 255, 255, 170))
    p.setFont(pfont(size * 0.42, italic=False))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "♪")
    p.end()
    return pm


# ---------------------------------------------------------------- обложки (в фоне, с кэшем на диске)
class CoverCache(QObject):
    ready = Signal(str)          # путь трека, чья обложка готова

    def __init__(self, cache_dir):
        super().__init__()
        self.dir = cache_dir
        self.mem = {}                # путь → QImage (или None, если обложки нет)
        self.queue = []
        self.lock = threading.Lock()
        self.busy = False

    def _disk(self, path):
        try:
            st = os.stat(path)
            key = f"{os.path.normcase(path)}|{st.st_size}|{int(st.st_mtime)}"
        except OSError:
            key = os.path.normcase(path)
        return os.path.join(self.dir, hashlib.md5(key.encode("utf-8", "replace")).hexdigest() + ".jpg")

    def get(self, path):
        """QImage или None; если ещё не загружена — поставить в очередь (придёт сигнал ready)."""
        if path in self.mem:
            return self.mem[path]
        with self.lock:
            if path not in self.queue:
                self.queue.append(path)
        if not self.busy:
            self.busy = True
            threading.Thread(target=self._work, daemon=True).start()
        return None

    def put(self, path, img):
        self.mem[path] = img

    def _work(self):
        try:
            while True:
                with self.lock:
                    if not self.queue:
                        return
                    path = self.queue.pop()          # последние запрошенные — первыми (то, что на экране)
                img = None
                disk = self._disk(path)
                if os.path.isfile(disk):
                    img = QImage(disk)
                    if img.isNull():
                        img = None
                    elif img.width() <= 2:
                        img = None               # метка «обложки нет»
                        self.mem[path] = None
                        self.ready.emit(path)
                        continue
                if img is None:
                    data = libmod.read_tags(path).get("cover")
                    if not data:
                        side = self._side_image(path)
                        if side:
                            data = side
                    if data:
                        im = QImage.fromData(data)
                        if not im.isNull():
                            img = im.scaled(360, 360, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                            Qt.TransformationMode.SmoothTransformation)
                    try:
                        os.makedirs(self.dir, exist_ok=True)
                        (img if img is not None else QImage(1, 1, QImage.Format.Format_RGB32)).save(disk, "JPG", 88)
                    except Exception:
                        pass
                self.mem[path] = img
                self.ready.emit(path)
        finally:
            self.busy = False
            with self.lock:
                again = bool(self.queue)
            if again:
                self.busy = True
                threading.Thread(target=self._work, daemon=True).start()

    @staticmethod
    def _side_image(path):
        """cover.jpg / folder.jpg рядом с треком."""
        d = os.path.dirname(path)
        for n in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "albumart.jpg"):
            f = os.path.join(d, n)
            if os.path.isfile(f):
                try:
                    with open(f, "rb") as fh:
                        return fh.read()
                except OSError:
                    pass
        return None


_PM = {}


def cover_pixmap(cache, path, size, seed=""):
    """Готовая картинка нужного размера (с кэшем — перерисовка списков не тормозит)."""
    img = cache.get(path) if path else None
    key = (path, size, img.cacheKey() if img is not None else seed)
    pm = _PM.get(key)
    if pm is None:
        if img is None:
            pm = placeholder(size, seed or path or "")
        else:
            pm = QPixmap.fromImage(img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation)).copy(0, 0, size, size)
        if len(_PM) > 1500:
            _PM.clear()
        _PM[key] = pm
    return pm


# ---------------------------------------------------------------- модели
class ListModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            r = self.rows[index.row()]
            return r.get("path") or r.get("title") or r.get("name")
        return None

    def refresh_path(self, path):
        for i, r in enumerate(self.rows):
            if r.get("cover_path") == path or r.get("path") == path:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)


# ---------------------------------------------------------------- плитки альбомов / исполнителей
class TileDelegate(QStyledItemDelegate):
    def __init__(self, win, round_art=False):
        super().__init__()
        self.win = win
        self.round_art = round_art
        self.W, self.H = 190, 250

    def sizeHint(self, option, index):
        return QSize(self.W, self.H)

    def paint(self, p, opt, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(opt.rect).adjusted(10, 10, -10, -6)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = rect.width()
        art = QRectF(rect.left(), rect.top(), side, side)
        pm = cover_pixmap(self.win.covers, r.get("cover_path"), int(side), r.get("title") or r.get("name"))
        if self.round_art:
            # исполнитель — портрет в скошенной рамке
            poly = skew_poly(art, side * 0.16)
            path = QPainterPath()
            path.addPolygon(poly)
            p.setClipPath(path)
            p.drawPixmap(art.toRect(), pm)
            p.setClipping(False)
            p.setPen(QPen(CYAN if (hover or sel) else QColor(255, 255, 255, 40), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(poly)
        else:
            if hover or sel:                                    # голубая «тень», как у выбранного пункта
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(CYAN)
                p.drawRect(art.translated(7, 7))
            p.drawPixmap(art.toRect(), pm)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(art)
        # подпись на скошенной плашке
        band = QRectF(rect.left() - 4, art.bottom() + 8, rect.width() + 8, 28)
        if hover or sel:
            g = QLinearGradient(band.topLeft(), band.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(band, 9))
        title = r.get("title") or r.get("name") or ""
        p.setFont(pfont(15))
        p.setPen(NAVY if (hover or sel) else WHITE)
        fm = QFontMetrics(p.font())
        p.drawText(band.adjusted(14, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, int(band.width() - 26)))
        sub = r.get("artist") if "artist" in r else f"{len(r.get('tracks', []))} трек. · {len(r.get('albums', []))} альб."
        p.setFont(pfont(12, bold=False))
        p.setPen(DIM)
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(band.left() + 12, band.bottom() + 2, band.width() - 16, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(sub or "", Qt.TextElideMode.ElideRight, int(band.width() - 16)))
        p.restore()


class TileView(QListView):
    def __init__(self, win, round_art=False):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(30)
        self.model_ = ListModel()
        self.setModel(self.model_)
        self.tiles = TileDelegate(win, round_art)
        self.setItemDelegate(self.tiles)

    def resizeEvent(self, e):
        # плитки растягиваются, чтобы ряд занимал всю ширину (без пустого края справа)
        avail = self.viewport().width() - 16
        cols = max(1, avail // 200)
        w = max(160, avail // cols - 12)
        if w != self.tiles.W:
            self.tiles.W, self.tiles.H = w, w + 60
            self.doItemsLayout()
        super().resizeEvent(e)


# ---------------------------------------------------------------- список треков
class TrackDelegate(QStyledItemDelegate):
    H = 46

    def __init__(self, win, show_album=True):
        super().__init__()
        self.win = win
        self.show_album = show_album

    def sizeHint(self, option, index):
        return QSize(400, self.H)

    def paint(self, p, opt, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(opt.rect)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        cur = self.win.is_current(r["path"])
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = rect.adjusted(52, 4, -8, -4)
        if cur:                                               # играет — белая→голубая скошенная плашка
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(0.65, ICE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(bar, 12))
        elif hover:
            c = QColor(CYAN)
            c.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(skew_poly(bar, 12))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(bar.left() + 10, rect.bottom()), QPointF(rect.right() - 8, rect.bottom()))
        # номер
        p.setFont(pfont(18))
        p.setPen(CYAN)
        num = "▶" if cur and self.win.owner_playing() else str(r.get("num", index.row() + 1))
        p.drawText(QRectF(rect.left(), rect.top(), 44, rect.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, num)
        ink = NAVY if cur else WHITE
        dim = QColor(30, 60, 110) if cur else DIM
        x = bar.left() + 22
        w = bar.width() - 22 - 70
        col_t = w * (0.5 if self.show_album else 0.6)
        col_a = w * (0.25 if self.show_album else 0.4)
        p.setFont(pfont(15, weight=QFont.Weight.Bold))
        fm = QFontMetrics(p.font())
        p.setPen(ink)
        p.drawText(QRectF(x, rect.top(), col_t - 12, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(r["title"], Qt.TextElideMode.ElideRight, int(col_t - 12)))
        p.setFont(pfont(13, bold=False))
        fm = QFontMetrics(p.font())
        p.setPen(dim)
        p.drawText(QRectF(x + col_t, rect.top(), col_a - 12, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(r["artist"], Qt.TextElideMode.ElideRight, int(col_a - 12)))
        if self.show_album:
            p.drawText(QRectF(x + col_t + col_a, rect.top(), w - col_t - col_a - 8, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(r["album"], Qt.TextElideMode.ElideRight, int(w - col_t - col_a - 8)))
        if self.win.owner.is_fav(r["path"]):
            p.setPen(QColor("#ff5f8a") if not cur else QColor("#d0245a"))
            p.drawText(QRectF(bar.right() - 92, rect.top(), 20, rect.height()), Qt.AlignmentFlag.AlignVCenter, "♥")
        p.setFont(pfont(14, weight=QFont.Weight.Bold))
        p.setPen(ink if cur else DIM)
        d = r.get("d") or 0
        p.drawText(QRectF(bar.right() - 70, rect.top(), 56, rect.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_ms(d * 1000) if d else "")
        p.restore()


class TrackView(QListView):
    def __init__(self, win, show_album=True):
        super().__init__()
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model_ = ListModel()
        self.setModel(self.model_)
        self.setItemDelegate(TrackDelegate(win, show_album))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)


# ---------------------------------------------------------------- шапка альбома / исполнителя
class Header(QWidget):
    play = Signal()
    shuffle = Signal()

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedHeight(250)
        self.kind, self.title, self.sub, self.meta, self.cover_path = "", "", "", "", None
        self.btn_play = QRectF()
        self.btn_shuf = QRectF()
        self.setMouseTracking(True)
        self.hover = None

    def set(self, kind, title, sub, meta, cover_path):
        self.kind, self.title, self.sub, self.meta, self.cover_path = kind, title, sub, meta, cover_path
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        art = QRectF(24, 24, 200, 200)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CYAN)
        p.drawRect(art.translated(9, 9))
        p.drawPixmap(art.toRect(), cover_pixmap(self.win.covers, self.cover_path, 200, self.title))
        x = art.right() + 40
        p.setFont(pfont(13))
        p.setPen(CYAN)
        p.drawText(QPointF(x, 54), self.kind)
        f = pfont(44)
        fm = QFontMetrics(f)
        avail = self.width() - x - 24
        while fm.horizontalAdvance(self.title) > avail and f.pixelSize() > 22:
            f.setPixelSize(f.pixelSize() - 2)
            fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(WHITE)
        p.drawText(QRectF(x, 60, avail, 64), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideRight, int(avail)))
        p.setFont(pfont(18, weight=QFont.Weight.Bold))
        p.setPen(ICE)
        p.drawText(QPointF(x + 2, 146), self.sub)
        p.setFont(pfont(13, bold=False))
        p.setPen(DIM)
        p.drawText(QPointF(x + 2, 172), self.meta)
        # кнопки
        self.btn_play = QRectF(x, 188, 170, 40)
        self.btn_shuf = QRectF(x + 182, 188, 170, 40)
        for rect, text, key in ((self.btn_play, "▶  СЛУШАТЬ", "play"), (self.btn_shuf, "🔀  ВПЕРЕМЕШКУ", "shuf")):
            on = self.hover == key
            g = QLinearGradient(rect.topLeft(), rect.topRight())
            g.setColorAt(0, WHITE if (key == "play" or on) else QColor(255, 255, 255, 0))
            g.setColorAt(1, CYAN if (key == "play" or on) else QColor(255, 255, 255, 0))
            p.setBrush(g)
            p.setPen(QPen(CYAN, 2))
            p.drawPolygon(skew_poly(rect, 12))
            p.setFont(pfont(15))
            p.setPen(NAVY if (key == "play" or on) else WHITE)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    def mouseMoveEvent(self, e):
        pos = e.position()
        h = "play" if self.btn_play.contains(pos) else "shuf" if self.btn_shuf.contains(pos) else None
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        pos = e.position()
        if self.btn_play.contains(pos):
            self.play.emit()
        elif self.btn_shuf.contains(pos):
            self.shuffle.emit()


class DetailPage(QWidget):
    """Альбом или исполнитель: шапка + треки."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setContentsMargins(18, 10, 18, 0)
        self.back = NavButton("‹  НАЗАД", small=True)
        self.back.clicked.connect(win.go_back)
        top.addWidget(self.back)
        top.addStretch(1)
        lay.addLayout(top)
        self.header = Header(win)
        lay.addWidget(self.header)
        self.tracks = TrackView(win)
        lay.addWidget(self.tracks, 1)
        self.paths = []
        self.header.play.connect(lambda: win.play_paths(self.paths, None))
        self.header.shuffle.connect(lambda: win.play_paths(self.paths, None, shuffle=True))
        self.tracks.doubleClicked.connect(lambda ix: win.play_paths(self.paths, self.paths[ix.row()]))
        self.tracks.customContextMenuRequested.connect(lambda pt: win.track_menu(self.tracks, pt, self.paths))

    def show_album(self, al):
        items = self.win.items
        self.paths = list(al["tracks"])
        rows = []
        for i, pth in enumerate(self.paths):
            t, a, alb = libmod.track_view(pth, items.get(pth, {}))
            n = items.get(pth, {}).get("n") or i + 1
            rows.append({"path": pth, "title": t, "artist": a, "album": alb, "d": items.get(pth, {}).get("d"),
                         "num": n})
        self.tracks.setItemDelegate(TrackDelegate(self.win, show_album=False))
        self.tracks.model_.set_rows(rows)
        self.header.set("АЛЬБОМ", al["title"], al["artist"],
                        f"{len(self.paths)} трек. · {fmt_total(al['duration'])}", al["cover_path"])

    def show_artist(self, ar):
        items = self.win.items
        self.paths = list(ar["tracks"])
        rows = []
        dur = 0
        for i, pth in enumerate(self.paths):
            it = items.get(pth, {})
            t, a, alb = libmod.track_view(pth, it)
            dur += it.get("d") or 0
            rows.append({"path": pth, "title": t, "artist": a, "album": alb, "d": it.get("d"), "num": i + 1})
        self.tracks.setItemDelegate(TrackDelegate(self.win, show_album=True))
        self.tracks.model_.set_rows(rows)
        self.header.set("ИСПОЛНИТЕЛЬ", ar["name"], f"{len(ar['albums'])} альб.",
                        f"{len(self.paths)} трек. · {fmt_total(dur)}", ar["cover_path"])


# ---------------------------------------------------------------- «Сейчас играет»
class NowPlaying(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._blur_key = None
        self._blur = None
        self.setMouseTracking(True)

    def _cover_img(self):
        o = self.win.owner
        img = getattr(o, "_cover", None)
        if img is not None and hasattr(img, "isNull") and not img.isNull():
            return img
        path = getattr(o, "loaded_path", None)
        return self.win.covers.get(path) if path else None

    def _blurred(self, img):
        key = (img.cacheKey() if img is not None else 0, self.width(), self.height())
        if key != self._blur_key:
            self._blur_key = key
            if img is None:
                self._blur = None
            else:
                small = img.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
                self._blur = QPixmap.fromImage(small.scaled(self.width(), self.width(),
                                                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                            Qt.TransformationMode.SmoothTransformation))
        return self._blur

    def paintEvent(self, _e):
        o = self.win.owner
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect()
        paint_bg(p, r)
        img = self._cover_img()
        bl = self._blurred(img)
        if bl is not None:
            p.setOpacity(0.35)
            p.drawPixmap(0, (r.height() - bl.height()) // 2, bl)
            p.setOpacity(1.0)
            veil = QLinearGradient(QPointF(r.topLeft()), QPointF(r.topRight()))
            veil.setColorAt(0, QColor(6, 19, 49, 120))
            veil.setColorAt(1, QColor(3, 10, 31, 235))
            p.fillRect(r, veil)

        # большая обложка, чуть повёрнутая
        side = min(int(r.height() * 0.62), int(r.width() * 0.36))
        cx, cy = r.width() * 0.25, r.height() * 0.46
        p.save()
        p.translate(cx, cy)
        p.rotate(-5)
        art = QRectF(-side / 2, -side / 2, side, side)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CYAN)
        p.drawRect(art.translated(12, 12))
        pm = (QPixmap.fromImage(img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation)).copy(0, 0, side, side)
              if img is not None else placeholder(side, getattr(o, "title", "")))
        p.drawPixmap(art.toRect(), pm)
        p.restore()

        # название — крупно, курсивом, с голубой плашкой под исполнителем
        x = r.width() * 0.48
        w = r.width() - x - 40
        title = getattr(o, "title", "") or "Ничего не играет"
        artist = getattr(o, "artist", "") or ""
        album = getattr(o, "album", "") or ""
        p.setFont(pfont(13))
        p.setPen(CYAN)
        p.drawText(QPointF(x, 70), "СЕЙЧАС ИГРАЕТ")
        f = pfont(46)
        fm = QFontMetrics(f)
        while fm.horizontalAdvance(title) > w and f.pixelSize() > 24:
            f.setPixelSize(f.pixelSize() - 2)
            fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(WHITE)
        p.drawText(QRectF(x, 76, w, 64), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, int(w)))
        if artist:
            band = QRectF(x - 6, 146, min(w, QFontMetrics(pfont(20)).horizontalAdvance(artist) + 50), 34)
            g = QLinearGradient(band.topLeft(), band.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(band, 11))
            p.setFont(pfont(20))
            p.setPen(NAVY)
            p.drawText(band.adjusted(18, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, artist)
        if album:
            p.setFont(pfont(14, bold=False))
            p.setPen(DIM)
            p.drawText(QPointF(x + 2, 206), album)

        # текст песни
        lyr = getattr(o, "lyrics", None)
        top = 232
        box = QRectF(x, top, w, r.height() - top - 30)
        if lyr is not None and len(lyr):
            pos = o.player.position()
            i = lyr.index_at(pos)
            n = len(lyr)
            line_h = 44
            rows = int(box.height() // line_h)
            first = max(0, min(i - rows // 3, n - rows))
            for k in range(first, min(n, first + rows)):
                yy = box.top() + (k - first) * line_h
                text = lyr.lines[k] or "♪"
                if k == i:
                    f = pfont(26)
                    fm = QFontMetrics(f)
                    tw = min(w, fm.horizontalAdvance(text) + 46)
                    band = QRectF(x - 8, yy + 2, tw, line_h - 6)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(47, 211, 255, 60))
                    p.drawPolygon(skew_poly(band, 12))
                    # заливка строки по ходу пения (как на экранчике плеера)
                    t0 = lyr.times[k]
                    t1 = lyr.times[k + 1] if k + 1 < n else t0 + 4000
                    frac = max(0.0, min(1.0, (pos - t0) / max(1, t1 - t0)))
                    p.setFont(f)
                    p.setPen(WHITE)
                    p.drawText(band.adjusted(18, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(text, Qt.TextElideMode.ElideRight, int(band.width() - 24)))
                    p.save()
                    p.setClipRect(QRectF(band.left(), band.top(), 18 + (band.width() - 24) * frac, band.height()))
                    p.setPen(CYAN)
                    p.drawText(band.adjusted(18, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(text, Qt.TextElideMode.ElideRight, int(band.width() - 24)))
                    p.restore()
                else:
                    f = pfont(19, weight=QFont.Weight.Bold)
                    fm = QFontMetrics(f)
                    p.setFont(f)
                    c = QColor(WHITE)
                    c.setAlpha(max(50, 190 - abs(k - i) * 35))
                    p.setPen(c)
                    p.drawText(QRectF(x + 10, yy, w - 10, line_h), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w - 10)))
        else:
            p.setFont(pfont(18, weight=QFont.Weight.Bold))
            p.setPen(DIM)
            p.drawText(box.adjusted(4, 10, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                       "ТЕКСТА НЕТ\n\nПоложи .lrc рядом с треком или сделай его сам:\n"
                       "меню плеера → Текст песни → Создать текст под песню")
        p.end()


# ---------------------------------------------------------------- кнопки
class NavButton(QPushButton):
    def __init__(self, text, small=False):
        super().__init__(text)
        self.small = small
        self.setCheckable(not small)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedHeight(34 if small else 46)
        if small:
            self.setFixedWidth(130)
        self._hover = False

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 4, -2, -4)
        on = self.isChecked()
        if on or self._hover:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, WHITE if on else QColor(255, 255, 255, 60))
            g.setColorAt(1, CYAN if on else QColor(47, 211, 255, 60))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(r, 12))
        if on:
            p.setBrush(CYAN)
            p.drawPolygon(skew_poly(QRectF(r.left() - 10, r.top(), 12, r.height()), 6))
        p.setFont(pfont(15 if self.small else 19))
        p.setPen(NAVY if on else WHITE)
        p.drawText(r.adjusted(22, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class IconButton(QPushButton):
    """⏮ ⏯ ⏭ — скошенные плашки."""

    def __init__(self, kind, size=46):
        super().__init__()
        self.kind = kind
        self.setFixedSize(int(size * 1.35), size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self.playing = False

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 3, -2, -3)
        main = self.kind == "play"
        if main or self._hover:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(1, CYAN)
            p.setBrush(g if main else QColor(47, 211, 255, 70))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(skew_poly(r, r.height() * 0.3))
        ink = NAVY if main else WHITE
        p.setBrush(ink)
        p.setPen(Qt.PenStyle.NoPen)
        c = r.center()
        s = r.height() * 0.22
        if self.kind == "play":
            if self.playing:
                p.drawRect(QRectF(c.x() - s * 0.9, c.y() - s, s * 0.6, s * 2))
                p.drawRect(QRectF(c.x() + s * 0.3, c.y() - s, s * 0.6, s * 2))
            else:
                p.drawPolygon(QPolygonF([QPointF(c.x() - s * 0.7, c.y() - s), QPointF(c.x() + s * 1.1, c.y()),
                                         QPointF(c.x() - s * 0.7, c.y() + s)]))
        else:
            d = 1 if self.kind == "next" else -1
            for off in (-s * 0.55, s * 0.55):
                cx = c.x() + off
                p.drawPolygon(QPolygonF([QPointF(cx - d * s * 0.6, c.y() - s * 0.8),
                                         QPointF(cx + d * s * 0.6, c.y()),
                                         QPointF(cx - d * s * 0.6, c.y() + s * 0.8)]))
        p.end()


# ---------------------------------------------------------------- нижняя панель
class PlayerBar(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setFixedHeight(92)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 22, 10)
        lay.setSpacing(14)
        self.art = QLabel()
        self.art.setFixedSize(66, 66)
        self.art.setCursor(Qt.CursorShape.PointingHandCursor)
        self.art.mousePressEvent = lambda _e: win.go("now")
        lay.addWidget(self.art)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.t = QLabel()
        self.t.setObjectName("bt")
        self.a = QLabel()
        self.a.setObjectName("ba")
        self.t.setFixedWidth(220)
        self.a.setFixedWidth(220)
        info.addStretch(1)
        info.addWidget(self.t)
        info.addWidget(self.a)
        info.addStretch(1)
        lay.addLayout(info)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        btns.addStretch(1)
        self.prev = IconButton("prev", 38)
        self.play = IconButton("play", 44)
        self.next = IconButton("next", 38)
        self.prev.clicked.connect(lambda: win.owner.step(-1))
        self.play.clicked.connect(win.owner.toggle_play)
        self.next.clicked.connect(lambda: win.owner.step(+1))
        for b in (self.prev, self.play, self.next):
            btns.addWidget(b)
        btns.addStretch(1)
        mid.addLayout(btns)
        prog = QHBoxLayout()
        self.cur = QLabel("0:00")
        self.cur.setObjectName("time")
        self.tot = QLabel("0:00")
        self.tot.setObjectName("time")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderReleased.connect(self._seek)
        self._dragging = False
        self.seek.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        prog.addWidget(self.cur)
        prog.addWidget(self.seek, 1)
        prog.addWidget(self.tot)
        mid.addLayout(prog)
        lay.addLayout(mid, 1)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(100)
        self.vol.valueChanged.connect(lambda v: (win.owner.player.setVolume(v / 100), win.owner.save_later()))
        vl = QLabel("🔊")
        vl.setObjectName("time")
        lay.addWidget(vl)
        lay.addWidget(self.vol)
        self._art_key = None

    def _seek(self):
        self._dragging = False
        d = self.win.owner.player.duration()
        if d > 0:
            self.win.owner.player.setPosition(int(d * self.seek.value() / 1000))

    def sync(self):
        o = self.win.owner
        fmw = QFontMetrics(pfont(16))
        self.t.setText(fmw.elidedText(getattr(o, "title", "") or "—", Qt.TextElideMode.ElideRight, 215))
        self.a.setText(QFontMetrics(pfont(13, bold=False)).elidedText(getattr(o, "artist", "") or "",
                                                                      Qt.TextElideMode.ElideRight, 215))
        self.play.playing = o.is_playing()
        self.play.update()
        pos, dur = o.player.position(), o.player.duration()
        self.cur.setText(fmt_ms(pos))
        self.tot.setText(fmt_ms(dur))
        if not self._dragging:
            self.seek.blockSignals(True)
            self.seek.setValue(int(1000 * pos / dur) if dur > 0 else 0)
            self.seek.blockSignals(False)
        if not self.vol.isSliderDown():
            self.vol.blockSignals(True)
            self.vol.setValue(int(round(o.player.volume() * 100)))
            self.vol.blockSignals(False)
        img = getattr(o, "_cover", None)
        path = getattr(o, "loaded_path", None)
        key = (img.cacheKey() if img is not None and hasattr(img, "cacheKey") else None, path)
        if key != self._art_key:
            self._art_key = key
            if img is not None and hasattr(img, "isNull") and not img.isNull():
                pm = QPixmap.fromImage(img.scaled(66, 66, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                  Qt.TransformationMode.SmoothTransformation)).copy(0, 0, 66, 66)
            else:
                pm = cover_pixmap(self.win.covers, path, 66, getattr(o, "title", ""))
            self.art.setPixmap(pm)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.fillRect(r, DEEP)
        p.setPen(QPen(CYAN, 2))
        p.drawLine(r.topLeft(), QPointF(r.width() * 0.55, r.top()))
        c = QColor(CYAN)
        c.setAlpha(60)
        p.setPen(QPen(c, 2))
        p.drawLine(QPointF(r.width() * 0.55, r.top()), r.topRight())
        p.end()


# ---------------------------------------------------------------- окно
class LibraryWindow(QWidget):
    """Большое окно «Медиатека». owner — Mp3Widget (плеер)."""

    def __init__(self, owner, cache_dir):
        super().__init__(None, Qt.WindowType.Window)
        self.owner = owner
        self.lib = owner.library
        self.setWindowTitle("Медиатека — MP3 Плеер")
        self.resize(1280, 800)
        self.setMinimumSize(980, 620)
        self.covers = CoverCache(cache_dir)
        self.covers.ready.connect(self._cover_ready)
        self.items = {}
        self.albums, self.artists = [], []
        self.history = []
        self._built_n = -1
        self._detail_obj = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(18, 22, 10, 18)
        sl.setSpacing(6)
        logo = QLabel("МЕДИАТЕКА")
        logo.setObjectName("logo")
        sl.addWidget(logo)
        self.count = QLabel("")
        self.count.setObjectName("count")
        sl.addWidget(self.count)
        sl.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        sl.addWidget(self.search)
        sl.addSpacing(14)
        self.nav = {}
        for key, text in (("albums", "АЛЬБОМЫ"), ("artists", "ИСПОЛНИТЕЛИ"), ("tracks", "ВСЕ ТРЕКИ"),
                          ("now", "СЕЙЧАС ИГРАЕТ")):
            b = NavButton(text)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            sl.addWidget(b)
            self.nav[key] = b
        sl.addStretch(1)
        self.scan_lbl = QLabel("")
        self.scan_lbl.setObjectName("count")
        self.scan_lbl.setWordWrap(True)
        sl.addWidget(self.scan_lbl)
        root.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        self.pages = {}
        self.albums_view = TileView(self)
        self.albums_view.clicked.connect(lambda ix: self.open_album(ix.data(Qt.ItemDataRole.UserRole)))
        self.artists_view = TileView(self, round_art=True)
        self.artists_view.clicked.connect(lambda ix: self.open_artist(ix.data(Qt.ItemDataRole.UserRole)))
        self.tracks_view = TrackView(self)
        self.tracks_paths = []
        self.tracks_view.doubleClicked.connect(
            lambda ix: self.play_paths(self.tracks_paths, self.tracks_paths[ix.row()]))
        self.tracks_view.customContextMenuRequested.connect(
            lambda pt: self.track_menu(self.tracks_view, pt, self.tracks_paths))
        self.detail = DetailPage(self)
        self.now = NowPlaying(self)
        for key, w, title in (("albums", self.albums_view, "АЛЬБОМЫ"), ("artists", self.artists_view, "ИСПОЛНИТЕЛИ"),
                              ("tracks", self.tracks_view, "ВСЕ ТРЕКИ"), ("detail", self.detail, None),
                              ("now", self.now, None)):
            page = w
            if title:
                page = QWidget()
                pl = QVBoxLayout(page)
                pl.setContentsMargins(22, 18, 12, 0)
                pl.setSpacing(8)
                h = QLabel(title)
                h.setObjectName("h1")
                pl.addWidget(h)
                if key == "tracks":
                    self.tracks_title = h
                pl.addWidget(w, 1)
            self.pages[key] = page
            self.stack.addWidget(page)
        right.addWidget(self.stack, 1)
        self.bar = PlayerBar(self)
        right.addWidget(self.bar)
        root.addLayout(right, 1)

        self.timer = QTimer(self, interval=80)
        self.timer.timeout.connect(self.tick)
        self._last_path = None
        self.restyle()
        self.go("albums")

    # ------------------------------------------------------------ стиль
    def restyle(self):
        c, w, d = CYAN.name(), WHITE.name(), DIM.name()
        self.setStyleSheet(f"""
            QWidget {{ color:{w}; font-family:'{FONT}'; }}
            #side {{ background:{DEEP.name()}; border-right:2px solid {CYAN_D.name()}; }}
            #logo {{ font-size:28px; font-weight:900; font-style:italic; color:{w}; letter-spacing:1px; }}
            #count {{ color:{d}; font-size:12px; font-style:italic; }}
            #h1 {{ font-size:34px; font-weight:900; font-style:italic; color:{w}; }}
            #bt {{ font-size:16px; font-weight:900; font-style:italic; }}
            #ba {{ font-size:13px; font-style:italic; color:{c}; }}
            #time {{ font-size:12px; font-weight:bold; color:{d}; min-width:38px; }}
            QLineEdit {{ background:#0e2350; border:2px solid {CYAN_D.name()}; border-radius:0;
                         padding:7px 10px; font-size:14px; font-style:italic; color:{w};
                         selection-background-color:{c}; }}
            QLineEdit:focus {{ border-color:{c}; }}
            QListView {{ background:transparent; border:none; outline:0; }}
            QListView::item:selected {{ background:transparent; }}
            QScrollBar:vertical {{ background:transparent; width:10px; margin:4px 2px; }}
            QScrollBar::handle:vertical {{ background:{CYAN_D.name()}; min-height:30px; }}
            QScrollBar::handle:vertical:hover {{ background:{c}; }}
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
                height:0; background:none; }}
            QSlider::groove:horizontal {{ height:4px; background:#1b3570; }}
            QSlider::sub-page:horizontal {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                                            stop:0 {w}, stop:1 {c}); }}
            QSlider::handle:horizontal {{ background:{w}; width:10px; margin:-6px 0;
                                          border:2px solid {c}; }}
            QMenu {{ background:#0b1d45; color:{w}; border:1px solid {c}; padding:4px; }}
            QMenu::item {{ padding:6px 26px 6px 16px; font-style:italic; }}
            QMenu::item:selected {{ background:{c}; color:{NAVY.name()}; }}
        """)

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_bg(p, self.rect())
        p.end()

    # ------------------------------------------------------------ данные
    def rebuild(self, force=False):
        with self.lib.lock:
            items = dict(self.lib.items)
        if not force and len(items) == self._built_n and items.keys() == self.items.keys():
            return
        self.items = items
        self._built_n = len(items)
        self.albums = libmod.build_albums(items)
        self.artists = libmod.build_artists(items, self.albums)
        self.albums_view.model_.set_rows(self.albums)
        self.artists_view.model_.set_rows([{**a, "title": a["name"]} for a in self.artists])
        self._fill_tracks(self.search.text())
        total = sum((it.get("d") or 0) for it in items.values())
        self.count.setText(f"{len(items)} трек. · {len(self.albums)} альб. · {len(self.artists)} исп.\n"
                           f"{fmt_total(total)} музыки")

    def _rows_for(self, paths):
        rows = []
        for i, pth in enumerate(paths):
            it = self.items.get(pth, {})
            t, a, alb = libmod.track_view(pth, it)
            rows.append({"path": pth, "title": t, "artist": a, "album": alb, "d": it.get("d"), "num": i + 1})
        return rows

    def _fill_tracks(self, q):
        if q.strip():
            paths = self.lib.search(q, limit=2000)
            self.tracks_title.setText(f"ПОИСК: «{q.strip()}»")
        else:
            paths = sorted(self.items, key=lambda p: tuple(libmod.norm(x) for x in
                                                            libmod.track_view(p, self.items[p])[1::-1]))
            self.tracks_title.setText("ВСЕ ТРЕКИ")
        self.tracks_paths = paths
        self.tracks_view.model_.set_rows(self._rows_for(paths))

    def _search_changed(self, q):
        self._fill_tracks(q)
        if q.strip():
            self.go("tracks", remember=False)

    def _cover_ready(self, path):
        for v in (self.albums_view, self.artists_view):
            v.model_.refresh_path(path)
            v.viewport().update()
        self.detail.header.update()
        if path == getattr(self.owner, "loaded_path", None):
            self.bar._art_key = None
            self.now.update()

    # ------------------------------------------------------------ навигация
    def go(self, key, remember=True):
        cur = self.stack.currentWidget()
        if remember and cur is not None and cur is not self.pages.get(key):
            self.history.append(self._state())
            self.history = self.history[-30:]
        self.stack.setCurrentWidget(self.pages[key])
        for k, b in self.nav.items():
            b.setChecked(k == key)
        self.timer.setInterval(40 if key == "now" else 250)

    def _state(self):
        w = self.stack.currentWidget()
        if w is self.pages["detail"]:
            return ("detail", self._detail_obj)
        return (next((k for k, v in self.pages.items() if v is w), "albums"), None)

    def go_back(self):
        if not self.history:
            self.go("albums", remember=False)
            return
        key, obj = self.history.pop()
        if key == "detail" and obj is not None:
            kind, data = obj
            (self.detail.show_album if kind == "album" else self.detail.show_artist)(data)
            self._detail_obj = obj
        self.go(key, remember=False)

    def open_album(self, al):
        self._detail_obj = ("album", al)
        self.detail.show_album(al)
        self.go("detail")

    def open_artist(self, ar):
        self._detail_obj = ("artist", ar)
        self.detail.show_artist(ar)
        self.go("detail")

    # ------------------------------------------------------------ игра
    def is_current(self, path):
        lp = getattr(self.owner, "loaded_path", None)
        return bool(lp) and os.path.normcase(lp) == os.path.normcase(path)

    def owner_playing(self):
        return self.owner.is_playing()

    def play_paths(self, paths, start, shuffle=False):
        if not paths:
            return
        self.owner.play_list(list(paths), start, shuffle=shuffle)
        QTimer.singleShot(150, lambda: [v.viewport().update() for v in (self.tracks_view, self.detail.tracks)])

    def track_menu(self, view, pt, paths):
        ix = view.indexAt(pt)
        if not ix.isValid():
            return
        path = paths[ix.row()]
        m = QMenu(self)
        m.addAction("▶  Играть", lambda: self.play_paths(paths, path))
        m.addAction("⏭  Играть следующим", lambda: self.owner.queue_next(path))
        m.addAction("✎  Теги…", lambda: self.owner.edit_tags(path))
        al = next((a for a in self.albums if path in a["tracks"]), None)
        if al:
            m.addAction(f"💿  Альбом «{al['title']}»", lambda: self.open_album(al))
        ar_name = libmod.track_view(path, self.items.get(path, {}))[1]
        ar = next((a for a in self.artists if a["name"] == ar_name), None)
        if ar:
            m.addAction(f"🎤  {ar['name']}", lambda: self.open_artist(ar))
        m.addAction("📂  Показать в папке", lambda: __import__("subprocess").Popen(["explorer", "/select,", path]))
        m.exec(view.viewport().mapToGlobal(pt))

    # ------------------------------------------------------------ жизнь окна
    def tick(self):
        self.bar.sync()
        if self.stack.currentWidget() is self.now:
            self.now.update()
        lp = getattr(self.owner, "loaded_path", None)
        if lp != self._last_path:
            self._last_path = lp
            self.tracks_view.viewport().update()
            self.detail.tracks.viewport().update()
        st = "сканирую папки…" if self.lib.scanning else ""
        if st != self.scan_lbl.text():
            self.scan_lbl.setText(st)
        if not self.lib.scanning and len(self.lib) != self._built_n:
            self.rebuild()

    def open(self):
        self.rebuild()
        self.timer.start()
        self.tick()
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Space and not self.search.hasFocus():
            self.owner.toggle_play()
        elif k == Qt.Key.Key_Escape:
            if self.search.text():
                self.search.clear()
            else:
                self.go_back()
        elif k == Qt.Key.Key_F and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.search.setFocus()
            self.search.selectAll()
        elif k == Qt.Key.Key_Backspace and not self.search.hasFocus():
            self.go_back()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.BackButton:
            self.go_back()
        else:
            super().mousePressEvent(e)
