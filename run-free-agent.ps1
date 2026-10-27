# Send only text that the user explicitly approves for external processing.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Agent,
  [Parameter(Mandatory=$true)][string]$TextFile,
  [switch]$Approved,
  [string]$OutputFile
)
$ErrorActionPreference='Stop'
$base=Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments=@((Join-Path $base 'free_agents.py'),'call','--agent',$Agent,'--text-file',$TextFile)
if ($Approved) { $arguments+=@('--data-policy','approved','--approved') }
else { $arguments+=@('--data-policy','public') }
if ($OutputFile) { $arguments+=@('--output',$OutputFile) }
& python @arguments
exit $LASTEXITCODE
