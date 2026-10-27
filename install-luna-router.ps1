[CmdletBinding()]
param([string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }), [switch]$RegisterMcp)
$ErrorActionPreference = 'Stop'
$InstallerArguments = @((Join-Path $PSScriptRoot 'install_router.py'), '--codex-home', $CodexHome)
if ($RegisterMcp) { $InstallerArguments += '--register-mcp' }
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @InstallerArguments }
else { & python @InstallerArguments }
exit $LASTEXITCODE
