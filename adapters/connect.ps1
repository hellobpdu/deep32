#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('codex','claude','both')][string]$Agent='',
    [string]$Workstation='',
    [string]$Name='',
    [string]$ProfileDir='',
    [switch]$Check,
    [ValidateSet('install','remove','status','check','pair','unpair','selftest','login')][string]$Relay='',
    [string]$RelayId='',
    [string]$AgentBinary='',
    [ValidateSet('','loopback','tailnet')][string]$RelayTransport='',
    [int]$RelayPort=7452,
    [string]$TailscaleBinary='',
    [switch]$NoAutostart,
    [switch]$Live,
    [switch]$Json
)
$ErrorActionPreference='Stop'
$python=if($env:DEEP32_CONNECT_PYTHON){$env:DEEP32_CONNECT_PYTHON}else{'python'}
$arguments=@('-I','-B',(Join-Path $PSScriptRoot 'connect.py'))
if($Relay){
    if($Workstation -or $Name -or $ProfileDir -or $Check){
        Write-Error 'Select relay lifecycle arguments separately from direct MCP enrolment.'
        exit 2
    }
    $arguments+=@('--relay',$Relay)
    if($RelayId){$arguments+=@('--relay-id',$RelayId)}
    if($Agent){$arguments+=@('--agent',$Agent)}
    if($AgentBinary){$arguments+=@('--agent-binary',$AgentBinary)}
    if($RelayTransport){$arguments+=@('--relay-transport',$RelayTransport)}
    if($PSBoundParameters.ContainsKey('RelayPort')){$arguments+=@('--relay-port',"$RelayPort")}
    if($TailscaleBinary){$arguments+=@('--tailscale-binary',$TailscaleBinary)}
    if($NoAutostart){$arguments+='--no-autostart'}
    if($Live){$arguments+='--live'}
    if($Json){$arguments+='--json'}
    & $python @arguments
    exit $LASTEXITCODE
}
if(-not $Agent -or -not $Workstation -or $RelayId -or $AgentBinary -or $Json -or $RelayTransport -or $RelayPort -ne 7452 -or $TailscaleBinary -or $NoAutostart -or $Live){
    Write-Error 'Direct MCP enrolment requires one agent selection and its Workstation URL.'
    exit 2
}
$arguments+=@('--agent',$Agent,'--workstation',$Workstation)
if($Name){$arguments+=@('--name',$Name)}
if($ProfileDir){$arguments+=@('--profile-dir',$ProfileDir)}
if($Check){$arguments+='--check'}
& $python @arguments
exit $LASTEXITCODE
