[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)] [string] $ProjectEndpoint,
    [Parameter(Mandatory = $false)] [string] $AgentName = "step-05-ag-ui-authenticated-gateway"
)

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "Set-AgentCard.ps1") -ProjectEndpoint $ProjectEndpoint -AgentName $AgentName
& (Join-Path $PSScriptRoot "Enable-A2A.ps1") -ProjectEndpoint $ProjectEndpoint -AgentName $AgentName
