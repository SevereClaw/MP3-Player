@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [!] Could not install dependencies.
  pause
  exit /b 1
)
start "" pythonw launch.py %*
