$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $projectRoot "requirements.txt"
$appUrl = "http://127.0.0.1:8000"

Set-Location -LiteralPath $projectRoot
Write-Host ""
Write-Host "AI Mock Interview" -ForegroundColor Green
Write-Host "Checking local environment..." -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python virtual environment could not be created." }
}

& $venvPython '-c' 'import fastapi, uvicorn, jinja2, multipart, pypdf, docx'
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies (first run only)..." -ForegroundColor Yellow
    & $venvPython -m pip install --default-timeout 120 -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check your network and try again." }
}

try {
    $existing = Invoke-RestMethod -Uri "${appUrl}/health" -TimeoutSec 2
    if ($existing.ok) {
        Write-Host "The service is already running. Opening browser..." -ForegroundColor Green
        try { Start-Process $appUrl -ErrorAction Stop } catch { Write-Host "Open this address manually: $appUrl" -ForegroundColor Yellow }
        exit 0
    }
} catch {
    # The service is not running yet.
}

Write-Host "Starting local service..." -ForegroundColor Green
$serverJob = Start-Job -ScriptBlock {
    param($python, $root)
    Set-Location -LiteralPath $root
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} -ArgumentList $venvPython, $projectRoot

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $health = Invoke-RestMethod -Uri "${appUrl}/health" -TimeoutSec 1
            if ($health.ok) { $ready = $true; break }
        } catch {
            if ($serverJob.State -in @('Failed','Stopped','Completed')) { break }
        }
    }
    if (-not $ready) { throw "The service did not start. Port 8000 may already be in use." }

    Write-Host ""
    Write-Host "Ready: $appUrl" -ForegroundColor Green
    Write-Host "The browser will open now. Close this window to stop the service." -ForegroundColor Cyan
    Write-Host ""
    try { Start-Process -FilePath "cmd.exe" -ArgumentList @('/c','start','',$appUrl) -WindowStyle Hidden -ErrorAction Stop }
    catch { Write-Host "The browser could not be opened automatically. Open this address manually: $appUrl" -ForegroundColor Yellow }
    while ($serverJob.State -notin @('Failed','Stopped','Completed')) {
        Start-Sleep -Seconds 1
    }
} finally {
    if ($null -ne $serverJob) { Stop-Job $serverJob -ErrorAction SilentlyContinue; Remove-Job $serverJob -Force -ErrorAction SilentlyContinue }
}
