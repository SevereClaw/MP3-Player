# -*- coding: utf-8 -*-
"""Выравнивание громкости: громкость каждого трека в LUFS (EBU R128 / ITU-R BS.1770).

  * Если в файле уже есть ReplayGain (теги REPLAYGAIN_TRACK_GAIN / R128_TRACK_GAIN) —
    берём его, ничего не декодируя.
  * Иначе один раз декодируем трек в фоне (QAudioDecoder) и меряем сами:
    K-взвешивание, блоки 400 мс с шагом 100 мс, абсолютный (−70) и относительный
    (−10 LU) пороги — как в стандарте.
  * Результат запоминаем в loudness.json (по пути, размеру и времени изменения файла).
"""
import json
import math
import os
import threading

try:
    import numpy as np
except Exception:          # без numpy выравнивание просто не работает
    np = None

RG_REF = -18.0             # ReplayGain 2.0: 0 дБ поправки = −18 LUFS


# ===================================================================== измерение
def _biquad_resp(b, a, w):
    z = np.exp(-1j * w)
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def k_weight_power(sr, n):
    """|H(f)|² K-фильтра (полка + срез НЧ) для бинов rfft длины n."""
    w = 2 * np.pi * np.fft.rfftfreq(n, 1.0 / sr) / sr
    # формулы libebur128 (эталон BS.1770) — для любой частоты дискретизации
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196   # полка +4 дБ
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773                        # срез НЧ
    K = math.tan(math.pi * f0 / sr)
    d = 1.0 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2.0 * (K * K - 1.0) / d, (1.0 - K / Q + K * K) / d]
    h = _biquad_resp(b1, a1, w) * _biquad_resp(b2, a2, w)
    return np.abs(h) ** 2


class LoudnessMeter:
    """Потоковый измеритель: feed(отсчёты float, shape (n, каналы)), потом lufs()."""
    def __init__(self):
        self.sr = None
        self.ch = None
        self.hop = None
        self.weights = None
        self.rest = None
        self.chunks = []          # средний квадрат (сумма по каналам) каждого 100-мс куска

    def feed(self, x, sr):
        if x.ndim == 1:
            x = x[:, None]
        if self.sr != sr or self.ch != x.shape[1]:
            if self.sr is not None and self.chunks:      # формат сменился посреди файла — редкость
                self.rest = None
            self.sr, self.ch = sr, x.shape[1]
            self.hop = int(round(sr * 0.1))
            self.weights = k_weight_power(sr, self.hop)
            self.rest = None
        ch = min(self.ch, 5)
        x = x[:, :ch].astype(np.float64, copy=False)
        if self.rest is not None and len(self.rest):
            x = np.concatenate([self.rest, x])
        n = (len(x) // self.hop) * self.hop
        self.rest = x[n:]
        if not n:
            return
        blocks = x[:n].reshape(-1, self.hop, ch)                    # (куски, отсчёты, каналы)
        spec = np.abs(np.fft.rfft(blocks, axis=1)) ** 2               # Парсеваль: средний квадрат
        spec[:, 1:-1 if self.hop % 2 == 0 else None, :] *= 2
        ms = (spec * self.weights[None, :, None]).sum(axis=1) / (self.hop * self.hop)
        gains = np.array([1.0, 1.0, 1.0, 1.41, 1.41][:ch])          # объёмные каналы громче
        self.chunks.extend((ms * gains[None, :]).sum(axis=1).tolist())

    def lufs(self):
        if len(self.chunks) < 4:
            return None
        z = np.array(self.chunks)
        z = np.convolve(z, np.ones(4) / 4.0, mode="valid")           # блоки 400 мс с шагом 100 мс
        with np.errstate(divide="ignore"):
            lk = -0.691 + 10 * np.log10(z)
        z = z[lk > -70.0]                                            # абсолютный порог
        if not len(z):
            return None
        rel = -0.691 + 10 * math.log10(z.mean()) - 10.0               # относительный порог
        with np.errstate(divide="ignore"):
            z2 = z[(-0.691 + 10 * np.log10(z)) > rel]
        if not len(z2):
            return None
        return round(-0.691 + 10 * math.log10(z2.mean()), 2)


# ===================================================================== ReplayGain из тегов
def _db(s):
    try:
        return float(str(s).lower().replace("db", "").strip().replace(",", "."))
    except Exception:
        return None


def replaygain_lufs(path):
    """Громкость по готовым тегам (LUFS) или None. Нужен mutagen; нет его — None."""
    try:
        import mutagen
    except Exception:
        return None
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None or not getattr(f, "tags", None):
        return None
    tags = f.tags
    # Opus: R128_TRACK_GAIN — целое в 1/256 дБ относительно −23 LUFS
    for k in ("R128_TRACK_GAIN", "r128_track_gain"):
        try:
            v = tags.get(k)
            if v:
                return round(-23.0 - int(v[0]) / 256.0, 2)
        except Exception:
            pass
    cands = []
    try:
        for k in tags.keys():
            kl = str(k).lower()
            if "replaygain_track_gain" in kl:                        # FLAC/OGG, TXXX:…, ----:com.apple…
                v = tags[k]
                v = getattr(v, "text", v)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "ignore")
                cands.append(v)
    except Exception:
        pass
    for v in cands:
        g = _db(v)
        if g is not None and -30 < g < 30:
            return round(RG_REF - g, 2)
    return None


# ===================================================================== кэш
class LoudnessCache:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.db = json.load(f)
        except Exception:
            self.db = {}

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.normpath(path))

    @staticmethod
    def _stamp(path):
        try:
            st = os.stat(path)
            return [int(st.st_size), int(st.st_mtime)]
        except OSError:
            return None

    def get(self, path):
        """LUFS, None (ещё не мерили), или 'fail' (файл не получилось измерить)."""
        e = self.db.get(self._key(path))
        if not e or e.get("s") != self._stamp(path):
            return None
        return e.get("l", "fail")

    def put(self, path, lufs, src="m"):
        with self.lock:
            self.db[self._key(path)] = {"s": self._stamp(path), "src": src,
                                        **({"l": lufs} if lufs is not None else {})}
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        with self.lock:
            data, self.dirty = dict(self.db), False
        try:
            if len(data) > 20000:                                    # не разрастаться бесконечно
                data = dict(list(data.items())[-15000:])
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            print("loudness cache:", e)


def gain_for(lufs, target):
    """Множитель громкости. Только убавляем (Qt не даёт громкость больше 100 %),
    а тихие треки остаются как есть."""
    if lufs is None or lufs == "fail":
        return 1.0
    g = 10 ** ((target - float(lufs)) / 20.0)
    return max(0.1, min(1.0, g))


# ===================================================================== фоновое измерение (Qt)
def _raw_bytes(buf):
    n = buf.byteCount()
    d = buf.constData()
    if isinstance(d, (bytes, bytearray)):
        return bytes(d[:n])
    try:
        return memoryview(d).tobytes()[:n]
    except TypeError:
        pass
    try:
        import shiboken6
        return bytes(shiboken6.VoidPtr(d, n, False))
    except Exception:
        import ctypes
        return ctypes.string_at(int(d), n)


_KINDS = {"Int16": ("<i2", 32768.0), "Int32": ("<i4", 2147483648.0),
          "Float": ("<f4", 1.0), "UInt8": ("u1", 128.0)}


def buffer_to_array(buf):
    """QAudioBuffer → (float32 (кадры, каналы), частота)."""
    fmt = buf.format()
    sr = fmt.sampleRate() or 44100
    ch = max(1, fmt.channelCount())
    sf = fmt.sampleFormat()
    dt, scale = _KINDS.get(getattr(sf, "name", str(sf)).split(".")[-1], ("<i2", 32768.0))
    raw = _raw_bytes(buf)
    item = np.dtype(dt).itemsize
    a = np.frombuffer(raw[: len(raw) - len(raw) % (item * ch)], dtype=dt).astype(np.float32)
    if dt == "u1":
        a -= 128.0
    a /= scale
    return a.reshape(-1, ch), sr


def decode_lufs(path, timeout_s=180):
    """Декодировать файл (QAudioDecoder) и измерить громкость. Вызывать в потоке с Qt."""
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder
    dec = QAudioDecoder()
    meter = LoudnessMeter()
    loop = QEventLoop()
    state = {"err": None, "bufs": 0}

    def pull():
        while dec.bufferAvailable():
            buf = dec.read()
            if not buf.isValid():
                break
            x, sr = buffer_to_array(buf)
            if len(x):
                meter.feed(x, sr)
                state["bufs"] += 1

    def check():
        e = dec.error()
        if e != QAudioDecoder.Error.NoError:
            state["err"] = dec.errorString() or str(e)
            loop.quit()

    dec.bufferReady.connect(pull)
    dec.finished.connect(loop.quit)
    guard = QTimer(interval=250)
    guard.timeout.connect(check)
    stop = QTimer(singleShot=True, interval=int(timeout_s * 1000))
    stop.timeout.connect(loop.quit)
    dec.setSource(QUrl.fromLocalFile(path))
    dec.start()
    guard.start()
    stop.start()
    loop.exec()
    guard.stop()
    stop.stop()
    pull()
    dec.stop()
    if state["err"] and not state["bufs"]:
        raise IOError(state["err"])
    return meter.lufs()


def make_worker(cache):
    """Поток-измеритель. Сигнал measured(путь, LUFS или None)."""
    from collections import deque
    from PySide6.QtCore import QThread, Signal

    class LoudnessWorker(QThread):
        measured = Signal(str, object)

        def __init__(self):
            super().__init__()
            self.cache = cache
            self.queue = deque()
            self.queued = set()
            self.lock = threading.Lock()
            self.wake = threading.Event()
            self.alive = True
            self.done = 0
            self.last_error = ""

        def enqueue(self, paths, front=False):
            with self.lock:
                items = [p for p in paths if p and self.cache.get(p) is None]
                if front:
                    for p in reversed(items):
                        k = os.path.normcase(p)
                        if k in self.queued:
                            try:
                                self.queue.remove(p)
                            except ValueError:
                                pass
                        self.queue.appendleft(p)
                        self.queued.add(k)
                else:
                    for p in items:
                        k = os.path.normcase(p)
                        if k not in self.queued:
                            self.queue.append(p)
                            self.queued.add(k)
            self.wake.set()

        def pending(self):
            return len(self.queue)

        def stop(self):
            self.alive = False
            self.wake.set()

        def run(self):
            since_save = 0
            while self.alive:
                with self.lock:
                    path = self.queue.popleft() if self.queue else None
                    if path:
                        self.queued.discard(os.path.normcase(path))
                if not path:
                    self.cache.save()
                    self.wake.wait(5.0)
                    self.wake.clear()
                    continue
                if self.cache.get(path) is not None or not os.path.isfile(path):
                    continue
                lufs, src = replaygain_lufs(path), "rg"
                if lufs is None:
                    src = "m"
                    try:
                        lufs = decode_lufs(path)
                    except Exception as e:
                        self.last_error = f"{os.path.basename(path)}: {e}"[:160]
                        lufs = None
                self.cache.put(path, lufs, src)
                self.done += 1
                self.measured.emit(path, lufs)
                since_save += 1
                if since_save >= 20:
                    self.cache.save()
                    since_save = 0
            self.cache.save()

    return LoudnessWorker()
