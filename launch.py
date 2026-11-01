# -*- coding: utf-8 -*-
"""Быстрый вход. Если плеер уже запущен — сразу отдаём ему файлы через системный
канал (без загрузки Qt) и выходим. Иначе — запускаем сам плеер."""
import os
import sys
import time

APP_NAME = "MP3Player"


def _pipe_name():
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return r"\\.\pipe" + "\\" + APP_NAME + "-" + user      # так его называет QLocalServer


def forward_to_running():
    """True — работающий плеер получил файлы (или просьбу показаться)."""
    if sys.platform != "win32":
        return False
    paths = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("-") and os.path.exists(a)]
    msg = ("open\n" + "\n".join(paths)).encode("utf-8") if paths else b"show"
    deadline = time.monotonic() + 1.5
    while True:
        try:
            with open(_pipe_name(), "wb", buffering=0) as f:
                f.write(msg)
            return True
        except FileNotFoundError:
            return False                    # плеер не запущен
        except OSError:
            if time.monotonic() > deadline:   # канал занят — подождём чуть-чуть
                return False
            time.sleep(0.05)


if __name__ == "__main__":
    if forward_to_running():
        sys.exit(0)
    import mp3player
    sys.exit(mp3player.main())
