# -*- coding: utf-8 -*-
"""Всплывающие окна плеера: плейлист с поиском и карточка с обложкой."""
import os

from PySide6.QtCore import (Qt, Signal, QTimer, QRect, QRectF, QPoint, QPointF,
                            QParallelAnimationGroup,
                            QPropertyAnimation, QEasingCurve, QEvent,
                            QCoreApplication)
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QFont, QFontMetrics,
                           QLinearGradient, QGuiApplication, QPen)
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QAbstractItemView)

BG = "#1b2124"
BORDER = "#4b5a60"
TEXT = "#d9e6e0"
DIM = "#7f918b"
LCD = "#96aba4"          # акцент (цвет ЖК) — меняется вместе с темой подсветки
LCD_INK = "#1a2421"
LCD_LIT = "#b0d8c4"
_live_panels = []        # окна, которые надо перекрасить при смене темы


def set_theme(lcd, ink, lit):
    """Сменить акцентные цвета всех всплывающих окон (hex-строки)."""
    global LCD, LCD_INK, LCD_LIT
    LCD, LCD_INK, LCD_LIT = lcd, ink, lit
    for w in list(_live_panels):
        try:
            w.restyle()
            w.update()
        except RuntimeError:
            _live_panels.remove(w)
FONTS = '"Consolas", "Lucida Console", "Courier New", monospace'


def place_beside(anchor: QRect, size, prefer_left=True, align_y=None):
    """Позиция окна рядом с плеером так, чтобы не вылезти за экран."""
    scr = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
    g = scr.availableGeometry()
    w, h = size.width(), size.height()
    gap = 10
    left_x = anchor.left() - w - gap
    right_x = anchor.right() + gap
    if prefer_left:
        x = left_x if left_x >= g.left() else right_x
    else:
        x = right_x if right_x + w <= g.right() else left_x
    y = anchor.center().y() - h // 2 if align_y is None else align_y
    x = max(g.left(), min(x, g.right() - w))
    y = max(g.top(), min(y, g.bottom() - h))
    return QPoint(x, y)


def display_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace(" - ", " — ")


# ===================================================================== Плейлист
class PlaylistPanel(QWidget):
    play_index = Signal(int)
    download = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._files = None
        self._current = None
        self.favs = set()          # пути избранных треков
        self.only_fav = False

        frame = QFrame(self)
        frame.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("ПЛЕЙЛИСТ")
        title.setObjectName("title")
        self.count = QLabel("")
        self.count.setObjectName("dim")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count)
        from PySide6.QtWidgets import QPushButton
        self.fav_btn = QPushButton("♥ только избранное")
        self.fav_btn.setObjectName("favbtn")
        self.fav_btn.setCheckable(True)
        self.fav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav_btn.toggled.connect(self._toggle_only_fav)
        head.addWidget(self.fav_btn)
        self.dl_btn = QPushButton("⬇ скачать")
        self.dl_btn.setObjectName("favbtn")
        self.dl_btn.setToolTip("Скачать в плеер через Doulander: ссылка YouTube и др. (Ctrl+Alt+D)")
        self.dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_btn.clicked.connect(self.download.emit)
        head.addWidget(self.dl_btn)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или папке…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemClicked.connect(self._choose)
        self.list.itemActivated.connect(self._choose)
        lay.addWidget(self.list, 1)

        hint = QLabel("клик — играть   ·   Enter — играть   ·   Esc — закрыть")
        hint.setObjectName("dim")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        self.restyle()
        self.resize(360, 480)
        _live_panels.append(self)

    def restyle(self):
        self.setStyleSheet(f"""
            #panel {{ background:{BG}; border:1px solid {BORDER}; border-radius:12px; }}
            QLabel {{ color:{TEXT}; font-family:{FONTS}; }}
            #title {{ font-size:15px; font-weight:bold; color:{LCD}; letter-spacing:2px; }}
            #dim {{ color:{DIM}; font-size:11px; }}
            QLineEdit {{ background:{LCD}; color:{LCD_INK}; border:none; border-radius:6px;
                         padding:6px 8px; font-family:{FONTS}; font-size:13px; font-weight:bold;
                         selection-background-color:{LCD_INK}; selection-color:{LCD}; }}
            QListWidget {{ background:transparent; border:none; outline:none; color:{TEXT};
                           font-family:{FONTS}; font-size:13px; }}
            QListWidget::item {{ padding:4px 6px; border-radius:5px; }}
            QListWidget::item:hover {{ background:#2a3337; }}
            QListWidget::item:selected {{ background:{LCD}; color:{LCD_INK}; }}
            #favbtn {{ background:transparent; color:{DIM}; border:1px solid {BORDER}; border-radius:9px;
                       padding:2px 8px; font-family:{FONTS}; font-size:11px; }}
            #favbtn:checked {{ background:{LCD}; color:{LCD_INK}; border-color:{LCD}; }}
            QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
            QScrollBar::handle:vertical {{ background:#4b5a60; border-radius:3px; min-height:24px; }}
            QScrollBar::handle:vertical:hover {{ background:{LCD}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
        """)
        if self._files:
            cur, self._current = self._current, None
            self.set_current(cur)

    # --- данные
    def set_tracks(self, files, current, names=None):
        self._names = names                     # у радио — названия станций вместо имён файлов
        if files is not self._files or self.list.count() != len(files):
            self._files = files
            self.list.clear()
            for i, f in enumerate(files):
                it = QListWidgetItem()
                it.setData(Qt.ItemDataRole.UserRole, i)
                folder = "" if names else os.path.basename(os.path.dirname(f))
                it.setData(Qt.ItemDataRole.UserRole + 1, (self._name(i) + " " + folder).lower())
                it.setToolTip(f)
                self.list.addItem(it)
            self._current = None
            self.count.setText((f"📻 {len(files)} станц." if names else f"{len(files)} трек.") if files else "")
            self._relabel_all()
        self.set_current(current)

    def _name(self, i):
        names = getattr(self, "_names", None)
        return names[i] if names and i < len(names) else display_name(self._files[i])

    def _label(self, i):
        mark = "▶" if i == self._current else " "
        heart = "♥ " if self._files[i] in self.favs else ""
        return f"{mark} {i + 1:>3}  {heart}{self._name(i)}"

    def set_favs(self, favs):
        self.favs = set(favs)
        if self._files:
            self._relabel_all()
            cur, self._current = self._current, None
            self.set_current(cur)
            self.apply_filter(self.search.text())

    def _toggle_only_fav(self, on):
        self.only_fav = on
        self.apply_filter(self.search.text())

    def _relabel_all(self):
        for i in range(self.list.count()):
            self.list.item(i).setText(self._label(i))

    def set_current(self, current):
        if not self._files:
            return
        old, self._current = self._current, current
        for i in (old, current):
            if i is not None and 0 <= i < self.list.count():
                it = self.list.item(i)
                it.setText(self._label(i))
                f = it.font()
                f.setBold(i == current)
                it.setFont(f)
                it.setForeground(QColor(LCD) if i == current else QColor(TEXT))

    def apply_filter(self, text):
        q = text.strip().lower()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = bool(q) and q not in it.data(Qt.ItemDataRole.UserRole + 1)
            if self.only_fav and self._files[i] not in self.favs:
                hide = True
            it.setHidden(hide)
            if not hide and first is None:
                first = it
        if q and first is not None:
            self.list.setCurrentItem(first)
            self.list.scrollToItem(first)

    def _choose(self, item):
        self.play_index.emit(int(item.data(Qt.ItemDataRole.UserRole)))
        self.set_current(int(item.data(Qt.ItemDataRole.UserRole)))

    # --- показ
    def open_near(self, anchor: QRect):
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.apply_filter("")
        scr = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        h = min(560, max(320, anchor.height()), scr.availableGeometry().height() - 20)
        self.resize(self.width(), h)
        self.move(place_beside(anchor, self.size(), prefer_left=True))
        self.show()
        self.activateWindow()
        self.search.setFocus()
        if self._current is not None and self._current < self.list.count():
            it = self.list.item(self._current)
            self.list.setCurrentItem(it)
            self.list.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)

    def eventFilter(self, obj, ev):
        if obj is self.search and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self.list.setFocus()
                QCoreApplication.sendEvent(self.list, ev)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                it = self.list.currentItem()
                if it is not None and not it.isHidden():
                    self._choose(it)
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        if ev.text() and ev.text().isprintable() and not self.search.hasFocus():
            self.search.setFocus()
            self.search.insert(ev.text())
            return
        super().keyPressEvent(ev)


# ===================================================================== Обложка
class CoverPopup(QWidget):
    W, H = 200, 268
    ART = 176

    def __init__(self):
        super().__init__(None)
        self._flags_mode = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.image = None
        self.title = self.artist = self.album = ""
        self.hide_timer = QTimer(self, singleShot=True, interval=4500)
        self.hide_timer.timeout.connect(self.fade_out)
        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._anim_done)
        self._closing = False

    def _set_mode(self, on_desktop):
        if self._flags_mode == on_desktop:
            return
        self._flags_mode = on_desktop
        f = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
             Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not on_desktop:
            f |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(f)

    def show_track(self, image, title, artist, album, anchor: QRect, on_desktop, pin_fn=None):
        self.image = image if image is not None and not image.isNull() else None
        self.title, self.artist, self.album = title or "—", artist or "", album or ""
        self._set_mode(on_desktop)
        # по вертикали — на уровне верхней части экрана плеера
        y = anchor.top() + int(anchor.height() * 0.25)
        self.move(place_beside(anchor, self.size(), prefer_left=True, align_y=y))
        self.update()
        if not self.isVisible() or self._closing:
            self._closing = False
            self.anim.stop()
            self.setWindowOpacity(0.0)
            self.show()
            if pin_fn:
                pin_fn(self)
            self.anim.setDuration(260)
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
        self.hide_timer.start()

    def fade_out(self):
        if not self.isVisible():
            return
        self._closing = True
        self.anim.stop()
        self.anim.setDuration(700)
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _anim_done(self):
        if self._closing:
            self._closing = False
            self.hide()

    def mousePressEvent(self, _e):
        self.hide_timer.stop()
        self.fade_out()

    def enterEvent(self, _e):
        self.hide_timer.stop()          # пока мышь над карточкой — не прятать

    def leaveEvent(self, _e):
        if self.isVisible() and not self._closing:
            self.hide_timer.start(1800)

    # --- рисование
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform |
                         QPainter.RenderHint.TextAntialiasing)
        r = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor(BG))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawPath(path)

        art = QRectF((self.W - self.ART) / 2, 12, self.ART, self.ART)
        ap = QPainterPath()
        ap.addRoundedRect(art, 9, 9)
        p.save()
        p.setClipPath(ap)
        if self.image is not None:
            img = self.image
            # заполнить квадрат с обрезкой по центру
            s = max(art.width() / img.width(), art.height() / img.height())
            w, h = img.width() * s, img.height() * s
            p.drawImage(QRectF(art.center().x() - w / 2, art.center().y() - h / 2, w, h), img)
        else:
            self._paint_placeholder(p, art)
        p.restore()
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(ap)

        def text(y, s, px, color, bold):
            f = QFont()
            f.setFamilies(["Consolas", "Lucida Console", "Courier New"])
            f.setPixelSize(px)
            f.setBold(bold)
            p.setFont(f)
            p.setPen(QColor(color))
            el = QFontMetrics(f).elidedText(s, Qt.TextElideMode.ElideRight, self.W - 24)
            p.drawText(QRectF(12, y, self.W - 24, px + 6), Qt.AlignmentFlag.AlignHCenter, el)

        text(198, self.title, 14, TEXT, True)
        if self.artist:
            text(220, self.artist, 12, LCD, False)
        if self.album:
            text(240, self.album, 11, DIM, False)
        p.end()

    def _paint_placeholder(self, p, art):
        g = QLinearGradient(art.topLeft(), art.bottomRight())
        g.setColorAt(0, QColor(LCD_LIT))
        g.setColorAt(1, QColor(LCD).darker(115))
        p.fillRect(art, g)
        # пластинка
        c = art.center()
        R = art.width() * 0.36
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LCD_INK))
        p.drawEllipse(c, R, R)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(5):
            rr = R * (0.55 + k * 0.09)
            p.drawEllipse(c, rr, rr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LCD))
        p.drawEllipse(c, R * 0.3, R * 0.3)
        p.setBrush(QColor(LCD_INK))
        p.drawEllipse(c, R * 0.06, R * 0.06)


# ============================================================ «Сейчас играет»
def _font(px, bold=False):
    f = QFont()
    f.setFamilies(["Consolas", "Lucida Console", "Courier New"])
    f.setPixelSize(px)
    f.setBold(bold)
    return f


class NowPlayingToast(QWidget):
    """Уведомление в углу экрана над панелью задач, когда плеер спрятан."""
    clicked = Signal()
    W, H = 330, 100
    ART = 76

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.image = None
        self.title = self.artist = self.extra = ""
        self._closing = False
        self.hide_timer = QTimer(self, singleShot=True, interval=5000)
        self.hide_timer.timeout.connect(self.fade_out)

        self.a_op = QPropertyAnimation(self, b"windowOpacity", self)
        self.a_pos = QPropertyAnimation(self, b"pos", self)
        for a in (self.a_op, self.a_pos):
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.group = QParallelAnimationGroup(self)
        self.group.addAnimation(self.a_op)
        self.group.addAnimation(self.a_pos)
        self.group.finished.connect(self._done)

    def _home(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(g.right() - self.W - 14, g.bottom() - self.H - 14)

    def show_track(self, image, title, artist, extra=""):
        self.image = image if image is not None and not image.isNull() else None
        self.title, self.artist, self.extra = title or "—", artist or "", extra
        self.update()
        home = self._home()
        if not self.isVisible() or self._closing:
            self._closing = False
            self.group.stop()
            self.setWindowOpacity(0.0)
            self.move(home + QPoint(40, 0))
            self.show()
            self.a_op.setDuration(280)
            self.a_op.setStartValue(0.0)
            self.a_op.setEndValue(1.0)
            self.a_pos.setDuration(320)
            self.a_pos.setStartValue(home + QPoint(40, 0))
            self.a_pos.setEndValue(home)
            self.group.start()
        self.hide_timer.start()

    def fade_out(self):
        if not self.isVisible():
            return
        self._closing = True
        self.group.stop()
        self.a_op.setDuration(500)
        self.a_op.setStartValue(self.windowOpacity())
        self.a_op.setEndValue(0.0)
        self.a_pos.setDuration(500)
        self.a_pos.setStartValue(self.pos())
        self.a_pos.setEndValue(self.pos() + QPoint(30, 0))
        self.group.start()

    def _done(self):
        if self._closing:
            self._closing = False
            self.hide()

    def mousePressEvent(self, e):
        self.hide_timer.stop()
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        self.fade_out()

    def enterEvent(self, _e):
        self.hide_timer.stop()

    def leaveEvent(self, _e):
        if self.isVisible() and not self._closing:
            self.hide_timer.start(2000)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform |
                         QPainter.RenderHint.TextAntialiasing)
        r = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, QColor(BG))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawPath(path)
        # зелёная ЖК-полоска слева
        p.fillRect(QRectF(0, 18, 3, self.H - 36), QColor(LCD))

        pad = (self.H - self.ART) / 2
        art = QRectF(pad + 2, pad, self.ART, self.ART)
        ap = QPainterPath()
        ap.addRoundedRect(art, 8, 8)
        p.save()
        p.setClipPath(ap)
        if self.image is not None:
            img = self.image
            k = max(art.width() / img.width(), art.height() / img.height())
            w, h = img.width() * k, img.height() * k
            p.drawImage(QRectF(art.center().x() - w / 2, art.center().y() - h / 2, w, h), img)
        else:
            CoverPopup._paint_placeholder(None, p, art)
        p.restore()

        x = art.right() + 14
        tw = self.W - x - 14

        def line(y, text, f, color):
            p.setFont(f)
            p.setPen(QColor(color))
            el = QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, int(tw))
            p.drawText(QPointF(x, y), el)

        line(pad + 12, "♪ СЕЙЧАС ИГРАЕТ", _font(11, True), LCD)
        line(pad + 36, self.title, _font(15, True), TEXT)
        if self.artist:
            line(pad + 56, self.artist, _font(12), LCD)
        if self.extra:
            line(pad + 74, self.extra, _font(11), DIM)


# ===================================================================== Эквалайзер
class EqPanel(QWidget):
    """10 полос ±12 дБ, пресеты, вкл/выкл. Сигнал changed(полосы, вкл, имя пресета)."""
    changed = Signal(list, bool, str)

    def __init__(self, bands_labels, presets, max_db=12):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        from PySide6.QtWidgets import QSlider, QComboBox, QPushButton, QGridLayout
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.presets = presets
        self._busy = False
        frame = QFrame(self)
        frame.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("ЭКВАЛАЙЗЕР")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch(1)
        self.on_btn = QPushButton("ВКЛ")
        self.on_btn.setObjectName("favbtn")
        self.on_btn.setCheckable(True)
        self.on_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on_btn.toggled.connect(self._on_toggle)
        head.addWidget(self.on_btn)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.addItems(list(presets) + ["Свой"])
        self.combo.currentTextChanged.connect(self._on_preset)
        row.addWidget(self.combo, 1)
        reset = QPushButton("сброс")
        reset.setObjectName("favbtn")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: self.combo.setCurrentText(next(iter(presets))))
        row.addWidget(reset)
        lay.addLayout(row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self.sliders, self.vals = [], []
        for i, lab in enumerate(bands_labels):
            v = QLabel("0")
            v.setObjectName("dim")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s = QSlider(Qt.Orientation.Vertical)
            s.setRange(-int(max_db * 2), int(max_db * 2))     # шаг 0,5 дБ
            s.setPageStep(2)
            s.setMinimumHeight(150)
            s.setCursor(Qt.CursorShape.PointingHandCursor)
            s.valueChanged.connect(self._on_slider)
            s.setToolTip(f"{lab} Гц — двойной клик: в ноль")
            s.installEventFilter(self)
            n = QLabel(lab)
            n.setObjectName("dim")
            n.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(v, 0, i, Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(s, 1, i, Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(n, 2, i, Qt.AlignmentFlag.AlignHCenter)
            self.sliders.append(s)
            self.vals.append(v)
        lay.addLayout(grid)
        bass = QHBoxLayout()
        lb = QLabel("◀ басы")
        lb.setObjectName("dim")
        lt = QLabel("верха ▶")
        lt.setObjectName("dim")
        bass.addWidget(lb)
        bass.addStretch(1)
        bass.addWidget(lt)
        lay.addLayout(bass)
        self.note = QLabel("")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.restyle()
        self.resize(430, 360)
        _live_panels.append(self)

    def restyle(self):
        self.setStyleSheet(f"""
            #panel {{ background:{BG}; border:1px solid {BORDER}; border-radius:12px; }}
            QLabel {{ color:{TEXT}; font-family:{FONTS}; }}
            #title {{ font-size:15px; font-weight:bold; color:{LCD}; letter-spacing:2px; }}
            #dim {{ color:{DIM}; font-size:11px; }}
            QComboBox {{ background:{LCD}; color:{LCD_INK}; border:none; border-radius:6px;
                         padding:4px 8px; font-family:{FONTS}; font-size:13px; font-weight:bold; }}
            QComboBox QAbstractItemView {{ background:{BG}; color:{TEXT}; selection-background-color:{LCD};
                                           selection-color:{LCD_INK}; font-family:{FONTS}; }}
            #favbtn {{ background:transparent; color:{DIM}; border:1px solid {BORDER}; border-radius:9px;
                       padding:2px 10px; font-family:{FONTS}; font-size:11px; }}
            #favbtn:checked {{ background:{LCD}; color:{LCD_INK}; border-color:{LCD}; }}
            QSlider::groove:vertical {{ background:#2a3337; width:6px; border-radius:3px; }}
            QSlider::sub-page:vertical {{ background:#2a3337; border-radius:3px; }}
            QSlider::add-page:vertical {{ background:{LCD}; border-radius:3px; }}
            QSlider::handle:vertical {{ background:{TEXT}; height:12px; margin:0 -6px; border-radius:6px; }}
            QSlider::handle:vertical:hover {{ background:{LCD_LIT}; }}
            QSlider:disabled {{ opacity:0.4; }}
        """)

    # --- данные
    def set_state(self, gains, enabled, preset, note=""):
        self._busy = True
        for s, v, g in zip(self.sliders, self.vals, gains):
            s.setValue(int(round(g * 2)))
            v.setText(f"{g:+.1f}".replace("+0.0", "0").replace("-0.0", "0"))
        self.on_btn.setChecked(bool(enabled))
        self.on_btn.setText("ВКЛ" if enabled else "ВЫКЛ")
        self.combo.setCurrentText(preset if preset in self.presets else "Свой")
        self.note.setText(note)
        self._busy = False

    def gains(self):
        return [s.value() / 2.0 for s in self.sliders]

    def _emit(self, preset):
        self.changed.emit(self.gains(), self.on_btn.isChecked(), preset)

    def _on_slider(self, _v):
        for s, v in zip(self.sliders, self.vals):
            g = s.value() / 2.0
            v.setText(f"{g:+.1f}".replace("+0.0", "0").replace("-0.0", "0"))
        if self._busy:
            return
        self._busy = True
        match = next((k for k, p in self.presets.items() if [float(x) for x in p] == self.gains()), "Свой")
        self.combo.setCurrentText(match)
        self._busy = False
        if not self.on_btn.isChecked():
            self.on_btn.setChecked(True)              # двигаешь ползунок — значит, включить
            return
        self._emit(match)

    def _on_preset(self, name):
        if self._busy or name not in self.presets:
            return
        self._busy = True
        for s, v, g in zip(self.sliders, self.vals, self.presets[name]):
            s.setValue(int(round(g * 2)))
            v.setText(f"{float(g):+.1f}".replace("+0.0", "0").replace("-0.0", "0"))
        self._busy = False
        if not self.on_btn.isChecked():
            self.on_btn.setChecked(True)
        else:
            self._emit(name)

    def _on_toggle(self, on):
        self.on_btn.setText("ВКЛ" if on else "ВЫКЛ")
        if not self._busy:
            self._emit(self.combo.currentText())

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.MouseButtonDblClick and obj in self.sliders:
            obj.setValue(0)
            return True
        return False

    def open_near(self, anchor: QRect):
        self.move(place_beside(anchor, self.size(), prefer_left=False))
        self.show()
        self.activateWindow()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(ev)
