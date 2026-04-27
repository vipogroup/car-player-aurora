#Requires -Version 5.1
# התקנת חבילת Python (yt-dlp) — הריצו פעם אחת אחרי חילוץ ה-zip
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONUNBUFFERED = "1"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "חסר Python. התקינו 3.10+ מ-https://www.python.org (סמנו Add Python to PATH) והריצו שוב." -ForegroundColor Yellow
  exit 1
}

Write-Host "מעדכן pip ומתקין תלויות (yt-dlp)..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r (Join-Path $here "requirements.txt")
Write-Host ""
Write-Host "הושלם. כעת: start-server-lan.bat (לחיבור מהטלפון באותה WiFi)" -ForegroundColor Green
Write-Host "אם הטלפון לא מצליח להתחבר: הריצו add-firewall-unblocked.ps1 כ'הרצה כמנהל' (פעם אחת)." -ForegroundColor DarkGray
