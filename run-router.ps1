param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RouterArguments)
$ErrorActionPreference = 'Stop'
if (-not $RouterArguments -or $RouterArguments.Count -eq 0) {
    Write-Host 'Auto Routing is active for run and plan. Usage: .\luna-auto.ps1 -Prompt "..." -Project "E:\repo"'
    exit 2
}
$RouterScript = Join-Path $PSScriptRoot 'luna.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $RouterScript @RouterArguments
} else {
    & python $RouterScript @RouterArguments
}
exit $LASTEXITCODE
