param(
    [string]$UserData = 'C:\Users\ZixuanMin\Documents\Electronic Arts\The Sims 4',
    [string]$SaveName = 'Slot_00000008.save'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name TS4_x64,TS4_DX9_x64 -ErrorAction SilentlyContinue) {
    throw 'Close the game before preparing the isolated test profile.'
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$validationRoot = Join-Path $repoRoot '.validation'
New-Item -ItemType Directory -Path $validationRoot -Force | Out-Null
$statePath = Join-Path $validationRoot 'environment.json'
if (Test-Path -LiteralPath $statePath) {
    $previousState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if (-not $previousState.restored) { throw 'A test environment is still active; restore it first.' }
    $archivedState = Join-Path $validationRoot ('environment-restored-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json')
    Copy-Item -LiteralPath $statePath -Destination $archivedState
}
$profileRoot = (Resolve-Path -LiteralPath $UserData).Path.TrimEnd('\')
$backupRoot = Join-Path $profileRoot ('ContextOverlay-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
function Assert-InProfile([string]$Candidate) {
    $resolved = [System.IO.Path]::GetFullPath($Candidate)
    if (-not $resolved.StartsWith($profileRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the test profile: $resolved"
    }
    return $resolved
}
$modsPath = Assert-InProfile (Join-Path $profileRoot 'Mods')
$savesPath = Assert-InProfile (Join-Path $profileRoot 'saves')
$backupMods = Assert-InProfile (Join-Path $backupRoot 'Mods')
$backupSaves = Assert-InProfile (Join-Path $backupRoot 'saves')
$saveSource = Assert-InProfile (Join-Path $savesPath $SaveName)
$package = Join-Path $repoRoot 'dist\ContextOverlay.ts4script'
if (-not (Test-Path -LiteralPath $saveSource -PathType Leaf)) { throw 'Selected test save does not exist.' }
if (-not (Test-Path -LiteralPath $package -PathType Leaf)) { throw 'Build the package first.' }
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$state = [ordered]@{ user_data=$profileRoot; backup=$backupRoot; save_name=$SaveName; prepared=$false; restored=$false }
$state.original_save_hashes = @(Get-ChildItem -LiteralPath $savesPath -File | ForEach-Object {
    @{ name=$_.Name; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
})
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
Copy-Item -LiteralPath (Join-Path $profileRoot 'Options.ini') -Destination (Join-Path $backupRoot 'Options.ini')
Move-Item -LiteralPath $modsPath -Destination $backupMods
Move-Item -LiteralPath $savesPath -Destination $backupSaves
New-Item -ItemType Directory -Path $modsPath,$savesPath | Out-Null
Copy-Item -LiteralPath (Join-Path $backupMods 'Resource.cfg') -Destination (Join-Path $modsPath 'Resource.cfg')
Copy-Item -LiteralPath (Join-Path $backupSaves $SaveName) -Destination (Join-Path $savesPath $SaveName)
$install = Join-Path $modsPath 'ContextOverlay'
New-Item -ItemType Directory -Path $install | Out-Null
Copy-Item -LiteralPath $package -Destination (Join-Path $install 'ContextOverlay.ts4script')
$optionsPath = Join-Path $profileRoot 'Options.ini'
$options = Get-Content -LiteralPath $optionsPath -Raw
$options = $options -replace '(?m)^fullscreen\s*=\s*\d+', 'fullscreen = 0'
$options = $options -replace '(?m)^windowedfullscreen\s*=\s*\d+', 'windowedfullscreen = 0'
$options = $options -replace '(?m)^resolutionwidth\s*=\s*\d+', 'resolutionwidth = 1280'
$options = $options -replace '(?m)^resolutionheight\s*=\s*\d+', 'resolutionheight = 720'
[System.IO.File]::WriteAllText($optionsPath, $options, [System.Text.UTF8Encoding]::new($false))
$outputRoot = Join-Path $profileRoot 'ContextOverlay'
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $outputRoot 'config.json'), '{"development_driver":true}', [System.Text.UTF8Encoding]::new($false))
$state.prepared = $true
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
Write-Output "Prepared test profile. Original saves and mods: $backupRoot"
