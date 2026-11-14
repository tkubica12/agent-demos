#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$SkipNetwork,
    [string]$SubscriptionId,
    [string]$PythonIndexUrl
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$failed = $false
foreach ($spec in @(@('az', 'version'), @('terraform', 'version'), @('uv', '--version'))) {
    $tool = Get-Command $spec[0] -ErrorAction SilentlyContinue
    if (-not $tool) { Write-Host "FAIL: $($spec[0]) not found."; $failed = $true; continue }
    $version = & $tool.Source $spec[1]
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: $($spec[0]) version command."; $failed = $true }
    else { Write-Host ($version | Out-String).Trim() }
}
if (Get-Command terraform -ErrorAction SilentlyContinue) {
    $details = & terraform version -json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or [version]($details.terraform_version -replace '-.*$', '') -lt [version]'1.7.0') {
        Write-Host 'FAIL: Terraform 1.7 or newer is required.'
        $failed = $true
    }
}
if ($SubscriptionId) {
    & az account show --subscription $SubscriptionId --query '{subscription:id,tenant:tenantId}' -o json
    if ($LASTEXITCODE -ne 0) { Write-Host 'FAIL: subscription login; use az login --use-device-code.'; $failed = $true }
} else { Write-Host 'INFO: no subscription selected; Azure authentication/authorization not checked.' }
if (-not $SkipNetwork) {
    if (-not $PythonIndexUrl) {
        throw 'Supply the effective approved -PythonIndexUrl after inspecting project uv configuration and environment; no default feed is substituted.'
    }
    $uri = $null
    if (-not [Uri]::TryCreate($PythonIndexUrl, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query) {
        throw 'PythonIndexUrl must be an approved HTTPS feed without credentials or query secrets.'
    }
    foreach ($endpoint in @('https://management.azure.com/', 'https://login.microsoftonline.com/', 'https://graph.microsoft.com/', 'https://ghcr.io/v2/', $PythonIndexUrl)) {
        try {
            $null = Invoke-WebRequest -Uri $endpoint -Method Head -UseBasicParsing -TimeoutSec 15
            Write-Host "PASS: HTTPS reachability $endpoint"
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($endpoint -ne $PythonIndexUrl -and $status -in @(400, 401, 403)) {
                Write-Host "INFO: $endpoint returned HTTP $status; reachable, authorization not proven."
            } else {
                Write-Host "FAIL: $endpoint HTTPS request; inspect DNS, TLS and approved proxy/feed."
                $failed = $true
            }
        }
    }
} else { Write-Host 'SKIP: network checks; private DNS/TLS, feeds and registry access remain unverified.' }
Write-Host 'Provider locks, Python resolver, private endpoint reachability and RBAC/Graph rights require separate checks.'
if ($failed) { exit 1 }
exit 0
