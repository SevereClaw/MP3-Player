# -*- coding: utf-8 -*-
"""Снимки окна «Медиатека» (альбомы, исполнители, альбом, сейчас играет) — в журнал как base64 JPEG."""
import base64, os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QBuffer, QIODevice, Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QLinearGradient, QFont
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
import library, library_ui
from lyrics import Lyrics

tmp = tempfile.mkdtemp()
lib = library.Library(os.path.join(tmp, "lib.json"))
data = [
    ("Persona 3 Reload OST", "Atlus Sound Team", [("Full Moon Full Life", 290), ("It's Going Down Now", 241),
                                                   ("Color Your Night", 263), ("Mass Destruction -Reload-", 214),
                                                   ("Changing Seasons", 221)], "#1d4fd8"),
    ("Группа крови", "Кино", [("Группа крови", 286), ("Закрой за мной дверь", 254), ("Война", 228),
                              ("Спокойная ночь", 384)], "#b3261e"),
    ("Unravel", "TK from 凛として時雨", [("unravel", 238)], "#6b21a8"),
    ("Gosick OST", "Komine Lisa", [("Destin Histoire", 262), ("Resuscitated Hope", 294)], "#c58b17"),
    ("Burn My Dread", "Yumi Kawamura", [("Burn My Dread", 247), ("Want To Be Close", 215)], "#0e7490"),
    ("Прованс", "Ёлка", [("Прованс", 212)], "#be185d"),
    ("Ado", "Ado", [("唱", 189), ("新時代", 224)], "#334155"),
]
covers = {}
for al, ar, tracks, col in data:
    for n, (t, d) in enumerate(tracks, 1):
        p = f"C:\\Music\\{al}\\{n:02d}. {ar} - {t}.flac"
        lib.items[p] = {"s": 1, "m": 1, "t": t, "a": ar, "al": al, "n": n, "d": d}
        img = QImage(360, 360, QImage.Format.Format_RGB32)
        pt = QPainter(img)
        g = QLinearGradient(0, 0, 360, 360)
        g.setColorAt(0, QColor(col).lighter(150)); g.setColorAt(1, QColor(col).darker(200))
        pt.fillRect(img.rect(), g)
        pt.setPen(QColor(255, 255, 255, 220)); f = QFont("Segoe UI"); f.setPixelSize(54); f.setBold(True); pt.setFont(f)
        pt.drawText(img.rect().adjusted(20, 20, -20, -20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap, al[:14])
        pt.end()
        covers[p] = img
lib._rebuild_hay()
cur = next(p for p in lib.items if "Full Moon" in p)


class Player:
    def position(self): return 61500
    def duration(self): return 290000
    def volume(self): return 0.7
    def setVolume(self, v): pass
    def setPosition(self, v): pass


lines = ["Baby boy, you stay the night", "Full moon, full life, I'm sinking in deep", "Ooh, I can feel the fire",
         "This feeling is the best of all", "And I believe that you can hear me", "Shine on, full moon",
         "Full moon, full life", "Tonight we're gonna dance"]
owner = types.SimpleNamespace(
    library=lib, player=Player(), title="Full Moon Full Life", artist="Atlus Sound Team",
    album="Persona 3 Reload OST", _cover=covers[cur], loaded_path=cur,
    lyrics=Lyrics([40000, 52000, 58000, 66000, 72000, 80000, 86000, 92000], lines),
    is_playing=lambda: True, is_fav=lambda p: "Color" in p or "Война" in p, toggle_play=lambda: None,
    step=lambda d: None, save_later=lambda: None, play_list=lambda *a, **k: None, queue_next=lambda p: None,
    edit_tags=lambda p=None: None)
print("start", flush=True)
w = library_ui.LibraryWindow(owner, os.path.join(tmp, "covers"))
for p, img in covers.items():
    w.covers.put(p, img)
w.resize(1280, 800)
print("built", flush=True)
w.open()
print("opened", flush=True)


def dump(name):
    for _ in range(5):
        app.processEvents()
    w.tick()
    app.processEvents()
    pm = w.grab()
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); pm.save(buf, "JPG", 80)
    b = base64.b64encode(bytes(buf.data())).decode()
    for k in range(0, len(b), 20000):                 # длинные строки журнал GitHub обрезает
        print("SHOTPART", name, k // 20000, b[k:k + 20000], flush=True)


w.go("albums"); dump("lib_albums.jpg")
w.go("artists"); dump("lib_artists.jpg")
w.open_album(next(a for a in w.albums if a["title"].startswith("Persona"))); dump("lib_album.jpg")
w.go("now"); dump("lib_now.jpg")
w.search.setText("кино"); dump("lib_search.jpg")
print("альбомов:", len(w.albums), "исполнителей:", len(w.artists))
