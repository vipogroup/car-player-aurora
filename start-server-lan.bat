@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   נגן YouTube — רשת מקומית (טלפון / מחשבים אחרים)
echo   מאזין על 0.0.0.0 — אותו פורט 5600
echo ========================================
echo.

REM UNBLOCKED_PLAYER_HOST=0.0.0.0 — גישה מכל מכשיר באותה Wi-Fi
set "UNBLOCKED_PLAYER_HOST=0.0.0.0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction Stop ^| Select-Object -First 1; if ($x) { Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '[OK] נסגר תהליך שאחז בפורט 5600' } } catch { Write-Host '(אין מאזין על 5600)' }"

echo.
echo מחשב זה (מקומי): http://127.0.0.1:5600/
echo מטלפון/מחשב אחר: בדפדפן במחשב - כפתור "מובייל  סריקת QR" למעלה, או הכתובת lan_url בטרמינל.
echo אם אין גישה: הרצה כמנהל: add-firewall-unblocked.ps1  או  אפשרו פורט 5600 בחומת האש.
echo.
set "OPEN_BROWSER=1"
python -u unblocked_player.py
if errorlevel 1 (
  echo.
  echo *** השרת נכשל. בדקי Python ו-pip install yt-dlp
  pause
)
