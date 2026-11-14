# -*- coding: utf-8 -*-
"""«Моя волна»: цикл «Микса», разрядка исполнителей, настоящее «Забытое» и настроение сессии."""
import collections
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library  # noqa: E402
import mywave as mw  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("OK  " if ok else "FAIL"), name, info)
    if not ok:
        fails.append(name)


TODAY = "2026-11-14"
items, stats = {}, {}
ARTISTS = [f"Artist{i}" for i in range(12)]
n = 0
for ai, a in enumerate(ARTISTS):
    for al in ("A", "B"):
        for k in range(5):                               # 12 исполнителей × 2 альбома × 5 треков = 120
            p = os.path.normpath(f"/m/{a}/{al}/{k}.mp3")
            items[p] = {"t": f"T{n}", "a": a, "al": f"{a} {al}", "d": 200.0}
            m = n % 3
            if m == 0:                                   # знакомое: слушал на днях
                stats[p] = {"plays": 5, "sec": 600.0, "last": "2026-11-10", "artist": a}
            elif m == 1:                                 # забытое: нравилось, но два месяца не звучало
                stats[p] = {"plays": 4, "sec": 500.0, "last": "2026-09-01", "artist": a}
            n += 1                                       # m == 2 — ни разу не играло


def kinds(paths):
    cut = mw._cutoff(TODAY)
    return [mw.kind_of(p, stats.get(p), set(), cut) for p in paths]


def artist(p):
    return items[p]["a"]


# ---------- виды треков
some = list(items)
k = collections.Counter(kinds(some))
check("виды: поровну знакомого, забытого и нового", k["known"] == k["old"] == k["new"] == 40, dict(k))
yesterday = {"plays": 9, "sec": 900.0, "last": "2026-11-13"}
check("слушал вчера — это не «забытое»", mw.kind_of("x", yesterday, set(), mw._cutoff(TODAY)) == "known")
check("29 дней назад — ещё не забытое",
      mw.kind_of("x", {"plays": 3, "sec": 300.0, "last": "2026-10-16"}, set(), mw._cutoff(TODAY)) == "known")
check("31 день назад — забытое",
      mw.kind_of("x", {"plays": 3, "sec": 300.0, "last": "2026-10-14"}, set(), mw._cutoff(TODAY)) == "old")
check("включил раз на 10 секунд и бросил — не «нравилось»",
      mw.kind_of("x", {"plays": 1, "sec": 10.0, "last": "2026-01-01"}, set(), mw._cutoff(TODAY)) == "known")

# ---------- «Микс»: два знакомых → новый → забытый
out = mw.pick(items, stats, set(), {}, [], "mix", n=20, rng=random.Random(1), today_iso=TODAY)
ks = kinds(out)
check("цикл «Микса»", ks == ["known", "known", "new", "old"] * 5, ks[:8])
more = mw.pick(items, stats, set(), {}, out[:6], "mix", n=4, rng=random.Random(2), today_iso=TODAY, slot=6)
check("цикл продолжается с того же места", kinds(more) == ["new", "old", "known", "known"], kinds(more))
check("без повторов", len(set(out)) == 20 and not set(out[:6]) & set(more))

# ---------- один исполнитель не чаще раза в пять треков
bad = 0
for seed in range(30):
    o = mw.pick(items, stats, set(), {}, [], "mix", n=40, rng=random.Random(seed), today_iso=TODAY)
    arts = [artist(p) for p in o]
    bad += sum(1 for i in range(len(arts)) if arts[i] in arts[max(0, i - 4):i])
check("разрядка исполнителей (30 волн по 40 треков)", bad == 0, f"нарушений: {bad}")
o = mw.pick(items, stats, set(), {}, [], "mix", n=3, rng=random.Random(5), today_iso=TODAY,
            recent_artists=["Artist1", "Artist2", "Artist3", "Artist4"])
check("учитывает, кто играл перед этим", not {artist(p) for p in o} & {"Artist1", "Artist2", "Artist3", "Artist4"})
two = {p: it for p, it in items.items() if it["a"] in ("Artist0", "Artist1")}
o = mw.pick(two, stats, set(), {}, [], "mix", n=20, rng=random.Random(3), today_iso=TODAY)
check("мало исполнителей — волна не встаёт", len(o) == 20)

# ---------- «Забытое»
o = mw.pick(items, stats, set(), {}, [], "calm", n=30, rng=random.Random(4), today_iso=TODAY)
share = kinds(o).count("old") / len(o)
check("«Забытое» — почти только давно не звучавшее", share >= 0.9, f"{share:.0%}")

# ---------- настроение сессии
def share_of(mood, who, runs=40, first=8):
    c = tot = 0
    for seed in range(runs):
        o = mw.pick(items, stats, set(), {}, [], "mix", n=first, rng=random.Random(100 + seed),
                    today_iso=TODAY, mood=mood)
        c += sum(1 for p in o if artist(p) == who)
        tot += len(o)
    return c / tot


base = share_of(None, "Artist3")
down = share_of(mw.mood_note(mw.mood_note({}, "Artist3", "Artist3 A", -1), "Artist3", "Artist3 B", -1), "Artist3")
up = share_of(mw.mood_note(mw.mood_note({}, "Artist3", "Artist3 A", +1), "Artist3", "Artist3 A", +1), "Artist3")
check("переключил дважды — исполнитель реже", down < base * 0.5, f"{base:.1%} → {down:.1%}")
check("дослушал дважды — исполнитель чаще", up > base * 1.5, f"{base:.1%} → {up:.1%}")
m = {}
for _ in range(10):
    mw.mood_note(m, "Artist3", "Artist3 A", -1)
check("даже после десяти пропусков исполнитель не пропадает насовсем",
      0.0 < mw.mood_factor(m, library.norm("Artist3"), library.norm("Artist3 A")) < 0.2)
check("чужих настроение не трогает", mw.mood_factor(m, library.norm("Artist5"), library.norm("Artist5 A")) == 1.0)
check("«неизвестный исполнитель» не штрафуется скопом",
      "a" not in mw.mood_note({}, library.UNKNOWN_ARTIST, "", -1))

# ---------- «не то» дважды — трек больше не попадается
ban = next(iter(items))
o = mw.pick(items, stats, set(), {ban: 2}, [], "mix", n=120, rng=random.Random(9), today_iso=TODAY)
check("дважды «не то» — навсегда из волны", ban not in o and len(o) == 119)
o = mw.pick(items, stats, set(), {}, list(items), "mix", n=10, rng=random.Random(9), today_iso=TODAY)
check("всё переслушано — круг начинается заново", len(o) == 10)

print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
