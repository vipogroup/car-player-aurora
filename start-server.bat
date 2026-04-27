@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   נגן YouTube — unblocked_player.py
echo ========================================
echo.

REM סוגר מאזין קודם על 5600 (^| כדי שלא CMD יפרש את ה-pipeline של PowerShell)
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction Stop ^| Select-Object -First 1; if ($x) { Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '[OK] נסגר תהליך שאחז בפורט 5600' } } catch { Write-Host '(אין מאזין על 5600 — אם יש שגיאת פורט, סגרי תהליך ידנית)' }"

echo.
echo כתובת: http://127.0.0.1:5600/
echo בדיקה: http://127.0.0.1:5600/__player_check   (חייב להופיע OK_UNBLOCKED_PLAYER_V5)
echo אם הדף הראשי נראה ישן אבל הבדיקה תקינה — סגרי כל Python על 5600 או נקי מטמון דפדפן.
echo.
echo מפעיל את השרת... הדפדפן ייפתח אחרי שנייה כשהשרת מוכן.
echo לעצירה: Ctrl+C
echo.

set "OPEN_BROWSER=1"
python unblocked_player.py
if errorlevel 1 (
  echo.
  echo *** השרת נכשל. בדקי ש-Python מותקן וש-yt-dlp זמין: pip install yt-dlp
  pause
)
