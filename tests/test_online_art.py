# -*- coding: utf-8 -*-
"""Живая проверка: обложки и фото исполнителей находятся в интернете (iTunes, Deezer)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import online_art as oa  # noqa: E402

fails = []


def check(name, got, ok):
    print(("OK  " if ok else "FAIL"), name, "->", (got or "")[:100])
    if not ok:
        fails.append(name)


u = oa.album_cover_url("Кино", "Группа крови")
check("обложка: Кино — Группа крови", u, bool(u))
u = oa.album_cover_url("ATLUS Sound Team", "Persona 3 Reload Original Soundtrack")
check("обложка: Persona 3 Reload OST", u, bool(u))
u = oa.album_cover_url("Shoji Meguro", "Persona 3 Portable Original Soundtrack")
check("обложка: P3P OST (другой исполнитель в базе)", u, bool(u))
u = oa.track_cover_url("Ado", "新時代")
check("обложка по треку: Ado — 新時代", u, bool(u))
u = oa.artist_photo_url("Ado")
check("фото: Ado", u, bool(u))
u = oa.artist_photo_url("Кино")
check("фото: Кино", u, bool(u))
u = oa.album_cover_url("Несуществующая группа qqzx", "Альбом которого нет zzq")
check("чужое не подставляется", u, u is None)
check("похожие названия", "", oa.similar("Persona 3 Original Soundtrack", "PERSONA3 Original Soundtrack"))
check("разные названия", "", not oa.similar("Mass Destruction", "Destruction AllStars"))
print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
