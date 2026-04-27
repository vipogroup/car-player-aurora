# Like restart-player.ps1 but binds LAN host (0.0.0.0).
$ErrorActionPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
$env:UNBLOCKED_PLAYER_HOST = '0.0.0.0'
try {
  $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($x) {
    Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host '[OK] Freed port 5600'
  } else {
    Write-Host '(No listener on 5600)'
  }
} catch { }

# If OPEN_BROWSER was set by caller, keep it. Otherwise default to no browser.
if (-not $env:OPEN_BROWSER) {
  $env:OPEN_BROWSER = '0'
}
$env:PYTHONUNBUFFERED = '1'
python -u unblocked_player.py
exit $LASTEXITCODE
