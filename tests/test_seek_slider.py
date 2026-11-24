# -*- coding: utf-8 -*-
"""Полоска перемотки медиатеки: клик по дорожке ставит позицию туда, куда нажали."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from library_ui import SeekSlider  # noqa: E402

fails = []
s = SeekSlider(Qt.Orientation.Horizontal)
s.setRange(0, 10000)
s.resize(400, 24)
s.setStyleSheet("QSlider::groove:horizontal { height:4px; background:#1b3570; }"
                "QSlider::handle:horizontal { background:white; width:10px; margin:-6px 0; }")
s.show()
app.processEvents()
released = []
s.sliderReleased.connect(lambda: released.append(s.value()))
for frac in (0.75, 0.2, 0.5):
    x = int(400 * frac)
    QTest.mouseClick(s, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 12))
    app.processEvents()
    want = 10000 * frac
    ok = abs(s.value() - want) < 400 and released and released[-1] == s.value()
    print(("OK  " if ok else "FAIL"), f"клик на {int(frac * 100)} % → {s.value()} (отпускание: {released[-1:]})")
    if not ok:
        fails.append(frac)
print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
