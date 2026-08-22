# Launch tender board: start server if not running, then open Chrome.
# Uses $PSScriptRoot so no hardcoded Chinese path (avoids PS5.1 cp950 issues).
$root = $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = "1"

$up = $false
try {
    if ((Invoke-WebRequest "http://127.0.0.1:8011/healthz" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) { $up = $true }
} catch {}

if (-not $up) {
    $py = Join-Path $root ".venv\Scripts\python.exe"
    Start-Process -FilePath $py `
        -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8011" `
        -WorkingDirectory $root -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            if ((Invoke-WebRequest "http://127.0.0.1:8011/healthz" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) { break }
        } catch {}
    }
}

Start-Process "http://127.0.0.1:8011"   # opens the default browser
