#requires -Version 5.1
# All six skills and marked AGENTS guidance are checked before merging.
# {{WORKSTATION_MCP_URL}} is replaced by the helper; DEEP32_MCP_TOKEN stays
# an environment reference. No credential or service is opened here.
[CmdletBinding()]
param([string]$CodexHome='', [string]$WorkstationUrl='')
$ErrorActionPreference='Stop'
if(-not $CodexHome){$CodexHome=if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $HOME '.codex'}}
if($env:DEEP32_PYTHON){$python=$env:DEEP32_PYTHON}
else{$command=Get-Command python3.exe,python.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if($command){$python=$command.Source}else{Write-Output 'Python 3.10 or newer was not found; run the inventory prerequisite check.';exit 1}}
$setupArgs=@('-I','-B',(Join-Path $PSScriptRoot '..\setup_skills.py'),'--agent','codex','--home',$CodexHome)
if($WorkstationUrl){$setupArgs+=@('--workstation-url',$WorkstationUrl)}
& $python @setupArgs
exit $LASTEXITCODE
