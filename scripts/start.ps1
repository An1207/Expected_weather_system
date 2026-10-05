$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Host "`.env 파일을 만들었습니다: $envPath" -ForegroundColor Yellow
    Write-Host "`.env의 ASOS/API Hub 인증키와 관측 URL을 입력한 뒤 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}

$apiKeyLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^KMA_API_KEY=' } | Select-Object -First 1
if (-not $apiKeyLine -or $apiKeyLine -eq "KMA_API_KEY=") {
    Write-Host "KMA_API_KEY가 비어 있어 장기 일자료 수집과 예측 입력 조회가 불가능합니다. API Hub 관측 설정은 별도로 확인하세요." -ForegroundColor Yellow
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
