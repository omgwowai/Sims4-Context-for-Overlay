param(
    [Parameter(Mandatory=$true, Position=0)][ValidateSet('prepare','restore')][string]$Action,
    [string]$UserData = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Electronic Arts\The Sims 4'),
    [string]$SaveName,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
if (Get-Process -Name TS4,TS4_x64,TS4_DX9_x64 -ErrorAction SilentlyContinue) {
    throw 'Close the game before preparing or restoring a test environment.'
}
$profileRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $UserData).Path).TrimEnd('\')
function In-Profile([string]$Relative) {
    $path = [IO.Path]::GetFullPath((Join-Path $profileRoot $Relative))
    if (-not $path.StartsWith($profileRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the test profile: $path"
    }
    return $path
}
$backup = In-Profile 'ContextOverlay\test-backup'
$statePath = Join-Path $backup 'environment.json'
$configPath = In-Profile 'ContextOverlay\config.json'
$installer = Join-Path $PSScriptRoot 'install.ps1'
$files = @('Options.ini', 'ContextOverlay\config.json')
if ($Action -eq 'prepare') {
    if (Test-Path -LiteralPath $backup) { throw 'A test backup exists; restore it first.' }
    if (-not $SaveName) { throw 'Pass -SaveName with the save file to copy for testing.' }
    $saveSource = In-Profile (Join-Path 'saves' $SaveName)
    if (-not (Test-Path -LiteralPath $saveSource -PathType Leaf)) { throw 'Selected test save does not exist.' }
    & $installer -Profile $profileRoot -NonInteractive -WhatIf
    New-Item -ItemType Directory -Path $backup | Out-Null
    $state = @{config_exists=(Test-Path -LiteralPath $configPath); save_hashes=@(
        Get-ChildItem -LiteralPath (In-Profile 'saves') -File | ForEach-Object {
            @{name=$_.Name; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
        })}
    foreach ($relative in $files) {
        $source = In-Profile $relative
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $backup (Split-Path -Leaf $relative))
        }
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
    foreach ($name in @('Mods','saves')) {
        $current = In-Profile $name
        Move-Item -LiteralPath $current -Destination (In-Profile ('ContextOverlay\test-backup\' + $name))
        New-Item -ItemType Directory -Path $current | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $backup 'Mods\Resource.cfg') -Destination (In-Profile 'Mods\Resource.cfg')
    Copy-Item -LiteralPath (Join-Path $backup ('saves\' + $SaveName)) -Destination $saveSource
    $optionsPath = In-Profile 'Options.ini'
    $options = Get-Content -LiteralPath $optionsPath -Raw
    foreach ($setting in @{fullscreen=0; windowedfullscreen=0; resolutionwidth=1280; resolutionheight=720}.GetEnumerator()) {
        $options = $options -replace ('(?m)^' + $setting.Key + '\s*=\s*\d+'), ($setting.Key + ' = ' + $setting.Value)
    }
    [IO.File]::WriteAllText($optionsPath, $options, [Text.UTF8Encoding]::new($false))
    $config = if ($state.config_exists) { Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
    $config | Add-Member -NotePropertyName development_driver -NotePropertyValue $true -Force
    [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    & $installer -Profile $profileRoot -NonInteractive
    Write-Output "Test environment ready. Original files: $backup"
} else {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($name in @('Mods','saves')) {
        $original = In-Profile ('ContextOverlay\test-backup\' + $name)
        $current = In-Profile $name
        if (Test-Path -LiteralPath $original) {
            if (Test-Path -LiteralPath $current) {
                Move-Item -LiteralPath $current -Destination (In-Profile ('ContextOverlay\test-backup\test-' + $name))
            }
            Move-Item -LiteralPath $original -Destination $current
        }
    }
    foreach ($relative in $files) {
        $destination = In-Profile $relative
        if ($relative -eq 'ContextOverlay\config.json' -and -not $state.config_exists) {
            if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination }
        } else {
            Copy-Item -LiteralPath (Join-Path $backup (Split-Path -Leaf $relative)) -Destination $destination -Force
        }
    }
    foreach ($item in $state.save_hashes) {
        $file = In-Profile ('saves\' + $item.name)
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $item.sha256) {
            throw "Restored save hash differs: $($item.name)"
        }
    }
    Remove-Item -LiteralPath (In-Profile 'ContextOverlay\test-backup') -Recurse -Force
    Write-Output 'Original files restored and save hashes verified; temporary test copies removed.'
    if (-not $SkipInstall) { & $installer -Profile $profileRoot -NonInteractive }
}
