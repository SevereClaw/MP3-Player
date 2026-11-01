# -*- coding: utf-8 -*-
"""Эквалайзер на Windows: PyAV декодирует файл, эквалайзер меняет полосы как задано,
DspPlayer открывает файл, знает длительность и мотает."""
import os
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import eq as eqmod  # noqa: E402
import dsp_player  # noqa: E402

fails = []
print("PyAV:", getattr(dsp_player.av, "__version__", None), dsp_player.AV_ERROR or "ok")
if not dsp_player.available():
    print("FAIL: PyAV недоступен")
    sys.exit(1)


def make_wav(path, parts, sr=44100):
    sig = []
    for f, db, sec in parts:
        t = np.arange(int(sr * sec)) / sr
        sig.append(10 ** (db / 20) * np.sin(2 * np.pi * f * t))
    a = (np.concatenate(sig) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.stack([a, a], 1).tobytes())


d = tempfile.mkdtemp(prefix="Эквалайзер ")
path = os.path.join(d, "тон 100 Гц и 8 кГц.wav")
make_wav(path, [(100, -12, 3), (8000, -12, 3)])


def render(gains, start_ms=0):
    e = eqmod.Equalizer(gains, True)
    r = dsp_player.Renderer(path, e, start_ms)
    out = []
    while True:
        x = r.read()
        if x is None:
            break
        out.append(x)
    r.close()
    return np.concatenate(out), r.duration_ms


def rms_db(x):
    return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)


flat, dur = render(eqmod.PRESETS["Плоский"])
bass, _ = render(eqmod.PRESETS["Больше баса"])
print("длительность:", dur, "мс; отсчётов", len(flat), "(48 кГц:", len(flat) / 48000, "с)")
if abs(dur - 6000) > 100:
    fails.append("длительность")
sr = 48000
seg = lambda x, a, b: x[int(a * sr):int(b * sr)]
low = rms_db(seg(bass, 0.5, 2.5)) - rms_db(seg(flat, 0.5, 2.5))
high = rms_db(seg(bass, 3.5, 5.5)) - rms_db(seg(flat, 3.5, 5.5))
want_low = float(np.interp(np.log10(100), np.log10(eqmod.BANDS), eqmod.PRESETS["Больше баса"])) + eqmod.preamp_db(eqmod.PRESETS["Больше баса"])
want_high = eqmod.preamp_db(eqmod.PRESETS["Больше баса"])
print(f"«Больше баса»: 100 Гц {low:+.2f} дБ (ждём {want_low:+.2f}), 8 кГц {high:+.2f} дБ (ждём {want_high:+.2f})")
if abs(low - want_low) > 0.3 or abs(high - want_high) > 0.3:
    fails.append("полосы")
part, _ = render(eqmod.PRESETS["Плоский"], start_ms=4000)
print("с 4-й секунды:", len(part) / sr, "с (ждём 2.0)")
if abs(len(part) / sr - 2.0) > 0.1:
    fails.append("перемотка")
if np.abs(bass).max() > 1.0:
    fails.append("перегруз")

from PySide6.QtCore import QCoreApplication, QTimer, QUrl  # noqa: E402
app = QCoreApplication(sys.argv)
p = dsp_player.DspPlayer(eqmod.Equalizer())
seen = []
p.mediaStatusChanged.connect(lambda st: seen.append(st.name))
p.setSource(QUrl.fromLocalFile(path))
QTimer.singleShot(3000, app.quit)
app.exec()
print("DspPlayer:", seen, "длительность", p.duration())
if "BufferedMedia" not in seen or abs(p.duration() - 6000) > 100:
    fails.append("DspPlayer")
p.setPosition(2500)
print("позиция после перемотки:", p.position())
if p.position() != 2500:
    fails.append("позиция")
if fails:
    print("FAIL:", fails)
    sys.exit(1)
print("OK: эквалайзер работает")
