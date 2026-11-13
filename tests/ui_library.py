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
# дубликаты: те же песни в другой папке / формате
for src, dst in (("C:\\Music\\Группа крови\\01. Кино - Группа крови.flac", "D:\\Скачанное\\Кино - Группа крови.mp3"),
                 ("C:\\Music\\Unravel\\01. TK from 凛として時雨 - unravel.flac", "D:\\Аниме\\unravel.m4a"),
                 ("C:\\Music\\Unravel\\01. TK from 凛として時雨 - unravel.flac", "D:\\Аниме\\unravel (1).mp3")):
    it = dict(lib.items[src]); it["s"] = 5_000_000; it["d"] = it["d"] + 1
    lib.items[dst] = it
    covers[dst] = covers[src]
for p in lib.items:
    lib.items[p].setdefault("s", 30_000_000)
    if lib.items[p]["s"] == 1:
        lib.items[p]["s"] = 32_000_000
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
        import random as _r
        _r.seed(3)
        days_ = {(_d.date.today() - _d.timedelta(days=k)).isoformat(): _r.choice([0, 600, 1800, 3600, 5400, 7200, 2400])
                 for k in range(400)}
        daily_ = {}
        for k in range(30):
            dd = (_d.date.today() - _d.timedelta(days=k)).isoformat()
            daily_[dd] = {p: {"s": _r.randint(0, 900), "p": _r.randint(0, 3)} for p in _r.sample(ps, 6)}
        self.stats = types.SimpleNamespace(data={
            "days": days_,
            "tracks": {p: {"sec": 200 * (i % 7 + 1), "plays": i % 5 + 1, "last": "2026-09-01" if i % 4 == 0 else today}
                       for i, p in enumerate(ps)},
            "daily": {**daily_, today: {p: {"s": 100 * (i % 6 + 1), "p": 1} for i, p in enumerate(ps)}}})
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
w.go("stats"); __import__("time").sleep(0.3); dump("lib_stats.jpg")
for _cat in ("repeat", "albums", "artists", "tracks"):          # категории итогов
    w.stats_page.set_cat(_cat)
    w.stats_page.sc.verticalScrollBar().setValue(330)
    dump(f"lib_stats_{_cat}.jpg")
w.stats_page.sc.verticalScrollBar().setValue(0)
import datetime as _dd
_today = _dd.date.today()
w.stats_page.set_period("all"); dump("lib_stats_all.jpg")
w.stats_page._go("year", _today.replace(month=1, day=1)); dump("lib_stats_year.jpg")
w.stats_page._go("day", _today); dump("lib_stats_day.jpg")
w.stats_page.set_period("month")
w.open_artist(next(a for a in w.artists if a["name"] == "Atlus Sound Team")); __import__("time").sleep(0.5); dump("lib_artist_meter.jpg")
w.stats_page.findChildren(__import__("PySide6.QtWidgets", fromlist=["QScrollArea"]).QScrollArea)[0].verticalScrollBar().setValue(500)
dump("lib_stats2.jpg")
w.go("dups"); __import__("time").sleep(0.5); dump("lib_dups.jpg")
import library_stats
print("групп дубликатов:", len(library_stats.find_duplicates(lib.items)))
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
from PySide6.QtGui import QMouseEvent                     # клик по строке текста — плавно доезжает, не прыгает
from PySide6.QtCore import QEvent
w.now.repaint()
_hit, _t, _k = w.now._line_hits[-1]
_s0 = w.now.scroll
app.sendEvent(w.now, QMouseEvent(QEvent.Type.MouseButtonPress, _hit.center(), w.now.mapToGlobal(_hit.center()),
                                 Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
_path = []
for _ in range(8):
    __import__("time").sleep(0.02); w.now.repaint(); _path.append(round(w.now.scroll, 2))
print("текст едет к строке", _k, "от", round(_s0, 2), ":", _path)
assert _s0 < _path[0] < _path[-1] <= _k and _path[0] - _s0 < (_k - _s0) * 0.5, _path
dump("lib_now_glide.jpg")
w.now._glide_until = 0; w.now.scroll = None
_rep = []                                                 # кнопка повтора: список → трек → выкл
owner.set_repeat = lambda m: (_rep.append(m), setattr(owner, "repeat", m))
for _ in range(3):
    w.bar.rep.click()
print("повтор:", _rep)
assert _rep == ["one", "off", "all"], _rep
owner.repeat, owner.shuffle = "one", True
w.bar.sync(); dump("lib_bar_repeat.jpg")
owner.repeat, owner.shuffle = "all", False
from PySide6.QtGui import QWheelEvent                     # колёсико на «Сейчас играет» — шкала громкости
from PySide6.QtCore import QPoint
_vol = []
owner.player.setVolume = lambda v: _vol.append(v)
for _ in range(2):
    app.sendEvent(w.now, QWheelEvent(QPointF(600, 300), QPointF(600, 300), QPoint(0, 0), QPoint(0, 120),
                                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                     Qt.ScrollPhase.NoScrollPhase, False))
print("громкость колёсиком:", _vol)
assert _vol == [0.75, 0.75], _vol                         # заглушка всегда отдаёт 0.7 → +5 %
for _ in range(12):
    __import__("time").sleep(0.03); w.now.repaint()
dump("lib_now_volume.jpg")
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
