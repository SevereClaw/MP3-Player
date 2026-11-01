# -*- coding: utf-8 -*-
"""Редактор своего скина: своя картинка плеера + разметка экрана и кнопок мышкой."""
import json
import math
import os
import re
from collections import deque

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QFont, QPixmap, QGuiApplication, QCursor
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QCheckBox, QSlider, QLineEdit, QFileDialog, QMessageBox, QApplication)

STEPS = [  # (ключ, подпись, обязательно)
    ("screen", "Обведи ЭКРАН — сюда плеер будет выводить название, время и меню", True),
    ("prev", "Обведи кнопку «Назад» ⏮", False),
    ("play", "Обведи кнопку «Играть / пауза» ⏯", False),
    ("next", "Обведи кнопку «Вперёд» ⏭", False),
    ("shuffle", "Обведи кнопку «Перемешать» 🔀", False),
    ("menu", "Обведи кнопку «Меню / настройки» ⚙  (можно пропустить)", False),
    ("folder_zone", "Обведи место для «Следующая папка» 📁  (можно пропустить)", False),
]
LABELS = {"screen": "ЭКРАН", "prev": "⏮", "play": "⏯", "next": "⏭", "shuffle": "🔀",
          "menu": "⚙", "folder_zone": "📁"}
COLORS = {"screen": "#3fd0ff", "folder_zone": "#ffb84d"}

DARK = """
QDialog { background:#1b2124; }
QLabel { color:#d9e6e0; font-size:13px; }
#step { color:#96aba4; font-size:15px; font-weight:bold; }
QPushButton { background:#2a3337; color:#d9e6e0; border:1px solid #4b5a60; border-radius:6px; padding:6px 14px; }
QPushButton:hover { background:#3a464b; }
QPushButton:disabled { color:#5d6a70; }
QPushButton#primary { background:#96aba4; color:#1a2421; font-weight:bold; border:none; }
QLineEdit { background:#96aba4; color:#1a2421; border:none; border-radius:6px; padding:5px 8px; font-weight:bold; }
QCheckBox { color:#d9e6e0; }
"""


# ------------------------------------------------------------------ фон
def remove_background(img: QImage, tol: int):
    """Убрать однотонный (или плавный) фон, связанный с краями картинки.
    Возвращает (картинка с прозрачностью, прямоугольник содержимого)."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    W, H = img.width(), img.height()
    f = max(1, math.ceil(max(W, H) / 640))
    sw, sh = max(1, W // f), max(1, H // f)
    small = img.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    px = [small.pixel(x, y) for y in range(sh) for x in range(sw)]
    rgb = [((c >> 16) & 255, (c >> 8) & 255, c & 255, (c >> 24) & 255) for c in px]
    corners = [rgb[0], rgb[sw - 1], rgb[(sh - 1) * sw], rgb[sh * sw - 1]]

    def dist(a, b):
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))

    near_corner = lambda c: c[3] < 20 or min(dist(c, k) for k in corners) <= tol * 2.5
    bg = bytearray(sw * sh)
    q = deque()
    for x in range(sw):
        for y in (0, sh - 1):
            i = y * sw + x
            if not bg[i] and near_corner(rgb[i]):
                bg[i] = 1
                q.append(i)
    for y in range(sh):
        for x in (0, sw - 1):
            i = y * sw + x
            if not bg[i] and near_corner(rgb[i]):
                bg[i] = 1
                q.append(i)
    while q:
        i = q.popleft()
        x, y = i % sw, i // sw
        c = rgb[i]
        for j in ((i - 1) if x > 0 else -1, (i + 1) if x < sw - 1 else -1,
                  (i - sw) if y > 0 else -1, (i + sw) if y < sh - 1 else -1):
            if j >= 0 and not bg[j]:
                cj = rgb[j]
                if cj[3] < 20 or (dist(cj, c) <= tol * 0.6 and near_corner(cj)):
                    bg[j] = 1
                    q.append(j)
    alpha = bytes(0 if b else 255 for b in bg)
    xs = [i % sw for i in range(sw * sh) if not bg[i]]
    if not xs:
        return img, QRectF(0, 0, W, H)
    ys = [i // sw for i in range(sw * sh) if not bg[i]]
    box = QRectF(max(0, (min(xs) - 1) * f), max(0, (min(ys) - 1) * f),
                 min(W, (max(xs) + 2) * f) - max(0, (min(xs) - 1) * f),
                 min(H, (max(ys) + 2) * f) - max(0, (min(ys) - 1) * f))
    mask = QImage(alpha, sw, sh, sw, QImage.Format.Format_Alpha8).copy()
    mask = mask.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = img.copy()
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()
    return out, box


# ------------------------------------------------------------------ холст
class Canvas(QWidget):
    rect_drawn = Signal(QRectF)

    def __init__(self):
        super().__init__()
        self.img = None
        self.rects = {}           # ключ -> QRectF в координатах картинки
        self.active = None
        self.drag = None
        self.setMinimumSize(420, 520)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, img):
        self.img = img
        self.update()

    def _geom(self):
        if self.img is None:
            return 1.0, 0.0, 0.0
        k = min(self.width() / self.img.width(), self.height() / self.img.height())
        ox = (self.width() - self.img.width() * k) / 2
        oy = (self.height() - self.img.height() * k) / 2
        return k, ox, oy

    def to_img(self, pt):
        k, ox, oy = self._geom()
        return QPointF((pt.x() - ox) / k, (pt.y() - oy) / k)

    def to_view(self, r):
        k, ox, oy = self._geom()
        return QRectF(ox + r.x() * k, oy + r.y() * k, r.width() * k, r.height() * k)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        # шахматка — видно прозрачность
        s = 12
        for y in range(0, self.height(), s):
            for x in range(0, self.width(), s):
                p.fillRect(x, y, s, s, QColor("#2a3035") if (x // s + y // s) % 2 else QColor("#22282c"))
        if self.img is None:
            return
        k, ox, oy = self._geom()
        p.drawImage(QRectF(ox, oy, self.img.width() * k, self.img.height() * k), self.img)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(13)
        p.setFont(f)
        for key, r in self.rects.items():
            col = QColor(COLORS.get(key, "#9cff7a"))
            vr = self.to_view(r)
            fill = QColor(col)
            fill.setAlpha(60 if key != self.active else 90)
            p.fillRect(vr, fill)
            p.setPen(QPen(col, 2))
            p.drawRect(vr)
            p.fillRect(QRectF(vr.left(), vr.top() - 18, 34 + 8 * len(LABELS[key]), 18), col)
            p.setPen(QColor("#111"))
            p.drawText(QRectF(vr.left() + 4, vr.top() - 18, 200, 18),
                       Qt.AlignmentFlag.AlignVCenter, LABELS[key])
        if self.drag:
            a, b = self.drag
            vr = QRectF(a, b).normalized()
            p.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.DashLine))
            p.drawRect(vr)

    def mousePressEvent(self, e):
        if self.img is not None and self.active and e.button() == Qt.MouseButton.LeftButton:
            self.drag = (e.position(), e.position())

    def mouseMoveEvent(self, e):
        if self.drag:
            self.drag = (self.drag[0], e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.drag:
            return
        a, b = self.drag
        self.drag = None
        r = QRectF(self.to_img(a), self.to_img(b)).normalized()
        r = r.intersected(QRectF(0, 0, self.img.width(), self.img.height()))
        if r.width() > 6 and r.height() > 6:
            self.rects[self.active] = r
            self.rect_drawn.emit(r)
        self.update()


# ------------------------------------------------------------------ окно
class SkinEditor(QDialog):
    def __init__(self, skins_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Свой скин плеера")
        self.setStyleSheet(DARK)
        self.skins_dir = skins_dir
        self.src = None           # исходная картинка
        self.cut = None           # после удаления фона
        self.box = None           # где содержимое
        self.step = 0
        self.saved_id = None

        lay = QVBoxLayout(self)
        self.step_lbl = QLabel("")
        self.step_lbl.setObjectName("step")
        self.step_lbl.setWordWrap(True)
        lay.addWidget(self.step_lbl)
        tip = QLabel("Тяни мышью рамку. Можно перерисовать — новая рамка заменит старую.")
        lay.addWidget(tip)

        self.canvas = Canvas()
        self.canvas.rect_drawn.connect(lambda _r: self.refresh())
        lay.addWidget(self.canvas, 1)

        opt = QHBoxLayout()
        self.bg_chk = QCheckBox("Убрать фон")
        self.bg_chk.toggled.connect(self.reprocess)
        opt.addWidget(self.bg_chk)
        opt.addWidget(QLabel("допуск"))
        self.tol = QSlider(Qt.Orientation.Horizontal)
        self.tol.setRange(4, 80)
        self.tol.setValue(24)
        self.tol.setFixedWidth(120)
        self.tol.sliderReleased.connect(self.reprocess)
        opt.addWidget(self.tol)
        opt.addSpacing(16)
        self.rot_chk = QCheckBox("Текст поперёк (экран «стоя»)")
        opt.addWidget(self.rot_chk)
        opt.addStretch(1)
        lay.addLayout(opt)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Название:"))
        self.name = QLineEdit("Мой плеер")
        self.name.setFixedWidth(180)
        bottom.addWidget(self.name)
        bottom.addStretch(1)
        self.b_back = QPushButton("◀ Назад")
        self.b_skip = QPushButton("Пропустить")
        self.b_next = QPushButton("Далее ▶")
        self.b_next.setObjectName("primary")
        self.b_back.clicked.connect(lambda: self.go(-1))
        self.b_skip.clicked.connect(self.skip)
        self.b_next.clicked.connect(lambda: self.go(+1))
        for b in (self.b_back, self.b_skip, self.b_next):
            bottom.addWidget(b)
        lay.addLayout(bottom)

        g = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(900, g.width() - 80), min(820, g.height() - 80))

    # --- картинка
    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Картинка плеера", "",
                                              "Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return False
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Свой скин", "Не получилось открыть картинку.")
            return False
        # слишком большие картинки уменьшаем — плееру столько не нужно
        if max(img.width(), img.height()) > 1800:
            img = img.scaled(1800, 1800, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.src = img.convertToFormat(QImage.Format.Format_ARGB32)
        base = os.path.splitext(os.path.basename(path))[0][:24]
        self.name.setText(base or "Мой плеер")
        # если у картинки нет прозрачности — сразу предложим убрать фон
        has_alpha = self.src.pixelColor(0, 0).alpha() < 250 or self.src.pixelColor(
            self.src.width() - 1, self.src.height() - 1).alpha() < 250
        self.bg_chk.blockSignals(True)
        self.bg_chk.setChecked(not has_alpha)
        self.bg_chk.blockSignals(False)
        self.reprocess()
        self.step = 0
        self.refresh()
        return True

    def reprocess(self):
        if self.src is None:
            return
        if self.bg_chk.isChecked():
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                self.cut, self.box = remove_background(self.src, self.tol.value())
            finally:
                QApplication.restoreOverrideCursor()
        else:
            self.cut, self.box = self.src, QRectF(0, 0, self.src.width(), self.src.height())
        self.canvas.set_image(self.cut)

    # --- шаги
    def refresh(self):
        key, text, req = STEPS[self.step]
        self.canvas.active = key
        n = len(STEPS)
        self.step_lbl.setText(f"Шаг {self.step + 1} из {n}.  {text}")
        self.b_back.setEnabled(self.step > 0)
        self.b_skip.setEnabled(not req)
        last = self.step == n - 1
        self.b_next.setText("💾 Сохранить" if last else "Далее ▶")
        self.b_next.setEnabled(key in self.canvas.rects or not req)
        if key == "screen" and "screen" in self.canvas.rects:
            r = self.canvas.rects["screen"]
            self.rot_chk.setChecked(r.height() > r.width())
        self.canvas.update()

    def skip(self):
        key = STEPS[self.step][0]
        self.canvas.rects.pop(key, None)
        self.go(+1)

    def go(self, d):
        if d > 0 and self.step == len(STEPS) - 1:
            self.save()
            return
        self.step = max(0, min(len(STEPS) - 1, self.step + d))
        self.refresh()

    # --- сохранение
    def save(self):
        if "screen" not in self.canvas.rects:
            QMessageBox.information(self, "Свой скин", "Нужно обвести экран.")
            return
        name = self.name.text().strip() or "Мой плеер"
        folder = re.sub(r'[\\/:*?"<>|]+', "_", name)[:40] or "skin"
        d = os.path.join(self.skins_dir, folder)
        os.makedirs(d, exist_ok=True)
        box = self.box.toAlignedRect()
        body = self.cut.copy(box)
        ox, oy = box.x(), box.y()
        body.save(os.path.join(d, "body.png"), "PNG")

        def rr(r):
            r = r.translated(-ox, -oy)
            return [round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1)]

        rects = self.canvas.rects
        meta = {
            "name": name, "body": "body.png",
            "screen": rr(rects["screen"]), "screen_radius": 6,
            "text_rotation": 90 if self.rot_chk.isChecked() else 0,
            "buttons": {k: rr(rects[k]) for k in ("prev", "play", "next", "shuffle", "menu") if k in rects},
            "press_offset": [0, 1.5],
            "folder_zone": rr(rects["folder_zone"]) if "folder_zone" in rects else None,
            "pad": [10, 10],
        }
        with open(os.path.join(d, "skin.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        self.saved_id = "user:" + folder
        self.accept()
