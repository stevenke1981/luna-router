# Auto Routing entrypoint. Does not modify Codex global config or ChatGPT UI model.
# Default: required-online Luna Decisions. Missing key/API error blocks; no local fallback.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true, Position=0)][ValidateNotNullOrEmpty()][string]$Prompt,
  [string]$Project = (Get-Location).Path,
  [ValidateSet('auto','luna','local')][string]$Routing = 'luna',
  [ValidateSet('metadata_only','public','no_external')][string]$DataPolicy = 'metadata_only',
  [switch]$TextOnly,
  [string]$Output
)
$ErrorActionPreference = 'Stop'
$RouterScript = Join-Path $PSScriptRoot 'router.py'
$RouterArguments = @($RouterScript, 'run', '--prompt', $Prompt,
                     '--project', $Project, '--routing', $Routing,
                     '--data-policy', $DataPolicy)
if ($TextOnly) { $RouterArguments += '--text-only' }
if ($Output) { $RouterArguments += @('--output', $Output) }
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @RouterArguments
} else {
    & python @RouterArguments
}
exit $LASTEXITCODE
