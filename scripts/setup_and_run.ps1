Param(
    [switch]$SkipInstall,
    [switch]$SkipRun
)

$ErrorActionPreference = 'Stop'

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $projectRoot

Write-Host "Project root: $projectRoot" -ForegroundColor Cyan

$pythonCandidates = @()

if ($env:VIRTUAL_ENV) {
    $pythonCandidates += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe')
}

$pythonCandidates += @(
    (Join-Path $projectRoot '.venv-2\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv-1\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv\Scripts\python.exe')
)

$pythonExe = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path $candidate) {
        try {
            & $candidate -c "import flask, flask_bcrypt, flask_mysqldb" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $pythonExe = $candidate
                break
            }
        }
        catch {
            # Try next candidate.
        }
    }
}

if (-not $pythonExe) {
    foreach ($candidate in $pythonCandidates) {
        if (Test-Path $candidate) {
            $pythonExe = $candidate
            break
        }
    }
}

if (-not $pythonExe) {
    $pythonExe = 'python'
    Write-Host "No local venv python found. Falling back to system 'python'." -ForegroundColor Yellow
}

Write-Host "Using Python: $pythonExe" -ForegroundColor Green

if (-not $SkipInstall) {
    Write-Host "Installing dependencies from requirements.txt ..." -ForegroundColor Cyan
    & $pythonExe -m pip install -r requirements.txt
}

Write-Host "" 
Write-Host "Database reminders:" -ForegroundColor Magenta
Write-Host "1) Run database/schema.sql" -ForegroundColor Magenta
Write-Host "2) Run migration scripts from database/MIGRATIONS.md (if needed)" -ForegroundColor Magenta
Write-Host "3) Insert admin@blood.com with bcrypt hash (use scripts/generate_admin_hash.py)" -ForegroundColor Magenta
Write-Host "" 
Write-Host "Optional integrations:" -ForegroundColor DarkCyan
Write-Host "- Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env for SMS" -ForegroundColor DarkCyan

if (-not $SkipRun) {
    Write-Host "" 
    Write-Host "Starting Flask app ..." -ForegroundColor Cyan
    & $pythonExe app.py
}
