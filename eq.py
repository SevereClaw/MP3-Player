# -*- coding: utf-8 -*-
"""10-полосный эквалайзер: полосы, пресеты, расчёт фильтра и обработка звука.

Кривая (дБ по полосам) → линейно-фазовый КИХ-фильтр (4097 отсчётов) → свёртка
блоками через БПФ (overlap-save). Всё на numpy, работает в потоке декодера.
Чтобы подъём полос не давал перегруза, громкость заранее снижается на пик кривой,
а редкие выбросы мягко срезаются."""
import math
import threading

try:
    import numpy as np
except Exception:
    np = None

BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
LABELS = ["31", "62", "125", "250", "500", "1к", "2к", "4к", "8к", "16к"]
MAX_DB = 12.0

PRESETS = {
    "Плоский":            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Больше баса":        [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Бас и верха":        [5, 4, 2, 0, -1, -1, 0, 2, 4, 5],
    "Рок":                [4, 3, 2, 0, -1, -1, 1, 3, 4, 4],
    "Поп":                [-1, 1, 3, 4, 3, 1, 0, -1, -1, -1],
    "Электроника":        [5, 4, 1, 0, -2, 1, 0, 2, 4, 5],
    "Хип-хоп":            [5, 5, 3, 1, -1, -1, 1, 0, 2, 3],
    "Вокал":              [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
    "Классика":           [3, 2, 1, 0, 0, 0, -1, -1, 1, 2],
    "Наушники-вкладыши":  [4, 3, 1, 0, 0, 0, 1, 2, 3, 2],
    "Мягче верха":        [0, 0, 0, 0, 0, 0, -1, -3, -5, -7],
    "Ночью (меньше баса)": [-6, -4, -2, 0, 0, 0, 0, 0, 0, 0],
}

SR = 48000
TAPS = 4097                 # длина фильтра (нечётная — линейная фаза)
BLOCK = 4096                # новых отсчётов на блок
NFFT = 8192                 # BLOCK + TAPS − 1


def is_flat(gains):
    return all(abs(float(g)) < 0.05 for g in gains)


def curve_db(gains, freqs):
    """Значение кривой (дБ) на частотах freqs: линейно по логарифму частоты."""
    lf = np.log10(np.clip(freqs, 1.0, None))
    return np.interp(lf, np.log10(BANDS), np.asarray(gains, dtype=float))


def preamp_db(gains):
    f = np.geomspace(20, 20000, 400)
    return -max(0.0, float(curve_db(gains, f).max()))


def design_fir(gains, sr=SR, taps=TAPS):
    """Линейно-фазовый КИХ по кривой (уже с предусилением)."""
    n = 16384
    f = np.fft.rfftfreq(n, 1.0 / sr)
    db = curve_db(gains, f) + preamp_db(gains)
    mag = 10 ** (db / 20.0)
    k = np.fft.irfft(mag, n)                       # нуль-фазовое ядро (центр в 0)
    k = np.roll(k, taps // 2)[:taps]
    k *= np.hanning(taps)
    return k.astype(np.float64)


class Equalizer:
    """Общий для всех дек: кривая меняется из интерфейса, деки подхватывают на лету."""
    def __init__(self, gains=None, enabled=True):
        self.lock = threading.Lock()
        self.version = 0
        self.gains = list(gains or PRESETS["Плоский"])
        self.enabled = enabled
        self._H = None

    def active(self):
        return self.enabled and not is_flat(self.gains) and np is not None

    def set(self, gains=None, enabled=None):
        with self.lock:
            if gains is not None:
                self.gains = [max(-MAX_DB, min(MAX_DB, float(g))) for g in gains]
            if enabled is not None:
                self.enabled = bool(enabled)
            self._H = None
            self.version += 1

    def spectrum(self):
        """Частотная характеристика для БПФ длины NFFT (или None — без обработки)."""
        with self.lock:
            if not self.active():
                return None
            if self._H is None:
                self._H = np.fft.rfft(design_fir(self.gains), NFFT)
            return self._H


class EqProcessor:
    """Обработка потока одной деки (стерео float32, 48 кГц)."""
    def __init__(self, eq):
        self.eq = eq
        self.version = -1
        self.H = None
        self.hist = np.zeros((TAPS - 1, 2), dtype=np.float64)
        self.pending = np.zeros((0, 2), dtype=np.float32)

    def reset(self):
        self.hist[:] = 0
        self.pending = np.zeros((0, 2), dtype=np.float32)

    def process(self, x, final=False):
        """x — (n, 2) float32. Возвращает обработанное (может быть короче — копится блок)."""
        if self.version != self.eq.version:
            self.version = self.eq.version
            self.H = self.eq.spectrum()
        if self.H is None:                         # плоско — без задержки и копий
            out = np.concatenate([self.pending, x]) if len(self.pending) else x
            self.pending = np.zeros((0, 2), dtype=np.float32)
            return out
        buf = np.concatenate([self.pending, x]) if len(self.pending) else x
        if final and len(buf) % BLOCK:
            buf = np.concatenate([buf, np.zeros((BLOCK - len(buf) % BLOCK, 2), np.float32)])
        n = (len(buf) // BLOCK) * BLOCK
        self.pending = buf[n:]
        if not n:
            return np.zeros((0, 2), dtype=np.float32)
        outs = []
        for i in range(0, n, BLOCK):
            blk = buf[i:i + BLOCK].astype(np.float64)
            seg = np.concatenate([self.hist, blk])            # TAPS−1 + BLOCK = NFFT
            y = np.fft.irfft(np.fft.rfft(seg, NFFT, axis=0) * self.H[:, None], NFFT, axis=0)
            outs.append(y[TAPS - 1:TAPS - 1 + BLOCK])
            self.hist = seg[-(TAPS - 1):]
        out = np.concatenate(outs)
        return soft_clip(out).astype(np.float32)


def soft_clip(x, t=0.9):
    """Выше порога t — плавное скругление к 1.0, ниже — без изменений."""
    a = np.abs(x)
    if not len(a) or a.max() <= t:
        return x
    over = a > t
    y = x.copy()
    y[over] = np.sign(x[over]) * (t + (1 - t) * np.tanh((a[over] - t) / (1 - t)))
    return y
