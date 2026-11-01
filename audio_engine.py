# -*- coding: utf-8 -*-
"""
Звуковой движок с двумя «деками»: плавная пауза, плавный переход между треками
(кроссфейд). Снаружи выглядит почти как QMediaPlayer: те же сигналы и методы,
сигналы идут только от активной деки.
"""
import math
import time

from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

PS = QMediaPlayer.PlaybackState


class _Deck:
    def __init__(self, parent):
        self.audio = QAudioOutput(parent)
        self.player = QMediaPlayer(parent)
        self.player.setAudioOutput(self.audio)
        self.x = 1.0            # уровень 0..1 (до кривой)
        self.target = 1.0
        self.rate = 0.0         # единиц в секунду
        self.on_done = None
        self.gain = 1.0         # выравнивание громкости этого трека (множитель)
        self.gain_target = 1.0


class AudioEngine(QObject):
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    metaDataChanged = Signal()
    playbackStateChanged = Signal(object)
    positionChanged = Signal(object)
    durationChanged = Signal(object)
    nearEnd = Signal()          # до конца трека осталось «время перехода»

    def __init__(self, parent=None, volume=0.6):
        super().__init__(parent)
        self.user_vol = float(volume)
        self.decks = [_Deck(self), _Deck(self)]
        self.active = 0
        self.pausing = False
        self.near_ms = 0
        self._near_fired = False
        self._last_tick = time.monotonic()
        self.timer = QTimer(self, interval=20)
        self.timer.timeout.connect(self._tick)
        for d in self.decks:
            self._wire(d)
            self._apply(d)

    # ------------------------------------------------------------ сигналы
    def _wire(self, d):
        p = d.player
        p.mediaStatusChanged.connect(lambda st, d=d: self._is(d) and self.mediaStatusChanged.emit(st))
        p.errorOccurred.connect(lambda e, s="", d=d: self._is(d) and self.errorOccurred.emit(e, s))
        p.metaDataChanged.connect(lambda d=d: self._is(d) and self.metaDataChanged.emit())
        p.playbackStateChanged.connect(lambda st, d=d: self._is(d) and self.playbackStateChanged.emit(self.playbackState()))
        p.durationChanged.connect(lambda v, d=d: self._is(d) and self.durationChanged.emit(v))
        p.positionChanged.connect(lambda v, d=d: self._on_pos(d, v))

    def _is(self, d):
        return d is self.cur

    def _on_pos(self, d, pos):
        if not self._is(d):
            return
        self.positionChanged.emit(pos)
        if self.near_ms > 0 and not self._near_fired and not self.pausing \
                and d.player.playbackState() == PS.PlayingState:
            dur = d.player.duration()
            if dur > self.near_ms + 5000 and pos >= dur - self.near_ms:
                self._near_fired = True
                self.nearEnd.emit()

    @property
    def cur(self):
        return self.decks[self.active]

    # ------------------------------------------------------------ как у QMediaPlayer
    def position(self):
        return self.cur.player.position()

    def duration(self):
        return self.cur.player.duration()

    def metaData(self):
        return self.cur.player.metaData()

    def mediaStatus(self):
        return self.cur.player.mediaStatus()

    def playbackState(self):
        if self.pausing:
            return PS.PausedState
        return self.cur.player.playbackState()

    def setPosition(self, ms):
        self.cur.player.setPosition(int(ms))
        dur = self.cur.player.duration()
        if dur <= 0 or ms < dur - self.near_ms - 500:
            self._near_fired = False

    def volume(self):
        return self.user_vol

    def setVolume(self, v):
        self.user_vol = max(0.0, min(1.0, float(v)))
        for d in self.decks:
            self._apply(d)

    def set_crossfade(self, ms):
        self.near_ms = max(0, int(ms))

    # ------------------------------------------------------------ управление
    def set_gain(self, g, glide_ms=1500):
        """Поправка громкости текущего трека; меняется плавно, без скачка."""
        d = self.cur
        d.gain_target = max(0.05, min(1.0, float(g)))
        if glide_ms <= 0:
            d.gain = d.gain_target
            self._apply(d)
            return
        d.gain_rate = abs(d.gain_target - d.gain) / (glide_ms / 1000.0)
        if not self.timer.isActive():
            self._last_tick = time.monotonic()
            self.timer.start()

    def load(self, url: QUrl, autoplay=True, fade_ms=0, gain=1.0):
        old = self.cur
        was_playing = (old.player.playbackState() == PS.PlayingState) and not self.pausing
        self.pausing = False
        self._near_fired = False
        if autoplay and was_playing and fade_ms > 0:
            # кроссфейд: новая дека нарастает, старая затихает
            self.active = 1 - self.active
            new = self.cur
            new.on_done = None
            new.player.stop()
            new.gain = new.gain_target = max(0.05, min(1.0, float(gain)))
            new.x = 0.0
            self._apply(new)
            new.player.setSource(url)
            new.player.play()
            self._ramp(new, 1.0, fade_ms)
            self._ramp(old, 0.0, fade_ms, on_done=lambda o=old: self._release(o))
            self.playbackStateChanged.emit(self.playbackState())
        else:
            d = self.cur
            d.on_done = None
            d.gain = d.gain_target = max(0.05, min(1.0, float(gain)))
            d.player.setSource(url)
            if autoplay:
                d.x = 0.0
                self._apply(d)
                d.player.play()
                self._ramp(d, 1.0, 120 if fade_ms > 0 else 0)   # без щелчка
            else:
                self._ramp(d, 1.0, 0)

    def _release(self, d):
        if d is not self.cur:
            d.player.stop()
            d.player.setSource(QUrl())

    def play(self, fade_ms=0):
        d = self.cur
        d.on_done = None
        resumed_from_pausing = self.pausing
        self.pausing = False
        if d.player.playbackState() != PS.PlayingState:
            if fade_ms > 0:
                d.x = 0.0
                self._apply(d)
            d.player.play()
        self._ramp(d, 1.0, fade_ms)
        if resumed_from_pausing:
            self.playbackStateChanged.emit(self.playbackState())

    def pause(self, fade_ms=0):
        d = self.cur
        if d.player.playbackState() != PS.PlayingState or self.pausing:
            return
        other = self.decks[1 - self.active]        # хвост прошлого трека при кроссфейде
        if other.player.playbackState() == PS.PlayingState:
            self._ramp(other, 0.0, max(1, fade_ms), on_done=lambda o=other: self._release(o))
        if fade_ms <= 0:
            d.player.pause()
            return
        self.pausing = True
        self.playbackStateChanged.emit(PS.PausedState)
        self._ramp(d, 0.0, fade_ms, on_done=lambda d=d: self._finish_pause(d))

    def _finish_pause(self, d):
        if self.pausing and d is self.cur:
            self.pausing = False
            d.player.pause()

    def stop(self):
        self.pausing = False
        d = self.cur
        d.on_done = None
        d.player.stop()
        self._ramp(d, 1.0, 0)

    # ------------------------------------------------------------ плавность
    def _apply(self, d):
        # равномощная кривая: при кроссфейде общая громкость не проседает
        d.audio.setVolume(self.user_vol * d.gain * math.sin(max(0.0, min(1.0, d.x)) * math.pi / 2))

    def _ramp(self, d, target, ms, on_done=None):
        d.target = target
        d.on_done = on_done
        if ms <= 0 or abs(d.x - target) < 1e-4:
            d.x = target
            self._apply(d)
            self._finish(d)
            return
        d.rate = abs(target - d.x) / (ms / 1000.0)
        if not self.timer.isActive():
            self._last_tick = time.monotonic()
            self.timer.start()

    def _finish(self, d):
        cb, d.on_done = d.on_done, None
        if cb:
            cb()

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        moving = False
        for d in self.decks:
            if d.gain != d.gain_target:                     # плавная поправка громкости
                step = getattr(d, "gain_rate", 1.0) * dt
                if abs(d.gain_target - d.gain) <= step:
                    d.gain = d.gain_target
                else:
                    d.gain += step if d.gain_target > d.gain else -step
                    moving = True
                if d.x == d.target:
                    self._apply(d)
            if d.x == d.target:
                continue
            step = d.rate * dt
            if abs(d.target - d.x) <= step:
                d.x = d.target
                self._apply(d)
                self._finish(d)
            else:
                d.x += step if d.target > d.x else -step
                self._apply(d)
                moving = True
        if not moving and all(d.x == d.target and d.gain == d.gain_target for d in self.decks):
            self.timer.stop()
