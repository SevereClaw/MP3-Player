# -*- coding: utf-8 -*-
"""Окно настройки «Итоги недели»."""
from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
                               QCheckBox, QComboBox, QTimeEdit, QPushButton)

import weekly

HELP = (
    "<b>Как завести бота (один раз, 3 минуты):</b><ol style='margin-left:-20px'>"
    "<li>Открой <a href='https://discord.com/developers/applications'>discord.com/developers/applications</a> → "
    "«New Application» (или своё приложение от Music → Discord).</li>"
    "<li>Слева <b>Bot</b> → <b>Reset Token</b> → скопируй токен и вставь сюда.</li>"
    "<li>Слева <b>OAuth2</b> → URL Generator → отметь <b>bot</b> → открой ссылку внизу и добавь бота "
    "на любой свой сервер (можно создать пустой). Без общего сервера бот не сможет написать.</li>"
    "<li>Твой ID: Discord → Настройки → Расширенные → включи <b>Режим разработчика</b>, "
    "потом правый клик по своему нику → <b>Копировать ID</b>.</li></ol>"
    "Токен хранится зашифрованным (средствами Windows) и уходит только в Discord."
)


class WeeklyDialog(QDialog):
    def __init__(self, cfg, on_save, on_test):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Итоги недели")
        self.on_save, self.on_test = on_save, on_test
        w = cfg.get("weekly") or {}
        lay = QVBoxLayout(self)
        intro = QLabel("Раз в неделю — что больше всего слушал: треки, исполнители, время по дням.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.cb_dc = QCheckBox("Присылать в Discord — бот напишет тебе в личку")
        self.cb_dc.setChecked(bool(w.get("discord")))
        self.cb_toast = QCheckBox("Показывать уведомлением Windows")
        self.cb_toast.setChecked(bool(w.get("toast", True)))
        lay.addWidget(self.cb_dc)
        lay.addWidget(self.cb_toast)

        form = QFormLayout()
        self.token = QLineEdit(weekly.unprotect(w.get("token", "")))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("токен бота")
        self.user = QLineEdit(str(w.get("user", "")))
        self.user.setPlaceholderText("например 123456789012345678")
        self.day = QComboBox()
        self.day.addItems(weekly.DAYS)
        self.day.setCurrentIndex(int(w.get("day", 6)))
        h, m = (w.get("time") or "20:00").split(":")
        self.time = QTimeEdit(QTime(int(h), int(m)))
        self.time.setDisplayFormat("HH:mm")
        form.addRow("Токен бота:", self.token)
        form.addRow("Твой ID в Discord:", self.user)
        form.addRow("Когда присылать:", self.day)
        form.addRow("Во сколько:", self.time)
        lay.addLayout(form)

        hl = QLabel(HELP)
        hl.setWordWrap(True)
        hl.setOpenExternalLinks(True)
        hl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(hl)
        self.msg = QLabel("Если компьютер в это время выключен — пришлю, как только включишь.")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)

        row = QHBoxLayout()
        test = QPushButton("Прислать итоги сейчас")
        test.clicked.connect(self.test)
        save = QPushButton("Сохранить")
        save.setDefault(True)
        save.clicked.connect(self.save)
        row.addWidget(test)
        row.addStretch(1)
        row.addWidget(save)
        lay.addLayout(row)
        self.resize(520, 0)

    def values(self):
        t = self.time.time()
        return {"discord": self.cb_dc.isChecked(), "toast": self.cb_toast.isChecked(),
                "token": weekly.protect(self.token.text().strip()) if self.token.text().strip() else "",
                "user": self.user.text().strip(), "day": self.day.currentIndex(),
                "time": f"{t.hour():02d}:{t.minute():02d}"}

    def save(self):
        v = self.values()
        if v["discord"] and (not self.token.text().strip() or not v["user"].isdigit()):
            self.msg.setText("Для Discord нужны токен бота и твой ID (только цифры).")
            return
        self.on_save(v)
        self.accept()

    def test(self):
        self.msg.setText("Отправляю…")
        self.on_test(self.values(), self.show_result)

    def show_result(self, ok, text):
        try:
            self.msg.setText(("✓ " if ok else "✗ ") + text)
        except RuntimeError:
            pass
