# Compatibility entrypoint: now installs the full bound engine, not just text.
[CmdletBinding()]
param([switch]$Force, [switch]$RegisterMcp)
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'install-luna-router.ps1') -RegisterMcp:$RegisterMcp
exit $LASTEXITCODE
