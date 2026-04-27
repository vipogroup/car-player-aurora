@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "OPEN_BROWSER=0"
python -u unblocked_player.py
if errorlevel 1 (
  echo.
  echo *** נכשל. ודאי Python, והתקיני: pip install yt-dlp
  pause
)
