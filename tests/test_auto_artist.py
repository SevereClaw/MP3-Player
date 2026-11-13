# -*- coding: utf-8 -*-
"""Неизвестный исполнитель находится в интернете (без сети: ответы Deezer/iTunes подставные),
запоминается в индексе, а отказ «не тот» — навсегда."""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library  # noqa: E402
import online_art as oa  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "FAIL"), name, "->", repr(got)[:120])
    if not ok:
        fails.append(name)


# ---------- online_art.find_artist на подставных ответах
DEEZER = {"data": [
    {"title": "Unravel (Acoustic)", "duration": 250, "artist": {"name": "Кавер-группа"}, "album": {"title": "Covers"}},
    {"title": "unravel", "duration": 239, "artist": {"name": "TK from 凛として時雨"}, "album": {"title": "Fantastic Magic"}},
    {"title": "Lovely Day", "duration": 200, "artist": {"name": "Bill Withers"}, "album": {"title": "Menagerie"}},
    {"title": "Bohemian Rhapsody", "duration": 354, "rank": 100, "artist": {"name": "The Backing Track Collective"},
     "album": {"title": "Backing Tracks"}},
    {"title": "Bohemian Rhapsody", "duration": 355, "rank": 5000, "artist": {"name": "Какой-то кавер"},
     "album": {"title": "Rock Hits"}},
    {"title": "Bohemian Rhapsody (Remastered 2011)", "duration": 354, "rank": 990000, "artist": {"name": "Queen"},
     "album": {"title": "A Night at the Opera"}},
    {"title": "Bohemian Rhapsody (Karaoke Version)", "duration": 354, "rank": 999999, "artist": {"name": "Sing King"},
     "album": {"title": "Hits"}},
    {"title": "Группа крови", "duration": 286, "artist": {"name": "Кино"}, "album": {"title": "Группа крови"}},
]}
calls = []


def fake_json(url, timeout=8):
    calls.append(url)
    return DEEZER if "deezer" in url else {"results": []}


oa._get_json = fake_json
check("по названию и длительности", oa.find_artist("Unravel", 238.4), "TK from 凛として時雨")
check("длительность совсем не та — не подставлять", oa.find_artist("Unravel", 150.0), None)
check("короткое название без длительности — не гадать", oa.find_artist("Love"), None)
check("по названию и альбому", oa.find_artist("unravel", 0, "Fantastic Magic"), "TK from 凛として時雨")
check("«Love» — это не «Lovely Day»", oa.find_artist("Love", 200), None)
check("исполнитель в имени файла", oa.find_artist("kino_gruppa krovi", 0, "", "Кино_Группа крови"), "Кино")
check("оригинал, а не караоке и не перепевка", oa.find_artist("Bohemian Rhapsody", 354), "Queen")
check("длительность гуляет (рип из игры) — самый популярный с тем же названием",
      oa.find_artist("Bohemian Rhapsody", 340), "Queen")
check("но не совсем другая длина", oa.find_artist("Bohemian Rhapsody", 120), None)
check("пустое название", oa.find_artist("", 200), None)


def offline(url, timeout=8):
    raise OSError("нет сети")


oa._get_json = offline
try:
    oa.find_artist("Unravel", 238)
    check("нет сети → Offline", "нет исключения", "Offline")
except oa.Offline:
    check("нет сети → Offline", "Offline", "Offline")

# ---------- индекс
root = tempfile.mkdtemp()
lib = library.Library(os.path.join(root, "library.json"))
A = os.path.normpath("/m/unravel.mp3")                    # ни тега, ни имени — искать
B = os.path.normpath("/m/Кино - Война.mp3")               # исполнитель в имени файла — не искать
C = os.path.normpath("/m/song.mp3")                       # есть тег — не искать
D = os.path.normpath("/m/qqq.mp3")                        # не найдётся
lib.items = {A: {"s": 1, "m": 1, "t": "unravel", "a": "", "al": "", "n": 0, "d": 238.0},
             B: {"s": 1, "m": 1, "t": "", "a": "", "al": "", "n": 0, "d": 228.0},
             C: {"s": 1, "m": 1, "t": "Song", "a": "Ado", "al": "", "n": 0, "d": 100.0},
             D: {"s": 1, "m": 1, "t": "", "a": "", "al": "", "n": 0, "d": 50.0}}
lib._rebuild_hay()
check("кого искать", sorted(lib.unknown_paths()), sorted([A, D]))
check("пока неизвестен", library.track_view(A, lib.items[A])[1], library.UNKNOWN_ARTIST)

asked = []


def lookup(title, dur, album, name):
    asked.append(title)
    return "TK from 凛として時雨" if title == "unravel" else None


ev = threading.Event()
lib.find_artists(lookup=lookup, done=ev.set)
ev.wait(20)
check("спросили только про неизвестных", sorted(asked), ["qqq", "unravel"])
check("исполнитель подставился", library.track_view(A, lib.items[A])[1], "TK from 凛として時雨")
check("помечен как найденный в интернете", lib.auto_artist(A), "TK from 凛として時雨")
check("ищется поиском", lib.search("凛として"), [A])
check("версия выросла (окно перерисуется)", lib.auto_version > 0, True)
check("ненайденное остаётся неизвестным", library.track_view(D, lib.items[D])[1], library.UNKNOWN_ARTIST)
check("повторно не ищем (месяц)", lib.unknown_paths(), [])
check("в списке исполнителей", [a["name"] for a in library.build_artists(lib.items)][:1] != [library.UNKNOWN_ARTIST], True)

lib2 = library.Library(os.path.join(root, "library.json"))
check("сохранилось на диск", lib2.auto_artist(A), "TK from 凛として時雨")

kept = library._keep_auto({"s": 2, "m": 2, "t": "unravel", "a": ""}, lib.items[A])
check("после пересканирования файла не теряется", kept.get("ag"), "TK from 凛として時雨")

lib.reject_artist(A)
check("«не тот» — снова неизвестен", library.track_view(A, lib.items[A])[1], library.UNKNOWN_ARTIST)
check("«не тот» — больше не искать", lib.unknown_paths(now=10 ** 11), [D])
lib.items[C]["ag"] = "Чужой"
check("свой тег важнее найденного", (library.track_view(C, lib.items[C])[1], lib.auto_artist(C)), ("Ado", ""))


def no_net(*a):
    raise oa.Offline("нет связи")


lib.items[D].pop("agt")
ev = threading.Event()
lib.find_artists(lookup=no_net, done=ev.set)
ev.wait(20)
check("нет сети — попробуем в другой раз", lib.unknown_paths(), [D])

print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
