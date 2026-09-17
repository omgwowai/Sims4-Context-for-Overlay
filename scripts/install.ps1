# Windows PowerShell 5.1; no Python, admin rights or persistent policy change.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Alias('Profile')][string]$UserData,
    [string]$PackageDirectory,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($PackageDirectory)) {
    $PackageDirectory = Join-Path $PSScriptRoot '..\dist'
}

function Assert-GameClosed {
    if (Get-Process -Name TS4,TS4_x64,TS4_DX9_x64 -ErrorAction SilentlyContinue) {
        throw 'Close The Sims 4 before installing. No game process will be stopped automatically.'
    }
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Test-Profile([string]$Path) {
    return ((Test-Path -LiteralPath (Join-Path $Path 'Mods') -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $Path 'Options.ini') -PathType Leaf))
}

Assert-GameClosed
$source = Join-Path $PackageDirectory 'ContextOverlay.ts4script'
$manifestPath = Join-Path $PackageDirectory 'build-manifest.json'
if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or
    -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Package missing. Extract the complete internal trial ZIP first; a source checkout needs a build in dist/.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ((Get-Sha256 $source) -ne $manifest.package_sha256) {
    throw 'Package checksum mismatch. Download and extract the trial ZIP again.'
}
if ($manifest.game_bytecode_magic -ne '420d0d0a') {
    throw 'Unsupported bytecode format; this installer expects the validated Python 3.7 package.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $source).Path)
try {
    $entry = $archive.GetEntry('context_overlay/build_info.json')
    if ($null -eq $entry) { throw 'Package build metadata is missing.' }
    $reader = [System.IO.StreamReader]::new($entry.Open())
    try { $embedded = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
    foreach ($property in $manifest.build_info.PSObject.Properties) {
        if ($embedded.($property.Name) -ne $property.Value) {
            throw ('Embedded build metadata mismatch: ' + $property.Name)
        }
    }
    $bytecode = @($archive.Entries | Where-Object { $_.FullName.EndsWith('.pyc') })
    if ($bytecode.Count -eq 0 -or $null -eq $archive.GetEntry('context_overlay_bootstrap.pyc')) {
        throw 'Package bytecode or bootstrap is missing.'
    }
    foreach ($entry in $bytecode) {
        $stream = $entry.Open()
        try {
            $magic = New-Object byte[] 4
            if ($stream.Read($magic, 0, 4) -ne 4 -or
                ([BitConverter]::ToString($magic).Replace('-', '').ToLowerInvariant()) -ne $manifest.game_bytecode_magic) {
                throw ('Bytecode mismatch: ' + $entry.FullName)
            }
        } finally { $stream.Dispose() }
    }
} finally { $archive.Dispose() }

# A source checkout must install its current sources, including added/deleted files.
$sourceRoot = Join-Path $PSScriptRoot '..\src'
if ((Test-Path -LiteralPath $sourceRoot -PathType Container) -and
    [IO.Path]::GetFullPath($PackageDirectory) -ieq [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\dist'))) {
    $sourceRoot = (Resolve-Path -LiteralPath $sourceRoot).Path
    $sourceFiles = @(Get-ChildItem -LiteralPath $sourceRoot -Filter '*.py' -File -Recurse)
    $manifestFiles = @($manifest.files.PSObject.Properties)
    if ($sourceFiles.Count -ne $manifestFiles.Count) { throw 'Source file set differs from build; rebuild first.' }
    foreach ($file in $sourceFiles) {
        $relative = $file.FullName.Substring($sourceRoot.Length + 1).Replace('\', '/')
        $expected = $manifest.files.PSObject.Properties[$relative]
        if ($null -eq $expected -or (Get-Sha256 $file.FullName) -ne $expected.Value) {
            throw ('Source differs from build; rebuild first: ' + $relative)
        }
    }
}

if (-not $UserData) {
    $documentRoots = @([Environment]::GetFolderPath('MyDocuments'), (Join-Path $env:USERPROFILE 'Documents'))
    foreach ($oneDriveRoot in @($env:OneDrive, $env:OneDriveCommercial)) {
        if ($oneDriveRoot) { $documentRoots += Join-Path $oneDriveRoot 'Documents' }
    }
    $candidates = @($documentRoots | Where-Object { $_ } | ForEach-Object {
        Join-Path $_ 'Electronic Arts\The Sims 4'
    } | Sort-Object -Unique | Where-Object { Test-Profile $_ })
    if ($candidates.Count -eq 1) {
        $UserData = $candidates[0]
    } elseif ($NonInteractive -or $WhatIfPreference) {
        throw 'Cannot select one user-data folder. Pass -Profile with the folder containing Mods and Options.ini.'
    } else {
        Write-Host 'Select the actual user-data folder, NOT the game installation folder.'
        $candidates | ForEach-Object { Write-Host ('Found: ' + $_) }
        $UserData = (Read-Host 'Paste the folder containing Mods and Options.ini').Trim().Trim('"')
    }
}
if (-not $UserData -or -not (Test-Profile $UserData)) {
    throw 'Invalid user-data folder. Start the game once, exit it, and select the folder containing Mods and Options.ini.'
}
$profileRoot = (Resolve-Path -LiteralPath $UserData).Path
$mods = Join-Path $profileRoot 'Mods'
$destination = [IO.Path]::GetFullPath((Join-Path $mods 'ContextOverlay\ContextOverlay.ts4script'))
$duplicates = @(Get-ChildItem -LiteralPath $mods -Filter '*.ts4script' -File -Recurse | Where-Object {
    $_.Name -ieq 'ContextOverlay.ts4script' -and $_.FullName -ine $destination
})
if ($duplicates.Count) {
    throw ('Duplicate ContextOverlay packages found. Move old copies out of Mods first: ' + ($duplicates.FullName -join ', '))
}
Write-Host ('Version: ' + $manifest.build_info.module_version)
Write-Host ('User-data folder: ' + $profileRoot)
Write-Host ('Destination: ' + $destination)
if ((Test-Path -LiteralPath $destination -PathType Leaf) -and (Get-Sha256 $destination) -eq $manifest.package_sha256) {
    Write-Host 'This exact package is already installed. No files changed.'
    return
}
if (-not $PSCmdlet.ShouldProcess($destination, 'Install ContextOverlay; back up the previous package outside Mods')) { return }

$backup = $null
$previousHash = $null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
$supportRoot = Join-Path $profileRoot 'ContextOverlay'
if (Test-Path -LiteralPath $destination -PathType Leaf) {
    $previousHash = Get-Sha256 $destination
    $backup = Join-Path $supportRoot ('install-backups\' + $stamp + '\ContextOverlay.ts4script')
    [IO.Directory]::CreateDirectory((Split-Path -Parent $backup)) | Out-Null
    [IO.File]::Copy($destination, $backup, $false)
    if ((Get-Sha256 $backup) -ne $previousHash) { throw 'Backup verification failed; installed package is unchanged.' }
}
$destinationDirectory = Split-Path -Parent $destination
[IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null
$pending = Join-Path $destinationDirectory ([Guid]::NewGuid().ToString('N') + '.installing')
try {
    [IO.File]::Copy((Resolve-Path -LiteralPath $source).Path, $pending, $false)
    if ((Get-Sha256 $pending) -ne $manifest.package_sha256) { throw 'Copy verification failed.' }
    Assert-GameClosed
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        [IO.File]::Replace($pending, $destination, [NullString]::Value)
    } else {
        [IO.File]::Move($pending, $destination)
    }
} finally {
    if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending }
}
if ((Get-Sha256 $destination) -ne $manifest.package_sha256) { throw 'Installed checksum verification failed.' }
[IO.Directory]::CreateDirectory($supportRoot) | Out-Null
$receipt = [ordered]@{
    installed_at_utc = $stamp
    module_version = $manifest.build_info.module_version
    package_sha256 = $manifest.package_sha256
    destination = $destination
    previous_sha256 = $previousHash
    backup = $backup
}
[IO.File]::WriteAllText((Join-Path $supportRoot 'install-receipt.json'), ($receipt | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
# Retain only the package just replaced. Never prune until installation succeeds.
if ($backup) {
    $backupRoot = [IO.Path]::GetFullPath((Join-Path $supportRoot 'install-backups'))
    if ((Get-Item -LiteralPath $backupRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Backup directory must not be a link.'
    }
    foreach ($directory in @(Get-ChildItem -LiteralPath $backupRoot -Directory)) {
        if ($directory.Name -eq $stamp) { continue }
        if ($directory.Name -notmatch '^\d{8}T\d{13}Z$' -or
            ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        $old = [IO.Path]::GetFullPath((Join-Path $directory.FullName 'ContextOverlay.ts4script'))
        if (-not $old.StartsWith($backupRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup outside support directory.' }
        if ($old -ieq $backup -or -not (Test-Path -LiteralPath $old -PathType Leaf)) { continue }
        if ((Get-Item -LiteralPath $old).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        Remove-Item -LiteralPath $old
        if (@(Get-ChildItem -LiteralPath $directory.FullName -Force).Count -eq 0) {
            Remove-Item -LiteralPath $directory.FullName
        }
    }
}
Write-Host 'Installation complete. Enable Custom Content and Mods + Script Mods Allowed in game options, then restart the game.'
if ($backup) { Write-Host ('Previous package backup: ' + $backup) }
Write-Host 'Saves, game options, existing config and other Mods were preserved.'
