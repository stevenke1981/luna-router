# Installs generated Codex agents without changing the global Codex provider/model.
[CmdletBinding()]
param(
  [ValidateSet('user','project')][string]$Scope = 'user',
  [string]$Project = (Get-Location).Path,
  [switch]$UseBundledSnapshot
)
$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $base 'free_agents.py'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.10+ is required' }
if ($UseBundledSnapshot) {
  & python $script sync --snapshot
} else {
  & python $script sync
}
if ($LASTEXITCODE -ne 0) { throw 'Could not refresh the free catalog. No installation performed.' }
if ($Scope -eq 'project') {
  & python $script install --scope project --project $Project
} else {
  & python $script install --scope user
}
if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Check any conflicting files.' }
Write-Host 'Codex roles installed; reopen Codex to refresh custom agents.'
Write-Host 'Default Codex model_provider/config.toml was not changed.'
