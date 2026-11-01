# -*- coding: utf-8 -*-
"""Плеер «живёт на рабочем столе» и не пропадает по Win+D.

Как это работает: следим, какое окно стало активным (SetWinEventHook).
  * активен рабочий стол (после Win+D или клика по обоям) — плеер встаёт поверх;
  * активировали программу — плеер уходит сразу под её окно;
  * если Windows всё-таки свернула/спрятала плеер — разворачиваем обратно.
"""
import sys

from PySide6.QtCore import QObject, QTimer

IS_WIN = sys.platform == "win32"
DESKTOP = {"WorkerW", "Progman"}
# панель задач, трей, «Пуск», всплывашки — на них не реагируем
SHELL = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
         "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow",
         "Windows.UI.Core.CoreWindow", "TaskListThumbnailWnd", "MultitaskingViewFrame",
         "ForegroundStaging", "TaskSwitcherWnd", "#32768"}


class DesktopKeeper(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.w = widget
        self.ok = False
        self.reason = ""
        self.enabled = True
        self.on_top = False
        self.events = 0
        self.restores = 0
        self.last_fg = ""
        self._hook = None
        self._proc = None
        if not IS_WIN:
            self.reason = "не Windows"
            return
        try:
            import ctypes
            from ctypes import wintypes
            self.ct, self.wt = ctypes, wintypes
            u = ctypes.windll.user32
            self.u = u
            WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                              wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
            u.SetWinEventHook.restype = wintypes.HANDLE
            u.SetWinEventHook.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HMODULE, WINEVENTPROC,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.UINT]
            u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            u.GetForegroundWindow.restype = wintypes.HWND
            u.IsIconic.argtypes = [wintypes.HWND]
            u.IsWindowVisible.argtypes = [wintypes.HWND]
            u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            bits = 64 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
            self.TOPMOST = (1 << bits) - 1
            self.NOTOPMOST = (1 << bits) - 2
            self._proc = WINEVENTPROC(self._on_event)       # держим ссылку, иначе сборщик мусора
            EVENT_SYSTEM_FOREGROUND = 0x0003
            WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002
            self._hook = u.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
                                           self._proc, 0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            if not self._hook:
                self.reason = "SetWinEventHook не сработал"
                return
            self.timer = QTimer(self, interval=800)
            self.timer.timeout.connect(self._watch)
            self.timer.start()
            self.ok = True
        except Exception as e:
            self.reason = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------
    def _active(self):
        return self.ok and self.enabled and getattr(self.w, "on_desktop", False) and self.w.isVisible()

    def _cls(self, hwnd):
        buf = self.ct.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _on_event(self, _hook, _event, hwnd, _obj, _child, _thread, _time):
        try:
            if not hwnd:
                return
            cls = self._cls(hwnd)
            self.events += 1
            self.last_fg = cls
            if not self._active() or cls in SHELL:
                return
            if cls in DESKTOP:
                self._ensure_shown()
                self._set_top(True)
            else:
                self._set_top(False, below=hwnd)
        except Exception as e:
            self.reason = f"событие: {e}"

    def _set_top(self, on, below=None):
        hwnd = int(self.w.winId())
        flags = 0x0001 | 0x0002 | 0x0010            # NOSIZE | NOMOVE | NOACTIVATE
        if on:
            if not self.on_top:
                self.u.SetWindowPos(hwnd, self.TOPMOST, 0, 0, 0, 0, flags)
                self.on_top = True
        else:
            if self.on_top:
                self.u.SetWindowPos(hwnd, self.NOTOPMOST, 0, 0, 0, 0, flags)
                self.on_top = False
                if below:                            # сразу под окно, которое активировали
                    self.u.SetWindowPos(hwnd, below, 0, 0, 0, 0, flags)

    def _ensure_shown(self):
        hwnd = int(self.w.winId())
        if self.u.IsIconic(hwnd) or not self.u.IsWindowVisible(hwnd):
            self.u.ShowWindow(hwnd, 4)               # SW_SHOWNOACTIVATE
            self.restores += 1

    def _watch(self):
        """Подстраховка: Win+D мог свернуть плеер, а событие не прийти."""
        if not self._active():
            if self.on_top and not getattr(self.w, "on_desktop", False):
                self.on_top = False
            return
        try:
            self._ensure_shown()
            fg = self.u.GetForegroundWindow()
            if fg:
                cls = self._cls(fg)
                if cls in DESKTOP:
                    self._set_top(True)
        except Exception:
            pass

    def release(self):
        """Снять «поверх» (например, при выключении функции)."""
        if self.ok and self.on_top:
            self._set_top(False)

    def close(self):
        if self._hook:
            try:
                self.u.UnhookWinEvent(self._hook)
            except Exception:
                pass
            self._hook = None
