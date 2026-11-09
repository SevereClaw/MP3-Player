# -*- coding: utf-8 -*-
"""Создание синхронного текста (.lrc): вставил текст → жмёшь пробел на каждой строчке в такт.

Клавиши в окне разметки:
  Пробел      — эта строчка начинается сейчас (и переход к следующей)
  Enter       — пустая строчка сейчас (проигрыш: текст на экране гаснет)
  Backspace   — отменить последнюю отметку (и перемотать чуть назад)
  ← / →       — перемотать на 5 с
  P           — пауза / играть
"""
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QListWidget, QListWidgetItem, QStackedWidget, QSpinBox, QMessageBox)

REACTION_MS = 200            # человек жмёт чуть позже, чем звучит строчка


def fmt_ts(ms):
    ms = max(0, int(ms))
    return f"[{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}]"


def clean_lines(text):
    """Строчки текста: без пустых и без уже стоящих таймкодов [00:12.34]."""
    import re
    out = []
    for ln in (text or "").splitlines():
        ln = re.sub(r"^(\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+", "", ln.strip()).strip()
        if ln and not re.fullmatch(r"\[[a-z]{2,}:.*\]", ln, re.I):     # [ar:…] и т.п. — не строчки
            out.append(ln)
    return out


class LrcSession:
    """Разметка: какие строчки уже получили время."""
    def __init__(self, lines, reaction_ms=REACTION_MS):
        self.lines = list(lines)
        self.reaction = reaction_ms
        self.marks = []               # [(мс, текст)] — по порядку, пустой текст = проигрыш
        self.idx = 0                  # какую строчку ждём

    def done(self):
        return self.idx >= len(self.lines)

    def stamp(self, pos_ms):
        if self.done():
            return None
        t = max(0, pos_ms - self.reaction)
        if self.marks and t < self.marks[-1][0]:
            t = self.marks[-1][0] + 10             # время не идёт назад
        self.marks.append((t, self.lines[self.idx]))
        self.idx += 1
        return t

    def gap(self, pos_ms):
        t = max(0, pos_ms - self.reaction)
        if self.marks and t <= self.marks[-1][0]:
            return None
        self.marks.append((t, ""))
        return t

    def undo(self):
        """Убрать последнюю отметку; вернуть время, куда перемотать (за 2 с до неё)."""
        if not self.marks:
            return None
        t, text = self.marks.pop()
        if text:
            self.idx -= 1
        return max(0, t - 2000)

    def to_lrc(self, title="", artist="", album="", length_ms=0):
        head = []
        for tag, val in (("ar", artist), ("ti", title), ("al", album)):
            if val:
                head.append(f"[{tag}:{val}]")
        if length_ms:
            head.append(f"[length:{length_ms // 60000:02d}:{length_ms % 60000 // 1000:02d}]")
        head.append("[re:MP3 Плеер — разметка вручную]")
        body = [f"{fmt_ts(t)}{text}" for t, text in self.marks]
        return "\n".join(head + body) + "\n"


def fetch_plain(artist, title, album="", duration_s=0):
    """Обычный текст (без таймкодов) с lrclib.net или ''."""
    import lyrics_online as L
    try:
        if artist:
            it = L._get("get", {"artist_name": artist, "track_name": title, "album_name": album,
                                "duration": int(duration_s) if duration_s else None})
            if it and (it.get("plainLyrics") or "").strip():
                return it["plainLyrics"]
        for params in ({"track_name": title, "artist_name": artist}, {"q": f"{artist} {title}".strip()}):
            for it in L._get("search", params) or []:
                if (it.get("plainLyrics") or "").strip():
                    return it["plainLyrics"]
    except Exception:
        pass
    return ""


# ===================================================================== окно
class LrcMakerWindow(QWidget):
    saved = Signal(str)               # путь к .lrc

    def __init__(self, owner, path, title, artist, album, style=""):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.o = owner
        self.path, self.title_, self.artist, self.album = path, title, artist, album
        self.session = None
        self.setWindowTitle("Текст под песню — " + (f"{artist} — {title}" if artist else title))
        self.setStyleSheet(style)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{(artist + ' — ') if artist else ''}{title}</b>")
        head.setObjectName("title")
        lay.addWidget(head)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)

        # --- шаг 1: текст
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.addWidget(QLabel("1. Вставь текст песни — по строчке на строку (пустые строки не важны):"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Вставь сюда текст песни…")
        l1.addWidget(self.text, 1)
        r1 = QHBoxLayout()
        self.find_btn = QPushButton("🌐 Найти текст в интернете")
        self.find_btn.clicked.connect(self.find_text)
        r1.addWidget(self.find_btn)
        r1.addStretch(1)
        r1.addWidget(QLabel("Поправка на реакцию, мс:"))
        self.react = QSpinBox()
        self.react.setRange(0, 1000)
        self.react.setSingleStep(50)
        self.react.setValue(int(self.o.cfg.get("lrc_reaction", REACTION_MS)))
        self.react.setToolTip("На столько раньше ставится строчка — мы жмём чуть позже, чем слышим")
        r1.addWidget(self.react)
        go = QPushButton("Начать разметку ▶")
        go.setObjectName("accent")
        go.clicked.connect(self.start)
        r1.addWidget(go)
        l1.addLayout(r1)
        self.pages.addWidget(p1)

        # --- шаг 2: разметка
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("2. <b>Пробел</b> — строчка начинается сейчас · <b>Enter</b> — проигрыш (пусто) · "
                           "<b>Backspace</b> — отменить · <b>← →</b> — 5 с · <b>P</b> — пауза")
        self.hint.setWordWrap(True)
        l2.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        l2.addWidget(self.list, 1)
        self.state = QLabel("")
        self.state.setObjectName("dim")
        l2.addWidget(self.state)
        r2 = QHBoxLayout()
        for text, fn in (("← К тексту", self.back), ("Сначала", self.restart)):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(fn)
            r2.addWidget(b)
        r2.addStretch(1)
        self.save_btn = QPushButton("💾 Сохранить .lrc")
        self.save_btn.setObjectName("accent")
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.clicked.connect(self.save)
        r2.addWidget(self.save_btn)
        l2.addLayout(r2)
        self.pages.addWidget(p2)

        self.tick = QTimer(self, interval=100)
        self.tick.timeout.connect(self.refresh_state)
        self.resize(560, 620)
        cur = self.o.lyrics
        if cur is not None and getattr(cur, "lines", None):          # уже есть текст — взять строчки
            try:
                self.text.setPlainText("\n".join(t for t in cur.lines if t.strip()))
            except Exception:
                pass

    # --- шаг 1
    def find_text(self):
        self.find_btn.setEnabled(False)
        self.find_btn.setText("Ищу…")
        QTimer.singleShot(50, self._find_now)

    def _find_now(self):
        dur = max(0, self.o.player.duration()) / 1000.0
        txt = fetch_plain(self.artist, self.title_, self.album, dur)
        self.find_btn.setEnabled(True)
        if txt:
            self.text.setPlainText(txt)
            self.find_btn.setText("🌐 Найдено на lrclib.net ✓")
        else:
            self.find_btn.setText("🌐 Не нашёл — вставь вручную")

    def start(self):
        lines = clean_lines(self.text.toPlainText())
        if not lines:
            QMessageBox.information(self, "Текст под песню", "Сначала вставь текст песни.")
            return
        self.o.cfg["lrc_reaction"] = self.react.value()
        self.session = LrcSession(lines, self.react.value())
        self.list.clear()
        for ln in lines:
            QListWidgetItem("        " + ln, self.list)
        self.pages.setCurrentIndex(1)
        self.o.player.setPosition(0)
        if not self.o.is_playing():
            self.o.toggle_play()
        self.tick.start()
        self.refresh_list()
        self.setFocus()

    def back(self):
        self.tick.stop()
        self.pages.setCurrentIndex(0)

    def restart(self):
        if self.session:
            self.session = LrcSession(self.session.lines, self.session.reaction)
            self.refresh_list()
        self.o.player.setPosition(0)
        if not self.o.is_playing():
            self.o.toggle_play()

    # --- разметка
    def keyPressEvent(self, ev):
        if self.pages.currentIndex() != 1 or not self.session:
            return super().keyPressEvent(ev)
        k = ev.key()
        pos = int(self.o.player.position())
        if k == Qt.Key.Key_Space:
            self.session.stamp(pos)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.session.gap(pos)
        elif k == Qt.Key.Key_Backspace:
            back = self.session.undo()
            if back is not None:
                self.o.player.setPosition(back)
        elif k == Qt.Key.Key_Left:
            self.o.player.setPosition(max(0, pos - 5000))
        elif k == Qt.Key.Key_Right:
            self.o.player.setPosition(pos + 5000)
        elif k == Qt.Key.Key_P:
            self.o.toggle_play()
        elif k == Qt.Key.Key_Escape:
            self.close()
            return
        else:
            return super().keyPressEvent(ev)
        self.refresh_list()

    def refresh_list(self):
        s = self.session
        stamped = {}
        li = 0
        for t, text in s.marks:
            if text:
                stamped[li] = t
                li += 1
        for i in range(self.list.count()):
            it = self.list.item(i)
            ts = fmt_ts(stamped[i])[1:-1] if i in stamped else "        "
            it.setText(f"{ts:>8}   {s.lines[i]}")
            f = it.font()
            f.setBold(i == s.idx)
            it.setFont(f)
            it.setForeground(Qt.GlobalColor.gray if i in stamped else Qt.GlobalColor.white)
        if s.idx < self.list.count():
            self.list.setCurrentRow(s.idx)
            self.list.scrollToItem(self.list.item(s.idx), QListWidget.ScrollHint.PositionAtCenter)
        self.refresh_state()

    def refresh_state(self):
        if not self.session:
            return
        s = self.session
        pos = int(self.o.player.position())
        nxt = s.lines[s.idx] if not s.done() else "— все строчки размечены, жми «Сохранить» —"
        self.state.setText(f"{fmt_ts(pos)[1:-1]}  ·  размечено {s.idx} из {len(s.lines)}  ·  дальше: {nxt[:60]}")

    def save(self):
        s = self.session
        if not s or not s.marks:
            return
        if not s.done() and QMessageBox.question(
                self, "Текст под песню", f"Размечено {s.idx} из {len(s.lines)} строчек. Сохранить так?") \
                != QMessageBox.StandardButton.Yes:
            return
        target = os.path.splitext(self.path)[0] + ".lrc"
        if os.path.exists(target) and QMessageBox.question(
                self, "Текст под песню", f"Файл уже есть:\n{target}\n\nЗаменить?") != QMessageBox.StandardButton.Yes:
            return
        body = s.to_lrc(self.title_, self.artist, self.album, max(0, self.o.player.duration()))
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError:
            alt = os.path.join(os.path.dirname(self.o.settings_path), "lyrics",
                               os.path.basename(target))
            os.makedirs(os.path.dirname(alt), exist_ok=True)
            with open(alt, "w", encoding="utf-8") as f:
                f.write(body)
            target = alt
        self.tick.stop()
        self.saved.emit(target)
        self.close()

    def closeEvent(self, ev):
        self.tick.stop()
        super().closeEvent(ev)
