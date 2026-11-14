#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$json = Get-Content -LiteralPath $Path -Raw
if ($json -notmatch '^\s*\{') { throw 'Image release must be a JSON object.' }
$release = $json | ConvertFrom-Json
if ($release -isnot [pscustomobject] -or
    @($release.PSObject.Properties).Count -ne 3 -or
    @($release.PSObject.Properties.Name | Where-Object { $_ -cnotin @('schema', 'source_commit', 'images') }).Count) {
    throw 'Image release must contain only schema, source_commit and images.'
}
if (($release.schema -isnot [int] -and $release.schema -isnot [long]) -or $release.schema -ne 1) {
    throw 'Unsupported image release schema.'
}
if ($release.source_commit -isnot [string] -or
    $release.source_commit -cnotmatch '^[0-9a-f]{40}$' -or $release.source_commit -cne $Commit) {
    throw 'Image release source_commit does not match the pinned source commit.'
}
if ($release.images -isnot [pscustomobject] -or
    @($release.images.PSObject.Properties).Count -ne 3 -or
    @($release.images.PSObject.Properties.Name | Where-Object { $_ -cnotin @('runtime', 'gateway', 'simulator') }).Count) {
    throw 'Image release must contain exactly runtime, gateway and simulator.'
}
foreach ($component in @('runtime', 'gateway', 'simulator')) {
    $image = $release.images.$component
    if ($image -isnot [string] -or $image -cnotmatch '^ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}$') {
        throw "Image release $component must be a digest-pinned GHCR image without tags or credentials."
    }
}
return $release
