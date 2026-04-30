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

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ and enable "Add Python to PATH".
  pause
  exit /b 1
)

if not exist "requirements.txt" (
  echo [ERROR] Missing requirements.txt in this folder.
  echo Extract the zip first, then run this file again.
  pause
  exit /b 1
)

echo Checking Python packages (yt-dlp)...
python -c "import yt_dlp" 2>nul
if errorlevel 1 (
  py -3 -c "import yt_dlp" 2>nul
)
if errorlevel 1 (
  echo.
  echo [ERROR] Missing dependencies. In THIS folder run once:
  echo   pip install -r requirements.txt
  echo   (or: py -3 -m pip install -r requirements.txt)
  echo Then double-click start-server-lan.bat again.
  pause
  exit /b 1
)

set "OPEN_BROWSER=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-player-lan.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Server exited with an error. Scroll up for Python messages.
  echo If packages are missing: pip install -r requirements.txt
  pause
  exit /b 1
)
