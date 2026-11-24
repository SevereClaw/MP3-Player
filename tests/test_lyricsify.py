# -*- coding: utf-8 -*-
"""lyricsify.com: адреса страниц и разбор текста с таймкодами (без интернета)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lyrics_online as L  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("OK  " if ok else "FAIL"), name, info)
    if not ok:
        fails.append(name)


check("адрес: исполнитель", L.lyricsify_slug("Mariah Carey") == "mariah-carey")
check("адрес: название с апострофом и скобками",
      L.lyricsify_slug("Don't Stop Me Now (feat. X)") == "dont-stop-me-now-feat-x", L.lyricsify_slug("Don't Stop Me Now (feat. X)"))
check("адрес: лишние пробелы и знаки", L.lyricsify_slug("  All I Want For Christmas Is You!  ") == "all-i-want-for-christmas-is-you")
page = ('<div class="x"><h3>t</h3></div><div id="lyrics" class="details">[ar:A]<br>[00:01.00]Hello &amp; hi'
        '<br/>[00:03.50]Line two<br>\n[00:05.00]Three</div>')
text = L.lyricsify_extract(page)
check("текст со страницы", text == "[ar:A]\n[00:01.00]Hello & hi\n[00:03.50]Line two\n[00:05.00]Three", repr(text))
check("без таймкодов — не берём", L.lyricsify_extract('<div class="details">just plain text</div>') == "")
check("нет блока — пусто", L.lyricsify_extract("<html>Just a moment...</html>") == "")

_real = L.fetch_lyricsify
check("без исполнителя — понятная причина", "исполнитель" in _real("", "Song")[1])

# ---------- NetEase: выбор песни и чистка текста
songs = [
    {"id": 1, "name": "Other Song", "artists": [{"name": "Someone"}], "duration": 200000},
    {"id": 2, "name": "未来は僕らの手の中", "artists": [{"name": "結束バンド"}], "duration": 214000},
    {"id": 3, "name": "Mirai wa Bokura no Te no Naka", "artists": [{"name": "Kessoku Band"}], "duration": 300000},
]
sg = L.netease_pick(songs, "", "Mirai wa Bokura no Te no Naka", 213.5)
check("NetEase: японское название, длительность совпала", sg and sg["id"] == 2, sg)
sg = L.netease_pick(songs, "Kessoku Band", "Mirai wa Bokura no Te no Naka", 0)
check("NetEase: без длительности — по названию и исполнителю", sg and sg["id"] == 3, sg)
sg = L.netease_pick(songs, "", "Совсем другая", 250)
check("NetEase: ничего похожего — ничего", sg is None, sg)
raw = "[00:00.00] 作词 : 樋口愛\n[00:01.00] 作曲 : 音羽-otoha-\n[00:12.30]真っ暗闇\n[00:15.00]line two\n[00:18.00]line three"
clean = L.netease_clean(raw)
check("NetEase: без строк-титров", "作词" not in clean and clean.startswith("[00:12.30]"), repr(clean))
check("NetEase: инструментал — пусто", L.netease_clean("[00:00.00]纯音乐，请欣赏") == "")

# lrclib ничего не дал, lyricsify нашёл → текст и пометка источника
L.fetch_lrclib = lambda *a, **k: (None, "текст не найден")
L.fetch_netease = lambda *a, **k: (None, "подходящей песни нет")
L.fetch_lyricsify = lambda a, t: ("[00:01.00]a\n[00:02.00]b\n[00:03.00]c", "")
t, why = L.fetch("A", "B")
check("запасной источник", t and why == "lyricsify.com", why)


def offline(*a, **k):
    raise OSError("нет сети")


L.fetch_lyricsify = lambda a, t: (None, "Cloudflare не пустил программу (403)")
t, why = L.fetch("A", "B")
check("причина по каждому сайту", t is None and "lrclib" in why and "NetEase" in why and "Cloudflare" in why, why)
L.fetch_netease = lambda *a, **k: ("[00:01.00]a\n[00:02.00]b\n[00:03.00]c", "")
t, why = L.fetch("A", "B")
check("NetEase раньше lyricsify", t and "NetEase" in why, why)
L.fetch_lrclib = offline
L.fetch_netease = offline
L.fetch_lyricsify = lambda a, t: (None, "нет связи")
try:
    L.fetch("A", "B")
    check("нет сети — ошибка как раньше", False)
except OSError:
    check("нет сети — ошибка как раньше", True)
print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
