# -*- coding: utf-8 -*-
"""Медиатека в стиле Persona 3 Reload: альбомы, исполнители и экран «Сейчас играет».

Большое окно поверх той же музыки, что у плеера-виджета: играет тот же плеер (owner),
те же теги, обложки, тексты .lrc и статистика. Окно только показывает и командует.
"""
import hashlib
import math
import os
import sys
import threading
import time

from PySide6.QtCore import (Qt, QTimer, QRect, QRectF, QPoint, QPointF, QSize, QObject, Signal, QModelIndex,
                            QAbstractListModel, QBuffer, QIODevice, QEvent)
from PySide6.QtGui import (QPainter, QColor, QFont, QFontMetrics, QPixmap, QImage, QPolygonF, QLinearGradient,
                           QPainterPath, QPen, QBrush, QIcon, QTransform, QRadialGradient)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QFrame,
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


# ---------------------------------------------------------------- звуки меню (свои, в духе P3R)
_SFX = {"obj": None}


def sfx(kind):
    """hover — курсор по пункту, select — выбор, back — назад."""
    o = _SFX["obj"]
    if o is not None:
        o.play(kind)


class UISounds:
    def __init__(self, folder, enabled_fn, volume_fn):
        from PySide6.QtMultimedia import QSoundEffect
        from PySide6.QtCore import QUrl
        self.enabled_fn, self.volume_fn = enabled_fn, volume_fn
        self.fx = {}
        self._last_hover = 0.0
        try:
            os.makedirs(folder, exist_ok=True)
            for kind, gen in (("hover", self._hover), ("select", self._select), ("back", self._back)):
                f = os.path.join(folder, f"ui_{kind}_v1.wav")
                if not os.path.isfile(f):
                    self._write(f, gen())
                e = QSoundEffect()
                e.setSource(QUrl.fromLocalFile(f))
                self.fx[kind] = e
        except Exception:
            self.fx = {}

    @staticmethod
    def _write(f, samples):
        import struct
        import wave as wav_std
        import numpy as np
        pcm = (np.clip(samples, -1, 1) * 32000).astype("<i2").tobytes()
        with wav_std.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(pcm)

    @staticmethod
    def _tone(freqs, dur, decay, sweep=0.0, start=0.0):
        import numpy as np
        sr = 44100
        t = np.arange(int(sr * (start + dur))) / sr
        out = np.zeros_like(t)
        tt = np.clip(t - start, 0, None)
        env = np.where(t >= start, np.exp(-tt * decay) * np.minimum(1, tt * 2000), 0)
        for f, a in freqs:
            ph = 2 * np.pi * (f * tt + 0.5 * sweep * tt * tt)
            out += a * np.sin(ph)
        return out * env

    def _hover(self):            # короткий стеклянный «тик»
        return 0.5 * self._tone([(2600, 0.7), (5200, 0.25)], 0.07, 70, sweep=-6000)

    def _select(self):           # яркий двойной «динь» вверх
        import numpy as np
        a = self._tone([(1320, 0.6), (2640, 0.2)], 0.2, 22)
        b = self._tone([(1980, 0.6), (3960, 0.2)], 0.26, 16, start=0.06)
        n = max(len(a), len(b))
        return 0.55 * (np.pad(a, (0, n - len(a))) + np.pad(b, (0, n - len(b))))

    def _back(self):             # мягкий спуск вниз
        return 0.5 * self._tone([(1500, 0.7), (3000, 0.15)], 0.14, 30, sweep=-5000)

    def play(self, kind):
        try:
            if not self.enabled_fn() or kind not in self.fx:
                return
            now = time.monotonic()
            if kind == "hover":
                if now - self._last_hover < 0.045:
                    return
                self._last_hover = now
            e = self.fx[kind]
            e.setVolume(max(0.0, min(1.0, float(self.volume_fn()))) * (0.45 if kind == "hover" else 0.7))
            e.play()
        except Exception:
            pass


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


_BG = {}


def bg_pixmap(w, h):
    """Тот же фон, но нарисованный один раз: градиент на всё окно в каждом кадре — это и грело процессор."""
    pm = _BG.get((w, h))
    if pm is None:
        if len(_BG) > 3:
            _BG.clear()
        pm = QPixmap(max(1, w), max(1, h))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_bg(p, QRect(0, 0, w, h))
        p.end()
        _BG[(w, h)] = pm
    return pm


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
        self.online = None           # fn(путь) → QImage|None — обложка из интернета (вызывается в фоне)
        self.artist_fn = None        # fn(имя) → QImage|None — фото исполнителя

    def get_artist(self, name):
        """Фото исполнителя (из интернета, с кэшем) или None, пока ищется / не нашлось."""
        return self.get("artist:" + name) if name else None

    def forget_missing(self):
        """Включили поиск в интернете — заново поискать то, чего не было."""
        self.mem = {k: v for k, v in self.mem.items() if v is not None}

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
                if path.startswith("artist:"):
                    img = None
                    if self.artist_fn:
                        try:
                            img = self.artist_fn(path[7:])
                        except Exception:
                            img = None
                    self.mem[path] = img
                    self.ready.emit(path)
                    continue
                img = None
                local_missing = False
                disk = self._disk(path)
                if os.path.isfile(disk):
                    img = QImage(disk)
                    if img.isNull():
                        img = None
                    elif img.width() <= 2:
                        img = None               # метка «в файле обложки нет»
                        local_missing = True
                if img is None and not local_missing:
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
                if img is None and self.online:                    # своей нет — поискать в интернете
                    try:
                        img = self.online(path)
                    except Exception:
                        img = None
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

    def flags(self, index):
        f = super().flags(index)
        if index.isValid() and self.rows[index.row()].get("path"):
            f |= Qt.ItemFlag.ItemIsDragEnabled
        return f

    def mimeTypes(self):
        return ["application/x-mp3p-paths", "text/uri-list"]

    def mimeData(self, indexes):
        from library_more import make_mime
        paths = [self.rows[i.row()]["path"] for i in indexes if i.isValid() and self.rows[i.row()].get("path")]
        return make_mime(paths)

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

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
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver) or bool(opt.state & QStyle.StateFlag.State_HasFocus)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = rect.width()
        art = QRectF(rect.left(), rect.top(), side, side)
        if r.get("cover_img") is not None or r.get("pid"):
            img = r.get("cover_img")
            if r.get("pid"):
                from library_more import playlist_image
                pl = self.win.playlists.get(r["pid"])
                img = playlist_image(self.win, pl) if pl else None
            pm = (QPixmap.fromImage(img.scaled(int(side), int(side), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                               Qt.TransformationMode.SmoothTransformation)).copy(0, 0, int(side), int(side))
                  if img is not None else placeholder(int(side), r.get("title", "")))
        elif self.round_art and r.get("name"):
            img = self.win.covers.get_artist(r["name"])           # фото исполнителя из интернета
            if img is not None:
                pm = QPixmap.fromImage(img.scaled(int(side), int(side), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                  Qt.TransformationMode.SmoothTransformation)).copy(0, 0, int(side), int(side))
            else:
                pm = cover_pixmap(self.win.covers, r.get("cover_path"), int(side), r.get("name"))
        else:
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


class _HoverSfx:
    """Звук курсора, когда мышь переходит на другую плитку / строку."""

    def _hover_sfx(self, e):
        ix = self.indexAt(e.position().toPoint())
        r = ix.row() if ix.isValid() else -1
        if r != getattr(self, "_hover_row", -1):
            self._hover_row = r
            if r >= 0:
                sfx("hover")


class TileView(QListView, _HoverSfx):
    def keyPressEvent(self, e):
        k = e.key()
        ix = self.currentIndex()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ix.isValid():
            sfx("select")
            self.clicked.emit(ix)
            return
        if k == Qt.Key.Key_Left and ix.isValid() and self.visualRect(ix).left() < 30 and hasattr(self, "win_"):
            self.win_.focus_sidebar()
            return
        super().keyPressEvent(e)

    def currentChanged(self, cur, prev):
        super().currentChanged(cur, prev)
        if self.hasFocus() and cur.isValid():
            sfx("hover")

    def mouseMoveEvent(self, e):
        self._hover_sfx(e)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if self.indexAt(e.position().toPoint()).isValid():
            sfx("select")
        super().mousePressEvent(e)

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
        self.win_ = win

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
def track_columns(rect, show_album=True, stats=False):
    """Колонки строки трека — общие для строк и для заголовка с сортировкой."""
    bar = QRectF(rect).adjusted(52, 4, -8, -4)
    R = bar.right()
    c = {"num": QRectF(rect.left(), rect.top(), 44, rect.height()),
         "dur": QRectF(R - 70, rect.top(), 56, rect.height())}
    right = R - 70
    if stats:
        c["added"] = QRectF(right - 96, rect.top(), 86, rect.height())
        c["plays"] = QRectF(right - 96 - 70, rect.top(), 60, rect.height())
        right -= 166
    c["fav"] = QRectF(right - 22, rect.top(), 20, rect.height())
    c["meter"] = QRectF(bar.left() + 14, rect.center().y() - 11, 22, 22)
    x = bar.left() + 48
    w = right - 26 - x
    ct = w * (0.46 if show_album else 0.6)
    ca = w * (0.27 if show_album else 0.4)
    c["title"] = QRectF(x, rect.top(), ct - 12, rect.height())
    c["artist"] = QRectF(x + ct, rect.top(), ca - 12, rect.height())
    if show_album:
        c["album"] = QRectF(x + ct + ca, rect.top(), w - ct - ca - 8, rect.height())
    return bar, c


class TrackDelegate(QStyledItemDelegate):
    H = 46

    def __init__(self, win, show_album=True, stats=False):
        super().__init__()
        self.win = win
        self.show_album = show_album
        self.stats = stats

    def sizeHint(self, option, index):
        return QSize(400, self.H)

    def _listened(self, path):
        t = self.win._stat_tracks().get(path) if hasattr(self.win, "_stat_tracks") else None
        return (float(t.get("sec") or 0), int(t.get("plays") or 0)) if t else (0.0, 0)

    def _max_for(self, model):
        """Самое большое время прослушивания среди строк этого списка (пересчёт — когда строки поменялись)."""
        rows = getattr(model, "rows", [])
        key = (id(rows), len(rows))
        if getattr(model, "_mx_key", None) != key:
            model._mx_key = key
            model._mx = max([self._listened(r.get("path"))[0] for r in rows if r.get("path")] + [0.0])
        return model._mx

    def helpEvent(self, event, view, option, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        if event.type() == QEvent.Type.ToolTip and r and r.get("path"):
            sec_l, plays_l = self._listened(r["path"])
            from PySide6.QtWidgets import QToolTip
            h, m = int(sec_l // 3600), int(sec_l % 3600 // 60)
            spent = (f"{h} ч {m} мин" if h else f"{m} мин") if sec_l >= 60 else ("меньше минуты" if sec_l else "ещё не слушал")
            QToolTip.showText(event.globalPos(), f"Слушал: {spent}" + (f" · {plays_l} раз" if plays_l else "") +
                              f"\n{r['path']}", view)
            return True
        return super().helpEvent(event, view, option, index)

    def paint(self, p, opt, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(opt.rect)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(opt.state & QStyle.StateFlag.State_HasFocus)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        cur = self.win.is_current(r["path"])
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar, col = track_columns(rect, self.show_album, self.stats)
        if selected and not cur:                             # выделено (Shift / Ctrl) — голубая плашка с меткой
            c = QColor(CYAN)
            c.setAlpha(75)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(skew_poly(bar, 12))
            p.setBrush(CYAN)
            p.drawPolygon(skew_poly(QRectF(bar.left() - 2, bar.top(), 8, bar.height()), 4))
        if cur:                                               # играет — белая→голубая скошенная плашка
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(0.65, ICE)
            g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(bar, 12))
        elif hover and not selected:
            c = QColor(CYAN)
            c.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(skew_poly(bar, 12))
        elif not selected:
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(bar.left() + 10, rect.bottom()), QPointF(rect.right() - 8, rect.bottom()))
        if focus:                                            # курсор клавиатуры — рамка, как в меню
            p.setPen(QPen(WHITE if not cur else NAVY, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(skew_poly(bar.adjusted(1, 1, -1, -1), 12))
        # номер
        p.setFont(pfont(18))
        p.setPen(CYAN)
        num = "▶" if cur and self.win.owner_playing() else str(r.get("num", index.row() + 1))
        p.drawText(col["num"], Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, num)
        ink = NAVY if cur else WHITE
        dim = QColor(30, 60, 110) if cur else DIM

        def text(key, s, f, pen, align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft):
            if key not in col:
                return
            p.setFont(f)
            p.setPen(pen)
            fm = QFontMetrics(f)
            p.drawText(col[key], align, fm.elidedText(s or "", Qt.TextElideMode.ElideRight, int(col[key].width())))
        # квадратик «сколько слушал»: заполнен по времени относительно самого слушаемого в этом списке
        sec_l, plays_l = self._listened(r["path"])
        mx = self._max_for(index.model())
        if mx > 0:
            q = col["meter"]
            lvl = min(1.0, sec_l / mx) if sec_l else 0.0
            p.setPen(QPen(QColor(30, 60, 110) if cur else QColor(127, 152, 196, 110), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(skew_poly(q, 5))
            if lvl > 0:
                fill = QRectF(q.left(), q.bottom() - q.height() * max(0.18, lvl), q.width(), q.height() * max(0.18, lvl))
                g = QLinearGradient(fill.topLeft(), fill.bottomLeft())
                g.setColorAt(0, WHITE if lvl > 0.6 else ICE)
                g.setColorAt(1, CYAN if not cur else CYAN_D)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.save()
                path_q = QPainterPath()
                path_q.addPolygon(skew_poly(q, 5))
                p.setClipPath(path_q)
                p.drawRect(fill)
                p.restore()
                if lvl > 0.66:                            # самые любимые — светятся
                    glow = QColor(CYAN)
                    glow.setAlpha(int(90 * lvl))
                    p.setPen(QPen(glow, 3))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPolygon(skew_poly(q.adjusted(-2, -2, 2, 2), 5))
        text("title", r["title"], pfont(15, weight=QFont.Weight.Bold), ink)
        text("artist", r["artist"], pfont(13, bold=False), dim)
        text("album", r["album"], pfont(13, bold=False), dim)
        if self.stats:
            right = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
            text("plays", str(r.get("plays") or "—"), pfont(13, weight=QFont.Weight.Bold), dim, right)
            text("added", r.get("added_s", ""), pfont(12, bold=False), dim, right)
        if self.win.owner.is_fav(r["path"]):
            text("fav", "♥", pfont(13, bold=False), QColor("#ff5f8a") if not cur else QColor("#d0245a"))
        d = r.get("d") or 0
        text("dur", fmt_ms(d * 1000) if d else "", pfont(14, weight=QFont.Weight.Bold), ink if cur else DIM,
             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        p.restore()


class TrackView(QListView, _HoverSfx):
    def mouseMoveEvent(self, e):
        self._hover_sfx(e)
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        sfx("select")
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        ix = self.currentIndex()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ix.isValid():
            sfx("select")
            self.doubleClicked.emit(ix)                   # Enter — как двойной клик: играть
            return
        if k == Qt.Key.Key_Delete and self.delete_cb and self.selected_paths():
            self.delete_cb(self.selected_paths())
            return
        if k == Qt.Key.Key_Left and self.win_ is not None:
            self.win_.focus_sidebar()
            return
        super().keyPressEvent(e)

    def currentChanged(self, cur, prev):
        super().currentChanged(cur, prev)
        if self.hasFocus() and cur.isValid():
            sfx("hover")

    def __init__(self, win, show_album=True):
        super().__init__()
        self.win_ = win
        self.delete_cb = None          # fn(paths) — Delete (плейлист, очередь)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model_ = ListModel()
        self.setModel(self.model_)
        self.setItemDelegate(TrackDelegate(win, show_album))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)   # Shift / Ctrl — несколько
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.reorder = None            # fn(src, dst) — перетаскивание внутри списка (плейлист, очередь)
        self.external_drop = None      # fn(paths, row) — бросили треки снаружи

    def selected_paths(self):
        rows = sorted({i.row() for i in self.selectedIndexes()})
        return [self.model_.rows[r]["path"] for r in rows if r < len(self.model_.rows)]

    def dragEnterEvent(self, e):
        if (e.source() is self and self.reorder) or (e.source() is not self and self.external_drop):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)
        if e.isAccepted():
            super().dragMoveEvent(e)
            e.acceptProposedAction()

    def dropEvent(self, e):
        ix = self.indexAt(e.position().toPoint())
        row = ix.row() if ix.isValid() else len(self.model_.rows)
        if ix.isValid() and e.position().y() > self.visualRect(ix).center().y():
            row += 1
        if e.source() is self and self.reorder:
            src = sorted({i.row() for i in self.selectedIndexes()})
            if len(src) == 1:
                s0 = src[0]
                self.reorder(s0, row - 1 if row > s0 else row)
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        elif self.external_drop:
            from library_more import paths_from_mime
            paths = paths_from_mime(e.mimeData())
            if paths:
                self.external_drop(paths, row)
            e.accept()
        else:
            e.ignore()



# ---------------------------------------------------------------- заголовок колонок с сортировкой
class SortHeader(QWidget):
    changed = Signal(str, bool)           # ключ, по убыванию

    LABELS = (("num", "#"), ("title", "НАЗВАНИЕ"), ("artist", "ИСПОЛНИТЕЛЬ"), ("album", "АЛЬБОМ"),
              ("plays", "ПРОСЛ."), ("added", "ДОБАВЛЕН"), ("dur", "ВРЕМЯ"))

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.key, self.desc = "num", False
        self.hover = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _cols(self):
        vp = self.view.viewport()
        top = self.window()
        off = vp.mapTo(top, QPoint(0, 0)).x() - self.mapTo(top, QPoint(0, 0)).x()
        _bar, c = track_columns(QRectF(off, 0, vp.width(), self.height()), True, True)
        return c

    def _at(self, x):
        for k, r in self._cols().items():
            if k != "fav" and r.left() - 6 <= x <= r.right() + 6:
                return k
        return None

    def mouseMoveEvent(self, e):
        h = self._at(e.position().x())
        if h != self.hover:
            self.hover = h
            if h:
                sfx("hover")
            self.update()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        k = self._at(e.position().x())
        if not k:
            return
        sfx("select")
        if k == self.key and k != "num":
            self.desc = not self.desc
        else:
            self.key = k
            self.desc = k in ("plays", "added", "dur")         # числа — сначала большие
        self.changed.emit(self.key, self.desc)
        self.update()

    def set_state(self, key, desc):
        self.key, self.desc = key, desc
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cols = self._cols()
        for k, label in self.LABELS:
            r = cols.get(k)
            if r is None:
                continue
            on = k == self.key
            p.setFont(pfont(12))
            p.setPen(CYAN if on else (WHITE if self.hover == k else DIM))
            txt = label + ((" ▼" if self.desc else " ▲") if on and k != "num" else "")
            align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if k in ("num", "plays", "added", "dur")
                                                    else Qt.AlignmentFlag.AlignLeft)
            p.drawText(r, align, txt)
        p.setPen(QPen(CYAN_D, 1))
        p.drawLine(QPointF(0, self.height() - 1), QPointF(self.width(), self.height() - 1))
        p.end()

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

    def set(self, kind, title, sub, meta, cover_path, cover_fn=None):
        self.kind, self.title, self.sub, self.meta, self.cover_path = kind, title, sub, meta, cover_path
        self.cover_fn = cover_fn        # плейлист: своя картинка или мозаика
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        art = QRectF(24, 24, 200, 200)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CYAN)
        p.drawRect(art.translated(9, 9))
        img = self.cover_fn() if getattr(self, "cover_fn", None) else None
        if img is not None:
            p.drawPixmap(art.toRect(), QPixmap.fromImage(img.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                                    Qt.TransformationMode.SmoothTransformation)).copy(0, 0, 200, 200))
        elif getattr(self, "cover_fn", None):
            p.drawPixmap(art.toRect(), placeholder(200, self.title))
        else:
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
        self.pl_btns = []
        for text, fn in (("✎ ПЕРЕИМЕНОВАТЬ", lambda: win.rename_playlist(self.pid)),
                         ("🖼 ОБЛОЖКА", lambda: win.playlist_cover(self.pid)),
                         ("🗑 УДАЛИТЬ", lambda: win.delete_playlist(self.pid))):
            b = P3Action(text)
            b.clicked.connect(fn)
            top.addWidget(b)
            self.pl_btns.append(b)
        lay.addLayout(top)
        self.pid = None
        self.header = Header(win)
        lay.addWidget(self.header)
        self.tracks = TrackView(win)
        lay.addWidget(self.tracks, 1)
        self.paths = []
        self.header.play.connect(lambda: win.play_paths(self.paths, None))
        self.header.shuffle.connect(lambda: win.play_paths(self.paths, None, shuffle=True))
        self.tracks.doubleClicked.connect(lambda ix: win.play_paths(self.paths, self.paths[ix.row()]))
        self.tracks.customContextMenuRequested.connect(
            lambda pt: win.track_menu(self.tracks, pt, self.paths, playlist=self.pid))

    def _mode(self, pid):
        self.pid = pid
        for b in self.pl_btns:
            b.setVisible(pid is not None)
        self.tracks.reorder = self._reorder if pid else None
        self.tracks.delete_cb = (lambda paths: (self.win.playlists.remove(self.pid, paths),
                                                self.win.playlists_changed())) if pid else None
        self.tracks.external_drop = self._drop if pid else None

    def _reorder(self, src, dst):
        self.win.playlists.move(self.pid, src, dst)
        self.win.playlists_changed()

    def _drop(self, paths, row):
        n = self.win.playlists.add(self.pid, paths)
        self.win.toast(f"＋ {n} В ПЛЕЙЛИСТ" if n else "УЖЕ ЕСТЬ")
        self.win.playlists_changed()

    def show_playlist(self, pl):
        self._mode(pl["id"])
        items = self.win.items
        self.paths = [p for p in pl["tracks"]]
        rows, dur = [], 0
        for i, pth in enumerate(self.paths):
            it = items.get(pth, {})
            t, a, alb = libmod.track_view(pth, it)
            dur += it.get("d") or 0
            rows.append({"path": pth, "title": t, "artist": a, "album": alb, "d": it.get("d"), "num": i + 1})
        self.tracks.setItemDelegate(TrackDelegate(self.win, show_album=True))
        self.tracks.model_.set_rows(rows)
        from library_more import playlist_image
        self.header.set("ПЛЕЙЛИСТ", pl["name"], f"{len(self.paths)} трек." if self.paths else "пока пусто",
                        f"{fmt_total(dur)} · перетаскивай треки, чтобы поменять порядок" if self.paths else
                        "Перетащи сюда треки или ПКМ по треку → «В плейлист…»", None,
                        cover_fn=lambda pl=pl: playlist_image(self.win, pl))

    def show_album(self, al):
        self._mode(None)
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
        self._mode(None)
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
        cov = self.win.covers
        self.header.set("ИСПОЛНИТЕЛЬ", ar["name"], f"{len(ar['albums'])} альб.",
                        f"{len(self.paths)} трек. · {fmt_total(dur)}", ar["cover_path"],
                        cover_fn=lambda n=ar["name"], cp=ar["cover_path"]: cov.get_artist(n) or cov.get(cp))


# ---------------------------------------------------------------- «Сейчас играет»

# ---------------------------------------------------------------- «Моя волна»
class WavePanel(QWidget):
    """Кнопка «Моя волна» с живыми волнами и выбором характера, как в Яндекс Музыке."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.o = win.owner
        self.t = 0.0
        self.amp = 0.3
        self._last = time.monotonic()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 6, 10, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addSpacing(44)                       # тут бегут волны
        chips = QHBoxLayout()
        chips.setSpacing(4)
        self.chips = {}
        import mywave
        for key, name in mywave.MODES.items():
            c = P3Chip(name)
            c.clicked.connect(lambda _=False, k=key: self.pick_mode(k))
            chips.addWidget(c)
            self.chips[key] = c
        chips.addStretch(1)
        lay.addLayout(chips)
        row = top
        self.go_btn = P3Action("▶  ЗАПУСТИТЬ")
        self.go_btn.setFixedWidth(150)
        self.go_btn.clicked.connect(self.toggle)
        self.dis_btn = P3Action("👎  НЕ ТО")
        self.dis_btn.clicked.connect(lambda: self.o.wave_dislike())
        self.fav_btn = P3Action("♡  НРАВИТСЯ")
        self.fav_btn.setFixedWidth(150)
        self.fav_btn.clicked.connect(lambda: self.o.toggle_favorite())
        for b in (self.dis_btn, self.fav_btn, self.go_btn):
            row.addWidget(b)
        self.sync()

    def active(self):
        try:
            return bool(self.o.wave_active())
        except Exception:
            return False

    def pick_mode(self, k):
        if self.active() and self.o.cfg.get("wave_mode") == k:
            return
        self.o.cfg["wave_mode"] = k
        self.o.start_wave(k)                  # нажал характер — волна сразу играет (и включается, если была выключена)
        self.sync()
        QTimer.singleShot(100, self.sync)

    def toggle(self):
        if self.active():
            self.o.stop_wave()
        else:
            self.o.start_wave(self.o.cfg.get("wave_mode", "mix"))
        QTimer.singleShot(100, self.sync)

    def sync(self):
        on = self.active()
        mode = self.o.cfg.get("wave_mode", "mix")
        for k, c in self.chips.items():
            c.setChecked(k == mode)
        self.go_btn.setText("■  ВЫКЛЮЧИТЬ" if on else "▶  ЗАПУСТИТЬ")
        self.dis_btn.setVisible(on)
        self.fav_btn.setVisible(on)
        path = getattr(self.o, "loaded_path", None)
        try:
            liked = bool(path) and self.o.is_fav(path)
        except Exception:
            liked = False
        self.fav_btn.setText("♥  В ИЗБРАННОМ" if liked else "♡  НРАВИТСЯ")

    def animate(self):
        """Следующий кадр волн: перерисовать только полосу с волнами, а не всю панель с кнопками."""
        self.update(0, 62 - 46, self.width(), 92)

    def paintEvent(self, _e):
        now = time.monotonic()
        dt = max(0.0, min(0.05, now - self._last))
        self._last = now
        on = self.active()
        playing = False
        try:
            playing = bool(self.o.is_playing())
        except Exception:
            pass
        target = 1.0 if (on and playing) else (0.45 if on else 0.22)
        self.amp += (target - self.amp) * (1 - math.exp(-dt * 3))
        self.t += dt * (0.6 + 1.4 * self.amp)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        # волны: три синусоиды, бегут и дышат
        mid = r.top() + 62
        for i, (col, a, k, sp, wdt) in enumerate(((CYAN, 16, 0.016, 1.0, 3.0), (WHITE, 11, 0.022, -1.3, 2.0),
                                                   (CYAN_D, 20, 0.011, 0.7, 2.0))):
            pts = []
            amp = a * self.amp
            for x in range(0, int(r.width()) + 10, 10):
                env = math.sin(math.pi * x / max(1.0, r.width()))         # к краям волна затихает
                pts.append(QPointF(x, mid + math.sin(x * k + self.t * 2.4 * sp + i) * amp * env
                                   + math.sin(x * k * 2.3 - self.t * 1.7 * sp) * amp * 0.35 * env))
            c = QColor(col)
            c.setAlpha(230 if i == 0 else 140)
            # толстая сглаженная линия рисуется раз в двадцать дольше тонкой — кладём несколько тонких рядом
            n_l = int(wdt)
            c.setAlpha(int(c.alpha() * (0.8 if n_l > 1 else 1.0)))
            p.setPen(QPen(c, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for j in range(n_l):
                off = j - (n_l - 1) / 2.0
                p.translate(0, off)
                p.drawPolyline(pts)
                p.translate(0, -off)
        # заголовок поверх волн
        p.setFont(pfont(30))
        p.setPen(WHITE)
        p.drawText(QPointF(14, 34), "МОЯ ВОЛНА")
        p.setFont(pfont(13))
        p.setPen(CYAN)
        if on:
            import mywave
            p.drawText(QPointF(214, 32), "ИГРАЕТ · " + mywave.MODES.get(self.o.cfg.get("wave_mode", "mix"), ""))
        else:
            p.setPen(DIM)
            p.drawText(QPointF(214, 32), "бесконечный поток твоей музыки")
        p.end()


class NowPlaying(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._blur_key = None
        self._blur = None
        self._back_key = None
        self._back = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)     # сама закрашивает всё — окно под ней не рисуем
        self.setMouseTracking(True)
        # анимация: обложка «парит» под мышкой, текст плавно едет и перетекает
        self.mouse = None
        self.hover = 0.0
        self.rx = self.ry = 0.0
        self.scroll = None
        self._last = time.monotonic()
        self._t = 0.0
        self._pm_key = None
        self._pm = None
        self.cover_rect = QRectF()
        # громкость колёсиком: шкала выезжает, столбики догоняют, цифра «подпрыгивает»
        self.vol_until = 0.0            # до какого момента показывать шкалу
        self.vol_a = 0.0                # видимость 0..1
        self.vol_v = None               # показанное значение (плавно догоняет настоящее)
        self.vol_kick = 0.0             # толчок при каждом щелчке колеса (+ громче, − тише)
        self._wheel_acc = 0.0
        self._mouse_t = 0.0             # когда последний раз двигали мышью над страницей
        self._glide_until = 0.0         # после клика по строке текст плавно доезжает, а не прыгает
        self._pulse = (-1, 0.0)         # (строка, когда кликнули) — вспышка на выбранной строке
        self.wave = WavePanel(win)
        self.wave.setParent(self)

    def resizeEvent(self, e):
        self.wave.setGeometry(18, self.height() - 132, self.width() - 36, 124)
        super().resizeEvent(e)

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        self._mouse_t = time.monotonic()
        on = self._line_at(self.mouse) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _e):
        self.mouse = None

    def wheelEvent(self, e):
        """Колёсико в любом месте страницы — громкость (шаг 5 %)."""
        d = e.angleDelta().y() or e.angleDelta().x()
        if not d:
            return
        self._wheel_acc += d / 120.0
        steps = int(self._wheel_acc)                # тачпад шлёт мелкие доли — копим до целого шага
        e.accept()
        if not steps:
            return
        self._wheel_acc -= steps
        o = self.win.owner
        try:
            cur = float(o.player.volume())
        except Exception:
            return
        v = min(1.0, max(0.0, round(cur + 0.05 * steps, 2)))
        if self.vol_v is None or self.vol_a < 0.02:
            self.vol_v = cur
        if v != cur:
            o.player.setVolume(v)
            getattr(o, "save_later", lambda: None)()
            sfx("hover")
        self.vol_kick = 1.0 if steps > 0 else -1.0
        self.vol_until = time.monotonic() + 1.6
        self.update()

    def _paint_volume(self, p, dt, cx, cy, side):
        o = self.win.owner
        try:
            real = float(o.player.volume())
        except Exception:
            return
        k = 1 - math.exp(-dt * 12)
        self.vol_a += ((1.0 if time.monotonic() < self.vol_until else 0.0) - self.vol_a) * k
        if self.vol_a < 0.01 or not isinstance(real, float):
            return
        self.vol_v = real if self.vol_v is None else self.vol_v + (real - self.vol_v) * (1 - math.exp(-dt * 16))
        self.vol_kick *= math.exp(-dt * 7)
        a, kick = self.vol_a, self.vol_kick
        w, h = max(300.0, min(side * 1.05, 440.0)), 78.0
        box = QRectF(cx - w / 2 - (1 - a) * 46, cy + side / 2 - h * 0.35, w, h)      # выезжает слева
        p.save()
        p.setOpacity(a)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawPolygon(skew_poly(box.translated(8, 8), 20))
        g = QLinearGradient(box.topLeft(), box.bottomRight())
        g.setColorAt(0, QColor(8, 26, 70, 245))
        g.setColorAt(1, QColor(3, 10, 31, 245))
        p.setBrush(g)
        p.setPen(QPen(CYAN, 2))
        p.drawPolygon(skew_poly(box, 20))
        p.setFont(pfont(12))
        p.setPen(CYAN)
        label = "ГРОМКОСТЬ" + ("  ▲" if kick > 0.15 else ("  ▼" if kick < -0.15 else ""))
        p.drawText(QRectF(box.left() + 30, box.top() + 8, 200, 18), Qt.AlignmentFlag.AlignVCenter, label)
        # столбики: растут слева направо, последний «живой» подпрыгивает на щелчке
        n = 20
        bx, bw_all = box.left() + 26, box.width() - 132
        step = bw_all / n
        base = box.bottom() - 14
        fill = self.vol_v * n
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            part = max(0.0, min(1.0, fill - i))
            hh = 10 + 24 * (i + 1) / n
            near = max(0.0, 1.0 - abs(i + 0.5 - fill) / 2.5)
            hh += 9 * abs(kick) * near
            r = QRectF(bx + i * step, base - hh, step * 0.62, hh)
            p.setBrush(QColor(255, 255, 255, 34))
            p.drawPolygon(skew_poly(r, 4))
            if part > 0:
                c = QColor(WHITE) if near > 0.5 and abs(kick) > 0.2 else QColor(CYAN if i >= n * 0.5 else ICE)
                c.setAlpha(int(255 * (0.35 + 0.65 * part)))
                p.setBrush(c)
                p.drawPolygon(skew_poly(QRectF(r.left(), r.bottom() - hh * part, r.width(), hh * part), 4))
        # цифра — крупно, на щелчке увеличивается и возвращается
        num = QRectF(box.right() - 108, box.top() + 4, 92, h - 8)
        p.translate(num.center())
        sc = 1.0 + 0.22 * abs(kick)
        p.scale(sc, sc)
        p.translate(0, -5 * kick)
        p.setFont(pfont(34))
        p.setPen(WHITE if real > 0 else DIM)
        p.drawText(QRectF(-60, -30, 120, 60), Qt.AlignmentFlag.AlignCenter,
                   f"{int(round(self.vol_v * 100))}" if real > 0 else "ВЫКЛ")
        p.restore()

    def _line_at(self, pos, index=False):
        for rect, t, k in getattr(self, "_line_hits", []):
            if rect.contains(pos):
                return k if index else t
        return None

    def mousePressEvent(self, e):
        """Клик по строке текста — перемотать на неё."""
        t = self._line_at(e.position())
        if t is not None and e.button() == Qt.MouseButton.LeftButton:
            sfx("select")
            o = self.win.owner
            o.player.setPosition(int(max(0, t)))
            if not o.is_playing():
                o.toggle_play()
            now = time.monotonic()
            self._glide_until = now + 1.5       # текст плавно доедет до этой строки, как бы далеко она ни была
            self._pulse = (self._line_at(e.position(), index=True), now)
            return
        super().mousePressEvent(e)

    def _cover_pm(self, img, side, seed):
        key = (img.cacheKey() if img is not None else seed, side)
        if key != self._pm_key:
            self._pm_key = key
            self._pm = (QPixmap.fromImage(img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                     Qt.TransformationMode.SmoothTransformation)).copy(0, 0, side, side)
                        if img is not None else placeholder(side, seed))
        return self._pm

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

    @staticmethod
    def _palette(img):
        """Два цвета обложки: самый сочный (акцент) и средний (общий тон)."""
        small = img.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB32)
        best, score, rs, gs, bs = None, -1.0, 0, 0, 0
        for y in range(small.height()):
            for x in range(small.width()):
                c = small.pixelColor(x, y)
                rs, gs, bs = rs + c.red(), gs + c.green(), bs + c.blue()
                sc = c.hsvSaturationF() * (0.35 + c.valueF()) * (1.0 if c.valueF() > 0.25 else 0.3)
                if sc > score:
                    best, score = c, sc
        n = max(1, small.width() * small.height())
        avg = QColor(rs // n, gs // n, bs // n)
        if best is None or best.hsvSaturationF() < 0.18:        # серая обложка — фирменный голубой
            accent = QColor(CYAN)
        else:
            accent = QColor.fromHsvF(max(0.0, best.hsvHueF()), min(0.85, max(0.5, best.hsvSaturationF())), 0.95)
        return accent, avg

    def _backdrop(self, img):
        """Фон страницы целиком — собирается один раз на трек, поэтому может быть дорогим:
        размытая обложка в её же цветах, свечение за обложкой, косые полосы и затемнение под текст."""
        r = self.rect()
        key = (img.cacheKey() if img is not None else 0, r.width(), r.height())
        if key != self._back_key or self._back is None:
            self._back_key = key
            w, h = max(1, r.width()), max(1, r.height())
            if img is None:
                self._back = QPixmap(bg_pixmap(w, h))
                return self._back
            accent, avg = self._palette(img)
            pm = QPixmap(w, h)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            # 1) основа: тёмный тон обложки, смешанный с фирменным синим
            def mix(c1, c2, k):
                return QColor(int(c1.red() * (1 - k) + c2.red() * k), int(c1.green() * (1 - k) + c2.green() * k),
                              int(c1.blue() * (1 - k) + c2.blue() * k))
            tone = QColor.fromHsvF(max(0.0, avg.hsvHueF()), min(0.75, avg.hsvSaturationF() + 0.15), 0.26)
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, mix(tone, NAVY2, 0.45))
            g.setColorAt(1, mix(tone, DEEP, 0.8))
            p.fillRect(0, 0, w, h, g)
            # 2) сама обложка — сильно размытая (в три приёма, чтобы без квадратов), на весь экран
            soft = img.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            soft = soft.scaled(160, 160, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            soft = soft.scaled(56, 56, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            side = int(max(w, h) * 1.25)
            big = soft.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.setOpacity(0.62)
            p.drawImage((w - side) // 2, (h - side) // 2, big)           # с запасом: края картинки за экраном
            p.setOpacity(1.0)
            # 3) свечение за обложкой — её самый сочный цвет
            cx, cy = w * 0.25, (h - 132) * 0.5
            glow = QRadialGradient(QPointF(cx, cy), max(w, h) * 0.48)
            c0, c1, c2 = QColor(accent), QColor(accent), QColor(accent)
            c0.setAlpha(120), c1.setAlpha(46), c2.setAlpha(0)
            glow.setColorAt(0, c0)
            glow.setColorAt(0.45, c1)
            glow.setColorAt(1, c2)
            p.fillRect(0, 0, w, h, glow)
            # 4) косые полосы, как в меню, — подкрашены цветом обложки
            p.setPen(Qt.PenStyle.NoPen)
            for x, ww, al in ((0.56, 0.20, 20), (0.74, 0.07, 30), (0.88, 0.26, 14), (0.08, 0.04, 12)):
                c = mix(accent, WHITE, 0.25)
                c.setAlpha(al)
                p.setBrush(c)
                x0, wd, sk = w * x, w * ww, h * 0.45
                p.drawPolygon(QPolygonF([QPointF(x0 + sk, 0), QPointF(x0 + sk + wd, 0),
                                         QPointF(x0 + wd - sk, h), QPointF(x0 - sk, h)]))
            # 5) затемнение: справа под текст, снизу под «Мою волну», по краям — виньетка
            veil = QLinearGradient(0, 0, w, 0)
            veil.setColorAt(0, QColor(3, 8, 26, 40))
            veil.setColorAt(0.45, QColor(3, 8, 26, 120))
            veil.setColorAt(1, QColor(2, 6, 20, 215))
            p.fillRect(0, 0, w, h, veil)
            low = QLinearGradient(0, h - 260, 0, h)
            low.setColorAt(0, QColor(2, 6, 20, 0))
            low.setColorAt(1, QColor(2, 6, 20, 200))
            p.fillRect(0, max(0, h - 260), w, 260, low)
            vig = QRadialGradient(QPointF(w * 0.4, h * 0.45), max(w, h) * 0.75)
            vig.setColorAt(0.55, QColor(0, 0, 0, 0))
            vig.setColorAt(1, QColor(0, 0, 0, 150))
            p.fillRect(0, 0, w, h, vig)
            p.end()
            self._back = pm
        return self._back

    def paintEvent(self, _e):
        o = self.win.owner
        p = QPainter(self)
        r = self.rect()
        img = self._cover_img()
        p.drawPixmap(0, 0, self._backdrop(img))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # ---- время кадра
        now = time.monotonic()
        dt = max(0.0, min(0.05, now - self._last))
        self._last = now
        self._t += dt

        def ease(cur, target, speed):
            return cur + (target - cur) * (1 - math.exp(-dt * speed))

        # большая обложка: под мышкой поднимается, парит и наклоняется за курсором (3D)
        side = min(int((r.height() - 132) * 0.74), int(r.width() * 0.32))
        cx, cy = r.width() * 0.25, (r.height() - 132) * 0.5
        self.cover_rect = QRectF(cx - side * 0.6, cy - side * 0.6, side * 1.2, side * 1.2)
        over = self.mouse is not None and self.cover_rect.contains(self.mouse)
        self.hover = ease(self.hover, 1.0 if over else 0.0, 7)
        if over:
            tx = max(-1.0, min(1.0, (self.mouse.x() - cx) / (side / 2)))
            ty = max(-1.0, min(1.0, (self.mouse.y() - cy) / (side / 2)))
        else:
            tx = ty = 0.0
        self.ry = ease(self.ry, tx * 16, 9)
        self.rx = ease(self.rx, -ty * 13, 9)
        hv = self.hover
        bob = math.sin(self._t * 2.1) * 9 * hv
        wob = math.sin(self._t * 1.4) * 1.6 * hv
        tr = QTransform()
        tr.translate(cx, cy - 14 * hv + bob)
        tr.rotate(self.ry, Qt.Axis.YAxis)
        tr.rotate(self.rx, Qt.Axis.XAxis)
        tr.rotate(-5 * (1 - 0.5 * hv) + wob)
        tr.scale(1 + 0.06 * hv, 1 + 0.06 * hv)
        art = QRectF(-side / 2, -side / 2, side, side)
        p.save()
        p.setTransform(tr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 + 50 * hv)))              # тень — дальше, когда «взлетает»
        p.drawRect(art.translated(18 + 22 * hv - self.ry * 0.8, 22 + 26 * hv + self.rx * 0.8))
        p.setBrush(CYAN)
        p.drawRect(art.translated(12 + 6 * hv - self.ry * 0.4, 12 + 6 * hv + self.rx * 0.4))
        p.drawPixmap(art.toRect(), self._cover_pm(img, side, getattr(o, "title", "")))
        if hv > 0.01:                                                 # блик скользит за наклоном
            gx = art.left() + art.width() * (0.5 - self.ry / 32)
            gy = art.top() + art.height() * (0.5 + self.rx / 26)
            g = QLinearGradient(QPointF(gx - side * 0.5, gy - side * 0.5), QPointF(gx + side * 0.5, gy + side * 0.5))
            g.setColorAt(0.0, QColor(255, 255, 255, 0))
            g.setColorAt(0.45, QColor(255, 255, 255, int(70 * hv)))
            g.setColorAt(0.55, QColor(255, 255, 255, int(70 * hv)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(g)
            p.drawRect(art)
        p.restore()

        self._paint_volume(p, dt, cx, cy, side)

        # название — крупно, курсивом, с голубой плашкой под исполнителем
        x = r.width() * 0.48
        w = r.width() - x - 40
        title = getattr(o, "title", "") or "Ничего не играет"
        artist = self.win.now_artist()
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
        box = QRectF(x, top, w, r.height() - top - 140)
        if lyr is not None and len(lyr):
            sp = getattr(o, "smooth_pos", None)
            pos = sp() if callable(sp) else None
            if not isinstance(pos, (int, float)):
                pos = o.player.position()
            i = max(0, lyr.index_at(pos))
            n = len(lyr)
            line_h = 44
            rows = max(1, int(box.height() // line_h))
            # строки плавно «едут» к текущей, а не прыгают
            gliding = now < self._glide_until
            if gliding and now - self._pulse[1] < 0.35 and 0 <= self._pulse[0] < n:
                i = self._pulse[0]                              # движок ещё перематывает — уже едем к выбранной
            if self.scroll is None or (abs(self.scroll - i) > 6 and not gliding):
                self.scroll = float(i)
            if gliding:                                         # плавный разгон и торможение, без рывка в начале
                dist = float(i) - self.scroll
                stepv = dist * (1 - math.exp(-dt * 9.0))
                cap = max(0.6, abs(dist) * 0.9) * dt * 14
                self.scroll += max(-cap, min(cap, stepv))
            else:
                self.scroll = ease(self.scroll, float(i), 7.5)
            first = max(0.0, min(self.scroll - rows // 3, float(max(0, n - rows))))
            p.save()
            p.setClipRect(box.adjusted(-20, -6, 20, 0))
            self._line_hits = []
            for k in range(max(0, int(first) - 1), min(n, int(first) + rows + 2)):
                yy = box.top() + (k - first) * line_h
                hit = QRectF(x - 8, yy, w + 8, line_h)
                if box.adjusted(-20, -6, 20, 0).intersects(hit):
                    self._line_hits.append((hit, lyr.times[k], k))
                    if self.mouse is not None and hit.contains(self.mouse) and k != i:
                        hv_c = QColor(47, 211, 255, 28)          # строка под мышкой — клик перемотает сюда
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(hv_c)
                        p.drawPolygon(skew_poly(hit.adjusted(0, 3, 0, -3), 12))
                text = lyr.lines[k] or "♪"
                fk = max(0.0, 1.0 - abs(k - self.scroll))             # насколько строка «текущая» (0..1)
                edge = min(1.0, max(0.0, (yy - box.top() + line_h) / line_h),
                           max(0.0, (box.bottom() - yy) / (line_h * 1.5)))   # мягкое исчезание у краёв
                # один шрифт, а размер — масштабом: растёт плавно, без скачков по пикселям
                sc = (19 + 7 * fk) / 26.0
                f = pfont(26)
                fm = QFontMetrics(f)
                indent = 10 + 8 * fk
                avail = (w - indent - 6) / sc
                txt = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(avail))
                tw = fm.horizontalAdvance(txt)
                if fk > 0.01:
                    band = QRectF(x - 8, yy + 2, min(w, tw * sc + 46), line_h - 6)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(47, 211, 255, int(60 * fk * edge)))
                    p.drawPolygon(skew_poly(band, 12))
                if k == self._pulse[0] and now - self._pulse[1] < 0.6:      # вспышка на выбранной строке
                    pu = 1.0 - (now - self._pulse[1]) / 0.6
                    pb = QRectF(x - 8 - 14 * (1 - pu), yy + 2, min(w, tw * sc + 46) + 28 * (1 - pu), line_h - 6)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, int(90 * pu * pu * edge)))
                    p.drawPolygon(skew_poly(pb, 12))
                c = QColor(WHITE)
                base = max(50, 190 - abs(k - self.scroll) * 35)
                c.setAlpha(int((base + (255 - base) * fk) * edge))
                p.save()
                p.translate(x - 8 + indent, yy + line_h / 2)
                p.scale(sc, sc)
                local = QRectF(0, -line_h / (2 * sc), avail + 4, line_h / sc)
                p.setFont(f)
                p.setPen(c)
                p.drawText(local, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
                if k == i and fk > 0.01:
                    # заливка строки по ходу пения, с мягким краем
                    t0 = lyr.times[k]
                    t1 = lyr.times[k + 1] if k + 1 < n else t0 + 4000
                    frac = max(0.0, min(1.0, (pos - t0) / max(1, t1 - t0)))
                    ex = tw * frac
                    g = QLinearGradient(QPointF(ex - 30, 0), QPointF(ex + 2, 0))
                    cc = QColor(CYAN)
                    cc.setAlpha(int(255 * fk * edge))
                    c2 = QColor(CYAN)
                    c2.setAlpha(0)
                    g.setColorAt(0, cc)
                    g.setColorAt(1, c2)
                    p.setClipRect(QRectF(-4, local.top(), max(0.0, ex + 6), local.height()), Qt.ClipOperation.IntersectClip)
                    p.setPen(QPen(QBrush(g), 1))
                    p.drawText(local, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
                p.restore()
            p.restore()
        else:
            p.setFont(pfont(18, weight=QFont.Weight.Bold))
            p.setPen(DIM)
            self._line_hits = []
            p.drawText(box.adjusted(4, 10, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                       "ТЕКСТА НЕТ\n\nПоложи .lrc рядом с треком или сделай его сам:\n"
                       "меню плеера → Текст песни → Создать текст под песню")
        p.end()


# ---------------------------------------------------------------- кнопки
class _Clicky:
    """Звук выбора при нажатии (кнопка «назад» — свой звук)."""

    def _sfx_click(self):
        sfx("back" if "НАЗАД" in self.text() else "select")


class NavButton(QPushButton, _Clicky):
    def focusInEvent(self, e):
        self._hover = True
        sfx("hover")
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._hover = False
        self.update()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        w = self.nav_win
        if w is not None and k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            w.focus_nav_step(self, -1 if k == Qt.Key.Key_Up else 1)
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Right, Qt.Key.Key_Space):
            self._sfx_click()
            self.click()
            if w is not None and k != Qt.Key.Key_Space:
                QTimer.singleShot(0, w.focus_content)
            return
        super().keyPressEvent(e)

    def __init__(self, text, small=False):
        super().__init__(text)
        self.pressed.connect(self._sfx_click)
        self.small = small
        self.setCheckable(not small)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if not small else Qt.FocusPolicy.TabFocus)
        self.nav_win = None             # окно — для стрелок между пунктами
        self.setFixedHeight(34 if small else 46)
        if small:
            self.setFixedWidth(130)
        self._hover = False

    def enterEvent(self, _e):
        self._hover = True
        sfx("hover")
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
    """⏮ ⏯ ⏭ и «перемешать» / «повтор» — скошенные плашки."""

    def __init__(self, kind, size=46):
        super().__init__()
        self.pressed.connect(lambda: sfx("select"))
        self.kind = kind
        self.setFixedSize(int(size * 1.35), size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self.playing = False
        self.mode = ""               # повтор: off / all / one; перемешать: on / off

    def enterEvent(self, _e):
        self._hover = True
        sfx("hover")
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
        if self.kind in ("repeat", "shuffle"):
            on = self.mode not in ("off", "")
            col = QColor(CYAN if on else WHITE)
            col.setAlpha(255 if on else (200 if self._hover else 105))
            pen = QPen(col, max(1.8, s * 0.28), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            def head(tip, dx):                              # наконечник стрелки (dx: +1 вправо, −1 влево)
                p.drawLine(tip, QPointF(tip.x() - dx * s * 0.55, tip.y() - s * 0.5))
                p.drawLine(tip, QPointF(tip.x() - dx * s * 0.55, tip.y() + s * 0.5))
            w2, h2 = s * 1.5, s * 0.8
            if self.kind == "repeat":                       # две стрелки по кругу
                path = QPainterPath()
                path.moveTo(c.x() - w2, c.y() + h2 * 0.1)
                path.lineTo(c.x() - w2, c.y() - h2 * 0.4)
                path.quadTo(c.x() - w2, c.y() - h2, c.x() - w2 + h2 * 0.6, c.y() - h2)
                path.lineTo(c.x() + w2 * 0.75, c.y() - h2)
                path.moveTo(c.x() + w2, c.y() - h2 * 0.1)
                path.lineTo(c.x() + w2, c.y() + h2 * 0.4)
                path.quadTo(c.x() + w2, c.y() + h2, c.x() + w2 - h2 * 0.6, c.y() + h2)
                path.lineTo(c.x() - w2 * 0.75, c.y() + h2)
                p.drawPath(path)
                head(QPointF(c.x() + w2 * 0.85, c.y() - h2), 1)
                head(QPointF(c.x() - w2 * 0.85, c.y() + h2), -1)
                if self.mode == "one":                      # «1» в середине — повтор одного трека
                    f = pfont(max(9, int(s * 1.25)))
                    p.setFont(f)
                    p.drawText(QRectF(c.x() - w2, c.y() - h2, w2 * 2, h2 * 2), Qt.AlignmentFlag.AlignCenter, "1")
            else:                                           # перемешать: две скрещённые стрелки
                for sy in (1, -1):
                    path = QPainterPath()
                    path.moveTo(c.x() - w2, c.y() - sy * h2)
                    path.cubicTo(c.x() - w2 * 0.2, c.y() - sy * h2, c.x() + w2 * 0.1, c.y() + sy * h2,
                                 c.x() + w2 * 0.85, c.y() + sy * h2)
                    p.drawPath(path)
                    head(QPointF(c.x() + w2, c.y() + sy * h2), 1)
            if on:                                          # точка под значком — «включено»
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(c.x(), r.bottom() - 2.5), 2.2, 2.2)
        elif self.kind == "play":
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
    SEEK_MAX = 10000                 # мелкие деления — ползунок идёт без скачков

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._pos_timer = QTimer(self, interval=33)          # ползунок и время — 30 раз в секунду на любой странице
        self._pos_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pos_timer.timeout.connect(self.sync_pos)
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
        self.shuf = IconButton("shuffle", 34)
        self.rep = IconButton("repeat", 34)
        self.prev.clicked.connect(lambda: win.owner.step(-1))
        self.play.clicked.connect(win.owner.toggle_play)
        self.next.clicked.connect(lambda: win.owner.step(+1))
        self.shuf.clicked.connect(lambda: (win.owner.toggle_shuffle(), self.sync()))
        self.rep.clicked.connect(self.cycle_repeat)
        for b in (self.shuf, self.prev, self.play, self.next, self.rep):
            btns.addWidget(b)
        btns.addStretch(1)
        mid.addLayout(btns)
        prog = QHBoxLayout()
        self.cur = QLabel("0:00")
        self.cur.setObjectName("time")
        self.tot = QLabel("0:00")
        self.tot.setObjectName("time")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, self.SEEK_MAX)
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
        self.queue_btn = P3Chip("☰  ДАЛЕЕ")
        self.queue_btn.setToolTip("Очередь: что будет играть дальше")
        self.queue_btn.clicked.connect(lambda: win.toggle_queue())
        lay.addWidget(self.queue_btn)
        vl = QLabel("🔊")
        vl.setObjectName("time")
        lay.addWidget(vl)
        lay.addWidget(self.vol)
        self._art_key = None

    REPEAT_TIPS = {"off": "Повтор выключен — список доиграет и остановится",
                   "all": "Повтор списка — после последнего трека снова первый",
                   "one": "Повтор трека — этот трек по кругу"}

    def cycle_repeat(self):
        """выключен → весь список → один трек → выключен."""
        o = self.win.owner
        cur = getattr(o, "repeat", "all")
        nxt = {"off": "all", "all": "one", "one": "off"}.get(cur if isinstance(cur, str) else "all", "all")
        o.set_repeat(nxt)
        self.win.toast({"all": "🔁 ПОВТОР СПИСКА", "one": "🔂 ПОВТОР ТРЕКА", "off": "ПОВТОР ВЫКЛЮЧЕН"}[nxt])
        self.sync()

    def _seek(self):
        self._dragging = False
        d = self.win.owner.player.duration()
        if d > 0:
            self.win.owner.player.setPosition(int(d * self.seek.value() / self.SEEK_MAX))

    def sync(self):
        o = self.win.owner
        fmw = QFontMetrics(pfont(16))
        self.t.setText(fmw.elidedText(getattr(o, "title", "") or "—", Qt.TextElideMode.ElideRight, 215))
        self.a.setText(QFontMetrics(pfont(13, bold=False)).elidedText(self.win.now_artist(),
                                                                      Qt.TextElideMode.ElideRight, 215))
        self.play.playing = o.is_playing()
        self.play.update()
        rep = getattr(o, "repeat", "all")
        rep = rep if rep in ("off", "all", "one") else "all"
        shuf = "on" if getattr(o, "shuffle", False) is True else "off"
        if rep != self.rep.mode or shuf != self.shuf.mode:
            self.rep.mode, self.shuf.mode = rep, shuf
            self.rep.setToolTip(self.REPEAT_TIPS[rep] + "\nклик — сменить")
            self.shuf.setToolTip("Перемешать: " + ("включено" if shuf == "on" else "выключено"))
            self.rep.update()
            self.shuf.update()
        self.sync_pos()
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

    def showEvent(self, e):
        self._pos_timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._pos_timer.stop()
        super().hideEvent(e)

    def sync_pos(self):
        o = self.win.owner
        pos, dur = o.player.position(), o.player.duration()
        sp = getattr(o, "smooth_pos", None)                 # движок отдаёт позицию рывками — берём сглаженную
        sm = sp() if callable(sp) else None
        if isinstance(sm, (int, float)):
            pos = max(0, min(sm, dur)) if dur > 0 else sm
        self.cur.setText(fmt_ms(pos))
        self.tot.setText(fmt_ms(dur))
        if not self._dragging and not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(int(self.SEEK_MAX * pos / dur) if dur > 0 else 0)
            self.seek.blockSignals(False)

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

# ---------------------------------------------------------------- настройки
class P3Toggle(QWidget):
    """Переключатель: скошенная дорожка, голубая, когда включено."""
    toggled = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(74, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on = False
        self.x = 0.0
        self.enabled_ = True
        self._first = True
        self._anim = QTimer(self, interval=16)
        self._anim.timeout.connect(self._step)

    def set_on(self, on, enabled=True):
        self.on, self.enabled_ = bool(on), enabled
        if self._first:                       # первый показ — сразу в нужном положении, без анимации
            self._first = False
            self.x = 1.0 if self.on else 0.0
        elif not self._anim.isActive():
            self._anim.start()
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def _step(self):
        t = 1.0 if self.on else 0.0
        self.x += (t - self.x) * 0.3
        if abs(t - self.x) < 0.01:
            self.x = t
            self._anim.stop()
        self.update()

    def mousePressEvent(self, e):
        if self.enabled_ and e.button() == Qt.MouseButton.LeftButton:
            sfx("select")
            self.toggled.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.enabled_ else 0.35)
        r = QRectF(self.rect()).adjusted(2, 4, -2, -4)
        g = QLinearGradient(r.topLeft(), r.topRight())
        c0 = QColor(WHITE)
        c1 = QColor(CYAN)
        off = QColor("#16295c")
        mix = lambda a, b, t: QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                                     int(a.blue() + (b.blue() - a.blue()) * t))
        g.setColorAt(0, mix(off, c0, self.x))
        g.setColorAt(1, mix(off, c1, self.x))
        p.setPen(QPen(CYAN_D if self.x < 0.5 else CYAN, 2))
        p.setBrush(g)
        p.drawPolygon(skew_poly(r, 8))
        kw = 26
        kx = r.left() + 6 + (r.width() - kw - 12) * self.x
        knob = QRectF(kx, r.top() + 4, kw, r.height() - 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(NAVY if self.x > 0.5 else WHITE)
        p.drawPolygon(skew_poly(knob, 5))
        p.end()


class P3Chip(QPushButton):
    """Вариант выбора — маленькая скошенная плашка."""

    def __init__(self, text):
        super().__init__(text)
        self.pressed.connect(lambda: sfx("select"))
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        fm = QFontMetrics(pfont(13))
        self.setFixedWidth(fm.horizontalAdvance(text) + 36)
        self._hover = False

    def enterEvent(self, _e):
        self._hover = True
        sfx("hover")
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.35)
        r = QRectF(self.rect()).adjusted(1, 2, -1, -2)
        on = self.isChecked()
        if on:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, WHITE)
            g.setColorAt(1, CYAN)
            p.setBrush(g)
        else:
            p.setBrush(QColor(47, 211, 255, 45) if self._hover else QColor(255, 255, 255, 0))
        p.setPen(QPen(CYAN if (on or self._hover) else CYAN_D, 1.5))
        p.drawPolygon(skew_poly(r, 8))
        p.setFont(pfont(13))
        p.setPen(NAVY if on else WHITE)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class P3Action(P3Chip):
    def __init__(self, text):
        super().__init__(text)
        self.setCheckable(False)


class SettingsPage(QWidget):
    """Все настройки плеера — в медиатеке, крупно и по разделам."""

    def __init__(self, win):
        super().__init__()
        from PySide6.QtWidgets import QScrollArea
        self.win = win
        self.o = win.owner
        self.M = sys.modules.get(type(self.o).__module__)          # константы плеера (скорости, скины…)
        self.syncers = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 6, 0)
        outer.setSpacing(8)
        h = QLabel("НАСТРОЙКИ")
        h.setObjectName("h1")
        outer.addWidget(h)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 18, 30)
        self.lay.setSpacing(2)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.build()

    # ---------- кирпичики
    def section(self, title):
        lb = QLabel(title)
        lb.setObjectName("sec")
        self.lay.addSpacing(16)
        self.lay.addWidget(lb)
        line = QFrame()
        line.setFixedHeight(2)
        line.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {CYAN.name()}, "
                           f"stop:0.6 rgba(47,211,255,40), stop:1 transparent);")
        self.lay.addWidget(line)
        self.lay.addSpacing(4)

    def _row(self, title, hint=""):
        row = QWidget()
        row.setObjectName("srow")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(6, 6, 6, 6)
        hl.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title)
        t.setObjectName("stitle")
        col.addWidget(t)
        if hint:
            hh = QLabel(hint)
            hh.setObjectName("shint")
            hh.setWordWrap(True)
            col.addWidget(hh)
        hl.addLayout(col, 1)
        self.lay.addWidget(row)
        return hl

    def _after(self):
        QTimer.singleShot(60, self.refresh)

    def toggle(self, title, get, act, hint="", enabled=None):
        hl = self._row(title, hint)
        sw = P3Toggle()
        sw.toggled.connect(lambda: (act(), self._after()))
        hl.addWidget(sw)
        self.syncers.append(lambda: sw.set_on(get(), enabled() if enabled else True))

    def choice(self, title, options, get, act, hint=""):
        hl = self._row(title, hint)
        chips = []
        box = QHBoxLayout()
        box.setSpacing(4)
        for key, label in options:
            c = P3Chip(label)
            c.clicked.connect(lambda _=False, k=key: (act(k), self._after()))
            box.addWidget(c)
            chips.append((key, c))
        hl.addLayout(box)

        def sync():
            cur = get()
            for k, c in chips:
                c.setChecked(k == cur)
        self.syncers.append(sync)

    def actions(self, title, buttons, hint="", label_get=None):
        hl = self._row(title, hint)
        for text, fn in buttons:
            b = P3Action(text)
            b.clicked.connect(lambda _=False, fn=fn: (fn(), self._after()))
            hl.addWidget(b)
        if label_get:
            t = hl.itemAt(0).layout().itemAt(0).widget()
            self.syncers.append(lambda t=t: t.setText(label_get()))

    # ---------- все разделы
    def build(self):
        o, M, cfg = self.o, self.M, (lambda k, d=None: self.o.cfg.get(k, d))

        self.section("МЕДИАТЕКА")
        self.toggle("Открывать медиатеку при запуске", lambda: cfg("start_library", True), o.toggle_lib_start,
                    "Иначе при запуске — маленький плеер на рабочем столе")
        self.toggle("Обложки и фото исполнителей из интернета", lambda: cfg("online_art", True),
                    lambda: self.flip("online_art", True, after=self.win.covers.forget_missing),
                    "Для треков без своей картинки — с iTunes и Deezer")
        self.toggle("Искать неизвестных исполнителей в интернете", lambda: cfg("online_artist", True),
                    lambda: self.flip("online_artist", True, after=getattr(o, "find_unknown_artists", None)),
                    "Трек без исполнителя — плеер сам найдёт, кто это. Не тот — ПКМ по треку → «Не тот исполнитель»")
        self.toggle("Анимация переходов", lambda: cfg("ui_anim", True), lambda: self.flip("ui_anim", True),
                    "Страницы выезжают скошенной полосой, как меню P3R")
        self.toggle("Звуки меню", lambda: cfg("ui_sounds", True), lambda: self.flip("ui_sounds", True),
                    "Курсор, выбор и «назад» — громкость как у звуков кнопок")
        self.actions("Дубликаты", [("🔍 НАЙТИ ДУБЛИКАТЫ", lambda: self.win.go("dups"))],
                     "Одна и та же песня в разных папках или форматах — оставить лучшую копию")
        self.actions("Папки с музыкой", [("+ ДОБАВИТЬ ПАПКУ", self.add_folder),
                                         ("⟳ ПЕРЕСКАНИРОВАТЬ", lambda: o.scan_library(force=True))],
                     "Отсюда медиатека берёт альбомы и исполнителей")
        self.folders_box = QVBoxLayout()
        self.folders_box.setSpacing(2)
        self.lay.addLayout(self.folders_box)

        self.section("ВОСПРОИЗВЕДЕНИЕ")
        self.toggle("Перемешивание", lambda: o.shuffle, o.toggle_shuffle)
        self.choice("Повтор", [("all", "ВЕСЬ СПИСОК"), ("one", "ОДИН ТРЕК"), ("off", "ВЫКЛ")],
                    lambda: o.repeat, o.set_repeat)
        self.choice("Переход между треками", [(ms, n.upper()) for n, ms in M.CROSSFADES.items()],
                    lambda: int(cfg("crossfade", 4000)), o.set_crossfade,
                    "Конец трека плавно перетекает в начало следующего")
        self.toggle("Затухание при паузе", lambda: cfg("fade_pause", True), o.toggle_fade_pause)
        self.toggle("Помнить место в длинных файлах (20+ мин)", lambda: cfg("resume_long", True), o.toggle_resume,
                    "Аудиокниги, подкасты, миксы — продолжатся с того же места")

        self.section("ЗВУК")
        self.actions("Эквалайзер", [("🎚 ОТКРЫТЬ", o.open_eq)], "Ctrl+Alt+E")
        self.choice("Выравнивание громкости", [(k, v[0].split(" (")[0].upper()) for k, v in o.NORM_MODES.items()],
                    lambda: cfg("normalize", "spotify"), o.set_normalize,
                    "Тихие и громкие треки звучат одинаково громко")

        self.section("ТЕКСТ ПЕСЕН")
        self.toggle("Показывать текст песни", lambda: cfg("lyrics", True), o.toggle_lyrics)
        self.toggle("Искать тексты в интернете", lambda: cfg("lyrics_online", True), o.toggle_lyrics_online,
                    "Если рядом с треком нет .lrc — найти на lrclib.net")
        self.actions("Свой текст под песню", [("✍ СОЗДАТЬ", o.open_lrc_maker)],
                     "Для того, что играет сейчас: пробелом в такт")

        self.section("МАЛЕНЬКИЙ ПЛЕЕР")
        skins = [(sid, (meta.get("name", sid)).upper()) for sid, (_d, meta) in o.find_skins().items()]
        self.choice("Скин", skins, lambda: o.skin_id, o.set_skin)
        self.choice("Подсветка экрана", [(n, n) for n in M.THEMES], lambda: cfg("theme", "ЗЕЛЁНАЯ"), o.set_theme)

        def cur_size():
            base = o.auto_scale_here()
            return next((v for v in M.SIZES.values() if abs(base * v - o.scale_f) < 0.011), None)
        self.choice("Размер", [(v, n.upper()) for n, v in M.SIZES.items()], cur_size,
                    lambda v: o.set_scale(o.auto_scale_here() * v), "Ещё можно Alt + колёсико над плеером")
        self.choice("Где показывать", [(True, "НА РАБОЧЕМ СТОЛЕ"), (False, "ПОВЕРХ ВСЕХ ОКОН")],
                    lambda: o.on_desktop, o.set_mode)
        self.toggle("Не прятаться по Win+D", lambda: cfg("keep_desktop", True), o.toggle_keep_desktop,
                    enabled=lambda: o.on_desktop and o.keeper.ok)
        self.toggle("Показывать в панели задач", lambda: cfg("taskbar", False), o.toggle_taskbar)
        self.toggle("Мини-режим", lambda: bool(cfg("mini")), lambda: o.set_mini(not cfg("mini")), "Ctrl+Alt+M")
        self.toggle("Заставка HELLO при запуске", lambda: cfg("boot_anim", True),
                    lambda: o.toggle_cfg("boot_anim", "ЗАСТАВКА"))
        self.toggle("3D-корпус: наклон при перемещении", lambda: cfg("three_d", True), o.toggle_3d)
        self.toggle("3D: наклоняться за курсором", lambda: cfg("tilt_hover", False),
                    lambda: o.toggle_cfg("tilt_hover", "НАКЛОН ЗА КУРСОРОМ"), enabled=lambda: cfg("three_d", True))
        self.toggle("Эквалайзер на экранчике", lambda: cfg("eq", True), lambda: o.toggle_cfg("eq", "ЭКВАЛАЙЗЕР"))
        self.toggle("…по настоящему звуку", lambda: cfg("eq_real", True), lambda: o.toggle_cfg("eq_real", "СПЕКТР"),
                    enabled=lambda: o.spectrum.available)
        self.toggle("Звук кнопок", lambda: cfg("click_sound", True), lambda: o.toggle_cfg("click_sound", "ЗВУК КНОПОК"))
        self.choice("Звуки", [("beep", "ПИСК"), ("classic", "ЩЕЛЧКИ")], lambda: cfg("sound_theme", "beep"),
                    o.set_sound_theme)
        self.choice("Громкость звуков", [(v, n.upper()) for n, v in M.SFX_LEVELS.items()],
                    lambda: next((v for v in M.SFX_LEVELS.values() if abs(v - float(cfg("sfx_volume", 0.5))) < 0.01), None),
                    o.set_sfx_volume)
        self.toggle("Всплывающая обложка", lambda: cfg("cover_popup", True), o.toggle_cover_popup)
        self.choice("Окошко «Сейчас играет»", [("hidden", "КОГДА ПЛЕЕР СПРЯТАН"), ("always", "ВСЕГДА"), ("off", "ВЫКЛ")],
                    lambda: cfg("toast", "hidden"), o.set_toast_mode)

        self.section("DISCORD И WINDOWS")
        self.toggle("Показывать в Discord (Music → Discord)", lambda: cfg("discord_link", True), o.toggle_discord_link)
        self.toggle("Горячие клавиши", lambda: cfg("hotkeys", True), o.toggle_hotkeys,
                    "Ctrl+Alt+Пробел — пауза  ·  Ctrl+Alt+→ / ← — треки  ·  Ctrl+Alt+O — медиатека  ·  "
                    "Ctrl+Alt+B — поиск  ·  Ctrl+Alt+T — теги")
        self.toggle("Запускать вместе с Windows", lambda: M.Autostart.enabled(), o.toggle_autostart,
                    "При включении компьютера — тихо, в трей, с маленьким плеером")
        self.actions("Открывать музыку в MP3 Плеере", [("➕ ДОБАВИТЬ В WINDOWS", o.assoc_register),
                                                       ("⭐ ПО УМОЛЧАНИЮ", o.assoc_make_default)],
                     "«Открыть с помощью» и двойной клик по mp3 / flac")

        self.section("ОБНОВЛЕНИЯ")
        self.toggle("Обновляться автоматически", lambda: cfg("auto_update", True), o.toggle_auto_update)
        self.actions("Сборка", [("⬆ ПРОВЕРИТЬ", lambda: o.check_update(manual=True)),
                                ("ЧТО НОВОГО", lambda: o.show_whats_new(max(0, o.build - 5), manual=True))],
                     label_get=lambda: f"Сборка {o.build}" if o.build else "Запущено из исходников")

        self.section("ИТОГИ И ПРОЧЕЕ")
        self.actions("Статистика прослушиваний", [("📊 ИТОГИ НЕДЕЛИ", o.open_weekly),
                                                  ("СБРОСИТЬ…", o.reset_stats)])
        self.actions("Если что-то не работает", [("🛠 ДИАГНОСТИКА", o.show_diagnostics)])
        self.lay.addStretch(1)

    def flip(self, key, default, after=None):
        self.o.cfg[key] = not self.o.cfg.get(key, default)
        self.o.save_later()
        if after:
            after()
        self.win.rebuild(force=True) if key == "online_art" else None

    # ---------- папки
    def add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Папка с музыкой", os.path.expanduser("~\\Music"))
        if not d:
            return
        d = os.path.normpath(d)
        folders = self.o.cfg.setdefault("folders", [])
        if not any(os.path.normcase(f) == os.path.normcase(d) for f in folders):
            folders.append(d)
            self.o.save_later()
        self.o.scan_library(force=True)

    def remove_folder(self, d):
        folders = self.o.cfg.get("folders", [])
        self.o.cfg["folders"] = [f for f in folders if os.path.normcase(f) != os.path.normcase(d)]
        self.o.save_later()
        self.o.scan_library(force=True)
        self._after()

    def _sync_folders(self):
        while self.folders_box.count():
            it = self.folders_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for d in self.o.cfg.get("folders", []):
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(26, 0, 6, 0)
            lb = QLabel(("📁  " if os.path.isdir(d) else "⚠  ") + d)
            lb.setObjectName("shint")
            hl.addWidget(lb, 1)
            x = P3Action("✖ УБРАТЬ")
            x.clicked.connect(lambda _=False, d=d: self.remove_folder(d))
            hl.addWidget(x)
            self.folders_box.addWidget(row)

    def refresh(self):
        for f in self.syncers:
            try:
                f()
            except Exception:
                pass
        self._sync_folders()

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)



# ---------------------------------------------------------------- переход между разделами (как меню P3R)
class Transition(QWidget):
    """Новая страница «въезжает» скошенной полосой, старая уезжает и гаснет; по краю — голубая плашка."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.old = self.new = None
        self.t = 1.0
        self.back = False
        self._t0 = 0.0
        self.dur = 0.32
        self.timer = QTimer(self, interval=12)
        self.timer.timeout.connect(self._step)
        self.hide()

    def start(self, old_pm, new_pm, back=False):
        self.old, self.new, self.back = old_pm, new_pm, back
        self.setGeometry(self.parentWidget().rect())
        self._t0 = time.monotonic()
        self.t = 0.0
        self.show()
        self.raise_()
        self.timer.start()

    def _step(self):
        self.t = min(1.0, (time.monotonic() - self._t0) / self.dur)
        if self.t >= 1.0:
            self.timer.stop()
            self.hide()
            self.old = self.new = None
            return
        self.update()

    def paintEvent(self, _e):
        if self.old is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        e = 1 - (1 - self.t) ** 3                                   # быстрое начало, мягкий конец
        sk = h * 0.35
        d = -1 if self.back else 1
        # старая страница уезжает в сторону и гаснет
        p.setOpacity(1 - e * 0.85)
        p.drawPixmap(int(-d * w * 0.18 * e), 0, self.old)
        p.setOpacity(1.0)
        # новая — открывается скошенной полосой
        edge = (w + sk) * (1 - e)                                    # левый край новой страницы (вперёд)
        if not self.back:
            poly = QPolygonF([QPointF(edge, 0), QPointF(w + sk, 0), QPointF(w + sk, h), QPointF(edge - sk, h)])
            band = QPolygonF([QPointF(edge - 22, 0), QPointF(edge + 4, 0), QPointF(edge - sk + 4, h),
                              QPointF(edge - sk - 22, h)])
        else:
            edge = w - edge + sk
            poly = QPolygonF([QPointF(-sk, 0), QPointF(edge, 0), QPointF(edge - sk, h), QPointF(-sk, h)])
            band = QPolygonF([QPointF(edge - 4, 0), QPointF(edge + 22, 0), QPointF(edge - sk + 22, h),
                              QPointF(edge - sk - 4, h)])
        path = QPainterPath()
        path.addPolygon(poly)
        p.save()
        p.setClipPath(path)
        p.drawPixmap(int(d * w * 0.06 * (1 - e)), 0, self.new)
        p.restore()
        g = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        g.setColorAt(0, WHITE)
        g.setColorAt(1, CYAN)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.setOpacity(1 - e * 0.6)
        p.drawPolygon(band)
        p.end()


# ---------------------------------------------------------------- своя рамка окна в стиле P3R
class TitleButton(QPushButton):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind                         # min / max / close
        self.setFixedSize(54, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False

    def enterEvent(self, _e):
        self._hover = True
        sfx("hover")
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 5, -2, -5)
        if self._hover:
            g = QLinearGradient(r.topLeft(), r.topRight())
            if self.kind == "close":
                g.setColorAt(0, QColor("#ff7aa0"))
                g.setColorAt(1, QColor("#e0245e"))
            else:
                g.setColorAt(0, WHITE)
                g.setColorAt(1, CYAN)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawPolygon(skew_poly(r, 8))
        ink = (WHITE if self.kind == "close" else NAVY) if self._hover else ICE
        pen = QPen(ink, 2)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        c = r.center()
        if self.kind == "min":
            p.drawLine(QPointF(c.x() - 6, c.y() + 1), QPointF(c.x() + 6, c.y() + 1))
        elif self.kind == "max":
            if self.window().isMaximized():
                p.drawRect(QRectF(c.x() - 4, c.y() - 2, 9, 8))
                p.drawPolyline([QPointF(c.x() - 1, c.y() - 5), QPointF(c.x() + 8, c.y() - 5), QPointF(c.x() + 8, c.y() + 3)])
            else:
                p.drawRect(QRectF(c.x() - 6, c.y() - 5, 12, 10))
        else:
            p.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
            p.drawLine(QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() - 5, c.y() + 5))
        p.end()


class TitleBar(QWidget):
    """Заголовок окна: тянуть — двигать (с прилипанием к краям Windows), двойной клик — на весь экран."""

    def __init__(self, win, title):
        super().__init__()
        self.win = win
        self.title = title
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        self.bmin, self.bmax, self.bclose = TitleButton("min"), TitleButton("max"), TitleButton("close")
        self.bmin.clicked.connect(win.showMinimized)
        self.bmax.clicked.connect(self.toggle_max)
        self.bclose.clicked.connect(win.close)
        for b in (self.bmin, self.bmax, self.bclose):
            lay.addWidget(b)

    def toggle_max(self):
        sfx("select")
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            h = self.win.windowHandle()
            if h is not None:
                h.startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_max()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor(3, 10, 31))
        g.setColorAt(0.6, QColor(6, 19, 49))
        g.setColorAt(1, QColor(11, 34, 87))
        p.fillRect(r, g)
        # голубая скошенная плашка с названием — как заголовок меню P3R
        f = pfont(14)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(self.title)
        tag = QRectF(10, 6, tw + 64, r.height() - 12)
        tg = QLinearGradient(tag.topLeft(), tag.topRight())
        tg.setColorAt(0, WHITE)
        tg.setColorAt(1, CYAN)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tg)
        p.drawPolygon(skew_poly(tag, 10))
        p.setBrush(CYAN)
        p.drawPolygon(skew_poly(QRectF(tag.right() + 4, tag.top(), 10, tag.height()), 10))
        p.setBrush(QColor(47, 211, 255, 120))
        p.drawPolygon(skew_poly(QRectF(tag.right() + 18, tag.top(), 6, tag.height()), 10))
        # нота-значок
        p.setFont(pfont(15, italic=False))
        p.setPen(NAVY)
        p.drawText(QRectF(tag.left() + 14, tag.top(), 20, tag.height()), Qt.AlignmentFlag.AlignVCenter, "♪")
        p.setFont(f)
        p.drawText(QRectF(tag.left() + 36, tag.top(), tw + 10, tag.height()), Qt.AlignmentFlag.AlignVCenter, self.title)
        # нижняя линия: яркая слева, гаснет вправо
        lg = QLinearGradient(r.bottomLeft(), r.bottomRight())
        lg.setColorAt(0, CYAN)
        lg.setColorAt(0.5, QColor(47, 211, 255, 90))
        lg.setColorAt(1, QColor(47, 211, 255, 20))
        p.setPen(QPen(QBrush(lg), 2))
        p.drawLine(QPointF(0, r.bottom()), QPointF(r.right(), r.bottom()))
        p.end()

class LibraryWindow(QWidget):
    """Большое окно «Медиатека». owner — Mp3Widget (плеер)."""
    EDGE = 6
    closed = Signal()

    def __init__(self, owner, cache_dir):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMouseTracking(True)
        self.owner = owner
        self.lib = owner.library
        self.setWindowTitle("Медиатека — MP3 Плеер")
        self.resize(1280, 800)
        self.setMinimumSize(980, 620)
        self.covers = CoverCache(cache_dir)
        self.covers.ready.connect(self._cover_ready)
        self.covers.online = self._online_cover
        self.covers.artist_fn = self._online_artist
        self._online_dir = os.path.join(cache_dir, "online")
        self._online_lock = threading.Lock()
        cfg = lambda k, d=None: owner.cfg.get(k, d) if hasattr(owner, "cfg") else d
        _SFX["obj"] = UISounds(os.path.join(cache_dir, "..", "sounds"), lambda: cfg("ui_sounds", True),
                               lambda: cfg("sfx_volume", 0.5))
        self.items = {}
        self.albums, self.artists = [], []
        self.history = []
        self._built_n = -1
        self._auto_v = self.lib.auto_version
        self._detail_obj = None
        import library_more as more
        self.more = more
        self.playlists = more.Playlists(getattr(owner, "data_dir", os.path.dirname(cache_dir)))
        self._pl_version = -1

        frame = QVBoxLayout(self)
        frame.setContentsMargins(self.EDGE, self.EDGE, self.EDGE, self.EDGE)   # край — тянуть, чтобы менять размер
        frame.setSpacing(0)
        self.frame = frame
        self.titlebar = TitleBar(self, "МЕДИАТЕКА — MP3 ПЛЕЕР")
        frame.addWidget(self.titlebar)
        root = QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        frame.addLayout(root, 1)
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
        for key, text in (("home", "ГЛАВНАЯ"), ("albums", "АЛЬБОМЫ"), ("artists", "ИСПОЛНИТЕЛИ"),
                          ("tracks", "ВСЕ ТРЕКИ"), ("now", "СЕЙЧАС ИГРАЕТ"), ("stats", "ИТОГИ"),
                          ("settings", "НАСТРОЙКИ")):
            b = NavButton(text)
            b.setFixedHeight(42)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            sl.addWidget(b)
            self.nav[key] = b
        sl.addSpacing(12)
        plh = QHBoxLayout()
        pll = QLabel("ПЛЕЙЛИСТЫ")
        pll.setObjectName("sec")
        plh.addWidget(pll)
        plh.addStretch(1)
        self.new_pl = self.more.SidePlaylist("+")
        self.new_pl.setFixedWidth(46)
        self.new_pl.setCheckable(False)
        self.new_pl.setToolTip("Новый плейлист (можно перетащить сюда треки)")
        self.new_pl.clicked.connect(lambda: self.new_playlist())
        self.new_pl.dropped.connect(lambda paths: self.new_playlist(paths))
        plh.addWidget(self.new_pl)
        sl.addLayout(plh)
        self.pl_box = QVBoxLayout()
        self.pl_box.setSpacing(0)
        self.pl_box.setContentsMargins(0, 0, 0, 0)
        plw = QWidget()
        plw.setLayout(self.pl_box)
        self.pl_scroll = __import__("PySide6.QtWidgets", fromlist=["QScrollArea"]).QScrollArea()
        self.pl_scroll.setWidgetResizable(True)
        self.pl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pl_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; }")
        self.pl_scroll.setWidget(plw)
        sl.addWidget(self.pl_scroll, 1)
        self.pl_btns = {}
        mini = NavButton("▣  МАЛЕНЬКИЙ ПЛЕЕР", small=True)
        mini.setFixedWidth(215)
        mini.setToolTip("Спрятать медиатеку — останется маленький плеер на рабочем столе и значок в трее")
        mini.clicked.connect(self.close)
        sl.addWidget(mini)
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
        self.tracks_view.setItemDelegate(TrackDelegate(self, show_album=True, stats=True))
        self.tracks_paths = []
        self.tracks_box = QWidget()
        tb = QVBoxLayout(self.tracks_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(4)
        fl = QHBoxLayout()
        fl.setSpacing(4)
        self.filter_chips = {}
        for key, name in (("all", "ВСЕ"), ("fav", "♥ ЛЮБИМЫЕ"), ("new", "НЕ СЛУШАЛ"), ("top", "ЧАСТО СЛУШАЮ"),
                          ("month", "ДОБАВЛЕНО ЗА МЕСЯЦ")):
            c = P3Chip(name)
            c.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            fl.addWidget(c)
            self.filter_chips[key] = c
        fl.addStretch(1)
        tb.addLayout(fl)
        self.sort_head = SortHeader(self.tracks_view)
        self.sort_head.changed.connect(self.set_sort)
        tb.addWidget(self.sort_head)
        tb.addWidget(self.tracks_view, 1)
        self.tracks_count = QLabel("")
        self.tracks_count.setObjectName("count")
        tb.addWidget(self.tracks_count)
        self.tracks_view.doubleClicked.connect(
            lambda ix: self.play_paths(self.tracks_paths, self.tracks_paths[ix.row()]))
        self.tracks_view.customContextMenuRequested.connect(
            lambda pt: self.track_menu(self.tracks_view, pt, self.tracks_paths))
        self.detail = DetailPage(self)
        self.now = NowPlaying(self)
        self.settings = SettingsPage(self)
        self.home = self.more.HomePage(self)
        import library_stats
        self.stats_page = library_stats.StatsPage(self)
        self.dups = library_stats.DuplicatesPage(self)
        for key, w, title in (("home", self.home, None), ("albums", self.albums_view, "АЛЬБОМЫ"), ("artists", self.artists_view, "ИСПОЛНИТЕЛИ"),
                              ("tracks", self.tracks_box, "ВСЕ ТРЕКИ"), ("detail", self.detail, None),
                              ("now", self.now, None), ("stats", self.stats_page, None), ("dups", self.dups, None),
                              ("settings", self.settings, None)):
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
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.stack, 1)
        self.queue = self.more.QueuePanel(self)
        self.queue.hide()
        mid.addWidget(self.queue)
        right.addLayout(mid, 1)
        self.trans = Transition(self.stack)
        self.bar = PlayerBar(self)
        right.addWidget(self.bar)
        root.addLayout(right, 1)

        self.timer = QTimer(self, interval=80)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)     # обычный таймер Windows дёргается (шаг ~15 мс)
        self.timer.timeout.connect(self.tick)
        self._last_path = None
        self._toast = QLabel(self)
        self._toast.setObjectName("toast")
        self._toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=1800)
        self._toast_timer.timeout.connect(self._toast.hide)
        self.restyle()
        self.sync_playlists()
        self.go("home")
        # клавиатура: стрелки по меню, Enter — выбрать, Ctrl+1…6 — разделы, Alt+← — назад
        for b in self.nav.values():
            b.nav_win = self
        from PySide6.QtGui import QShortcut, QKeySequence
        for i, key in enumerate(("home", "albums", "artists", "tracks", "now", "stats", "settings"), 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: (sfx("select"), self.go(k)))
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.go_back)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.toggle_queue())
        self.setAcceptDrops(True)

    # ------------------------------------------------------------ стиль
    def restyle(self):
        c, w, d = CYAN.name(), WHITE.name(), DIM.name()
        self.setStyleSheet(f"""
            QWidget {{ color:{w}; font-family:'{FONT}'; }}
            #side {{ background:{DEEP.name()}; border-right:2px solid {CYAN_D.name()}; }}
            #logo {{ font-size:28px; font-weight:900; font-style:italic; color:{w}; letter-spacing:1px; }}
            #count {{ color:{d}; font-size:12px; font-style:italic; }}
            #h1 {{ font-size:34px; font-weight:900; font-style:italic; color:{w}; }}
            #sec {{ font-size:20px; font-weight:900; font-style:italic; color:{c}; letter-spacing:1px; }}
            #h2 {{ font-size:26px; font-weight:900; font-style:italic; color:{w}; }}
            #dupbest {{ font-size:13px; font-weight:900; font-style:italic; color:{c}; }}
            #dupext {{ font-size:13px; font-weight:bold; font-style:italic; color:{d}; }}
            #qnow {{ font-size:13px; font-weight:bold; font-style:italic; color:{c}; padding:4px 0; }}
            #hero {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(47,211,255,40),
                     stop:0.5 rgba(11,34,87,160), stop:1 rgba(3,10,31,200)); border:2px solid {c}; }}
            #toast {{ background:{c}; color:{NAVY.name()}; font-size:15px; font-weight:900; font-style:italic;
                      padding:8px 22px; }}
            QInputDialog, QMessageBox {{ background:{NAVY.name()}; }}
            QInputDialog QPushButton, QMessageBox QPushButton {{ background:{CYAN_D.name()}; color:{w};
                      border:none; padding:6px 18px; font-weight:bold; font-style:italic; }}
            #srow {{ background:transparent; }}
            #srow:hover {{ background:rgba(47,211,255,18); }}
            #stitle {{ font-size:15px; font-weight:bold; font-style:italic; color:{w}; }}
            #shint {{ font-size:12px; font-style:italic; color:{d}; }}
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

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(e.rect(), bg_pixmap(self.width(), self.height()), e.rect())      # только то, что просили
        if not self.isMaximized():
            # обводка: голубая линия с бело-голубым «бликом» в углах, как рамки в меню P3R
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setPen(QPen(QColor(3, 10, 31), 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(2, 2, -2, -2))
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, CYAN)
            g.setColorAt(0.5, CYAN_D)
            g.setColorAt(1, CYAN)
            p.setPen(QPen(QBrush(g), 2))
            p.drawRect(r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE)
            k = 26
            p.drawPolygon(QPolygonF([r.topLeft(), QPointF(r.left() + k, r.top()), QPointF(r.left() + k - 4, r.top() + 4),
                                     QPointF(r.left() + 4, r.top() + 4), QPointF(r.left() + 4, r.top() + k - 4),
                                     QPointF(r.left(), r.top() + k)]))
            p.drawPolygon(QPolygonF([r.bottomRight(), QPointF(r.right() - k, r.bottom()),
                                     QPointF(r.right() - k + 4, r.bottom() - 4), QPointF(r.right() - 4, r.bottom() - 4),
                                     QPointF(r.right() - 4, r.bottom() - k + 4), QPointF(r.right(), r.bottom() - k)]))
        p.end()

    # ------------------------------------------------------------ рамка: размер, развернуть
    def _edges(self, pos):
        if self.isMaximized():
            return Qt.Edge(0)
        e, w, h = self.EDGE + 2, self.width(), self.height()
        x, y = pos.x(), pos.y()
        edges = Qt.Edge(0)
        if x <= e:
            edges |= Qt.Edge.LeftEdge
        if x >= w - e:
            edges |= Qt.Edge.RightEdge
        if y <= e:
            edges |= Qt.Edge.TopEdge
        if y >= h - e:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, e):
        ed = self._edges(e.position())
        L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        if ed in (L | T, R | B):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif ed in (R | T, L | B):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif ed in (L, R):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif ed in (T, B):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange:
            m = 0 if self.isMaximized() else self.EDGE
            self.frame.setContentsMargins(m, m, m, m)
            self.titlebar.bmax.update()
            self.update()
        super().changeEvent(e)

    def showEvent_native(self):
        """Кнопка в панели задач должна сворачивать окно и без системной рамки."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_STYLE, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = -16, 0x00020000, 0x00010000, 0x00080000
            u = ctypes.windll.user32
            st = u.GetWindowLongW(hwnd, GWL_STYLE)
            u.SetWindowLongW(hwnd, GWL_STYLE, st | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
        except Exception:
            pass

    # ------------------------------------------------------------ данные
    def rebuild(self, force=False):
        with self.lib.lock:
            items = dict(self.lib.items)
        if not force and len(items) == self._built_n and items.keys() == self.items.keys():
            return
        self.items = items
        self._built_n = len(items)
        self._auto_v = self.lib.auto_version
        self.albums = libmod.build_albums(items)
        self.artists = libmod.build_artists(items, self.albums)
        self.albums_view.model_.set_rows(self.albums)
        self.artists_view.model_.set_rows([{**a, "title": a["name"]} for a in self.artists])
        self._fill_tracks(self.search.text())
        total = sum((it.get("d") or 0) for it in items.values())
        self.count.setText(f"{len(items)} трек. · {len(self.albums)} альб. · {len(self.artists)} исп.\n"
                           f"{fmt_total(total)} музыки")

    def _stat_tracks(self):
        st = getattr(self.owner, "stats", None)
        return (st.data.get("tracks") or {}) if st is not None else {}

    def _rows_for(self, paths):
        rows = []
        stats = self._stat_tracks()
        for i, pth in enumerate(paths):
            it = self.items.get(pth, {})
            t, a, alb = libmod.track_view(pth, it)
            m = it.get("m") or 0
            rows.append({"path": pth, "title": t, "artist": a, "album": alb, "d": it.get("d"), "num": i + 1,
                         "plays": int((stats.get(pth) or {}).get("plays") or 0), "added": m,
                         "added_s": time.strftime("%d.%m.%y", time.localtime(m)) if m else ""})
        return rows

    def set_sort(self, key, desc):
        self.owner.cfg["tracks_sort"] = [key, desc]
        self._fill_tracks(self.search.text())

    def set_filter(self, key):
        self.owner.cfg["tracks_filter"] = key
        self._fill_tracks(self.search.text())

    def _fill_tracks(self, q):
        cfg = getattr(self.owner, "cfg", {})
        key, desc = (cfg.get("tracks_sort") or ["num", False])[:2]
        flt = cfg.get("tracks_filter", "all")
        for k, c in self.filter_chips.items():
            c.setChecked(k == flt)
        self.sort_head.set_state(key, desc)
        if q.strip():
            paths = self.lib.search(q, limit=2000)
            self.tracks_title.setText(f"ПОИСК: «{q.strip()}»")
        else:
            paths = sorted(self.items, key=lambda p: tuple(libmod.norm(x) for x in
                                                            libmod.track_view(p, self.items[p])[1::-1]))
            self.tracks_title.setText("ВСЕ ТРЕКИ")
        rows = self._rows_for(paths)
        month_ago = time.time() - 30 * 86400
        if flt == "fav":
            rows = [r for r in rows if self.owner.is_fav(r["path"])]
        elif flt == "new":
            rows = [r for r in rows if not r["plays"]]
        elif flt == "top":
            rows = [r for r in rows if r["plays"] >= 5]
        elif flt == "month":
            rows = [r for r in rows if r["added"] >= month_ago]
        if key != "num":
            sk = {"title": lambda r: libmod.norm(r["title"]), "artist": lambda r: (libmod.norm(r["artist"]), libmod.norm(r["album"])),
                  "album": lambda r: (libmod.norm(r["album"]), libmod.norm(r["title"])), "plays": lambda r: r["plays"],
                  "added": lambda r: r["added"], "dur": lambda r: r.get("d") or 0}[key]
            rows.sort(key=sk, reverse=bool(desc))
        for i, r in enumerate(rows):
            r["num"] = i + 1
        self.tracks_paths = [r["path"] for r in rows]
        self.tracks_view.model_.set_rows(rows)
        total = sum((r.get("d") or 0) for r in rows)
        self.tracks_count.setText(f"{len(rows)} трек. · {fmt_total(total)}")

    def _search_changed(self, q):
        self._fill_tracks(q)
        if q.strip():
            self.go("tracks", remember=False)

    def _cover_ready(self, path):
        for v in (self.albums_view, self.artists_view, self.tracks_view, self.detail.tracks):
            v.viewport().update()
        self.detail.header.update()
        for _lb, strip, _k in self.home.sections.values():
            strip.viewport().update()
        if path == getattr(self.owner, "loaded_path", None):
            self.bar._art_key = None
            self.now.update()

    # ------------------------------------------------------------ навигация
    def go(self, key, remember=True, back=False):
        cur = self.stack.currentWidget()
        if remember and cur is not None and cur is not self.pages.get(key):
            self.history.append(self._state())
            self.history = self.history[-30:]
        new = self.pages[key]
        anim = (self.isVisible() and cur is not None and (cur is not new or key == "detail")
                and getattr(self.owner, "cfg", {}).get("ui_anim", True))
        old_pm = self._snap(cur) if anim else None
        self.stack.setCurrentWidget(new)
        if anim:
            new.resize(self.stack.size())
            self.trans.start(old_pm, self._snap(new), back=back)
        for k, b in self.nav.items():
            b.setChecked(k == key)
        self._pace()
        if hasattr(self, "pl_btns"):
            self._mark_playlist()

    # ------------------------------------------------------------ клавиатура
    def _nav_list(self):
        return list(self.nav.values()) + list(self.pl_btns.values())

    def focus_sidebar(self):
        sfx("back")
        lst = self._nav_list()
        target = next((b for b in lst if b.isChecked()), lst[0] if lst else None)
        if target:
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def focus_nav_step(self, btn, d):
        lst = self._nav_list()
        if btn in lst:
            i = (lst.index(btn) + d) % len(lst)
            lst[i].setFocus(Qt.FocusReason.OtherFocusReason)
            lst[i].nav_win = self

    def focus_content(self):
        w = self.stack.currentWidget()
        view = {self.pages["albums"]: self.albums_view, self.pages["artists"]: self.artists_view,
                self.pages["tracks"]: self.tracks_view, self.pages["detail"]: self.detail.tracks}.get(w)
        if w is self.home:
            view = next((st for _l, st, _k in self.home.sections.values() if st.isVisible()), None)
        if view is None:
            w.setFocus()
            return
        view.setFocus(Qt.FocusReason.OtherFocusReason)
        if not view.currentIndex().isValid() and view.model().rowCount() > 0:
            view.setCurrentIndex(view.model().index(0, 0))

    # ------------------------------------------------------------ перетаскивание из Проводника
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        dirs = [os.path.normpath(u) for u in urls if os.path.isdir(u)]
        if dirs:                                   # папку — в медиатеку
            folders = self.owner.cfg.setdefault("folders", [])
            added = 0
            for d in dirs:
                if not any(os.path.normcase(f) == os.path.normcase(d) for f in folders):
                    folders.append(d)
                    added += 1
            self.owner.save_later()
            self.owner.scan_library(force=True)
            self.toast(f"📁 ПАПКА В МЕДИАТЕКЕ: {os.path.basename(dirs[0])}" + (f" И ЕЩЁ {len(dirs) - 1}" if len(dirs) > 1 else ""))
        files = [p for p in self.more.paths_from_mime(e.mimeData()) if not any(
            os.path.normcase(p).startswith(os.path.normcase(d) + os.sep) for d in dirs)]
        if files:                                  # файлы — играть сейчас
            for p in files:
                try:
                    self.lib.update(p)             # сразу видны в поиске и «Всех треках»
                except Exception:
                    pass
            self.play_paths(files, files[0])
            self.toast(f"▶ {len(files)} ТРЕК." if len(files) > 1 else "▶ " + os.path.basename(files[0]))
        e.acceptProposedAction()

    # ------------------------------------------------------------ теги сразу у нескольких треков
    def batch_tags(self, paths):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QFileDialog
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Теги: {len(paths)} трек.")
        dlg.setStyleSheet(self.styleSheet() + f"QDialog {{ background:{NAVY.name()}; }}")
        lay = QVBoxLayout(dlg)
        info = QLabel(f"Меняется только то, что заполнено. Названия треков останутся свои.\n{len(paths)} трек. выбрано")
        info.setObjectName("shint")
        lay.addWidget(info)
        form = QFormLayout()
        first = libmod.read_tags(paths[0])
        same = lambda k: first.get(k, "") if all(libmod.read_tags(p).get(k, "") == first.get(k, "") for p in paths[1:6]) else ""
        artist = QLineEdit(same("artist"))
        album = QLineEdit(same("album"))
        artist.setPlaceholderText("не менять")
        album.setPlaceholderText("не менять")
        form.addRow("Исполнитель", artist)
        form.addRow("Альбом", album)
        lay.addLayout(form)
        cover = {"data": None}
        cbtn = P3Action("🖼 ОБЛОЖКА ДЛЯ ВСЕХ…")
        cbtn.setFixedWidth(240)

        def pick():
            fn, _ = QFileDialog.getOpenFileName(dlg, "Обложка", os.path.dirname(paths[0]),
                                                "Картинки (*.jpg *.jpeg *.png *.webp *.bmp)")
            if fn:
                img = QImage(fn)
                if not img.isNull():
                    if max(img.width(), img.height()) > 800:
                        img = img.scaled(800, 800, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                    buf = QBuffer()
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    img.convertToFormat(QImage.Format.Format_RGB32).save(buf, "JPG", 90)
                    cover["data"] = bytes(buf.data())
                    cbtn.setText("🖼 ОБЛОЖКА ВЫБРАНА ✓")
        cbtn.clicked.connect(pick)
        lay.addWidget(cbtn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        na, nal = artist.text().strip(), album.text().strip()
        if not (na or nal or cover["data"]):
            return
        done = 0
        for p in paths:
            if not libmod.can_write(p):
                continue
            t = libmod.read_tags(p)
            d = {"title": t.get("title", ""), "artist": na or t.get("artist", ""), "album": nal or t.get("album", ""),
                 "cover": cover["data"], "remove_cover": False}
            if hasattr(self.owner, "save_tags"):
                self.owner.save_tags(p, d)
            done += 1
        self.toast(f"✎ ТЕГИ: {done} ТРЕК.")
        QTimer.singleShot(1500, lambda: self.rebuild(force=True))

    def _snap(self, page):
        """Снимок страницы вместе с фоном окна (страницы прозрачные — фон рисует окно)."""
        pm = QPixmap(self.stack.size())
        p = QPainter(pm)
        off = self.stack.mapTo(self, QPoint(0, 0))
        p.translate(-off.x(), -off.y())
        paint_bg(p, self.rect())
        p.translate(off.x(), off.y())
        page.render(p, QPoint(0, 0), renderFlags=QWidget.RenderFlag.DrawChildren)
        p.end()
        return pm

    def _state(self):
        w = self.stack.currentWidget()
        if w is self.pages["detail"]:
            return ("detail", self._detail_obj)
        return (next((k for k, v in self.pages.items() if v is w), "home"), None)

    def go_back(self):
        sfx("back")
        if not self.history:
            self.go("home", remember=False, back=True)
            return
        key, obj = self.history.pop()
        if key == "detail" and obj is not None:
            kind, data = obj
            if kind == "playlist":
                pl = self.playlists.get(data)
                if pl is None:
                    self.go_back()
                    return
                self.detail.show_playlist(pl)
            else:
                (self.detail.show_album if kind == "album" else self.detail.show_artist)(data)
            self._detail_obj = obj
        self.go(key, remember=False, back=True)
        self._mark_playlist()

    # ------------------------------------------------------------ обложки и фото из интернета (в фоне)
    def _online_cached(self, key, url_fn):
        import online_art as oa
        os.makedirs(self._online_dir, exist_ok=True)
        h = hashlib.md5("|".join(key).encode("utf-8", "replace")).hexdigest()
        f = os.path.join(self._online_dir, h + ".jpg")
        none = os.path.join(self._online_dir, h + ".none")
        if os.path.isfile(f):
            img = QImage(f)
            return None if img.isNull() else img
        if os.path.isfile(none) and time.time() - os.path.getmtime(none) < 14 * 86400:
            return None                                   # недавно искали — не нашлось
        try:
            url = url_fn()
        except oa.Offline:
            return None                                   # нет сети — попробуем в другой раз
        if not url:
            open(none, "w").close()
            return None
        try:
            img = QImage.fromData(oa.get_bytes(url))
        except Exception:
            return None
        if img.isNull():
            return None
        img = img.scaled(360, 360, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        img.save(f, "JPG", 90)
        return img

    def now_artist(self):
        """Исполнитель текущего трека; в тегах пусто — тот, что нашёлся в интернете."""
        o = self.owner
        a = getattr(o, "artist", "") or ""
        if a:
            return a
        path = getattr(o, "loaded_path", None)
        return self.lib.auto_artist(path) if isinstance(path, str) and path else ""

    def _online_on(self):
        return getattr(self.owner, "cfg", {}).get("online_art", True)

    def _online_cover(self, path):
        if not self._online_on():
            return None
        import online_art as oa
        it = self.lib.items.get(path) or {}
        ga, gt = libmod.guess_from_name(path)
        artist, album, title = libmod.tag_artist(it) or ga or it.get("ag", ""), it.get("al") or "", it.get("t") or gt
        if album:
            key = ("al", libmod.norm(artist), libmod.norm(album))
            return self._online_cached(key, lambda: oa.album_cover_url(artist, album))
        if artist and title:
            key = ("tr", libmod.norm(artist), libmod.norm(title))
            return self._online_cached(key, lambda: oa.track_cover_url(artist, title))
        return None

    def _online_artist(self, name):
        if not self._online_on() or name in (libmod.UNKNOWN_ARTIST, libmod.VARIOUS):
            return None
        import online_art as oa
        return self._online_cached(("ar", libmod.norm(name)), lambda: oa.artist_photo_url(name))

    # ------------------------------------------------------------ плейлисты
    def sync_playlists(self):
        """Список плейлистов слева."""
        while self.pl_box.count():
            it = self.pl_box.takeAt(0)
            wd = it.widget()
            if wd is not None:
                wd.hide()
                wd.setParent(None)
                wd.deleteLater()
        self.pl_btns = {}
        for pl in self.playlists.items:
            b = self.more.SidePlaylist(pl["name"].upper())
            b.setToolTip(f"{pl['name']} · {len(pl['tracks'])} трек.  (перетащи сюда треки)")
            b.clicked.connect(lambda _=False, pid=pl["id"]: self.open_playlist(pid))
            b.dropped.connect(lambda paths, pid=pl["id"]: self._drop_to_playlist(pid, paths))
            self.pl_box.addWidget(b)
            self.pl_btns[pl["id"]] = b
        if not self.playlists.items:
            hint = QLabel("Нажми «+» или перетащи\nсюда трек из списка")
            hint.setObjectName("count")
            self.pl_box.addWidget(hint)
        self.pl_box.addStretch(1)
        self._pl_version = self.playlists.version
        self._mark_playlist()

    def _mark_playlist(self):
        cur = self._detail_obj[1] if (self._detail_obj and self._detail_obj[0] == "playlist"
                                      and self.stack.currentWidget() is self.pages.get("detail")) else None
        for pid, b in self.pl_btns.items():
            b.setChecked(pid == cur)

    def playlists_changed(self):
        self.sync_playlists()
        if self._detail_obj and self._detail_obj[0] == "playlist" and self.stack.currentWidget() is self.detail:
            pl = self.playlists.get(self._detail_obj[1])
            if pl:
                self.detail.show_playlist(pl)
        if self.stack.currentWidget() is self.home:
            self.home.refresh()

    def open_playlist(self, pid):
        pl = self.playlists.get(pid)
        if not pl:
            return
        self._detail_obj = ("playlist", pid)
        self.detail.show_playlist(pl)
        self.go("detail")
        self._mark_playlist()

    def new_playlist(self, paths=()):
        name = self.more.ask_name(self, "Новый плейлист")
        if not name:
            return
        pl = self.playlists.create(name, paths)
        self.playlists_changed()
        self.open_playlist(pl["id"])

    def _drop_to_playlist(self, pid, paths):
        pl = self.playlists.get(pid)
        n = self.playlists.add(pid, paths)
        self.toast(f"＋ {n} В «{pl['name']}»" if n else "УЖЕ ЕСТЬ В ПЛЕЙЛИСТЕ")
        self.playlists_changed()

    def add_to_playlist(self, paths):
        pop = self.more.AddToPlaylist(self, paths)
        pop.setStyleSheet(self.styleSheet())
        pos = self.mapFromGlobal(__import__("PySide6.QtGui", fromlist=["QCursor"]).QCursor.pos())
        pop.adjustSize()
        x = max(10, min(self.width() - pop.width() - 10, pos.x() - pop.width() // 2))
        y = max(10, min(self.height() - pop.height() - 10, pos.y() - 40))
        pop.move(self.mapToGlobal(QPoint(x, y)))
        pop.show()

    def rename_playlist(self, pid):
        pl = self.playlists.get(pid)
        if pl:
            name = self.more.ask_name(self, "Переименовать", pl["name"])
            if name:
                self.playlists.rename(pid, name)
                self.playlists_changed()

    def playlist_cover(self, pid):
        from PySide6.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getOpenFileName(self, "Обложка плейлиста", os.path.expanduser("~\\Pictures"),
                                            "Картинки (*.jpg *.jpeg *.png *.webp *.bmp)")
        if fn:
            img = QImage(fn)
            if not img.isNull():
                self.playlists.set_cover(pid, img)
                self.playlists_changed()

    def delete_playlist(self, pid):
        from PySide6.QtWidgets import QMessageBox
        pl = self.playlists.get(pid)
        if not pl:
            return
        if QMessageBox.question(self, "Удалить плейлист", f"Удалить плейлист «{pl['name']}»?\n"
                                "Сами треки останутся на диске.") != QMessageBox.StandardButton.Yes:
            return
        self.playlists.delete(pid)
        self.history = [h for h in self.history if h != ("detail", ("playlist", pid))]
        self.playlists_changed()
        self.go("home", remember=False)

    # ------------------------------------------------------------ очередь и подсказки
    def toggle_queue(self, show=None):
        show = (not self.queue.isVisible()) if show is None else show
        self.queue.setVisible(show)
        self.bar.queue_btn.setChecked(show)
        if show:
            self.queue.refresh(True)

    def toast(self, text):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move((self.width() - self._toast.width()) // 2 + 120, self.height() - 92 - self._toast.height() - 16)
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start()

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

    def track_menu(self, view, pt, paths, playlist=None):
        ix = view.indexAt(pt)
        if not ix.isValid():
            return
        path = paths[ix.row()]
        sel = view.selected_paths() if hasattr(view, "selected_paths") else []
        group = sel if (len(sel) > 1 and path in sel) else [path]
        m = QMenu(self)
        if len(group) > 1:
            m.addAction(f"▶  Играть выбранные ({len(group)})", lambda: self.play_paths(group, group[0]))
        else:
            m.addAction("▶  Играть", lambda: self.play_paths(paths, path))
        m.addAction("⏭  Играть следующим", lambda: (self.owner.queue_insert(group, 0), self.toast("⏭ СЛЕДУЮЩИМ")))
        m.addAction("☰  Добавить в очередь", lambda: (self.owner.queue_insert(group, None),
                                                       self.toast(f"＋ {len(group)} В ОЧЕРЕДЬ")))
        m.addAction("➕  В плейлист…", lambda: self.add_to_playlist(group))
        if playlist:
            m.addAction("✖  Убрать из плейлиста", lambda: (self.playlists.remove(playlist, group),
                                                           self.playlists_changed()))
        m.addSeparator()
        if len(group) > 1:
            m.addAction(f"✎  Теги выбранных ({len(group)})…", lambda: self.batch_tags(group))
        else:
            m.addAction("✎  Теги…", lambda: self.owner.edit_tags(path))
        al = next((a for a in self.albums if path in a["tracks"]), None)
        if al:
            m.addAction(f"💿  Альбом «{al['title']}»", lambda: self.open_album(al))
        ar_name = libmod.track_view(path, self.items.get(path, {}))[1]
        ar = next((a for a in self.artists if a["name"] == ar_name), None)
        if ar:
            m.addAction(f"🎤  {ar['name']}", lambda: self.open_artist(ar))
        autos = [p for p in group if self.lib.auto_artist(p)]
        if autos:                                           # исполнитель подставлен из интернета
            m.addAction("✖  Не тот исполнитель — убрать" + (f" ({len(autos)})" if len(autos) > 1 else ""),
                        lambda: [self.lib.reject_artist(p) for p in autos])
            if len(group) == 1:
                m.addAction("✎  Не тот исполнитель — вписать своего…", lambda: self.owner.edit_tags(path))
        m.addAction("📂  Показать в папке", lambda: __import__("subprocess").Popen(["explorer", "/select,", path]))
        m.exec(view.viewport().mapToGlobal(pt))

    # ------------------------------------------------------------ жизнь окна
    def _pace(self):
        """Частота кадров по делу: полная — только когда окно перед глазами и музыка играет."""
        page = self.stack.currentWidget()
        active = self.isActiveWindow() and not self.isMinimized()
        try:
            playing = bool(self.owner.is_playing())
        except Exception:
            playing = True
        if self.isMinimized():
            ms, bar = 500, 500
        elif page in (self.now, self.home):
            if page is self.now and time.monotonic() < max(self.now.vol_until, self.now._glide_until,
                                                           self.now._mouse_t + 1.0) + 0.5:
                ms = 16                                     # громкость, переезд текста, мышь над обложкой — 60 кадров
            else:                                           # само по себе текст и волны идут в 30 кадров
                ms = (33 if playing else 50) if active else (100 if playing else 250)
            bar = 33 if (active and playing) else 250
        else:
            ms, bar = 250, (33 if (active and playing) else 250)
        if self.timer.interval() != ms:
            self.timer.setInterval(ms)
        if self.bar._pos_timer.interval() != bar:
            self.bar._pos_timer.setInterval(bar)

    def tick(self):
        self._pace()
        if self.isMinimized():                              # свёрнуто — ничего не рисуем
            return
        self.bar.sync()
        if self.queue.isVisible():
            self.queue.refresh()
        if self.stack.currentWidget() is self.home:
            self.home.wave.animate()
            if int(time.monotonic() * 4) % 2 == 0:
                self.home.wave.sync()
        if self.playlists.version != self._pl_version:
            self.sync_playlists()
        if self.stack.currentWidget() is self.now:
            self.now.update()                               # волна внутри страницы перерисуется вместе с ней
            if int(time.monotonic() * 4) % 2 == 0:
                self.now.wave.sync()
        lp = getattr(self.owner, "loaded_path", None)
        if lp != self._last_path:
            self._last_path = lp
            self.tracks_view.viewport().update()
            self.detail.tracks.viewport().update()
        st = "сканирую папки…" if self.lib.scanning else ("ищу исполнителей…" if self.lib.finding else "")
        if st != self.scan_lbl.text():
            self.scan_lbl.setText(st)
        if not self.lib.scanning and len(self.lib) != self._built_n:
            self.rebuild()
        if self.lib.auto_version != self._auto_v:           # нашлись исполнители в интернете
            paths, self.lib.auto_paths = self.lib.auto_paths, []
            for p in paths:                                 # теперь по исполнителю найдётся и обложка
                if self.covers.mem.get(p, 0) is None:
                    self.covers.mem.pop(p, None)
            self.bar._art_key = None
            self.rebuild(force=True)

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
        if not getattr(self, "_styled_native", False):
            self._styled_native = True
            self.showEvent_native()

    def closeEvent(self, e):
        # крестик — не выход: окно прячется, программа живёт в трее с маленьким плеером
        e.ignore()
        self.hide()
        self.closed.emit()

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
            return
        ed = self._edges(e.position())
        if e.button() == Qt.MouseButton.LeftButton and ed != Qt.Edge(0) and self.windowHandle() is not None:
            self.windowHandle().startSystemResize(ed)
            return
        super().mousePressEvent(e)
