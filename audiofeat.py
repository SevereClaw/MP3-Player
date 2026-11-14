# -*- coding: utf-8 -*-
"""«Характер звука» трека — чтобы «Моя волна» шла плавно: после тяжёлого рока не спокойное пианино.

Один раз в фоне слушаем кусок каждого файла (45 секунд из середины, моно 11 кГц) и запоминаем
четыре числа:
  e — энергия 0..1 (насколько громко и плотно),
  p — ударность 0..1 (как часто и резко бьёт ритм),
  b — яркость 0..1 (сколько верхов: тарелки, перегруз, вокал против глухого баса и пианино),
  t — темп, ударов в минуту (0 — ритм не слышен).
Хранится в audiofeat.json по пути, размеру и времени файла — второй раз тот же файл не слушаем.
"""
import json
import math
import os
import threading
import time

try:
    import numpy as np
except Exception:
    np = None

SR = 11025
HOP = 256
WIN = 1024
TAKE_S = 45.0


# ------------------------------------------------------------------ числа из звука
def features_from_pcm(x, sr=SR):
    """x — моно float32. Возвращает {"e","p","b","t"} или None, если звука мало / тишина."""
    if np is None or x is None or len(x) < sr * 4:
        return None
    x = np.asarray(x, dtype=np.float32)
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 1e-4:
        return None
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(WIN)[None, :], axis=1)).astype(np.float32)
    freqs = np.fft.rfftfreq(WIN, 1.0 / sr)
    power = spec.sum(axis=1) + 1e-9
    # яркость: «центр тяжести» спектра (в октавах от 200 Гц до 3.2 кГц)
    cent = float(np.median((spec * freqs[None, :]).sum(axis=1) / power))
    bright = min(1.0, max(0.0, math.log2(max(cent, 200.0) / 200.0) / 4.0))
    # ударность: насколько резко и часто спектр «вспыхивает» (поток спектра)
    logs = np.log1p(spec * 50.0)
    flux = np.maximum(0.0, logs[1:] - logs[:-1]).sum(axis=1)
    flux_n = flux / (np.median(logs.sum(axis=1)) + 1e-6)
    perc = min(1.0, max(0.0, (float(np.mean(flux_n)) - 0.02) / 0.10))
    # энергия: громкость (в дБ) + плотность (мало тихих мест) + ударность + яркость
    db = 20.0 * math.log10(rms)
    loud = min(1.0, max(0.0, (db + 32.0) / 24.0))                # −32 дБ → 0, −8 дБ → 1
    fr = np.sqrt(np.mean(x[: n * HOP].reshape(n, HOP) ** 2, axis=1)) if n * HOP <= len(x) else np.array([rms])
    dense = float(np.mean(fr > rms * 0.5))                       # доля «нетихих» кусков
    energy = min(1.0, max(0.0, 0.40 * loud + 0.15 * dense + 0.30 * perc + 0.15 * bright))
    tempo = _tempo(flux, sr) if perc >= 0.12 else 0.0             # ничего не бьёт — темпа нет
    return {"e": round(energy, 3), "p": round(perc, 3), "b": round(bright, 3), "t": round(tempo, 1)}


def _tempo(flux, sr):
    """Темп по автокорреляции «вспышек» (60–190 уд/мин, с уклоном к 120). 0 — ритма не слышно."""
    f = flux - flux.mean()
    if len(f) < 200 or float(np.abs(f).max()) <= 0:
        return 0.0
    fps = sr / HOP
    size = 1 << int(math.ceil(math.log2(len(f) * 2)))
    ac = np.fft.irfft(np.abs(np.fft.rfft(f, size)) ** 2)[: len(f)]
    if ac[0] <= 0:
        return 0.0
    ac = ac / ac[0]
    best, best_s = 0.0, 0.0
    for lag in range(int(fps * 60 / 190), int(fps * 60 / 60) + 1):
        if lag + 1 >= len(ac):
            break
        bpm = 60.0 * fps / lag
        s = float(ac[lag]) * math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        if s > best_s:
            best, best_s = bpm, s
    return best if best_s > 0.04 else 0.0


def distance(a, b):
    """Насколько по-разному звучат два трека: 0 — одинаково, ~0.25 — соседи, от 0.6 — «рок и пианино»."""
    if not a or not b:
        return None
    d2 = (a["e"] - b["e"]) ** 2 + (0.7 * (a["p"] - b["p"])) ** 2 + (0.5 * (a["b"] - b["b"])) ** 2
    ta, tb = a.get("t") or 0, b.get("t") or 0
    if ta > 0 and tb > 0:
        o = abs(math.log2(ta / tb))
        o = min(o, abs(o - 1.0))                                 # вдвое быстрее/медленнее — тот же ритм
        d2 += (0.35 * o) ** 2
    return math.sqrt(d2)


def flow_factor(a, b, width=0.3):
    """Множитель для веса следующего трека: похожий на текущий — 1, непохожий — до 0.04."""
    d = distance(a, b)
    if d is None:
        return 0.45                                              # ещё не проанализирован — «средний»
    return max(0.04, math.exp(-(d / width) ** 2))


# ------------------------------------------------------------------ звук из файла
def decode_excerpt(path, take_s=TAKE_S):
    """Кусок из середины файла, моно float32 11 кГц. Нужен PyAV (он уже есть в плеере)."""
    import av
    c = av.open(path)
    try:
        st = next((s for s in c.streams if s.type == "audio"), None)
        if st is None:
            return None
        dur = float(c.duration or 0) / 1e6
        start = max(0.0, min(dur * 0.35, dur - take_s - 5.0)) if dur > take_s + 10 else 0.0
        if start > 1.0:
            try:
                c.seek(int(start * 1e6), any_frame=False, backward=True)
            except Exception:
                pass
        res = av.AudioResampler(format="flt", layout="mono", rate=SR)
        out, got, need = [], 0, int(take_s * SR)
        for packet in c.demux(st):
            try:
                frames = packet.decode()
            except Exception:
                continue
            for fr in frames:
                for r in res.resample(fr):
                    a = r.to_ndarray().reshape(-1)
                    out.append(a)
                    got += len(a)
            if got >= need:
                break
        if not out:
            return None
        return np.concatenate(out)[:need].astype(np.float32)
    finally:
        c.close()


def analyze(path):
    try:
        return features_from_pcm(decode_excerpt(path))
    except Exception:
        return None


# ------------------------------------------------------------------ память и фоновый разбор
class FeatureStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.dirty = False
        self.busy = False
        self.done = 0
        self._stop = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.db = json.load(f)
        except Exception:
            self.db = {}

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.normpath(path))

    def get(self, path):
        e = self.db.get(self._key(path))
        return e.get("f") if e else None

    def known(self, path, size, mtime):
        e = self.db.get(self._key(path))
        return bool(e) and e.get("s") == [int(size), int(mtime)]

    def put(self, path, size, mtime, feat):
        with self.lock:
            self.db[self._key(path)] = {"s": [int(size), int(mtime)], **({"f": feat} if feat else {})}
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        with self.lock:
            data, self.dirty = dict(self.db), False
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            print("audiofeat:", e)

    def stop(self):
        self._stop = True

    def analyze_all(self, items, enabled=lambda: True, first=(), analyzer=None, rest=2.0):
        """В фоне разобрать всё, что ещё не разобрано. items — {путь: {"s": размер, "m": время}}.
        first — пути, которые нужны раньше других (очередь волны). rest — во сколько раз дольше
        отдыхать, чем работать: разбор не должен греть процессор."""
        if self.busy or np is None:
            return False
        if analyzer is None:
            try:
                import av  # noqa: F401
            except Exception:
                return False                                     # нечем декодировать — попробуем в другой раз
        todo = [p for p in list(first) + list(items) if p in items
                and not self.known(p, items[p].get("s", 0), items[p].get("m", 0))]
        todo = list(dict.fromkeys(todo))
        if not todo:
            return False
        self.busy, self._stop = True, False
        fn = analyzer or analyze

        def work():
            last_save = time.time()
            try:
                for p in todo:
                    if self._stop or not enabled():
                        break
                    it = items.get(p) or {}
                    if not os.path.isfile(p):
                        continue
                    t0 = time.time()
                    feat = fn(p)
                    self.put(p, it.get("s", 0), it.get("m", 0), feat)
                    self.done += 1
                    if time.time() - last_save > 20:
                        last_save = time.time()
                        self.save()
                    if rest:
                        time.sleep(min(2.0, max(0.02, (time.time() - t0) * rest)))
            finally:
                self.save()
                self.busy = False

        threading.Thread(target=work, daemon=True, name="audiofeat").start()
        return True
