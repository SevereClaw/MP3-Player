# -*- coding: utf-8 -*-
"""Снимки окон «Поиск по всей музыке» и «Теги трека» — в журнал как base64."""
import base64, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QBuffer, QIODevice, QRect
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
import library, panels

lib = library.Library(os.path.join(os.environ.get("TEMP", "."), "ui_lib.json"))
lib.items = {}
for i, (t, a, al, folder) in enumerate([
        ("Группа крови", "Кино", "Группа крови", "Кино"), ("Звезда по имени Солнце", "Кино", "", "Кино"),
        ("Кукушка", "Кино", "Чёрный альбом", "Кино"), ("Mass Destruction", "Shoji Meguro", "P3", "Persona"),
        ("Кино в моей голове", "Ёлка", "", "Разное")]):
    lib.items[f"C:\\Music\\{folder}\\{a} - {t}.mp3"] = {"s": 1, "m": i, "t": t, "a": a, "al": al}
lib._rebuild_hay()

def dump(w, name):
    w.show(); app.processEvents()
    pm = w.grab(); buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); pm.save(buf, "PNG")
    print("SHOT", name, base64.b64encode(bytes(buf.data())).decode())

s = panels.SearchPanel(lib)
s.open_near(QRect(900, 100, 300, 600), "кино")
dump(s, "search.png")
e = panels.TagEditor()
e.open_for("C:\\Music\\Кино\\01. Кино - Группа крови.mp3",
           {"title": "Группа крови", "artist": "Кино", "album": "Группа крови", "cover": None},
           QRect(900, 100, 300, 600))
dump(e, "tags.png")
