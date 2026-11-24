# -*- coding: utf-8 -*-
"""Поверх игры: текст песни и карточка трека в стиле Persona 3 Reload.

Оба окна прозрачные, «насквозь» для мыши и никогда не забирают фокус — игра продолжает получать
клавиатуру и мышь. Показываются только когда игра идёт окном на весь экран (без рамки): поверх
«настоящего» полноэкранного Direct3D окно всё равно не видно, а попытка показать его может свернуть игру.
"""
import time

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import (QPainter, QColor, QFont, QFontMetricsF, QPainterPath, QPolygonF, QPen,
                           QLinearGradient, QGuiApplication, QCursor, QImage)
from PySide6.QtWidgets import QWidget

NAVY = QColor("#061331")
NAVY2 = QColor("#0b2257")
CYAN = QColor("#2fd3ff")
ICE = QColor("#bff4ff")
WHITE = QColor("#f4f9ff")
FONT = "Segoe UI"

_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
          | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
          | Qt.WindowType.WindowTransparentForInput)


def _font(px, italic=True, weight=QFont.Weight.Black):
    f = QFont(FONT)
    f.setPixelSize(max(1, int(px)))
    f.setItalic(italic)
    f.setWeight(weight)
    return f


def _skew(r: QRectF, k=None):
    """Параллелограмм с наклоном вправо — фирменная плашка меню P3R."""
    k = r.height() * 0.35 if k is None else k
    return QPolygonF([QPointF(r.left() + k, r.top()), QPointF(r.right(), r.top()),
                      QPointF(r.right() - k, r.bottom()), QPointF(r.left(), r.bottom())])


def _slant(x, top, w, h, k):
    """Полоса шириной w с тем же наклоном, что у плашки высотой h со сдвигом k (верх смещён вправо)."""
    return QPolygonF([QPointF(x + k, top), QPointF(x + k + w, top), QPointF(x + w, top + h), QPointF(x, top + h)])


def _fit_font(text, width, sizes, **kw):
    """Самый крупный шрифт из sizes, в который текст помещается целиком (или самый мелкий)."""
    f = None
    for px in sizes:
        f = _font(px, **kw)
        if QFontMetricsF(f).horizontalAdvance(text) <= width:
            return f
    return f


def game_screen():
    """Экран, на котором игра: курсор в игре почти всегда внутри её окна."""
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def _no_activate(w: QWidget):
    """Windows: окно не становится активным и не попадает в Alt+Tab (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)."""
    try:
        import sys
        if sys.platform != "win32":
            return
        import ctypes
        hwnd = int(w.winId())
        u = ctypes.windll.user32
        GWL_EXSTYLE = -20
        u.GetWindowLongW.restype = ctypes.c_long
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | 0x08000000 | 0x00000080 | 0x00000020)
    except Exception:
        pass


class _OverlayBase(QWidget):
    CLICK_THROUGH_QT = True      # «насквозь для мыши» силами Qt (у текста — только через Windows, см. GameLyrics)

    def __init__(self):
        flags = _FLAGS if self.CLICK_THROUGH_QT else (_FLAGS & ~Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._styled = False
        self._fading_out = False
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._fade_done)

    def target_opacity(self):
        return 1.0

    def fade_in(self, ms=260):
        """Показать с плавным проявлением (или вернуть, если как раз гасло)."""
        appearing = not self.isVisible() or self._fading_out
        self._fading_out = False
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self._show_quiet()
        if appearing:
            self.fade.stop()
            self.fade.setDuration(ms)
            self.fade.setStartValue(self.windowOpacity())
            self.fade.setEndValue(self.target_opacity())
            self.fade.start()
        elif self.fade.state() != QPropertyAnimation.State.Running:
            self.setWindowOpacity(self.target_opacity())

    def fade_out(self, ms=380):
        """Плавно погасить и спрятать (Alt+Tab из игры, конец строки)."""
        if not self.isVisible() or self._fading_out:
            return
        self._fading_out = True
        self.fade.stop()
        self.fade.setDuration(ms)
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_done(self):
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self._after_hide()

    def _after_hide(self):
        pass

    def _show_quiet(self):
        if not self._styled:
            self.winId()
            _no_activate(self)
            self._styled = True
        if not self.isVisible():
            self.show()
        self.raise_()                                      # игра могла оказаться выше — без активации


# =====================================================================================
class GameLyrics(_OverlayBase):
    """Строка текста песни внизу экрана поверх игры: текущая — крупно, следующая — бледнее.
    Смена строки — короткий «выезд» скошенной плашки, как подписи в P3R."""
    W, H = 760, 92
    # Qt-флаг «насквозь для мыши» Qt держит сам (на каждый клик отвечает Windows «здесь окна нет»), и снять
    # его на время Ctrl нельзя без пересоздания окна. Поэтому у текста насквозь только через стиль окна
    # Windows (WS_EX_TRANSPARENT), а его уже можно снимать и ставить обратно.
    CLICK_THROUGH_QT = False
    moved = Signal(float, float)         # центр окна в долях экрана (0..1) — после перетаскивания
    scaled = Signal(float)               # новый размер (1.0 — обычный) — после Ctrl + колёсико
    lock_toggled = Signal(bool)          # Ctrl+Alt: закрепили / открепили

    def __init__(self):
        super().__init__()
        self.k = 1.0                     # размер (Ctrl + колёсико)
        self.pos_frac = None             # (x, y) центра в долях экрана, если перетаскивали
        self.setFixedSize(self.W, self.H)
        self.cur = self.nxt = ""
        self.title = ""
        self.opacity = 0.7               # общая прозрачность (настройка «Прозрачность текста»)
        self._changed = 0.0
        self.anim = QTimer(self, interval=16)
        self.anim.timeout.connect(self._frame)
        # Ctrl зажат — окно ловит мышь: тащить и менять размер. Отпустил — снова насквозь.
        self.edit = False
        self._drag = None
        self.locked = False              # Ctrl+Alt: закрепить — Ctrl больше не включает перетаскивание
        self._combo = None               # (зажаты Ctrl+Alt, нажималась ли ещё какая-то клавиша)
        self.ctrl_poll = QTimer(self, interval=40)
        self.ctrl_poll.timeout.connect(self._poll_ctrl)

    # ------------------------------------------------------------ место и размер
    def set_scale(self, k):
        self.k = max(0.6, min(2.2, round(float(k), 2)))
        self.setFixedSize(int(self.W * self.k), int(self.H * self.k))
        self.place()
        self.update()

    def place(self, screen=None):
        if self._drag is not None:
            return                       # тащат мышью — не мешаем
        g = (screen or game_screen()).geometry()
        w, h = self.width(), self.height()
        if self.pos_frac:
            cx, cy = g.x() + g.width() * self.pos_frac[0], g.y() + g.height() * self.pos_frac[1]
            x = int(min(max(cx - w / 2, g.x()), g.x() + g.width() - w))
            y = int(min(max(cy - h / 2, g.y()), g.y() + g.height() - h))
            self.move(x, y)
        else:
            self.move(g.x() + (g.width() - w) // 2, g.y() + g.height() - h - int(g.height() * 0.08))

    @staticmethod
    def _key(vk):
        try:
            import sys
            if sys.platform != "win32":
                return False
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    @classmethod
    def _ctrl_down(cls):
        return cls._key(0x11)

    # клавиши, которые «портят» чистое Ctrl+Alt: буквы, цифры, стрелки, F-клавиши, медиа, пробел и т. п.
    _OTHER_KEYS = tuple(range(0x08, 0x10)) + tuple(range(0x13, 0x5B)) + tuple(range(0x60, 0x88)) + \
        tuple(range(0xA6, 0xB8)) + tuple(range(0xBA, 0xE5))

    def _poll_ctrl(self):
        """Раз в 40 мс, пока идёт игра: Ctrl — режим перетаскивания (если не закреплено),
        чистое Ctrl+Alt (нажал и отпустил без других клавиш) — закрепить / открепить."""
        ctrl, alt = self._ctrl_down(), self._key(0x12)
        if ctrl and alt:
            other = any(self._key(vk) for vk in self._OTHER_KEYS if vk not in (0x10, 0x11, 0x12))
            if self._combo is None:
                self._combo = other
            elif other:
                self._combo = True
        elif self._combo is not None:
            clean = not self._combo
            self._combo = None
            if clean:
                self.locked = not self.locked
                self.lock_toggled.emit(self.locked)
        want = self.isVisible() and not self.locked and ((ctrl and not alt) or self._drag is not None)
        if want != self.edit:
            self._set_edit(want)

    def _set_edit(self, on):
        """Включить / выключить «ловлю мыши» (WS_EX_TRANSPARENT снимается только пока зажат Ctrl)."""
        if on == self.edit:
            return
        self.edit = on
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)
        try:
            import sys
            if sys.platform == "win32":
                import ctypes
                u = ctypes.windll.user32
                hwnd = int(self.winId())
                u.GetWindowLongW.restype = ctypes.c_long
                ex = u.GetWindowLongW(hwnd, -20)
                ex = (ex & ~0x20) if on else (ex | 0x20)          # WS_EX_TRANSPARENT
                u.SetWindowLongW(hwnd, -20, ex)
        except Exception:
            pass
        if on:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()

    def mousePressEvent(self, e):
        if self.edit and e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            g = game_screen().geometry()
            c = self.frameGeometry().center()
            self.pos_frac = (min(1.0, max(0.0, (c.x() - g.x()) / max(1, g.width()))),
                             min(1.0, max(0.0, (c.y() - g.y()) / max(1, g.height()))))
            self.moved.emit(*self.pos_frac)

    def wheelEvent(self, e):
        if not self.edit:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        if d:
            c = self.frameGeometry().center()
            self.set_scale(self.k + (0.1 if d > 0 else -0.1))
            if self.pos_frac is None:                          # размер меняется вокруг центра, не прыгая
                g = game_screen().geometry()
                self.pos_frac = ((c.x() - g.x()) / max(1, g.width()), (c.y() - g.y()) / max(1, g.height()))
                self.place()
            self.scaled.emit(self.k)
            e.accept()

    def set_lines(self, cur, nxt, title=""):
        cur, nxt = (cur or "").strip(), (nxt or "").strip()
        if (cur, nxt) == (self.cur, self.nxt) and self.isVisible() and not self._fading_out:
            return
        changed = cur != self.cur
        self.cur, self.nxt, self.title = cur, nxt, title
        if changed:
            self._changed = time.monotonic()
            self.anim.start()
        self.place()
        self.fade_in()
        self.update()

    def notice(self, text):
        """Короткая подпись (например, «текст поверх игры выключен») — видна, даже если сам текст выключен."""
        self.cur, self.nxt = "", ""
        self.set_lines(text, "")

    def target_opacity(self):
        return max(0.2, min(1.0, self.opacity))

    def clear(self, fade=True):
        """Спрятать: плавно (по умолчанию) или сразу (выход из программы).
        Пока зажат Ctrl (двигают / меняют размер) — плавно не прячем."""
        if fade and (self.edit or self._drag is not None) and self.isVisible():
            return
        if fade and self.isVisible():
            self.fade_out()
            return
        self.fade.stop()
        self._fading_out = False
        self.anim.stop()
        if self.isVisible():
            self.hide()
        self.cur = self.nxt = ""

    def _after_hide(self):
        self.anim.stop()
        self.cur = self.nxt = ""

    def _frame(self):
        if time.monotonic() - self._changed > 0.35:
            self.anim.stop()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        t = min(1.0, (time.monotonic() - self._changed) / 0.35)
        ease = 1 - (1 - t) ** 3
        p.scale(self.k, self.k)                  # рисуем в обычном размере, окно увеличено / уменьшено
        W, H = self.W, self.H
        if self.edit:                            # зажат Ctrl: рамка и подсказка
            fr = QColor(CYAN)
            fr.setAlpha(200)
            p.setPen(QPen(fr, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QColor(6, 19, 49, 70))
            p.drawRect(QRectF(1, 1, W - 2, H - 2))
            p.setFont(_font(11, italic=False, weight=QFont.Weight.Bold))
            p.setPen(ICE)
            p.drawText(QRectF(8, -1, W - 16, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"Ctrl + мышь — двигать  ·  Ctrl + колёсико — размер ({int(round(self.k * 100))} %)")

        # подложка-полоса: тёмная скошенная, чтобы текст читался на любом кадре игры
        if self.cur:
            f = _fit_font(self.cur, W - 120, (26, 23, 21, 19, 17))
            fm = QFontMetricsF(f)
            text = fm.elidedText(self.cur, Qt.TextElideMode.ElideRight, W - 120)
            tw = fm.horizontalAdvance(text)
            bw = tw + 80
            x0 = (W - bw) / 2 + (1 - ease) * 40
            band = QRectF(x0, 10, bw, 42)
            c = QColor(NAVY)
            c.setAlpha(int(140 * ease))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(_skew(band, 14))
            acc = QColor(CYAN)
            acc.setAlpha(int(220 * ease))
            p.setBrush(acc)
            p.drawPolygon(_slant(band.left(), band.top(), 5, band.height(), 14))      # голубая кромка плашки
            p.drawPolygon(_skew(QRectF(band.left() + 22, band.bottom() - 3, bw * 0.55, 3), 1.5))
            # сам текст: белый курсив с тонкой голубой тенью
            path = QPainterPath()
            path.addText(QPointF(x0 + 40, band.top() + 21 + (fm.ascent() - fm.descent()) / 2), f, text)
            sh = QColor(CYAN)
            sh.setAlpha(int(110 * ease))
            p.save()
            p.translate(2, 2)
            p.fillPath(path, sh)
            p.restore()
            p.setPen(QPen(QColor(3, 10, 31, int(200 * ease)), 2.2))
            p.drawPath(path)
            wc = QColor(WHITE)
            wc.setAlpha(int(255 * ease))
            p.fillPath(path, wc)
        if self.nxt:
            f2 = _font(15, weight=QFont.Weight.DemiBold)
            fm2 = QFontMetricsF(f2)
            text2 = fm2.elidedText(self.nxt, Qt.TextElideMode.ElideRight, W - 200)
            tw2 = fm2.horizontalAdvance(text2)
            r2 = QRectF((W - tw2 - 44) / 2 + 22, 60, tw2 + 44, 24)
            c2 = QColor(NAVY)
            c2.setAlpha(95)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c2)
            p.drawPolygon(_skew(r2, 8))
            p.setFont(f2)
            p.setPen(ICE)
            p.drawText(r2, Qt.AlignmentFlag.AlignCenter, text2)
        p.end()


# =====================================================================================
class GameTrackCard(_OverlayBase):
    """Карточка «Дальше играет» после дослушанного трека: обложка в голубой раме, название и исполнитель
    на скошенных плашках. Выезжает слева, держится пару секунд и растворяется."""
    W, H = 560, 190
    HOLD = 3.2                       # сколько секунд видна целиком
    IN, OUT = 0.45, 0.6

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.W, self.H)
        self.image = None
        self.title = self.artist = self.done = ""
        self._t0 = 0.0
        self.anim = QTimer(self, interval=16)
        self.anim.timeout.connect(self._frame)

    def show_track(self, image, title, artist, finished=""):
        self.image = image if (isinstance(image, QImage) and not image.isNull()) else None
        self.title, self.artist, self.done = title or "", artist or "", finished or ""
        g = game_screen().geometry()
        self.move(g.x() + int(g.width() * 0.035), g.y() + int(g.height() * 0.08))
        self._t0 = time.monotonic()
        self.fade.stop()
        self._fading_out = False
        self.setWindowOpacity(1.0)
        self._show_quiet()
        self.anim.start()
        self.update()

    def cancel(self, fade=False):
        """Убрать карточку: сразу или плавно (Alt+Tab из игры)."""
        if fade and self.isVisible():
            self.fade_out(300)
            return
        self.fade.stop()
        self._fading_out = False
        self.anim.stop()
        if self.isVisible():
            self.hide()

    def _after_hide(self):
        self.anim.stop()

    def _phase(self):
        t = time.monotonic() - self._t0
        if t < self.IN:
            return "in", t / self.IN
        if t < self.IN + self.HOLD:
            return "hold", 1.0
        if t < self.IN + self.HOLD + self.OUT:
            return "out", (t - self.IN - self.HOLD) / self.OUT
        return "gone", 1.0

    def _frame(self):
        ph, k = self._phase()
        if ph == "gone":
            self.cancel()
            return
        # окно рисуется только пока что-то движется: в «hold» хватает редких кадров
        self.anim.setInterval(16 if ph != "hold" else 120)
        if ph == "out" and not self._fading_out:
            self.setWindowOpacity(1.0 - k)
        self.update()

    def paintEvent(self, _e):
        ph, k = self._phase()
        ease = 1 - (1 - k) ** 3 if ph == "in" else 1.0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        dx = (1 - ease) * -220                         # выезд слева
        p.translate(dx, 0)
        alpha = ease

        # основная плашка: полупрозрачный тёмно-синий параллелограмм с градиентом
        body = QRectF(40, 26, self.W - 70, self.H - 52)
        g = QLinearGradient(body.topLeft(), body.bottomRight())
        a1, a2 = QColor(NAVY2), QColor(NAVY)
        a1.setAlpha(int(215 * alpha))
        a2.setAlpha(int(190 * alpha))
        g.setColorAt(0, a1)
        g.setColorAt(1, a2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPolygon(_skew(body, 46))
        # голубой «срез» слева и полоса снизу
        c = QColor(CYAN)
        c.setAlpha(int(255 * alpha))
        p.setBrush(c)
        p.drawPolygon(_slant(body.left(), body.top(), 7, body.height(), 46))      # голубая кромка плашки
        p.drawPolygon(_skew(QRectF(body.left() + 150, body.bottom() - 6, body.width() * 0.62, 6), 3))

        # обложка: квадрат в голубой раме, слегка повёрнут — как портреты в меню P3R
        art = QRectF(60, 34, 122, 122)
        p.save()
        p.translate(art.center())
        p.rotate(-4)
        p.translate(-art.center())
        frame = art.adjusted(-5, -5, 5, 5)
        p.setBrush(QColor(255, 255, 255, int(235 * alpha)))
        p.drawRect(frame)
        p.setBrush(c)
        p.drawRect(frame.adjusted(2, 2, -2, -2))
        if self.image is not None:
            img = self.image.scaled(int(art.width() * 2), int(art.height() * 2),
                                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
            src = QRectF((img.width() - art.width() * 2) / 2, (img.height() - art.height() * 2) / 2,
                         art.width() * 2, art.height() * 2)
            p.setOpacity(alpha)
            p.drawImage(art, img, src)
            p.setOpacity(1.0)
        else:                                          # обложки нет — нота на тёмном фоне
            p.setBrush(QColor(3, 10, 31, int(240 * alpha)))
            p.drawRect(art)
            p.setPen(QColor(CYAN.red(), CYAN.green(), CYAN.blue(), int(255 * alpha)))
            p.setFont(_font(64, italic=False))
            p.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")
        p.restore()

        tx = 205
        right = self.W - 60
        # заголовок-ярлык: белая скошенная плашка с тёмным текстом
        tag = "NOW PLAYING"
        tf = _font(15)
        tfm = QFontMetricsF(tf)
        tr = QRectF(tx, 40, tfm.horizontalAdvance(tag) + 34, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(240 * alpha)))
        p.drawPolygon(_skew(tr, 8))
        p.setFont(tf)
        p.setPen(QColor(6, 19, 49, int(255 * alpha)))
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter, tag)

        # название — крупно, курсивом; исполнитель — голубым
        name = self.title or "Без названия"
        f = _fit_font(name, right - tx, (30, 26, 23, 20))
        fm = QFontMetricsF(f)
        title = fm.elidedText(name, Qt.TextElideMode.ElideRight, right - tx)
        path = QPainterPath()
        path.addText(QPointF(tx, 104), f, title)
        p.setPen(QPen(QColor(3, 10, 31, int(220 * alpha)), 3))
        p.drawPath(path)
        p.fillPath(path, QColor(WHITE.red(), WHITE.green(), WHITE.blue(), int(255 * alpha)))
        if self.artist:
            af = _font(19, weight=QFont.Weight.Bold)
            p.setFont(af)
            p.setPen(QColor(CYAN.red(), CYAN.green(), CYAN.blue(), int(255 * alpha)))
            p.drawText(QPointF(tx + 8, 132), QFontMetricsF(af).elidedText(self.artist, Qt.TextElideMode.ElideRight,
                                                                             right - tx - 8))
        if self.done:
            df = _font(13, weight=QFont.Weight.DemiBold)
            p.setFont(df)
            p.setPen(QColor(ICE.red(), ICE.green(), ICE.blue(), int(200 * alpha)))
            p.drawText(QPointF(tx + 16, 155), QFontMetricsF(df).elidedText("дослушано: " + self.done,
                                                                              Qt.TextElideMode.ElideRight,
                                                                              right - tx - 30))
        p.end()


# =====================================================================================
class VolumeBadge(_OverlayBase):
    """Плашка громкости плеера (Ctrl + ползунок громкости): «MP3» и полоса из скошенных делений.
    Видна поверх всего, клики насквозь, через секунду с небольшим гаснет."""
    W, H = 300, 74
    HOLD_MS = 1300

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.W, self.H)
        self.level = 0.0
        self.muted = False
        self.hide_timer = QTimer(self, singleShot=True)
        self.hide_timer.timeout.connect(lambda: self.fade_out(320))

    def show_level(self, level, muted=False):
        self.level, self.muted = max(0.0, min(1.0, float(level))), bool(muted)
        g = game_screen().geometry()
        self.move(g.x() + g.width() - self.W - int(g.width() * 0.03), g.y() + int(g.height() * 0.06))
        self.fade_in(160)
        self.update()
        self.hide_timer.start(self.HOLD_MS)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        body = QRectF(14, 8, self.W - 22, self.H - 16)
        bg = QColor(NAVY)
        bg.setAlpha(215)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawPolygon(_skew(body, 18))
        p.setBrush(CYAN)
        p.drawPolygon(_slant(body.left(), body.top(), 5, body.height(), 18))      # голубая кромка плашки
        # ярлык «MP3»
        tag = QRectF(body.left() + 22, body.top() + 9, 54, 22)
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawPolygon(_skew(tag, 7))
        p.setFont(_font(14))
        p.setPen(NAVY)
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, "MP3")
        # число
        p.setFont(_font(22))
        p.setPen(QColor(255, 120, 140) if self.muted else WHITE)
        txt = "ВЫКЛ" if self.muted else f"{int(round(self.level * 100))}%"
        p.drawText(QRectF(tag.right() + 8, body.top() + 2, 120, 34),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        # полоса из 20 скошенных делений
        n, x0, y0, w, h, gap = 20, body.left() + 26, body.bottom() - 20, 9.0, 11.0, 2.4
        lit = 0 if self.muted else int(round(self.level * n))
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            c = QColor(CYAN) if i < lit else QColor(255, 255, 255, 45)
            p.setBrush(c)
            p.drawPolygon(_slant(x0 + i * (w + gap), y0, w, h, 3))
        p.end()
