# 啟動開發伺服器（Windows PowerShell）
# 用法： .\run.ps1            預設 127.0.0.1:8000
#        .\run.ps1 -Host 0.0.0.0 -Port 8000   對外（給 Cloudflare Tunnel 用）
param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv")) {
    Write-Host "建立虛擬環境 .venv ..." -ForegroundColor Cyan
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path ".\.env")) {
    Write-Host "尚未建立 .env，從範例複製一份（請務必修改 APP_PASSWORD / SECRET_KEY）" -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

.\.venv\Scripts\python.exe scripts\init_db.py
Write-Host "啟動 http://${BindHost}:${Port}" -ForegroundColor Green
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host $BindHost --port $Port
