@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install Python 3.10+ from python.org
  echo     and tick "Add python.exe to PATH" during install.
  goto :err
)

echo === Installing dependencies ===
python -m pip install --upgrade pip
python -m pip install --upgrade -r requirements.txt pyinstaller
if errorlevel 1 goto :err

echo === Building MP3Player (folder build - starts fast) ===
python -m PyInstaller --noconfirm --clean --onedir --windowed --name MP3Player --icon assets\icon.ico --add-data "assets;assets" --collect-all winrt --hidden-import mp3player --hidden-import skin_editor --hidden-import lyrics --hidden-import geom3d --hidden-import radio --hidden-import doulander_link launch.py
if errorlevel 1 goto :err

echo.
echo Done: dist\MP3Player\MP3Player.exe
echo Keep the whole dist\MP3Player folder together (exe + _internal).
explorer "dist\MP3Player"
pause
exit /b 0

:err
echo.
echo [!] Build failed. Scroll up to see the error.
pause
exit /b 1
