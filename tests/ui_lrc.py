# -*- coding: utf-8 -*-
"""Снимок окна «Текст под песню» (оба шага) — в журнал как base64."""
import base64, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
import mp3player, lrc_maker

class FakePlayer:
    pos = 15300
    def position(self): return self.pos
    def duration(self): return 286000
    def setPosition(self, ms): self.pos = ms
owner = types.SimpleNamespace(cfg={}, lyrics=None, player=FakePlayer(), is_playing=lambda: True,
                              toggle_play=lambda: None, settings_path="x")
w = lrc_maker.LrcMakerWindow(owner, "C:/m/Кино - Группа крови.mp3", "Группа крови", "Кино", "",
                             mp3player.Mp3Widget.LRC_STYLE)
w.text.setPlainText("Тёплое место, но улицы ждут\nОтпечатков наших ног\nЗвёздная пыль на сапогах\n"
                    "Мягкое кресло, клетчатый плед\nНе нажатый вовремя курок\nСолнечный день в ослепительных снах")
def dump(name):
    w.show(); app.processEvents()
    pm = w.grab(); buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); pm.save(buf, "PNG")
    print("SHOT", name, base64.b64encode(bytes(buf.data())).decode())
dump("lrc1.png")
w.start()
for t in (12400, 15100):
    owner.player.pos = t; w.session.stamp(t)
owner.player.pos = 17800; w.refresh_list()
dump("lrc2.png")
