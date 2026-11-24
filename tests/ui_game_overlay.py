# -*- coding: utf-8 -*-
"""Снимки окон «поверх игры» (текст песни и карточка трека) на фоне «кадра игры» — в журнал как base64."""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QBuffer, QIODevice, Qt, QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor, QLinearGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import game_overlay as go  # noqa: E402


def game_frame(w, h):
    """Яркий пёстрый фон — проверить, что текст читается на любом кадре."""
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#7ec850"))
    g.setColorAt(0.5, QColor("#f0e0a0"))
    g.setColorAt(1, QColor("#3a6ea5"))
    p.fillRect(img.rect(), g)
    for i in range(14):
        p.fillRect(QRectF(i * w / 14, h * 0.55, w / 28, h * 0.45), QColor(255, 255, 255, 120))
    p.end()
    return img


def cover():
    img = QImage(300, 300, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 300, 300)
    g.setColorAt(0, QColor("#ff4d8d"))
    g.setColorAt(1, QColor("#3b1f8f"))
    p.fillRect(img.rect(), g)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPixelSize(90)
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "P3R")
    p.end()
    return img


def shot(name, widget, settle=0.0):
    t_end = time.monotonic() + settle
    while time.monotonic() < t_end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    bg = game_frame(widget.width(), widget.height())
    over = widget.grab().toImage()
    p = QPainter(bg)
    p.drawImage(0, 0, over)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    bg.save(buf, "PNG")
    print("SHOT", name, base64.b64encode(bytes(buf.data())).decode())


ly = go.GameLyrics()
ly.set_lines("Мы будем петь и смеяться как дети", "Среди упорной борьбы и труда", "Песня")
shot("game_lyrics.png", ly, settle=0.6)
ly.set_lines("A very long lyric line that just keeps going and going to check how the text gets cut at the edge",
             "", "")
shot("game_lyrics_long.png", ly, settle=0.6)
ly.set_lines("Burn my dread", "Hey yo", "")
ly.set_scale(1.4)
ly.ctrl_poll.stop()                     # в тесте Ctrl никто не держит — опрос сразу выключил бы режим
ly._set_edit(True)
shot("game_lyrics_edit.png", ly, settle=0.6)
print("edit mode accepts mouse:", not ly.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents), "size", ly.width(), ly.height())
# Windows сама решает, кому достаётся клик: окно под точкой (WindowFromPoint) — наше только в режиме Ctrl
import ctypes
from ctypes import wintypes
ly.setWindowOpacity(1.0)
ly.move(200, 200)
for _ in range(10):
    app.processEvents(); time.sleep(0.03)
u = ctypes.windll.user32
u.WindowFromPoint.restype = wintypes.HWND
c = ly.frameGeometry().center()
ratio = ly.devicePixelRatioF()
pt = wintypes.POINT(int(c.x() * ratio), int(c.y() * ratio))
own = int(ly.winId())
def under():
    h = u.WindowFromPoint(pt)
    return int(h or 0)
hit_edit = under() == own
ly._set_edit(False)
for _ in range(5):
    app.processEvents(); time.sleep(0.02)
hit_normal = under() == own
print("HIT with Ctrl:", hit_edit, "| HIT without Ctrl:", hit_normal, flush=True)
ly.set_scale(1.0)
ly.clear(fade=False)

card = go.GameTrackCard()
card.show_track(cover(), "Burn My Dread", "Shoji Meguro feat. Lotus Juice", "Mass Destruction")
shot("game_card.png", card, settle=1.2)
card.show_track(None, "Трек без обложки и с очень длинным названием, которое не помещается", "", "")
shot("game_card_noart.png", card, settle=1.2)
badge = go.VolumeBadge()
badge.show_level(0.46)
shot("vol_badge.png", badge, settle=0.4)
badge.show_level(0.0, True)
shot("vol_badge_mute.png", badge, settle=0.4)
badge.hide()
print("flags lyrics:", int(ly.windowFlags()), "card:", int(card.windowFlags()))
print("transparent for input:", bool(card.windowFlags() & Qt.WindowType.WindowTransparentForInput))
card.cancel()
# Ctrl+Alt: чистое нажатие закрепляет / открепляет, Ctrl+Alt+G (горячая клавиша плеера) — нет
pressed = set()
go.GameLyrics._key = staticmethod(lambda vk: vk in pressed)
lk = go.GameLyrics()
lk.show()
events = []
lk.lock_toggled.connect(events.append)
def poll(keys):
    pressed.clear(); pressed.update(keys); lk._poll_ctrl()
poll({0x11}); edit_on_ctrl = lk.edit
poll({0x11, 0x12}); poll(set())
poll({0x11, 0x12}); poll({0x11, 0x12, 0x47}); poll(set())
poll({0x11}); edit_when_locked = lk.edit
poll(set())
print("LOCK events:", events, "| edit on Ctrl:", edit_on_ctrl, "| edit on Ctrl while locked:", edit_when_locked, flush=True)
assert events == [True] and edit_on_ctrl and not edit_when_locked
print("DONE")
