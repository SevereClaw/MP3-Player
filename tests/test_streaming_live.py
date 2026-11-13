# -*- coding: utf-8 -*-
"""Живая проверка: Spotify-плейлист по ссылке, поиск и загрузка в Яндекс Музыке (без входа)."""
import os, sys, tempfile, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ymusic

ok = True
try:
    r = ymusic.fetch_list("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    print("spotify:", r["title"], len(r["tracks"]), r["tracks"][:3])
    assert len(r["tracks"]) >= 10
except Exception as e:
    ok = False
    print("SPOTIFY FAIL", type(e).__name__, e)
try:
    r = ymusic.fetch_list("https://open.spotify.com/album/4LH4d3cOWNNsVw41Gqt2kv")
    print("spotify album:", r["title"], len(r["tracks"]), r["tracks"][:2])
except Exception as e:
    print("spotify album fail", type(e).__name__, e)
c = ymusic.Client()
try:
    res = c.search("Кино Группа крови")
    print("yandex search:", len(res), res[:2])
    assert res
    t = res[0]
    print("resolve:", ymusic.resolve(c, {"title": "Группа крови", "artist": "Кино"}))
    try:
        url, preview = c.download_url(t["id"])
        print("download url ok, preview:", preview, url[:60])
        req = urllib.request.Request(url, headers={"User-Agent": ymusic.UA, "Range": "bytes=0-2000"})
        head = urllib.request.urlopen(req, timeout=20).read(16)
        print("audio head:", head[:4])
    except Exception as e:
        print("download-info без входа:", type(e).__name__, e)
except Exception as e:
    ok = False
    print("YANDEX FAIL", type(e).__name__, e)
for link in ("https://music.yandex.ru/album/3389008", "https://music.yandex.ru/users/yamusic-top/playlists/1076"):
    try:
        r = ymusic.fetch_list(link, c)
        print("yandex link:", link, r["title"], len(r["tracks"]), r["tracks"][:1])
    except Exception as e:
        print("yandex link fail:", link, type(e).__name__, e)
m = ymusic.Matcher({"C:\\M\\Кино - Группа крови.mp3": {"t": "Группа крови", "a": "Кино", "d": 286},
                    "C:\\M\\x.mp3": {"t": "Группа крови", "a": "Другие", "d": 100}})
assert m.find({"title": "Группа крови", "artist": "КИНО", "duration": 285}) == "C:\\M\\Кино - Группа крови.mp3"
assert m.find({"title": "Звезда", "artist": "Кино"}) is None
print("matcher ok")
sys.exit(0 if ok else 1)
