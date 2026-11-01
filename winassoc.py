# -*- coding: utf-8 -*-
"""Связь с проводником Windows (только для текущего пользователя, без прав администратора):
  * «Открыть с помощью → MP3 Плеер» для аудиофайлов;
  * пункт «▶ Играть в MP3 Плеере» в контекстном меню файлов и папок;
  * регистрация в «Приложения по умолчанию» — чтобы плеер можно было выбрать там.
Сделать себя «по умолчанию» молча Windows 10/11 не даёт: пользователь подтверждает сам."""
import os
import sys

IS_WIN = sys.platform == "win32"
PROGID = "MP3Player.AudioFile"
APP_EXE = "MP3Player.exe"
APP_TITLE = "MP3 Плеер"
CAPS = r"Software\MP3Player\Capabilities"
VERB = "mp3player.play"
VERB_TEXT = "▶ Играть в MP3 Плеере"


def launch_command():
    """Команда запуска с файлом: собранный exe или python + скрипт."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"', sys.executable
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.py")
    return f'"{w if os.path.exists(w) else exe}" "{script}"', (w if os.path.exists(w) else exe)


def _set(root, path, name, value, kind=None):
    import winreg
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, kind if kind is not None else winreg.REG_SZ, value)


def _get(root, path, name=""):
    import winreg
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _delete_tree(root, path):
    import winreg
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def _delete_value(root, path, name):
    import winreg
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def is_registered():
    if not IS_WIN:
        return False
    import winreg
    return _get(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\shell\open\command") is not None


def registered_command():
    import winreg
    return _get(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\shell\open\command")


def register(exts):
    """Зарегистрировать плеер для расширений exts (['.mp3', '.flac', …])."""
    if not IS_WIN:
        return False
    import winreg
    HK = winreg.HKEY_CURRENT_USER
    cmd, exe = launch_command()
    open_cmd = f'{cmd} "%1"'
    icon = f'"{exe}",0'

    # ProgID — «тип файла»
    base = rf"Software\Classes\{PROGID}"
    _set(HK, base, "", "Музыка (MP3 Плеер)")
    _set(HK, base, "FriendlyTypeName", "Музыка (MP3 Плеер)")
    _set(HK, base + r"\DefaultIcon", "", icon)
    _set(HK, base + r"\shell", "", "open")
    _set(HK, base + r"\shell\open", "", "Играть в MP3 Плеере")
    _set(HK, base + r"\shell\open\command", "", open_cmd)

    # само приложение (для «Открыть с помощью»)
    app = rf"Software\Classes\Applications\{APP_EXE}"
    _set(HK, app, "FriendlyAppName", APP_TITLE)
    _set(HK, app + r"\shell\open\command", "", open_cmd)
    for e in exts:
        _set(HK, app + r"\SupportedTypes", e, "")

    # «Приложения по умолчанию»
    _set(HK, CAPS, "ApplicationName", APP_TITLE)
    _set(HK, CAPS, "ApplicationDescription", "Плеер-виджет для рабочего стола")
    _set(HK, CAPS, "ApplicationIcon", icon)
    for e in exts:
        _set(HK, CAPS + r"\FileAssociations", e, PROGID)
    _set(HK, r"Software\RegisteredApplications", APP_TITLE, CAPS)

    # «Открыть с помощью» у каждого расширения + пункт контекстного меню
    for e in exts:
        _set(HK, rf"Software\Classes\{e}\OpenWithProgids", PROGID, b"", winreg.REG_NONE)
        verb = rf"Software\Classes\SystemFileAssociations\{e}\shell\{VERB}"
        _set(HK, verb, "MUIVerb", VERB_TEXT)
        _set(HK, verb, "Icon", icon)
        _set(HK, verb + r"\command", "", open_cmd)
    # папка: «Играть папку»
    dverb = rf"Software\Classes\Directory\shell\{VERB}"
    _set(HK, dverb, "MUIVerb", "▶ Играть папку в MP3 Плеере")
    _set(HK, dverb, "Icon", icon)
    _set(HK, dverb + r"\command", "", f'{cmd} "%1"')
    _notify()
    return True


def unregister(exts):
    if not IS_WIN:
        return
    import winreg
    HK = winreg.HKEY_CURRENT_USER
    _delete_tree(HK, rf"Software\Classes\{PROGID}")
    _delete_tree(HK, rf"Software\Classes\Applications\{APP_EXE}")
    _delete_tree(HK, r"Software\MP3Player")
    _delete_value(HK, r"Software\RegisteredApplications", APP_TITLE)
    for e in exts:
        _delete_value(HK, rf"Software\Classes\{e}\OpenWithProgids", PROGID)
        _delete_tree(HK, rf"Software\Classes\SystemFileAssociations\{e}\shell\{VERB}")
    _delete_tree(HK, rf"Software\Classes\Directory\shell\{VERB}")
    _notify()


def refresh_if_moved(exts):
    """Если exe переложили в другое место — тихо обновить пути в реестре."""
    if not is_registered():
        return False
    cmd, _exe = launch_command()
    if registered_command() != f'{cmd} "%1"':
        register(exts)
        return True
    return False


def open_default_apps_settings():
    """Страница «Приложения по умолчанию» для нашего плеера (Windows 11),
    в Windows 10 — общая страница приложений по умолчанию."""
    try:
        import urllib.parse
        os.startfile("ms-settings:defaultapps?registeredAppUser=" + urllib.parse.quote(APP_TITLE))
    except Exception:
        try:
            os.startfile("ms-settings:defaultapps")
        except Exception:
            pass
