param(
    [switch]$DeleteHostedAgent
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location $projectRoot
try {
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
        azd ai agent delete step-11-agent365-teams --no-prompt
        if ($LASTEXITCODE -ne 0) {
            throw "Hosted Agent deletion failed."
        }
    }
} finally {
    Pop-Location
}
