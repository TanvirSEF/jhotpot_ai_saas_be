param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseUrl,
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = Join-Path $resolvedOutput "nexussuite-$timestamp.dump"

if (Test-Path -LiteralPath $backupPath) {
    throw "Refusing to overwrite existing backup: $backupPath"
}

$libpqUrl = $DatabaseUrl -replace '^postgresql\+asyncpg://', 'postgresql://'
& pg_dump --format=custom --no-owner --no-acl --file=$backupPath --dbname=$libpqUrl
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

& pg_restore --list $backupPath | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Backup validation failed: $backupPath"
}

Write-Output $backupPath
