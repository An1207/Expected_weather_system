param([switch]$IncludeHistory)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
$gitExecutable = if ($gitCommand) { $gitCommand.Source } else { 'C:\Program Files\Git\cmd\git.exe' }
$gitOptions = @('-c', ('safe.directory=' + $projectRoot.Replace('\', '/')), '-C', $projectRoot)

$trackedPaths = @(& $gitExecutable @gitOptions ls-files)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git tracking.' }
$trackedEnv = @($trackedPaths | Where-Object { ($_ -split '/')[-1] -match '^\.env(?:\..+)?$' -and ($_ -split '/')[-1] -ne '.env.example' })
if ($trackedEnv.Count) { throw 'A non-example environment file is tracked. Stop before committing.' }

$protectedPaths = @('.env', '.env.local', '.env.production', 'backend/.env', 'backend/.env.local',
                    'data/local/probe.json', 'artifacts/candidates/probe.json', 'artifacts/backups/probe.json')
foreach ($protectedPath in $protectedPaths) {
    & $gitExecutable @gitOptions check-ignore --quiet --no-index -- $protectedPath
    if ($LASTEXITCODE -ne 0) { throw ('Missing ignore rule: ' + $protectedPath) }
}
Write-Output 'PASS: private environment files, local data, candidates and backups are ignored; no private .env is tracked.'

$credentialValues = @()
$envPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    foreach ($configLine in (Get-Content -LiteralPath $envPath)) {
        if ($configLine -notmatch '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { continue }
        $settingName = $Matches[1]
        $settingValue = $Matches[2].Trim().Trim('"').Trim("'")
        if ($settingName -match '^KMA_.*KEY$') { $credentialValues += $settingValue }
        if ($settingName -match '^KMA_.*URL$' -and $settingValue -match '[?&]authKey=([^&]+)') {
            $credentialValues += $Matches[1]
        }
    }
}
$credentialPatterns = @($credentialValues | Where-Object { $_.Length -gt 8 -and $_ -notin @('YOUR_AUTH_KEY', 'YOUR_API_KEY') } | ForEach-Object {
    $_
    [System.Net.WebUtility]::UrlDecode($_)
    [Uri]::EscapeDataString([System.Net.WebUtility]::UrlDecode($_))
} | Select-Object -Unique)
if (-not $credentialPatterns.Count) { throw 'No local API credential available for comparison. Exclusion checks passed, but content audit is incomplete.' }

function Assert-NoCredential([string]$text, [string]$label) {
    foreach ($credentialPattern in $credentialPatterns) {
        if ($text.Contains($credentialPattern)) { throw ('Configured API credential detected in ' + $label + '. Nothing was printed; stop before pushing.') }
    }
}

$binaryExtensions = @('.cbm', '.joblib', '.png', '.jpg', '.jpeg', '.gif', '.zip', '.pdf', '.h5', '.parquet')
$scannedFiles = 0
foreach ($trackedPath in $trackedPaths) {
    $fullPath = Join-Path $projectRoot $trackedPath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { continue }
    if ([IO.Path]::GetExtension($fullPath).ToLowerInvariant() -in $binaryExtensions) { continue }
    Assert-NoCredential (Get-Content -LiteralPath $fullPath -Raw) ('tracked text file ' + $trackedPath)
    $scannedFiles++
}
$stagedDiff = @(& $gitExecutable @gitOptions diff --cached --no-ext-diff)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect staged changes.' }
Assert-NoCredential ($stagedDiff -join "`n") 'staged changes'
Write-Output ('PASS: current configured API credentials absent from ' + $scannedFiles + ' tracked text files and staged changes.')

if ($IncludeHistory) {
    $historyDiff = @(& $gitExecutable @gitOptions log --all --format= --patch --no-ext-diff)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect local Git history.' }
    Assert-NoCredential ($historyDiff -join "`n") 'local Git history text patches'
    Write-Output 'PASS: current configured API credentials absent from local Git history text patches.'
}
Write-Output 'Scope: known local API credentials in text only; this does not prove absence of unknown old keys or binary secrets.'
