@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Unblocked Player - Setup

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ and enable "Add Python to PATH", then run again.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] Setup failed. Please review the messages above.
  pause
  exit /b %RC%
)

echo.
echo Setup completed successfully.
echo Next step: run start-server-lan.bat
pause
