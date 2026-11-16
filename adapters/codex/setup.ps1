#requires -Version 5.1
<#
What:   Installs the Deep32 research skill and its five lens skills into a Codex home: copies skills\*, writes the
        Codex home's absolute path where the files say ${CODEX_HOME}, merges the Deep32 block into AGENTS.md
        between its markers (the rest of the file is never touched), and, when -WorkstationUrl is given, appends the
        deep32 MCP entry to config.toml if it has none. No token, key or address is stored by this script: the MCP
        token comes from the DEEP32_MCP_TOKEN environment variable, which pairing sets. Afterwards every installed
        skill file is compared with this kit by SHA-256. Files are written as UTF-8 without a byte-order mark.
Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1 [-CodexHome <folder>] [-WorkstationUrl https://...]
        -CodexHome defaults to %CODEX_HOME%, else %USERPROFILE%\.codex.
Exit:   0 installed and checked; 1 the post-install check failed; 2 bad arguments;
        3 refused: the target is this PC's Codex home (the CODEX_HOME folder or %USERPROFILE%\.codex) and the
          original Deep32 install is present.
#>
[CmdletBinding()]
param([string]$CodexHome='',[string]$WorkstationUrl='')
$ErrorActionPreference='Stop'
$kit=$PSScriptRoot
$utf8=New-Object System.Text.UTF8Encoding $false
function Read-Utf8([string]$Path){[IO.File]::ReadAllText($Path,$utf8)}
function Write-Utf8([string]$Path,[string]$Text){[IO.File]::WriteAllText($Path,$Text,$utf8)}
$realHome=if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $HOME '.codex'}
if(-not $CodexHome){$CodexHome=$realHome}
if(-not [IO.Path]::IsPathRooted($CodexHome)){Write-Output 'CodexHome must be an absolute folder.';exit 2}
$D=[IO.Path]::GetFullPath($CodexHome).TrimEnd('\')
if($WorkstationUrl -and ($WorkstationUrl -notmatch '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/mcp$')){Write-Output 'WorkstationUrl must look like https://<host>[:port]/mcp.';exit 2}
# The original install's markers (names only, nothing opened). They matter only when the target is the real Codex home.
$homes=@($env:CODEX_HOME,(Join-Path $HOME '.codex'),$(if($env:USERPROFILE){Join-Path $env:USERPROFILE '.codex'}))|Where-Object{$_}|ForEach-Object{[IO.Path]::GetFullPath($_).TrimEnd('\')}
if(@($homes|Where-Object{$_ -ieq $D}).Count){
    $markers=@(
        (Join-Path $env:LOCALAPPDATA 'Programs\Deep32\Deep32-package.json'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Deep32\state\private\relay')
    )|Where-Object{Test-Path -LiteralPath $_}
    if(@($markers).Count){Write-Output 'refused: this PC holds the original Deep32 install; the distribution never installs beside it.';exit 3}
}
$Dp=$D.Replace([string][char]92,'/')
function Get-Installed([string]$Text){$Text.Replace('${CODEX_HOME}',$Dp)}
$null=New-Item -ItemType Directory -Force (Join-Path $D 'skills')
$names=@(Get-ChildItem -LiteralPath (Join-Path $kit 'skills') -Directory|ForEach-Object Name)
foreach($n in $names){
    $dst=Join-Path $D ('skills\'+$n)
    if(Test-Path -LiteralPath $dst){Remove-Item -LiteralPath $dst -Recurse -Force}
    Copy-Item -LiteralPath (Join-Path $kit ('skills\'+$n)) -Destination (Join-Path $D 'skills') -Recurse
    Get-ChildItem -LiteralPath $dst -Recurse -File -Filter '*.md'|ForEach-Object{Write-Utf8 $_.FullName (Get-Installed (Read-Utf8 $_.FullName))}
}
# AGENTS.md: replace the block between the markers, or append it; never rewrite the rest of the file.
$block=(Get-Installed (Read-Utf8 (Join-Path $kit 'AGENTS.deep32.md'))).TrimEnd()+"`n"
$agents=Join-Path $D 'AGENTS.md'
$text=if(Test-Path -LiteralPath $agents){Read-Utf8 $agents}else{''}
$rx=New-Object Text.RegularExpressions.Regex('<!-- deep32:begin.*?<!-- deep32:end -->\r?\n?',[Text.RegularExpressions.RegexOptions]::Singleline)
if($rx.IsMatch($text)){$text=$rx.Replace($text,$block.Replace('$','$$'),1)}
else{$text=$(if($text -and -not $text.EndsWith("`n")){$text+"`n`n"}elseif($text){$text+"`n"}else{''})+$block}
Write-Utf8 $agents $text
$note=''
if($WorkstationUrl){
    $config=Join-Path $D 'config.toml'
    $current=if(Test-Path -LiteralPath $config){Read-Utf8 $config}else{''}
    if($current -match '(?m)^\[mcp_servers\.deep32\]'){$note='config.toml already has a deep32 entry: left as it is.'}
    else{
        $entry=(Read-Utf8 (Join-Path $kit 'config.deep32.toml')).Replace('{{WORKSTATION_MCP_URL}}',$WorkstationUrl)
        Write-Utf8 $config ($(if($current -and -not $current.EndsWith("`n")){$current+"`n"}else{$current})+"`n"+$entry)
    }
}else{$note='no -WorkstationUrl: config.toml not changed (pairing adds the deep32 entry).'}
# Post-install check: every installed skill file equals the kit's file after the path rewrite.
$sha=[Security.Cryptography.SHA256]::Create();$bad=@()
foreach($n in $names){
    Get-ChildItem -LiteralPath (Join-Path $kit ('skills\'+$n)) -Recurse -File|ForEach-Object{
        $rel=$_.FullName.Substring((Join-Path $kit 'skills').Length+1)
        $src=if($_.Extension -eq '.md'){$utf8.GetBytes((Get-Installed (Read-Utf8 $_.FullName)))}else{[IO.File]::ReadAllBytes($_.FullName)}
        $dstPath=Join-Path (Join-Path $D 'skills') $rel
        $dst=if(Test-Path -LiteralPath $dstPath){[IO.File]::ReadAllBytes($dstPath)}else{[byte[]]@()}
        if([BitConverter]::ToString($sha.ComputeHash($src)) -ne [BitConverter]::ToString($sha.ComputeHash($dst))){$bad+=$dstPath}
    }
}
if(([regex]::Matches((Read-Utf8 $agents),'<!-- deep32:begin')).Count -ne 1){$bad+=$agents}
if($bad.Count){Write-Output 'check FAILED, these differ from the kit:';$bad;exit 1}
if($note){Write-Output $note}
Write-Output ('done: '+$D+' ('+$names.Count+' skills installed and checked)')
exit 0
