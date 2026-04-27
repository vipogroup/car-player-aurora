@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Unblocked Player - Localhost

echo.
echo ========================================
echo   Unblocked Player (localhost mode)
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ and enable "Add Python to PATH".
  pause
  exit /b 1
)

echo.
echo Local URL:  http://127.0.0.1:5600/
echo Check URL:  http://127.0.0.1:5600/__player_check
echo.
echo Starting server... browser will open automatically.
echo Stop server: Ctrl+C
echo.

set "OPEN_BROWSER=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-player.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Server failed to start.
  echo Try running setup-windows.bat first.
  pause
)
