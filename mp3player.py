# -*- coding: utf-8 -*-
"""
MP3 Плеер — маленький плеер-виджет для рабочего стола Windows.
Окно без фона, в форме МП3-плеера. Боковые кнопки переключают музыку.
"""
import os
import sys
import json
import math
import random
import re
import threading
import time

from PySide6.QtCore import (Qt, QTimer, QRect, QRectF, QPointF, QUrl, QStandardPaths,
                            QLockFile, QPoint, QBuffer, QIODevice, QObject, Signal, QThread)
from PySide6.QtGui import (QPixmap, QPainter, QColor, QFont, QFontMetricsF, QTransform, QDesktopServices,
                           QPolygonF, QImage,
                           QIcon, QAction, QActionGroup, QPainterPath, QPen,
                           QLinearGradient, QGuiApplication, QCursor)
from PySide6.QtWidgets import (QApplication, QWidget, QMenu, QSystemTrayIcon,
                               QFileDialog, QToolTip, QMessageBox)
from PySide6.QtMultimedia import QMediaPlayer, QMediaMetaData, QSoundEffect

from media_keys import SystemMedia, GlobalHotkeys, HOTKEYS, MEDIA_FALLBACK
from panels import (PlaylistPanel, CoverPopup, NowPlayingToast, EqPanel, WhatsNewPanel, set_theme as panels_set_theme,
                    display_name)
import eq as eqmod
from audio_engine import AudioEngine
from lyrics import load_for_track, load_file, INDEX as LRC_INDEX
from spectrum import Spectrum
from desktop_keeper import DesktopKeeper
import winassoc
import geom3d
from stats import Stats, fmt_dur
import radio as radiomod
import doulander_link as dlmod
import updater
import lyrics_online
import loudness

APP_NAME = "MP3Player"
APP_TITLE = "MP3 Плеер"
AUDIO_EXT = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a",
             ".aac", ".wma", ".aiff", ".aif", ".mka"}

# ---- координаты на исходной картинке (assets/player.png, 303 x 1402) ----
# --- раскладка текущего скина (заполняет apply_skin_layout) ---
SCREEN = QRectF(45, 392, 162, 669)             # экран в координатах картинки
SCREEN_R = 6                                    # скругление экрана
LCD_ROT = 90                                    # 90 — экран «стоя», текст повёрнут; 0 — обычный
BUTTONS = {}                                    # роль -> прямоугольник кнопки
PRESS_OFF = (-1.5, 0.0)                         # сдвиг «вдавленной» кнопки
FOLDER_ZONE = None                              # зона «следующая папка»
CAP = None                                      # колпачок: dict или None
REELS = []                                      # катушки кассеты: (x, y, r)
SKIN_HINTS = {}
HIT_PAD = 10                                    # кнопки чуть «шире» для мыши
LCD_T = 162                                     # «высота» экрана в единицах раскладки текста
# полоска прогресса в «повёрнутых» координатах экрана (вдоль экрана u, поперёк v)
LCD_M, VOL_W = 16, 64
BAR_V0, BAR_V1 = 132, 146
LONG_MS = 20 * 60 * 1000                        # «длинный» файл: запоминать место
# прозрачный запас вокруг плеера — место, куда «отлетает» снятый колпачок
PAD_X, PAD_TOP = 40, 240
THEMES = {   # фон, фон с подсветкой, «чернила»
    "ЗЕЛЁНАЯ":   ((150, 171, 164), (176, 222, 196), (26, 36, 33)),
    "ГОЛУБАЯ":   ((136, 160, 176), (150, 206, 242), (14, 26, 46)),
    "ОРАНЖЕВАЯ": ((178, 160, 128), (255, 184, 92), (42, 22, 6)),
    "КРАСНАЯ":   ((172, 142, 142), (255, 128, 116), (44, 10, 10)),
    "БЕЛАЯ":     ((166, 170, 168), (236, 241, 239), (20, 22, 24)),
}
STATS_HINTS = {
    "prev": "◀  Предыдущая страница итогов",
    "next": "Следующая страница итогов  ▶",
    "play": "Выйти из итогов",
    "shuffle": "Выйти из итогов",
    "menu": "Выйти из итогов",
}
USB_HINTS = {
    "prev": "▲  Вверх по меню",
    "play": "OK  Изменить / выбрать",
    "next": "▼  Вниз по меню",
    "shuffle": "⏏  Выйти из настроек",
    "menu": "⏏  Выйти из настроек",
}
BOOT_LEN = 2.6                                  # длина заставки, сек
BYE_LEN = 1.0
EQ_BANDS = 12
# «характер» полос: басы выше и медленнее, верха ниже и дёрганее
EQ_PROFILE = [0.95, 1.0, 0.92, 0.85, 0.8, 0.74, 0.7, 0.66, 0.6, 0.55, 0.5, 0.42]
SFX_LEVELS = {"Тихо": 0.25, "Средне": 0.5, "Громко": 0.85}   # громкость звуков кнопок
CROSSFADES = {"Выключен": 0, "2 сек": 2000, "4 сек": 4000, "6 сек": 6000, "8 сек": 8000}                                # тёмный колпачок сверху (клик — плейлист)
BUTTON_HINTS = {
    "prev": "⏮  Предыдущий трек\n(удерживай — перемотка назад)",
    "play": "⏯  Играть / Пауза\n(удерживай — повтор A–B)",
    "next": "⏭  Следующий трек\n(удерживай — перемотка вперёд)",
    "shuffle": "🔀  Перемешивание вкл / выкл",
    "cap": "🔌  Снять колпачок — настройки (USB-режим)",
    "bottom": "📁  Следующая папка",
    "menu": "⚙  Настройки на экране",
}
SIZES = {"Маленький": 0.6, "Средний": 0.8, "Большой": 1.0, "Огромный": 1.25}   # × «по экрану»
SCALE_MIN, SCALE_MAX = 0.15, 2.5


def auto_scale(w=303, h=1402):
    """Плеер ~70% высоты экрана, но не шире трети экрана."""
    g = QGuiApplication.primaryScreen().availableGeometry()
    return round(max(0.2, min(1.8, g.height() * 0.7 / h, g.width() * 0.32 / w)), 2)


def _rect(v):
    return QRectF(*v) if v else None


def apply_skin_layout(meta):
    """Перенести раскладку из skin.json в глобальные настройки отрисовки."""
    global SCREEN, SCREEN_R, LCD_ROT, BUTTONS, PRESS_OFF, FOLDER_ZONE, CAP, REELS, SKIN_HINTS
    global PAD_X, PAD_TOP
    SCREEN = _rect(meta["screen"])
    SCREEN_R = float(meta.get("screen_radius", 6))
    rot = meta.get("text_rotation")
    LCD_ROT = int(rot) if rot is not None else (90 if SCREEN.height() > SCREEN.width() else 0)
    order = ["play", "prev", "next", "shuffle", "menu"]          # центр крестовины — первым
    btns = meta.get("buttons", {})
    BUTTONS = {k: _rect(btns[k]) for k in order if btns.get(k)}
    po = meta.get("press_offset", [-1.5, 0])
    PRESS_OFF = (float(po[0]), float(po[1]))
    FOLDER_ZONE = _rect(meta.get("folder_zone"))
    cap = meta.get("cap")
    CAP = None
    if cap:
        CAP = {"bottom": float(cap.get("bottom", 0)), "pivot_x": float(cap.get("pivot_x", 0)),
               "lift": float(cap.get("lift", 180)), "plug": _rect(cap.get("plug"))}
    REELS = [tuple(r) for r in meta.get("reels", [])]
    SKIN_HINTS = meta.get("hints", {})
    pad = meta.get("pad", [10, 10])
    PAD_X, PAD_TOP = float(pad[0]), float(pad[1])

LCD_BG = QColor(150, 171, 164)
LCD_BG_LIT = QColor(176, 222, 196)
LCD_INK = QColor(26, 36, 33)


def apply_theme(name):
    """Сменить цвета ЖК-экрана и акценты всплывающих окон."""
    global LCD_BG, LCD_BG_LIT, LCD_INK
    bg, lit, ink = THEMES.get(name, THEMES["ЗЕЛЁНАЯ"])
    LCD_BG, LCD_BG_LIT, LCD_INK = QColor(*bg), QColor(*lit), QColor(*ink)
    accent = QColor(*[round(a * 0.45 + b * 0.55) for a, b in zip(bg, lit)])
    panels_set_theme(accent.name(), LCD_INK.name(), LCD_BG_LIT.name())


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def fmt_time(ms):
    if ms is None or ms < 0:
        ms = 0
    s = int(ms // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def npath(p):
    """Один вид пути: C:\\Music\\song.mp3 (а не C:/Music\\song.mp3) — иначе привязки,
    ♥, закладки и статистика «не узнают» тот же трек, открытый по-другому."""
    return os.path.normpath(p) if p else p


def pkey(p):
    """Ключ для сравнения путей (без учёта регистра в Windows)."""
    return os.path.normcase(os.path.normpath(p)) if p else ""


def scan_audio(paths, recursive=True):
    out = []
    paths = [npath(p) for p in paths]
    for p in paths:
        if os.path.isdir(p) and not recursive:          # только эта папка — быстро
            try:
                for f in os.listdir(p):
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT and os.path.isfile(os.path.join(p, f)):
                        out.append(npath(os.path.join(p, f)))
            except OSError:
                pass
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                        out.append(npath(os.path.join(root, f)))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXT:
            out.append(p)
    out.sort(key=natural_key)
    return out


# ---------------------------------------------------------------- Windows
class WinDesktop:
    """Прикрепляет окно к рабочему столу (не прячется по Win+D, не лезет поверх окон)."""
    ok = sys.platform == "win32"

    @staticmethod
    def _api():
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            setlp = u.SetWindowLongPtrW
        else:
            setlp = u.SetWindowLongW
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        return u, setlp

    @classmethod
    def apply(cls, widget, on_desktop):
        """Окно не забирает фокус и не всплывает поверх других окон при клике.
        В режиме «на рабочем столе» оно обычное (не поверх всех): живёт под окнами
        программ и видно, когда они свёрнуты."""
        if not cls.ok:
            return
        try:
            u, setlp = cls._api()
            hwnd = int(widget.winId())
            GWL_EXSTYLE, GWL_HWNDPARENT = -20, -8
            WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x00000080
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            setlp(hwnd, GWL_HWNDPARENT, 0)       # без «хозяина» — иначе может уйти под рабочий стол
        except Exception as e:  # не критично
            print("desktop style failed:", e)

    @classmethod
    def peek_topmost(cls, widget, ms=4000):
        """На время сделать окно «поверх всех», потом вернуть обычным —
        чтобы при запуске его точно было видно, даже если поверх открыт проводник."""
        if not cls.ok:
            return
        try:
            import ctypes
            u, _ = cls._api()
            bits = 64 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
            topmost = (1 << bits) - 1        # HWND_TOPMOST = -1
            notop = (1 << bits) - 2          # HWND_NOTOPMOST = -2
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040
            u.SetWindowPos(int(widget.winId()), topmost, 0, 0, 0, 0, flags)

            def back():
                try:
                    if getattr(widget, "on_desktop", True):
                        u.SetWindowPos(int(widget.winId()), notop, 0, 0, 0, 0, flags)
                except Exception:
                    pass
            QTimer.singleShot(ms, back)
        except Exception as e:
            print("peek_topmost:", e)

    @classmethod
    def bring_front(cls, widget):
        """Показать окно поверх остальных (не забирая фокус)."""
        if not cls.ok:
            widget.raise_()
            return
        try:
            u, _ = cls._api()
            # HWND_TOP, SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_SHOWWINDOW
            u.SetWindowPos(int(widget.winId()), 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        except Exception:
            widget.raise_()


class Autostart:
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    @staticmethod
    def command():
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.py")
        return f'"{w if os.path.exists(w) else exe}" "{entry}"'

    @classmethod
    def enabled(cls):
        if sys.platform != "win32":
            return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        except OSError:
            return False

    @classmethod
    def refresh(cls):
        """exe переехал (например, новая сборка) — обновить путь в автозапуске."""
        if sys.platform != "win32" or not cls.enabled():
            return
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.KEY) as k:
                cur = winreg.QueryValueEx(k, APP_NAME)[0]
            if cur != cls.command():
                cls.set(True)
        except OSError:
            pass

    @classmethod
    def set(cls, on):
        if sys.platform != "win32":
            return
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cls.KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cls.command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass


# ---------------------------------------------------------------- Player
class MiniBar(QWidget):
    """Мини-режим: полоска с ЖК-окошком (название, время, прогресс) и кнопками ⏮ ⏯ ⏭ ⤢."""
    W, H = 360, 58
    LCD = QRectF(8, 8, 214, 42)
    BTN = {"prev": QPointF(246, 29), "play": QPointF(279, 29), "next": QPointF(312, 29)}
    EXPAND = QRectF(334, 21, 18, 16)
    HINTS = {"prev": "⏮  Предыдущий", "play": "⏯  Играть / пауза", "next": "⏭  Следующий",
             "expand": "⤢  Большой плеер  (или двойной клик по окошку)", "bar": "Перемотка"}

    def __init__(self, owner):
        super().__init__(None)
        self.o = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFixedSize(self.W, self.H)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(resource("icon.ico")))
        self.pressed = None
        self.hover = None
        self.drag = None
        self.dragged = False
        self.apply_flags()

    @property
    def on_desktop(self):
        return self.o.on_desktop

    def apply_flags(self):
        f = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
        if not self.o.on_desktop:
            f |= Qt.WindowType.WindowStaysOnTopHint
        vis = self.isVisible()
        self.setWindowFlags(f)
        if vis:
            self.show()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: WinDesktop.apply(self, self.o.on_desktop))

    # --- зоны
    def zone(self, pt):
        for k, c in self.BTN.items():
            if (pt - c).manhattanLength() <= 17:
                return k
        if self.EXPAND.adjusted(-4, -4, 4, 4).contains(pt):
            return "expand"
        bar = self.bar_rect()
        if bar.adjusted(-2, -5, 2, 5).contains(pt) and self.o.player.duration() > 0:
            return "bar"
        if self.LCD.contains(pt):
            return "lcd"
        return None

    def bar_rect(self):
        L = self.LCD
        return QRectF(L.left() + 96, L.bottom() - 11, L.width() - 104, 5)

    # --- рисование
    def paintEvent(self, _e):
        o = self.o
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        body = QRectF(1, 1, self.W - 2, self.H - 2)
        g = QLinearGradient(body.topLeft(), body.bottomLeft())
        g.setColorAt(0, QColor("#353c44"))
        g.setColorAt(0.5, QColor("#23292f"))
        g.setColorAt(1, QColor("#15191d"))
        path = QPainterPath()
        path.addRoundedRect(body, 15, 15)
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawLine(QPointF(14, 3), QPointF(self.W - 14, 3))           # блик сверху
        p.setPen(QPen(QColor("#0b0d12"), 2))
        p.drawPath(path)

        # ЖК-окошко
        now = time.monotonic()
        lit = max(0.0, min(1.0, (o.light_until - now) / 0.8))
        bg = QColor(round(LCD_BG.red() + (LCD_BG_LIT.red() - LCD_BG.red()) * lit),
                    round(LCD_BG.green() + (LCD_BG_LIT.green() - LCD_BG.green()) * lit),
                    round(LCD_BG.blue() + (LCD_BG_LIT.blue() - LCD_BG.blue()) * lit))
        L = self.LCD
        lp = QPainterPath()
        lp.addRoundedRect(L, 7, 7)
        p.fillPath(lp, bg)
        p.setPen(QPen(QColor("#0b0d12"), 1.5))
        p.drawPath(lp)
        p.save()
        p.setClipPath(lp)
        ink = QColor(LCD_INK)
        # значок состояния
        ic = QRectF(L.left() + 9, L.top() + 8, 10, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        if o.is_playing():
            tri = QPainterPath()
            tri.moveTo(ic.left(), ic.top())
            tri.lineTo(ic.right(), ic.center().y())
            tri.lineTo(ic.left(), ic.bottom())
            tri.closeSubpath()
            p.drawPath(tri)
        else:
            p.drawRect(QRectF(ic.left(), ic.top(), 3.5, ic.height()))
            p.drawRect(QRectF(ic.left() + 6, ic.top(), 3.5, ic.height()))
        # бегущее название
        f = QFont()
        f.setFamilies(["Consolas", "Lucida Console", "Courier New"])
        f.setPixelSize(15)
        f.setBold(True)
        title = (f"{o.artist} — {o.title}" if o.artist else o.title) if o.files else "НЕТ МУЗЫКИ"
        if now < o.notice[1]:
            title = o.notice[0]
        tr = QRectF(L.left() + 26, L.top() + 3, L.width() - 32, 20)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(title)
        x = tr.left()
        if w > tr.width():
            cyc = w + 50
            t = (now - o.t0) * 32
            x -= max(0.0, (t % (cyc + 45)) - 45)
        p.setFont(f)
        p.setClipRect(tr, Qt.ClipOperation.IntersectClip)
        for dx in ((0, cyc) if w > tr.width() else (0,)):
            p.setPen(QColor(0, 0, 0, 30))
            p.drawText(QPointF(x + dx + 1.5, tr.top() + 16.5), title)
            p.setPen(ink)
            p.drawText(QPointF(x + dx, tr.top() + 15), title)
        p.restore()
        # время и прогресс
        dur, pos = o.player.duration(), o.player.position()
        f2 = QFont(f)
        f2.setPixelSize(11)
        p.setFont(f2)
        p.setPen(ink)
        p.drawText(QPointF(L.left() + 9, L.bottom() - 6), f"{fmt_time(pos)}/{fmt_time(dur)}" if dur > 0 else "--:--")
        bar = self.bar_rect()
        p.setPen(QPen(ink, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bar)
        if dur > 0:
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * min(1.0, pos / dur), bar.height()), ink)

        # кнопки
        for k, c in self.BTN.items():
            r = 13.5
            down = self.pressed == k
            bg2 = QLinearGradient(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
            bg2.setColorAt(0, QColor("#f1f3f6") if not down else QColor("#b9c0c8"))
            bg2.setColorAt(1, QColor("#aeb6c0") if not down else QColor("#8d959f"))
            p.setBrush(bg2)
            p.setPen(QPen(QColor("#0b0d12"), 2 if self.hover != k else 2.6))
            p.drawEllipse(c + QPointF(0, 1 if down else 0), r, r)
            self._icon(p, k, c + QPointF(0, 1 if down else 0))
        # «развернуть»
        e = self.EXPAND
        p.setPen(QPen(QColor("#c9d1da") if self.hover != "expand" else QColor("#ffffff"), 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(e, 3, 3)
        p.drawLine(QPointF(e.left() + 4, e.bottom() - 4), QPointF(e.right() - 4, e.top() + 4))
        p.drawLine(QPointF(e.right() - 9, e.top() + 4), QPointF(e.right() - 4, e.top() + 4))
        p.drawLine(QPointF(e.right() - 4, e.top() + 4), QPointF(e.right() - 4, e.top() + 9))
        p.end()

    def _icon(self, p, k, c):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#23282e"))
        x, y = c.x(), c.y()
        if k == "play":
            if self.o.is_playing():
                p.drawRect(QRectF(x - 5, y - 6, 3.6, 12))
                p.drawRect(QRectF(x + 1.4, y - 6, 3.6, 12))
            else:
                tri = QPainterPath()
                tri.moveTo(x - 4, y - 6.5)
                tri.lineTo(x + 6, y)
                tri.lineTo(x - 4, y + 6.5)
                tri.closeSubpath()
                p.drawPath(tri)
        else:
            sgn = 1 if k == "next" else -1
            for off in (-4.5, 1.5):
                tri = QPainterPath()
                xx = x + sgn * off
                tri.moveTo(xx - sgn * 3, y - 5.5)
                tri.lineTo(xx + sgn * 3.5, y)
                tri.lineTo(xx - sgn * 3, y + 5.5)
                tri.closeSubpath()
                p.drawPath(tri)
            bx = x + sgn * 5.5
            p.drawRect(QRectF(min(bx, bx + sgn * 2), y - 5.5, 2, 11))

    # --- мышь
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            if e.button() == Qt.MouseButton.MiddleButton:
                self.o.toggle_play()
            return
        z = self.zone(e.position())
        if z in self.BTN or z == "expand":
            self.pressed = z
            self.o.sound("click_down")
            self.update()
        elif z == "bar":
            b = self.bar_rect()
            frac = max(0.0, min(1.0, (e.position().x() - b.left()) / b.width()))
            self.o.player.setPosition(int(frac * self.o.player.duration()))
            self.o.light()
        else:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.dragged = False

    def mouseMoveEvent(self, e):
        if self.drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            t = e.globalPosition().toPoint() - self.drag
            if (t - self.pos()).manhattanLength() > 2 or self.dragged:
                self.dragged = True
                self.move(t)
            return
        z = self.zone(e.position())
        if z != self.hover:
            self.hover = z
            self.setCursor(Qt.CursorShape.PointingHandCursor if z in ("prev", "play", "next", "expand", "bar")
                           else Qt.CursorShape.ArrowCursor)
            if z in self.HINTS:
                QToolTip.showText(e.globalPosition().toPoint(), self.HINTS[z], self)
            else:
                QToolTip.hideText()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.pressed:
            k, self.pressed = self.pressed, None
            self.o.sound("click_up")
            if self.zone(e.position()) == k:
                self.o.light()
                {"prev": lambda: self.o.step(-1), "next": lambda: self.o.step(+1),
                 "play": self.o.toggle_play, "expand": lambda: self.o.set_mini(False)}[k]()
            self.update()
        elif self.drag is not None:
            if self.dragged:
                self.o.cfg["mini_x"], self.o.cfg["mini_y"] = self.x(), self.y()
                self.o.save_later()
            self.drag = None

    def mouseDoubleClickEvent(self, e):
        if self.zone(e.position()) in ("lcd", None):
            self.o.set_mini(False)
        else:
            self.mousePressEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if d:
            self.o.change_volume(0.05 if d > 0 else -0.05)

    def contextMenuEvent(self, e):
        self.o.sync_menu()
        self.o.menu.exec(e.globalPos())

    def leaveEvent(self, _e):
        self.hover = None
        QToolTip.hideText()
        self.update()


UPDATE_REPO = "SevereClaw/MP3-Player"
UPDATE_ASSET = "MP3Player.zip"


class UpdateSignals(QObject):
    event = Signal(str, object)          # ("found"/"progress"/"ready"/"latest"/"error", данные)


class LyricsSignals(QObject):
    done = Signal(str, str, str, bool)   # трек, путь к .lrc (или ""), пояснение, вручную ли


class Mp3Widget(QWidget):
    def __init__(self, settings_path, skip_restore=False):
        super().__init__()
        self.settings_path = settings_path
        self.cfg = self.load_cfg()

        self.data_dir = os.path.dirname(settings_path)
        self.skins = self.find_skins()
        self.skin_id = self.cfg.get("skin", "flash")
        if self.skin_id not in self.skins:
            self.skin_id = "flash"
        self.load_skin_files(self.skin_id)
        self._scaled_cap = None
        self.reel_angle = 0.0
        # 3D: наклон корпуса (рысканье, тангаж), скорость, цель; грани — по контуру картинки
        self.tilt = [0.0, 0.0]
        self.tilt_v = [0.0, 0.0]
        self.drag_vel = [0.0, 0.0]
        self._drag_prev = None
        self.spin = None                  # Ctrl+тянуть: (точка, рысканье, тангаж в начале)
        self.hover_pt = None
        self._phys_last = time.monotonic()
        # колпачок: 0 — надет, 1 — снят
        self.cap_open = False
        self.cap_t = 0.0
        self.usb_sel = 0
        self.usb_top = 0
        apply_theme(self.cfg.get("theme", self.skin_meta.get("theme", "ЗЕЛЁНАЯ")))
        # звуки кнопок
        self.sfx = {}
        self.sounds_dir = os.path.join(self.data_dir, "sounds")
        for name in ("click_down", "click_up", "cap_off", "cap_on", "boot", "beep"):
            own = os.path.join(self.sounds_dir, name + ".wav")      # свой звук заменяет встроенный
            e = QSoundEffect(self)
            e.setSource(QUrl.fromLocalFile(own if os.path.isfile(own) else resource(name + ".wav")))
            e.setVolume(float(self.cfg.get("sfx_volume", 0.5)))
            self.sfx[name] = e
        self._boot_sound_done = False
        if self.cfg.get("size_v") != 2:        # старые версии были слишком мелкими
            self.cfg["scale"] = auto_scale()
            self.cfg["size_v"] = 2
        scales = self.cfg.setdefault("scales", {})
        if "flash" not in scales and self.cfg.get("scale"):
            scales["flash"] = self.cfg["scale"]
        self.scale_f = float(scales.get(self.skin_id) or auto_scale(self.native_w, self.native_h))
        self.angle = float(self.cfg.get("angle", 0.0))
        self.rot_drag = None              # (угол мыши в начале, угол плеера в начале)
        self._scaled = None
        self._scaled_dark = None

        # плейлист
        self.files = []
        self.order = []
        self.pos_in_order = -1
        self.shuffle = bool(self.cfg.get("shuffle", False))
        self.repeat = self.cfg.get("repeat", "all")          # all / one / off
        self.error_streak = 0
        self.pending_seek = None

        # аудио
        self.player = AudioEngine(self, volume=float(self.cfg.get("volume", 0.6)))
        # эквалайзер звука: при «плоском» — обычный путь звука, иначе — свой (dsp_player)
        self.player.equalizer.set(self.cfg.get("eq_gains") or eqmod.PRESETS["Плоский"],
                                  bool(self.cfg.get("eq_on", False)))
        self.player.use_dsp = self.player.equalizer.active() and bool(self.player.dsp_decks)
        self.eq_panel = None
        self.eq_apply_timer = QTimer(self, singleShot=True, interval=60)
        self.eq_apply_timer.timeout.connect(self.apply_eq_now)
        self._eq_pending = None
        # выравнивание громкости: меряем каждый файл один раз и запоминаем
        self.loud_cache = loudness.LoudnessCache(
            os.path.join(os.path.dirname(self.settings_path), "loudness.json"))
        self.loud_worker = None
        self.loud_gains = []                      # поправки уже измеренных — для «средней»
        if loudness.np is not None:
            self.loud_worker = loudness.make_worker(self.loud_cache)
            self.loud_worker.measured.connect(self.on_loudness)
            self.loud_worker.start(QThread.Priority.LowestPriority)
        self.player.set_crossfade(int(self.cfg.get("crossfade", 4000)))
        self.player.nearEnd.connect(self.on_near_end)
        self.spectrum = Spectrum(self.player, self)
        self.radiometa = radiomod.RadioMeta(self)
        self.radiometa.changed.connect(self.on_radio_title)
        # «скачать в плеер» через Doulander
        self.dl = dlmod.DoulanderJobs(self)
        self.dl.started.connect(self.on_dl_started)
        self.dl.titled.connect(self.on_dl_titled)
        self.dl.progress.connect(self.on_dl_progress)
        self.dl.finished.connect(self.on_dl_finished)
        self.dl_pct, self.dl_title, self._dl_flash = None, "", -100
        # автообновление из релизов GitHub
        self.build = updater.current_build()
        self.upd = UpdateSignals(self)
        self.upd.event.connect(self.on_update_event)
        self.upd_state, self.upd_ready, self.upd_manual = "", None, False
        self.upd_idle_since = None
        self.upd_timer = QTimer(self, interval=60 * 1000)        # раз в минуту: пора ли?
        self.upd_timer.timeout.connect(self.update_tick)
        self.upd_timer.start()
        QTimer.singleShot(30 * 1000, self.update_tick)
        self.news_panel = None
        QTimer.singleShot(int(BOOT_LEN * 1000) + 2500, self.check_whats_new)
        # тексты из интернета (lrclib.net)
        self.lyr_sig = LyricsSignals(self)
        self.lyr_sig.done.connect(self.on_lyrics_online)
        self.lyr_busy = set()
        self.lyr_timer = QTimer(self, singleShot=True, interval=1500)
        self.lyr_timer.timeout.connect(lambda: self.fetch_lyrics_online(manual=False))
        self._radio_retry = 0
        self._eq_real_since = None
        # статистика и итоги
        self.stats = Stats(os.path.join(self.data_dir, "stats.json"))
        self._listened = 0.0
        self._play_counted = False
        self.stats_page = None
        # связь с Music → Discord: файл «что играет»
        self.np_path = os.path.join(os.environ.get("APPDATA") or self.data_dir, APP_NAME, "nowplaying.json")
        self._np_last = (None, 0.0)
        self._np_cover = ("", "")         # (файл обложки для Discord, md5)
        # одиночный клик по экрану ждёт, не будет ли второго (двойной — избранное)
        self._screen_click = QTimer(self, singleShot=True,
                                    interval=QApplication.styleHints().mouseDoubleClickInterval())
        self._screen_click.timeout.connect(self.open_playlist)
        self.loaded_path = None
        self.player.mediaStatusChanged.connect(self.on_status)
        self.player.errorOccurred.connect(self.on_error)
        self.player.metaDataChanged.connect(self.on_meta)
        self.player.playbackStateChanged.connect(lambda *_: self.refresh())
        self.player.positionChanged.connect(lambda *_: self.refresh_screen())
        self.player.durationChanged.connect(lambda *_: self.refresh_screen())

        self.title = ""
        self.artist = ""
        self.album = ""
        self._cover = None
        self.lyrics = None
        self.radio = False                # сейчас играет интернет-радио
        self.station = ""
        self.ab_a = None                  # повтор A–B: мс или None
        self.ab_b = None

        # Windows: медиа-клавиши, системная панель, горячие клавиши
        self.data_dir = os.path.dirname(settings_path)
        self._last_tl = 0.0
        self.sysmedia = SystemMedia(self)
        self.sysmedia.command.connect(self.on_media_command)
        self.hotkeys = GlobalHotkeys(self)
        self.hotkeys.command.connect(self.on_media_command)
        if self.cfg.get("hotkeys", True):
            self.hotkeys.register(HOTKEYS)
        if not self.sysmedia.ok:
            self.hotkeys.register(MEDIA_FALLBACK)
        self.player.playbackStateChanged.connect(self.sync_system_status)
        self.player.positionChanged.connect(lambda *_: self.sync_timeline())
        self.player.durationChanged.connect(lambda *_: self.sync_timeline(force=True))
        self.sys_track_timer = QTimer(self, singleShot=True, interval=250)
        self.sys_track_timer.timeout.connect(self.push_system_track)

        # плейлист и всплывающая обложка
        self.playlist_panel = PlaylistPanel()
        self.playlist_panel.play_index.connect(self.play_index)
        self.playlist_panel.download.connect(lambda: (self.playlist_panel.hide(), self.download_dialog()))
        self.cover_popup = CoverPopup()
        self.toast = NowPlayingToast()
        self.toast.clicked.connect(lambda: self.show_player())
        self._announce_until = 0.0

        # состояние UI
        self.pressed = None
        self.scrub = None             # доля 0..1, пока тянем полоску прогресса
        self.hold_seeking = False
        self.drag_from = None
        self.drag_moved = False
        self.hover_btn = None
        self.light_until = 0.0
        self.notice = ("", 0.0)       # временная надпись (текст, до какого времени)
        self.t0 = time.monotonic()

        self.hold_timer = QTimer(self, singleShot=True, interval=450)
        self.hold_timer.timeout.connect(self.start_hold_seek)
        self.seek_timer = QTimer(self, interval=120)
        self.seek_timer.timeout.connect(self.hold_seek_step)
        # заставка и эквалайзер
        self.boot_t0 = (time.monotonic() if self.cfg.get("boot_anim", True) and not skip_restore
                        else None)                  # открыли файл — сразу играть, без заставки
        if self.boot_t0:
            self.light_until = self.boot_t0 + BOOT_LEN + 2.5
        self.bye_t0 = None
        self.eq_val = [0.0] * EQ_BANDS
        self.eq_tgt = [0.0] * EQ_BANDS
        self.eq_peak = [0.0] * EQ_BANDS
        self.eq_next = 0.0
        self.eq_beat = 0.0
        self._last_anim = time.monotonic()
        self.anim = QTimer(self, interval=40)
        self.anim.timeout.connect(self.on_anim)
        self.anim.start()
        self.phys_timer = QTimer(self, interval=16)     # 3D-наклон: ~60 кадров, только пока качается
        self.phys_timer.timeout.connect(self.on_phys)
        QTimer.singleShot(1500, self.prebuild_3d)      # грани посчитать заранее, а не на первом рывке
        self.save_timer = QTimer(self, singleShot=True, interval=1500)
        self.save_timer.timeout.connect(self.save_cfg)
        self.autosave = QTimer(self, interval=30000)
        self.autosave.timeout.connect(lambda: self.is_playing() and self.save_cfg())
        self.autosave.start()

        # окно
        self.on_desktop = self.cfg.get("mode", "desktop") == "desktop"
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(resource("icon.ico")))
        self.apply_flags()
        self.apply_scale()
        self.restore_pos()

        Autostart.refresh()
        if self.cfg.get("assoc") and sys.platform == "win32":
            try:
                winassoc.refresh_if_moved(sorted(AUDIO_EXT))
            except Exception as e:
                print("assoc refresh:", e)
        self.keeper = DesktopKeeper(self)
        self.keeper.enabled = bool(self.cfg.get("keep_desktop", True))
        self.build_menu()
        self.build_tray()
        self.mini = MiniBar(self)
        self.mini_keeper = DesktopKeeper(self.mini)
        self.mini_keeper.enabled = bool(self.cfg.get("keep_desktop", True))

        # восстановить плейлист (если не открыли файл из проводника)
        src = self.cfg.get("sources") or []
        src = [s for s in src if os.path.exists(s)]
        if self.cfg.get("radio") and not skip_restore:
            self.play_radio(int(self.cfg.get("radio_index", 0)), autoplay=False)
        elif src and not skip_restore:
            self.load_sources(src, start_path=self.cfg.get("track"),
                              start_pos=self.cfg.get("position", 0), autoplay=False,
                              flat=bool(self.cfg.get("sources_flat")))

    # ------------------------------------------------------------ config
    def load_cfg(self):
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return {}
        # старые версии сохраняли пути вперемешку с / и \\ — привести к одному виду
        try:
            for k in ("lrc_links", "lrc_offset", "resume", "folder_state"):
                if isinstance(cfg.get(k), dict):
                    cfg[k] = {npath(a): (npath(b) if k == "lrc_links" else b) for a, b in cfg[k].items()}
            for k in ("favorites", "folders", "sources", "lyrics_dirs"):
                if isinstance(cfg.get(k), list):
                    seen, out = set(), []
                    for x in cfg[k]:
                        if x and pkey(x) not in seen:
                            seen.add(pkey(x))
                            out.append(npath(x))
                    cfg[k] = out
            if cfg.get("track"):
                cfg["track"] = npath(cfg["track"])
        except Exception as e:
            print("cfg paths:", e)
        return cfg

    def save_cfg(self):
        self.stats.save()
        self.remember_position()
        self.remember_folder_state()
        cur = self.current_path()
        self.cfg.update({
            "scale": self.scale_f,
            "skin": self.skin_id,
            "volume": round(self.player.volume(), 3),
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "mode": "desktop" if self.on_desktop else "top",
            "cx": round(self.center_global().x()), "cy": round(self.center_global().y()),
            "angle": self.angle,
            "track": cur,
            "position": int(self.player.position()) if cur else 0,
        })
        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print("save failed:", e)

    def save_later(self):
        self.save_timer.start()

    # ------------------------------------------------------------ window
    def apply_flags(self):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                 Qt.WindowType.NoDropShadowWindowHint)
        if not self.on_desktop:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        vis = self.isVisible()
        self.setWindowFlags(flags)
        if vis:
            self.show()
            WinDesktop.apply(self, self.on_desktop)

    # ------------------------------------------------------------ скины
    def skin_dirs(self):
        return [resource("skins"), os.path.join(self.data_dir, "skins")]

    def find_skins(self):
        """id -> (папка, описание). Свои скины — из %APPDATA%\\MP3Player\\skins."""
        found = {}
        for n, base in enumerate(self.skin_dirs()):
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name)
                f = os.path.join(d, "skin.json")
                if not os.path.isfile(f):
                    continue
                try:
                    with open(f, "r", encoding="utf-8-sig") as fh:
                        meta = json.load(fh)
                    if not meta.get("screen") or not os.path.isfile(os.path.join(d, meta.get("body", "body.png"))):
                        continue
                    sid = name if n == 0 else "user:" + name
                    found[sid] = (d, meta)
                except Exception as e:
                    print("skin", name, e)
        return found

    def load_skin_files(self, sid):
        d, meta = self.skins[sid]
        self.skin_meta = meta
        apply_skin_layout(meta)
        self.base = QPixmap(os.path.join(d, meta.get("body", "body.png")))
        cap = (meta.get("cap") or {}).get("image")
        self.cap_pm = QPixmap(os.path.join(d, cap)) if cap else None
        if self.cap_pm is not None and self.cap_pm.isNull():
            self.cap_pm = None
            apply_skin_layout(dict(meta, cap=None))
        self.native_w, self.native_h = self.base.width(), self.base.height()
        self._scaled = None
        self._sides3d = None
        self._layer = None
        self.shape3d = meta.get("shape", "slab")            # slab — брусок, cylinder — трубка
        self.cyl_cx = float(meta.get("axis_x", self.native_w / 2))
        self.cyl_r = float(meta.get("radius", self.native_w / 2))
        self.cyl_band = tuple(meta["band_u"]) if meta.get("band_u") else None
        if self.shape3d == "cylinder":
            self.depth3d = 2 * self.cyl_r                    # для попадания мышкой — лицо на передней точке
        else:
            self.depth3d = float(meta.get("depth") or round(min(self.native_w, self.native_h) * 0.22))

    def set_skin(self, sid, keep_theme=False):
        self.skins = self.find_skins()
        if sid not in self.skins:
            self.flash("СКИН НЕ НАЙДЕН")
            return
        c = self.center_global()
        self.cfg.setdefault("scales", {})[self.skin_id] = self.scale_f
        self.cap_open, self.cap_t = False, 0.0
        self.skin_id = sid
        self.cfg["skin"] = sid
        self.load_skin_files(sid)
        self.scale_f = float(self.cfg["scales"].get(sid) or auto_scale(self.native_w, self.native_h))
        if not keep_theme and self.skin_meta.get("theme"):
            self.set_theme(self.skin_meta["theme"])
        self.apply_geometry(c)
        self.keep_on_screen()
        self.light(3.0)
        QTimer.singleShot(300, self.prebuild_3d)
        self.save_later()

    def cycle_skin(self):
        ids = list(self.find_skins())
        if ids:
            i = ids.index(self.skin_id) if self.skin_id in ids else -1
            self.set_skin(ids[(i + 1) % len(ids)])

    def build_skins_menu(self):
        m = self.skins_menu
        m.clear()
        self.skins = self.find_skins()
        grp = QActionGroup(m)
        user_started = False
        for sid, (_d, meta) in self.skins.items():
            if sid.startswith("user:") and not user_started:
                m.addSeparator()
                user_started = True
            a = m.addAction(meta.get("name", sid), lambda _=False, i=sid: self.set_skin(i))
            a.setCheckable(True)
            a.setChecked(sid == self.skin_id)
            grp.addAction(a)
        m.addSeparator()
        m.addAction("✏  Свой скин из картинки…", self.open_skin_editor)
        m.addAction("📂  Папка своих скинов", self.open_skins_folder)

    def user_skins_dir(self):
        d = os.path.join(self.data_dir, "skins")
        os.makedirs(d, exist_ok=True)
        return d

    def open_skins_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.user_skins_dir()))

    def open_skin_editor(self):
        from skin_editor import SkinEditor
        dlg = SkinEditor(self.user_skins_dir())
        dlg.setWindowIcon(QIcon(resource("icon.ico")))
        if not dlg.open_image():
            return
        if dlg.exec() and dlg.saved_id:
            self.set_skin(dlg.saved_id)
            self.flash("СКИН СОХРАНЁН")

    def auto_scale_here(self):
        return auto_scale(self.native_w, self.native_h)

    # ------------------------------------------------------------ геометрия (масштаб + поворот)
    def canvas_rect(self):
        """Всё, что рисуем, в координатах картинки: корпус + запас для колпачка."""
        r = QRectF(-PAD_X, -PAD_TOP, self.native_w + 2 * PAD_X, self.native_h + PAD_TOP)
        if self.cfg.get("three_d", True):        # место для боков, когда корпус наклонён
            if getattr(self, "shape3d", "slab") == "cylinder":
                m = 40                               # трубка при повороте не шире себя
            else:
                m = getattr(self, "depth3d", 60) + 40
            r = r.adjusted(-m, -m * 0.6, m, m * 0.6)
        return r

    def pivot(self):
        return QPointF(self.native_w / 2, self.native_h / 2)

    def _rs(self):
        t = QTransform()
        t.scale(self.scale_f, self.scale_f)
        t.rotate(self.angle)
        pv = self.pivot()
        t.translate(-pv.x(), -pv.y())
        return t

    def xf(self):
        """Картинка плеера → координаты окна."""
        b = self._rs().mapRect(self.canvas_rect())
        return self._rs() * QTransform.fromTranslate(-b.left(), -b.top())

    def center_global(self):
        c = self.xf().map(self.pivot())
        return QPointF(self.x() + c.x(), self.y() + c.y())

    def place_center(self, c):
        o = self.xf().map(self.pivot())
        self.move(round(c.x() - o.x()), round(c.y() - o.y()))

    def apply_geometry(self, center=None):
        self._scaled = None
        b = self._rs().mapRect(self.canvas_rect())
        self.setFixedSize(max(1, math.ceil(b.width())), max(1, math.ceil(b.height())))
        if center is not None:
            self.place_center(center)
        self.update()

    def apply_scale(self):
        self.apply_geometry()

    def scaled_pixmaps(self):
        dpr = self.devicePixelRatioF()
        w = round(self.native_w * self.scale_f * dpr)
        h = round(self.native_h * self.scale_f * dpr)
        if self._scaled is None or self._scaled.size().width() != w:
            if self.cap_pm is not None:
                self._scaled_cap = self.cap_pm.scaled(
                    w, round(self.cap_pm.height() * self.scale_f * dpr),
                    Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            else:
                self._scaled_cap = None
            pm = self.base.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            dark = QPixmap(pm)
            p = QPainter(dark)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            p.fillRect(dark.rect(), QColor(0, 0, 0, 95))
            p.end()
            self._scaled, self._scaled_dark = pm, dark
        return self._scaled, self._scaled_dark

    def visual_rect(self):
        """Где на экране сам плеер (рамка вокруг повёрнутого корпуса)."""
        r = self.xf().mapRect(QRectF(0, 0, self.native_w, self.native_h))
        return r.translated(self.x(), self.y()).toAlignedRect()

    def keep_on_screen(self):
        vr = self.visual_rect()
        g = (QGuiApplication.screenAt(vr.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        dx = max(g.left() - vr.left(), 0) or min(g.right() - vr.right(), 0)
        dy = max(g.top() - vr.top(), 0) or min(g.bottom() - vr.bottom(), 0)
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)

    def restore_pos(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        s = self.scale_f
        cx, cy = self.cfg.get("cx"), self.cfg.get("cy")
        if cx is None and self.cfg.get("x") is not None:          # старый формат
            cx = self.cfg["x"] + self.native_w * s / 2
            cy = self.cfg["y"] + self.native_h * s / 2
        if cx is not None and cy is not None:
            for sc in QGuiApplication.screens():
                if sc.availableGeometry().contains(QPoint(int(cx), int(cy))):
                    self.place_center(QPointF(cx, cy))
                    self.keep_on_screen()
                    return
        vw = self.visual_rect().width()
        self.place_center(QPointF(scr.right() - vw / 2 - 60, scr.center().y()))
        self.keep_on_screen()

    def set_angle(self, a, snap=True):
        a = ((a + 180.0) % 360.0) - 180.0
        if snap:
            for k in (-180, -90, 0, 90, 180):
                if abs(a - k) < 4:
                    a = float(k)
        if a == -180.0:
            a = 180.0
        c = self.center_global()
        self.angle = a
        self.apply_geometry(c)
        self.cfg["angle"] = a
        self.save_later()

    def hideEvent(self, e):
        super().hideEvent(e)
        if hasattr(self, "cover_popup"):
            self.cover_popup.hide()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: WinDesktop.apply(self, self.on_desktop))

    # ------------------------------------------------------------ playlist
    def load_sources(self, sources, start_path=None, start_pos=0, autoplay=True, fav=False, remember=True,
                     flat=False):
        files = scan_audio(sources, recursive=not flat)
        if not files:
            self.flash("НЕТ АУДИОФАЙЛОВ")
            return
        self.remember_folder_state()
        folder = sources[0] if len(sources) == 1 and os.path.isdir(sources[0]) else None
        if folder and remember:
            folders = self.cfg.setdefault("folders", [])
            if not any(os.path.normcase(f) == os.path.normcase(folder) for f in folders):
                folders.append(folder)
            if start_path is None:
                st = (self.cfg.get("folder_state") or {}).get(folder) or {}
                start_path, start_pos = st.get("track"), st.get("position", 0)
        sources = [npath(x) for x in sources]
        start_path = npath(start_path) if start_path else start_path
        if start_path and start_path not in files:        # тот же файл, записанный иначе
            start_path = next((f for f in files if pkey(f) == pkey(start_path)), start_path)
        self.radio = False
        self.cfg["radio"] = False
        self.cfg["sources"] = sources
        self.cfg["sources_flat"] = flat
        self.cfg["fav_mode"] = fav
        self.files = files
        self.rebuild_order(keep=None)
        idx = 0
        if start_path and start_path in self.files:
            idx = self.files.index(start_path)
        else:
            start_pos = 0
        self.pos_in_order = self.order.index(idx) if self.shuffle else idx
        if self.shuffle and start_path is None:
            self.pos_in_order = 0
        label = ((os.path.basename(folder.rstrip("/\\")) or folder) if folder
                 else "♥ ИЗБРАННОЕ" if fav else "ТРЕКОВ")
        self.flash(f"{label}: {len(self.files)}", 2.2)
        self.load_current(autoplay=autoplay, seek=start_pos, fade_ms=self.fade_ms("skip"))
        if self.playlist_panel.isVisible():
            self.playlist_panel.set_tracks(self.files, self.current_index())
        self.queue_loudness()
        self.save_later()

    # ------------------------------------------------------------ место в файле / папки
    def remember_position(self):
        """Запомнить, где остановились в длинном файле (подкаст, аудиокнига)."""
        path = self.loaded_path
        if not path or not self.cfg.get("resume_long", True):
            return
        dur, pos = self.player.duration(), self.player.position()
        if dur < LONG_MS:
            return
        res = self.cfg.setdefault("resume", {})
        res.pop(path, None)
        if 15000 < pos < dur - 30000:
            res[path] = int(pos)
            while len(res) > 300:
                res.pop(next(iter(res)))

    def current_folder(self):
        src = self.cfg.get("sources") or []
        return src[0] if len(src) == 1 and os.path.isdir(src[0]) else None

    def remember_folder_state(self):
        folder, cur = self.current_folder(), self.current_path()
        if folder and cur and self.files:
            self.cfg.setdefault("folder_state", {})[folder] = {
                "track": cur, "position": int(self.player.position())}

    def switch_folder(self, folder):
        if not os.path.isdir(folder):
            self.flash("ПАПКА НЕ НАЙДЕНА")
            return
        if folder == self.current_folder():
            return
        self.load_sources([folder], autoplay=self.is_playing())

    def next_folder(self):
        folders = [f for f in self.cfg.get("folders", []) if os.path.isdir(f)]
        if not folders:
            self.open_folder()
            return
        cur = self.current_folder()
        i = folders.index(cur) if cur in folders else -1
        nxt = folders[(i + 1) % len(folders)]
        if nxt == cur:
            self.flash("ДРУГИХ ПАПОК НЕТ")
            return
        self.switch_folder(nxt)

    def forget_current_folder(self):
        cur = self.current_folder()
        folders = self.cfg.get("folders", [])
        if cur in folders:
            folders.remove(cur)
            (self.cfg.get("folder_state") or {}).pop(cur, None)
            self.flash("УБРАНА ИЗ СПИСКА")
            self.save_later()

    def build_folders_menu(self):
        m = self.folders_menu
        m.clear()
        cur = self.current_folder()
        folders = self.cfg.get("folders", [])
        grp = QActionGroup(m)
        for f in folders:
            name = os.path.basename(f.rstrip("/\\")) or f
            a = m.addAction(("" if os.path.isdir(f) else "⚠ ") + name,
                            lambda _=False, f=f: self.switch_folder(f))
            a.setToolTip(f)
            a.setCheckable(True)
            a.setChecked(f == cur)
            grp.addAction(a)
        if folders:
            m.addSeparator()
            m.addAction("⟳  Следующая папка  (клик по низу плеера)", self.next_folder)
        m.addAction("➕  Добавить папку…", self.open_folder)
        if cur in folders:
            m.addAction("✖  Убрать текущую из списка", self.forget_current_folder)

    def rebuild_order(self, keep):
        n = len(self.files)
        self.order = list(range(n))
        if self.shuffle and n:
            random.shuffle(self.order)
            if keep is not None and keep in self.order:
                self.order.remove(keep)
                self.order.insert(0, keep)
        if keep is not None and keep in self.order:
            self.pos_in_order = self.order.index(keep)

    def current_index(self):
        if 0 <= self.pos_in_order < len(self.order):
            return self.order[self.pos_in_order]
        return None

    def current_path(self):
        i = self.current_index()
        return self.files[i] if i is not None else None

    def fade_ms(self, kind):
        """kind: 'pause' / 'skip' / 'auto'"""
        xf = int(self.cfg.get("crossfade", 4000))
        if kind == "pause":
            return 350 if self.cfg.get("fade_pause", True) else 0
        if kind == "skip":
            return min(800, xf) if xf else 0
        return xf

    def load_current(self, autoplay=True, seek=0, fade_ms=0):
        path = self.current_path()
        if not path:
            return
        if self.radio:
            self.load_station(autoplay, fade_ms)
            return
        self.radiometa.stop()
        stem = os.path.splitext(os.path.basename(path))[0]
        if " - " in stem:
            a, t = stem.split(" - ", 1)
            self.artist, self.title = a.strip(), t.strip()
        else:
            self.artist, self.title = "", stem
        self.album = ""
        self._cover = None
        self._np_cover = ("", "")
        self.remember_position()                 # место в прошлом (длинном) файле
        resumed = False
        if not seek and self.cfg.get("resume_long", True):
            saved = (self.cfg.get("resume") or {}).get(path)
            if saved:
                seek, resumed = max(0, int(saved) - 3000), True
        self.loaded_path = path
        self.lyrics = self.load_lyrics(path) if self.cfg.get("lyrics", True) else None
        if self.lyrics is None and self.__dict__.get("lyr_timer") is not None:
            self.lyr_timer.start()                   # .lrc нет — через 1,5 с поискать в интернете
        self.ab_a = self.ab_b = None                 # новый трек — без повтора A–B
        self._listened, self._play_counted = 0.0, False
        self.pending_seek = seek if seek and seek > 0 else None
        self.player.load(QUrl.fromLocalFile(path), autoplay=autoplay, fade_ms=fade_ms,
                         gain=self.track_gain(path))
        if resumed:
            self.flash(f"ПРОДОЛЖЕНИЕ {fmt_time(seek)}", 2.5)
        if autoplay:
            self._announce_until = time.monotonic() + 3.0
        if self.playlist_panel.isVisible():
            self.playlist_panel.set_current(self.current_index())
        self.update_tray_tip()
        self.sys_track_timer.start()
        self.refresh()

    def step(self, delta, user=True):
        if not self.order:
            self.flash("ПКМ → ОТКРЫТЬ ПАПКУ")
            return
        # «Назад» после 3 секунд — в начало трека
        if delta < 0 and user and self.player.position() > 3000 and not self.radio:
            self.player.setPosition(0)
            return
        n = len(self.order)
        p = self.pos_in_order + delta
        if p >= n:
            if self.shuffle:
                self.rebuild_order(keep=None)
            p = 0
        elif p < 0:
            p = n - 1
        self.pos_in_order = p
        self.load_current(autoplay=True, fade_ms=self.fade_ms("skip" if user else "auto"))

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play_index(self, i):
        if 0 <= i < len(self.files):
            self.pos_in_order = self.order.index(i)
            self.load_current(autoplay=True, fade_ms=self.fade_ms("skip"))

    # ------------------------------------------------------------ интернет-радио
    def stations(self):
        st = self.cfg.get("stations")
        if not isinstance(st, list) or not st:
            st = [dict(x) for x in radiomod.DEFAULT_STATIONS]
            self.cfg["stations"] = st
        return st

    def station_name(self, i=None):
        st = self.stations()
        i = self.current_index() if i is None else i
        return st[i]["name"] if i is not None and 0 <= i < len(st) else ""

    def play_radio(self, index=None, autoplay=True):
        """Перейти в режим радио: «плейлист» — список станций."""
        st = self.stations()
        if not self.radio:
            self.remember_folder_state()
            self.cfg["before_radio"] = self.current_folder()
        self.radio = True
        self.cfg["radio"] = True
        self.files = [x["url"] for x in st]
        self.order = list(range(len(self.files)))
        if index is None:
            index = int(self.cfg.get("radio_index", 0))
        self.pos_in_order = max(0, min(len(self.files) - 1, index))
        self.load_current(autoplay=autoplay, fade_ms=self.fade_ms("skip"))
        if self.playlist_panel.isVisible():
            self.playlist_panel.set_tracks(self.files, self.current_index(), [x["name"] for x in st])
        self.save_later()

    def leave_radio(self):
        back = self.cfg.get("before_radio")
        self.radiometa.stop()
        if back and os.path.isdir(back):
            self.load_sources([back], autoplay=self.is_playing())
        else:
            self.radio = False
            self.cfg["radio"] = False
            self.player.stop()
            self.files, self.order = [], []
            self.flash("ВЫБЕРИ ПАПКУ")
        self.save_later()

    def load_station(self, autoplay=True, fade_ms=0):
        i = self.current_index()
        url = self.files[i]
        self.station = self.station_name(i)
        self.cfg["radio_index"] = i
        self.title, self.artist, self.album = self.station, "", "📻 Радио"
        self._cover, self._np_cover = None, ("", "")
        self.lyrics = None
        self.ab_a = self.ab_b = None
        self._listened, self._play_counted = 0.0, False
        self.loaded_path = url
        self.pending_seek = None
        self.player.load(QUrl(url), autoplay=autoplay, fade_ms=fade_ms, gain=1.0)
        self.radiometa.start(url)
        self._radio_retry = 0
        if autoplay:
            self._announce_until = time.monotonic() + 3.0
        self.flash("📻 " + self.station, 2.5)
        if self.playlist_panel.isVisible():
            self.playlist_panel.set_current(i)
        self.update_tray_tip()
        self.sys_track_timer.start()
        self.refresh()

    def on_radio_title(self, title, icy_name):
        """Песня из потока: «Исполнитель - Песня» → на экран, в Windows и Discord."""
        if not self.radio:
            return
        a, t = radiomod.split_title(title)
        self.artist, self.title = (a, t) if t else ("", self.station)
        self._listened, self._play_counted = 0.0, False
        self._announce_until = time.monotonic() + 3.0
        self.update_tray_tip()
        self.sys_track_timer.start()
        self.light(2.0)
        self.refresh()

    def radio_reconnect(self):
        if not self.radio:
            return
        self._radio_retry += 1
        if self._radio_retry > 6:
            self.flash("СТАНЦИЯ НЕ ОТВЕЧАЕТ", 3.0)
            return
        self.flash("📻 ПЕРЕПОДКЛЮЧАЮСЬ…", 2.0)
        retry = self._radio_retry
        self.player.load(QUrl(self.current_path()), autoplay=True, fade_ms=0)
        self._radio_retry = retry

    def add_station(self):
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(None, "Добавить станцию", "Ссылка на поток (http/https):")
        url = (url or "").strip()
        if not ok or not url:
            return
        if not radiomod.is_url(url):
            self.flash("НУЖНА ССЫЛКА HTTP")
            return
        name, ok = QInputDialog.getText(None, "Добавить станцию", "Название:", text=url.split("/")[2])
        st = self.stations()
        st.append({"name": (name or url).strip(), "url": url})
        self.play_radio(len(st) - 1)

    def import_stations(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(None, "Плейлист радио", "",
                                                  "Плейлисты (*.m3u *.m3u8 *.pls);;Все файлы (*.*)")
        if not path:
            return
        new = radiomod.parse_playlist_file(path)
        if not new:
            self.flash("В ФАЙЛЕ НЕТ СТАНЦИЙ", 2.5)
            return
        st = self.stations()
        known = {x["url"] for x in st}
        added = [x for x in new if x["url"] not in known]
        st.extend(added)
        first = next((i for i, x in enumerate(st) if x["url"] == new[0]["url"]), 0)
        self.play_radio(first)
        self.flash(f"📻 ДОБАВЛЕНО: {len(added)}", 2.5)

    def remove_station(self):
        st = self.stations()
        i = self.current_index() if self.radio else None
        if i is None or not (0 <= i < len(st)) or len(st) <= 1:
            return
        st.pop(i)
        self.play_radio(min(i, len(st) - 1), autoplay=self.is_playing())

    # ------------------------------------------------------------ скачать в плеер (Doulander)
    def doulander_dir(self, ask=True):
        d = dlmod.find_doulander(self.cfg.get("doulander_dir"))
        if d:
            self.cfg["doulander_dir"] = d
            return d
        if not ask:
            return None
        QMessageBox.information(None, "Doulander не найден",
                                "Не нашёл Doulander (Downloader) в обычном месте.\n"
                                "Укажи его Downloader.exe — плеер возьмёт оттуда yt-dlp, ffmpeg и настройки.")
        return self.choose_doulander()

    def choose_doulander(self):
        path, _ = QFileDialog.getOpenFileName(None, "Где Doulander (Downloader.exe)?",
                                              self.cfg.get("doulander_dir") or "",
                                              "Downloader.exe (*.exe);;Все файлы (*.*)")
        if not path:
            return None
        d = dlmod.find_doulander(path)
        if not d:
            QMessageBox.warning(None, "Doulander", "Рядом с этим файлом нет yt-dlp.\n"
                                "Открой Doulander и нажми «Обновить компоненты», потом попробуй снова.")
            return None
        self.cfg["doulander_dir"] = d
        self.save_later()
        self.flash("DOULANDER НАЙДЕН", 2.0)
        return d

    def download_target(self):
        """Куда качать: текущая папка; если её нет (радио, избранное) — последняя
        папка загрузок или папка загрузок Doulander."""
        folder = None if self.radio or self.cfg.get("fav_mode") else self.current_folder()
        if not folder and self.files and not self.radio:
            cur = self.current_path()
            folder = os.path.dirname(cur) if cur and os.path.isfile(cur) else None
        if not folder:
            folder = self.cfg.get("download_dir")
        if not folder:
            d = self.doulander_dir(ask=False)
            folder = (dlmod.read_settings(d).get("folder") if d else None) or \
                os.path.join(os.path.expanduser("~"), "Music")
        return npath(folder)

    def download_dialog(self, url=None):
        d = self.doulander_dir()
        if not d:
            return
        target = self.download_target()
        if not url:
            from PySide6.QtWidgets import QInputDialog
            clip = (QApplication.clipboard().text() or "").strip()
            pre = clip if dlmod.is_url(clip) and "\n" not in clip else ""
            url, ok = QInputDialog.getText(
                None, "⬇ Скачать в плеер",
                "Ссылка (YouTube и др.) или «Исполнитель — песня»:\n"
                f"сохраню в: {target}", text=pre)
            url = (url or "").strip()
            if not ok or not url:
                return
        self.start_download(url, target)

    def start_download(self, url, target=None):
        d = self.doulander_dir()
        if not d:
            return
        target = target or self.download_target()
        self.cfg["download_dir"] = target
        n = self.dl.add(d, url, target)
        self.flash(f"⬇ В ОЧЕРЕДИ: {n + 1}" if n else "⬇ СКАЧИВАЮ…", 2.0)

    def on_dl_started(self, url):
        self.dl_pct, self.dl_title, self._dl_flash = 0.0, "", -100
        self.refresh_screen()

    def on_dl_titled(self, title):
        self.dl_title = title
        self.flash(f"⬇ {title}", 2.5)

    def on_dl_progress(self, pct):
        self.dl_pct = pct
        if pct - self._dl_flash >= 25 and pct < 100:      # на экране — каждые 25 %
            self._dl_flash = pct
            self.flash(f"⬇ {int(pct)}%  {self.dl_title}", 1.6)
        else:
            self.refresh_screen()
        self.update_tray_tip()

    def on_dl_finished(self, path, folder, err):
        self.dl_pct = None
        if err or not path:
            self.flash("⬇ НЕ СКАЧАЛОСЬ", 3.0)
            if err != "отменено":
                self.show_message("Не скачалось", err or "файл не появился")
            self.update_tray_tip()
            return
        name = display_name(path)
        if (not self.radio and not self.cfg.get("fav_mode") and self.files
                and (pkey(folder) == pkey(self.current_folder() or "")
                     or (self.current_folder() is None and len(self.cfg.get("sources") or []) > 0))):
            self.add_to_playlist(path)
            if not self.is_playing():
                self.play_index(next(i for i, f in enumerate(self.files) if pkey(f) == pkey(path)))
            self.flash(f"⬇ ГОТОВО: {name}", 3.0)
        else:                                            # радио / пусто — открыть папку с новым треком
            self.load_sources([folder], start_path=path, autoplay=True, flat=True)
            self.flash(f"⬇ ГОТОВО: {name}", 3.0)
        if self.playlist_panel.isVisible():
            self.playlist_panel.set_tracks(self.files, self.current_index())
        self.update_tray_tip()

    def show_message(self, title, text):
        if hasattr(self, "tray") and self.tray.isVisible():
            self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, 8000)
        else:
            QMessageBox.warning(None, title, text)

    def add_to_playlist(self, path):
        path = npath(path)
        if any(pkey(f) == pkey(path) for f in self.files):
            return
        cur = self.current_index()
        self.files.append(path)
        new_i = len(self.files) - 1
        if self.shuffle:                                 # в перемешанном — сыграет следующим
            self.order.insert(self.pos_in_order + 1, new_i)
        else:
            self.order.append(new_i)
        src = self.cfg.get("sources") or []
        if not any(os.path.isdir(x) and pkey(os.path.dirname(path)).startswith(pkey(x)) for x in src):
            src.append(path)
        if cur is not None and not self.shuffle:
            self.pos_in_order = self.order.index(cur)
        self.save_later()

    # ------------------------------------------------------------ автообновление
    def build_update_menu(self, um):
        um.clear()
        um.addAction(f"Сборка {self.build}" if self.build else "Запущено из исходников").setEnabled(False)
        if self.upd_ready:
            um.addAction(f"⬆  Установить сборку {self.upd_ready[1]} сейчас", lambda: self.install_update())
        elif self.upd_state.startswith("downloading"):
            um.addAction(f"Скачиваю сборку… {self.upd_state.split()[-1]}").setEnabled(False)
        else:
            um.addAction("Проверить сейчас", lambda: self.check_update(manual=True))
        a = um.addAction("Обновляться автоматически", self.toggle_auto_update)
        a.setCheckable(True)
        a.setChecked(self.cfg.get("auto_update", True))
        um.addAction("Что нового в последних сборках", lambda: self.show_whats_new(max(0, self.build - 5),
                                                                                 manual=True))
        um.addAction("Все сборки на GitHub…",
                     lambda: QDesktopServices.openUrl(QUrl(f"https://github.com/{UPDATE_REPO}/releases")))

    def check_whats_new(self):
        """Обновились (сами или вручную) — показать, что изменилось с прошлой сборки."""
        if not updater.can_update():
            return
        last = int(self.cfg.get("last_seen_build") or 0)
        self.cfg["last_seen_build"] = self.build
        self.save_later()
        if 0 < last < self.build:
            self.show_whats_new(last)

    def show_whats_new(self, after, manual=False):
        emit = self.upd.event.emit

        def work():
            try:
                emit("notes", (updater.notes_since(UPDATE_REPO, after, self.build), manual))
            except Exception as e:
                if manual:
                    emit("error", f"Не удалось загрузить список изменений: {e}")
        threading.Thread(target=work, daemon=True).start()

    def toggle_auto_update(self):
        self.cfg["auto_update"] = not self.cfg.get("auto_update", True)
        self.flash("АВТООБНОВЛЕНИЕ " + ("ВКЛ" if self.cfg["auto_update"] else "ВЫКЛ"))
        self.save_later()

    def update_tick(self):
        # скачанная сборка ставится, когда музыка не играет хотя бы 2 минуты
        if self.upd_ready and self.cfg.get("auto_update", True):
            if self.is_playing() or self.__dict__.get("dl_pct") is not None:
                self.upd_idle_since = None
            else:
                self.upd_idle_since = self.upd_idle_since or time.monotonic()
                if time.monotonic() - self.upd_idle_since >= 120:
                    self.install_update()
                    return
        if (updater.can_update() and self.cfg.get("auto_update", True) and not self.upd_ready
                and updater.due(self.cfg.get("update_checked"), hours=6)):
            self.check_update(manual=False)

    def check_update(self, manual=False):
        if not updater.can_update():
            if manual:
                QMessageBox.information(None, APP_TITLE, "Обновление работает в собранной программе "
                                                         "из релизов GitHub.")
            return
        if self.upd_state in ("checking",) or self.upd_state.startswith("downloading"):
            return
        self.upd_state, self.upd_manual = "checking", manual
        if manual:
            self.flash("ПРОВЕРЯЮ ОБНОВЛЕНИЯ…")
        threading.Thread(target=self._update_worker, args=(manual,), daemon=True).start()

    def _update_worker(self, manual):
        emit = self.upd.event.emit
        try:
            rel = updater.latest(UPDATE_REPO, UPDATE_ASSET)
            self.cfg["update_checked"] = time.time()
            if not rel or rel["build"] <= self.build:
                emit("latest", None)
                return
            if not manual and not self.cfg.get("auto_update", True):
                emit("idle", None)
                return
            emit("found", rel["build"])
            work = updater.work_dir("MP3Player")
            zpath = os.path.join(work, f"MP3Player-{rel['build']}.zip")
            last = [-10]

            def prog(p):
                if p - last[0] >= 5:
                    last[0] = p
                    emit("progress", p)
            updater.download(rel["url"], zpath, rel["size"], prog)
            new_dir = updater.unpack_folder(zpath, "MP3Player", "MP3Player.exe")
            try:
                os.remove(zpath)
            except OSError:
                pass
            emit("ready", (new_dir, rel["build"]))
        except Exception as e:
            emit("error", f"{type(e).__name__}: {e}")

    def on_update_event(self, kind, data):
        manual = self.upd_manual
        if kind == "latest":
            self.upd_state = ""
            if manual:
                self.flash(f"ПОСЛЕДНЯЯ СБОРКА: {self.build}", 2.5)
        elif kind == "idle":
            self.upd_state = ""
        elif kind == "found":
            self.upd_state = "downloading 0%"
            if manual:
                self.flash(f"⬆ СКАЧИВАЮ СБОРКУ {data}", 2.5)
        elif kind == "progress":
            self.upd_state = f"downloading {int(data)}%"
        elif kind == "ready":
            self.upd_state, self.upd_ready = "ready", data
            if manual:
                self.install_update()
            elif hasattr(self, "tray"):
                self.tray.showMessage(APP_TITLE, f"Скачана сборка {data[1]}. Поставлю, когда музыка "
                                                 "не будет играть, или при выходе.",
                                      QSystemTrayIcon.MessageIcon.Information, 6000)
        elif kind == "notes":
            notes, manual = data
            if not notes:
                if manual:
                    self.flash("СПИСОК ИЗМЕНЕНИЙ ПУСТ", 2.0)
                return
            if self.news_panel is None:
                self.news_panel = WhatsNewPanel()
            title = "ЧТО НОВОГО" if manual else f"ОБНОВЛЕНО ДО СБОРКИ {self.build}"
            self.news_panel.show_notes(title, notes, self.visual_rect(),
                                       f"https://github.com/{UPDATE_REPO}/releases")
            return
        elif kind == "error":
            self.upd_state = ""
            if manual:
                QMessageBox.warning(None, APP_TITLE, f"Не удалось обновиться:\n{data}")

    def install_update(self, restart=True):
        if not self.upd_ready:
            return False
        new_dir, build = self.upd_ready
        self.upd_ready = None
        try:
            self.remember_position()
            self.remember_folder_state()
            self.save_cfg()
            updater.apply_folder(new_dir, "MP3Player", "MP3Player.exe",
                                 restart_args=["--updated"], restart=restart)
        except Exception as e:
            QMessageBox.warning(None, APP_TITLE, f"Не удалось обновиться:\n{e}")
            return False
        if restart:
            self.flash(f"⬆ ОБНОВЛЯЮСЬ ДО {build}…", 3.0)
            self.bye_t0 = time.monotonic()             # без прощальной анимации — сразу
            QTimer.singleShot(400, self._quit_now)
        return True

    def build_radio_menu(self):
        m = self.radio_menu
        m.clear()
        if self.radio:
            m.addAction("↩  Вернуться к музыке", self.leave_radio)
        grp = QActionGroup(m)
        cur = self.current_index() if self.radio else None
        for i, x in enumerate(self.stations()):
            a = m.addAction(x["name"], lambda _=False, i=i: self.play_radio(i))
            a.setToolTip(x["url"])
            a.setCheckable(True)
            a.setChecked(i == cur)
            grp.addAction(a)
        m.addSeparator()
        m.addAction("➕  Добавить станцию по ссылке…", self.add_station)
        m.addAction("📂  Открыть .m3u / .pls…   (или перетащи на плеер)", lambda: self.import_stations())
        if self.radio:
            m.addAction("✖  Убрать эту станцию", self.remove_station)

    # ------------------------------------------------------------ избранное
    # ------------------------------------------------------------ Music → Discord
    def write_nowplaying(self, force=False, closing=False):
        """Раз в 2 с (и сразу при смене трека/паузы) пишем, что играет, —
        Music → Discord показывает это в статусе как «MP3 Плеер»."""
        if not self.cfg.get("discord_link", True) and not closing:
            return
        path = self.loaded_path
        if closing or not path:
            state = "closed" if closing else "stopped"
        else:
            state = "playing" if self.is_playing() else "paused"
        pos = self.player.position() if path else 0
        lyric, has_ly = None, bool(self.lyrics)
        if self.lyrics:
            i = self.lyrics.index_at(pos)
            lyric = self.lyrics.lines[i] if i >= 0 else ""
        ab = (f"{fmt_time(self.ab_a)}–{fmt_time(self.ab_b)}" if self.ab_b is not None else "")
        snap = (state, path, self.title, self.artist, lyric, self.is_fav(path), ab, self.skin_id,
                self._np_cover[1])
        now = time.time()
        last_snap, last_t = self._np_last
        if not force and snap == last_snap and now - last_t < 2.0:
            return
        data = {
            "v": 1, "app": "MP3 Плеер", "pid": os.getpid(), "updated": now, "state": state,
            "title": self.title, "artist": self.artist, "album": self.album, "path": path or "",
            "position": round(pos / 1000.0, 2), "length": round(max(0, self.player.duration()) / 1000.0, 2),
            "lyric": lyric, "has_lyrics": has_ly, "fav": self.is_fav(path), "ab": ab,
            "skin": self.skin_meta.get("name", self.skin_id),
            "cover_file": self._np_cover[0], "cover_hash": self._np_cover[1],
        }
        try:
            os.makedirs(os.path.dirname(self.np_path), exist_ok=True)
            tmp = self.np_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.np_path)
            self._np_last = (snap, now)
        except Exception as e:
            print("nowplaying:", e)

    def save_discord_cover(self):
        """Обложку из тегов файла — в JPEG рядом с nowplaying.json (Music → Discord
        загрузит её на хостинг: Discord берёт картинки только по ссылке)."""
        img = self._cover
        if img is None or img.isNull():
            self._np_cover = ("", "")
            return
        try:
            import hashlib
            if img.width() > 512 or img.height() > 512:
                img = img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            data, ext = b"", "jpg"
            for fmt, ext in (("JPG", "jpg"), ("PNG", "png")):     # нет JPEG-модуля — пишем PNG
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                ok = img.convertToFormat(img.Format.Format_RGB32).save(buf, fmt, 90)
                data = bytes(buf.data())
                if ok and len(data) > 200:
                    break
            if len(data) <= 200:
                self._np_cover = ("", "")
                return
            h = hashlib.md5(data).hexdigest()
            if h == self._np_cover[1]:
                return
            path = os.path.join(os.path.dirname(self.np_path), f"discord_cover_{h[:8]}.{ext}")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.isfile(path):
                with open(path, "wb") as f:
                    f.write(data)
            old = self._np_cover[0]
            self._np_cover = (path, h)
            if old and old != path:                  # старую картинку убрать
                try:
                    os.remove(old)
                except OSError:
                    pass
        except Exception as e:
            print("discord cover:", e)

    def toggle_discord_link(self):
        on = not self.cfg.get("discord_link", True)
        self.cfg["discord_link"] = on
        if not on:
            self.write_nowplaying(closing=True)
        self.flash("DISCORD: " + ("ВКЛ" if on else "ВЫКЛ"))
        self.save_later()

    def favorites(self):
        return self.cfg.setdefault("favorites", [])

    def is_fav(self, path):
        if not path:
            return False
        k = pkey(path)
        return any(pkey(f) == k for f in self.favorites())

    def toggle_favorite(self):
        path = self.current_path()
        if not path:
            return
        if self.radio:
            self.flash("♥ — ТОЛЬКО ДЛЯ ТРЕКОВ")
            return
        favs = self.favorites()
        if self.is_fav(path):
            favs[:] = [f for f in favs if pkey(f) != pkey(path)]
            self.flash("♡ УБРАНО ИЗ ИЗБРАННОГО")
        else:
            favs.append(path)
            self.flash("♥ В ИЗБРАННОМ")
        self.sound("click_down")
        self.light()
        self.playlist_panel.set_favs(favs)
        self.save_later()

    def play_favorites(self):
        """Все ♥-треки из всех папок одним списком; повторно — назад к папке."""
        if self.cfg.get("fav_mode"):
            back = self.cfg.get("before_fav")
            if back and os.path.isdir(back):
                self.load_sources([back], autoplay=self.is_playing())
            else:
                self.flash("ВЫБЕРИ ПАПКУ")
            return
        favs = [f for f in self.favorites() if os.path.isfile(f)]
        if not favs:
            self.flash("НЕТ ИЗБРАННЫХ — ДВОЙНОЙ КЛИК ПО ЭКРАНУ", 3.0)
            return
        self.cfg["before_fav"] = self.current_folder()
        self.load_sources(favs, autoplay=True, fav=True)

    # ------------------------------------------------------------ итоги
    def stats_pages(self):
        pages = ["week", "tracks:0"]
        if len(self.stats.top_tracks(6)) > 3:
            pages.append("tracks:3")
        pages += ["artists", "totals"]
        return pages

    def open_stats(self):
        if self.cap_open:
            self.cap_open = False
        self.stats_page = 0
        self.playlist_panel.hide()
        self.light(8.0)
        self.update()

    def stats_move(self, d):
        n = len(self.stats_pages())
        self.stats_page = (self.stats_page + d) % n
        self.light(8.0)
        self.refresh_screen()

    def close_stats(self):
        self.stats_page = None
        self.refresh_screen()

    def reset_stats(self):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(None, APP_TITLE, "Сбросить всю статистику прослушивания?") == \
                QMessageBox.StandardButton.Yes:
            self.stats.data = {"tracks": {}, "days": {}, "since": time.strftime("%Y-%m-%d")}
            self.stats.dirty = True
            self.stats.save()
            self.flash("ИТОГИ СБРОШЕНЫ")

    def paint_stats(self, p, L, T, now):
        m = 16
        pages = self.stats_pages()
        self.stats_page %= len(pages)
        page = pages[self.stats_page]
        f_h = self.lcd_font(22)
        y1 = 36
        pg = f"{self.stats_page + 1}/{len(pages)}"

        def header(left, right="", short=None):
            fm = QFontMetricsF(f_h)
            full = (right + "  " if right else "") + pg
            # узкий экран: короткий заголовок, потом без номера страницы
            for lt, rt in ((left, full), (short or left, full), (short or left, right or pg)):
                if fm.horizontalAdvance(lt) + fm.horizontalAdvance(rt) + 14 <= L - 2 * m:
                    break
            rw = fm.horizontalAdvance(rt)
            self.ink_text(p, L - m - rw, y1, rt, f_h)
            el = fm.elidedText(lt, Qt.TextElideMode.ElideRight, L - 2 * m - rw - 12)
            self.ink_text(p, m, y1, el, f_h)

        def rows(items):
            """items: [(левый текст, правый текст)], до 3 строк."""
            for r, (left, right) in enumerate(items[:3]):
                top = 44 + r * 34
                base = top + 24
                fs = 26
                f = self.lcd_font(fs)
                rw = QFontMetricsF(f).horizontalAdvance(right)
                avail = L - 2 * m - rw - 16
                el = QFontMetricsF(f).elidedText(left, Qt.TextElideMode.ElideRight, max(40.0, avail))
                self.ink_text(p, m, base, el, f)
                self.ink_text(p, L - m - rw, base, right, f)

        if page == "week":
            week = self.stats.week()
            total = sum(sec for _, sec, _ in week)
            header("ИТОГИ НЕДЕЛИ", fmt_dur(total), "НЕДЕЛЯ")
            if total <= 0:
                self.ink_text(p, m, 100, "ПОКА ПУСТО — ВКЛЮЧИ МУЗЫКУ", self.lcd_font(22))
                return
            top_v, bot_v = 46.0, 134.0
            segs, gap = 8, 3.0
            seg_h = ((bot_v - top_v) - gap * (segs - 1)) / segs
            colw = (L - 2 * m) / 7
            mx = max(sec for _, sec, _ in week) or 1
            f_d = self.lcd_font(16)
            for i, (name, sec, is_today) in enumerate(week):
                cx = m + colw * (i + 0.5)
                bw = min(46.0, colw * 0.55)
                on = int(round(segs * sec / mx)) if sec > 0 else 0
                if sec > 0:
                    on = max(1, on)
                for j in range(segs):
                    y = bot_v - (j + 1) * seg_h - j * gap
                    c = QColor(LCD_INK) if j < on else QColor(0, 0, 0, 16)
                    if is_today and j < on and int(now * 2) % 2:
                        c.setAlpha(170)                 # сегодняшний столбик мерцает
                    p.fillRect(QRectF(cx - bw / 2, y, bw, seg_h), c)
                lbl = f"[{name}]" if is_today else name
                lw = QFontMetricsF(f_d).horizontalAdvance(lbl)
                self.ink_text(p, cx - lw / 2, 155, lbl, f_d)
        elif page.startswith("tracks"):
            start = int(page.split(":")[1])
            top = self.stats.top_tracks(6)[start:start + 3]
            header(f"ТОП ТРЕКОВ {start + 1}–{start + 3}", "", f"ТОП {start + 1}–{start + 3}")
            if not top:
                self.ink_text(p, m, 100, "ПОКА ПУСТО", self.lcd_font(22))
            rows([(f"{start + k + 1}. {t}" + (f" — {a}" if a else ""), f"×{n}")
                  for k, (t, a, n, _sec) in enumerate(top)])
        elif page == "artists":
            header("ТОП ИСПОЛНИТЕЛЕЙ", "", "АРТИСТЫ")
            top = self.stats.top_artists(3)
            if not top:
                self.ink_text(p, m, 100, "ПОКА НЕКОГО ОТМЕТИТЬ", self.lcd_font(22))
            rows([(f"{k + 1}. {a}", fmt_dur(sec)) for k, (a, _n, sec) in enumerate(top)])
        else:
            t = self.stats.totals()
            since = t["since"][8:10] + "." + t["since"][5:7] if len(t["since"]) >= 10 else ""
            header("ВСЕГО", f"С {since}" if since else "")
            rows([("СЕГОДНЯ", fmt_dur(t["today"])),
                  ("ЗА ВСЁ ВРЕМЯ", fmt_dur(t["all_sec"])),
                  ("ПРОСЛУШИВАНИЙ", str(t["plays"]))])

    def open_playlist(self):
        if not self.files:
            self.open_folder()
            return
        if self.playlist_panel.isVisible():
            self.playlist_panel.hide()
            return
        names = [x["name"] for x in self.stations()] if self.radio else None
        self.playlist_panel.set_tracks(self.files, self.current_index(), names)
        self.playlist_panel.set_favs(self.favorites())
        self.playlist_panel.open_near(self.visual_rect())

    def toggle_play(self):
        if not self.order:
            self.open_folder()
            return
        if self.is_playing():
            self.player.pause(self.fade_ms("pause"))
            self.remember_position()
        else:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia:
                self.load_current(autoplay=True)
            else:
                self.player.play(self.fade_ms("pause"))
        self.save_later()

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self.rebuild_order(keep=self.current_index())
        self.flash("ПЕРЕМЕШАТЬ: ВКЛ" if self.shuffle else "ПЕРЕМЕШАТЬ: ВЫКЛ")
        if hasattr(self, "act_shuffle"):
            self.act_shuffle.setChecked(self.shuffle)
        self.save_later()

    def set_repeat(self, mode):
        self.repeat = mode
        self.flash({"all": "ПОВТОР: ВСЕ", "one": "ПОВТОР: ОДИН", "off": "ПОВТОР: ВЫКЛ"}[mode])
        self.save_later()

    def change_volume(self, d):
        v = min(1.0, max(0.0, round(self.player.volume() + d, 2)))
        self.player.setVolume(v)
        self.flash(f"ГРОМКОСТЬ {int(round(v * 100))}%")
        self.save_later()

    def seek_by(self, ms):
        if self.player.duration() > 0:
            self.player.setPosition(max(0, min(self.player.duration() - 500,
                                               self.player.position() + ms)))

    # ------------------------------------------------------------ player signals
    def on_status(self, st):
        S = QMediaPlayer.MediaStatus
        if st in (S.LoadedMedia, S.BufferedMedia):
            self.error_streak = 0
            if self.radio and st == S.BufferedMedia:
                self._radio_retry = 0
            if self.pending_seek:
                self.player.setPosition(int(self.pending_seek))
                self.pending_seek = None
        elif st == S.EndOfMedia and self.radio:
            QTimer.singleShot(2000, self.radio_reconnect)    # поток оборвался
        elif st == S.EndOfMedia and self.ab_b is not None:
            self.player.setPosition(self.ab_a)
            self.player.play()
        elif st == S.EndOfMedia:
            (self.cfg.get("resume") or {}).pop(self.loaded_path, None)   # дослушано
            if self.repeat == "one":
                self.player.setPosition(0)
                self.player.play()
            elif self.repeat == "off" and self.pos_in_order >= len(self.order) - 1:
                self.player.stop()
            else:
                self.step(+1, user=False)

    # ------------------------------------------------------------ повтор A–B
    def ab_cycle(self):
        """A → B → сброс (как на настоящих плеерах)."""
        if self.ab_a is None:
            self.ab_set_a()
        elif self.ab_b is None:
            self.ab_set_b()
        else:
            self.ab_clear()

    def ab_set_a(self):
        if self.player.duration() <= 0:
            return
        self.ab_a, self.ab_b = int(self.player.position()), None
        self.sound("click_down")
        self.flash(f"ТОЧКА A {fmt_time(self.ab_a)}", 2.2)

    def ab_set_b(self):
        if self.ab_a is None:
            self.ab_set_a()
            return
        b = int(self.player.position())
        if b < self.ab_a + 500:
            self.flash("B ДОЛЖНА БЫТЬ ПОЗЖЕ A")
            return
        self.ab_b = b
        self.sound("click_down")
        self.flash(f"A–B {fmt_time(self.ab_a)}–{fmt_time(b)}", 2.5)
        self.player.setPosition(self.ab_a)

    def ab_clear(self):
        if self.ab_a is None and self.ab_b is None:
            return
        self.ab_a = self.ab_b = None
        self.sound("click_down")
        self.flash("A–B СБРОШЕН")

    def ab_check(self):
        if self.ab_b is not None and self.is_playing() and self.player.position() >= self.ab_b:
            self.player.setPosition(self.ab_a)

    def on_near_end(self):
        """Скоро конец трека — начать плавный переход к следующему."""
        if self.ab_b is not None:              # крутим кусок — к следующему не переходим
            return
        if self.repeat == "one":
            return
        if self.repeat == "off" and self.pos_in_order >= len(self.order) - 1:
            return
        self.step(+1, user=False)

    def on_error(self, _err, msg=""):
        if self.radio:                              # радио: не листать дальше, а переподключиться
            QTimer.singleShot(3000, self.radio_reconnect)
            return
        self.error_streak += 1
        self.flash("ОШИБКА ФАЙЛА")
        if self.error_streak < max(1, len(self.order)):
            QTimer.singleShot(400, lambda: self.step(+1, user=False))
        else:
            self.player.stop()

    def on_meta(self):
        self._meta_lyrics_retry = True
        md = self.player.metaData()
        t = md.stringValue(QMediaMetaData.Key.Title)
        a = (md.stringValue(QMediaMetaData.Key.ContributingArtist) or
             md.stringValue(QMediaMetaData.Key.AlbumArtist))
        if t:
            self.title = t.strip()
        if a:
            self.artist = a.strip()
        al = md.stringValue(QMediaMetaData.Key.AlbumTitle)
        if al:
            self.album = al.strip()
        for key in (QMediaMetaData.Key.ThumbnailImage, QMediaMetaData.Key.CoverArtImage):
            img = md.value(key)
            if img is not None and hasattr(img, "isNull") and not img.isNull():
                self._cover = img
                break
        # текст не нашёлся по имени файла — попробовать по тегам (название / исполнитель)
        if (self.cfg.get("lyrics", True) and self.lyrics is None and self.loaded_path
                and getattr(self, "_meta_lyrics_retry", False) and (t or a)):
            self._meta_lyrics_retry = False
            self.lyrics = self.load_lyrics(self.loaded_path, self.title, self.artist)
        if self.lyrics is None and self.loaded_path and not self.radio:
            self.lyr_timer.start()            # своего .lrc нет — поискать в интернете
        self.update_tray_tip()
        self.sys_track_timer.start()
        self.refresh()

    # ------------------------------------------------------------ Windows media
    def on_media_command(self, cmd):
        self.light()
        if cmd == "play_pause":
            self.toggle_play()
        elif cmd == "play":
            if not self.is_playing():
                self.toggle_play()
        elif cmd == "pause":
            if self.is_playing():
                self.player.pause(self.fade_ms("pause"))
                self.remember_position()
        elif cmd == "stop":
            self.remember_position()
            self.player.pause()
            self.player.setPosition(0)
        elif cmd == "next_folder":
            self.next_folder()
        elif cmd == "next":
            self.step(+1)
        elif cmd == "prev":
            self.step(-1)
        elif cmd == "vol_up":
            self.change_volume(0.05)
        elif cmd == "vol_down":
            self.change_volume(-0.05)
        elif cmd == "shuffle":
            self.toggle_shuffle()
        elif cmd == "toggle_window":
            target = self.mini if self.cfg.get("mini") else self
            if target.isVisible():
                target.hide()
            else:
                self.show_player()
        elif cmd == "radio":
            self.leave_radio() if self.radio else self.play_radio()
        elif cmd == "eq":
            self.open_eq()
        elif cmd == "download":
            self.download_dialog()
        elif cmd == "mini":
            self.set_mini(not self.cfg.get("mini"))
        elif cmd == "ab":
            self.ab_cycle()
        elif cmd == "fav":
            self.toggle_favorite()
        elif cmd == "stats":
            self.close_stats() if self.stats_page is not None else self.open_stats()
        elif cmd == "playlist":
            self.open_playlist()
        elif cmd.startswith("seek:"):
            self.player.setPosition(max(0, int(cmd[5:])))
            self.sync_timeline(force=True)

    def sync_system_status(self, *_):
        St = QMediaPlayer.PlaybackState
        st = self.player.playbackState()
        if not self.files:
            self.sysmedia.set_status("closed")
        elif st == St.PlayingState:
            self.sysmedia.set_status("playing")
        elif st == St.PausedState:
            self.sysmedia.set_status("paused")
        else:
            self.sysmedia.set_status("stopped" if self.player.position() == 0 else "paused")
        self.sync_timeline(force=True)

    def sync_timeline(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_tl < 5:
            return
        self._last_tl = now
        self.sysmedia.set_timeline(self.player.position(), self.player.duration())

    def push_system_track(self):
        if not self.current_path():
            return
        if self.cfg.get("discord_link", True):
            self.save_discord_cover()
        # всплывающая обложка — только когда трек сменился во время игры
        if (self.cfg.get("cover_popup", True) and self.isVisible()
                and time.monotonic() < self._announce_until):
            self.cover_popup.show_track(self._cover, self.title, self.artist, self.album,
                                        self.visual_rect(), self.on_desktop,
                                        lambda w: WinDesktop.apply(w, self.on_desktop))
        # «Сейчас играет» в углу экрана
        mode = self.cfg.get("toast", "hidden")
        if time.monotonic() < self._announce_until and (
                mode == "always" or (mode == "hidden" and not self.isVisible()
                                     and not self.mini.isVisible())):
            i = self.current_index()
            extra = f"{(i or 0) + 1} / {len(self.files)}" + (f"  ·  {self.album}" if self.album else "")
            self.toast.show_track(self._cover, self.title, self.artist, extra)
        if not self.sysmedia.ok:
            return
        cover = resource("icon.png")
        if self._cover is not None:
            try:
                self._cover_flip = not getattr(self, "_cover_flip", False)
                path = os.path.join(self.data_dir, "cover_%d.png" % self._cover_flip)
                img = self._cover
                if img.width() > 600:
                    img = img.scaledToWidth(600, Qt.TransformationMode.SmoothTransformation)
                if img.save(path, "PNG"):
                    cover = path
            except Exception as e:
                print("cover:", e)
        self.sysmedia.set_track(self.title, self.artist, self.album, cover)
        self.sync_system_status()

    # ------------------------------------------------------------ helpers UI
    def flash(self, text, sec=1.6):
        start = time.monotonic()
        if self.__dict__.get("boot_t0") is not None:     # не терять надпись за заставкой
            start = max(start, self.boot_t0 + BOOT_LEN)
        self.notice = (text, start + sec)
        self.light(3.0)
        self.refresh_screen()

    def light(self, sec=4.0):
        self.light_until = max(self.light_until, time.monotonic() + sec)

    def refresh(self):
        self.update()

    def nat(self, pt):
        """Точка окна → координаты исходной картинки плеера (с учётом поворота)."""
        inv, _ok = self.face_xf().inverted()
        return inv.map(QPointF(pt))

    def screen_widget_rect(self):
        return self.face_xf().mapRect(SCREEN).adjusted(-3, -3, 3, 3).toAlignedRect()

    def refresh_screen(self):
        if not self.isVisible():
            return
        if self.tilted():
            self.update()
            return
        self.update(self.screen_widget_rect())

    def button_at(self, pt):
        q = self.nat(pt)
        for name, r in BUTTONS.items():
            if r.adjusted(-HIT_PAD, -4, HIT_PAD, 4).contains(q):
                return name
        return None

    def lcd_geom(self):
        """(преобразование «раскладка текста → картинка», длина L, высота T)."""
        t = QTransform()
        if LCD_ROT == 90:
            k = SCREEN.width() / LCD_T
            t.translate(SCREEN.right(), SCREEN.top())
            t.rotate(90)
            t.scale(k, k)
            return t, SCREEN.height() / k, LCD_T
        k = SCREEN.height() / LCD_T
        t.translate(SCREEN.left(), SCREEN.top())
        t.scale(k, k)
        return t, SCREEN.width() / k, LCD_T

    def lcd_uv(self, pt):
        t, _L, _T = self.lcd_geom()
        inv, _ok = t.inverted()
        q = inv.map(self.nat(pt))
        return q.x(), q.y()

    def bar_span(self, L=None):
        if L is None:
            L = self.lcd_geom()[1]
        return LCD_M, L - LCD_M - VOL_W - 14

    def bar_frac(self, pt):
        """Доля трека по точке мыши, если точка на полоске прогресса (иначе None)."""
        if self.cap_open or not SCREEN.contains(self.nat(pt)):
            return None
        u, v = self.lcd_uv(pt)
        u0, u1 = self.bar_span()
        if BAR_V0 - 9 <= v <= BAR_V1 + 9 and u0 - 6 <= u <= u1 + 6:
            return min(1.0, max(0.0, (u - u0) / (u1 - u0)))
        return None

    def bar_frac_any(self, pt):
        u, _v = self.lcd_uv(pt)
        u0, u1 = self.bar_span()
        return min(1.0, max(0.0, (u - u0) / (u1 - u0)))

    def on_cap(self, pt):
        """Колпачок (надетый) или, если снят, — колпачок в воздухе и USB-штекер."""
        if CAP is None:
            return False
        q = self.nat(pt)
        if not self.cap_open:
            return 0 <= q.y() < CAP["bottom"] and 0 <= q.x() <= self.native_w
        plug = CAP["plug"]
        return q.y() < (plug.bottom() - 4 if plug else CAP["bottom"]) and -PAD_X <= q.x() <= self.native_w + PAD_X

    def on_bottom(self, pt):
        return FOLDER_ZONE is not None and FOLDER_ZONE.contains(self.nat(pt))

    def on_screen(self, pt):
        return SCREEN.contains(self.nat(pt))

    # ------------------------------------------------------------ звуки
    def sound(self, name):
        if not self.cfg.get("click_sound", True):
            return
        if self.cfg.get("sound_theme", "beep") == "beep":   # «писк»: на нажатие и включение
            name = {"click_down": "beep", "click_up": None, "boot": "beep"}.get(name, name)
        e = self.sfx.get(name) if name else None
        if e is not None:
            e.stop()
            e.play()

    def cycle_sfx_volume(self):
        vals = list(SFX_LEVELS.values())
        cur = float(self.cfg.get("sfx_volume", 0.5))
        i = min(range(len(vals)), key=lambda k: abs(vals[k] - cur))
        self.set_sfx_volume(vals[(i + 1) % len(vals)])

    def set_sfx_volume(self, v):
        self.cfg["sfx_volume"] = v
        for e in self.sfx.values():
            e.setVolume(v)
        self.save_later()
        self.sound("click_down")                  # сразу слышно, как стало

    def set_sound_theme(self, theme):
        self.cfg["sound_theme"] = theme
        self.save_later()
        self.sound("click_down")

    def open_sounds_folder(self):
        os.makedirs(self.sounds_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.sounds_dir))

    # ------------------------------------------------------------ колпачок и USB-режим
    def toggle_cap(self):
        self.cap_open = not self.cap_open
        self.light(6.0)
        if self.cap_open:
            self.sound("cap_off" if CAP is not None else "click_up")
            self.usb_sel = self.usb_top = 0
            self.playlist_panel.hide()
        self.update()

    def usb_items(self):
        """Пункты меню настроек: (название, значение или '', действие)."""
        names = list(THEMES)
        theme = self.cfg.get("theme", "ЗЕЛЁНАЯ")
        onoff = lambda key: "ВКЛ" if self.cfg.get(key, True) else "ВЫКЛ"
        xf = int(self.cfg.get("crossfade", 4000))
        auto = self.auto_scale_here()
        size_name = next((k for k, v in SIZES.items() if abs(auto * v - self.scale_f) < 0.011), None)
        sizes = list(SIZES)
        size_label = size_name.upper() if size_name else f"{round(self.scale_f * 100)}%"

        def cycle_theme():
            self.set_theme(names[(names.index(theme) + 1) % len(names)] if theme in names else names[0])

        def cycle_xf():
            vals = list(CROSSFADES.values())
            self.set_crossfade(vals[(vals.index(xf) + 1) % len(vals)] if xf in vals else 4000)

        def cycle_size():
            bigger = [auto * v for v in SIZES.values() if auto * v > self.scale_f + 0.011]
            self.set_scale(bigger[0] if bigger else auto * min(SIZES.values()))

        return [
            ("СКИН", self.skin_meta.get("name", self.skin_id).upper(), self.cycle_skin),
            ("ПОДСВЕТКА", theme, cycle_theme),
            ("ЗВУК КНОПОК", onoff("click_sound"), lambda: self.toggle_cfg("click_sound", "ЗВУК КНОПОК")),
            ("ЗВУК (EQ)", (self.cfg.get("eq_preset", "Плоский").upper()[:10]
                           if self.player.equalizer.active() else "ВЫКЛ"), self.cycle_eq_preset),
            ("ЭКВАЛАЙЗЕР", ("ВЫКЛ" if not self.cfg.get("eq", True) else
                            "СПЕКТР" if self.eq_real_on() else "ДЕКОР"), self.cycle_eq),
            ("ГРОМК. ЗВУКОВ", next((k.upper() for k, v in SFX_LEVELS.items()
                                    if abs(v - float(self.cfg.get("sfx_volume", 0.5))) < 0.01), "СРЕДНЕ"),
             self.cycle_sfx_volume),
            ("3D", "ВКЛ" if self.cfg.get("three_d", True) else "ВЫКЛ", self.toggle_3d),
            ("ЗВУКИ", "ПИСК" if self.cfg.get("sound_theme", "beep") == "beep" else "ЩЕЛЧКИ",
             lambda: self.set_sound_theme("classic" if self.cfg.get("sound_theme", "beep") == "beep" else "beep")),
            ("ТЕКСТ ПЕСНИ", onoff("lyrics"), self.toggle_lyrics),
            ("ЗАСТАВКА", onoff("boot_anim"), lambda: self.toggle_cfg("boot_anim", "ЗАСТАВКА")),
            ("ПЕРЕХОД", f"{xf // 1000} С" if xf else "ВЫКЛ", cycle_xf),
            ("ЗАТУХАНИЕ", onoff("fade_pause"), self.toggle_fade_pause),
            ("РОВНАЯ ГРОМК.", {"off": "ВЫКЛ", "spotify": "−14", "quiet": "−18"}.get(
                self.cfg.get("normalize", "spotify"), "−14"), self.cycle_normalize),
            ("ЗАКЛАДКИ", onoff("resume_long"), self.toggle_resume),
            ("ОБЛОЖКА", onoff("cover_popup"), self.toggle_cover_popup),
            ("РАЗМЕР", size_label, cycle_size),
            ("ГДЕ", "НА СТОЛЕ" if self.on_desktop else "ПОВЕРХ", lambda: self.set_mode(not self.on_desktop)),
            ("ИЗБРАННОЕ", ("НАЗАД" if self.cfg.get("fav_mode") else f"{len(self.favorites())} ♥"),
             self.play_favorites),
            ("РАДИО", "ВЫЙТИ" if self.radio else "→",
             lambda: (self.toggle_cap(), self.leave_radio() if self.radio else self.play_radio())),
            ("СКАЧАТЬ", f"{int(self.dl_pct)}%" if self.dl_pct is not None else "→",
             lambda: (self.toggle_cap(), self.download_dialog())),
            ("МИНИ-РЕЖИМ", "→", lambda: (self.toggle_cap(), self.set_mini(True))),
            ("ИТОГИ", "→", self.open_stats),
            ("DISCORD", "ВКЛ" if self.cfg.get("discord_link", True) else "ВЫКЛ", self.toggle_discord_link),
            ("ОТКРЫТЬ ПАПКУ", "→", self.open_folder),
            ("ПОКАЗАТЬ ЗАСТАВКУ", "→", lambda: (self.toggle_cap(), self.replay_boot())),
            ("НАДЕТЬ КОЛПАЧОК" if CAP is not None else "ВЫЙТИ", "⏏", self.toggle_cap),
        ]

    def usb_move(self, d):
        n = len(self.usb_items())
        self.usb_sel = (self.usb_sel + d) % n
        if self.usb_sel < self.usb_top:
            self.usb_top = self.usb_sel
        elif self.usb_sel >= self.usb_top + 3:
            self.usb_top = self.usb_sel - 2
        self.light(6.0)
        self.refresh_screen()

    def usb_activate(self):
        items = self.usb_items()
        if 0 <= self.usb_sel < len(items):
            items[self.usb_sel][2]()
        self.light(6.0)
        self.update()

    def set_theme(self, name):
        self.cfg["theme"] = name
        apply_theme(name)
        self.light(4.0)
        self.update()
        self.save_later()

    def paint_usb(self, p, L, T, now):
        m = 16
        f_h = self.lcd_font(22)
        f_i = self.lcd_font(28)
        # заголовок: значок USB + стрелки «обмена данными»
        y1 = 36
        arrows = "⇅" if int(now * 2.5) % 2 else "⇵"
        head = f"USB {arrows}  НАСТРОЙКИ" if CAP is not None else "⚙  НАСТРОЙКИ"
        if QFontMetricsF(f_h).horizontalAdvance(head) > L - 2 * m - 60:
            head = "⚙ МЕНЮ"
        self.ink_text(p, m, y1, head, f_h)
        items = self.usb_items()
        pos = f"{self.usb_sel + 1}/{len(items)}"
        w = QFontMetricsF(f_h).horizontalAdvance(pos)
        self.ink_text(p, L - m - w, y1, pos, f_h)
        # три видимых пункта
        row_h = 34
        for row in range(3):
            i = self.usb_top + row
            if i >= len(items):
                break
            name, val, _ = items[i]
            top = 44 + row * row_h
            # не влезает (узкий экран) — уменьшить шрифт строки
            fs = 28
            while fs > 14 and (QFontMetricsF(self.lcd_font(fs)).horizontalAdvance("▶ " + name + "  " + val)
                               > L - 2 * m - 12):
                fs -= 2
            f_i = self.lcd_font(fs)
            r = QRectF(m - 4, top, L - 2 * m + 8, row_h - 4)
            base = top + row_h - 10
            if i == self.usb_sel:                    # выбранный — инверсия
                p.fillRect(r, LCD_INK)
                bg = QColor(LCD_BG_LIT)
                p.setFont(f_i)
                p.setPen(bg)
                p.drawText(QPointF(m + 6, base), "▶ " + name)
                vw = QFontMetricsF(f_i).horizontalAdvance(val)
                p.drawText(QPointF(L - m - 6 - vw, base), val)
            else:
                self.ink_text(p, m + 6, base, "  " + name, f_i)
                vw = QFontMetricsF(f_i).horizontalAdvance(val)
                self.ink_text(p, L - m - 6 - vw, base, val, f_i)

    def update_tray_tip(self):
        if hasattr(self, "tray"):
            t = f"{self.artist} — {self.title}" if self.artist else (self.title or APP_TITLE)
            if self.__dict__.get("dl_pct") is not None:
                t = f"⬇ {int(self.dl_pct)}% {self.dl_title}\n" + t
            self.tray.setToolTip(t[:120])

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing |
                         QPainter.RenderHint.TextAntialiasing |
                         QPainter.RenderHint.SmoothPixmapTransform)
        pm, dark = self.scaled_pixmaps()
        base = self.xf()
        if self.tilted() and self.shape3d == "cylinder":
            self.paint_cylinder(p, base, pm, dark)     # трубка: лицо натягиваем на цилиндр
        else:
            if self.tilted():                          # брусок: сначала боковые грани
                p.setTransform(base)
                self.paint_sides(p)
            p.setTransform(self.face_xf(base))         # дальше всё — в координатах картинки (лицо)
            self.paint_face(p, pm, dark)
        p.end()

    def paint_face(self, p, pm, dark):
        """Всё лицо плеера в координатах картинки: корпус, кнопки, экран, колпачок."""
        nw, nh = self.native_w, self.native_h
        e = self.cap_ease()
        if e > 0.001 and CAP is not None and CAP["plug"] is not None:   # USB-штекер под колпачком
            self.paint_plug(p)

        p.drawPixmap(QRectF(0, 0, nw, nh), pm, QRectF(0, 0, pm.width(), pm.height()))

        if self.pressed in BUTTONS:
            r = BUTTONS[self.pressed]
            k = pm.width() / nw
            src = QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k)
            # «вдавленная» кнопка: темнее и на полтора пикселя внутрь корпуса
            p.drawPixmap(r.translated(*PRESS_OFF), dark, src)

        if REELS:
            self.paint_reels(p)

        self.paint_lcd(p)

        # колпачок: надет — на месте; снят — висит над штекером, чуть повёрнут и покачивается
        cap = self._scaled_cap
        if cap is not None and CAP is not None:
            cap_h = nw * cap.height() / cap.width()
            p.save()
            if e > 0.001:
                bob = math.sin(time.monotonic() * 2.2) * 4 * min(1.0, self.cap_t)
                cx, cy = CAP["pivot_x"], CAP["bottom"] * 0.5
                p.translate(cx - 4 * e, cy - (CAP["lift"] * e + bob))
                p.rotate(-8 * e)
                p.translate(-cx, -cy)
            p.drawPixmap(QRectF(0, 0, nw, cap_h), cap, QRectF(0, 0, cap.width(), cap.height()))
            p.restore()

    def paint_cylinder(self, p, base, pm, dark):
        cr = self.canvas_rect()
        k = self.scale_f * self.devicePixelRatioF()
        lw, lh = max(1, math.ceil(cr.width() * k)), max(1, math.ceil(cr.height() * k))
        if self._layer is None or self._layer.width() != lw or self._layer.height() != lh:
            self._layer = QImage(lw, lh, QImage.Format.Format_ARGB32_Premultiplied)
        layer = self._layer
        layer.fill(Qt.GlobalColor.transparent)
        lp = QPainter(layer)
        lp.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                          QPainter.RenderHint.SmoothPixmapTransform)
        lp.scale(k, k)
        lp.translate(-cr.left(), -cr.top())
        self.paint_face(lp, pm, dark)
        lp.end()

        strips = geom3d.cylinder_strips(self.cyl_cx, self.cyl_r, cr.top(), cr.bottom(),
                                        self.tilt[0], self.tilt[1], band_u=self.cyl_band,
                                        cy=self.native_h / 2)

        def put(ua, ub, quad):
            src = QRectF(ua, cr.top(), ub - ua, cr.height())
            sp = QPolygonF([src.topLeft(), src.topRight(), src.bottomRight(), src.bottomLeft()])
            dp = QPolygonF([QPointF(x, y) for x, y in quad])
            t = QTransform()
            if not QTransform.quadToQuad(sp, dp, t):
                return None
            p.setTransform(t * base)
            p.drawImage(src, layer, QRectF((ua - cr.left()) * k, 0, (ub - ua) * k, lh))
            return src

        for ua, ub, quad, shade, under in strips:
            if under:
                put(under[0], under[1], quad)          # гладкий бок под краем картинки
            src = put(ua, ub, quad)
            if src is None:
                continue
            # свет: каждая полоска — по своему углу на окружности
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            if shade == "edge":
                p.fillRect(src, QColor(8, 10, 14, 200))
            elif shade < 0:
                p.fillRect(src, QColor(0, 0, 0, int(min(200, -shade * 1.15 * 255))))
            elif shade > 0.005:
                p.fillRect(src, QColor(255, 255, 255, int(min(90, shade * 0.35 * 255))))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def paint_reels(self, p):
        """Втулки кассеты — крутятся, пока играет музыка."""
        for k, (x, y, r) in enumerate(REELS):
            p.save()
            p.translate(x, y)
            p.rotate(self.reel_angle * (1.0 if k == 0 else 1.12))
            hub = r * 0.5
            p.setPen(QPen(QColor("#0b0d12"), 2.5))
            p.setBrush(QColor("#f1f2f4"))
            p.drawEllipse(QPointF(0, 0), hub, hub)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2b2f38"))
            for i in range(6):                      # зубчики
                p.save()
                p.rotate(i * 60)
                p.drawRoundedRect(QRectF(-hub * 0.14, -hub * 0.92, hub * 0.28, hub * 0.42), 1.5, 1.5)
                p.restore()
            p.setBrush(QColor("#2b2f38"))
            p.drawEllipse(QPointF(0, 0), hub * 0.26, hub * 0.26)
            p.restore()

    # ------------------------------------------------------------ 3D
    def tilted(self):
        return self.cfg.get("three_d", True) and (abs(self.tilt[0]) > 0.05 or abs(self.tilt[1]) > 0.05)

    def projector(self):
        return geom3d.Projector(self.native_w / 2, self.native_h / 2, self.tilt[0], self.tilt[1])

    def face_xf(self, base=None):
        """Картинка-лицо → окно: перспектива наклона + масштаб/поворот/сдвиг окна."""
        base = base if base is not None else self.xf()
        if not self.tilted():
            return base
        nw, nh = self.native_w, self.native_h
        quad = geom3d.face_quad(nw, nh, self.projector(), self.depth3d)
        src = QPolygonF([QPointF(0, 0), QPointF(nw, 0), QPointF(nw, nh), QPointF(0, nh)])
        dst = QPolygonF([QPointF(x, y) for x, y in quad])
        t = QTransform()
        if not QTransform.quadToQuad(src, dst, t):
            return base
        return t * base

    def sides3d(self):
        """Боковые грани: «full» — с колпачком, «body» — без (колпачок снят)."""
        if self._sides3d is None:
            def make(img):
                img = img.convertToFormat(QImage.Format.Format_ARGB32)
                w, h = img.width(), img.height()
                px = img.pixel
                alpha = lambda x, y: (px(x, y) >> 24) & 255
                color = lambda x, y: ((px(x, y) >> 16) & 255, (px(x, y) >> 8) & 255, px(x, y) & 255)
                pts = geom3d.rdp(geom3d.trace_outline(alpha, w, h, step=4), 1.6)
                return geom3d.build_sides(pts, alpha, color, w, h)
            body = self.base.toImage()
            res = {"body": make(body)}
            if self.cap_pm is not None:
                full = body.convertToFormat(QImage.Format.Format_ARGB32)
                q = QPainter(full)
                q.drawPixmap(0, 0, self.cap_pm)
                q.end()
                res["full"] = make(full)
            else:
                res["full"] = res["body"]
            self._sides3d = res
        return self._sides3d

    def paint_sides(self, p):
        sides = self.sides3d()["full" if self.cap_t <= 0.001 else "body"]
        quads = geom3d.side_quads(sides, self.projector(), self.depth3d)
        ink = QPen(QColor(11, 13, 18), 2.6)
        ink.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ink.setCapStyle(Qt.PenCapStyle.RoundCap)
        for _z, quad, col in quads:
            poly = QPolygonF([QPointF(x, y) for x, y in quad])
            c = QColor(*col)
            p.setPen(QPen(c, 0.8))                 # без щелей между гранями
            p.setBrush(c)
            p.drawPolygon(poly)
            p.setPen(ink)                          # задний край — чёрным, как контур картинки
            p.drawLine(poly[2], poly[3])

    def prebuild_3d(self):
        if self.cfg.get("three_d", True):
            try:
                self.sides3d()
            except Exception as e:
                print("3d:", e)

    def start_phys(self):
        if not self.cfg.get("three_d", True):
            return
        if not self.phys_timer.isActive():
            self._phys_last = time.monotonic()
            self.phys_timer.start()

    def on_phys(self):
        now = time.monotonic()
        dt = min(0.05, now - self._phys_last)
        self._phys_last = now
        if self.spin is not None:                  # крутят руками — без пружины
            self.update()
            return
        dragging = self.drag_from is not None and self.drag_moved
        if dragging:
            k = math.exp(-dt * 7.0)                # мышь остановилась — наклон уходит
            self.drag_vel = [self.drag_vel[0] * k, self.drag_vel[1] * k]
            a = math.radians(-self.angle)          # скорость — в осях самого плеера
            vx = self.drag_vel[0] * math.cos(a) - self.drag_vel[1] * math.sin(a)
            vy = self.drag_vel[0] * math.sin(a) + self.drag_vel[1] * math.cos(a)
            pmax = 16.0 if self.shape3d == "cylinder" else 24.0
            tgt = [max(-32.0, min(32.0, vx * 0.03)), max(-pmax, min(pmax, -vy * 0.024))]
        elif self.cfg.get("tilt_hover", False) and self.hover_pt is not None:
            q = self.xf().inverted()[0].map(QPointF(self.hover_pt))   # без учёта наклона — без «петли»
            tgt = [max(-10.0, min(10.0, (q.x() / self.native_w - 0.5) * 16)),
                   max(-8.0, min(8.0, -(q.y() / self.native_h - 0.5) * 12))]
        else:
            tgt = [0.0, 0.0]
        K, C = 95.0, 8.5                           # пружина и затухание — пару раз качнётся
        for i in (0, 1):
            acc = K * (tgt[i] - self.tilt[i]) - C * self.tilt_v[i]
            self.tilt_v[i] += acc * dt
            self.tilt[i] += self.tilt_v[i] * dt
        settled = (not dragging and tgt == [0.0, 0.0]
                   and all(abs(self.tilt[i]) < 0.04 and abs(self.tilt_v[i]) < 0.08 for i in (0, 1)))
        if settled:
            self.tilt, self.tilt_v = [0.0, 0.0], [0.0, 0.0]
            self.phys_timer.stop()
        self.update()

    def cap_ease(self):
        t = max(0.0, min(1.0, self.cap_t))
        if self.cap_open:                      # снимается — с «отскоком»
            c1 = 1.70158
            c3 = c1 + 1
            return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2
        return t * t * (3 - 2 * t)             # надевается — плавно

    def paint_plug(self, p):
        r = CAP["plug"]
        path = QPainterPath()
        path.addRoundedRect(r, 7, 7)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0.0, QColor("#7d8693"))
        g.setColorAt(0.35, QColor("#eef1f5"))
        g.setColorAt(0.55, QColor("#c3cad4"))
        g.setColorAt(1.0, QColor("#6f7884"))
        p.fillPath(path, g)
        # блик
        p.fillRect(QRectF(r.left() + 30, r.top() + 10, 8, r.height() - 30), QColor(255, 255, 255, 90))
        # два окошка разъёма USB-A
        p.setPen(QPen(QColor("#111418"), 3))
        p.setBrush(QColor("#2a3038"))
        for dx in (-34, 34):
            hole = QRectF(r.center().x() + dx - 15, r.top() + 34, 30, 24)
            p.drawRoundedRect(hole, 3, 3)
        # контур в стиле картинки
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#0b0d12"), 4))
        p.drawPath(path)
        # тень от корпуса на штекере
        sh = QLinearGradient(QPointF(0, r.bottom() - 26), QPointF(0, r.bottom()))
        sh.setColorAt(0, QColor(0, 0, 0, 0))
        sh.setColorAt(1, QColor(0, 0, 0, 110))
        p.fillRect(QRectF(r.left() + 2, r.bottom() - 26, r.width() - 4, 24), sh)

    # ------------------------------------------------------------ анимации экрана
    def on_anim(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last_anim)
        self._last_anim = now
        if self.boot_t0 is not None and not self._boot_sound_done and now - self.boot_t0 > 0.3:
            self._boot_sound_done = True                   # звук включения — когда загорается экран
            self.sound("boot")
        if self.boot_t0 is not None and now - self.boot_t0 > BOOT_LEN:
            self.boot_t0 = None
        target = 1.0 if self.cap_open else 0.0
        if self.cap_t != target:
            spd = dt / (0.5 if self.cap_open else 0.35)
            self.cap_t = min(target, self.cap_t + spd) if target > self.cap_t else max(target, self.cap_t - spd)
            if not self.cap_open and self.cap_t == 0.0 and CAP is not None:
                self.sound("cap_on")
            self.update()
        elif self.cap_open and CAP is not None:
            self.update()                     # покачивание колпачка
        self.ab_check()
        self.write_nowplaying()
        if self.mini.isVisible():
            self.mini.update()
        if self.is_playing() and self.loaded_path:      # статистика: время и «прослушивания»
            key = (f"radio:{self.station}:{self.artist} - {self.title}" if self.radio else self.loaded_path)
            self.stats.add_time(key, dt, self.title, self.artist)
            self._listened += dt
            dur = self.player.duration()
            if not self._play_counted and (dur > 0 or self.radio) and self._listened >= (
                    30.0 if self.radio else min(30.0, dur / 2000.0)):
                self._play_counted = True
                self.stats.add_play(key)
        if REELS and self.is_playing():
            self.reel_angle = (self.reel_angle + dt * 160) % 360
            self.update()
        if self.cfg.get("eq", True):
            self.eq_step(now, dt)
        self.refresh_screen()

    def eq_real_on(self):
        return self.cfg.get("eq_real", True) and self.spectrum.available

    def eq_step(self, now, dt):
        active = self.is_playing() and self.player.volume() > 0
        if active and self.eq_real_on() and self.spectrum.recent():
            level = self.player.volume() ** 0.35
            for k in range(EQ_BANDS):
                self.eq_tgt[k] = level * self.spectrum.bands[k]
        elif active and now >= self.eq_next:
            self.eq_next = now + random.uniform(0.07, 0.14)
            level = self.player.volume() ** 0.4
            if now >= self.eq_beat:                       # «удар» — все вверх, басы сильнее
                self.eq_beat = now + random.uniform(0.38, 0.6)
                for k in range(EQ_BANDS):
                    self.eq_tgt[k] = level * EQ_PROFILE[k] * random.uniform(0.75, 1.0) * (1.0 if k < 4 else 0.85)
            else:
                for k in range(EQ_BANDS):
                    if random.random() < (0.35 if k < 4 else 0.7):   # басы меняются реже
                        self.eq_tgt[k] = level * EQ_PROFILE[k] * random.uniform(0.15, 0.9)
        if not active:
            self.eq_tgt = [0.0] * EQ_BANDS
        for k in range(EQ_BANDS):
            v, t = self.eq_val[k], self.eq_tgt[k]
            if t > v:
                v = min(t, v + dt * 9.0)                    # быстрый подъём
            else:
                v = max(t, v - dt * (2.2 if k < 4 else 3.2))  # плавное падение
            self.eq_val[k] = v
            self.eq_tgt[k] = max(0.0, t - dt * 1.5)         # цель сама «тает»
            pk = self.eq_peak[k]
            self.eq_peak[k] = v if v >= pk else max(v, pk - dt * 0.7)

    def paint_lyrics(self, p, rect2, rect3, pos, f_big, now):
        """Караоке: текущая строка «заливается» в такт, ниже — следующая.
        Возвращает False, если сейчас проигрыш без текста (тогда показываем название)."""
        ly = self.lyrics
        i = ly.index_at(pos)
        if i < 0 or not ly.lines[i].strip():
            return False
        text = ly.lines[i]
        t0 = ly.times[i]
        t1 = ly.times[i + 1] if i + 1 < len(ly) else t0 + 6000
        dur = max(400, min(t1 - t0, 900 + 330 * len(text)))
        frac = max(0.0, min(1.0, (pos - t0) / dur))
        fm = QFontMetricsF(f_big)
        w = fm.horizontalAdvance(text)
        avail = rect2.width()
        wipe = w * frac
        off = max(0.0, min(wipe - avail * 0.6, w - avail)) if w > avail else 0.0
        x = rect2.left() - off
        base = 87
        p.save()
        p.setClipRect(rect2, Qt.ClipOperation.IntersectClip)
        dim = QColor(LCD_INK)
        dim.setAlpha(70)
        p.setFont(f_big)
        p.setPen(dim)
        p.drawText(QPointF(x, base), text)                 # ещё не спетая часть — бледно
        p.setClipRect(QRectF(rect2.left(), rect2.top(), max(0.0, x + wipe - rect2.left()),
                             rect2.height()), Qt.ClipOperation.IntersectClip)
        self.ink_text(p, x, base, text, f_big)             # спетая — чёрными «сегментами»
        p.restore()
        # третья строка: уведомление или следующая строка текста
        if now < self.notice[1]:
            self.marquee(p, rect3, 120, self.notice[0], self.lcd_font(24), now)
        else:
            nxt = next((ly.lines[k] for k in range(i + 1, min(i + 4, len(ly))) if ly.lines[k].strip()), "")
            if nxt:
                f = self.lcd_font(24, bold=False)
                p.save()
                p.setClipRect(rect3, Qt.ClipOperation.IntersectClip)
                c = QColor(LCD_INK)
                c.setAlpha(130)
                p.setFont(f)
                p.setPen(c)
                el = QFontMetricsF(f).elidedText(nxt, Qt.TextElideMode.ElideRight, rect3.width())
                p.drawText(QPointF(rect3.left(), 120), el)
                p.restore()
        return True

    def paint_eq(self, p, u0, u1, v_bottom, v_top):
        """Столбики из сегментов, растут «вверх» по экрану (к правому краю корпуса)."""
        n = EQ_BANDS
        gap = 4
        w = (u1 - u0 - gap * (n - 1)) / n
        if w < 4:
            return
        segs, seg_gap = 6, 1.5
        seg_h = ((v_bottom - v_top) - seg_gap * (segs - 1)) / segs
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(n):
            x = u0 + k * (w + gap)
            on = int(round(self.eq_val[k] * segs))
            peak = min(segs - 1, int(self.eq_peak[k] * segs))
            for j in range(segs):
                y = v_bottom - (j + 1) * seg_h - j * seg_gap
                if j < on:
                    c = QColor(LCD_INK)
                elif j == peak and self.eq_peak[k] > 0.05:
                    c = QColor(LCD_INK)
                    c.setAlpha(150)
                else:
                    c = QColor(0, 0, 0, 16)
                p.fillRect(QRectF(x, y, w, seg_h), c)

    def boot_lit(self, now):
        """Подсветка во время заставки/прощания: None — обычная."""
        if self.boot_t0 is not None:
            t = now - self.boot_t0
            if t < 0.25:
                return 0.0
            if t < 0.5:
                return 1.0 if int(t * 28) % 2 else 0.25        # мигание при включении
            return 1.0
        if self.bye_t0 is not None:
            t = now - self.bye_t0
            return max(0.0, 1.0 - max(0.0, t - 0.45) / 0.45)
        return None

    def paint_boot(self, p, L, T, now):
        t = now - self.boot_t0
        m = 16
        if t < 0.25:
            return                                           # экран ещё «выключен»
        if t < 1.05:
            # тест сегментов: полоски пробегают по экрану, потом всё вспыхивает
            k = (t - 0.25) / 0.6
            n = 22
            bw = (L - 2 * m) / n
            p.setPen(Qt.PenStyle.NoPen)
            if k < 1.0:
                for i in range(n):
                    if i <= k * n:
                        a = 255 if (i + int(t * 20)) % 2 == 0 else 120
                        c = QColor(LCD_INK)
                        c.setAlpha(a)
                        p.fillRect(QRectF(m + i * bw + 2, 12, bw - 4, T - 24), c)
            else:
                c = QColor(LCD_INK)
                c.setAlpha(230 if int(t * 16) % 2 else 70)
                p.fillRect(QRectF(m, 12, L - 2 * m, T - 24), c)
            return
        # HELLO по буквам + полоска загрузки
        word = "HELLO"
        f = self.lcd_font(72)
        fm = QFontMetricsF(f)
        full_w = fm.horizontalAdvance(word)
        x0 = (L - full_w) / 2
        tt = t - 1.05
        shown = min(len(word), int(tt / 0.13) + 1)
        blink_off = tt > 1.2 and int(tt * 10) % 2 == 1
        if not blink_off:
            self.ink_text(p, x0, 98, word[:shown], f)
        f_s = self.lcd_font(18)
        cap = "MP3 PLAYER"
        self.ink_text(p, (L - QFontMetricsF(f_s).horizontalAdvance(cap)) / 2, 30, cap, f_s)
        bar = QRectF(L * 0.2, 128, L * 0.6, 14)
        p.setPen(QPen(LCD_INK, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bar)
        prog = min(1.0, tt / 1.2)
        inner = bar.adjusted(3, 3, -3, -3)
        seg_w, seg_gap = 6, 3
        nseg = int((inner.width() + seg_gap) // (seg_w + seg_gap))
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(int(nseg * prog)):
            p.fillRect(QRectF(inner.left() + i * (seg_w + seg_gap), inner.top(), seg_w, inner.height()), LCD_INK)

    def paint_bye(self, p, L, T, now):
        t = now - self.bye_t0
        if t > 0.8:
            return
        f = self.lcd_font(72)
        word = "BYE"
        w = QFontMetricsF(f).horizontalAdvance(word)
        self.ink_text(p, (L - w) / 2, 104, word, f)

    def paint_lcd(self, p):
        now = time.monotonic()
        lit = max(0.0, min(1.0, (self.light_until - now) / 0.8))   # плавное угасание
        bl = self.boot_lit(now)
        if bl is not None:
            lit = bl
            if bl == 0.0:          # «выключенный» экран темнее обычного
                bg0 = LCD_BG.darker(118)
                path = QPainterPath()
                path.addRoundedRect(SCREEN, SCREEN_R, SCREEN_R)
                p.fillPath(path, bg0)
                return
        bg = QColor(
            round(LCD_BG.red() + (LCD_BG_LIT.red() - LCD_BG.red()) * lit),
            round(LCD_BG.green() + (LCD_BG_LIT.green() - LCD_BG.green()) * lit),
            round(LCD_BG.blue() + (LCD_BG_LIT.blue() - LCD_BG.blue()) * lit))

        # фон экрана
        path = QPainterPath()
        path.addRoundedRect(SCREEN, SCREEN_R, SCREEN_R)
        g = QLinearGradient(SCREEN.topLeft(), SCREEN.topRight())
        g.setColorAt(0, bg.darker(106))
        g.setColorAt(0.5, bg)
        g.setColorAt(1, bg.darker(103))
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        # пиксельная сетка ЖК
        p.setPen(QPen(QColor(0, 0, 0, 12), 1))
        x = SCREEN.left()
        while x < SCREEN.right():
            p.drawLine(QPointF(x, SCREEN.top()), QPointF(x, SCREEN.bottom()))
            x += 4
        y = SCREEN.top()
        while y < SCREEN.bottom():
            p.drawLine(QPointF(SCREEN.left(), y), QPointF(SCREEN.right(), y))
            y += 4
        # внутренняя тень
        sh = QLinearGradient(SCREEN.topLeft(), QPointF(SCREEN.left() + 12, SCREEN.top()))
        sh.setColorAt(0, QColor(0, 0, 0, 45))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(SCREEN, sh)

        # система координат текста: у «стоячего» экрана повёрнута на 90°
        t, L, T = self.lcd_geom()
        p.setTransform(t, True)
        self.paint_content(p, L, T, now)
        p.restore()

    def lcd_font(self, px, bold=True):
        f = QFont()
        f.setFamilies(["Consolas", "Lucida Console", "Courier New", "monospace"])
        f.setPixelSize(int(px))
        f.setBold(bold)
        f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return f

    def ink_text(self, p, x, baseline, text, font):
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 28))                    # «призрак» ЖК
        p.drawText(QPointF(x + 2, baseline + 2), text)
        p.setPen(LCD_INK)
        p.drawText(QPointF(x, baseline), text)

    def marquee(self, p, rect, baseline, text, font, now, speed=38):
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(text)
        p.save()
        p.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        if w <= rect.width():
            self.ink_text(p, rect.left(), baseline, text, font)
        else:
            gap = 60
            cycle = w + gap
            t = (now - self.t0) * speed
            pause = 1.5 * speed                        # пауза в начале
            off = max(0.0, (t % (cycle + pause)) - pause)
            x = rect.left() - off
            self.ink_text(p, x, baseline, text, font)
            self.ink_text(p, x + cycle, baseline, text, font)
        p.restore()

    def paint_content(self, p, L, T, now):
        if self.boot_t0 is not None:
            self.paint_boot(p, L, T, now)
            return
        if self.bye_t0 is not None:
            self.paint_bye(p, L, T, now)
            return
        if self.cap_open:
            self.paint_usb(p, L, T, now)
            return
        if self.stats_page is not None:
            self.paint_stats(p, L, T, now)
            return
        m = 16
        playing = self.is_playing()
        dur = self.player.duration()
        pos = self.player.position()
        if self.scrub is not None and dur > 0:
            pos = int(self.scrub * dur)
        f_small = self.lcd_font(22)
        f_big = self.lcd_font(42)
        f_mid = self.lcd_font(24, bold=False)

        # --- строка 1: значок состояния, номер, формат, время
        y1 = 36
        icon = QRectF(m, y1 - 18, 18, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(LCD_INK)
        if playing:
            tri = QPainterPath()
            tri.moveTo(icon.left(), icon.top())
            tri.lineTo(icon.right(), icon.center().y())
            tri.lineTo(icon.left(), icon.bottom())
            tri.closeSubpath()
            p.drawPath(tri)
        elif self.files:
            p.drawRect(QRectF(icon.left() + 1, icon.top(), 6, 18))
            p.drawRect(QRectF(icon.left() + 11, icon.top(), 6, 18))
        else:
            p.drawRect(icon.adjusted(1, 1, -1, -1))
        p.setBrush(Qt.BrushStyle.NoBrush)

        x = m + 30
        if self.files:
            i = self.current_index()
            num = f"{(i or 0) + 1:02d}/{len(self.files):02d}"
            self.ink_text(p, x, y1, num, f_small)
            x += QFontMetricsF(f_small).horizontalAdvance(num) + 16
            ext = os.path.splitext(self.current_path() or "")[1][1:].upper()[:4] or "MP3"
        else:
            ext = "MP3"
        if self.radio:
            ext = "RADIO"
        ab = "  A–B" if self.ab_b is not None else ("  A–" if self.ab_a is not None else "")
        tag = (ext + ("  ♥" if self.is_fav(self.current_path()) else "") + ab + ("  LRC" if self.lyrics else "") + ("  RND" if self.shuffle else "")
               + ("  RPT1" if self.repeat == "one" else "")
               + (f"  DL{int(self.dl_pct)}%" if self.dl_pct is not None else "")
               + ("  EQ" if self.player.current_kind() == "dsp" else ""))
        ts = fmt_time(pos)
        tw = QFontMetricsF(f_small).horizontalAdvance(ts)
        self.ink_text(p, L - m - tw, y1, ts, f_small)
        tag_end = x
        if x + QFontMetricsF(f_small).horizontalAdvance(tag) < L - m - tw - 12:     # узкий экран — без тега
            self.ink_text(p, x, y1, tag, f_small)
            tag_end = x + QFontMetricsF(f_small).horizontalAdvance(tag)
        if self.cfg.get("eq", True) and self.files:
            eq_u1 = L - m - tw - 18
            eq_u0 = max(tag_end + 18, eq_u1 - 170)
            if eq_u1 - eq_u0 >= 80:
                self.paint_eq(p, eq_u0, eq_u1, y1 + 2, y1 - 22)

        # --- строка 2 и 3: название / исполнитель (или уведомление)
        text_rect2 = QRectF(m, 44, L - 2 * m, 52)
        text_rect3 = QRectF(m, 98, L - 2 * m, 28)
        if not self.files:
            self.marquee(p, text_rect2, 87, "НЕТ МУЗЫКИ", f_big, now)
            note = self.notice[0] if now < self.notice[1] else "ПКМ → ОТКРЫТЬ ПАПКУ"
            self.marquee(p, text_rect3, 120, note, f_mid, now)
        elif self.lyrics and self.paint_lyrics(p, text_rect2, text_rect3, pos, f_big, now):
            pass
        else:
            self.marquee(p, text_rect2, 87, self.title or "—", f_big, now)
            if now < self.notice[1]:
                self.marquee(p, text_rect3, 120, self.notice[0], self.lcd_font(24), now)
            else:
                self.marquee(p, text_rect3, 120, self.artist or "Неизвестный исполнитель", f_mid, now, 30)

        # --- строка 4: прогресс + громкость
        vol_w = VOL_W
        bu0, bu1 = self.bar_span(L)
        bar = QRectF(bu0, BAR_V0, bu1 - bu0, BAR_V1 - BAR_V0)
        if self.radio:                                   # прямой эфир: вместо прогресса — «● LIVE»
            f_l = self.lcd_font(16)
            on = int(now * 1.6) % 2 == 0 and self.is_playing()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(LCD_INK if on else QColor(0, 0, 0, 30))
            p.drawEllipse(QPointF(bu0 + 7, bar.center().y()), 5, 5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            self.ink_text(p, bu0 + 18, bar.bottom(), "LIVE  ·  " + (self.station or "РАДИО"), f_l)
            bar = None
        if bar is not None:
            p.setPen(QPen(LCD_INK, 2))
            p.drawRect(bar)
        if bar is not None and dur > 0:
            seg_w, seg_gap = 6, 3
            inner = bar.adjusted(3, 3, -3, -3)
            nseg = int((inner.width() + seg_gap) // (seg_w + seg_gap))
            fill = int(round(nseg * min(1.0, pos / dur)))
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(nseg):
                c = QColor(LCD_INK) if k < fill else QColor(0, 0, 0, 22)
                p.fillRect(QRectF(inner.left() + k * (seg_w + seg_gap), inner.top(), seg_w, inner.height()), c)
            if self.ab_a is not None:                   # засечки A и B, участок между ними
                xa = inner.left() + inner.width() * min(1.0, self.ab_a / dur)
                if self.ab_b is not None:
                    xb = inner.left() + inner.width() * min(1.0, self.ab_b / dur)
                    loop = QColor(LCD_INK)
                    loop.setAlpha(45)
                    p.fillRect(QRectF(xa, bar.top() - 6, xb - xa, bar.height() + 12), loop)
                    p.fillRect(QRectF(xb - 1.5, bar.top() - 7, 3, bar.height() + 14), LCD_INK)
                p.fillRect(QRectF(xa - 1.5, bar.top() - 7, 3, bar.height() + 14), LCD_INK)
        # громкость: 5 столбиков
        v = self.player.volume()
        bx = L - m - vol_w
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(5):
            h = 5 + k * 3.2
            on = v > (k + 0.5) / 5 - 0.001 or (k == 0 and v > 0)
            c = QColor(LCD_INK) if on else QColor(0, 0, 0, 25)
            p.fillRect(QRectF(bx + k * 13, 146 - h, 9, h), c)

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if self.boot_t0 is not None:          # клик — пропустить заставку
            self.boot_t0 = None
            self.refresh_screen()
        pt = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            btn = self.button_at(pt)
            if btn:
                self.pressed = btn
                self.light()
                self.sound("click_down")
                if btn in ("prev", "next", "play") and not self.cap_open and self.stats_page is None:
                    self.hold_timer.start()
                self.update()
            elif self.player.duration() > 0 and self.bar_frac(pt) is not None:
                self.scrub = self.bar_frac(pt)
                self.light()
                self.refresh_screen()
            elif e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.rot_drag = (self.mouse_angle(e.globalPosition()), self.angle)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif (e.modifiers() & Qt.KeyboardModifier.ControlModifier) and self.cfg.get("three_d", True):
                self.spin = (e.globalPosition(), self.tilt[0], self.tilt[1])   # покрутить в руках
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.start_phys()
            else:
                self.drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self.drag_moved = False
                self._drag_prev = (e.globalPosition(), time.monotonic())
                self.drag_vel = [0.0, 0.0]
        elif e.button() == Qt.MouseButton.MiddleButton:
            self.toggle_play()

    def mouse_angle(self, gpos):
        c = self.center_global()
        return math.degrees(math.atan2(gpos.y() - c.y(), gpos.x() - c.x()))

    def mouseMoveEvent(self, e):
        if self.spin is not None:
            p0, y0, t0 = self.spin
            d = e.globalPosition() - p0
            if self.shape3d == "cylinder":             # трубку можно крутить по кругу
                self.tilt = [y0 + d.x() * 0.6, max(-20.0, min(20.0, t0 - d.y() * 0.25))]
            else:
                self.tilt = [max(-70.0, min(70.0, y0 + d.x() * 0.45)),
                             max(-60.0, min(60.0, t0 - d.y() * 0.35))]
            self.tilt_v = [0.0, 0.0]
            self.update()
            return
        if self.rot_drag is not None:
            a0, start = self.rot_drag
            self.set_angle(start + self.mouse_angle(e.globalPosition()) - a0)
            QToolTip.showText(e.globalPosition().toPoint(), f"🔄 {round(self.angle)}°", self)
            return
        if self.scrub is not None:
            self.scrub = self.bar_frac_any(e.position())
            self.light()
            self.refresh_screen()
            QToolTip.showText(e.globalPosition().toPoint(),
                              fmt_time(self.scrub * self.player.duration()), self)
            return
        if self.drag_from is not None and e.buttons() & Qt.MouseButton.LeftButton:
            target = e.globalPosition().toPoint() - self.drag_from
            if (target - self.pos()).manhattanLength() > 2 or self.drag_moved:
                self.drag_moved = True
                self.move(target)
                # скорость перетаскивания → наклон корпуса (3D)
                now, g = time.monotonic(), e.globalPosition()
                if self._drag_prev is not None:
                    pg, pt = self._drag_prev
                    dtm = now - pt
                    if dtm > 0.004:
                        iv = ((g.x() - pg.x()) / dtm, (g.y() - pg.y()) / dtm)
                        self.drag_vel = [self.drag_vel[0] * 0.6 + iv[0] * 0.4,
                                         self.drag_vel[1] * 0.6 + iv[1] * 0.4]
                self._drag_prev = (g, now)
                self.start_phys()
            return
        self.hover_pt = e.position()
        if self.cfg.get("tilt_hover", False):
            self.start_phys()
        btn = (self.button_at(e.position()) or ("cap" if self.on_cap(e.position()) else None)
               or ("bottom" if self.on_bottom(e.position()) else None))
        f = self.bar_frac(e.position()) if self.player.duration() > 0 else None
        if f is not None and not btn:
            self.hover_btn = "bar"
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"⏩ {fmt_time(f * self.player.duration())}", self)
            return
        if btn != self.hover_btn:
            self.hover_btn = btn
            self.setCursor(Qt.CursorShape.PointingHandCursor if btn else Qt.CursorShape.ArrowCursor)
            if btn:
                hint = ((USB_HINTS.get(btn) if self.cap_open else None)
                        or (STATS_HINTS.get(btn) if self.stats_page is not None else None)
                        or BUTTON_HINTS[btn])
                if btn == "bottom" and SKIN_HINTS.get("folder"):
                    hint = SKIN_HINTS["folder"]
                if btn == "cap" and self.cap_open:
                    hint = "⏏  Надеть колпачок"
                QToolTip.showText(e.globalPosition().toPoint(), hint, self)
            else:
                QToolTip.hideText()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.spin is not None:                 # отпустили — пружиной обратно
            self.spin = None
            self.tilt[0] = (self.tilt[0] + 180.0) % 360.0 - 180.0   # ближайший путь назад
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.start_phys()
            return
        if self.rot_drag is not None:
            self.rot_drag = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.hideText()
            self.keep_on_screen()
            self.save_later()
            return
        if self.scrub is not None:
            dur = self.player.duration()
            if dur > 0:
                self.player.setPosition(int(min(self.scrub, 0.999) * dur))
            self.scrub = None
            QToolTip.hideText()
            self.sync_timeline(force=True)
            self.refresh_screen()
        elif self.pressed:
            btn, self.pressed = self.pressed, None
            self.hold_timer.stop()
            was_hold = self.hold_seeking
            self.hold_seeking = False
            self.seek_timer.stop()
            self.sound("click_up")
            if not was_hold and self.button_at(e.position()) == btn:
                if self.stats_page is not None and not self.cap_open:
                    {"prev": lambda: self.stats_move(-1), "next": lambda: self.stats_move(+1)}.get(
                        btn, self.close_stats)()
                elif self.cap_open:
                    {"prev": lambda: self.usb_move(-1), "next": lambda: self.usb_move(+1),
                     "play": self.usb_activate, "shuffle": self.toggle_cap,
                     "menu": self.toggle_cap}[btn]()
                else:
                    {"prev": lambda: self.step(-1), "next": lambda: self.step(+1),
                     "play": self.toggle_play, "shuffle": self.toggle_shuffle,
                     "menu": self.toggle_cap}[btn]()
            self.update()
        elif self.drag_from is not None:
            if not self.drag_moved and self.on_screen(e.position()):
                self.light()
                if self.cap_open:
                    self.usb_click(e.position())
                elif self.stats_page is not None:
                    self.stats_move(+1)
                else:
                    self._screen_click.start()          # ждём: может, это двойной клик
            elif not self.drag_moved and self.on_cap(e.position()):
                self.toggle_cap()
            elif not self.drag_moved and self.on_bottom(e.position()):
                self.light()
                self.next_folder()
            self.drag_from = None
            if self.drag_moved:
                self.save_later()

    def usb_click(self, pt):
        """Клик по пункту меню на экране — выбрать и сразу изменить."""
        _u, v = self.lcd_uv(pt)
        row = int((v - 44) // 34)
        if 0 <= row < 3:
            i = self.usb_top + row
            if i < len(self.usb_items()):
                self.usb_sel = i
                self.sound("click_down")
                self.usb_activate()

    def mouseDoubleClickEvent(self, e):
        # двойной клик по экрану — в избранное / убрать; по кнопкам — как обычное нажатие
        if (e.button() == Qt.MouseButton.LeftButton and self.on_screen(e.position())
                and not self.cap_open and self.stats_page is None and self.files):
            self._screen_click.stop()
            self.toggle_favorite()
            return
        self.mousePressEvent(e)

    def leaveEvent(self, _e):
        self.hover_btn = None
        self.hover_pt = None
        if self.cfg.get("tilt_hover", False):
            self.start_phys()
        QToolTip.hideText()

    def start_hold_seek(self):
        if self.pressed == "play" and self.player.duration() > 0:
            self.hold_seeking = True             # отпускание кнопки уже не «пауза»
            self.ab_cycle()
            return
        if self.pressed in ("prev", "next") and self.player.duration() > 0:
            self.hold_seeking = True
            self.hold_seek_step()
            self.seek_timer.start()

    def hold_seek_step(self):
        self.light()
        self.seek_by(4000 if self.pressed == "next" else -4000)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()   # с Alt Qt присылает «горизонтальное» колесо
        if not d:
            return
        if e.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.set_scale(self.scale_f + (0.04 if d > 0 else -0.04))
            return
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.set_angle(self.angle + (5 if d > 0 else -5))
            self.keep_on_screen()
            return
        if self.cap_open and self.on_screen(e.position()):
            self.usb_move(-1 if d > 0 else +1)
            return
        if self.stats_page is not None and self.on_screen(e.position()):
            self.stats_move(-1 if d > 0 else +1)
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.seek_by(5000 if d > 0 else -5000)
            self.light()
        else:
            self.change_volume(0.05 if d > 0 else -0.05)

    def contextMenuEvent(self, e):
        self.sync_menu()
        self.menu.exec(e.globalPos())

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or (md.hasText() and dlmod.is_url(md.text().strip())):
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        web = [u.toString() for u in md.urls() if u.scheme() in ("http", "https")]
        if not web and md.hasText() and dlmod.is_url(md.text().strip()):
            web = [md.text().strip()]
        if web and not any(u.isLocalFile() for u in md.urls()):
            for u in web:                                # ссылка из браузера → скачать в плеер
                self.start_download(u)
            return
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        lists = [p for p in paths if p.lower().endswith((".m3u", ".m3u8", ".pls"))]
        if lists and len(lists) == len(paths):         # плейлист радио — добавить станции
            self.import_stations(lists[0])
            return
        lrcs = [p for p in paths if p.lower().endswith(".lrc")]
        if lrcs and len(lrcs) == len(paths):           # .lrc на плеер — привязать к текущему треку
            self.link_lrc_file(lrcs[0])
            return
        if paths:
            self.load_sources(paths, autoplay=True)

    # ------------------------------------------------------------ menu / tray
    def build_menu(self):
        m = QMenu()
        m.setStyleSheet("""
            QMenu { background:#20262a; color:#dfe8e4; border:1px solid #4a5560; padding:4px; }
            QMenu::item { padding:5px 22px 5px 22px; }
            QMenu::item:selected { background:#8fb3a5; color:#111; }
            QMenu::separator { height:1px; background:#3a444c; margin:4px 8px; }
            QMenu::indicator { width:12px; height:12px; left:5px; }
        """)
        m.addAction("📂  Открыть папку с музыкой…", self.open_folder)
        self.radio_menu = m.addMenu("📻  Радио   (Ctrl+Alt+R)")
        self.radio_menu.setToolTipsVisible(True)
        self.radio_menu.aboutToShow.connect(self.build_radio_menu)
        self.folders_menu = m.addMenu("📁  Мои папки")
        self.folders_menu.setToolTipsVisible(True)
        self.folders_menu.aboutToShow.connect(self.build_folders_menu)
        m.addAction("🎵  Открыть файлы…", self.open_files)
        dlm = m.addMenu("⬇  Скачать в плеер (Doulander)   (Ctrl+Alt+D)")
        dlm.addAction("Вставить ссылку…", self.download_dialog)
        dlm.addAction("Отменить скачивание", self.dl.cancel).setEnabled(self.dl.pending() > 0)
        dlm.addSeparator()
        dd = self.doulander_dir(ask=False)
        dlm.addAction(("Doulander: " + dd) if dd else "Где Doulander?…", self.choose_doulander)
        m.addAction("📜  Плейлист", self.open_playlist)
        um = m.addMenu("⬆  Обновления")
        um.aboutToShow.connect(lambda um=um: self.build_update_menu(um))
        m.addSeparator()
        self.act_play = m.addAction("⏯  Играть / Пауза", self.toggle_play)
        m.addAction("⏭  Следующий", lambda: self.step(+1))
        m.addAction("⏮  Предыдущий", lambda: self.step(-1))
        fv = m.addMenu("❤  Избранное")
        self.act_fav = fv.addAction("♥  В избранное   (двойной клик по экрану)", self.toggle_favorite)
        self.act_fav_play = fv.addAction("Слушать избранное", self.play_favorites)
        it = m.addMenu("📊  Итоги")
        it.addAction("Показать на экране   (Ctrl+Alt+I)", self.open_stats)
        it.addAction("Сбросить статистику…", self.reset_stats)
        ab = m.addMenu("🅰🅱  Повтор A–B   (удерживай ⏯ / Ctrl+Alt+A)")
        ab.addAction("Поставить A (сейчас)", self.ab_set_a)
        ab.addAction("Поставить B (сейчас) и крутить", self.ab_set_b)
        ab.addAction("Сбросить", self.ab_clear)
        self.act_shuffle = m.addAction("🔀  Перемешивание", self.toggle_shuffle)
        self.act_shuffle.setCheckable(True)

        rep = m.addMenu("🔁  Повтор")
        grp = QActionGroup(self)
        self.rep_actions = {}
        for key, name in (("all", "Весь список"), ("one", "Один трек"), ("off", "Выключен")):
            a = rep.addAction(name, lambda _=False, k=key: self.set_repeat(k))
            a.setCheckable(True)
            grp.addAction(a)
            self.rep_actions[key] = a

        m.addSeparator()
        size = m.addMenu("📏  Размер   (Alt + колёсико)")
        sgrp = QActionGroup(self)
        self.size_actions = {}
        self.skins_menu = m.addMenu("🎨  Скин")
        self.skins_menu.aboutToShow.connect(self.build_skins_menu)
        rot = m.addMenu("🔄  Поворот   (Shift + тянуть / колёсико)")
        rot.addAction("Вертикально  (0°)", lambda: self.set_angle(0))
        rot.addAction("Горизонтально, текст ровно  (−90°)", lambda: self.set_angle(-90))
        rot.addAction("Горизонтально  (90°)", lambda: self.set_angle(90))
        rot.addAction("Вверх ногами  (180°)", lambda: self.set_angle(180))
        rot.addSeparator()
        rot.addAction("Повернуть на 90° ↻", lambda: self.set_angle(self.angle + 90))
        rot.addAction("Повернуть на 90° ↺", lambda: self.set_angle(self.angle - 90))
        size.addAction("По экрану", lambda: self.set_scale(self.auto_scale_here()))
        size.addSeparator()
        for name, val in SIZES.items():
            a = size.addAction(name, lambda _=False, v=val: self.set_scale(self.auto_scale_here() * v))
            a.setCheckable(True)
            sgrp.addAction(a)
            self.size_actions[val] = a

        place = m.addMenu("🖥  Где показывать")
        pgrp = QActionGroup(self)
        self.act_desk = place.addAction("Только на рабочем столе", lambda: self.set_mode(True))
        self.act_top = place.addAction("Поверх всех окон", lambda: self.set_mode(False))
        for a in (self.act_desk, self.act_top):
            a.setCheckable(True)
            pgrp.addAction(a)
        place.addSeparator()
        self.act_keep = place.addAction("Не прятаться по Win+D", self.toggle_keep_desktop)
        self.act_keep.setCheckable(True)

        self.hot_menu = m.addMenu("⌨  Горячие клавиши")
        self.act_hot = self.hot_menu.addAction("Включены", self.toggle_hotkeys)
        self.act_hot.setCheckable(True)
        self.hot_menu.addSeparator()
        self.hot_labels = []
        for _hid, _m, _vk, _cmd, label in HOTKEYS:
            a = self.hot_menu.addAction(label)
            a.setEnabled(False)
            self.hot_labels.append((label, a))
        self.hot_menu.menuAction().setVisible(sys.platform == "win32")

        sm = m.addMenu("🎚  Плавность")
        self.act_fade = sm.addAction("Затухание при паузе", self.toggle_fade_pause)
        self.act_fade.setCheckable(True)
        sm.addSeparator()
        hdr = sm.addAction("Переход между треками:")
        hdr.setEnabled(False)
        xgrp = QActionGroup(self)
        self.xf_actions = {}
        for name, ms in CROSSFADES.items():
            a = sm.addAction("   " + name, lambda _=False, v=ms: self.set_crossfade(v))
            a.setCheckable(True)
            xgrp.addAction(a)
            self.xf_actions[ms] = a
        scr = m.addMenu("📟  Экран")
        self.act_boot = scr.addAction("Заставка HELLO при запуске", lambda: self.toggle_cfg("boot_anim", "ЗАСТАВКА"))
        self.act_3d = scr.addAction("3D-корпус: наклон при перемещении  (Ctrl + тянуть — покрутить)",
                                    self.toggle_3d)
        self.act_3d.setCheckable(True)
        self.act_tilt_hover = scr.addAction("3D: наклоняться за курсором",
                                            lambda: self.toggle_cfg("tilt_hover", "НАКЛОН ЗА КУРСОРОМ"))
        self.act_tilt_hover.setCheckable(True)
        scr.addSeparator()
        self.act_eq = scr.addAction("Эквалайзер", lambda: self.toggle_cfg("eq", "ЭКВАЛАЙЗЕР"))
        self.act_eq_real = scr.addAction("Эквалайзер по настоящему звуку", lambda: self.toggle_cfg("eq_real", "СПЕКТР"))
        self.act_eq_real.setCheckable(True)
        self.act_lyr = scr.addAction("Текст песни (.lrc)", self.toggle_lyrics)
        self.act_lyr.setCheckable(True)
        m.addAction("🎚  Эквалайзер звука…   (Ctrl+Alt+E)", self.open_eq)
        nm = m.addMenu("🔊  Выравнивание громкости")
        nm.aboutToShow.connect(lambda nm=nm: self.build_normalize_menu(nm))
        self.lyrics_menu = m.addMenu("🎤  Текст песни")
        self.lyrics_menu.setToolTipsVisible(True)
        self.lyrics_menu.aboutToShow.connect(self.build_lyrics_menu)
        self.act_click = scr.addAction("Звук кнопок", lambda: self.toggle_cfg("click_sound", "ЗВУК КНОПОК"))
        snd = scr.addMenu("Звуки")
        sgrp2 = QActionGroup(self)
        self.snd_actions = {}
        for key, name in (("beep", "Писк (твой звук из заставки)"), ("classic", "Щелчки")):
            a = snd.addAction(name, lambda _=False, k=key: self.set_sound_theme(k))
            a.setCheckable(True)
            sgrp2.addAction(a)
            self.snd_actions[key] = a
        snd.addSeparator()
        vg = QActionGroup(self)
        self.sfx_vol_actions = {}
        for name, v in SFX_LEVELS.items():
            a = snd.addAction("Громкость: " + name.lower(), lambda _=False, v=v: self.set_sfx_volume(v))
            a.setCheckable(True)
            vg.addAction(a)
            self.sfx_vol_actions[v] = a
        snd.addSeparator()
        snd.addAction("Папка своих звуков…", self.open_sounds_folder)
        self.act_click.setCheckable(True)
        th = scr.addMenu("Подсветка")
        thg = QActionGroup(self)
        self.theme_actions = {}
        for name in THEMES:
            a = th.addAction(name.capitalize(), lambda _=False, n=name: self.set_theme(n))
            a.setCheckable(True)
            thg.addAction(a)
            self.theme_actions[name] = a
        scr.addAction("Показать заставку сейчас", self.replay_boot)
        scr.addAction("Снять / надеть колпачок (настройки)", self.toggle_cap)
        for a in (self.act_boot, self.act_eq):
            a.setCheckable(True)
        self.act_resume = m.addAction("🔖  Помнить место в длинных файлах (20+ мин)", self.toggle_resume)
        self.act_resume.setCheckable(True)

        self.act_cover = m.addAction("🖼  Всплывающая обложка", self.toggle_cover_popup)
        self.act_cover.setCheckable(True)
        tm = m.addMenu("🔔  «Сейчас играет»")
        tgrp = QActionGroup(self)
        self.toast_actions = {}
        for key, name in (("hidden", "Когда плеер спрятан"), ("always", "Всегда"), ("off", "Выключено")):
            a = tm.addAction(name, lambda _=False, k=key: self.set_toast_mode(k))
            a.setCheckable(True)
            tgrp.addAction(a)
            self.toast_actions[key] = a

        self.act_dc = m.addAction("🎮  Показывать в Discord (через Music → Discord)", self.toggle_discord_link)
        self.act_dc.setCheckable(True)
        self.assoc_menu = m.addMenu("🔗  Открывать музыку в MP3 Плеере")
        self.assoc_menu.aboutToShow.connect(self.build_assoc_menu)
        self.assoc_menu.menuAction().setVisible(sys.platform == "win32")
        self.act_auto = m.addAction("🚀  Запускать вместе с Windows", self.toggle_autostart)
        self.act_auto.setCheckable(True)
        self.act_auto.setVisible(sys.platform == "win32")
        m.addSeparator()
        self.act_mini = m.addAction("▭  Мини-режим   (Ctrl+Alt+M)", lambda: self.set_mini(not self.cfg.get("mini")))
        self.act_mini.setCheckable(True)
        m.addAction("👁  Показать плеер", self.show_player)
        m.addAction("🙈  Спрятать (останется в трее)", self.hide)
        m.addAction("🛠  Диагностика…", self.show_diagnostics)
        m.addAction("✖  Выход", self.quit)
        self.menu = m

    def sync_menu(self):
        self.act_shuffle.setChecked(self.shuffle)
        self.rep_actions[self.repeat].setChecked(True)
        for v, a in self.size_actions.items():
            a.setChecked(abs(self.auto_scale_here() * v - self.scale_f) < 0.011)
        self.act_desk.setChecked(self.on_desktop)
        self.act_top.setChecked(not self.on_desktop)
        self.act_auto.setChecked(Autostart.enabled())
        self.act_mini.setChecked(bool(self.cfg.get("mini")))
        self.act_dc.setChecked(bool(self.cfg.get("discord_link", True)))
        self.act_cover.setChecked(bool(self.cfg.get("cover_popup", True)))
        fav_now = self.is_fav(self.current_path())
        self.act_fav.setText(("♡  Убрать из избранного" if fav_now else "♥  В избранное")
                             + "   (двойной клик по экрану)")
        self.act_fav.setEnabled(bool(self.current_path()))
        n_fav = len([f for f in self.favorites() if os.path.isfile(f)])
        self.act_fav_play.setText("↩  Вернуться к папке" if self.cfg.get("fav_mode")
                                  else f"▶  Слушать избранное ({n_fav})")
        self.act_fade.setChecked(bool(self.cfg.get("fade_pause", True)))
        xf = int(self.cfg.get("crossfade", 4000))
        (self.xf_actions.get(xf) or self.xf_actions[4000]).setChecked(True)
        self.act_resume.setChecked(bool(self.cfg.get("resume_long", True)))
        self.act_boot.setChecked(bool(self.cfg.get("boot_anim", True)))
        self.act_eq.setChecked(bool(self.cfg.get("eq", True)))
        self.act_lyr.setChecked(bool(self.cfg.get("lyrics", True)))
        self.act_click.setChecked(bool(self.cfg.get("click_sound", True)))
        self.act_3d.setChecked(bool(self.cfg.get("three_d", True)))
        self.act_tilt_hover.setChecked(bool(self.cfg.get("tilt_hover", False)))
        self.act_tilt_hover.setEnabled(bool(self.cfg.get("three_d", True)))
        self.act_eq_real.setChecked(bool(self.cfg.get("eq_real", True)))
        self.act_eq_real.setEnabled(self.spectrum.available)
        self.snd_actions.get(self.cfg.get("sound_theme", "beep"), self.snd_actions["beep"]).setChecked(True)
        cur_v = float(self.cfg.get("sfx_volume", 0.5))
        for v, a in self.sfx_vol_actions.items():
            a.setChecked(abs(v - cur_v) < 0.01)
        self.act_keep.setChecked(bool(self.cfg.get("keep_desktop", True)))
        self.act_keep.setEnabled(self.on_desktop and self.keeper.ok)
        (self.theme_actions.get(self.cfg.get("theme", "ЗЕЛЁНАЯ")) or
         self.theme_actions["ЗЕЛЁНАЯ"]).setChecked(True)
        self.toast_actions.get(self.cfg.get("toast", "hidden"), self.toast_actions["hidden"]).setChecked(True)
        on = bool(self.cfg.get("hotkeys", True))
        self.act_hot.setChecked(on)
        for label, a in self.hot_labels:
            a.setText(label + ("   (занято другой программой)" if on and label in self.hotkeys.failed else ""))

    def build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(resource("icon.ico")), self)
        self.tray.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self.sync_menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.setToolTip(APP_TITLE)
        self.tray.show()

    def on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and reason == QSystemTrayIcon.ActivationReason.DoubleClick:
                self.hide()
            else:
                self.show_player()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.toggle_play()

    def open_from_shell(self, paths):
        """Файлы/папки из проводника: один файл — играем его, а плейлист — вся его папка;
        несколько файлов — ровно они; папка — вся папка."""
        paths = [p for p in dict.fromkeys(paths) if os.path.exists(p)]
        if not paths:
            self.show_player()
            return
        if self.cap_open:
            self.toggle_cap()
        self.stats_page = None
        audio = [p for p in paths if os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXT]
        lrcs = [p for p in paths if p.lower().endswith(".lrc")]
        lists = [p for p in paths if p.lower().endswith((".m3u", ".m3u8", ".pls"))]
        dirs = [p for p in paths if os.path.isdir(p)]
        if lists and not audio and not dirs:
            self.import_stations(lists[0])
        elif lrcs and not audio and not dirs:
            self.link_lrc_file(lrcs[0])
        elif len(audio) == 1 and not dirs:
            f = audio[0]
            # плейлист — только эта папка (без подпапок): открывается мгновенно
            self.load_sources([os.path.dirname(f)], start_path=f, start_pos=0, autoplay=True,
                              remember=False, flat=True)
        elif audio or dirs:
            self.load_sources(dirs + audio, autoplay=True)
        self.show_player()

    def assoc_register(self, quiet=False):
        try:
            winassoc.register(sorted(AUDIO_EXT))
            self.cfg["assoc"] = True
            self.save_later()
            if not quiet:
                self.flash("ДОБАВЛЕН В WINDOWS", 2.5)
            return True
        except Exception as e:
            self.flash("НЕ ВЫШЛО: РЕЕСТР")
            print("assoc:", e)
            return False

    def assoc_make_default(self):
        from PySide6.QtWidgets import QMessageBox
        if not self.assoc_register(quiet=True):
            return
        box = QMessageBox()
        box.setWindowTitle(APP_TITLE)
        box.setWindowIcon(QIcon(resource("icon.ico")))
        box.setText("Плеер добавлен в Windows. Осталось выбрать его по умолчанию — "
                    "Windows разрешает это сделать только тебе самому.")
        box.setInformativeText(
            "Сейчас откроются «Приложения по умолчанию»:\n"
            "• Windows 11 — на странице «MP3 Плеер» нажми «Установить по умолчанию» "
            "(или выбери его для .mp3, .flac и т.д.).\n"
            "• Windows 10 — «Выбор стандартных приложений для типов файлов» → .mp3 → MP3 Плеер.\n\n"
            "Или проще: правый клик по любому MP3 → «Открыть с помощью» → «Выбрать другое "
            "приложение» → MP3 Плеер → «Всегда».\n\n"
            "Пункт «▶ Играть в MP3 Плеере» в контекстном меню файлов и папок уже работает.")
        box.exec()
        winassoc.open_default_apps_settings()

    def assoc_unregister(self):
        try:
            winassoc.unregister(sorted(AUDIO_EXT))
        except Exception as e:
            print("assoc:", e)
        self.cfg["assoc"] = False
        self.flash("УБРАН ИЗ WINDOWS", 2.5)
        self.save_later()

    def build_assoc_menu(self):
        m = self.assoc_menu
        m.clear()
        reg = winassoc.is_registered()
        a = m.addAction("✔  Добавлен: «Открыть с помощью» и меню проводника" if reg
                        else "➕  Добавить в «Открыть с помощью» и меню проводника", self.assoc_register)
        a.setEnabled(not reg)
        m.addAction("⭐  Сделать плеером по умолчанию…", self.assoc_make_default)
        if reg:
            m.addSeparator()
            m.addAction("✖  Убрать плеер из Windows", self.assoc_unregister)

    def set_mini(self, on):
        """Мини-режим: большой плеер прячется, вместо него — полоска."""
        self.cfg["mini"] = bool(on)
        if on:
            m = self.mini
            x, y = self.cfg.get("mini_x"), self.cfg.get("mini_y")
            ok = x is not None and any(sc.availableGeometry().contains(QPoint(x + 30, y + 20))
                                       for sc in QGuiApplication.screens())
            if not ok:                               # туда, где стоял плеер
                vr = self.visual_rect()
                g = (QGuiApplication.screenAt(vr.center()) or QGuiApplication.primaryScreen()).availableGeometry()
                x = max(g.left(), min(vr.center().x() - m.W // 2, g.right() - m.W))
                y = max(g.top(), min(vr.bottom() - m.H - 20, g.bottom() - m.H))
            m.move(x, y)
            self.playlist_panel.hide()
            self.hide()
            m.show()
            WinDesktop.bring_front(m)
        else:
            self.mini.hide()
            self.cfg["mini"] = False
            self.show_player()
        self.save_later()

    def show_player(self):
        """Показать плеер и вытащить его поверх окон (если потерялся — вернуть на экран)."""
        if self.cfg.get("mini"):
            self.mini.show()
            WinDesktop.bring_front(self.mini)
            if self.on_desktop:
                WinDesktop.peek_topmost(self.mini, 2500)
            self.light(3.0)
            return
        vr = self.visual_rect()
        if not any(sc.availableGeometry().intersects(vr) for sc in QGuiApplication.screens()):
            for k in ("x", "y", "cx", "cy"):
                self.cfg.pop(k, None)
            self.restore_pos()
        self.show()
        WinDesktop.bring_front(self)
        if self.on_desktop:
            WinDesktop.peek_topmost(self, 3000)
        self.light(3.0)

    def open_folder(self):
        start = (self.cfg.get("sources") or [QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MusicLocation)])[0]
        d = QFileDialog.getExistingDirectory(None, "Папка с музыкой", start)
        if d:
            self.load_sources([d], autoplay=True)

    def open_files(self):
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)
        exts = " ".join("*" + x for x in sorted(AUDIO_EXT))
        files, _ = QFileDialog.getOpenFileNames(None, "Аудиофайлы", start, f"Музыка ({exts})")
        if files:
            self.load_sources(files, autoplay=True)

    def set_scale(self, v):
        v = round(max(SCALE_MIN, min(SCALE_MAX, v)), 2)
        c = self.center_global()
        self.scale_f = v
        self.cfg.setdefault("scales", {})[self.skin_id] = v
        self.apply_geometry(c)
        self.keep_on_screen()
        self.save_later()

    def set_mode(self, desktop):
        self.keeper.release()
        self.mini_keeper.release()
        self.on_desktop = desktop
        self.apply_flags()
        self.mini.apply_flags()
        self.save_later()

    def toggle_cfg(self, key, label):
        self.cfg[key] = not self.cfg.get(key, True)
        self.flash(f"{label}: " + ("ВКЛ" if self.cfg[key] else "ВЫКЛ"))
        self.save_later()

    def cycle_eq(self):
        """СПЕКТР → ДЕКОР → ВЫКЛ → СПЕКТР (без поддержки спектра: ДЕКОР ↔ ВЫКЛ)."""
        if not self.cfg.get("eq", True):
            self.cfg["eq"], self.cfg["eq_real"] = True, True
        elif self.eq_real_on():
            self.cfg["eq_real"] = False
        else:
            self.cfg["eq"] = False
        self.save_later()

    def toggle_3d(self):
        c = self.center_global()
        self.cfg["three_d"] = not self.cfg.get("three_d", True)
        self.tilt, self.tilt_v = [0.0, 0.0], [0.0, 0.0]
        self.phys_timer.stop()
        self.apply_geometry(c)
        self.keep_on_screen()
        self.flash("3D: " + ("ВКЛ" if self.cfg["three_d"] else "ВЫКЛ"))
        if self.cfg["three_d"]:                   # показать, что умеет: качнуть
            self.tilt_v = [160.0, -60.0]
            self.start_phys()
        self.save_later()

    def toggle_keep_desktop(self):
        on = not self.cfg.get("keep_desktop", True)
        self.cfg["keep_desktop"] = on
        self.keeper.enabled = on
        self.mini_keeper.enabled = on
        if not on:
            self.keeper.release()
            self.mini_keeper.release()
        self.flash("WIN+D: " + ("НЕ ПРЯЧУСЬ" if on else "КАК ВСЕ"))
        self.save_later()

    def diagnostics_text(self):
        import platform
        try:
            import PySide6
            from PySide6.QtCore import qVersion
            qt = f"PySide6 {PySide6.__version__}, Qt {qVersion()}"
        except Exception as e:
            qt = f"? ({e})"
        try:
            import numpy
            npv = numpy.__version__
        except Exception:
            npv = "нет (спектр считается без него)"
        sp, kp = self.spectrum, self.keeper
        age = time.monotonic() - sp.t_last if sp.t_last else None
        lines = [
            f"Версия: {qt}, Python {platform.python_version()}, {platform.platform()}",
            "",
            "НАСТОЯЩИЙ СПЕКТР",
            f"  доступен: {'да' if sp.available else 'нет — ' + sp.reason}",
            f"  включён в настройках: {'да' if self.cfg.get('eq_real', True) else 'нет'}",
            f"  получено кусков звука: {sp.count}" + (f", последний {age:.1f} с назад" if age is not None else ""),
            f"  формат: {sp.fmt_info or '—'}; способ чтения: {sp.method or '—'}",
            f"  numpy: {npv}",
            f"  ошибок: {sp.errors}" + (f" (последняя: {sp.last_error})" if sp.last_error else ""),
            "",
            "WIN+D / РАБОЧИЙ СТОЛ",
            f"  режим: {'на рабочем столе' if self.on_desktop else 'поверх всех окон'}; "
            f"защита: {'вкл' if self.keeper.enabled else 'выкл'}",
            f"  слежение: {'работает' if kp.ok else 'нет — ' + kp.reason}",
            f"  событий: {kp.events}; последнее активное окно: {kp.last_fg or '—'}",
            f"  сейчас поверх: {'да' if kp.on_top else 'нет'}; возвращал из свёрнутого: {kp.restores} раз",
            "",
            "ПРОЧЕЕ",
            f"  медиа-клавиши Windows: {'да' if self.sysmedia.ok else 'нет — ' + self.sysmedia.error}",
            f"  занятые горячие клавиши: {', '.join(self.hotkeys.failed) or 'нет'}",
            f"  скин: {self.skin_meta.get('name', self.skin_id)}; масштаб {self.scale_f}; угол {self.angle}°",
            f"  папка настроек: {self.data_dir}",
        ]
        return "\n".join(lines)

    def show_diagnostics(self):
        from PySide6.QtWidgets import QMessageBox
        text = self.diagnostics_text()
        box = QMessageBox()
        box.setWindowTitle("MP3 Плеер — диагностика")
        box.setWindowIcon(QIcon(resource("icon.ico")))
        box.setText("Что работает на этом компьютере. Кнопка «Копировать» — чтобы прислать.")
        box.setDetailedText(text)
        box.setInformativeText(text.split("\n\n")[1] if "\n\n" in text else text)
        copy = box.addButton("Копировать", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is copy:
            QApplication.clipboard().setText(text)

    # ------------------------------------------------------------ текст песни: поиск и привязка
    def load_lyrics(self, path, title="", artist=""):
        if not path:
            return None
        return load_for_track(path, self.cfg.get("lrc_links") or {}, self.cfg.get("lyrics_dirs") or [],
                              title or self.title, artist or self.artist,
                              int((self.cfg.get("lrc_offset") or {}).get(path, 0)))

    def reload_lyrics(self, note=True):
        p = self.loaded_path
        self.lyrics = self.load_lyrics(p) if (p and self.cfg.get("lyrics", True)) else None
        if note and p:
            self.flash(("ТЕКСТ: " + os.path.basename(self.lyrics.source)) if self.lyrics else "НЕТ .LRC ДЛЯ ТРЕКА", 2.5)

    def link_lrc_file(self, lrc):
        p = self.loaded_path
        if not p or not lrc:
            return
        ly = load_file(lrc)
        if not ly:
            self.flash("В ФАЙЛЕ НЕТ ТАЙМКОДОВ", 2.5)
            return
        links = self.cfg.setdefault("lrc_links", {})
        for k in [k for k in links if pkey(k) == pkey(p)]:
            links.pop(k)
        links[npath(p)] = npath(lrc)
        if not self.cfg.get("lyrics", True):
            self.cfg["lyrics"] = True
        self.reload_lyrics(note=False)
        self.flash("ПРИВЯЗАН: " + os.path.basename(lrc), 2.5)
        self.save_later()

    def pick_lrc_for_track(self):
        if not self.loaded_path:
            self.flash("СНАЧАЛА ВКЛЮЧИ ТРЕК")
            return
        start = (self.cfg.get("lyrics_dirs") or [os.path.dirname(self.loaded_path)])[0]
        f, _ = QFileDialog.getOpenFileName(None, "Текст для: " + os.path.basename(self.loaded_path),
                                           start, "Текст песни (*.lrc);;Все файлы (*.*)")
        if f:
            self.link_lrc_file(f)

    def unlink_lrc(self):
        links = self.cfg.get("lrc_links") or {}
        hits = [k for k in links if pkey(k) == pkey(self.loaded_path)]
        for k in hits:
            links.pop(k)
        if hits:
            self.reload_lyrics()
            self.save_later()

    def shift_lyrics(self, ms):
        p = self.loaded_path
        if not p:
            return
        offs = self.cfg.setdefault("lrc_offset", {})
        v = 0 if ms is None else int(offs.get(p, 0)) + ms
        if v:
            offs[p] = v
        else:
            offs.pop(p, None)
        self.reload_lyrics(note=False)
        self.flash(f"СДВИГ ТЕКСТА {v / 1000:+.1f} С" if v else "СДВИГ ТЕКСТА СБРОШЕН")
        self.save_later()

    def add_lyrics_dir(self):
        d = QFileDialog.getExistingDirectory(None, "Папка с файлами .lrc",
                                             (self.cfg.get("lyrics_dirs") or [""])[0])
        if d:
            dirs = self.cfg.setdefault("lyrics_dirs", [])
            if d not in dirs:
                dirs.append(d)
            LRC_INDEX.build(tuple(x for x in dirs if os.path.isdir(x)))
            self.flash(f".LRC В ПАПКАХ: {len(LRC_INDEX.files)}", 2.5)
            if self.lyrics is None:
                self.reload_lyrics(note=False)
            self.save_later()

    def remove_lyrics_dir(self, d):
        dirs = self.cfg.get("lyrics_dirs") or []
        if d in dirs:
            dirs.remove(d)
            LRC_INDEX.build(tuple(x for x in dirs if os.path.isdir(x)))
            self.reload_lyrics(note=False)
            self.save_later()

    def rescan_lyrics(self):
        LRC_INDEX.build(tuple(x for x in (self.cfg.get("lyrics_dirs") or []) if os.path.isdir(x)))
        self.reload_lyrics()

    # ------------------------------------------------------------ эквалайзер звука
    def eq_note(self):
        if not self.player.dsp_decks:
            return "Недоступен: " + (self.player.dsp_error or "нет PyAV")
        if self.radio:
            return "На радио эквалайзер не действует."
        return "Двойной клик по ползунку — в ноль. Esc — закрыть."

    def open_eq(self):
        if self.eq_panel is None:
            self.eq_panel = EqPanel(eqmod.LABELS, eqmod.PRESETS, eqmod.MAX_DB)
            self.eq_panel.changed.connect(self.on_eq_changed)
        e = self.player.equalizer
        self.eq_panel.set_state(e.gains, e.enabled, self.cfg.get("eq_preset", "Плоский"), self.eq_note())
        for sl in self.eq_panel.sliders:
            sl.setEnabled(bool(self.player.dsp_decks))
        self.eq_panel.open_near(self.visual_rect())

    def on_eq_changed(self, gains, enabled, preset):
        self._eq_pending = (gains, enabled, preset)
        self.eq_apply_timer.start()                   # ползунок тянут — пересчёт не чаще 16 раз/с

    def apply_eq_now(self):
        if not self._eq_pending:
            return
        gains, enabled, preset = self._eq_pending
        self._eq_pending = None
        self.set_eq(gains, enabled, preset, note=False)

    def set_eq(self, gains, enabled, preset, note=True):
        e = self.player.equalizer
        e.set(gains, enabled)
        self.cfg["eq_gains"], self.cfg["eq_on"], self.cfg["eq_preset"] = list(e.gains), e.enabled, preset
        want = e.active() and bool(self.player.dsp_decks)
        if want != self.player.use_dsp:
            self.player.use_dsp = want
            self.switch_audio_path()
        if note:
            self.flash(("EQ: " + preset.upper()) if e.active() else "EQ: ВЫКЛ", 2.0)
        self.save_later()

    def switch_audio_path(self):
        """Эквалайзер включили/выключили — перевести текущий трек на нужный путь звука
        с коротким кроссфейдом, с того же места."""
        path = self.loaded_path
        if not path or self.radio:
            return
        url = QUrl.fromLocalFile(path)
        if self.player.kind_for(url) == self.player.current_kind():
            return
        pos, playing = self.player.position(), self.is_playing()
        to_dsp = self.player.kind_for(url) == "dsp"
        self.pending_seek = pos if (pos > 0 and not to_dsp) else None   # свой путь мотает сразу
        self.player.load(url, autoplay=playing, fade_ms=500 if playing else 0, gain=self.track_gain(path))
        if pos > 0:
            self.player.setPosition(pos)

    def cycle_eq_preset(self):
        names = list(eqmod.PRESETS)
        cur = self.cfg.get("eq_preset", "Плоский")
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[1]
        self.set_eq(eqmod.PRESETS[nxt], nxt != "Плоский", nxt)

    # ------------------------------------------------------------ выравнивание громкости
    NORM_MODES = {"off": ("Выключено", None), "spotify": ("Как Spotify (−14 LUFS)", -14.0),
                  "quiet": ("Ровнее, но тише (−18 LUFS)", -18.0)}

    def norm_target(self):
        return self.NORM_MODES.get(self.cfg.get("normalize", "spotify"), (None, -14.0))[1]

    def track_gain(self, path):
        target = self.norm_target()
        if target is None or not path or self.loud_worker is None:
            return 1.0
        lufs = self.loud_cache.get(path)
        if lufs is None:                       # ещё не мерили — сразу в начало очереди,
            self.loud_worker.enqueue([path], front=True)
            if self.loud_gains:                # а пока — средняя поправка, чтобы не скакало
                return sum(self.loud_gains[-50:]) / len(self.loud_gains[-50:])
            return 1.0
        return loudness.gain_for(lufs, target)

    def queue_loudness(self):
        if self.loud_worker is None or self.norm_target() is None or self.radio or not self.files:
            return
        i = self.pos_in_order
        upcoming = [self.files[j] for j in self.order[i:] + self.order[:i]]     # ближайшие — первыми
        self.loud_worker.enqueue(upcoming[:3], front=True)
        self.loud_worker.enqueue(upcoming[3:])

    def on_loudness(self, path, lufs):
        target = self.norm_target()
        if target is None:
            return
        if lufs is not None:
            self.loud_gains.append(loudness.gain_for(lufs, target))
            del self.loud_gains[:-200]
        if self.loaded_path and pkey(path) == pkey(self.loaded_path) and not self.radio:
            self.player.set_gain(loudness.gain_for(lufs, target), glide_ms=2500)

    def set_normalize(self, mode):
        self.cfg["normalize"] = mode
        self.flash("ВЫРАВНИВАНИЕ: " + {"off": "ВЫКЛ", "spotify": "−14 LUFS", "quiet": "−18 LUFS"}[mode], 2.0)
        if not self.radio:
            self.player.set_gain(self.track_gain(self.loaded_path) if mode != "off" else 1.0, glide_ms=800)
        self.queue_loudness()
        self.save_later()

    def cycle_normalize(self):
        keys = list(self.NORM_MODES)
        cur = self.cfg.get("normalize", "spotify")
        self.set_normalize(keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else "spotify")

    def remeasure_current(self):
        p = self.loaded_path
        if not p or self.loud_worker is None or self.radio:
            return
        self.loud_cache.db.pop(loudness.LoudnessCache._key(p), None)
        self.loud_worker.enqueue([p], front=True)
        self.flash("МЕРЯЮ ГРОМКОСТЬ…")

    def build_normalize_menu(self, nm):
        nm.clear()
        cur = self.cfg.get("normalize", "spotify")
        grp = QActionGroup(nm)
        for key, (name, _t) in self.NORM_MODES.items():
            a = nm.addAction(name, lambda _=False, k=key: self.set_normalize(k))
            a.setCheckable(True)
            a.setChecked(key == cur)
            grp.addAction(a)
        nm.addSeparator()
        if self.loud_worker is None:
            nm.addAction("Недоступно (нет numpy)").setEnabled(False)
            return
        p = self.loaded_path
        if p and not self.radio:
            lufs = self.loud_cache.get(p)
            if lufs is None:
                txt = "Этот трек: ещё меряю…"
            elif lufs == "fail":
                txt = "Этот трек: не получилось измерить"
            else:
                g = loudness.gain_for(lufs, self.norm_target() or -14.0)
                txt = f"Этот трек: {lufs:.1f} LUFS → {20 * math.log10(g):+.1f} дБ"
            nm.addAction(txt).setEnabled(False)
            nm.addAction("Перемерить этот трек", self.remeasure_current)
        left = self.loud_worker.pending()
        nm.addAction(f"В очереди на замер: {left}" if left else "Все треки списка измерены").setEnabled(False)

    # ------------------------------------------------------------ тексты из интернета
    def fetch_lyrics_online(self, manual=False):
        path = self.loaded_path
        if not path or self.radio or not self.cfg.get("lyrics", True):
            return
        if not manual:
            if not self.cfg.get("lyrics_online", True) or self.lyrics is not None:
                return
            miss = (self.cfg.get("lyrics_missing") or {}).get(pkey(path))
            if miss and time.time() - miss < 7 * 24 * 3600:     # недавно не нашли — не долбим сайт
                return
        if pkey(path) in self.lyr_busy:
            return
        self.lyr_busy.add(pkey(path))
        if manual:
            self.flash("🌐 ИЩУ ТЕКСТ…", 2.0)
        artist, title, album = self.artist, self.title, self.album
        dur = max(0, self.player.duration()) / 1000.0
        fallback = os.path.join(os.path.dirname(self.settings_path), "lyrics")
        emit = self.lyr_sig.done.emit

        def work():
            try:
                text, why = lyrics_online.fetch(artist, title, album, dur)
                if not text:
                    emit(path, "", why, manual)
                    return
                saved = lyrics_online.save(text, path, artist, title, album, fallback)
                emit(path, saved, why, manual)
            except Exception as e:
                emit(path, "", f"нет связи с lrclib.net ({type(e).__name__})", manual)
        threading.Thread(target=work, daemon=True).start()

    def on_lyrics_online(self, path, lrc, why, manual):
        self.lyr_busy.discard(pkey(path))
        misses = self.cfg.setdefault("lyrics_missing", {})
        if not lrc:
            if "нет связи" not in why:
                misses[pkey(path)] = time.time()
                while len(misses) > 2000:
                    misses.pop(next(iter(misses)))
            if manual:
                self.flash(("🌐 " + why).upper(), 3.0)
            self.save_later()
            return
        misses.pop(pkey(path), None)
        if pkey(os.path.dirname(lrc)) != pkey(os.path.dirname(path)):   # не рядом с треком — привязать
            links = self.cfg.setdefault("lrc_links", {})
            for k in [k for k in links if pkey(k) == pkey(path)]:
                links.pop(k)
            links[npath(path)] = npath(lrc)
        self.save_later()
        if self.loaded_path and pkey(self.loaded_path) == pkey(path):
            self.reload_lyrics(note=False)
            if self.lyrics:
                self.flash("🌐 ТЕКСТ НАЙДЕН", 2.0)

    def toggle_lyrics_online(self):
        self.cfg["lyrics_online"] = not self.cfg.get("lyrics_online", True)
        self.flash("ТЕКСТЫ ИЗ СЕТИ: " + ("ВКЛ" if self.cfg["lyrics_online"] else "ВЫКЛ"))
        if self.cfg["lyrics_online"]:
            self.fetch_lyrics_online(manual=False)
        self.save_later()

    def build_lyrics_menu(self):
        m = self.lyrics_menu
        m.clear()
        a = m.addAction("Показывать текст на экране", self.toggle_lyrics)
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("lyrics", True)))
        a = m.addAction("Искать тексты в интернете сам (если .lrc нет)", self.toggle_lyrics_online)
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("lyrics_online", True)))
        m.addSeparator()
        p = self.loaded_path
        if p:
            if self.lyrics:
                linked = any(pkey(k) == pkey(p) for k in (self.cfg.get("lrc_links") or {}))
                info = m.addAction(("📌 " if linked else "🔍 ") + "Сейчас: " + os.path.basename(self.lyrics.source))
                info.setToolTip(self.lyrics.source)
            else:
                info = m.addAction("Для этого трека текст не найден")
            info.setEnabled(False)
            if not self.radio:
                m.addAction("🌐  Найти текст в интернете (lrclib.net)" if not self.lyrics
                            else "🌐  Поискать другой текст в интернете",
                            lambda: self.fetch_lyrics_online(manual=True))
            m.addAction("📎  Привязать .lrc к этому треку…   (или перетащи .lrc на плеер)",
                        self.pick_lrc_for_track)
            if any(pkey(k) == pkey(p) for k in (self.cfg.get("lrc_links") or {})):
                m.addAction("✖  Отвязать .lrc", self.unlink_lrc)
            m.addSeparator()
            off = int((self.cfg.get("lrc_offset") or {}).get(p, 0))
            m.addAction("⏪  Текст раньше на 0,5 с", lambda: self.shift_lyrics(+500))
            m.addAction("⏩  Текст позже на 0,5 с", lambda: self.shift_lyrics(-500))
            if off:
                m.addAction(f"Сбросить сдвиг (сейчас {off / 1000:+.1f} с)", lambda: self.shift_lyrics(None))
            m.addSeparator()
        h = m.addAction("Папки с текстами:")
        h.setEnabled(False)
        for d in self.cfg.get("lyrics_dirs") or []:
            a = m.addAction(("    ✖  " if os.path.isdir(d) else "    ⚠  ") + (os.path.basename(d.rstrip("/\\")) or d),
                            lambda _=False, d=d: self.remove_lyrics_dir(d))
            a.setToolTip("Убрать из списка: " + d)
        m.addAction("    ➕  Добавить папку с .lrc…", self.add_lyrics_dir)
        if self.cfg.get("lyrics_dirs"):
            m.addAction("    ⟳  Пересканировать", self.rescan_lyrics)

    def toggle_lyrics(self):
        self.toggle_cfg("lyrics", "ТЕКСТ ПЕСНИ")
        p = self.loaded_path
        self.lyrics = self.load_lyrics(p) if (p and self.cfg.get("lyrics", True)) else None
        if self.cfg.get("lyrics", True) and p and not self.lyrics:
            self.flash("НЕТ .LRC ФАЙЛА")
            self.fetch_lyrics_online(manual=False)

    def replay_boot(self):
        self._boot_sound_done = False
        self.boot_t0 = time.monotonic()
        self.light_until = self.boot_t0 + BOOT_LEN + 2.5

    def toggle_fade_pause(self):
        self.cfg["fade_pause"] = not self.cfg.get("fade_pause", True)
        self.flash("ЗАТУХАНИЕ: ВКЛ" if self.cfg["fade_pause"] else "ЗАТУХАНИЕ: ВЫКЛ")
        self.save_later()

    def set_crossfade(self, ms):
        self.cfg["crossfade"] = ms
        self.player.set_crossfade(ms)
        self.flash(f"ПЕРЕХОД: {ms // 1000} СЕК" if ms else "ПЕРЕХОД: ВЫКЛ")
        self.save_later()

    def toggle_resume(self):
        on = not self.cfg.get("resume_long", True)
        self.cfg["resume_long"] = on
        if not on:
            self.cfg.pop("resume", None)
        self.flash("ЗАКЛАДКИ: ВКЛ" if on else "ЗАКЛАДКИ: ВЫКЛ")
        self.save_later()

    def set_toast_mode(self, mode):
        self.cfg["toast"] = mode
        if mode == "off":
            self.toast.hide()
        self.save_later()

    def toggle_cover_popup(self):
        on = not self.cfg.get("cover_popup", True)
        self.cfg["cover_popup"] = on
        if not on:
            self.cover_popup.hide()
        self.flash("ОБЛОЖКА: ВКЛ" if on else "ОБЛОЖКА: ВЫКЛ")
        self.save_later()

    def toggle_hotkeys(self):
        on = not self.cfg.get("hotkeys", True)
        self.cfg["hotkeys"] = on
        if on:
            self.hotkeys.register(HOTKEYS)
            busy = len(self.hotkeys.failed)
            self.flash("КЛАВИШИ: ВКЛ" + (f" ({busy} ЗАНЯТО)" if busy else ""))
        else:
            self.hotkeys.unregister(HOTKEYS)
            self.flash("КЛАВИШИ: ВЫКЛ")
        self.save_later()

    def toggle_autostart(self):
        try:
            Autostart.set(not Autostart.enabled())
        except Exception as e:
            self.flash("НЕ ВЫШЛО")
            print(e)

    def quit(self):
        if (self.bye_t0 is None and self.isVisible() and self.cfg.get("boot_anim", True)):
            self.bye_t0 = time.monotonic()
            self.light_until = 0
            if self.is_playing():
                self.player.pause(600)
            QTimer.singleShot(int(BYE_LEN * 1000), self._quit_now)
            return
        self._quit_now()

    def _quit_now(self):
        if self.loud_worker is not None:
            self.loud_worker.stop()
            self.loud_cache.save()
        if self.upd_ready and self.cfg.get("auto_update", True):
            self.install_update(restart=False)          # скачанная сборка — при выходе
        self.write_nowplaying(closing=True)
        self.keeper.close()
        self.mini_keeper.close()
        self.mini.hide()
        self.save_cfg()
        self.hotkeys.unregister()
        self.sysmedia.close()
        self.cover_popup.hide()
        self.playlist_panel.hide()
        self.toast.hide()
        self.tray.hide()
        QApplication.quit()


def _log_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "error.log")


def _log_exception(exc_type, exc, tb):
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("\n=== %Y-%m-%d %H:%M:%S ===\n") + text)
    except Exception:
        pass
    return text


def _fatal_box(text):
    msg = ("MP3 Плеер не смог запуститься.\n\n" + text[-1500:] +
           "\n\nПодробности: " + _log_path())
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg)


def _check_dsp(out_path):
    """MP3Player.exe --check-dsp файл — проверка сборки: есть ли PyAV и эквалайзер."""
    try:
        import dsp_player
        ok = dsp_player.available()
        msg = "ok" if ok else ("PyAV: " + (dsp_player.AV_ERROR or "нет numpy"))
    except Exception as e:
        ok, msg = False, f"{type(e).__name__}: {e}"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(msg)
    return 0 if ok else 1


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--check-dsp":
        return _check_dsp(sys.argv[2])
    # ошибки во время работы — в лог, а не молча
    sys.excepthook = lambda t, e, tb: (_log_exception(t, e, tb), sys.__excepthook__(t, e, tb))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("misafumka.mp3player")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    os.makedirs(data_dir, exist_ok=True)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    server_name = APP_NAME + "-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")

    # файлы/папки из командной строки (двойной клик по MP3, «Открыть с помощью», меню проводника)
    shell_paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("-") and os.path.exists(a)]
    message = ("open\n" + "\n".join(shell_paths)).encode("utf-8") if shell_paths else b"show"

    lock = QLockFile(os.path.join(data_dir, "running.lock"))
    if not lock.tryLock(100):
        # уже запущен — передать ему файлы (или попросить показаться) и выйти.
        # Проводник при выделении нескольких файлов запускает нас несколько раз
        # одновременно, поэтому даём главному экземпляру время подняться.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            sock = QLocalSocket()
            sock.connectToServer(server_name)
            if sock.waitForConnected(400):
                sock.write(message)
                sock.flush()
                sock.waitForBytesWritten(1000)
                sock.disconnectFromServer()
                return 0
            if lock.tryLock(0):          # хозяин замка исчез — становимся главными
                break
            time.sleep(0.2)
        else:
            lock.removeStaleLockFile()
            if not lock.tryLock(100):
                _fatal_box("Плеер уже запущен, но не отвечает.\n"
                           "Закрой MP3Player в Диспетчере задач и запусти снова.")
                return 1

    # сервер поднимаем сразу — до долгой загрузки окна; что придёт раньше — копим
    inbox = list(shell_paths)
    holder = {"w": None}
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    flush_timer = QTimer(singleShot=True, interval=450)    # собрать файлы от «пачки» запусков

    def flush_inbox():
        w = holder["w"]
        if w is None:
            return
        paths, inbox[:] = list(inbox), []
        if paths:
            w.open_from_shell(paths)
        else:
            w.show_player()
    flush_timer.timeout.connect(flush_inbox)

    def on_message(data):
        text = bytes(data).decode("utf-8", "replace")
        if text.startswith("open\n"):
            inbox.extend(p for p in text[5:].split("\n") if p.strip())
        flush_timer.start()

    if server.listen(server_name):
        def on_conn():
            while server.hasPendingConnections():
                c = server.nextPendingConnection()
                buf = bytearray()
                c.readyRead.connect(lambda c=c, buf=buf: buf.extend(bytes(c.readAll())))
                c.disconnected.connect(lambda c=c, buf=buf: (buf.extend(bytes(c.readAll())),
                                                             on_message(buf), c.deleteLater()))
        server.newConnection.connect(on_conn)

    try:
        w = Mp3Widget(os.path.join(data_dir, "settings.json"), skip_restore=bool(shell_paths))
    except Exception:
        _fatal_box(_log_exception(*sys.exc_info()))
        return 1
    holder["w"] = w
    if inbox:
        flush_inbox()                      # файл из проводника — играть сразу

    w.show()
    w.raise_()

    def first_show():
        if w.cfg.get("mini"):
            w.set_mini(True)                   # закрыли в мини-режиме — в нём и открываемся
            return
        WinDesktop.bring_front(w)
        if w.on_desktop:
            WinDesktop.peek_topmost(w, 4500)
    QTimer.singleShot(200, first_show)
    if "--updated" in sys.argv and w.build:
        QTimer.singleShot(int(BOOT_LEN * 1000) + 600, lambda: w.flash(f"⬆ СБОРКА {w.build}", 3.0))
    app.aboutToQuit.connect(w.save_cfg)
    rc = app.exec()
    lock.unlock()
    return rc


if __name__ == "__main__":
    sys.exit(main())
