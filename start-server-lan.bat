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

set "OPEN_BROWSER=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-player-lan.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Server failed to start.
  echo Try running setup-windows.bat first.
  pause
  exit /b 1
)
