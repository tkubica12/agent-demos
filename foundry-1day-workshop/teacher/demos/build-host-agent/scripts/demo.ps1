[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("contract", "preflight", "prepare", "prompt", "memory", "memory-fallback", "memory-reset", "local", "deploy", "verify", "showcase", "cleanup")]
    [string]$Command = "preflight",

    [Parameter(ValueFromRemainingArguments)]
    [string[]]$RemainingArguments
)

$ErrorActionPreference = "Stop"
$demoRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $demoRoot "..\..\..")).Path
$script = Join-Path $PSScriptRoot "demo.py"

& uv run --project $repoRoot python $script $Command @RemainingArguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
