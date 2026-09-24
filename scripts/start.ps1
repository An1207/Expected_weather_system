$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Host "`.env 파일을 만들었습니다: $envPath" -ForegroundColor Yellow
    Write-Host "KMA_API_KEY 값을 입력한 뒤 이 스크립트를 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}

$apiKeyLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^KMA_API_KEY=' } | Select-Object -First 1
if (-not $apiKeyLine -or $apiKeyLine -eq "KMA_API_KEY=") {
    Write-Host "`.env의 KMA_API_KEY가 비어 있습니다." -ForegroundColor Red
    exit 1
}

Push-Location $projectRoot
try {
    docker compose up --build -d
    docker compose ps
    Write-Host "웹: http://localhost:3000" -ForegroundColor Green
    Write-Host "API 문서: http://localhost:8000/docs" -ForegroundColor Green
} finally {
    Pop-Location
}
