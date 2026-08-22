$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectRoot "data"
$LogFile = Join-Path $LogDir "daily_update_last_run.log"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = "python"

if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
}

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Set-Location -LiteralPath $ProjectRoot

"daily_update started $(Get-Date -Format o)" | Set-Content -LiteralPath $LogFile -Encoding UTF8

function Invoke-DailyStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $scriptPath = Join-Path $ScriptDir $ScriptName
    $started = Get-Date
    $status = "ok"
    $exitCode = 0
    $message = ""

    try {
        Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ""
        Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value "=== $Name ==="
        Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ("command: {0} {1} {2}" -f $Python, $scriptPath, ($Arguments -join " "))

        $output = & $Python $scriptPath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        if ($output) {
            $output | ForEach-Object {
                Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ([string]$_)
            }
        }
        if ($exitCode -ne 0) {
            $status = "failed"
            $message = "exit_code=$exitCode"
        }
    }
    catch {
        $status = "failed"
        $exitCode = 1
        $message = $_.Exception.Message
        Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ("ERROR: {0}" -f $message)
    }

    $ended = Get-Date
    $seconds = [int]($ended - $started).TotalSeconds
    Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ("status: {0}; seconds: {1}" -f $status, $seconds)

    [PSCustomObject]@{
        Name = $Name
        Status = $status
        ExitCode = $exitCode
        Seconds = $seconds
        Message = $message
    }
}

$results = @()
$results += Invoke-DailyStep -Name "media_tenders" -ScriptName "crawl_media_tenders.py" -Arguments @("--pages", "2")
$results += Invoke-DailyStep -Name "edu_tenders" -ScriptName "crawl_edu_tenders.py" -Arguments @()
$results += Invoke-DailyStep -Name "grants_httpx" -ScriptName "crawl_grants.py" -Arguments @("--all")
$results += Invoke-DailyStep -Name "source_digiplus" -ScriptName "crawl_grants.py" -Arguments @("--source", "digiplus")
$results += Invoke-DailyStep -Name "pw_moea" -ScriptName "crawl_grants.py" -Arguments @("--source", "moea")
$results += Invoke-DailyStep -Name "pw_sbir_localcity" -ScriptName "crawl_grants.py" -Arguments @("--source", "sbir_localcity")
$results += Invoke-DailyStep -Name "pw_nstc_rfp" -ScriptName "crawl_grants.py" -Arguments @("--source", "nstc_rfp")
$results += Invoke-DailyStep -Name "pw_taipei_siti" -ScriptName "crawl_grants.py" -Arguments @("--source", "taipei_siti")
$results += Invoke-DailyStep -Name "pw_taichung_sbir" -ScriptName "crawl_grants.py" -Arguments @("--source", "taichung_sbir")
$results += Invoke-DailyStep -Name "pw_tainan_sbir" -ScriptName "crawl_grants.py" -Arguments @("--source", "tainan_sbir")
$results += Invoke-DailyStep -Name "dedup_grants" -ScriptName "dedup_grants.py" -Arguments @()

Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ""
Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value "AI enrich is intentionally not part of the daily task."
Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value "Manual AI enrich example: python scripts\enrich_ai.py --kind grants --do both --limit 50"
Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value ("daily_update finished {0}" -f (Get-Date -Format o))

Write-Host "daily_update.ps1 step summary"
$results | Format-Table -AutoSize
Write-Host ("log_file: {0}" -f $LogFile)
Write-Host "AI enrich is not run here. Manual example:"
Write-Host "python scripts\enrich_ai.py --kind grants --do both --limit 50"
