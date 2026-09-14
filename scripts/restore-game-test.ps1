param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name TS4_x64,TS4_DX9_x64 -ErrorAction SilentlyContinue) {
    throw 'Close the game before restoring the original profile.'
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $repoRoot '.validation\environment.json'
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.restored) { throw 'This environment has already been restored.' }
$profileRoot = (Resolve-Path -LiteralPath $state.user_data).Path.TrimEnd('\')
$backupRoot = (Resolve-Path -LiteralPath $state.backup).Path
function Assert-InProfile([string]$Candidate) {
    $resolved = [System.IO.Path]::GetFullPath($Candidate)
    if (-not $resolved.StartsWith($profileRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the named game profile: $resolved"
    }
    return $resolved
}
foreach ($name in @('Mods','saves')) {
    $original = Assert-InProfile (Join-Path $backupRoot $name)
    $current = Assert-InProfile (Join-Path $profileRoot $name)
    $testArchive = Assert-InProfile (Join-Path $backupRoot ('test-' + $name))
    if (-not (Test-Path -LiteralPath $original -PathType Container)) { throw "Missing original directory: $original" }
    if (Test-Path -LiteralPath $testArchive) { throw "Test archive already exists: $testArchive" }
    if (Test-Path -LiteralPath $current) { Move-Item -LiteralPath $current -Destination $testArchive }
    Move-Item -LiteralPath $original -Destination $current
}
Copy-Item -LiteralPath (Join-Path $backupRoot 'Options.ini') -Destination (Join-Path $profileRoot 'Options.ini') -Force
foreach ($item in $state.original_save_hashes) {
    $file = Assert-InProfile (Join-Path (Join-Path $profileRoot 'saves') $item.name)
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $item.sha256) {
        throw "Restored save hash differs: $($item.name)"
    }
}
if (-not $SkipInstall) {
    $install = Join-Path $profileRoot 'Mods\ContextOverlay'
    New-Item -ItemType Directory -Path $install -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'dist\ContextOverlay.ts4script') -Destination (Join-Path $install 'ContextOverlay.ts4script')
}
[System.IO.File]::WriteAllText((Join-Path $profileRoot 'ContextOverlay\config.json'), '{"development_driver":false}', [System.Text.UTF8Encoding]::new($false))
$state.restored = $true
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
Write-Output 'Original saves and mods restored. All original save hashes match.'
