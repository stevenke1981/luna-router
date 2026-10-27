[CmdletBinding()]
param([ValidateSet('user','project')][string]$Scope='user', [string]$Project=(Get-Location).Path)
$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $base 'install-free-agents.ps1') -Scope $Scope -Project $Project
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
