param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$ExpectedCanary,
    [string]$ToolboxName = "step-11-contoso-support"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    if (-not $env:FOUNDRY_PROJECT_ENDPOINT) {
        throw "FOUNDRY_PROJECT_ENDPOINT must be set."
    }
    $current = azd ai toolbox show $ToolboxName `
        --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
        --output json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $current.toolbox.default_version) {
        throw "Could not resolve the current toolbox default."
    }
    $previous = [string]$current.toolbox.default_version
    $endpoint = (
        "$($env:FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/'))/toolboxes/" +
        "$ToolboxName/versions/$Version/mcp?api-version=v1"
    )
    $candidate = uv run python scripts\toolbox_ops.py inspect-skill `
        --endpoint $endpoint |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $candidate.skill_canary -ne $ExpectedCanary) {
        throw "Candidate pinned skill did not pass acceptance."
    }
    $completed = $false
    try {
        azd ai toolbox publish $ToolboxName $Version `
            --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
            --output json |
            Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not publish toolbox version $Version."
        }
        uv run python smoke_deployed.py `
            --expected-escalation-canary $ExpectedCanary `
            --skip-memory
        if ($LASTEXITCODE -ne 0) {
            throw "Published toolbox failed deployed acceptance."
        }
        $completed = $true
    } finally {
        if (-not $completed) {
            azd ai toolbox publish $ToolboxName $previous `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json |
                Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Published acceptance failed and rollback also failed."
            }
        }
    }
} finally {
    Pop-Location
}
