$ErrorActionPreference = 'Stop'
$paths = @(
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$edge = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) {
    $installer = Join-Path $env:TEMP 'MicrosoftEdgeEnterpriseX64.msi'
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/?linkid=2093437' -OutFile $installer
    $process = Start-Process msiexec.exe -ArgumentList @('/i', $installer, '/qn', '/norestart') -PassThru -Wait
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Edge installation failed: $($process.ExitCode)"
    }
    Remove-Item $installer
    $edge = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $edge) { throw 'Edge browser is still missing.' }
(Get-Item $edge).VersionInfo | Select-Object ProductName, ProductVersion
