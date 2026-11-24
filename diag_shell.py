# -*- coding: utf-8 -*-
"""«Почему грузится Проводник?» — плеер по очереди выключает свои части и меряет, сколько
процессора в это время ест explorer.exe (как «ЦП» в диспетчере задач).

Этапы по 4 секунды: как есть → без окна плеера (ничего не рисуется) → без медиапанели Windows →
без значка в трее → на паузе. Потом всё возвращается как было, а итог показывается списком."""
import os
import sys
import time

from PySide6.QtCore import QObject, QTimer


# ------------------------------------------------------------------ время процессора explorer.exe
def _explorer_pids():
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    pids = []
    e = PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == "explorer.exe":
            pids.append(e.th32ProcessID)
        ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return pids


def _cpu_seconds(pid):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0.0
    c, x, kt, ut = (wintypes.FILETIME() for _ in range(4))
    try:
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x), ctypes.byref(kt), ctypes.byref(ut)):
            return 0.0
        v = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
        return v(kt) + v(ut)
    finally:
        k.CloseHandle(h)


def explorer_cpu():
    """Суммарное время процессора всех explorer.exe, секунды."""
    return sum(_cpu_seconds(p) for p in _explorer_pids())


def own_cpu():
    return _cpu_seconds(os.getpid()) if sys.platform == "win32" else 0.0


# ------------------------------------------------------------------ проверка по этапам
class ShellLoadCheck(QObject):
    STEP = 4.0

    def __init__(self, player, on_status, on_done):
        super().__init__(player)
        self.p = player
        self.on_status, self.on_done = on_status, on_done
        self.ncpu = os.cpu_count() or 1
        self.results = []
        self._undo = []
        self.steps = [
            ("как есть", lambda: None),
            ("окно плеера спрятано (ничего не рисуется)", self._hide_windows),
            ("медиапанель Windows выключена", self._smtc_off),
            ("значок в трее убран", self._tray_off),
            ("музыка на паузе", self._pause),
        ]
        self.i = -1
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._finish_step)

    # каждый этап добавляет к уже сделанному ещё одно выключение и запоминает, как его вернуть
    def _hide_windows(self):
        for w in (self.p, getattr(self.p, "mini", None), self.p.__dict__.get("library_win")):
            if w is not None and w.isVisible():
                w.hide()
                self._undo.append(w.show)

    def _smtc_off(self):
        sm = self.p.sysmedia
        if getattr(sm, "ok", False):
            try:
                sm.smtc.is_enabled = False
                self._undo.append(lambda: setattr(sm.smtc, "is_enabled", True))
            except Exception:
                pass

    def _tray_off(self):
        t = getattr(self.p, "tray", None)
        if t is not None and t.isVisible():
            t.hide()
            self._undo.append(t.show)

    def _pause(self):
        if self.p.is_playing():
            self.p.toggle_play()
            self._undo.append(lambda: (not self.p.is_playing()) and self.p.toggle_play())

    def start(self):
        if sys.platform != "win32" or not _explorer_pids():
            self.on_done("Проверка работает только в Windows с запущенным Проводником.", [])
            return
        self._next()

    def _next(self):
        self.i += 1
        if self.i >= len(self.steps):
            self._restore()
            self.on_done(self.report(), self.results)
            return
        name, act = self.steps[self.i]
        try:
            act()
        except Exception as e:
            print("diag step:", e)
        self.on_status(f"Проверка {self.i + 1}/{len(self.steps)}: {name}…")
        # секунду даём устояться, потом меряем
        QTimer.singleShot(1000, self._begin_measure)

    def _begin_measure(self):
        self.t0, self.e0, self.o0 = time.monotonic(), explorer_cpu(), own_cpu()
        self.timer.start(int(self.STEP * 1000))

    def _finish_step(self):
        dt = max(0.1, time.monotonic() - self.t0)
        exp = (explorer_cpu() - self.e0) / dt / self.ncpu * 100
        own = (own_cpu() - self.o0) / dt / self.ncpu * 100
        self.results.append((self.steps[self.i][0], exp, own))
        self._next()

    def _restore(self):
        for f in reversed(self._undo):
            try:
                f()
            except Exception as e:
                print("diag restore:", e)
        self._undo.clear()

    def report(self):
        lines = ["Нагрузка на процессор (как в диспетчере задач):", ""]
        for name, exp, own in self.results:
            lines.append(f"• {name}: Проводник {exp:.1f} %, плеер {own:.1f} %")
        if len(self.results) >= 2:
            base = self.results[0][1]
            drops = [(self.results[k - 1][1] - self.results[k][1], self.results[k][0]) for k in range(1, len(self.results))]
            drop, who = max(drops)
            lines.append("")
            if base < 3:
                lines.append("Проводник сейчас почти не нагружен — проверь, когда нагрузка снова появится.")
            elif drop > max(2.0, base * 0.3):
                lines.append(f"Сильнее всего нагрузка упала на шаге «{who}» (−{drop:.1f} %).")
            else:
                lines.append("Ни один шаг заметно не снизил нагрузку — похоже, Проводник грузит не плеер.")
        lines.append("")
        lines.append("Всё уже вернулось как было. Пришли этот текст разработчику.")
        return "\n".join(lines)
