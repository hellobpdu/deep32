#requires -Version 5.1
# Merge complete skill files, marked CLAUDE guidance and optional scoped MCP config.
# The config retains ${DEEP32_MCP_TOKEN}; no credential or service is opened here.
[CmdletBinding()]
param([string]$ClaudeHome='', [string]$WorkstationUrl='')
$ErrorActionPreference='Stop'
if(-not $ClaudeHome){$ClaudeHome=if($env:CLAUDE_CONFIG_DIR){$env:CLAUDE_CONFIG_DIR}else{Join-Path $HOME '.claude'}}
if($env:DEEP32_PYTHON){$python=$env:DEEP32_PYTHON}
else{$command=Get-Command python3.exe,python.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if($command){$python=$command.Source}else{Write-Output 'Python 3.10 or newer was not found; run the inventory prerequisite check.';exit 1}}
$setupArgs=@('-I','-B',(Join-Path $PSScriptRoot '..\setup_skills.py'),'--agent','claude','--home',$ClaudeHome)
if($WorkstationUrl){$setupArgs+=@('--workstation-url',$WorkstationUrl)}
& $python @setupArgs
exit $LASTEXITCODE
