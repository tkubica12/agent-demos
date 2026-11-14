#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [Parameter(Mandatory = $true)][string]$WorkRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

function Resolve-SafePath([string]$Root, [string]$Relative) {
    if (-not $Relative -or [IO.Path]::IsPathRooted($Relative) -or
        $Relative -match '[:*?"<>|]' -or $Relative -match '(^|[\\/])(\.\.?|)([\\/]|$)' -or
        $Relative -match '[. ]([\\/]|$)') { throw "Unsafe relative path: $Relative" }
    $full = [IO.Path]::GetFullPath((Join-Path $Root $Relative.Replace('/', '\')))
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes root: $Relative" }
    $current = $full
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and
            ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Linked path is not permitted: $current"
        }
        $current = Split-Path -Parent $current
    }
    return $full
}

$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path.TrimEnd('\')
$WorkRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($WorkRoot).TrimEnd('\')
if ($WorkRoot -ieq [IO.Path]::GetPathRoot($WorkRoot).TrimEnd('\')) {
    throw 'WorkRoot must be a dedicated directory, not a filesystem root.'
}
if ($WorkRoot -ieq $PackageRoot -or
    $WorkRoot.StartsWith($PackageRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $PackageRoot.StartsWith($WorkRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkRoot must be outside the package and must not contain it.'
}
$manifestPath = Resolve-SafePath $PackageRoot 'manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.entryPoint -cne '.agents/skills/autopilots-deploy/SKILL.md' -or @($manifest.files).Count -eq 0) {
    throw 'Invalid package entry point or file inventory.'
}
$listed = @{}
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    $full = Resolve-SafePath $PackageRoot $relative
    $key = $relative.Replace('/', '\').ToLowerInvariant()
    if ($listed.ContainsKey($key) -or $key -eq 'manifest.json') { throw "Duplicate/reserved package path: $relative" }
    if ($entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-Sha256 $full) -cne $entry.sha256) {
        throw "Package hash mismatch: $relative"
    }
    $listed[$key] = $true
}
Get-ChildItem -LiteralPath $PackageRoot -Recurse -Force | ForEach-Object {
    if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked package entry: $($_.FullName)" }
    if (-not $_.PSIsContainer) {
        $relative = $_.FullName.Substring($PackageRoot.Length + 1).ToLowerInvariant()
        if ($relative -ne 'manifest.json' -and -not $listed.ContainsKey($relative)) {
            throw "Unlisted package file: $relative"
        }
    }
}
$skill = Resolve-SafePath $PackageRoot '.agents/skills/autopilots-deploy'
$lockPath = Resolve-SafePath $PackageRoot '.agents/skills/autopilots-deploy/assets/source.lock.json'
if (-not $listed.ContainsKey('.agents\skills\autopilots-deploy\assets\source.lock.json')) { throw 'Source lock is not inventoried.' }
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
if ($lock.commit -cnotmatch '^[0-9a-f]{40}$' -or $lock.sourcePath -cne 'autopilots-on-azure' -or
    $manifest.source.commit -cne $lock.commit) { throw 'Source lock differs from package source.' }
foreach ($item in @(@($lock.archive, $lock.sha256), @($lock.imageRelease.path, $lock.imageRelease.sha256))) {
    $path = Resolve-SafePath $PackageRoot $item[0]
    if (-not $listed.ContainsKey($item[0].Replace('/', '\').ToLowerInvariant()) -or
        $item[1] -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-Sha256 $path) -cne $item[1]) {
        throw 'Source archive or image release hash differs from source lock.'
    }
}
$archive = Resolve-SafePath $PackageRoot $lock.archive
$images = Resolve-SafePath $PackageRoot $lock.imageRelease.path
$null = & (Join-Path $PSScriptRoot 'Read-ImageRelease.ps1') -Path $images -Commit $lock.commit
$sourceDir = Resolve-SafePath $WorkRoot ('source-' + $lock.commit.Substring(0, 12))
if (Test-Path -LiteralPath $sourceDir) { throw 'Source directory already exists; use a fresh workspace, never overwrite it or its state.' }
$stateDir = Resolve-SafePath $WorkRoot 'state'
$null = New-Item -ItemType Directory -Force -Path $WorkRoot
$temporary = Resolve-SafePath $WorkRoot ('.extract-' + [guid]::NewGuid().ToString('N'))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    $seen = @{}
    foreach ($entry in $zip.Entries) {
        $relative = $entry.FullName.TrimEnd('/')
        $null = Resolve-SafePath $temporary $relative
        if ($relative -cne $lock.sourcePath -and -not $relative.StartsWith($lock.sourcePath + '/', [StringComparison]::Ordinal)) {
            throw "Unexpected source archive root: $relative"
        }
        $key = $relative.Replace('/', '\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { throw "Duplicate archive path: $relative" }
        $seen[$key] = $true
        if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw "Archive symbolic link: $relative" }
    }
    if (-not $seen.ContainsKey('autopilots-on-azure\scripts\deploy_simulator_environment.py')) {
        throw 'Source archive has no deployment entry point.'
    }
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $temporary)
    $sourceManifest = Get-Content -LiteralPath (Join-Path $temporary 'autopilots-on-azure\deployment-manifest.json') -Raw | ConvertFrom-Json
    if ($sourceManifest.source_commit -cne $lock.commit) { throw 'Extracted deployment source commit differs from the source lock.' }
    Copy-Item -LiteralPath $lockPath -Destination (Join-Path $temporary 'source.lock.json')
    Move-Item -LiteralPath $temporary -Destination $sourceDir
} finally {
    $zip.Dispose()
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
$null = New-Item -ItemType Directory -Force -Path $stateDir
foreach ($pair in @(@('stacks.example.toml', 'stacks.toml'), @('dns-zones.example.json', 'dns-zones.json'))) {
    $target = Resolve-SafePath $stateDir $pair[1]
    if (-not (Test-Path -LiteralPath $target)) {
        Copy-Item -LiteralPath (Join-Path $skill ('assets\' + $pair[0])) -Destination $target
    }
}
$stateImages = Resolve-SafePath $stateDir 'image-release.json'
if (Test-Path -LiteralPath $stateImages) {
    if ((Get-Sha256 $stateImages) -cne $lock.imageRelease.sha256) {
        throw 'Existing state image release differs; update private inputs deliberately, never overwrite them.'
    }
} else { Copy-Item -LiteralPath $images -Destination $stateImages }
[pscustomobject]@{
    Source = Join-Path $sourceDir 'autopilots-on-azure'
    State = $stateDir
    Config = Join-Path $stateDir 'stacks.toml'
    Commit = $lock.commit
    Images = $stateImages
}
