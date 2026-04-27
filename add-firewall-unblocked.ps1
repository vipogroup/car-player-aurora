#Requires -Version 5.1
#Requires -RunAsAdministrator
# פתיחת פורט 5600 לכניסה (TCP) — הריצו כ״הרצה כמנהל״ אם המובייל לא מתחבר
$ErrorActionPreference = "Stop"
$port = 5600
$ruleName = "Unblocked local music $port (TCP in)"

$ex = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($ex) {
  Write-Host "כלל כבר קיים: $ruleName" -ForegroundColor Green
  exit 0
}

New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port | Out-Null
Write-Host "נוסף כלל חומת אש: פורט $port (TCP) נכנס — נסי שוב מהטלפון." -ForegroundColor Green
