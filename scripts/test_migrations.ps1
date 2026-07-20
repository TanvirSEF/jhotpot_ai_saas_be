$ErrorActionPreference = "Stop"

$composeFile = Join-Path $PSScriptRoot "..\compose.test.yaml"
$env:RUN_DATABASE_INTEGRATION_TESTS = "1"
$env:TEST_DATABASE_URL = (
    "postgresql+asyncpg://nexussuite:nexussuite_test_password" +
    "@127.0.0.1:55432/nexussuite_test"
)
$env:RUN_REDIS_INTEGRATION_TESTS = "1"
$env:TEST_REDIS_URL = "redis://127.0.0.1:56379/15"

try {
    docker compose -f $composeFile up -d --wait
    & "$PSScriptRoot\..\venv\Scripts\python.exe" -m unittest discover `
        -s "$PSScriptRoot\..\tests\integration" -v
}
finally {
    docker compose -f $composeFile down --volumes
}
