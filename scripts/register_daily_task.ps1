$ErrorActionPreference = "Stop"

$TaskName = "GovTenderDailyUpdate"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DailyScript = Join-Path $ScriptDir "daily_update.ps1"

if (-not (Test-Path -LiteralPath $DailyScript)) {
    throw "daily_update.ps1 not found: $DailyScript"
}

$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$actionArgs = ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $DailyScript)
$action = New-ScheduledTaskAction -Execute $psExe -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Daily -At "06:30"
$principalUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$principal = New-ScheduledTaskPrincipal -UserId $principalUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

schtasks /query /tn $TaskName
