# -*- coding: utf-8 -*-
"""Выравнивание громкости на настоящей Windows: Qt декодирует файлы, мы меряем LUFS."""
import os
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loudness as L  # noqa: E402


def make_wav(path, db, sec=12, sr=44100, f=1000):
    t = np.arange(int(sr * sec)) / sr
    a = (10 ** (db / 20) * np.sin(2 * np.pi * f * t) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.stack([a, a], 1).tobytes())


d = tempfile.mkdtemp(prefix="Громкость ")
loud, quiet, tagged = (os.path.join(d, n) for n in ("громкий.wav", "тихий.wav", "с тегом.wav"))
make_wav(loud, -10)
make_wav(quiet, -25)
make_wav(tagged, -25)
from mutagen.wave import WAVE  # noqa: E402
from mutagen.id3 import TXXX  # noqa: E402
w = WAVE(tagged)
w.add_tags()
w.tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-4.50 dB"]))
w.save()

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402
app = QCoreApplication(sys.argv)
fails = []

v = L.decode_lufs(loud)
print("громкий (ждём −10.0):", v)
if v is None or abs(v + 10.0) > 0.3:
    fails.append("громкий")
v = L.decode_lufs(quiet)
print("тихий (ждём −25.0):", v)
if v is None or abs(v + 25.0) > 0.3:
    fails.append("тихий")
v = L.replaygain_lufs(tagged)
print("ReplayGain −4.5 дБ (ждём −13.5):", v)
if v is None or abs(v + 13.5) > 0.01:
    fails.append("replaygain")

# поток-измеритель целиком
cache = L.LoudnessCache(os.path.join(d, "loudness.json"))
wk = L.make_worker(cache)
got = {}
wk.measured.connect(lambda p, l: got.__setitem__(os.path.basename(p), l))
wk.start()
wk.enqueue([loud, quiet, tagged])
QTimer.singleShot(200, lambda: None)
t = QTimer()
t.timeout.connect(lambda: len(got) >= 3 and app.quit())
t.start(100)
QTimer.singleShot(60000, app.quit)
app.exec()
wk.stop()
wk.wait(5000)
print("поток:", got)
if len(got) < 3:
    fails.append("поток не измерил все файлы")
cache2 = L.LoudnessCache(os.path.join(d, "loudness.json"))
print("из кэша:", cache2.get(loud), cache2.get(tagged))
if cache2.get(loud) is None:
    fails.append("кэш не сохранился")
print("поправки при −14:", L.gain_for(got.get("громкий.wav"), -14), L.gain_for(got.get("тихий.wav"), -14))
if fails:
    print("FAIL:", fails)
    sys.exit(1)
print("OK: громкость меряется")
