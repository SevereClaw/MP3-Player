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
CROSSFADES = {"Выключен": 0, "2 сек": 2000, "4 сек": 4000, "6 сек": 6000, "8 сек": 8000}
SIZES = {"Маленький": 0.6, "Средний": 0.8, "Большой": 1.0, "Огромный": 1.25}
THEMES = {"ЗЕЛЁНАЯ": 0, "ГОЛУБАЯ": 0, "ОРАНЖЕВАЯ": 0, "КРАСНАЯ": 0, "БЕЛАЯ": 0}
SFX_LEVELS = {"Тихо": 0.25, "Средне": 0.5, "Громко": 0.85}
HOTKEYS = []


class Autostart:
    @staticmethod
    def enabled():
        return True


class FakeOwner:
    """Плеер-заглушка: всё, чего нет, — пустые функции."""
    NORM_MODES = {"off": ("Выключено", None), "spotify": ("Как Spotify (−14 LUFS)", -14.0),
                  "quiet": ("Ровнее, но тише (−18 LUFS)", -18.0)}

    def __init__(self):
        self.library, self.player = lib, Player()
        self.title, self.artist, self.album = "Full Moon Full Life", "Atlus Sound Team", "Persona 3 Reload OST"
        self._cover, self.loaded_path = covers[cur], cur
        self.lyrics = Lyrics([40000, 52000, 58000, 66000, 72000, 80000, 86000, 92000], lines)
        self.cfg = {"folders": ["C:\\Music", "D:\\Аниме OST"], "crossfade": 4000, "theme": "ГОЛУБАЯ"}
        self.shuffle, self.repeat, self.skin_id, self.scale_f, self.on_desktop, self.build = False, "all", "flash", 0.8, True, 23
        self.keeper = types.SimpleNamespace(ok=True)
        self.data_dir = tmp
        ps = list(lib.items)
        import datetime as _d
        today = _d.date.today().isoformat()
        self.cfg["recent"] = ps[::3]
        self.stats = types.SimpleNamespace(data={
            "tracks": {p: {"sec": 200 * (i % 7 + 1), "plays": i % 5 + 1, "last": "2026-09-01" if i % 4 == 0 else today}
                       for i, p in enumerate(ps)},
            "daily": {today: {p: {"s": 100 * (i % 6 + 1), "p": 1} for i, p in enumerate(ps)}}})
        self.spectrum = types.SimpleNamespace(available=True)

    def __getattr__(self, name):
        return lambda *a, **k: None

    def is_playing(self): return True
    def queue_upcoming(self, limit=300):
        ps = list(lib.items)
        i = ps.index(cur)
        return (ps[i + 1:] + ps[:i])[:12]
    def smooth_pos(self): return self.player.position()
    def is_fav(self, p): return "Color" in p or "Война" in p
    def auto_scale_here(self): return 1.0
    def find_skins(self):
        return {"flash": ("", {"name": "Флешка"}), "pocket": ("", {"name": "Карманный"}),
                "cassette": ("", {"name": "Кассета"})}


owner = FakeOwner()
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


pl = w.playlists.create("Persona вайб", [p for p in lib.items if "Persona" in p or "Burn" in p])
w.playlists.create("Ночная дорога", [p for p in lib.items if "Кино" in p or "Ado" in p][:5])
w.playlists_changed()
w.go("home"); dump("lib_home.jpg")
w.open_playlist(pl["id"]); dump("lib_playlist.jpg")
w.toggle_queue(True); dump("lib_queue.jpg")
w.toggle_queue(False)
from library_more import AddToPlaylist
pop = AddToPlaylist(w, [next(iter(lib.items))]); pop.setStyleSheet(w.styleSheet()); pop.adjustSize()
pop.show(); app.processEvents()
pm = pop.grab(); buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); pm.save(buf, "JPG", 85)
b = base64.b64encode(bytes(buf.data())).decode()
for k in range(0, len(b), 20000):
    print("SHOTPART", "lib_addpl.jpg", k // 20000, b[k:k + 20000], flush=True)
pop.close()
w.go("tracks")
w.set_sort("plays", True)
w.tracks_view.setFocus()
from PySide6.QtCore import QItemSelectionModel
sm = w.tracks_view.selectionModel()
for r in (1, 2, 4):
    sm.select(w.tracks_view.model().index(r, 0), QItemSelectionModel.SelectionFlag.Select)
sm.setCurrentIndex(w.tracks_view.model().index(4, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
__import__("time").sleep(0.5)
dump("lib_tracks_sorted.jpg")
w.set_filter("fav"); __import__("time").sleep(0.5); dump("lib_tracks_fav.jpg")
w.set_filter("all")
w.focus_sidebar(); w.focus_nav_step(w.nav["tracks"], 1); __import__("time").sleep(0.5); dump("lib_keyboard.jpg")
w.go("albums"); dump("lib_albums.jpg")
w.go("artists")
for _ in range(80):                                   # фото исполнителей грузятся из интернета
    app.processEvents(); _t.sleep(0.15) if False else __import__("time").sleep(0.15)
dump("lib_artists.jpg")
# переход между страницами — кадр посередине анимации
w.go("albums"); __import__("time").sleep(0.12); app.processEvents(); w.trans.repaint()
pm = w.grab(); buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); pm.save(buf, "JPG", 80)
b = base64.b64encode(bytes(buf.data())).decode()
for k in range(0, len(b), 20000):
    print("SHOTPART", "lib_transition.jpg", k // 20000, b[k:k + 20000], flush=True)
w.open_album(next(a for a in w.albums if a["title"].startswith("Persona"))); dump("lib_album.jpg")
w.go("now"); dump("lib_now.jpg")
from PySide6.QtCore import QPointF as _P
import time as _t
r = w.now.rect()
side = min(int((r.height() - 132) * 0.74), int(r.width() * 0.32))
w.now.mouse = _P(r.width() * 0.25 + side * 0.35, (r.height() - 132) * 0.5 - side * 0.3)   # мышь над обложкой, правый верх
for _ in range(40):
    w.now.repaint(); app.processEvents(); _t.sleep(0.016)
dump("lib_now_hover.jpg")
w.now.mouse = None
owner.wave_active = lambda: True
owner.cfg["wave_mode"] = "fav"
w.now.wave.sync()
for _ in range(30):
    w.now.wave.repaint(); w.now.repaint(); app.processEvents(); _t.sleep(0.016)
dump("lib_wave.jpg")
w.search.setText("кино"); dump("lib_search.jpg")
w.search.clear()
w.go("settings"); dump("lib_settings.jpg")
w.settings.scroll.verticalScrollBar().setValue(700); dump("lib_settings2.jpg")
print("альбомов:", len(w.albums), "исполнителей:", len(w.artists))
