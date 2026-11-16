<#
verify-release.ps1: check a Deep32 release before anything is installed from it.

What it does, in order (contract C2, docs/contracts/C2-release-manifest-draft.md):
  1. Checks the manifest's OpenPGP signature with gpgv, against the release key that
     ships beside this script (release-key.gpg) and nothing else, exactly:
       gpgv --homedir <new empty private folder> --keyring <full path of release-key.gpg>
           --status-fd 1 MANIFEST.json.asc MANIFEST.json
     The manifest and its signature are read once and copied into a private folder;
     gpgv checks those copies and the same manifest bytes are then parsed. Your own
     GnuPG keyring is never read. The signature is accepted only when gpgv succeeds,
     reports exactly one GOODSIG and one VALIDSIG and nothing bad, VALIDSIG names a
     binary signature (class 00: over the exact bytes; a text-mode signature, class 01,
     is refused), and the last field of VALIDSIG (the fingerprint of the signing key's
     primary key) equals the fingerprint pinned in release-key.fingerprint. The
     manifest must name that same key (signer.fingerprint). Nothing in the manifest is
     used before this step passes.
  2. Checks the size and SHA-256 of every part of every disc.
  3. Joins each disc's parts in order and checks the whole-disc size and SHA-256 of
     the joined file. A disc that fails is deleted, never left half-joined.
  gpgv is looked for in this order: -Gpgv when given; gpgv on PATH; Git for Windows
  (C:\Program Files\Git\usr\bin\gpgv.exe); Gpg4win (C:\Program Files\GnuPG\bin\gpgv.exe,
  then C:\Program Files (x86)\GnuPG\bin\gpgv.exe). None found: exit 3, never a pass.
  A gpgv built for MSYS2 (Git for Windows) or Cygwin is given /c/... paths, a native
  one C:/... paths. There is no unsigned mode. Runs on Windows PowerShell 5.1 or later.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File verify-release.ps1 -PartsDir DIR
      [-KeyDir DIR]         folder holding release-key.gpg and release-key.fingerprint
                            (default: this script's own folder, install\ of the repository)
      [-Manifest FILE]      default DIR\MANIFEST.json
      [-Signature FILE]     default: the manifest path with .asc added
      [-OutDir DIR]         where joined discs are written (default DIR)
      [-Role system,content,gpu]   only these discs
      [-NoJoin]             check the signature and the parts only
      [-SignatureOnly]      check the signature and the manifest's form only
      [-Gpgv PATH]          the gpgv to use
      [-Quiet]              print only failures and the final line
  Test hook: when the environment variable DEEP32_GPGV_CANDIDATES is set, its list
  (paths separated by "|") replaces the three fixed gpgv locations above.

Exit codes (the same in verify-release.sh):
  0  every check passed (and every disc was joined, unless told not to join)
  1  usage error
  2  an input file or folder is missing or unreadable, or release-key.fingerprint is
     malformed
  3  gpgv was not found or could not be started
  4  the signature is not good, is not by the pinned release key, or the manifest
     names another signer than the key that signed it
  5  the manifest is not usable: not JSON, unknown format or version, unsafe names,
     or a requested role is not in it
  6  a part is missing or has the wrong size
  7  a part has the wrong SHA-256
  8  a joined disc has the wrong size or SHA-256 (it is deleted)
  9  a joined disc or the private work folder could not be written
  10 (verify-release.sh only) another needed tool is missing: a SHA-256 tool
  When several checks fail, the code is that of the first failure found; every part
  is still checked so that all bad parts are listed.
#>
[CmdletBinding()]
param(
    [string]$PartsDir,
    [string]$KeyDir,
    [string]$Manifest,
    [string]$Signature,
    [string]$OutDir,
    [string[]]$Role,
    [switch]$NoJoin,
    [switch]$SignatureOnly,
    [string]$Gpgv,
    [switch]$Quiet
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$FORMAT = 'deep32-release-manifest'
$FORMAT_VERSION = 1
# \z, not $: in .NET "$" also matches before a final newline.
$NameRx = '^[A-Za-z0-9][A-Za-z0-9._-]*\z'
$HashRx = '^[0-9a-f]{64}\z'
$FprRx = '^[0-9A-F]{40}\z'
$GpgvPlaces = @('C:\Program Files\Git\usr\bin\gpgv.exe', 'C:\Program Files\GnuPG\bin\gpgv.exe', 'C:\Program Files (x86)\GnuPG\bin\gpgv.exe')
$script:Joining = $null
$script:Work = $null
$script:GpgvFlavour = 'native'

function Say([string]$m) { if (-not $Quiet) { Write-Host $m } }
function Warn([string]$m) { [Console]::Error.WriteLine($m) }
function Finish([int]$code, [string]$msg) {
    if ($script:Joining) { Remove-Item -LiteralPath $script:Joining -Force -ErrorAction SilentlyContinue }
    if ($script:Work) { Remove-Item -LiteralPath $script:Work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($code -ne 0) {
        Warn "FAIL: $msg"
        Write-Host "RESULT: FAIL (exit $code)"
    } elseif ($msg) {
        Write-Host $msg
    }
    exit $code
}

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    if ($obj -is [string] -or $obj -is [System.ValueType]) { return $null }
    $p = $obj.PSObject.Properties[$name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Test-Int($v) { return (($v -is [int]) -or ($v -is [long])) }

function Quote-Arg([string]$a) {
    if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $bs = 0
    foreach ($ch in $a.ToCharArray()) {
        if ($ch -eq '\') { $bs++; continue }
        if ($ch -eq '"') { [void]$sb.Append('\' * ($bs * 2 + 1)); [void]$sb.Append('"'); $bs = 0; continue }
        if ($bs -gt 0) { [void]$sb.Append('\' * $bs); $bs = 0 }
        [void]$sb.Append($ch)
    }
    if ($bs -gt 0) { [void]$sb.Append('\' * ($bs * 2)) }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Find-Gpgv {
    if ($Gpgv) {
        if (Test-Path -LiteralPath $Gpgv -PathType Leaf) { return (Resolve-Path -LiteralPath $Gpgv).ProviderPath }
        $c = Get-Command $Gpgv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return $c.Path }
        return $null
    }
    $c = Get-Command gpgv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Path }
    $places = $GpgvPlaces
    if ($env:DEEP32_GPGV_CANDIDATES) { $places = @($env:DEEP32_GPGV_CANDIDATES -split '\|' | Where-Object { $_ }) }
    foreach ($p in $places) {
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    return $null
}

# A gpgv built for MSYS2 (Git for Windows) or Cygwin reads "C:/x" as a key resource URL
# and refuses it: it is given /c/x (or /cygdrive/c/x). A native gpgv (Gpg4win) is given
# C:/x. Forward slashes either way: gpgv takes a keyring name without "/" as a file in
# its home folder.
function Get-GpgvFlavour([string]$exe) {
    $dir = Split-Path -Parent $exe
    if (Test-Path -LiteralPath (Join-Path $dir 'msys-2.0.dll') -PathType Leaf) { return 'msys' }
    if (Test-Path -LiteralPath (Join-Path $dir 'cygwin1.dll') -PathType Leaf) { return 'cygwin' }
    return 'native'
}
function Convert-GpgvPath([string]$p) {
    $f = $p -replace '\\', '/'
    if ($script:GpgvFlavour -ne 'native' -and $f -match '^([A-Za-z]):/(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        if ($script:GpgvFlavour -eq 'cygwin') { return "/cygdrive/$drive/" + $Matches[2] }
        return "/$drive/" + $Matches[2]
    }
    return $f
}

# gpgv's status lines go to standard output (--status-fd 1), its messages to standard error.
function Invoke-Gpgv([string[]]$GArgs) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $script:GpgvPath
    $psi.Arguments = (($GArgs | ForEach-Object { Quote-Arg $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $outTask = $p.StandardOutput.ReadToEndAsync()
    $errTask = $p.StandardError.ReadToEndAsync()
    try { $p.StandardInput.Close() } catch { }
    $p.WaitForExit()
    $r = New-Object psobject
    $r | Add-Member -NotePropertyName Code -NotePropertyValue $p.ExitCode
    $r | Add-Member -NotePropertyName Out -NotePropertyValue $outTask.Result
    $r | Add-Member -NotePropertyName Err -NotePropertyValue $errTask.Result
    return $r
}

function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# release-key.fingerprint: line 1 the 40 hex digits; line 2, if there, the same spaced.
function Read-Pin([string]$path) {
    try { $lines = @([System.IO.File]::ReadAllLines($path) | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' -and -not $_.StartsWith('#') }) }
    catch { Finish 2 "release-key.fingerprint unreadable: $path" }
    if ($lines.Count -lt 1 -or $lines[0] -cnotmatch $FprRx) { Finish 2 "the first line of $path must be the 40 upper-case hex digits of the release key's fingerprint" }
    if ($lines.Count -gt 2 -or ($lines.Count -eq 2 -and ($lines[1] -replace ' ', '') -cne $lines[0])) {
        Finish 2 "$path`: the second line must be the same fingerprint, spaced, and nothing more"
    }
    return $lines[0]
}

try {
    # ---- arguments ----------------------------------------------------------
    if (-not $PartsDir) { Finish 1 '-PartsDir is required (see the header of this script)' }
    $wantRoles = @()
    foreach ($r in @($Role)) {
        if (-not $r) { continue }
        foreach ($x in ($r -split ',')) {
            $x = $x.Trim()
            if ($x -eq '') { continue }
            if (@('system', 'content', 'gpu') -notcontains $x) { Finish 1 "-Role must be system, content or gpu, not '$x'" }
            $wantRoles += $x
        }
    }
    if (-not $KeyDir) { $KeyDir = $PSScriptRoot }
    if (-not (Test-Path -LiteralPath $PartsDir -PathType Container)) { Finish 2 "parts folder not found: $PartsDir" }
    $PartsDir = (Resolve-Path -LiteralPath $PartsDir).ProviderPath
    if (-not (Test-Path -LiteralPath $KeyDir -PathType Container)) { Finish 2 "key folder not found: $KeyDir" }
    $KeyDir = (Resolve-Path -LiteralPath $KeyDir).ProviderPath
    if (-not $Manifest) { $Manifest = Join-Path $PartsDir 'MANIFEST.json' }
    if (-not $Signature) { $Signature = "$Manifest.asc" }
    if (-not $OutDir) { $OutDir = $PartsDir }
    $keyring = Join-Path $KeyDir 'release-key.gpg'
    $pinFile = Join-Path $KeyDir 'release-key.fingerprint'
    foreach ($pair in @(@('manifest', $Manifest), @('signature', $Signature), @('release key', $keyring), @('release key fingerprint', $pinFile))) {
        if (-not (Test-Path -LiteralPath $pair[1] -PathType Leaf)) { Finish 2 ("{0} not found: {1}" -f $pair[0], $pair[1]) }
    }
    $Manifest = (Resolve-Path -LiteralPath $Manifest).ProviderPath
    $Signature = (Resolve-Path -LiteralPath $Signature).ProviderPath
    $keyring = (Resolve-Path -LiteralPath $keyring).ProviderPath
    $pinFile = (Resolve-Path -LiteralPath $pinFile).ProviderPath
    if (-not $NoJoin -and -not $SignatureOnly) {
        if (-not (Test-Path -LiteralPath $OutDir -PathType Container)) { Finish 2 "output folder not found: $OutDir" }
        $OutDir = (Resolve-Path -LiteralPath $OutDir).ProviderPath
    }
    $pin = Read-Pin $pinFile

    $script:GpgvPath = Find-Gpgv
    if (-not $script:GpgvPath) {
        Finish 3 'gpgv not found. It comes with Git for Windows (C:\Program Files\Git\usr\bin\gpgv.exe) and with Gpg4win; install one of them, or give -Gpgv with the path of a gpgv. On macOS: brew install gnupg. On Linux: the gpgv package.'
    }

    # ---- 1. signature -------------------------------------------------------
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Manifest)
        $sigBytes = [System.IO.File]::ReadAllBytes($Signature)
    } catch { Finish 2 "manifest or signature unreadable: $Manifest" }
    try {
        $script:Work = Join-Path ([System.IO.Path]::GetTempPath()) ('deep32-verify-' + [guid]::NewGuid().ToString('N'))
        [void](New-Item -ItemType Directory -Path $script:Work)
        [void](New-Item -ItemType Directory -Path (Join-Path $script:Work 'gnupg'))
        [System.IO.File]::WriteAllBytes((Join-Path $script:Work 'MANIFEST.json'), $bytes)
        [System.IO.File]::WriteAllBytes((Join-Path $script:Work 'MANIFEST.json.asc'), $sigBytes)
    } catch { Finish 9 "could not make a private work folder: $($_.Exception.Message)" }
    $script:GpgvFlavour = Get-GpgvFlavour $script:GpgvPath
    $gargs = @('--homedir', (Convert-GpgvPath (Join-Path $script:Work 'gnupg')), '--keyring', (Convert-GpgvPath $keyring),
               '--status-fd', '1', (Convert-GpgvPath (Join-Path $script:Work 'MANIFEST.json.asc')),
               (Convert-GpgvPath (Join-Path $script:Work 'MANIFEST.json')))
    Say "signature: gpgv --keyring $keyring --status-fd 1 $Signature $Manifest"
    Say "  using $($script:GpgvPath) ($($script:GpgvFlavour) build); pinned release key $pin"
    try { $v = Invoke-Gpgv $gargs } catch { Finish 3 "gpgv could not be started ($($script:GpgvPath)): $($_.Exception.Message)" }
    $status = @($v.Out -split "`r?`n" | Where-Object { $_.StartsWith('[GNUPG:] ') } | ForEach-Object { $_.Substring(9).Trim() })
    $good = @($status | Where-Object { $_ -match '^GOODSIG( |$)' })
    $valid = @($status | Where-Object { $_ -match '^VALIDSIG( |$)' })
    $bad = @($status | Where-Object { $_ -match '^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY)( |$)' })
    if ($v.Code -ne 0 -or $good.Count -ne 1 -or $valid.Count -ne 1 -or $bad.Count -gt 0) {
        foreach ($l in @($v.Err -split "`r?`n" | Where-Object { $_.Trim() -ne '' })) { Warn "  gpgv: $l" }
        foreach ($l in $bad) { Warn "  status: $l" }
        if (@($bad | Where-Object { $_ -match '^EXPKEYSIG' }).Count -gt 0) {
            Finish 4 'the signature was made by the release key, but the release key here has expired: take the current install\release-key.gpg from the repository (its expiry is extended before it lapses) and verify again'
        }
        if (@($bad | Where-Object { $_ -match '^(NO_PUBKEY|ERRSIG)' }).Count -gt 0) {
            Finish 4 'the manifest was signed by a key that is not the shipped release key'
        }
        if (@($bad | Where-Object { $_ -match '^BADSIG' }).Count -gt 0) {
            Finish 4 'the manifest signature is BAD: the manifest or its signature has been changed'
        }
        if ($good.Count -gt 1 -or $valid.Count -gt 1) { Finish 4 'the signature file holds more than one signature; a release has exactly one' }
        Finish 4 "the manifest signature is NOT good (gpgv exit $($v.Code))"
    }
    $vf = @($valid[0] -split '\s+')
    $signedBy = $vf[$vf.Count - 1].ToUpperInvariant()
    if ($vf.Count -lt 11 -or $signedBy -cne $pin) {
        Finish 4 "the manifest was signed by key $signedBy, not by the pinned release key $pin"
    }
    # VALIDSIG fields: fpr date timestamp expiry version reserved pk-algo hash-algo CLASS primary-fpr
    if ($vf[9] -cne '00') {
        Finish 4 "the signature has class $($vf[9]), not 00: only a binary detached signature (gpg --detach-sign, without --textmode) covers the exact bytes of the manifest"
    }
    Say "  good signature by the pinned release key $pin"

    # ---- read the manifest (the signed bytes only) ---------------------------
    try {
        $text = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
        $m = $text | ConvertFrom-Json
    } catch { Finish 5 'manifest is not valid JSON' }
    if ((Get-Prop $m 'format') -ne $FORMAT) { Finish 5 "manifest format is not $FORMAT" }
    $fv = Get-Prop $m 'format_version'
    if (-not (Test-Int $fv) -or $fv -ne $FORMAT_VERSION) { Finish 5 "manifest format_version '$fv' is not supported (this verifier reads $FORMAT_VERSION)" }
    $signer = Get-Prop $m 'signer'
    $sfpr = Get-Prop $signer 'fingerprint'
    if ((Get-Prop $signer 'type') -cne 'openpgp' -or -not ($sfpr -is [string]) -or $sfpr -cnotmatch $FprRx) {
        Finish 5 'manifest signer is not {"type": "openpgp", "fingerprint": <40 upper-case hex digits>}'
    }
    if ($sfpr -cne $pin) { Finish 4 "the manifest names signer $sfpr but was signed by the release key $pin" }
    $rel = Get-Prop $m 'release'
    Say ("manifest: release {0} ({1}), created {2}, signer {3}" -f (Get-Prop $rel 'version'), (Get-Prop $rel 'tag'), (Get-Prop $rel 'created'), $sfpr)
    if ((Get-Prop $rel 'complete') -ne $true) { Warn 'WARNING: this manifest is marked incomplete (fewer than three discs)' }

    $discs = @(Get-Prop $m 'discs')
    if ($discs.Count -lt 1 -or $null -eq $discs[0]) { Finish 5 'manifest lists no discs' }
    foreach ($d in $discs) {
        $dr = Get-Prop $d 'role'
        if (@('system', 'content', 'gpu') -notcontains $dr) { Finish 5 'manifest has a disc with an unknown role' }
        $df = Get-Prop $d 'file'; $ds = Get-Prop $d 'size'; $dh = Get-Prop $d 'sha256'
        $parts = @(Get-Prop $d 'parts')
        if (-not ($df -is [string]) -or $df -notmatch $NameRx -or -not (Test-Int $ds) -or $ds -lt 1 -or
            -not ($dh -is [string]) -or $dh -cnotmatch $HashRx -or $parts.Count -lt 1 -or $null -eq $parts[0]) {
            Finish 5 "manifest disc $dr has an unsafe or malformed entry"
        }
        foreach ($p in $parts) {
            $pn = Get-Prop $p 'name'; $ps = Get-Prop $p 'size'; $ph = Get-Prop $p 'sha256'
            if (-not ($pn -is [string]) -or $pn -notmatch $NameRx -or -not (Test-Int $ps) -or
                -not ($ph -is [string]) -or $ph -cnotmatch $HashRx) {
                Finish 5 "manifest disc $dr has an unsafe or malformed part"
            }
        }
    }
    foreach ($r in $wantRoles) {
        if (-not ($discs | Where-Object { (Get-Prop $_ 'role') -eq $r })) { Finish 5 "role $r is not in this manifest" }
    }
    if ($wantRoles.Count -gt 0) { $discs = @($discs | Where-Object { $wantRoles -contains (Get-Prop $_ 'role') }) }
    if ($SignatureOnly) { Finish 0 'RESULT: OK (signature and manifest form; parts not checked)' }

    # ---- 2. parts -------------------------------------------------------------
    $firstFail = 0
    $okParts = 0
    foreach ($d in $discs) {
        $parts = @(Get-Prop $d 'parts')
        Say ("disc {0}: {1}, {2} bytes, {3} part(s)" -f $d.role, $d.file, $d.size, $parts.Count)
        foreach ($p in $parts) {
            $path = Join-Path $PartsDir $p.name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                if ($firstFail -eq 0) { $firstFail = 6 }
                Warn "FAIL: part missing: $($p.name)"; continue
            }
            $len = (Get-Item -LiteralPath $path).Length
            if ($len -ne [long]$p.size) {
                if ($firstFail -eq 0) { $firstFail = 6 }
                Warn "FAIL: part $($p.name) is $len bytes, the manifest says $($p.size)"; continue
            }
            $h = Get-Sha256 $path
            if ($h -cne $p.sha256) {
                if ($firstFail -eq 0) { $firstFail = 7 }
                Warn "FAIL: part $($p.name) has SHA-256 $h, the manifest says $($p.sha256)"; continue
            }
            $okParts++
            Say "  ok  $($p.name)"
        }
    }
    if ($firstFail -ne 0) { Finish $firstFail 'one or more parts are bad; nothing was joined' }
    Say "parts: all $okParts good"

    if ($NoJoin) { Finish 0 "RESULT: OK (signature and $okParts parts; not joined)" }

    # ---- 3. join and check each disc ----------------------------------------
    $joined = 0
    $buf = New-Object byte[] (8MB)
    foreach ($d in $discs) {
        $target = Join-Path $OutDir $d.file
        $partial = "$target.partial"
        $script:Joining = $partial
        Say "join $($d.role) -> $target"
        $out = $null
        try {
            $out = [System.IO.File]::Open($partial, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            foreach ($p in @($d.parts)) {
                $in = [System.IO.File]::OpenRead((Join-Path $PartsDir $p.name))
                try {
                    while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) { $out.Write($buf, 0, $n) }
                } finally { $in.Dispose() }
            }
            $out.Flush()
        } catch {
            if ($out) { $out.Dispose() }
            Finish 9 "write failed while joining $($d.file): $($_.Exception.Message)"
        }
        $out.Dispose()
        $len = (Get-Item -LiteralPath $partial).Length
        if ($len -ne [long]$d.size) { Finish 8 "joined $($d.file) is $len bytes, the manifest says $($d.size)" }
        $h = Get-Sha256 $partial
        if ($h -cne $d.sha256) { Finish 8 "joined $($d.file) has SHA-256 $h, the manifest says $($d.sha256)" }
        try { Move-Item -LiteralPath $partial -Destination $target -Force } catch { Finish 9 "could not rename the joined disc to $target" }
        $script:Joining = $null
        $joined++
        Say "  ok  $($d.file) sha256 $h"
    }
    Finish 0 "RESULT: OK (signature, $okParts parts, $joined disc(s) joined and checked)"
} catch {
    Finish 2 ("unexpected error: " + $_.Exception.Message)
}
