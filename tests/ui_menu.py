# -*- coding: utf-8 -*-
"""Снимки меню плеера (автосборка): верхний уровень и подменю — в журнал как base64."""
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
import mp3player  # noqa: E402

w = mp3player.Mp3Widget(os.path.join(tempfile.mkdtemp(), "settings.json"), skip_restore=True)
w.sync_menu()


def dump(menu, name):
    menu.ensurePolished()
    menu.adjustSize()
    pm = menu.grab()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    print("SHOT", name, base64.b64encode(bytes(buf.data())).decode())


dump(w.menu, "menu.png")
top = [a.text() for a in w.menu.actions() if not a.isSeparator()]
print("пунктов сверху:", len(top), top)
for a in w.menu.actions():
    if a.menu() and any(k in a.text() for k in ("Вид", "Настройки", "Звук", "Воспроизведение")):
        a.menu().aboutToShow.emit()
        dump(a.menu(), a.text().split()[-1] + ".png")
w._quit_now() if hasattr(w, "_quit_now") else None
