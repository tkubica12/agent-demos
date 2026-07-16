param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$ExpectedCanary,
    [string]$ToolboxName = "step-10-contoso-support"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    uv run python scripts\toolbox_ops.py promote `
        --toolbox $ToolboxName `
        --version $Version `
        --expected-canary $ExpectedCanary
    if ($LASTEXITCODE -ne 0) {
        throw "Toolbox promotion failed; the previous default was restored."
    }
} finally {
    Pop-Location
}
