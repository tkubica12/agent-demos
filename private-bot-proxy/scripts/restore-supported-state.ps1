. "$PSScriptRoot\common.ps1"
& "$PSScriptRoot\set-bot-pna.ps1" -State Enabled | Out-Null
& "$PSScriptRoot\switch-backend.ps1" -Backend "aca-control" | Out-Null
& "$PSScriptRoot\repair-bot-pe.ps1" | Out-Null
Start-Sleep -Seconds 30
& "$PSScriptRoot\deployed-smoke.ps1"
