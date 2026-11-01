# -*- coding: utf-8 -*-
"""Настоящий спектр для эквалайзера: берём звук из плеера (Qt 6.8+, QAudioBufferOutput)
и раскладываем по 12 полосам. Если не получилось — available=False и reason."""
import array
import ctypes
import math
import time

from PySide6.QtCore import QObject

BANDS = 12
F_LO, F_HI = 50.0, 14000.0
EDGES = [F_LO * (F_HI / F_LO) ** (i / BANDS) for i in range(BANDS + 1)]
CENTERS = [math.sqrt(EDGES[i] * EDGES[i + 1]) for i in range(BANDS)]

try:
    import numpy as np
except Exception:          # без numpy — медленнее, но работает
    np = None


def _fft_mag(x):
    """Модули спектра (половина) — итеративное БПФ, длина — степень двойки."""
    n = len(x)
    a = [complex(v, 0.0) for v in x]
    j = 0
    for i in range(1, n):                     # перестановка по обратным битам
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        half = size // 2
        w_step = complex(math.cos(-2 * math.pi / size), math.sin(-2 * math.pi / size))
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(start, start + half):
                t = w * a[k + half]
                a[k + half] = a[k] - t
                a[k] = a[k] + t
                w *= w_step
        size *= 2
    return [abs(c) for c in a[: n // 2 + 1]]


class Spectrum(QObject):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.available = False
        self.reason = ""
        self.bands = [0.0] * BANDS
        self.t_last = 0.0
        self.count = 0
        self.errors = 0
        self.last_error = ""
        self.fmt_info = ""
        self.method = ""
        self._last_calc = 0.0
        self._outs = []
        try:
            from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
        except Exception:
            self.reason = "нужен PySide6 6.8 или новее (нет QAudioBufferOutput)"
            return
        if not hasattr(QMediaPlayer, "setAudioBufferOutput"):
            self.reason = "нужен PySide6 6.8 или новее"
            return
        try:
            for d in engine.decks:
                if hasattr(d.player, "audio_callback"):        # дека с эквалайзером — звук отдаёт сама
                    d.player.audio_callback = lambda a, sr, d=d: self._on_samples(d, a, sr)
                    continue
                out = QAudioBufferOutput(self)
                d.player.setAudioBufferOutput(out)
                out.audioBufferReceived.connect(lambda buf, d=d: self._on_buffer(d, buf))
                self._outs.append(out)
            self.available = True
        except Exception as e:
            self.reason = f"{type(e).__name__}: {e}"

    def recent(self, max_age=0.35):
        return self.available and time.monotonic() - self.t_last < max_age

    # ------------------------------------------------------------ данные
    def _raw(self, buf):
        n = buf.byteCount()
        d = buf.constData()
        if isinstance(d, (bytes, bytearray)):
            self.method = "bytes"
            return bytes(d[:n])
        try:
            mv = memoryview(d)
            self.method = "memoryview"
            return mv.tobytes()[:n]
        except TypeError:
            pass
        try:
            import shiboken6
            vp = shiboken6.VoidPtr(d, n, False)
            self.method = "VoidPtr"
            return bytes(vp)
        except Exception:
            pass
        self.method = "ctypes"
        return ctypes.string_at(int(d), n)

    def _on_buffer(self, deck, buf):
        if deck is not self.engine.cur:
            return
        now = time.monotonic()
        if now - self._last_calc < (0.03 if np is not None else 0.06):   # ~30 (или 15) раз в секунду
            return
        self._last_calc = now
        try:
            fmt = buf.format()
            sr = fmt.sampleRate() or 44100
            ch = max(1, fmt.channelCount())
            sf = fmt.sampleFormat()
            name = getattr(sf, "name", str(sf))
            raw = self._raw(buf)
            self.fmt_info = f"{name}, {sr} Гц, каналов {ch}, {len(raw)} байт"
            mono = self._mono(raw, name, ch)
            if mono is None or len(mono) < 256:
                return
            self.bands = self._analyse(mono, sr)
            self.t_last = now
            self.count += 1
        except Exception as e:
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    def _on_samples(self, deck, mono, sr):
        if deck is not self.engine.cur:
            return
        now = time.monotonic()
        if now - self._last_calc < 0.03:
            return
        self._last_calc = now
        try:
            m = list(mono[-2048:]) if np is None else mono[-2048:]
            if len(m) >= 256:
                self.bands = self._analyse(m, sr)
                self.t_last = now
                self.count += 1
        except Exception as e:
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    def _mono(self, raw, name, ch):
        kinds = {"Int16": ("h", 32768.0), "Int32": ("i", 2147483648.0),
                 "Float": ("f", 1.0), "UInt8": ("B", 128.0)}
        code, scale = kinds.get(name, ("h", 32768.0))
        if np is not None:
            dt = {"h": np.int16, "i": np.int32, "f": np.float32, "B": np.uint8}[code]
            a = np.frombuffer(raw[: len(raw) - len(raw) % np.dtype(dt).itemsize], dtype=dt).astype(np.float32)
            if code == "B":
                a -= 128.0
            a /= scale
            if ch > 1:
                a = a[: len(a) - len(a) % ch].reshape(-1, ch).mean(axis=1)
            return a[-2048:]
        arr = array.array(code)
        arr.frombytes(raw[: len(raw) - len(raw) % arr.itemsize])
        frames = arr[0::ch][-512:]                  # без numpy — один канал, 512 отсчётов
        off = 128.0 if code == "B" else 0.0
        return [(v - off) / scale for v in frames]

    def _analyse(self, x, sr):
        n = len(x)
        out = []
        if np is not None:
            win = np.hanning(n)
            spec = np.abs(np.fft.rfft(x * win)) / (n / 4)
            freqs = np.fft.rfftfreq(n, 1.0 / sr)
            for i in range(BANDS):
                m = (freqs >= EDGES[i]) & (freqs < EDGES[i + 1])
                v = float(spec[m].max()) if m.any() else 0.0
                out.append(v)
        else:
            # своё БПФ на 512 точек (окно Ханна) — точнее, чем отдельные частоты
            x = list(x[-512:])
            n = len(x)
            if n & (n - 1):
                n = 1 << (n.bit_length() - 1)
                x = x[-n:]
            xw = [v * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i, v in enumerate(x)]
            spec = _fft_mag(xw)
            for i in range(BANDS):
                lo = max(1, int(EDGES[i] * n / sr))
                hi = max(lo + 1, int(EDGES[i + 1] * n / sr) + 1)
                seg = spec[lo:min(hi, len(spec))]
                out.append((max(seg) if seg else 0.0) / (n / 4))
        res = []
        for i, v in enumerate(out):
            db = 20.0 * math.log10(v + 1e-6) + i * 1.2       # верхам чуть помочь — их всегда тише
            res.append(max(0.0, min(1.0, (db + 54.0) / 50.0)))
        return res
