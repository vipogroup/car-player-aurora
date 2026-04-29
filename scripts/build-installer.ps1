# Build CarPlayerAurora-Setup.exe locally (~2–3 MB). Temporarily hides heavy folders Inno may still pack.
# Requires: Inno Setup 6 — winget install JRSoftware.InnoSetup
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Find-Iscc {
  foreach ($p in @(
      (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )) {
    if (Test-Path $p) { return $p }
  }
  throw 'ISCC.exe not found. Install: winget install JRSoftware.InnoSetup'
}

$iscc = Find-Iscc
$hidLib = '_build_exclude_offline_library'
$hidZip1 = '_build_exclude_car-music-player.zip'
$hidZip2 = '_build_exclude_local-server-unblocked.zip'
try {
  if (Test-Path 'offline_library') { Rename-Item -LiteralPath 'offline_library' -NewName $hidLib }
  if (Test-Path 'car-music-player.zip') { Rename-Item -LiteralPath 'car-music-player.zip' -NewName $hidZip1 }
  if (Test-Path 'local-server-unblocked.zip') { Rename-Item -LiteralPath 'local-server-unblocked.zip' -NewName $hidZip2 }

  & $iscc 'installer\CarPlayerAurora.iss'
  Get-Item 'installer\Output\CarPlayerAurora-Setup.exe' | Format-List FullName, Length
}
finally {
  if (Test-Path $hidLib) { Rename-Item -LiteralPath $hidLib -NewName 'offline_library' }
  if (Test-Path $hidZip1) { Rename-Item -LiteralPath $hidZip1 -NewName 'car-music-player.zip' }
  if (Test-Path $hidZip2) { Rename-Item -LiteralPath $hidZip2 -NewName 'local-server-unblocked.zip' }
}
