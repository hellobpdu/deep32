param([Parameter(Mandatory=$true)][string]$Tools, [Parameter(Mandatory=$true)][string]$Scratch, [string]$SplitMediaRoot, [string]$Python)
# Every operating-system gateway is synthetic. No Docker, mount or distribution runs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $Tools 'deep32-get.ps1')
$script:events = New-Object 'Collections.Generic.List[string]'
$script:attached=$true
$script:reject=$false
function Get-Command { param([string]$Name, [string]$CommandType, [string]$ErrorAction); return @{ Name = $Name } }
function Get-Deep32PlainPath { param([string]$Path, [switch]$Directory, [switch]$MayNotExist); $script:events.Add('path:'+$Path); return $Path }
function Get-DiskImage { param([string]$ImagePath, [string]$ErrorAction); return [pscustomobject]@{Attached=$script:attached} }
function Mount-DiskImage { param([string]$ImagePath, [string]$Access, [switch]$PassThru, [string]$ErrorAction); if($Access -cne 'ReadOnly'){throw 'Writable mount refused.'};$script:events.Add('read-only-mount');return [pscustomobject]@{Attached=$true} }
function Dismount-DiskImage { param([string]$ImagePath,[string]$ErrorAction);$script:events.Add('dismount') }
function Get-Volume { param([Parameter(ValueFromPipeline=$true)]$InputObject); process { return [pscustomobject]@{DriveLetter=$Scratch.Substring(0,1)} } }
function Test-Deep32InstallPrerequisites { param([string]$Journey); if ($Journey -cne 'J1') { throw 'Wrong prerequisite selection.' }; $script:events.Add('prerequisites-pass');if($script:reject){throw 'Synthetic prerequisite refusal.'} }
function Invoke-Deep32DockerLoader {
    param([string[]]$Arguments)
    if($Arguments[0] -notlike '*:\forms\provision.py'){throw 'Wrong shared provisioner.'}
    foreach($pair in @(@('--form','docker'),@('--platform','windows'),@('--docker-role','full'),@('--settings',(Join-Path $Scratch 'settings.json')),@('--content',(Join-Path $Scratch 'content')),@('--data-root',(Join-Path $Scratch 'data')))) {
        $index=[Array]::IndexOf($Arguments,$pair[0]);if($index -lt 0 -or $Arguments[$index+1] -cne $pair[1]){throw 'Loader argument mismatch.'}
    }
    if(-not $script:events.Contains('prerequisites-pass')){throw 'Loader ran before prerequisites.'}
    $script:events.Add('loader')
}
$plan = @{ path = (Join-Path $Scratch 'synthetic-plan.json'); directory=$Scratch; accepted = ('a' * 64);
    plan = [pscustomobject]@{ install_id = ('b' * 24); identity = [pscustomobject]@{journey='J1';docker_role='full';content=(Join-Path $Scratch 'content');data_root=(Join-Path $Scratch 'data')} } }
foreach($attached in @($true,$false)) {
    $script:attached=$attached;$script:events.Clear()
    Invoke-Deep32Install -Image (Join-Path $Scratch 'synthetic-disc.iso') -Destination $Scratch -SettingsPlan $plan
    if (@($script:events | Where-Object { $_ -ceq 'loader' }).Count -ne 1) { throw 'Expected exactly one loader call.' }
    if ($script:events.Contains('dismount')) { throw 'Full-role model media was detached.' }
    if(-not $attached -and -not $script:events.Contains('read-only-mount')){throw 'Missing read-only attachment.'}
}
$script:events.Clear();$script:reject=$true
try {Invoke-Deep32Install -Image 'synthetic' -Destination $Scratch -SettingsPlan $plan;throw 'Prerequisite refusal ignored.'}
catch {if($_.Exception.Message -cne 'Synthetic prerequisite refusal.'){throw}}
if(-not $script:events.Contains('dismount') -or $script:events.Contains('loader')){throw 'Failed prerequisite did not clean up its own attachment.'}
$script:events.Clear()
try { Invoke-Deep32Install -Image 'synthetic' -Destination $Scratch; throw 'Missing settings accepted.' }
catch { if ($_.Exception.Message -cnotmatch '^Validated settings') { throw } }
if ($script:events.Count -ne 0) { throw 'Missing settings reached a host gateway.' }
$stage = Join-Path $Scratch ('synthetic-settings-stage-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($stage)
foreach ($name in @('plan.json', 'settings.json', 'settings-deployment.json', 'deployment.json')) {
    [IO.File]::WriteAllText((Join-Path $stage $name), 'SYNTHETIC NON-SECRET SETTINGS FIXTURE', [Text.Encoding]::ASCII)
}
Remove-Deep32SettingsStage -Directory $stage
if ([IO.Directory]::Exists($stage)) { throw 'Protected settings staging was retained after cleanup.' }
Write-Output "PASS: PowerShell $($PSVersionTable.PSVersion) shared Docker provisioner, protected settings path, final prerequisite, one loader, retained model media and owned failure cleanup."

if (-not $SplitMediaRoot) { return }

# Use real settings, media-cache and join helpers with synthetic native host commands.
$script:Deep32PythonExecutable = $Python
$script:fixturePrograms = Join-Path $Scratch 'synthetic-programs'
[void][IO.Directory]::CreateDirectory((Join-Path $script:fixturePrograms 'Tailscale'))
[IO.File]::WriteAllText((Join-Path $script:fixturePrograms 'Tailscale\tailscale.exe'), 'synthetic executable name')
$script:planRunner = @'
import json, os, runpy, subprocess, sys
from pathlib import Path
os.environ['ProgramFiles'] = sys.argv[1]
def local_tailnet(argv, **options):
    assert argv == [expected_exe, 'status', '--json']
    status = {'BackendState': 'Running', 'CurrentTailnet': {'MagicDNSSuffix': 'synthetic-tailnet.ts.net'},
              'Self': {'DNSName': 'forge.synthetic-tailnet.ts.net.', 'Online': True}}
    return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(status).encode('ascii'))
subprocess.run = local_tailnet
expected_exe = str(Path(sys.argv[1]) / 'Tailscale' / 'tailscale.exe')
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name='__main__')
'@
function Invoke-Deep32Settings {
    param([string[]]$Arguments)
    $helper = Join-Path $Tools 'deep32_settings.py'
    if ($Arguments[0] -ceq 'plan') { & $Python -I -B -c $script:planRunner $script:fixturePrograms $helper @Arguments }
    else { & $Python -I -B $helper @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic settings handoff refused.' }
}
$script:realMediaOpen = ${function:Open-Deep32VerifiedMedia}
$script:realIsoJoin = ${function:Invoke-Deep32WindowsIsoJoin}
$script:splitFailure = ''
$script:joinWorkspace = ''
$script:splitMediaRoot = $SplitMediaRoot
function Open-Deep32VerifiedMedia {
    param([string]$Path, [string]$Source, [long]$Size, [string]$Hash)
    $script:events.Add('verify:' + [IO.Path]::GetFileNameWithoutExtension($Path))
    & $script:realMediaOpen -Path $Path -Source $Source -Size $Size -Hash $Hash
}
function Assert-FixtureMediaLocks([bool]$locked) {
    foreach($record in $mediaRecords) {
        $blocked=$false
        try { $writer=[IO.File]::Open($record.image,'Open','Write','ReadWrite'); $writer.Dispose() }
        catch {
            $err=$_.Exception
            while($err.InnerException){$err=$err.InnerException}
            if(($err.HResult -band 0xffff) -ne 32){throw}
            $blocked=$true
        }
        if($blocked -ne $locked){throw 'Verified media handle lifetime differs.'}
    }
}
function Get-DiskImage { throw 'Raw split install reached disk image inspection.' }
function Mount-DiskImage { throw 'Raw split install reached disk image mounting.' }
function Dismount-DiskImage { throw 'Raw split install reached disk image dismounting.' }
function Invoke-Deep32WindowsIsoJoin {
    param([string[]]$Arguments)
    Assert-FixtureMediaLocks $true
    foreach ($entry in @(@('--core',$mediaRecords[0].image),@('--embedder',$mediaRecords[1].image),
            @('--ingestion',$mediaRecords[2].image))) {
        if ($Arguments[[Array]::IndexOf($Arguments, $entry[0]) + 1] -cne $entry[1]) { throw 'Wrong raw ISO argument.' }
    }
    $script:joinWorkspace = $Arguments[[Array]::IndexOf($Arguments, '--windows-workspace') + 1]
    $acl = if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::GetAccessControl([IO.DirectoryInfo]::new($script:joinWorkspace))
    } else { [IO.Directory]::GetAccessControl($script:joinWorkspace) }
    $rules = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $identities = @($rules | ForEach-Object { $_.IdentityReference.Value })
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2 -or
            $identities -notcontains $owner -or $identities -notcontains 'S-1-5-18') {
        throw 'Raw workspace ACL does not belong exclusively to its owner and SYSTEM.'
    }
    foreach ($rule in $rules) {
        if ($rule.InheritanceFlags -ne 'ContainerInherit, ObjectInherit') { throw 'Raw workspace rules do not cover child entries.' }
    }
    $script:events.Add('raw-join')
    if ($script:splitFailure -ceq 'raw-join') { throw 'Synthetic raw reader failure.' }
    & $script:realIsoJoin -Arguments $Arguments
}
function Test-Deep32InstallPrerequisites {
    param([string]$Journey,[string]$InternalForgeEvidence)
    if ($Journey -cne 'J1') { throw 'Wrong split prerequisite selection.' }
    if ($InternalForgeEvidence -cne (Join-Path $splitPlan.directory 'forge-evidence.json')) { throw 'The evaluator did not get private evidence.' }
    $proof = ConvertFrom-Json ([IO.File]::ReadAllText($InternalForgeEvidence))
    if ($proof.prerequisites.'gpu.driver'.version -cne '810.2' -or $proof.storage_plan[0].free_bytes -ne 9000) {
        throw 'The evaluator did not get the exact signed observations.'
    }
    $script:events.Add('prerequisites')
    if ($script:splitFailure -ceq 'prerequisites' -and $script:events.Contains('raw-join')) { throw 'Synthetic final refusal.' }
}
function Test-Deep32SignedForgePrerequisites {
    param([hashtable]$Evidence, [hashtable]$SettingsPlan)
    if ($Evidence.source -cne $Scratch -or $Evidence.gpu_class -cne 'turing' -or
            $SettingsPlan.accepted -cne $splitPlan.accepted -or $SettingsPlan.path -cne $splitPlan.path -or
            $SettingsPlan.plan.identity.tailnet_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'The selected release or consent changed before signed preflight.'
    }
    $script:events.Add('signed')
    $final = $script:events.Contains('raw-join')
    if ($script:splitFailure -ceq 'first-signed' -and -not $final) { throw 'Synthetic early signed refusal.' }
    if ($script:splitFailure -ceq 'second-signed' -and $final) { throw 'Synthetic final signed refusal.' }
    if ($script:splitFailure -ceq 'malformed-signed') { return 'unexpected stdout' }
    return '{"prerequisites":{"gpu.driver":{"state":"met","version":"810.2"}},"storage_plan":[{"filesystem_id":"test","needed_bytes":8000,"free_bytes":9000}]}'
}
function Invoke-Deep32DockerLoader {
    param([string[]]$Arguments)
    Assert-FixtureMediaLocks $true
    $expectedKit = Join-Path $script:joinWorkspace 'kit'
    if ($Arguments[0] -cne (Join-Path $expectedKit 'forms\provision.py')) { throw 'Unexpected split provisioner.' }
    foreach ($pair in @(@('--kit', $expectedKit), @('--form', 'docker'), @('--platform', 'windows'),
            @('--docker-role', 'forge'), @('--data-root', $splitOptions.data_root),
            @('--settings', (Join-Path $splitPlan.directory 'settings.json')))) {
        $index = [Array]::IndexOf($Arguments, $pair[0])
        if ($index -lt 0 -or $Arguments[$index+1] -cne $pair[1]) { throw 'Split loader argument mismatch.' }
    }
    $payload = $Arguments[[Array]::IndexOf($Arguments, '--payload') + 1]
    $manifest = Join-Path ([IO.Path]::GetDirectoryName($payload)) 'PAYLOAD.json'
    if (-not [IO.File]::Exists($manifest) -or $payload -like "$script:splitMediaRoot*") { throw 'Loader did not receive the durable joined payload.' }
    $roles = @(ConvertFrom-Json ([IO.File]::ReadAllText((Join-Path $payload 'images\index.json'))) |
        ForEach-Object { $_.role })
    if (($roles -join ',') -cne 'builder,converter,door,qdrant,reranker,tei-turing') { throw 'Incomplete joined payload.' }
    $script:events.Add('loader')
    if ($script:splitFailure -ceq 'loader') { throw 'Synthetic loader failure.' }
}
function Assert-SplitEvents {
    param([string[]]$Expected)
    $observed = @($script:events | Where-Object { $_ -cnotlike 'path:*' })
    if (($observed -join ',') -cne ($Expected -join ',')) {
        throw ('Split install order or cleanup differs. Observed ' + ($observed -join ','))
    }
    Assert-FixtureMediaLocks $false
    if ($script:joinWorkspace -and [IO.Directory]::Exists($script:joinWorkspace)) { throw 'Raw ISO workspace was retained.' }
    if ($splitPlan -and [IO.File]::Exists((Join-Path $splitPlan.directory 'forge-evidence.json'))) { throw 'Evidence retained after evaluation.' }
}

$fixtureIso = @'
import hashlib, io, pathlib, sys, tarfile
source, output = map(pathlib.Path, sys.argv[1:])
files = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
if source.name == 'core':
    files.setdefault('forms/provision.py', b'# synthetic fixture only\n')
files.pop('SHA256SUMS', None)
files['SHA256SUMS'] = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                               for name, data in sorted(files.items())).encode('ascii')
with tarfile.open(output, 'w', format=tarfile.PAX_FORMAT) as archive:
    for name, data in sorted(files.items()):
        member = tarfile.TarInfo(name)
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
'@
$mediaRecords = foreach ($name in @('core', 'embedder-turing', 'ingestion')) {
    $image = Join-Path $Scratch ($name + '.iso')
    & $Python -I -B -c $fixtureIso (Join-Path $SplitMediaRoot $name) $image
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the synthetic raw media fixture.' }
    @{ component = $name; image = $image; disc = @{ size = ([IO.FileInfo]::new($image)).Length; sha256 = (Get-Deep32Hash $image) } }
}
$choices = Join-Path $Scratch 'synthetic-choices.json'
[IO.File]::WriteAllText($choices, '{"format":"deep32-settings","format_version":1,"role":"forge","node_name":"deep32-forge","user_name":"deep32","time_zone":"Etc/UTC","keyboard":"us","profile":"two-machines","transport":"tailnet","publish":"serve-https","forge_accept_ct":true}', [Text.Encoding]::ASCII)
$splitOptions = @{ journey = 'J1'; form = 'docker'; platform = 'windows'; docker_role = 'forge'
    data_root = (Join-Path $Scratch 'synthetic-installed-forge'); docker_storage_path = (Join-Path $Scratch 'synthetic-backing')
    settings_arguments = @('--settings', $choices); confirm_tailnet = 'synthetic-tailnet.ts.net'
    desktop_isolation_confirmed = $true; non_interactive = $true; accept_plan = ('0' * 64) }
[void][IO.Directory]::CreateDirectory($splitOptions.docker_storage_path)
$previewArgs = @('plan', '--journey', 'J1', '--form', 'docker', '--platform', 'windows',
    '--docker-role', 'forge', '--data-root', $splitOptions.data_root, '--destination', $Scratch,
    '--media-sha256', $mediaRecords[0].disc.sha256, '--settings', $choices, '--confirm-tailnet', $splitOptions.confirm_tailnet,
    '--docker-storage-path', $splitOptions.docker_storage_path, '--confirm-desktop-isolation')
foreach ($record in $mediaRecords) {
    $previewArgs += @('--media-component', $record.component, [string]$record.disc.size, $record.disc.sha256)
}
$preview = ConvertFrom-Json ((Invoke-Deep32Settings -Arguments $previewArgs) -join "`n")
$script:events.Clear()
$splitPlan = $null
try {
    [void](New-Deep32SettingsPlan -ImageHash $mediaRecords[0].disc.sha256 -Destination $Scratch -Options $splitOptions -MediaComponents $mediaRecords)
    throw 'Wrong split plan digest was accepted.'
} catch { if ($_.Exception.Message -cnotlike 'Plan consent required*') { throw } }
Assert-SplitEvents @()
$splitOptions.accept_plan = $preview.digest
$splitPlan = New-Deep32SettingsPlan -ImageHash $mediaRecords[0].disc.sha256 -Destination $Scratch -Options $splitOptions -MediaComponents $mediaRecords
$splitEvidence = @{ source = $Scratch; gpu_class = 'turing'; key = 'synthetic-key'; fingerprint = ('A' * 40); gpg = '' }
try {
    $preflight = @('signed', 'prerequisites')
    $verification = @('verify:core', 'verify:embedder-turing', 'verify:ingestion')
    $joined = $verification + 'raw-join'
    $script:events.Clear()
    $script:splitFailure = 'first-signed'
    try {
        Invoke-Deep32SplitInstall -MediaComponents $mediaRecords -SettingsPlan $splitPlan -Evidence $splitEvidence
        throw 'Early signed preflight refusal was ignored.'
    } catch { if ($_.Exception.Message -cne 'Synthetic early signed refusal.') { throw } }
    Assert-SplitEvents @('signed')
    if ([IO.Directory]::Exists($splitOptions.data_root + '.deep32-media')) { throw 'Early refusal created the media cache.' }

    $script:events.Clear()
    $script:splitFailure = 'malformed-signed'
    try {
        Invoke-Deep32SplitInstall -MediaComponents $mediaRecords -SettingsPlan $splitPlan -Evidence $splitEvidence
        throw 'Malformed signed observations reached the inventory.'
    } catch { if ($_.Exception.Message -cnotlike '*returned invalid observations*') { throw } }
    Assert-SplitEvents @('signed')

    $script:events.Clear()
    $script:splitFailure = ''
    Invoke-Deep32SplitInstall -MediaComponents $mediaRecords -SettingsPlan $splitPlan -Evidence $splitEvidence
    Assert-SplitEvents ($preflight + $joined + $preflight + 'loader')

    foreach ($stageFailure in @('raw-join', 'prerequisites', 'second-signed', 'loader')) {
        $script:events.Clear()
        $script:splitFailure = $stageFailure
        $message = switch ($stageFailure) {
            'raw-join' { 'Synthetic raw reader failure.' }
            prerequisites { 'Synthetic final refusal.' }
            'second-signed' { 'Synthetic final signed refusal.' }
            loader { 'Synthetic loader failure.' }
        }
        try {
            Invoke-Deep32SplitInstall -MediaComponents $mediaRecords -SettingsPlan $splitPlan -Evidence $splitEvidence
            throw 'Split fixture refusal was ignored.'
        } catch { if ($_.Exception.Message -cne $message) { throw } }
        $expected = switch ($stageFailure) {
            'raw-join' { $joined }
            prerequisites { $joined + $preflight }
            'second-signed' { $joined + 'signed' }
            default { $joined + $preflight + 'loader' }
        }
        Assert-SplitEvents ($preflight + $expected)
    }
    $script:events.Clear()
    $script:splitFailure = ''
    [IO.File]::AppendAllText($mediaRecords[2].image, 'tampered')
    try {
        Invoke-Deep32SplitInstall -MediaComponents $mediaRecords -SettingsPlan $splitPlan -Evidence $splitEvidence
        throw 'Tampered split media reached the raw reader.'
    } catch { if ($_.Exception.Message -ceq 'Tampered split media reached the raw reader.') { throw } }
    Assert-SplitEvents ($preflight + $verification)
} finally {
    Remove-Deep32SettingsStage -Directory $splitPlan.directory
}
if ([IO.Directory]::Exists($splitPlan.directory)) { throw 'Split plan staging was retained.' }
Write-Output 'PASS: PowerShell real raw-media join, bound consent, held verified ISO handles, and cleanup on every tested path.'
