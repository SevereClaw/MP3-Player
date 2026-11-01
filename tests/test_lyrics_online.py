# -*- coding: utf-8 -*-
"""Проверка поиска текстов на lrclib.net (автосборка; нужен интернет)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lyrics_online as L  # noqa: E402
from lyrics import load_file  # noqa: E402

CASES = [  # исполнитель, название (как в тегах или имени файла), альбом, длительность
    ("Rosa Walton", "I Really Want to Stay at Your House", "Cyberpunk 2077", 246),
    ("Atlus", "Colors Flying High -opening movie version", "Persona 5: The Royal", 96),
    ("Король и Шут", "Лесник", "", 0),
    ("Lotus Juice", "01. Full Moon Full Life (Official Audio)", "", 0),
]
found = 0
for a, t, al, d in CASES:
    try:
        text, why = L.fetch(a, t, al, d)
    except Exception as e:
        text, why = None, f"ошибка {type(e).__name__}: {e}"
    print(f"{a} — {t}: {why}" + (f" ({text.count(chr(10)) + 1} строк)" if text else ""))
    if text:
        found += 1
        track = os.path.join(tempfile.mkdtemp(), f"{a} - test.mp3")
        open(track, "wb").close()
        p = L.save(text, track, a, t, al)
        ly = load_file(p)
        assert ly and len(ly) > 3, "сохранённый .lrc не читается плеером"
print(f"найдено {found} из {len(CASES)}")
sys.exit(0 if found >= 2 else 1)
