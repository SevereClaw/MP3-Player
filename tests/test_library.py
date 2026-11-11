# -*- coding: utf-8 -*-
"""Поиск по всей музыке и правка тегов (mp3, flac, m4a, ogg/opus) — на настоящих файлах."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("OK  " if ok else "FAIL"), name, "->", repr(got)[:120])
    if not ok:
        fails.append(name)


def make(path, codec, fmt=None):
    import av
    out = av.open(path, "w", format=fmt)
    rate = 48000 if codec in ("libopus", "opus") else 44100
    st = out.add_stream(codec, rate=rate)
    st.layout = "stereo"
    t = np.arange(rate) / rate
    pcm = (np.sin(2 * np.pi * 440 * t) * 0.2).astype(np.float32)
    frame_fmt = st.codec_context.format.name if st.codec_context.format else "fltp"
    for i in range(0, rate, 1024):
        chunk = pcm[i:i + 1024]
        arr = np.vstack([chunk, chunk]).astype(np.float32)
        fr = av.AudioFrame.from_ndarray(arr, format="fltp", layout="stereo")
        fr.sample_rate = rate
        for fr2 in ([fr] if frame_fmt == "fltp" else []):
            for p in st.encode(fr2):
                out.mux(p)
        if frame_fmt != "fltp":
            rs = av.AudioResampler(format=frame_fmt, layout="stereo", rate=rate)
            for r in rs.resample(fr):
                for p in st.encode(r):
                    out.mux(p)
    for p in st.encode(None):
        out.mux(p)
    out.close()


# маленькая настоящая картинка
from PySide6.QtGui import QImage, QColor, QGuiApplication          # noqa: E402
_app = QGuiApplication(sys.argv)
from PySide6.QtCore import QBuffer, QIODevice    # noqa: E402
img = QImage(64, 64, QImage.Format.Format_RGB32)
img.fill(QColor("#c03050"))
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
img.save(buf, "JPG", 90)
COVER = bytes(buf.data())

root = tempfile.mkdtemp()
music = os.path.join(root, "Музыка", "Кино")
os.makedirs(music)
os.makedirs(os.path.join(root, "Музыка", "Persona"))
cases = [("01. Кино - Группа крови.mp3", "libmp3lame", None),
         ("Звезда по имени Солнце.flac", "flac", None),
         ("Mass Destruction.m4a", "aac", "ipod"),
         ("Ёлка - Прованс.ogg", "libvorbis", "ogg"),
         ("Burn My Dread.opus", "libopus", "ogg")]
made = []
for name, codec, fmt in cases:
    sub = "Persona" if name.startswith(("Mass", "Burn")) else "Кино"
    p = os.path.join(root, "Музыка", sub, name)
    try:
        make(p, codec, fmt)
        made.append(p)
        print("создан", name)
    except Exception as e:
        print("не создать", name, "—", e)
check("создались mp3 и flac", all(any(m.endswith(x) for m in made) for x in (".mp3", ".flac")), True)

for p in made:
    n = os.path.basename(p)
    library.write_tags(p, "Название " + n[:6], "Исполнитель Ё", "Альбом X", cover=COVER)
    t = library.read_tags(p)
    check(f"{n}: название", t["title"], "Название " + n[:6])
    check(f"{n}: исполнитель", t["artist"], "Исполнитель Ё")
    check(f"{n}: альбом", t["album"], "Альбом X")
    check(f"{n}: обложка записалась", bool(t["cover"]) and t["cover"][:2] == b"\xff\xd8", True)
    library.write_tags(p, "Название " + n[:6], "Исполнитель Ё", "", remove_cover=True)
    t = library.read_tags(p)
    check(f"{n}: обложка убрана", t["cover"], None)
    check(f"{n}: пустой альбом убран", t["album"], "")

# поиск
mp3 = next(p for p in made if p.endswith(".mp3"))
library.write_tags(mp3, "Группа крови", "Кино", "Группа крови")
lib = library.Library(os.path.join(root, "library.json"))
import threading                                    # noqa: E402
ev = threading.Event()
lib.scan([os.path.join(root, "Музыка")], done=ev.set)
ev.wait(60)
check("в индексе все файлы", len(lib), len(made))
check("пара букв: «груп кр»", lib.search("груп кр")[:1], [mp3])
check("по исполнителю «КИНО»", mp3 in lib.search("КИНО"), True)
check("ё = е: «исполнитель е»", len(lib.search("исполнитель е")) >= len(made) - 1, True)
check("по папке «persona»", sorted(os.path.basename(p) for p in lib.search("persona")),
      sorted(os.path.basename(p) for p in made if "Persona" in p))
check("нет совпадений", lib.search("zzzqqq"), [])
lib2 = library.Library(os.path.join(root, "library.json"))
check("индекс сохранился на диск", len(lib2), len(made))
check("имя файла → исполнитель/название", library.guess_from_name("C:/m/01. Кино - Группа крови.mp3"),
      ("Кино", "Группа крови"))
print("ИТОГ:", "ошибок нет" if not fails else f"ошибки: {fails}")
sys.exit(1 if fails else 0)
