Param(
    [string]$DbName = "donation_management",
    [string]$MysqlUser = "root",
    [string]$MysqlPassword = "",
    [int]$MysqlPort = 3307,
    [string]$MysqlHost = "localhost",
    [string]$MysqlExePath = "C:\xampp\mysql\bin\mysql.exe",
    [string]$MysqlDumpExePath = "C:\xampp\mysql\bin\mysqldump.exe",
    [string]$BackupDir = "database\backups",
    [switch]$ForceNoBackup,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

$schemaPath = Join-Path $projectRoot "database\schema.sql"
$migrationsDir = Join-Path $projectRoot "database\migrations"
$backupRoot = Join-Path $projectRoot $BackupDir

if (-not (Test-Path $MysqlExePath)) {
    throw "mysql client not found at: $MysqlExePath"
}

if (-not (Test-Path $schemaPath)) {
    throw "schema file not found: $schemaPath"
}

if (-not (Test-Path $migrationsDir)) {
    throw "migrations directory not found: $migrationsDir"
}

$connectionArgs = @("-h", $MysqlHost, "-P", $MysqlPort, "-u", $MysqlUser)
if ($MysqlPassword) {
    $connectionArgs += "-p$MysqlPassword"
}

function Invoke-MySqlCommand {
    param(
        [string]$Sql
    )

    if ($DryRun) {
        Write-Host "[DRY-RUN] mysql -h $MysqlHost -P $MysqlPort -u $MysqlUser -e \"$Sql\"" -ForegroundColor Yellow
        return
    }

    & $MysqlExePath @connectionArgs -e $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "mysql command failed: $Sql"
    }
}

function Convert-SqlForTargetDb {
    param(
        [string]$SqlContent,
        [string]$TargetDb
    )

    $converted = $SqlContent
    $converted = [regex]::Replace($converted, '(?im)^\s*CREATE\s+DATABASE\s+IF\s+NOT\s+EXISTS\s+`?\w+`?\s*;\s*', '')
    $converted = [regex]::Replace($converted, '(?im)^\s*USE\s+`?\w+`?\s*;\s*', '')
    $converted = [regex]::Replace($converted, '(?i)\bdonation_management\b', $TargetDb)
    return $converted
}

function Invoke-MySqlScript {
    param(
        [string]$SqlPath,
        [string]$TargetDb
    )

    if ($DryRun) {
        Write-Host "[DRY-RUN] apply SQL file: $SqlPath -> database: $TargetDb" -ForegroundColor Yellow
        return
    }

    $raw = Get-Content -Path $SqlPath -Raw
    $converted = Convert-SqlForTargetDb -SqlContent $raw -TargetDb $TargetDb
    $tempFile = Join-Path $env:TEMP ("recover_" + [Guid]::NewGuid().ToString() + ".sql")

    try {
        Set-Content -Path $tempFile -Value $converted -Encoding UTF8
        Get-Content -Path $tempFile -Raw | & $MysqlExePath @connectionArgs $TargetDb
        if ($LASTEXITCODE -ne 0) {
            throw "Failed while importing SQL file: $SqlPath"
        }
    }
    finally {
        if (Test-Path $tempFile) {
            Remove-Item $tempFile -Force
        }
    }
}

Write-Host "Project root: $projectRoot" -ForegroundColor Cyan
Write-Host "Target DB: $DbName" -ForegroundColor Cyan
Write-Host "MySQL endpoint: $MysqlHost`:$MysqlPort" -ForegroundColor Cyan

if (-not $ForceNoBackup) {
    if (-not (Test-Path $MysqlDumpExePath)) {
        throw "mysqldump not found at: $MysqlDumpExePath"
    }

    if ($DryRun) {
        Write-Host "[DRY-RUN] backup database $DbName into $backupRoot" -ForegroundColor Yellow
    }
    else {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $backupFile = Join-Path $backupRoot ("${DbName}_backup_${timestamp}.sql")

        & $MysqlDumpExePath @connectionArgs --databases $DbName --routines --triggers --events --single-transaction > $backupFile
        if ($LASTEXITCODE -ne 0) {
            throw "Backup failed for database $DbName"
        }

        Write-Host "Backup created: $backupFile" -ForegroundColor Green
    }
}

Invoke-MySqlCommand -Sql "DROP DATABASE IF EXISTS $DbName; CREATE DATABASE $DbName CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
Invoke-MySqlScript -SqlPath $schemaPath -TargetDb $DbName

$migrationFiles = Get-ChildItem -Path $migrationsDir -Filter "*.sql" | Sort-Object Name
foreach ($file in $migrationFiles) {
    Invoke-MySqlScript -SqlPath $file.FullName -TargetDb $DbName
}

Write-Host "Recovery complete for database '$DbName'." -ForegroundColor Green
Write-Host "If needed, set MYSQL_DB=$DbName and MYSQL_PORT=$MysqlPort in .env" -ForegroundColor Magenta
