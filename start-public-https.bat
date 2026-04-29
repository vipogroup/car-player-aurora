@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Unblocked — קישור HTTPS ציבורי

echo.
echo ============================================================
echo   קישור כמו אתר (HTTPS) — Cloudflare Quick Tunnel
echo   חלון 1: השרת  ^|  חלון 2: המנהרה (הקישור יופיע שם)
echo ============================================================
echo.

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [חסר] cloudflared לא נמצא ב-PATH.
  echo.
  echo התקנה מהירה ^(PowerShell כמנהל^):
  echo   winget install Cloudflare.cloudflared
  echo.
  echo או הורדה: https://github.com/cloudflare/cloudflared/releases
  echo מדריך: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  echo.
  pause
  exit /b 1
)

echo פותח את השרת המקומי (פורט 5600)...
start "Unblocked Player — LAN" cmd /k "%~dp0start-server-lan.bat"

echo ממתין 6 שניות לעליית השרת...
timeout /t 6 /nobreak >nul

echo פותח חלון מנהרה — העתיקי משם את הקישור שמתחיל ב-https://
start "HTTPS — העתיקי את הקישור" cmd /k cloudflared tunnel --url http://127.0.0.1:5600

echo.
echo בחלון "HTTPS" יופיע שורה עם trycloudflare.com — זה הקישור לפתיחה בטלפון מכל מקום.
echo המחשב חייב להישאר דולק כל עוד רוצים שהקישור יעבוד.
echo.
pause
