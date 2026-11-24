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

# lrclib ничего не дал, lyricsify нашёл → текст и пометка источника
L.fetch_lrclib = lambda *a, **k: (None, "текст не найден")
L.fetch_lyricsify = lambda a, t: ("[00:01.00]a\n[00:02.00]b\n[00:03.00]c", "")
t, why = L.fetch("A", "B")
check("запасной источник", t and why == "lyricsify.com", why)


def offline(*a, **k):
    raise OSError("нет сети")


L.fetch_lyricsify = lambda a, t: (None, "Cloudflare не пустил программу (403)")
t, why = L.fetch("A", "B")
check("причина по каждому сайту", t is None and "lrclib" in why and "Cloudflare" in why, why)
L.fetch_lrclib = offline
L.fetch_lyricsify = lambda a, t: (None, "нет связи")
try:
    L.fetch("A", "B")
    check("нет сети — ошибка как раньше", False)
except OSError:
    check("нет сети — ошибка как раньше", True)
print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
