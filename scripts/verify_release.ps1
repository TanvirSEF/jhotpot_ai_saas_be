param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$live = Invoke-RestMethod -Uri "$BaseUrl/live" -TimeoutSec 5
if ($live.status -ne "alive") {
    throw "Liveness check failed."
}

$ready = Invoke-RestMethod -Uri "$BaseUrl/ready" -TimeoutSec 10
if ($ready.status -ne "ready") {
    throw "Readiness check failed."
}

Write-Output "Release checks passed for $BaseUrl"
