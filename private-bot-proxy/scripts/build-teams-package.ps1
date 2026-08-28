. "$PSScriptRoot\common.ps1"
$entra = Get-Content (Join-Path $script:ArtifactRoot "entra.json") -Raw | ConvertFrom-Json -AsHashtable
$output = Join-Path $script:ArtifactRoot "teams-package"
$teamsAppIdPath = Join-Path $script:ArtifactRoot "teams-app-id.txt"
if (Test-Path $teamsAppIdPath) {
    $teamsAppId = (Get-Content $teamsAppIdPath -Raw).Trim()
} else {
    $teamsAppId = [guid]::NewGuid().ToString()
    $teamsAppId | Set-Content $teamsAppIdPath -Encoding utf8
}
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot python -m private_bot_proxy.teams_package --output $output --app-id $entra.appId --teams-app-id $teamsAppId
Write-Output (Join-Path $output "private-bot-proxy.zip")
