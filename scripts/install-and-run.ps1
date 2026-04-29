# Installs Car Player / Aurora from GitHub (main) and starts the full local server.
# Run once: irm https://raw.githubusercontent.com/vipogroup/car-player-aurora/main/scripts/install-and-run.ps1 | iex
# Requires: Windows, Python 3.10+ on PATH (winget install Python.Python.3.13)

param(
  [string]$InstallPath = "",
  [switch]$SkipBrowser
)

$ErrorActionPreference = 'Stop'
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
      & python -m pip install --upgrade pip 2>$null
      & python -m pip install -r requirements.txt
    }
    else {
      & py -3 -m pip install --upgrade pip 2>$null
      & py -3 -m pip install -r requirements.txt
    }
  }
  finally {
    Pop-Location
  }
}

Write-Host "=== Car Player / Aurora - install and run ===" -ForegroundColor Cyan
Write-Host "Install folder: $InstallPath"

if (-not (Test-PythonAvailable)) {
  Write-Host "[ERROR] Python not found. Install with (then reopen PowerShell):" -ForegroundColor Red
  Write-Host "  winget install Python.Python.3.13" -ForegroundColor Yellow
  Write-Host "Enable 'Add Python to PATH' in the installer." -ForegroundColor Yellow
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
