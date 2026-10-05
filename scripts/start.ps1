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
    Write-Host "KMA_API_KEY가 비어 있어 관측·예측은 대기 상태로 표시됩니다." -ForegroundColor Yellow
}

Push-Location $projectRoot
try {
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw "Docker 서비스 기동에 실패했습니다." }
    docker compose ps
    Write-Host "웹: http://localhost:3000" -ForegroundColor Green
    Write-Host "API 문서: http://localhost:8000/docs" -ForegroundColor Green
} finally {
    Pop-Location
}
