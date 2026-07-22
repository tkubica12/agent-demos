param(
    [switch]$DeleteHostedAgent,
    [string]$AgentName = "step-11-contoso-support-agent"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location $projectRoot
try {
    if (Test-Path "evidence\agent365-instance.json") {
        uv run python scripts\provision_agent365_instance.py cleanup
        if ($LASTEXITCODE -ne 0) {
            throw "Direct Agent 365 instance cleanup failed."
        }
    }
    foreach ($layer in @("infra\agent365", "infra\app", "infra\platform")) {
        if (Test-Path "$layer\terraform.tfstate") {
            Push-Location $layer
            try {
                terraform destroy -auto-approve -input=false
                if ($LASTEXITCODE -ne 0) {
                    throw "Terraform destroy failed in $layer."
                }
            } finally {
                Pop-Location
            }
        }
    }
    if ($DeleteHostedAgent) {
        azd ai agent delete $AgentName --no-prompt
        if ($LASTEXITCODE -ne 0) {
            throw "Hosted Agent deletion failed."
        }
    }
} finally {
    Pop-Location
}
