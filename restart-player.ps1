# Free TCP 5600 then run unblocked_player (OPEN_BROWSER=0). Used by VS Code / Cursor task.
$ErrorActionPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
try {
  $x = Get-NetTCPConnection -LocalPort 5600 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($x) {
    Stop-Process -Id $x.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host '[OK] Freed port 5600'
  } else {
    Write-Host '(No listener on 5600)'
  }
} catch { }

$env:OPEN_BROWSER = '0'
$env:PYTHONUNBUFFERED = '1'
python -u unblocked_player.py
exit $LASTEXITCODE
