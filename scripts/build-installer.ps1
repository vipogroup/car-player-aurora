# Build CarPlayerAurora-Setup.exe locally. Moves heavy folders to %TEMP% for the compile so [Files] Source * does not pack them.
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
# אל תשנה שם בתוך הריפו — Source * עדיין יארוז תיקייה בשם _build_*. מעבירים מחוץ לעץ המקור לזמן הקומפילציה.
$stashRoot = Join-Path $env:TEMP ("car-player-inno-stash-" + [Guid]::NewGuid().ToString('N'))
$stashLib = Join-Path $stashRoot 'offline_library'
$stashVenv = Join-Path $stashRoot 'dot_venv'
$stashZip1 = Join-Path $stashRoot 'car-music-player.zip'
$stashZip2 = Join-Path $stashRoot 'local-server-unblocked.zip'
New-Item -ItemType Directory -Path $stashRoot -Force | Out-Null
try {
  if (Test-Path 'offline_library') { Move-Item -LiteralPath 'offline_library' -Destination $stashLib }
  if (Test-Path 'car-music-player.zip') { Move-Item -LiteralPath 'car-music-player.zip' -Destination $stashZip1 }
  if (Test-Path 'local-server-unblocked.zip') { Move-Item -LiteralPath 'local-server-unblocked.zip' -Destination $stashZip2 }
  if (Test-Path '.venv') { Move-Item -LiteralPath '.venv' -Destination $stashVenv }

  & $iscc 'installer\CarPlayerAurora.iss'
  Get-Item 'installer\Output\CarPlayerAurora-Setup.exe' | Format-List FullName, Length
}
finally {
  if (Test-Path $stashLib) { Move-Item -LiteralPath $stashLib -Destination (Join-Path $root 'offline_library') }
  if (Test-Path $stashZip1) { Move-Item -LiteralPath $stashZip1 -Destination (Join-Path $root 'car-music-player.zip') }
  if (Test-Path $stashZip2) { Move-Item -LiteralPath $stashZip2 -Destination (Join-Path $root 'local-server-unblocked.zip') }
  if (Test-Path $stashVenv) { Move-Item -LiteralPath $stashVenv -Destination (Join-Path $root '.venv') }
  if (Test-Path $stashRoot) { Remove-Item -LiteralPath $stashRoot -Force -Recurse -ErrorAction SilentlyContinue }
}
