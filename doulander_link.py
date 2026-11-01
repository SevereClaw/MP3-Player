# -*- coding: utf-8 -*-
"""«Скачать в плеер» через Doulander (Downloader).

Плеер не тащит свой yt-dlp: берёт всё из папки Doulander — yt-dlp, ffmpeg, deno —
и его настройки (формат музыки, качество, квадратная обложка, тексты .lrc, cookies,
прокси для YouTube). Скачивание идёт в фоне, по одной ссылке за раз."""
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse

from PySide6.QtCore import QObject, Signal

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ANSI = re.compile(r"\x1b\[[0-9;]*m")
URL_RE = re.compile(r"^https?://\S+$", re.I)
YOUTUBE_HOST = re.compile(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$", re.I)

AUDIO_FORMATS = {"MP3": "mp3", "M4A": "m4a", "OPUS": "opus", "FLAC": "flac", "WAV": "wav"}
LOSSLESS = {"FLAC", "WAV"}
AUDIO_QUALITIES = {"Лучшее": "0", "320 kbps": "320K", "192 kbps": "192K", "128 kbps": "128K"}
COOKIE_SOURCES = {"Firefox": "firefox", "Chrome": "chrome", "Edge": "edge", "Opera": "opera",
                  "Brave": "brave"}


def default_dirs():
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", "")
    home = os.path.expanduser("~")
    return [os.path.join(la, "Programs", "Downloader"), os.path.join(la, "Programs", "Doulander"),
            os.path.join(pf, "Downloader"), os.path.join(home, "Desktop", "Downloader"),
            os.path.join(home, "Downloads", "Downloader", "Downloader")]


def ytdlp_in(folder):
    if not folder:
        return None
    for p in (os.path.join(folder, "yt-dlp", "yt-dlp.exe"), os.path.join(folder, "yt-dlp.exe")):
        if os.path.isfile(p):
            return p
    return None


def find_doulander(saved=None):
    """Папка Doulander (там, где лежит его yt-dlp) или None."""
    for d in ([saved] if saved else []) + default_dirs():
        if d and (os.path.isfile(d) or d.lower().endswith(".exe")):   # указали сам Downloader.exe
            d = os.path.dirname(d)
        if ytdlp_in(d):
            return os.path.normpath(d)
    return None


def _tool(folder, name):
    for d in (folder, os.path.join(folder, "tools"), os.path.join(folder, "bin")):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def read_settings(folder):
    try:
        with open(os.path.join(folder, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_url(s):
    return bool(URL_RE.match((s or "").strip()))


def is_youtube(url):
    if not is_url(url):
        return True                            # поиск по названию идёт на YouTube
    try:
        return bool(YOUTUBE_HOST.search(urllib.parse.urlsplit(url).hostname or ""))
    except ValueError:
        return False


def cookie_args(s):
    name = s.get("cookies") or "Нет"
    if name in COOKIE_SOURCES:
        return ["--cookies-from-browser", COOKIE_SOURCES[name]]
    if name.startswith("Яндекс"):
        la = os.environ.get("LOCALAPPDATA", "")
        return ["--cookies-from-browser",
                "chromium:" + os.path.join(la, "Yandex", "YandexBrowser", "User Data", "Default")]
    if name.startswith("Файл"):
        p = s.get("cookies_file")
        if p and os.path.isfile(p):
            return ["--cookies", p]
    return []


def proxy_for(s, url):
    mode, proxy = s.get("proxy_mode") or "Нет", (s.get("proxy") or "").strip()
    if not proxy or mode == "Нет":
        return None
    return proxy if mode == "Все сайты" or is_youtube(url) else None


def build_command(folder, s, url, out_dir, cookies=None):
    ytdlp = ytdlp_in(folder)
    fmt = (s.get("audio_format") or "MP3").upper()
    if fmt not in AUDIO_FORMATS:
        fmt = "MP3"
    target = url if is_url(url) else "ytsearch1:" + url
    cmd = [ytdlp, target, "-P", out_dir, "--no-playlist",
           "-o", "%(title)s.%(ext)s", "--windows-filenames",
           "--newline", "--progress", "--no-simulate",
           "--progress-template", "download:[P]%(progress._percent_str)s",
           "--print", "before_dl:[T]%(title)s",
           "--print", "after_move:[F]%(filepath)s",
           "-f", "ba/b", "-x", "--audio-format", AUDIO_FORMATS[fmt],
           "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "5",
           "--file-access-retries", "10", "--socket-timeout", "15"]
    if fmt not in LOSSLESS:
        cmd += ["--audio-quality", AUDIO_QUALITIES.get(s.get("audio_quality"), "0")]
    if s.get("embed", True) and fmt != "WAV":
        cmd += ["--embed-thumbnail"]
        if s.get("square_cover", True):       # обложка 16:9 → квадрат, как у альбома
            cmd += ["--convert-thumbnails", "webp>jpg/png>jpg/jpg>png",
                    "--ppa", "ThumbnailsConvertor:-qmin 1 -q:v 1 -vf "
                             "crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\""]
        cmd += ["--embed-metadata"]
    if s.get("lyrics"):                         # текст песни из субтитров клипа → .lrc
        cmd += ["--write-subs", "--sub-langs", "ru.*,en.*", "--convert-subs", "lrc"]
    if s.get("sponsorblock"):
        cmd += ["--sponsorblock-remove", "sponsor,selfpromo,interaction,music_offtopic"]
    cmd += cookies if cookies is not None else cookie_args(s)
    proxy = proxy_for(s, url)
    if proxy:
        cmd += ["--proxy", proxy]
    ff = _tool(folder, "ffmpeg.exe")
    if ff:
        cmd += ["--ffmpeg-location", os.path.dirname(ff)]
    deno = _tool(folder, "deno.exe") or next(
        (p for p in [os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe")]
         if os.path.isfile(p)), None)
    if deno:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    return cmd, proxy


def tidy_lyrics(path):
    """«Трек.ru.lrc» → «Трек.lrc» (так его сразу находит плеер)."""
    stem = os.path.splitext(path)[0]
    folder, base = os.path.split(stem)
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return
    cands = [n for n in names if n.startswith(base + ".") and n.lower().endswith(".lrc")
             and n != base + ".lrc"]
    if not cands:
        return
    cands.sort(key=lambda n: (0 if ".ru" in n.lower() else 1 if ".en" in n.lower() else 2, n))
    try:
        if not os.path.exists(stem + ".lrc"):          # свой .lrc уже есть — его не трогаем
            os.replace(os.path.join(folder, cands.pop(0)), stem + ".lrc")
        for n in cands:
            os.remove(os.path.join(folder, n))
    except OSError:
        pass


def explain(err, url, proxy, cookies):
    low = (err or "").lower()
    if "not a bot" in low or ("confirm you" in low and "bot" in low):
        return ("YouTube просит вход («вы не бот?»). В Doulander: ⚙ → Cookies — браузер, "
                "где вы вошли в YouTube, или смените сервер VPN.")
    if any(k in low for k in ("timed out", "timeout", "connection", "ssl", "unreachable",
                              "getaddrinfo", "reset by peer", "unable to download webpage",
                              "unable to download api page", "proxy")):
        if proxy:
            return "Прокси не отвечает — проверьте, что VPN-программа запущена (Doulander: ⚙ → Сеть)."
        if is_youtube(url):
            return "YouTube не открывается напрямую — включите прокси в Doulander: ⚙ Настройки → Сеть."
        return "Нет связи с сайтом — проверьте интернет."
    if "unsupported url" in low:
        return "Эту ссылку скачать нельзя."
    if "video unavailable" in low or "private video" in low:
        return "Видео недоступно (удалено или закрыто)."
    if "cookie" in low and cookies:
        return "Не удалось прочитать cookies браузера — закройте браузер или выберите другой в Doulander."
    lines = [ln for ln in (err or "").splitlines() if "ERROR" in ln] or (err or "").splitlines()
    return (lines[-1] if lines else "неизвестная ошибка")[:200]


class DoulanderJobs(QObject):
    """Очередь скачиваний. Сигналы приходят из фонового потока (Qt доставит их в окно)."""
    started = Signal(str)                 # ссылка
    titled = Signal(str)                  # название ролика
    progress = Signal(float)              # проценты 0..100
    finished = Signal(str, str, str)      # файл (или ""), папка, ошибка (или "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue = []
        self.busy = False
        self.lock = threading.Lock()
        self.proc = None
        self._cancel = False

    def add(self, folder, url, out_dir):
        with self.lock:
            self.queue.append((folder, url.strip(), out_dir))
            if self.busy:
                return len(self.queue)
            self.busy = True
        threading.Thread(target=self._run_all, daemon=True).start()
        return 0

    def pending(self):
        return len(self.queue) + (1 if self.busy else 0)

    def cancel(self):
        with self.lock:
            self.queue.clear()
        self._cancel = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    def _run_all(self):
        while True:
            with self.lock:
                if not self.queue:
                    self.busy = False
                    return
                folder, url, out_dir = self.queue.pop(0)
            try:
                self._run_one(folder, url, out_dir)
            except Exception as e:
                self.finished.emit("", out_dir, f"{type(e).__name__}: {e}")

    def _run_one(self, folder, url, out_dir):
        self._cancel = False
        self.started.emit(url)
        s = read_settings(folder)
        os.makedirs(out_dir, exist_ok=True)
        cookies = cookie_args(s)
        for attempt in range(2):
            cmd, proxy = build_command(folder, s, url, out_dir, cookies)
            code, out, files = self._exec(cmd)
            if code == 0 and files:
                path = files[-1]
                if s.get("lyrics"):
                    tidy_lyrics(path)
                self.finished.emit(os.path.normpath(path), out_dir, "")
                return
            low = out.lower()
            bot = "not a bot" in low or ("confirm you" in low and "bot" in low)
            if attempt == 0 and bot and not cookies:     # как Doulander: пробуем cookies Firefox
                cookies = ["--cookies-from-browser", "firefox"]
                continue
            if self._cancel:
                self.finished.emit("", out_dir, "отменено")
                return
            self.finished.emit("", out_dir, explain(out, url, proxy, cookies)
                               if code else "файл не появился")
            return

    def _exec(self, cmd):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self.proc = p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, env=env,
                                             creationflags=NO_WINDOW)
        except OSError as e:
            return 1, f"не запускается yt-dlp из Doulander: {e}", []
        tail, files, last = [], [], -10.0
        for raw in p.stdout:
            line = ANSI.sub("", raw.decode("utf-8", "replace")).strip()
            if line.startswith("[P]"):
                try:
                    pct = float(line[3:].strip().rstrip("%"))
                except ValueError:
                    continue
                if pct - last >= 1 or pct >= 100:
                    last = pct
                    self.progress.emit(pct)
            elif line.startswith("[T]"):
                self.titled.emit(line[3:].strip())
            elif line.startswith("[F]"):
                files.append(line[3:].strip())
            elif line:
                tail = (tail + [line])[-40:]
        p.wait()
        files = [f for f in files if os.path.isfile(f)]
        return p.returncode, "\n".join(tail), files
