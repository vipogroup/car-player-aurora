@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Unblocked Player - LAN

echo.
echo ========================================
echo   Unblocked Player (LAN mode)
echo   For phone + PC on same Wi-Fi
echo ========================================
echo.

if not exist "requirements.txt" (
  echo [ERROR] Missing requirements.txt in this folder.
  echo Extract the zip first, then run this file again.
  pause
  exit /b 1
)

set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"

if defined PYEXE (
  echo Using local Python: .venv
  "%PYEXE%" -c "import yt_dlp" 2>nul
  if errorlevel 1 (
    echo.
    echo [ERROR] חבילות חסרות ב-.venv. הריצי מהתיקייה:
    echo   installer-postinstall.cmd
    echo או התקיני מחדש את CarPlayerAurora-Setup.exe
    pause
    exit /b 1
  )
  goto RUN_SERVER
)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH and no .venv here.
  echo התקיני Python 3.10+ עם PATH, או הריצי installer-postinstall.cmd אחרי התקנה מלאה.
  echo.
  echo PowerShell או CMD כמנהל ^(מומלץ — לכל המחשב^):
  echo   winget install Python.Python.3.13 --scope machine
  echo בלי מנהל ^(משתמש נוכחי בלבד^):
  echo   winget install Python.Python.3.13 --scope user
  echo אחרי ההתקנה סגרי את כל חלונות המסוף והפעילי שוב את הקובץ.
  echo אם אין winget: https://www.python.org/downloads/
  pause
  exit /b 1
)

echo Checking Python packages (yt-dlp)...
python -c "import yt_dlp" 2>nul
if errorlevel 1 py -3 -c "import yt_dlp" 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] חסרה חבילת yt-dlp. אפשרות א׳ — הריצי בתיקייה:
  echo   installer-postinstall.cmd
  echo אפשרות ב׳ — ידנית:
  echo   pip install -r requirements.txt
  pause
  exit /b 1
)

:RUN_SERVER
set "OPEN_BROWSER=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-player-lan.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Server exited with an error. Scroll up for Python messages.
  pause
  exit /b 1
)
