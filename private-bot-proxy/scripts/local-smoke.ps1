$env:PBP_SKIP_AZURE_CONTEXT = "1"
. "$PSScriptRoot\common.ps1"
$env:PBP_SKIP_AZURE_CONTEXT = $null
$env:PBP_TENANT_ID = "11111111-1111-1111-1111-111111111111"
$env:PBP_APP_ID = "22222222-2222-2222-2222-222222222222"
$env:PBP_MANAGED_IDENTITY_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot pytest
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot ruff check private_bot_proxy tests
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot ruff format --check private_bot_proxy tests
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot python -m private_bot_proxy.teams_package `
    --output "$script:ArtifactRoot\teams-package-test" `
    --app-id $env:PBP_APP_ID `
    --teams-app-id "44444444-4444-4444-4444-444444444444"
