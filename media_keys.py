# -*- coding: utf-8 -*-
"""
Связь плеера с Windows:
  * SystemMedia   — системные медиа-сессии (SMTC): медиа-клавиши, панель громкости
                    Windows, обложка; другие программы (например Music → Discord)
                    видят, что играет.
  * GlobalHotkeys — глобальные сочетания Ctrl+Alt+… (работают из любого окна).
Все команды приходят сигналом command(str): play_pause / play / pause / next /
prev / stop / vol_up / vol_down / shuffle / toggle_window / seek:<ms>.
"""
import sys
import threading
import asyncio
from datetime import timedelta

from PySide6.QtCore import QObject, Signal, QAbstractNativeEventFilter, QCoreApplication

IS_WIN = sys.platform == "win32"


# ======================================================================= SMTC
class SystemMedia(QObject):
    command = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ok = False
        self.error = ""
        self._cover_seq = 0
        if not IS_WIN:
            self.error = "не Windows"
            return
        try:
            from winrt.windows.media.playback import MediaPlayer
            from winrt.windows.media import (MediaPlaybackType, MediaPlaybackStatus,
                                             SystemMediaTransportControlsButton)
            self._Type = MediaPlaybackType
            self._Status = MediaPlaybackStatus
            self._Btn = SystemMediaTransportControlsButton

            # «пустой» MediaPlayer нужен только как владелец системной сессии
            self._mp = MediaPlayer()
            self._mp.command_manager.is_enabled = False
            s = self._mp.system_media_transport_controls
            s.is_enabled = True
            s.is_play_enabled = True
            s.is_pause_enabled = True
            s.is_next_enabled = True
            s.is_previous_enabled = True
            s.is_stop_enabled = True
            s.playback_status = MediaPlaybackStatus.CLOSED
            self._tok = s.add_button_pressed(self._on_button)
            try:
                self._tok2 = s.add_playback_position_change_requested(self._on_seek)
            except Exception:
                pass
            self.smtc = s
            self.ok = True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            print("SMTC недоступен:", self.error)

    # --- события из Windows (приходят в другом потоке → сигнал Qt)
    def _on_button(self, _sender, args):
        B = self._Btn
        m = {B.PLAY: "play", B.PAUSE: "pause", B.NEXT: "next",
             B.PREVIOUS: "prev", B.STOP: "stop"}
        cmd = m.get(args.button)
        if cmd:
            self.command.emit(cmd)

    def _on_seek(self, _sender, args):
        try:
            ms = int(args.requested_playback_position.total_seconds() * 1000)
            self.command.emit(f"seek:{ms}")
        except Exception:
            pass

    # --- обновление из плеера
    def set_status(self, state):
        """state: 'playing' / 'paused' / 'stopped' / 'closed'"""
        if not self.ok:
            return
        S = self._Status
        try:
            self.smtc.playback_status = {"playing": S.PLAYING, "paused": S.PAUSED,
                                         "stopped": S.STOPPED, "closed": S.CLOSED}[state]
        except Exception as e:
            print("SMTC status:", e)

    def set_track(self, title, artist, album="", cover_path=None):
        if not self.ok:
            return
        try:
            u = self.smtc.display_updater
            u.type = self._Type.MUSIC
            mp = u.music_properties
            mp.title = title or ""
            mp.artist = artist or ""
            try:
                mp.album_title = album or ""
            except Exception:
                pass
            u.update()
        except Exception as e:
            print("SMTC track:", e)
            return
        if cover_path:
            self._cover_seq += 1
            seq = self._cover_seq
            threading.Thread(target=self._set_cover, args=(cover_path, seq), daemon=True).start()

    def _set_cover(self, path, seq):
        async def run():
            from winrt.windows.storage import StorageFile
            from winrt.windows.storage.streams import RandomAccessStreamReference
            f = await StorageFile.get_file_from_path_async(path)
            if seq != self._cover_seq:          # уже сменился трек
                return
            u = self.smtc.display_updater
            u.thumbnail = RandomAccessStreamReference.create_from_file(f)
            u.update()
        try:
            asyncio.run(run())
        except Exception as e:
            print("SMTC cover:", e)

    def set_timeline(self, pos_ms, dur_ms):
        if not self.ok or dur_ms <= 0:
            return
        try:
            from winrt.windows.media import SystemMediaTransportControlsTimelineProperties as TL
            t = TL()
            t.start_time = timedelta(0)
            t.min_seek_time = timedelta(0)
            t.end_time = timedelta(milliseconds=dur_ms)
            t.max_seek_time = timedelta(milliseconds=dur_ms)
            t.position = timedelta(milliseconds=max(0, min(pos_ms, dur_ms)))
            self.smtc.update_timeline_properties(t)
        except Exception as e:
            print("SMTC timeline:", e)
            self.set_timeline = lambda *a: None   # больше не пытаться

    def close(self):
        if self.ok:
            try:
                self.set_status("closed")
                self.smtc.is_enabled = False
            except Exception:
                pass


# =============================================================== Горячие клавиши
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
VK_SPACE, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x20, 0x25, 0x26, 0x27, 0x28
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_STOP, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB2, 0xB3
WM_HOTKEY = 0x0312

HOTKEYS = [  # (id, модификаторы, клавиша, команда, подпись)
    (1, MOD_CONTROL | MOD_ALT, VK_SPACE, "play_pause", "Ctrl+Alt+Пробел — играть / пауза"),
    (2, MOD_CONTROL | MOD_ALT, VK_RIGHT, "next", "Ctrl+Alt+→ — следующий"),
    (3, MOD_CONTROL | MOD_ALT, VK_LEFT, "prev", "Ctrl+Alt+← — предыдущий"),
    (4, MOD_CONTROL | MOD_ALT, VK_UP, "vol_up", "Ctrl+Alt+↑ — громче"),
    (5, MOD_CONTROL | MOD_ALT, VK_DOWN, "vol_down", "Ctrl+Alt+↓ — тише"),
    (6, MOD_CONTROL | MOD_ALT, ord("S"), "shuffle", "Ctrl+Alt+S — перемешивание"),
    (7, MOD_CONTROL | MOD_ALT, ord("P"), "toggle_window", "Ctrl+Alt+P — показать / спрятать"),
    (8, MOD_CONTROL | MOD_ALT, ord("L"), "playlist", "Ctrl+Alt+L — плейлист"),
    (9, MOD_CONTROL | MOD_ALT, ord("F"), "next_folder", "Ctrl+Alt+F — следующая папка"),
    (10, MOD_CONTROL | MOD_ALT, ord("A"), "ab", "Ctrl+Alt+A — повтор A–B (A, B, сброс)"),
    (11, MOD_CONTROL | MOD_ALT, ord("K"), "fav", "Ctrl+Alt+K — ♥ в избранное / убрать"),
    (12, MOD_CONTROL | MOD_ALT, ord("I"), "stats", "Ctrl+Alt+I — итоги на экране"),
    (13, MOD_CONTROL | MOD_ALT, ord("M"), "mini", "Ctrl+Alt+M — мини-режим / большой плеер"),
    (14, MOD_CONTROL | MOD_ALT, ord("R"), "radio", "Ctrl+Alt+R — радио / обратно к музыке"),
    (15, MOD_CONTROL | MOD_ALT, ord("D"), "download", "Ctrl+Alt+D — скачать в плеер (Doulander)"),
    (16, MOD_CONTROL | MOD_ALT, ord("E"), "eq", "Ctrl+Alt+E — эквалайзер звука"),
    (17, MOD_CONTROL | MOD_ALT, ord("B"), "search", "Ctrl+Alt+B — поиск по всей музыке"),
    (18, MOD_CONTROL | MOD_ALT, ord("T"), "tags", "Ctrl+Alt+T — теги текущего трека"),
]
# Запасной вариант: если системные медиа-сессии недоступны, ловим медиа-клавиши сами
MEDIA_FALLBACK = [
    (21, 0, VK_MEDIA_PLAY_PAUSE, "play_pause", ""),
    (22, 0, VK_MEDIA_NEXT, "next", ""),
    (23, 0, VK_MEDIA_PREV, "prev", ""),
    (24, 0, VK_MEDIA_STOP, "stop", ""),
]


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, eventType, message):
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = self.owner._MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam in self.owner.registered:
                    self.owner.command.emit(self.owner.registered[msg.wParam])
                    return True, 0
        except Exception:
            pass
        return False, 0


class GlobalHotkeys(QObject):
    command = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registered = {}      # id -> команда
        self.failed = []          # подписи сочетаний, занятых другими программами
        self._filter = None
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            self._u = ctypes.windll.user32
            self._u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self._u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._MSG = wintypes.MSG

    def _install(self):
        if self._filter is None:
            self._filter = _HotkeyFilter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)

    def register(self, table):
        if not IS_WIN:
            return
        self._install()
        for hid, mod, vk, cmd, label in table:
            if hid in self.registered:
                continue
            if self._u.RegisterHotKey(None, hid, mod | MOD_NOREPEAT, vk):
                self.registered[hid] = cmd
            elif label:
                self.failed.append(label)

    def unregister(self, table=None):
        if not IS_WIN:
            return
        ids = [t[0] for t in table] if table else list(self.registered)
        for hid in ids:
            if hid in self.registered:
                self._u.UnregisterHotKey(None, hid)
                del self.registered[hid]
        labels = {t[4] for t in table} if table else {t[4] for t in HOTKEYS}
        self.failed = [f for f in self.failed if f not in labels]
