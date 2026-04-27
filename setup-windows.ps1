#Requires -Version 5.1
# Install python dependencies once after extracting the zip.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONUNBUFFERED = "1"

$pyExe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
  $pyExe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $pyExe = "py -3"
}
if (-not $pyExe) {
  Write-Host "Python was not found. Install Python 3.10+ from https://www.python.org and enable Add Python to PATH." -ForegroundColor Yellow
  exit 1
}

Write-Host "Updating pip and installing dependencies..." -ForegroundColor Cyan
Invoke-Expression "$pyExe -m pip install --upgrade pip"
Invoke-Expression "$pyExe -m pip install -r `"$($here)\requirements.txt`""
Write-Host ""
Write-Host "Setup complete. Next step: run start-server-lan.bat" -ForegroundColor Green
Write-Host "If phone cannot connect: run add-firewall-unblocked.ps1 as Administrator (once)." -ForegroundColor DarkGray
