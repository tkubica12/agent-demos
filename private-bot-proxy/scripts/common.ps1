$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"

$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:ArtifactRoot = Join-Path $script:ProjectRoot ".artifacts"
$script:BootstrapState = Join-Path $script:ArtifactRoot "bootstrap.tfstate"
$script:ApplicationState = Join-Path $script:ArtifactRoot "application.tfstate"
$script:DeploymentState = Join-Path $script:ArtifactRoot "deployment.json"
$suffixPath = Join-Path $script:ArtifactRoot "suffix.txt"
if ($env:PBP_SKIP_AZURE_CONTEXT -eq "1") {
    $script:SubscriptionId = ""
    $script:TenantId = ""
    $script:Suffix = "local"
} else {
    $account = az account show -o json | ConvertFrom-Json
    if (-not $account.id -or -not $account.tenantId) {
        throw "An active Azure CLI subscription and tenant are required."
    }
    $script:SubscriptionId = $account.id
    $script:TenantId = $account.tenantId
    if (Test-Path $suffixPath) {
        $script:Suffix = (Get-Content $suffixPath -Raw).Trim()
    } else {
        New-Item -ItemType Directory -Path $script:ArtifactRoot -Force | Out-Null
        $source = "$script:SubscriptionId`:private-bot-proxy"
        $hash = [System.Security.Cryptography.SHA256]::HashData(
            [System.Text.Encoding]::UTF8.GetBytes($source)
        )
        $script:Suffix = "pbp$([Convert]::ToHexString($hash).ToLower().Substring(0, 7))"
        $script:Suffix | Set-Content $suffixPath -Encoding utf8
    }
}
$script:CoreLocation = "westeurope"
$script:Location = "northeurope"
$script:Hostname = "botservice.tomasonline.net"
$script:DnsZone = "tomasonline.net"
$script:DnsResourceGroup = "rg-base"

function Initialize-ArtifactRoot {
    New-Item -ItemType Directory -Path $script:ArtifactRoot -Force | Out-Null
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)]
        [string] $FilePath,
        [Parameter(ValueFromRemainingArguments)]
        [string[]] $Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Get-BootstrapOutput {
    $json = terraform -chdir="$script:ProjectRoot\infra\bootstrap" output -state="$script:BootstrapState" -json
    if ($LASTEXITCODE -ne 0) { throw "Unable to read bootstrap Terraform outputs." }
    return $json | ConvertFrom-Json -AsHashtable
}

function Get-ApplicationOutput {
    $json = terraform -chdir="$script:ProjectRoot\infra\application" output -state="$script:ApplicationState" -json
    if ($LASTEXITCODE -ne 0) { throw "Unable to read application Terraform outputs." }
    return $json | ConvertFrom-Json -AsHashtable
}

function Get-OutputValue {
    param([hashtable] $Outputs, [string] $Name)
    return $Outputs[$Name].value
}
