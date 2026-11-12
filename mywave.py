# -*- coding: utf-8 -*-
"""«Моя волна» — бесконечный поток из своей музыки, как в Яндекс Музыке.

Без интернета и чужих сервисов: подбор идёт по тому, что ты сам слушаешь —
сколько времени у каждого исполнителя, избранное (♥), что давно не играло,
и что ты пропускаешь («не то» и быстрые перелистывания — реже, а потом совсем нет).
"""
import os
import random

import library as libmod

MODES = {
    "mix": "МИКС",            # всего понемногу, с уклоном в то, что нравится
    "fav": "ЛЮБИМОЕ",         # то, что слушаешь больше всего, и ♥
    "new": "НЕЗНАКОМОЕ",      # ни разу не игравшее — сначала от любимых исполнителей
    "calm": "ЗАБЫТОЕ",        # нравилось, но давно не звучало
}


def _artist_affinity(items, tracks):
    """Исполнитель → 0..1: доля времени, которое ты его слушал (относительно самого любимого)."""
    by = {}
    for path, t in tracks.items():
        it = items.get(path)
        artist = libmod.track_view(path, it)[1] if it else (t.get("artist") or "")
        k = libmod.norm(artist)
        if k:
            by[k] = by.get(k, 0.0) + float(t.get("sec") or 0.0)
    top = max(by.values()) if by else 0.0
    return {k: (v / top) ** 0.5 for k, v in by.items()} if top > 0 else {}


def weight(path, it, mode, stats_tracks, aff, favs, dislikes, today_iso=""):
    t = stats_tracks.get(path) or {}
    plays = int(t.get("plays") or 0)
    sec = float(t.get("sec") or 0.0)
    liked = min(1.0, sec / 900.0)                      # 15 минут прослушивания — «очень нравится»
    a = aff.get(libmod.norm(libmod.track_view(path, it)[1]), 0.0)
    fav = 1.0 if path in favs else 0.0
    dis = float(dislikes.get(path, 0) or 0)
    if dis >= 2:
        return 0.0
    if mode == "fav":
        w = 0.15 + 3.0 * liked + 2.5 * fav + 1.5 * a
    elif mode == "new":
        w = (2.5 if plays == 0 else 0.15) * (0.4 + 1.6 * a)
    elif mode == "calm":
        last = t.get("last") or ""
        old = plays > 0 and last and last < today_iso
        w = (2.5 if old else 0.3) * (0.5 + liked + fav)
    else:
        w = 1.0 + 1.3 * a + 1.0 * fav + 0.8 * liked + (0.5 if plays == 0 else 0.0)
    return w * (0.25 ** dis)


def pick(items, stats_tracks, favs, dislikes, history, mode="mix", n=20, last_artist="", rng=None, today_iso=""):
    """n следующих треков. history — что уже звучало в этой волне (не повторять)."""
    rng = rng or random.Random()
    aff = _artist_affinity(items, stats_tracks)
    seen = {os.path.normcase(p) for p in history}
    pool = []
    for p, it in items.items():
        if os.path.normcase(p) in seen:
            continue
        w = weight(p, it, mode, stats_tracks, aff, favs, dislikes, today_iso)
        if w > 0:
            pool.append([p, w, libmod.norm(libmod.track_view(p, it)[1])])
    if not pool and history:                            # всё переслушано — начинаем круг заново
        return pick(items, stats_tracks, favs, dislikes, [], mode, n, last_artist, rng, today_iso)
    out = []
    prev = libmod.norm(last_artist)
    streak = 0
    while pool and len(out) < n:
        # один исполнитель — не больше двух подряд
        cand = [c for c in pool if not (c[2] == prev and streak >= 2)] or pool
        total = sum(c[1] for c in cand)
        x = rng.random() * total
        for c in cand:
            x -= c[1]
            if x <= 0:
                break
        pool.remove(c)
        out.append(c[0])
        streak = streak + 1 if c[2] == prev else 1
        prev = c[2]
    return out
