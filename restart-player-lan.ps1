# Like restart-player.ps1 but binds LAN host (0.0.0.0).
# אחרי שינוי ב-unblocked_player.py חייבים להריץ מחדש — Python טוען את הקוד פעם אחת בהפעלה.
# לריסטארט אוטומטי בכל שמירה: restart-player-lan-watch.ps1 (או $env:UNBLOCKED_AUTO_RELOAD='1' לפני python)
# לשמירה על פלט ישן: $env:UNBLOCKED_NO_CLEAR='1' לפני ההרצה
$ErrorActionPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
if ($env:UNBLOCKED_NO_CLEAR -ne '1') { Clear-Host }
$env:UNBLOCKED_PLAYER_HOST = '0.0.0.0'
try {
  $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($x) {
    Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host '[OK] Freed port 5600'
  } else {
    Write-Host '[i] Port 5600 is free (no old server to stop)'
  }
} catch { }

# If OPEN_BROWSER was set by caller, keep it. Otherwise default to no browser.
if (-not $env:OPEN_BROWSER) {
  $env:OPEN_BROWSER = '0'
}
$env:PYTHONUNBUFFERED = '1'
python -u unblocked_player.py
exit $LASTEXITCODE
