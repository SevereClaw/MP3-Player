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
    "calm": "ЗАБЫТОЕ",        # нравилось, но не звучало месяц и больше
}

FORGOT_DAYS = 30               # столько не звучало — «забытое»
CYCLE = ("known", "known", "new", "old")   # «Микс»: два знакомых → новый → забытый
ARTIST_GAP = 4                 # один исполнитель — не чаще раза в пять треков


# ------------------------------------------------------------------ настроение сессии
def mood_note(mood, artist, album, delta):
    """Реакция на трек в этой волне: delta < 0 — переключил в начале / «не то», > 0 — дослушал / вернул в начало.
    mood — словарь, живёт до конца волны: {"a": {исполнитель: балл}, "al": {альбом: балл}}."""
    for key, name in (("a", artist), ("al", album)):
        k = libmod.norm(name or "")
        if not k or (key == "a" and k == libmod.norm(libmod.UNKNOWN_ARTIST)):
            continue
        d = mood.setdefault(key, {})
        d[k] = max(-3.0, min(3.0, d.get(k, 0.0) + delta))
    return mood


def mood_factor(mood, artist_key, album_key):
    """Во сколько раз чаще (или реже) давать трек при таком настроении сессии."""
    if not mood:
        return 1.0
    s = 0.9 * (mood.get("a") or {}).get(artist_key, 0.0) + 0.6 * (mood.get("al") or {}).get(album_key, 0.0)
    return max(0.08, min(4.0, 2.0 ** s))


def kind_of(path, t, favs, cutoff_iso):
    """Что это за трек для слушателя: new — ни разу не играл, old — нравился, но давно не звучал, known — остальное."""
    plays = int((t or {}).get("plays") or 0)
    if plays == 0:
        return "new"
    last = (t or {}).get("last") or ""
    liked = float((t or {}).get("sec") or 0.0) >= 120 or path in favs
    if liked and cutoff_iso and (not last or last <= cutoff_iso):
        return "old"
    return "known"


def _cutoff(today_iso):
    import datetime
    try:
        day = datetime.date.fromisoformat(today_iso) if today_iso else datetime.date.today()
    except ValueError:
        day = datetime.date.today()
    return (day - datetime.timedelta(days=FORGOT_DAYS)).isoformat()




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


def weight(path, it, mode, stats_tracks, aff, favs, dislikes, today_iso="", kind=None, slot=None):
    """Вес трека в розыгрыше. kind — из kind_of(); slot — какое место цикла «Микса» заполняем."""
    t = stats_tracks.get(path) or {}
    plays = int(t.get("plays") or 0)
    sec = float(t.get("sec") or 0.0)
    liked = min(1.0, sec / 900.0)                      # 15 минут прослушивания — «очень нравится»
    a = aff.get(libmod.norm(libmod.track_view(path, it)[1]), 0.0)
    fav = 1.0 if path in favs else 0.0
    dis = float(dislikes.get(path, 0) or 0)
    if dis >= 2:
        return 0.0
    if kind is None:
        kind = kind_of(path, t, favs, _cutoff(today_iso))
    if mode == "fav":
        w = 0.15 + 3.0 * liked + 2.5 * fav + 1.5 * a
    elif mode == "new":
        w = (2.5 if plays == 0 else 0.15) * (0.4 + 1.6 * a)
    elif mode == "calm":
        w = (2.5 if kind == "old" else 0.05) * (0.5 + liked + fav)
    elif slot == "new":
        w = 0.4 + 1.6 * a                              # новое — сначала от любимых исполнителей
    elif slot == "old":
        w = 0.5 + liked + fav
    else:
        w = 1.0 + 1.3 * a + 1.0 * fav + 0.8 * liked
    return w * (0.25 ** dis)


def pick(items, stats_tracks, favs, dislikes, history, mode="mix", n=20, last_artist="", rng=None, today_iso="",
         mood=None, recent_artists=None, slot=0, feats=None, prev_path=None):
    """n следующих треков. history — что уже звучало в этой волне (не повторять).
    feats — функция путь → «характер звука» (audiofeat) или None; prev_path — после какого трека подбираем:
    следующий берётся похожим по энергии и темпу, дальше — по цепочке, так что поток меняется плавно.
    mood — настроение сессии (mood_note); recent_artists — кто играл последним (для разрядки исполнителей);
    slot — сколько треков волна уже выдала (чтобы цикл «Микса» продолжался, а не начинался заново)."""
    rng = rng or random.Random()
    aff = _artist_affinity(items, stats_tracks)
    cutoff = _cutoff(today_iso)
    unknown = libmod.norm(libmod.UNKNOWN_ARTIST)
    seen = {os.path.normcase(p) for p in history}
    pool = []                                           # [путь, исполнитель, вид, множитель настроения, элемент]
    for p, it in items.items():
        if os.path.normcase(p) in seen or float(dislikes.get(p, 0) or 0) >= 2:
            continue
        _t, artist, album = libmod.track_view(p, it)[:3]
        ak = libmod.norm(artist)
        pool.append([p, ak, kind_of(p, stats_tracks.get(p), favs, cutoff),
                     mood_factor(mood, ak, libmod.norm(album)), it])
    if not pool and history:                            # всё переслушано — начинаем круг заново
        return pick(items, stats_tracks, favs, dislikes, [], mode, n, last_artist, rng, today_iso,
                    mood, recent_artists, slot, feats, prev_path)
    recent = [libmod.norm(a) for a in (recent_artists or ([last_artist] if last_artist else []))][-ARTIST_GAP:]
    out = []
    flow = None
    if feats is not None:
        import audiofeat
        flow = audiofeat.flow_factor
    for c in pool:
        c.append(feats(c[0]) if flow else None)         # c[5] — характер звука (или None, если ещё не разобран)
        c.append({})                                    # c[6] — уже посчитанные веса
    pf = feats(prev_path) if (feats is not None and prev_path) else None
    while pool and len(out) < n:
        want = CYCLE[(slot + len(out)) % len(CYCLE)] if mode == "mix" else None
        cand = [c for c in pool if c[2] == want] if want else pool
        if not cand:                                    # такого вида не осталось — берём из всех
            cand, want = pool, None
        # один исполнитель — не чаще раза в пять треков; мало исполнителей — правило мягко отступает
        for gap in (ARTIST_GAP, 2, 1, 0):
            block = {a for a in recent[-gap:] if a and a != unknown} if gap else set()
            free = [c for c in cand if c[1] not in block]
            if free:
                cand = free
                break
        ws = []
        for c in cand:                                  # вес трека для этого места цикла считаем один раз
            w = c[6].get(want)
            if w is None:
                w = c[6][want] = weight(c[0], c[4], mode, stats_tracks, aff, favs, dislikes, today_iso,
                                        c[2], want) * c[3]
            ws.append(w)
        if flow is not None and pf:                     # плавность: похожее на предыдущий трек — намного вероятнее
            ws = [w * flow(pf, c[5]) for w, c in zip(ws, cand)]
        total = sum(ws)
        if total <= 0:
            choice = cand[0]
        else:
            x = rng.random() * total
            choice = cand[-1]
            for c, w in zip(cand, ws):
                x -= w
                if x <= 0:
                    choice = c
                    break
        pool.remove(choice)
        out.append(choice[0])
        recent = (recent + [choice[1]])[-ARTIST_GAP:]
        if flow is not None and choice[5]:
            pf = choice[5]
    return out
