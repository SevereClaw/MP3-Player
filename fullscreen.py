# -*- coding: utf-8 -*-
"""Идёт ли сейчас игра или другое приложение на весь экран.

Нужно, чтобы не мешать играм: всплывающее окно поверх игры заставляет Windows заново собирать
картинку экрана (игра теряет «прямой» вывод), и в ней падает FPS; фоновый разбор музыки
отнимает процессор. Пока на экране игра — окна не показываем и тяжёлую работу откладываем."""
import os
import sys
import time

_cache = (0.0, None)


def _query():
    """None — игры нет; "exclusive" — игра в «настоящем» полноэкранном режиме Direct3D (поверх неё окно
    не видно, а показ может свернуть игру); "window" — на весь экран, но окном (без рамки) / другое приложение."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    # 1) сама Windows знает: 2 — приложение на весь экран, 3 — игра в полноэкранном Direct3D, 4 — презентация
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value in (2, 3, 4):
            return "exclusive" if state.value == 3 else "window"
    except Exception:
        pass
    # 2) игра «в окне без рамки»: активное чужое окно закрывает монитор целиком
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return None                                     # наше собственное окно — не игра
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return None                                     # рабочий стол и панель задач

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        mon = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(wintypes.HANDLE(mon), ctypes.byref(mi)):
            return None
        m = mi.rcMonitor
        full = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
        return "window" if full else None
    except Exception:
        return None


def game_running(max_age=1.0):
    """True, если на экране игра / полноэкранное приложение. Ответ запоминается на секунду."""
    return bool(game_mode(max_age))


def game_mode(max_age=1.0):
    """None / "exclusive" / "window" (см. _query). Ответ запоминается на секунду."""
    global _cache
    now = time.monotonic()
    if now - _cache[0] > max_age:
        _cache = (now, _query())
    return _cache[1]


def background_thread():
    """Вызвать внутри фонового потока: Windows даст ему процессор и диск в последнюю очередь."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = ctypes.c_void_p
        if not k.SetThreadPriority(ctypes.c_void_p(k.GetCurrentThread()), 0x00010000):     # BACKGROUND_BEGIN
            k.SetThreadPriority(ctypes.c_void_p(k.GetCurrentThread()), -2)                  # LOWEST
    except Exception:
        pass
