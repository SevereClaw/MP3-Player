# -*- coding: utf-8 -*-
"""Свой путь звука — для эквалайзера.

  Renderer  — PyAV (FFmpeg) декодирует файл → 48 кГц стерео float → эквалайзер.
  DspPlayer — снаружи как QMediaPlayer (те же методы и сигналы, что использует
              AudioEngine), внутри: поток-декодер + буфер + QAudioSink.
  DspOutput — как QAudioOutput: setVolume.
Встроенный QMediaPlayer нужен только для тегов и обложки (звук он не играет)."""
import threading
import time

try:
    import numpy as np
except Exception:
    np = None
try:
    import av
except Exception as _e:      # без PyAV эквалайзер недоступен
    av = None
    AV_ERROR = f"{type(_e).__name__}: {_e}"
else:
    AV_ERROR = ""

from PySide6.QtCore import QObject, Signal, QTimer, QIODevice, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioFormat, QAudioSink, QMediaDevices, QAudio

import eq as eqmod

SR = eqmod.SR
MS = QMediaPlayer.MediaStatus
PS = QMediaPlayer.PlaybackState


def available():
    return av is not None and np is not None


# ===================================================================== декодер
class Renderer:
    """Итератор обработанного звука из файла: (n, 2) float32, 48 кГц."""
    def __init__(self, path, equalizer, start_ms=0):
        self.c = av.open(path)
        self.s = self.c.streams.audio[0]
        try:
            self.s.thread_type = "AUTO"
        except Exception:
            pass
        self.proc = eqmod.EqProcessor(equalizer)
        self.duration_ms = int((self.c.duration or 0) / 1000) or \
            int(float((self.s.duration or 0) * (self.s.time_base or 0)) * 1000)
        self._seek(start_ms)

    def _seek(self, ms):
        self.res = av.AudioResampler(format="flt", layout="stereo", rate=SR)
        self.skip_until = max(0, ms) / 1000.0
        self.proc.reset()
        if ms > 0:
            try:
                self.c.seek(int(ms * 1000), any_frame=False, backward=True)   # AV_TIME_BASE — мкс
            except Exception:
                self.c.seek(0)
        self.it = self.c.decode(self.s)
        self.done = False

    def seek(self, ms):
        self._seek(ms)

    def _frames(self, frame):
        for f in self.res.resample(frame):
            a = f.to_ndarray()
            yield a.reshape(-1, 2).astype(np.float32, copy=False)

    def read(self):
        """Следующий кусок или None в конце."""
        if self.done:
            return None
        while True:
            try:
                frame = next(self.it)
            except StopIteration:
                tail = [x for x in self._frames(None)]
                out = self.proc.process(np.concatenate(tail) if tail else np.zeros((0, 2), np.float32),
                                        final=True)
                self.done = True
                return out if len(out) else None
            except av.error.InvalidDataError:
                continue                                   # битый кадр — пропустить
            if self.skip_until > 0:                        # после перемотки — до нужного места
                t = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
                if t + frame.samples / float(frame.sample_rate or SR) <= self.skip_until:
                    continue
            parts = list(self._frames(frame))
            if not parts:
                continue
            x = np.concatenate(parts) if len(parts) > 1 else parts[0]
            if self.skip_until > 0 and frame.pts is not None:
                t = float(frame.pts * frame.time_base)
                cut = int(max(0.0, self.skip_until - t) * SR)
                x = x[cut:]
                self.skip_until = 0
            out = self.proc.process(x)
            if len(out):
                return out

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass


# ===================================================================== вывод
class _Pull(QIODevice):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def readData(self, maxlen):
        return self.owner._pull(maxlen)

    def writeData(self, data):
        return -1

    def bytesAvailable(self):
        return self.owner._avail() + super().bytesAvailable()

    def isSequential(self):
        return True


class DspOutput(QObject):
    """Как QAudioOutput: громкость деки."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None
        self._v = 1.0

    def setVolume(self, v):
        self._v = max(0.0, min(1.0, float(v)))
        if self.player is not None:
            self.player._set_volume(self._v)

    def volume(self):
        return self._v


class DspPlayer(QObject):
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    metaDataChanged = Signal()
    playbackStateChanged = Signal(object)
    positionChanged = Signal(object)
    durationChanged = Signal(object)

    AHEAD_S = 0.6            # сколько звука держать готовым

    def __init__(self, equalizer, parent=None):
        super().__init__(parent)
        self.eq = equalizer
        self.audio_callback = None          # спектр: f(моно float32, частота)
        self._meta = QMediaPlayer(self)     # только теги/обложка/длительность
        self._meta.metaDataChanged.connect(self.metaDataChanged.emit)
        self._state = PS.StoppedState
        self._status = MS.NoMedia
        self._dur = 0
        self._source = QUrl()
        self._vol = 1.0
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._gen = 0
        self._thread = None
        self._seek_req = None
        self._ended = False
        self._base_ms = 0
        self._proc_at_base = 0
        self._spec_t = 0.0
        self.sink = None
        self.dev = _Pull(self)
        self.dev.open(QIODevice.OpenModeFlag.ReadOnly)
        self.fmt = QAudioFormat()
        self.fmt.setSampleRate(SR)
        self.fmt.setChannelCount(2)
        self.fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        self.pos_timer = QTimer(self, interval=100)
        self.pos_timer.timeout.connect(self._tick)

    # ---------------------------------------------------- как у QMediaPlayer
    def setAudioOutput(self, out):
        out.player = self
        self._set_volume(out.volume())

    def setSource(self, url):
        self._stop_thread()
        self._close_sink()
        self._source = QUrl(url)
        self._meta.setSource(QUrl(url))
        self._buf = bytearray()
        self._base_ms, self._proc_at_base = 0, 0
        self._ended = False
        if url.isEmpty():
            self._set_status(MS.NoMedia)
            self._set_state(PS.StoppedState)
            return
        self._set_status(MS.LoadingMedia)
        self._start_thread(0)

    def source(self):
        return self._source

    def play(self):
        if self._source.isEmpty():
            return
        if self._ended:                              # после конца — сначала
            self.setPosition(0)
        if self.sink is None:
            self._open_sink()
        if self.sink is None:
            return
        st = self.sink.state()
        if st == QAudio.State.SuspendedState:
            self.sink.resume()
        elif st == QAudio.State.StoppedState:
            self._proc_at_base = 0
            self.sink.start(self.dev)
        self._set_state(PS.PlayingState)
        self.pos_timer.start()

    def pause(self):
        if self.sink is not None and self._state == PS.PlayingState:
            self.sink.suspend()
        if self._state != PS.StoppedState:
            self._set_state(PS.PausedState)
        self.pos_timer.stop()
        self.positionChanged.emit(self.position())

    def stop(self):
        self._close_sink()
        if not self._source.isEmpty():
            self._restart_at(0)
        self._set_state(PS.StoppedState)
        self.pos_timer.stop()

    def position(self):
        if self.sink is None:
            return self._base_ms
        played = max(0, self.sink.processedUSecs() // 1000 - self._proc_at_base)
        return min(self._dur or 10 ** 12, self._base_ms + played)

    def setPosition(self, ms):
        ms = max(0, int(ms))
        self._ended = False
        self._restart_at(ms)
        self.positionChanged.emit(ms)
        if self._status == MS.EndOfMedia:
            self._set_status(MS.BufferedMedia)

    def duration(self):
        return self._dur

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def metaData(self):
        return self._meta.metaData()

    def setAudioBufferOutput(self, _out):          # спектр у этой деки — через audio_callback
        pass

    # ---------------------------------------------------- внутреннее
    def _set_volume(self, v):
        self._vol = v
        if self.sink is not None:
            self.sink.setVolume(v)

    def _set_state(self, st):
        if st != self._state:
            self._state = st
            self.playbackStateChanged.emit(st)

    def _set_status(self, st):
        if st != self._status:
            self._status = st
            self.mediaStatusChanged.emit(st)

    def _open_sink(self):
        dev = QMediaDevices.defaultAudioOutput()
        if not dev.isFormatSupported(self.fmt):
            self.errorOccurred.emit(QMediaPlayer.Error.FormatError,
                                    "звуковая карта не принимает 48 кГц float")
            return
        self.sink = QAudioSink(dev, self.fmt, self)
        self.sink.setBufferSize(int(SR * 2 * 4 * 0.2))          # 200 мс
        self.sink.setVolume(self._vol)
        self.sink.stateChanged.connect(self._on_sink_state)

    def _close_sink(self):
        if self.sink is not None:
            try:
                self.sink.stateChanged.disconnect(self._on_sink_state)
            except Exception:
                pass
            self.sink.stop()
            self.sink.deleteLater()
            self.sink = None

    def _restart_at(self, ms):
        """Перемотка: новый поток декодера с нужного места, буфер с нуля."""
        playing = self._state == PS.PlayingState
        self._stop_thread()
        with self._lock:
            self._buf = bytearray()
        self._base_ms = ms
        if self.sink is not None:
            self._proc_at_base = self.sink.processedUSecs() // 1000
        self._start_thread(ms)
        if playing and self.sink is not None and self.sink.state() == QAudio.State.IdleState:
            self.sink.resume()

    def _start_thread(self, ms):
        self._gen += 1
        self._thread = threading.Thread(target=self._decode, args=(self._gen, self._source.toLocalFile(), ms),
                                        daemon=True)
        self._thread.start()

    def _stop_thread(self):
        self._gen += 1
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=1.5)

    def _decode(self, gen, path, ms):
        try:
            r = Renderer(path, self.eq, ms)
        except Exception as e:
            if gen == self._gen:
                QTimer.singleShot(0, self, lambda e=e: self._fail(str(e)))
            return
        if gen == self._gen and r.duration_ms != self._dur:
            self._dur = r.duration_ms
            QTimer.singleShot(0, self, lambda: (self.durationChanged.emit(self._dur),
                                                self._set_status(MS.BufferedMedia)
                                                if self._status == MS.LoadingMedia else None))
        limit = int(SR * 8 * self.AHEAD_S)
        try:
            while gen == self._gen:
                with self._lock:
                    full = len(self._buf) >= limit
                if full:
                    time.sleep(0.02)
                    continue
                x = r.read()
                if x is None:
                    with self._lock:
                        if gen == self._gen:
                            self._ended = True
                    return
                b = x.tobytes()
                with self._lock:
                    if gen != self._gen:
                        return
                    self._buf += b
        except Exception as e:
            if gen == self._gen:
                QTimer.singleShot(0, self, lambda e=e: self._fail(str(e)))
        finally:
            r.close()

    def _fail(self, msg):
        self._set_status(MS.InvalidMedia)
        self.errorOccurred.emit(QMediaPlayer.Error.ResourceError, msg)

    def _avail(self):
        return len(self._buf)

    def _pull(self, maxlen):
        with self._lock:
            n = min(maxlen, len(self._buf))
            n -= n % 8
            data = bytes(self._buf[:n])
            del self._buf[:n]
            ended = self._ended and not self._buf
        if not data and not ended and maxlen >= 8:
            return bytes(min(maxlen, 4096) // 8 * 8)          # ещё декодируется — тишина
        cb = self.audio_callback
        if cb and data and time.monotonic() - self._spec_t > 0.03:
            self._spec_t = time.monotonic()
            try:
                a = np.frombuffer(data[-2048 * 8:], dtype=np.float32).reshape(-1, 2).mean(axis=1)
                cb(a, SR)
            except Exception:
                pass
        return data

    def _on_sink_state(self, st):
        if st == QAudio.State.IdleState and self._ended and not self._buf:
            self.pos_timer.stop()
            self._set_state(PS.StoppedState)
            self._set_status(MS.EndOfMedia)
            self._close_sink()
            self._base_ms = self._dur

    def _tick(self):
        if self._state == PS.PlayingState:
            self.positionChanged.emit(self.position())
