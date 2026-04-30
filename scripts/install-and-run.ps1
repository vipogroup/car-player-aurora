# Installs Car Player / Aurora from GitHub (main) and starts the full local server.
# Run once: irm https://raw.githubusercontent.com/vipogroup/car-player-aurora/main/scripts/install-and-run.ps1 | iex
# Requires: Windows, Python 3.10+ on PATH (e.g. winget install Python.Python.3.13 --scope machine)

param(
  [string]$InstallPath = "",
  [switch]$SkipBrowser
)

$ErrorActionPreference = 'Stop'
# PS 7+: pip/python לפעמים כותבים אזהרות ל-stderr — בלי זה הסקריפט נעצר ב-NativeCommandError למרות שהפקודה הצליחה
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}
$RepoZipUrl = 'https://github.com/vipogroup/car-player-aurora/archive/refs/heads/main.zip'

if (-not $InstallPath) {
  $InstallPath = Join-Path $env:USERPROFILE 'CarPlayer-Aurora'
}

function Test-PythonAvailable {
  if (Get-Command python -ErrorAction SilentlyContinue) { return $true }
  if (Get-Command py -ErrorAction SilentlyContinue) { return $true }
  return $false
}

function Invoke-PipRequirements {
  param([string]$Root)
  Push-Location $Root
  try {
    if (Get-Command python -ErrorAction SilentlyContinue) {
      & python -m pip install --upgrade pip --disable-pip-version-check 2>&1 | Out-Null
      & python -m pip install -r requirements.txt
    }
    else {
      & py -3 -m pip install --upgrade pip --disable-pip-version-check 2>&1 | Out-Null
      & py -3 -m pip install -r requirements.txt
    }
  }
  finally {
    Pop-Location
  }
}

function Install-DesktopShortcuts {
  param([string]$Root)
  try {
    $desk = [Environment]::GetFolderPath('Desktop')
    if (-not $desk) { return }
    $wsh = New-Object -ComObject WScript.Shell
    $serverBat = Join-Path $Root 'start-server-lan.bat'
    $openBat = Join-Path $Root 'open-aurora.bat'
    if (-not (Test-Path -LiteralPath $serverBat)) { return }
    $lnk1 = Join-Path $desk 'Car Player server.lnk'
    $s1 = $wsh.CreateShortcut($lnk1)
    $s1.TargetPath = $serverBat
    $s1.WorkingDirectory = $Root
    $s1.WindowStyle = 1
    $s1.Description = 'Car Player / Aurora — local server (keep window open)'
    $s1.Save()
    if (Test-Path -LiteralPath $openBat) {
      $lnk2 = Join-Path $desk 'Aurora.lnk'
      $s2 = $wsh.CreateShortcut($lnk2)
      $s2.TargetPath = $openBat
      $s2.WorkingDirectory = $Root
      $s2.Description = 'פתיחת Aurora בדפדפן (השרת חייב לרוץ)'
      $s2.Save()
    }
    Write-Host "נוצרו בשולחן העבודה: Car Player server.lnk ו-Aurora.lnk" -ForegroundColor Green
  }
  catch {
    Write-Host "(לא נוצרו קיצורי דרך — אפשר ליצור ידנית ל-$Root\start-server-lan.bat)" -ForegroundColor Yellow
  }
}

Write-Host "=== Car Player / Aurora - install and run ===" -ForegroundColor Cyan
Write-Host "Install folder: $InstallPath"

if (-not (Test-PythonAvailable)) {
  Write-Host "[ERROR] Python not found. Install (then close all PowerShell/CMD windows and run this script again):" -ForegroundColor Red
  Write-Host "  As Administrator (machine-wide):" -ForegroundColor Yellow
  Write-Host "    winget install Python.Python.3.13 --scope machine" -ForegroundColor Yellow
  Write-Host "  Or current user only:" -ForegroundColor Yellow
  Write-Host "    winget install Python.Python.3.13 --scope user" -ForegroundColor Yellow
  Write-Host "Enable 'Add python.exe to PATH' in the Python installer. If winget is missing, install from python.org." -ForegroundColor Yellow
  exit 1
}

$tmpZip = Join-Path $env:TEMP ("car-player-" + [guid]::NewGuid().ToString() + '.zip')
$tmpParent = Join-Path $env:TEMP ("car-player-extract-" + [guid]::NewGuid().ToString())

try {
  Write-Host "Downloading repository ZIP..."
  Invoke-WebRequest -Uri $RepoZipUrl -OutFile $tmpZip -UseBasicParsing

  New-Item -ItemType Directory -Path $tmpParent -Force | Out-Null
  Write-Host "Extracting..."
  Expand-Archive -LiteralPath $tmpZip -DestinationPath $tmpParent -Force

  $inner = Get-ChildItem -LiteralPath $tmpParent -Directory | Select-Object -First 1
  if (-not $inner) { throw 'ZIP had no root folder' }

  if (Test-Path -LiteralPath $InstallPath) {
    Write-Host "Removing existing folder: $InstallPath"
    Remove-Item -LiteralPath $InstallPath -Recurse -Force
  }
  Move-Item -LiteralPath $inner.FullName -Destination $InstallPath

  Write-Host "Installing Python dependencies (first run may take a minute)..."
  Invoke-PipRequirements -Root $InstallPath

  Install-DesktopShortcuts -Root $InstallPath

  Write-Host "Starting local server (new window). Leave that window open." -ForegroundColor Green
  $bat = Join-Path $InstallPath 'start-server-lan.bat'
  if (-not (Test-Path -LiteralPath $bat)) { throw "start-server-lan.bat not found under $InstallPath" }

  Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', 'start-server-lan.bat' -WorkingDirectory $InstallPath

  if (-not $SkipBrowser) {
    Start-Sleep -Seconds 5
    $url = 'http://127.0.0.1:5600/aurora/index.html'
    Write-Host "Opening browser: $url"
    Start-Process $url
  }
}
finally {
  if (Test-Path -LiteralPath $tmpZip) { Remove-Item -LiteralPath $tmpZip -Force -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $tmpParent) { Remove-Item -LiteralPath $tmpParent -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "Done. If the page does not load, wait a few seconds and refresh." -ForegroundColor Cyan
