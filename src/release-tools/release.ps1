<#
release.ps1: upload the files of a signed Deep32 release to GitHub, only when the exact
build has passed every gate and Jeffrey has cleared it in writing.

Release operator tooling (src/release-tools). Run by Jeffrey only, in his
own terminal. Agents run it only with -WhatIf, against a stand-in gh, in tests.

The repository is public. Nothing is uploaded unless all of these hold for the exact
build being released; they are checked in this order, before gh is even looked for:
  1. The manifest signature and every local part verify with the shipped verifier
     (install\verify-release.ps1 -NoJoin: gpgv against install\release-key.gpg
     and the fingerprint pinned in install\release-key.fingerprint).
  2. The manifest is complete (all three discs).
  3. The gate report (-GateReport: one gate report JSON, or a folder whose *.json files
     are all gate reports) was made for this exact build: its manifest_sha256 is this
     MANIFEST.json's and its tree_hash this build's tree hash (run_gates.py writes both
     when given --manifest and --export-report; a report without tree_hash is refused),
     its overall result is "pass", every gate it lists passed, and together the reports
     cover G1 to G7 and G9 to G15 (gate ids compared exactly). A G8 entry is not read:
     this script runs G8 itself, live (5).
  4. The export report (-ExportReport, written by source-export.py) is that of an
     export run (mode "export") that finished with status "ok", ran its gates
     (gates.run true) and they passed (gates.exit_code 0), and gives the build's tree hash.
  5. Gate G8, his go-file, passes now. This script runs
       python -I -B release-checks\run_gates.py --tree TREE --gates G8
              --manifest MANIFEST.json --export-report EXPORT_REPORT --report <temporary file>
     and refuses unless it exits 0 and its report shows G8 passed for this manifest and
     this tree. So there is one go-file rule, run_gates.py's (release review, 2026-11-14):
     src/release-tools\GO-<release version>.txt is a plain file (not a link, at most
     64 KiB) holding exactly one line "good to go: <SHA-256 of this MANIFEST.json>" and
     one line "tree: <tree hash of this build>", besides only blank lines and lines
     starting "#" (any other line, such as "not good to go yet", fails G8); the export
     report is that of a finished, gated export; and TREE (the exported folder) has that
     tree hash now. Jeffrey writes the go-file himself; an agent never does, and this
     script never does. When G8 fails, the script prints why, both values and how to
     write the file, and stops.
Then, and only then:
  6. Finds gh. If GitHub CLI is not installed it says how to install it and sign in, and
     stops.
  7. Reads each release's assets (gh release view TAG --json assets). Every release must
     already exist: he creates them on GitHub from his signed tags. This script never
     creates a release or a repository, never pushes code or tags, and never changes the
     repository's visibility.
  8. Shows the plan and asks him to type UPLOAD. Then uploads, one file per command, every
     file not already there with the same size, disc parts first and MANIFEST.json with
     MANIFEST.json.asc last:
       gh release upload TAG FILE --repo OWNER/NAME
     An asset of the same name with another size stops the run; nothing is overwritten.
  9. Reads the assets back and checks every name and size.

  -WhatIf: every check above runs (the gh read commands too); every upload is printed
  with "WHATIF, not run:" instead of being run. -GateRunner, -KeyDir and -Verifier are
  for -WhatIf tests only: a real upload always runs release-checks\run_gates.py, which
  reads the go-file from this script's folder, and verifies with the shipped verifier
  and key.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File release.ps1
      -Repo OWNER/NAME -ReleaseDir DIR -GateReport FILE_OR_FOLDER -ExportReport FILE
      [-Gh PATH]          default: gh on PATH
      [-Tree DIR]         the exported tree G8 checks; default: the export report's
                          out_resolved, else its out
      [-Python FILE]      Python 3.10 or later for run_gates.py; default:
                          %USERPROFILE%\.local\bin\python3.15.exe, else python3 or python
                          on PATH (never the WindowsApps stand-ins)
      [-WhatIf]
      [-GateRunner FILE]  -WhatIf only; default: ..\release-checks\run_gates.py (it reads the go-file
                          from the release folder beside its own gates folder)
      [-KeyDir DIR]       -WhatIf only; default: ..\..\install
      [-Verifier FILE]    -WhatIf only; default: ..\..\install\verify-release.ps1

Gate report (written by release-checks\run_gates.py with --manifest MANIFEST.json and
--export-report EXPORT_REPORT):
  {"format": "deep32-gate-report", "format_version": 1,
   "manifest_sha256": "<SHA-256 of the MANIFEST.json being released>",
   "tree_hash": "<tree hash of this build>", "result": "pass",
   "gates": [{"id": "G1", "result": "pass"}, ...]}

Exit codes:
  0  done; with -WhatIf: every check passed and the uploads were printed
  1  usage error
  2  an input is missing (folder, manifest, signature, verifier, key folder, gate or
     export report, the exported tree, the gate runner or Python), the gate runner
     stopped with a usage or configuration error, or an unexpected error stopped the
     run (its message is printed)
  3  refused: the gate reports do not show every gate passed on this exact build, or
     the export report is not that of a finished, gated export
  4  the manifest signature or a local part failed verification
  5  a gh command failed, or the assets read back after upload are not complete
  6  GitHub is not in the state needed: a release is missing, an asset has the same
     name but another size, or a carried disc is not there
  7  he did not confirm the upload
  8  the manifest is marked incomplete (fewer than three discs): not releasable
  9  refused: gate G8 did not pass (no go-file; a go-file that does not name this
     manifest and tree, or holds another line; or a tree that is not the exported one)
  10 GitHub CLI (gh) is not installed
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Repo,
    [string]$ReleaseDir,
    [string]$GateReport,
    [string]$ExportReport,
    [string]$Gh,
    [string]$Tree,
    [string]$Python,
    [string]$GateRunner,
    [string]$KeyDir,
    [string]$Verifier
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$DryRun = [bool]$WhatIfPreference
$WhatIfPreference = $false

# \z, not $: in .NET "$" also matches before a final newline.
$NameRx = '^[A-Za-z0-9][A-Za-z0-9._-]*\z'
$TagRx = '^[0-9A-Za-z][0-9A-Za-z._+-]*\z'
$VersionRx = '^[0-9A-Za-z][0-9A-Za-z.+-]*\z'
$ShaRx = '^[0-9a-f]{64}\z'
$RequiredGates = @('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G9', 'G10', 'G11', 'G12', 'G13', 'G14', 'G15')
$ObsoleteGates = @('G8')
$SigName = 'MANIFEST.json.asc'

function Say([string]$m) { Write-Host $m }
function Stop-Release([int]$code, [string]$msg) {
    [Console]::Error.WriteLine("REFUSED: $msg")
    Write-Host "RESULT: STOPPED (exit $code)"
    exit $code
}
function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    if ($obj -is [string] -or $obj -is [System.ValueType]) { return $null }
    $p = $obj.PSObject.Properties[$name]
    if ($null -eq $p) { return $null }
    return $p.Value
}
# Any error nobody caught ends the run with a known exit code, never half-way silently.
trap {
    [Console]::Error.WriteLine('REFUSED: unexpected error: ' + $_.Exception.Message)
    Write-Host 'RESULT: STOPPED (exit 2)'
    exit 2
}
function Show-Arg([string]$a) {
    if ($a -match '[\s"]') { return '"' + ($a -replace '"', '\"') + '"' }
    return $a
}
function Get-BytesSha256([byte[]]$b) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($b)) -replace '-', '').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Read-Json([string]$path) {
    # UTF-8 with or without a byte-order mark (Windows PowerShell 5.1 writes one).
    $t = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    return ($t | ConvertFrom-Json)
}

# Runs gh. Read commands always run; uploads are only printed under -WhatIf.
function Invoke-Gh([string[]]$GhArgs, [switch]$Changes, [switch]$AllowFail) {
    $line = 'gh ' + (($GhArgs | ForEach-Object { Show-Arg $_ }) -join ' ')
    if ($Changes -and $DryRun) {
        Say "WHATIF, not run: $line"
        return $null
    }
    Say "> $line"
    # gh's standard error is merged and split off here: in Windows PowerShell 5.1, when
    # the caller redirects error output (2>&1, *>), each stderr line of a program becomes
    # an error record, and with ErrorActionPreference Stop the first one would end the
    # script (a missing release is reported on stderr).
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $all = @(& $script:GhPath @GhArgs 2>&1)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $out = @()
    foreach ($o in $all) {
        if ($o -is [System.Management.Automation.ErrorRecord]) { [Console]::Error.WriteLine('  gh: ' + $o.Exception.Message) }
        else { $out += [string]$o }
    }
    if ($code -ne 0) {
        if ($AllowFail) { return $null }
        Stop-Release 5 "gh failed (exit $code): $line"
    }
    return ($out -join "`n")
}

# Every problem found in the gate reports, as text; none means they clear this build.
function Test-GateReports([string]$path, [string]$manifestSha, [string]$treeHash) {
    $files = @()
    if (Test-Path -LiteralPath $path -PathType Container) {
        $files = @(Get-ChildItem -LiteralPath $path -Filter '*.json' -File | Sort-Object Name | ForEach-Object { $_.FullName })
        if ($files.Count -eq 0) { return @("no gate report (*.json) in $path") }
    } elseif (Test-Path -LiteralPath $path -PathType Leaf) {
        $files = @((Resolve-Path -LiteralPath $path).ProviderPath)
    } else {
        return @("gate report not found: $path")
    }
    $problems = @()
    # Gate ids are compared exactly ("g1" is not G1): a PowerShell @{} ignores case.
    $passed = New-Object System.Collections.Hashtable ([System.StringComparer]::Ordinal)
    foreach ($f in $files) {
        $name = Split-Path -Leaf $f
        try { $g = Read-Json $f } catch { $problems += "${name}: not valid JSON"; continue }
        $gfv = Get-Prop $g 'format_version'
        if ((Get-Prop $g 'format') -cne 'deep32-gate-report' -or -not ($gfv -is [int] -or $gfv -is [long]) -or $gfv -ne 1) {
            $problems += "${name}: not a deep32-gate-report version 1"; continue
        }
        if ((Get-Prop $g 'manifest_sha256') -cne $manifestSha) {
            $problems += "${name}: made for another manifest (its manifest_sha256 is not $manifestSha)"
        }
        $th = Get-Prop $g 'tree_hash'
        if (-not ($th -is [string])) { $problems += "${name}: names no tree_hash (run the gates with --export-report of this build)" }
        elseif ($th -cne $treeHash) { $problems += "${name}: made for another tree (tree_hash is not $treeHash)" }
        if ((Get-Prop $g 'result') -cne 'pass') { $problems += ("${name}: overall result is '{0}', not pass" -f (Get-Prop $g 'result')) }
        $entries = @(Get-Prop $g 'gates')
        if ($entries.Count -eq 0 -or $null -eq $entries[0]) { $problems += "${name}: lists no gates"; continue }
        foreach ($e in $entries) {
            $id = Get-Prop $e 'id'
            if (-not ($id -is [string])) { $problems += "${name}: a gate entry has no id"; continue }
            if ($ObsoleteGates -ccontains $id) { Say "  ${name}: $id is not read (this script runs G8 itself, live, through run_gates.py)"; continue }
            $res = Get-Prop $e 'result'
            if ($res -cne 'pass') { $problems += "${name}: gate $id did not pass (result: '$res')"; continue }
            $passed[$id] = $true
        }
    }
    foreach ($id in $RequiredGates) {
        if (-not $passed.ContainsKey($id)) { $problems += "gate $id has no passing result in the gate reports" }
    }
    return $problems
}

# ---- arguments ------------------------------------------------------------
if (-not $Repo -or $Repo -notmatch '^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$') { Stop-Release 1 '-Repo OWNER/NAME is required' }
if (-not $ReleaseDir) { Stop-Release 1 '-ReleaseDir is required' }
if (-not $GateReport) { Stop-Release 1 '-GateReport is required (a gate report JSON, or a folder of them)' }
if (-not $ExportReport) { Stop-Release 1 '-ExportReport is required (the export report of this build)' }
if (-not $DryRun -and ($GateRunner -or $KeyDir -or $Verifier)) {
    Stop-Release 1 '-GateRunner, -KeyDir and -Verifier are for -WhatIf tests only; a real upload uses release-checks\run_gates.py (it reads the go-file beside this script) and the shipped verifier and key'
}
if (-not (Test-Path -LiteralPath $ReleaseDir -PathType Container)) { Stop-Release 2 "release folder not found: $ReleaseDir" }
$ReleaseDir = (Resolve-Path -LiteralPath $ReleaseDir).ProviderPath
$here = $PSScriptRoot
if (-not $GateRunner) { $GateRunner = Join-Path $here '..\release-checks\run_gates.py' }
if (-not $KeyDir) { $KeyDir = Join-Path $here '..\..\install' }
if (-not $Verifier) { $Verifier = Join-Path $here '..\..\install\verify-release.ps1' }
$manifestPath = Join-Path $ReleaseDir 'MANIFEST.json'
$sigPath = Join-Path $ReleaseDir $SigName
foreach ($pair in @(@('manifest', $manifestPath), @('signature', $sigPath), @('verifier', $Verifier), @('export report', $ExportReport), @('gate runner', $GateRunner))) {
    if (-not (Test-Path -LiteralPath $pair[1] -PathType Leaf)) { Stop-Release 2 ("{0} not found: {1}" -f $pair[0], $pair[1]) }
}
if (-not (Test-Path -LiteralPath $KeyDir -PathType Container)) { Stop-Release 2 "key folder not found: $KeyDir" }
if (-not (Test-Path -LiteralPath $GateReport)) { Stop-Release 2 "gate report not found: $GateReport" }
# Full paths: .NET file calls resolve a relative path against the process folder, which
# is not PowerShell's current location when the script is run with .\release.ps1.
$Verifier = (Resolve-Path -LiteralPath $Verifier).ProviderPath
$KeyDir = (Resolve-Path -LiteralPath $KeyDir).ProviderPath
$GateRunner = (Resolve-Path -LiteralPath $GateRunner).ProviderPath
# The folder G8 reads the go-file from: <the gate runner's export folder>\release (for the real runner, this
# script's own folder). Only used to print where the file goes.
$GoDir = Join-Path (Split-Path -Parent (Split-Path -Parent $GateRunner)) 'release'
$GateReport = (Resolve-Path -LiteralPath $GateReport).ProviderPath
$ExportReport = (Resolve-Path -LiteralPath $ExportReport).ProviderPath
if ($DryRun) { Say 'MODE: -WhatIf (every check runs; nothing on GitHub is changed)' }

$manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
$manifestSha = Get-BytesSha256 $manifestBytes
Say "manifest: $manifestPath (sha256 $manifestSha)"

# ---- 1. signature and local parts -----------------------------------------
Say ''
Say 'step 1: manifest signature and local parts (shipped verifier)'
try { $m = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($manifestBytes) | ConvertFrom-Json } catch { Stop-Release 4 'MANIFEST.json is not valid JSON' }
$discs = @(Get-Prop $m 'discs')
$localRoles = @()
foreach ($d in $discs) {
    $names = @(@(Get-Prop $d 'parts') | ForEach-Object { Get-Prop $_ 'name' })
    $present = @($names | Where-Object { $_ -is [string] -and $_ -match $NameRx -and (Test-Path -LiteralPath (Join-Path $ReleaseDir $_) -PathType Leaf) })
    if ($present.Count -gt 0) { $localRoles += [string](Get-Prop $d 'role') }
}
$vargs = @{ PartsDir = $ReleaseDir; KeyDir = $KeyDir; Manifest = $manifestPath; Signature = $sigPath }
if ($localRoles.Count -gt 0) { $vargs['NoJoin'] = $true; $vargs['Role'] = $localRoles } else { $vargs['SignatureOnly'] = $true }
Say ("> verify-release.ps1 -PartsDir {0} -KeyDir {1} {2}" -f (Show-Arg $ReleaseDir), (Show-Arg $KeyDir), ($(if ($localRoles.Count -gt 0) { '-NoJoin -Role ' + ($localRoles -join ',') } else { '-SignatureOnly' })))
& $Verifier @vargs
$vcode = $LASTEXITCODE
if ($vcode -ne 0) { Stop-Release 4 "verification failed (verify-release exit $vcode)" }
$again = [System.IO.File]::ReadAllBytes($manifestPath)
if ((Get-BytesSha256 $again) -cne $manifestSha) { Stop-Release 4 'MANIFEST.json changed while it was being verified' }

# From here the manifest is trusted.
$rel = Get-Prop $m 'release'
$version = [string](Get-Prop $rel 'version')
$codeTag = [string](Get-Prop $rel 'tag')
if ($version -notmatch $VersionRx) { Stop-Release 4 'manifest release.version is not usable' }
if ($codeTag -notmatch $TagRx) { Stop-Release 4 'manifest release.tag is not a usable tag' }
Say ("  release {0} ({1}), {2} disc(s), signer {3}" -f $version, $codeTag, $discs.Count, (Get-Prop (Get-Prop $m 'signer') 'fingerprint'))

# ---- 2. complete ------------------------------------------------------------
if ((Get-Prop $rel 'complete') -ne $true) { Stop-Release 8 'the manifest is marked incomplete (fewer than three discs; development only): it is not released' }

# ---- 3 and 4. gate reports and export report for this build ----------------
Say ''
Say 'step 2: export report and gate reports for this exact build'
try { $er = Read-Json $ExportReport } catch { Stop-Release 3 "export report is not valid JSON: $ExportReport" }
$treeHash = Get-Prop $er 'tree_hash'
$erProblems = @()
if ((Get-Prop (Get-Prop $er 'tool') 'name') -cne 'run-export') { $erProblems += 'it was not written by run-export' }
if ((Get-Prop $er 'mode') -cne 'export') { $erProblems += ("its mode is '{0}', not export (a --check run does not make the tree)" -f (Get-Prop $er 'mode')) }
if ((Get-Prop $er 'status') -cne 'ok') { $erProblems += ("its status is '{0}', not ok" -f (Get-Prop $er 'status')) }
if (-not ($treeHash -is [string]) -or $treeHash -cnotmatch $ShaRx) { $erProblems += 'it has no tree_hash'; $treeHash = '' }
$erGates = Get-Prop $er 'gates'
$erRun = Get-Prop $erGates 'run'
$erGateCode = Get-Prop $erGates 'exit_code'
if (-not ($erRun -is [bool] -and $erRun)) { $erProblems += 'its export ran without gates, or it records no gate run (--no-gates is for lab tests only)' }
elseif (-not ($erGateCode -is [int] -or $erGateCode -is [long]) -or $erGateCode -ne 0) { $erProblems += "its gates did not pass (gates.exit_code '$erGateCode')" }
if ($erProblems.Count -gt 0) {
    foreach ($p in $erProblems) { [Console]::Error.WriteLine("  - export report: $p") }
    Stop-Release 3 "the export report is not that of a finished, gated export: $ExportReport"
}
Say "  export report: status ok, tree hash $treeHash"
$problems = @(Test-GateReports $GateReport $manifestSha $treeHash | Where-Object { $_ })
if ($problems.Count -gt 0) {
    foreach ($p in $problems) { [Console]::Error.WriteLine("  - $p") }
    Stop-Release 3 'the gate reports do not show every gate passed on this exact build'
}
Say ("  gate reports: every gate passed on this manifest ({0})" -f ($RequiredGates -join ', '))

# ---- 5. his go-file: gate G8, run now through run_gates.py --------------------
Say ''
Say 'step 3: the go-file (gate G8, run now)'
$goPath = Join-Path $GoDir "GO-$version.txt"
$goLine = "good to go: $manifestSha"
$treeLine = "tree: $treeHash"
# The exported tree G8 compares with the export report's tree hash.
if (-not $Tree) {
    foreach ($k in @('out_resolved', 'out')) {
        $v = Get-Prop $er $k
        if (-not $Tree -and $v -is [string] -and $v) { $Tree = $v }
    }
}
if (-not $Tree) { Stop-Release 2 'the export report names no output folder (out_resolved or out): give -Tree' }
if (-not (Test-Path -LiteralPath $Tree -PathType Container)) { Stop-Release 2 "exported tree not found: $Tree" }
$Tree = (Resolve-Path -LiteralPath $Tree).ProviderPath
# Python for the gate runner: -Python, else the usual places (never a WindowsApps stand-in, which opens the Store).
if ($Python) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { Stop-Release 2 "Python not found: $Python" }
    $Python = (Resolve-Path -LiteralPath $Python).ProviderPath
} else {
    $cand = @()
    if ($env:USERPROFILE) { $cand += (Join-Path $env:USERPROFILE '.local\bin\python3.15.exe') }
    foreach ($n in @('python3.exe', 'python.exe')) {
        $cand += @(Get-Command $n -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Path } |
                   Where-Object { $_ -and $_ -notmatch '\\WindowsApps\\' })
    }
    foreach ($c in $cand) { if (-not $Python -and (Test-Path -LiteralPath $c -PathType Leaf)) { $Python = $c } }
    if (-not $Python) { Stop-Release 2 'Python 3.10 or later was not found: give -Python' }
}
$g8Report = Join-Path ([System.IO.Path]::GetTempPath()) ('deep32-release-g8-' + [guid]::NewGuid().ToString('N') + '.json')
$g8Args = @('-I', '-B', $GateRunner, '--tree', $Tree, '--gates', 'G8', '--manifest', $manifestPath,
            '--export-report', $ExportReport, '--report', $g8Report)
Say ('> ' + (Show-Arg $Python) + ' ' + (($g8Args | ForEach-Object { Show-Arg $_ }) -join ' '))
$g8 = $null
$g8Code = $null
try {
    # The runner's standard error is merged and split off here, as for gh (Windows PowerShell 5.1 turns
    # each stderr line into an error record when output is redirected).
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $g8Out = @(& $Python @g8Args 2>&1)
        $g8Code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    foreach ($o in $g8Out) {
        if ($o -is [System.Management.Automation.ErrorRecord]) { [Console]::Error.WriteLine('  run_gates: ' + $o.Exception.Message) }
        else { Say ('  | ' + [string]$o) }
    }
    if (Test-Path -LiteralPath $g8Report -PathType Leaf) {
        try { $g8 = Read-Json $g8Report } catch { $g8 = $null }
    }
} finally {
    if (Test-Path -LiteralPath $g8Report) { Remove-Item -LiteralPath $g8Report -Force -ErrorAction SilentlyContinue }
}
if ($null -eq $g8 -or $g8Code -eq 2 -or ($g8Code -ne 0 -and $g8Code -ne 1)) {
    Stop-Release 2 "the gate runner did not run G8 to its end (exit $g8Code); see its output above"
}
$g8Problems = @()
$g8Entries = @(Get-Prop $g8 'gates')
$g8Gate = $null
if ($g8Entries.Count -eq 1 -and (Get-Prop $g8Entries[0] 'id') -ceq 'G8') { $g8Gate = $g8Entries[0] }
if ((Get-Prop $g8 'format') -cne 'deep32-gate-report') { $g8Problems += 'its report is not a deep32 gate report' }
if ($null -eq $g8Gate) { $g8Problems += 'its report does not hold exactly the gate G8' }
elseif ((Get-Prop $g8Gate 'result') -cne 'pass') {
    foreach ($f in @(Get-Prop $g8Gate 'findings')) {
        if ($null -ne $f) { $g8Problems += ('G8 {0}: {1}' -f (Get-Prop $f 'check'), (Get-Prop $f 'context')) }
    }
    if ($g8Problems.Count -eq 0) { $g8Problems += ("G8 result '{0}'" -f (Get-Prop $g8Gate 'result')) }
}
if ((Get-Prop $g8 'result') -cne 'pass') { $g8Problems += ("overall result '{0}'" -f (Get-Prop $g8 'result')) }
foreach ($p in @(Get-Prop $g8 'problems')) { if ($p -is [string] -and $p) { $g8Problems += $p } }
if ((Get-Prop $g8 'manifest_sha256') -cne $manifestSha) { $g8Problems += 'its report names another manifest' }
if ((Get-Prop $g8 'tree_hash') -cne $treeHash) { $g8Problems += 'its report names another tree' }
if ($g8Code -ne 0 -and $g8Problems.Count -eq 0) { $g8Problems += "run_gates.py exit $g8Code" }
if ($g8Problems.Count -gt 0) {
    foreach ($p in ($g8Problems | Select-Object -Unique)) { [Console]::Error.WriteLine("  - $p") }
    Say ''
    Say 'This build is not cleared. Only Jeffrey clears a build, by writing the go-file himself'
    Say 'in a terminal of his own (an agent never writes it; this script never writes it).'
    Say 'First compare the two values with the build: the manifest hash with'
    Say "  (Get-FileHash -Algorithm SHA256 `"$manifestPath`").Hash.ToLower()"
    Say "and the tree hash with the export report $ExportReport."
    Say 'Then, if he is satisfied that this build is good to go (notes may follow on lines starting #;'
    Say 'any other line fails G8):'
    Say ("  Set-Content -Encoding ascii -LiteralPath `"{0}`" -Value '{1}', '{2}'" -f $goPath, $goLine, $treeLine)
    Stop-Release 9 'gate G8 did not pass: no go-file from Jeffrey names this exact build'
}
Say "  G8 passed: $goPath names this manifest and this tree"

# ---- 6. gh ------------------------------------------------------------------
Say ''
Say 'step 4: GitHub CLI'
if ($Gh) {
    if (-not (Test-Path -LiteralPath $Gh -PathType Leaf)) { Stop-Release 10 "gh not found: $Gh" }
    $script:GhPath = (Resolve-Path -LiteralPath $Gh).ProviderPath
} else {
    $c = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) {
        Say 'GitHub CLI (gh) is not installed. Install it and sign in, in a terminal of your own:'
        Say '  winget install --id GitHub.cli -e'
        Say '  (close and reopen the terminal so that gh is on PATH)'
        Say '  gh auth login     (GitHub.com, HTTPS, sign in with the browser)'
        Say '  gh auth status    (shows the account it signed in as)'
        Say 'Then run this script again.'
        Stop-Release 10 'gh (GitHub CLI) is not installed'
    }
    $script:GhPath = $c.Path
}
Say "  gh: $($script:GhPath)"

$plan = New-Object System.Collections.ArrayList
$remote = New-Object System.Collections.ArrayList
foreach ($d in $discs) {
    $tag = [string](Get-Prop $d 'tag')
    if ($tag -notmatch $TagRx) { Stop-Release 4 "disc $($d.role) has an unusable tag" }
    foreach ($p in @(Get-Prop $d 'parts')) {
        $item = New-Object psobject -Property @{ Tag = $tag; Name = [string]$p.name; Size = [long]$p.size; Path = (Join-Path $ReleaseDir $p.name) }
        if ($localRoles -contains $d.role) { [void]$plan.Add($item) } else { [void]$remote.Add($item) }
    }
}
foreach ($n in @('MANIFEST.json', $SigName)) {
    $pp = Join-Path $ReleaseDir $n
    [void]$plan.Add((New-Object psobject -Property @{ Tag = $codeTag; Name = $n; Size = [long](Get-Item -LiteralPath $pp).Length; Path = $pp }))
}

# ---- 7. what is on GitHub already ------------------------------------------
Say ''
Say 'step 5: releases and assets on GitHub'
$tags = New-Object System.Collections.ArrayList
foreach ($i in @($plan) + @($remote)) { if (-not $tags.Contains($i.Tag)) { [void]$tags.Add($i.Tag) } }
$assets = @{}
foreach ($t in $tags) {
    $j = Invoke-Gh @('release', 'view', $t, '--repo', $Repo, '--json', 'assets') -AllowFail
    if ($null -eq $j) {
        Stop-Release 6 ("release $t does not exist on $Repo. Create it yourself on GitHub from your signed tag " +
            "(for example: gh release create $t --repo $Repo --draft --verify-tag --title $t), then run this again")
    }
    $map = @{}
    try { $obj = $j | ConvertFrom-Json } catch { Stop-Release 5 "gh returned unreadable JSON for release $t" }
    foreach ($a in @(Get-Prop $obj 'assets')) {
        $an = Get-Prop $a 'name'
        if ($an -is [string]) { $map[$an] = [long](Get-Prop $a 'size') }
    }
    $assets[$t] = $map
    Say ("  release {0}: {1} asset(s) already there" -f $t, $map.Count)
}
foreach ($r in $remote) {
    $map = $assets[$r.Tag]
    if (-not $map.ContainsKey($r.Name)) { Stop-Release 6 "carried disc part $($r.Name) is not in release $($r.Tag) and is not in $ReleaseDir" }
    if ($map[$r.Name] -ne $r.Size) { Stop-Release 6 "carried disc part $($r.Name) in release $($r.Tag) has size $($map[$r.Name]), the manifest says $($r.Size)" }
}
if ($remote.Count -gt 0) { Say "  carried parts already on GitHub with the right sizes: $($remote.Count)" }
$uploads = @()
foreach ($i in $plan) {
    $map = $assets[$i.Tag]
    if ($map.ContainsKey($i.Name)) {
        if ($map[$i.Name] -eq $i.Size) { Say "  already uploaded: $($i.Tag)/$($i.Name)"; continue }
        Stop-Release 6 "$($i.Tag)/$($i.Name) is on GitHub with size $($map[$i.Name]), the local file has $($i.Size); delete it on GitHub by hand if it is wrong"
    }
    $uploads += $i
}
$isLast = { param($u) $u.Tag -eq $codeTag -and ($u.Name -eq 'MANIFEST.json' -or $u.Name -eq $SigName) }
$ordered = @(@($uploads | Where-Object { -not (& $isLast $_) }) + @($uploads | Where-Object { & $isLast $_ }))

# ---- 8. upload --------------------------------------------------------------
Say ''
Say ("step 6: {0} file(s) to upload to {1}" -f $ordered.Count, $Repo)
if (-not $DryRun -and $ordered.Count -gt 0) {
    $answer = $null
    try { $answer = Read-Host "Type UPLOAD to upload these files to the releases of $Repo on GitHub" } catch { $answer = $null }
    if ($answer -cne 'UPLOAD') { Stop-Release 7 'not confirmed; nothing was changed' }
}
foreach ($u in $ordered) {
    [void](Invoke-Gh @('release', 'upload', $u.Tag, $u.Path, '--repo', $Repo) -Changes)
}

# ---- 9. read back ------------------------------------------------------------
if ($DryRun) {
    Say ''
    Say 'RESULT: OK (-WhatIf: every check passed; the uploads above were printed, not run)'
    exit 0
}
Say ''
Say 'step 7: reading the assets back'
foreach ($t in $tags) {
    $j = Invoke-Gh @('release', 'view', $t, '--repo', $Repo, '--json', 'assets')
    $map = @{}
    foreach ($a in @(Get-Prop ($j | ConvertFrom-Json) 'assets')) { $map[[string](Get-Prop $a 'name')] = [long](Get-Prop $a 'size') }
    foreach ($i in @(@($plan) + @($remote) | Where-Object { $_.Tag -eq $t })) {
        if (-not $map.ContainsKey($i.Name) -or $map[$i.Name] -ne $i.Size) { Stop-Release 5 "after upload, $t/$($i.Name) is missing or has the wrong size" }
    }
    Say "  release ${t}: complete"
}
Say ''
Say 'Every file is on GitHub. Publishing a draft release is yours, on GitHub.'
Say 'RESULT: OK'
exit 0
