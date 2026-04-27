# כמו restart-player.ps1, אבל מאזין על כל הממשקים (טלפון באותה רשת).
$ErrorActionPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
$env:UNBLOCKED_PLAYER_HOST = '0.0.0.0'
try {
  $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($x) {
    Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host '[OK] Freed port 5600'
  }
} catch { }

$env:OPEN_BROWSER = '0'
$env:PYTHONUNBUFFERED = '1'
python -u unblocked_player.py
exit $LASTEXITCODE
