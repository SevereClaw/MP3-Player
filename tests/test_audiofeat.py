# -*- coding: utf-8 -*-
"""«Характер звука» (audiofeat): тяжёлое и тихое различаются, файл читается через PyAV, разбор идёт в фоне."""
import os
import sys
import tempfile
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audiofeat as af  # noqa: E402

SR = af.SR
fails = []


def check(name, ok, info=""):
    print(("OK  " if ok else "FAIL"), name, info)
    if not ok:
        fails.append(name)


def rock(bpm=150, sec=40, seed=1):
    r = np.random.default_rng(seed); n = SR * sec; t = np.arange(n) / SR
    x = np.zeros(n, np.float32); beat = 60.0 / bpm
    for k in range(int(sec / beat)):
        i = int(k * beat * SR); m = min(n - i, int(0.18 * SR)); tt = np.arange(m) / SR
        x[i:i+m] += (np.sin(2*np.pi*70*tt) * np.exp(-tt*18)).astype(np.float32) * 0.9          # бочка
        if k % 2: x[i:i+m] += (r.standard_normal(m) * np.exp(-tt*25)).astype(np.float32) * 0.6  # малый
        h = int((k + 0.5) * beat * SR); m2 = min(n - h, int(0.05 * SR))
        if m2 > 0: x[h:h+m2] += (r.standard_normal(m2) * np.exp(-np.arange(m2)/SR*80)).astype(np.float32) * 0.3
    g = np.tanh(4 * (np.sin(2*np.pi*110*t) + np.sin(2*np.pi*165*t) + np.sin(2*np.pi*220*t))) * 0.35   # перегруз
    return np.clip(x + g.astype(np.float32), -1, 1)
def piano(sec=40, seed=2, gap=0.9, vol=0.12):
    r = np.random.default_rng(seed); n = SR * sec; x = np.zeros(n, np.float32)
    notes = [220, 261.6, 293.7, 329.6, 392, 440]
    t0 = 0.0
    while t0 < sec - 3:
        i = int(t0 * SR); m = min(n - i, int(2.5 * SR)); tt = np.arange(m) / SR; f = r.choice(notes)
        x[i:i+m] += ((np.sin(2*np.pi*f*tt) + 0.3*np.sin(2*np.pi*2*f*tt)) * np.exp(-tt*1.6) * (1-np.exp(-tt*200))).astype(np.float32) * vol
        t0 += gap * r.uniform(0.7, 1.4)
    return x
def dance(bpm=128, sec=40, seed=3):
    r = np.random.default_rng(seed); n = SR * sec; t = np.arange(n) / SR; x = np.zeros(n, np.float32); beat = 60.0 / bpm
    for k in range(int(sec / beat)):
        i = int(k * beat * SR); m = min(n - i, int(0.2 * SR)); tt = np.arange(m) / SR
        x[i:i+m] += (np.sin(2*np.pi*(55+60*np.exp(-tt*30))*tt) * np.exp(-tt*12)).astype(np.float32) * 0.8
        h = int((k + 0.5) * beat * SR); m2 = min(n - h, int(0.04 * SR))
        if m2 > 0: x[h:h+m2] += (r.standard_normal(m2) * np.exp(-np.arange(m2)/SR*90)).astype(np.float32) * 0.25
    x += (np.sign(np.sin(2*np.pi*110*t)) * 0.12 * (0.5 + 0.5*np.sin(2*np.pi*t*bpm/60/2))).astype(np.float32)
    return np.clip(x, -1, 1)
def ambient(sec=40):
    t = np.arange(SR * sec) / SR
    return ((np.sin(2*np.pi*110*t) + np.sin(2*np.pi*164.8*t)*0.6 + np.sin(2*np.pi*220.3*t)*0.4) * 0.08 * (0.6 + 0.4*np.sin(2*np.pi*t/9))).astype(np.float32)


S = {"rock150": rock(150), "rock170": rock(170, seed=5), "dance128": dance(128), "dance100": dance(100, seed=7),
     "piano": piano(), "piano_quiet": piano(seed=9, gap=1.4, vol=0.07), "ambient": ambient()}
F = {k: af.features_from_pcm(v) for k, v in S.items()}
for k, f in F.items():
    print(f"     {k:12s}", f)
d = lambda a, b: af.distance(F[a], F[b])                  # noqa: E731
check("рок энергичнее пианино", F["rock150"]["e"] > F["piano"]["e"] + 0.3)
check("эмбиент — самое спокойное", F["ambient"]["e"] < min(F["rock150"]["e"], F["dance128"]["e"]) - 0.3)
check("два рока — соседи", d("rock150", "rock170") < 0.2, f'{d("rock150", "rock170"):.2f}')
check("два пианино — соседи", d("piano", "piano_quiet") < 0.25, f'{d("piano", "piano_quiet"):.2f}')
check("рок и пианино — далеко", d("rock150", "piano") > 0.5, f'{d("rock150", "piano"):.2f}')
check("танцевальное ближе к року, чем к пианино", d("dance128", "rock150") < d("dance128", "piano") * 0.6)
check("темп танцевального 128", abs(F["dance128"]["t"] - 128) < 4, F["dance128"]["t"])
check("темп танцевального 100", abs(F["dance100"]["t"] - 100) < 4, F["dance100"]["t"])
check("у эмбиента темпа нет", F["ambient"]["t"] == 0)
check("похожее — множитель около 1", af.flow_factor(F["rock150"], F["rock170"]) > 0.85)
check("рок → пианино — множитель маленький", af.flow_factor(F["rock150"], F["piano"]) < 0.08)
check("неразобранный трек — средний множитель", 0.2 < af.flow_factor(F["rock150"], None) < 0.7)
check("тишина и обрывок — без характеристик",
      af.features_from_pcm(np.zeros(SR * 10, np.float32)) is None and af.features_from_pcm(S["piano"][:SR]) is None)

# ---------- настоящий файл через PyAV (кусок из середины)
tmp = tempfile.mkdtemp()


def make_wav(name, x, sr=44100):
    y = np.interp(np.arange(int(len(x) * sr / SR)) * SR / sr, np.arange(len(x)), x)
    path = os.path.join(tmp, name)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
        w.writeframes(np.column_stack([pcm, pcm]).tobytes())
    return path


files = {"rock": make_wav("рок.wav", np.tile(S["rock150"], 3)), "piano": make_wav("пианино.wav", np.tile(S["piano"], 3))}
try:
    import av  # noqa: F401
    have_av = True
except Exception:
    have_av = False
if have_av:
    x = af.decode_excerpt(files["rock"])
    check("PyAV: 45 секунд моно", x is not None and abs(len(x) - SR * 45) < SR, None if x is None else len(x) / SR)
    fr, fp = af.analyze(files["rock"]), af.analyze(files["piano"])
    check("из файла: рок как рок", fr is not None and abs(fr["e"] - F["rock150"]["e"]) < 0.08, fr)
    check("из файла: пианино как пианино", fp is not None and abs(fp["e"] - F["piano"]["e"]) < 0.08, fp)
    check("битый файл не роняет разбор", af.analyze(os.path.join(tmp, "нет такого.mp3")) is None)
else:
    print("     (PyAV нет — проверка чтения файлов пропущена)")

# ---------- фоновый разбор и память
store = af.FeatureStore(os.path.join(tmp, "audiofeat.json"))
items = {p: {"s": os.path.getsize(p), "m": int(os.path.getmtime(p))} for p in files.values()}
calls = []


def fake(p):
    calls.append(p)
    return F["rock150"] if "рок" in p else F["piano"]


check("разбор запустился", store.analyze_all(items, analyzer=fake, rest=0, first=[files["piano"]]))
for _ in range(100):
    if not store.busy:
        break
    time.sleep(0.05)
check("сначала то, что нужно волне", calls == [files["piano"], files["rock"]], [os.path.basename(c) for c in calls])
check("характер запомнен", store.get(files["rock"]) == F["rock150"] and store.get(files["piano"]) == F["piano"])
store2 = af.FeatureStore(os.path.join(tmp, "audiofeat.json"))
check("сохранилось на диск", store2.get(files["rock"]) == F["rock150"])
check("второй раз те же файлы не слушаем", store2.analyze_all(items, analyzer=fake, rest=0) is False)
items[files["rock"]]["m"] += 5
ev = threading.Event()
check("файл изменился — слушаем заново", store2.analyze_all(items, analyzer=lambda p: (ev.set(), F["piano"])[1], rest=0)
      and ev.wait(5))

print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
