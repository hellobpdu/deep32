# PowerShell 5.1 or later. Download public Deep32 release assets without an account.
# Exit codes: 0 success, 1 refusal or failure, 3 selected disc pending.
$script:Deep32ReleaseBase = 'https://github.com/hellobpdu/deep32/releases/download'
$script:Deep32ScriptDirectory = $PSScriptRoot

function Assert-Deep32Name {
    param([object]$Name)
    if ($Name -isnot [string] -or $Name.Length -gt 200 -or
        $Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $Name.Contains('..') -or
        $Name.EndsWith('.') -or $Name -imatch '^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)') {
        throw 'A release file name is unsafe.'
    }
}

function Get-Deep32PlainPath {
    param([string]$Path, [switch]$Directory, [switch]$MayNotExist)
    $full = [IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        try {
            $attributes = [IO.File]::GetAttributes($cursor)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Links and reparse points are refused.'
            }
        } catch [IO.FileNotFoundException] {
        } catch [IO.DirectoryNotFoundException] {
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $exists = [IO.File]::Exists($full) -or [IO.Directory]::Exists($full)
    if (-not $exists) {
        if ($MayNotExist) { return $full }
        throw "A required path is missing: $full"
    }
    $isDirectory = ([IO.File]::GetAttributes($full) -band [IO.FileAttributes]::Directory) -ne 0
    if ($isDirectory -ne [bool]$Directory) { throw "Unexpected path type: $full" }
    return $full
}

function New-Deep32Directory {
    param([string]$Path)
    $full = Get-Deep32PlainPath -Path $Path -Directory -MayNotExist
    [void][IO.Directory]::CreateDirectory($full)
    return Get-Deep32PlainPath -Path $full -Directory
}

function Get-Deep32Hash {
    param([string]$Path)
    $full = Get-Deep32PlainPath -Path $Path
    $stream = $null
    $digest = $null
    try {
        $stream = [IO.File]::Open($full, 'Open', 'Read', 'Read')
        $digest = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($digest.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $digest) { $digest.Dispose() }
    }
}

function Assert-Deep32File {
    param([string]$Path, [long]$Size, [string]$Hash)
    [void](Get-Deep32PlainPath -Path $Path)
    if (([IO.FileInfo]$Path).Length -ne $Size -or (Get-Deep32Hash $Path) -cne $Hash) {
        throw "A local file is corrupt; remove this file to retry: $Path"
    }
}

function Copy-Deep32Stream {
    param([IO.Stream]$InputStream, [IO.Stream]$OutputStream, [long]$Limit)
    $buffer = New-Object byte[] 1048576
    [long]$copied = 0
    while ($copied -lt $Limit) {
        $want = [int][Math]::Min([long]$buffer.Length, $Limit - $copied)
        $count = $InputStream.Read($buffer, 0, $want)
        if ($count -eq 0) { break }
        $OutputStream.Write($buffer, 0, $count)
        $copied += $count
    }
    return $copied
}

function Move-Deep32NewFile {
    param([string]$Source, [string]$Destination)
    [void](Get-Deep32PlainPath -Path $Source)
    [void](Get-Deep32PlainPath -Path $Destination -MayNotExist)
    if ([IO.File]::Exists($Destination)) { throw "Refusing to replace: $Destination" }
    [IO.File]::Move($Source, $Destination)
}

function New-Deep32RangeUri {
    param([string]$Uri, [long]$Offset)
    # Windows PowerShell 5.1 uses HttpWebRequest, whose Range header needs
    # AddRange. Its public request factory permits a process-local adapter.
    # Each adapter matches only one unique URI fragment; HTTP omits fragments.
    if ($null -eq ('Deep32.ReleaseRangeCreator' -as [type])) {
        try {
            Add-Type -Language CSharp -ErrorAction Stop -TypeDefinition @'
using System;
using System.Net;
namespace Deep32 {
    public sealed class ReleaseRangeCreator : IWebRequestCreate {
        private readonly long offset;
        public ReleaseRangeCreator(long start) { offset = start; }
        public WebRequest Create(Uri uri) {
            HttpWebRequest request = WebRequest.CreateHttp(uri);
            request.AddRange(offset);
            return request;
        }
    }
}
'@
        } catch {
            throw 'Range resume needs standard .NET compilation in PowerShell 5.1. Use PowerShell 7 or remove the partial download to restart at byte zero.'
        }
    }
    $builder = New-Object UriBuilder $Uri
    $builder.Fragment = 'deep32-range-' + [Guid]::NewGuid().ToString('N')
    $rangeUri = $builder.Uri
    $creator = New-Object Deep32.ReleaseRangeCreator -ArgumentList $Offset
    if (-not [Net.WebRequest]::RegisterPrefix($rangeUri.AbsoluteUri, $creator)) {
        throw 'The process-local range transport could not be registered.'
    }
    return $rangeUri.AbsoluteUri
}

function Invoke-Deep32Request {
    param([string]$Uri, [string]$OutFile, [hashtable]$Headers)
    $requestUri = $Uri
    $requestHeaders = @{}
    foreach ($name in $Headers.Keys) { $requestHeaders[$name] = $Headers[$name] }
    if ($PSVersionTable.PSVersion.Major -le 5 -and $requestHeaders.ContainsKey('Range')) {
        if ([string]$requestHeaders['Range'] -cnotmatch '^bytes=([0-9]+)-$') { throw 'An internal range request is invalid.' }
        $requestUri = New-Deep32RangeUri -Uri $Uri -Offset ([long]$Matches[1])
        $requestHeaders.Remove('Range')
    }
    return Invoke-WebRequest -Uri $requestUri -UseBasicParsing -OutFile $OutFile -PassThru -Headers $requestHeaders -ErrorAction Stop
}

function Invoke-Deep32Fetch {
    param([string]$Name, [string]$Target, [long]$ExpectedSize = -1, [switch]$Metadata)
    Assert-Deep32Name $Name
    [void](Get-Deep32PlainPath -Path $Target -MayNotExist)
    [long]$offset = 0
    if ([IO.File]::Exists($Target)) { $offset = ([IO.FileInfo]$Target).Length }
    if ($Metadata -and $offset -ne 0) { throw 'A metadata transfer must start at zero.' }
    if ($ExpectedSize -ge 0 -and $offset -gt $ExpectedSize) { throw "A partial download is too large: $Target" }
    if ($ExpectedSize -ge 0 -and $offset -eq $ExpectedSize) { return }
    if ($script:Deep32Source) {
        $source = Get-Deep32PlainPath -Path (Join-Path $script:Deep32Source $Name)
        [long]$size = ([IO.FileInfo]$source).Length
        if (($ExpectedSize -ge 0 -and $size -ne $ExpectedSize) -or ($Metadata -and $size -gt 16777216)) {
            throw "The source file has an unexpected size: $Name"
        }
        $inputStream = $null
        $outputStream = $null
        try {
            $inputStream = [IO.File]::Open($source, 'Open', 'Read', 'Read')
            [void]$inputStream.Seek($offset, 'Begin')
            $outputStream = [IO.File]::Open($Target, 'OpenOrCreate', 'Write', 'None')
            [void]$outputStream.Seek($offset, 'Begin')
            $copied = Copy-Deep32Stream $inputStream $outputStream ($size - $offset)
            $outputStream.Flush()
            if ($copied -ne $size - $offset) { throw "The source file was interrupted: $Name" }
        } finally {
            if ($null -ne $inputStream) { $inputStream.Dispose() }
            if ($null -ne $outputStream) { $outputStream.Dispose() }
        }
        return
    }
    if (-not (Get-Command Invoke-WebRequest -ErrorAction SilentlyContinue)) {
        throw 'Invoke-WebRequest is missing. Run this script in Windows PowerShell 5.1 or later.'
    }
    # A range response is separate until its status and range are checked. Never
    # append an ignored Range response to the previous partial download.
    $responseFile = $Target + '.response'
    [void](Get-Deep32PlainPath -Path $responseFile -MayNotExist)
    if ([IO.File]::Exists($responseFile)) { [IO.File]::Delete($responseFile) }
    $headers = @{}
    if ($offset -gt 0) { $headers['Range'] = 'bytes=' + $offset + '-' }
    $response = $null
    $failure = $null
    try {
        $response = Invoke-Deep32Request -Uri ($script:Deep32ReleaseBase + '/' + $script:Deep32Release + '/' + $Name) `
            -OutFile $responseFile -Headers $headers
    } catch {
        $failure = $_
        $responseProperty = $_.Exception.PSObject.Properties['Response']
        if ($null -ne $responseProperty -and $null -ne $responseProperty.Value) { $response = $responseProperty.Value }
    }
    if ($null -eq $response -or -not [IO.File]::Exists($responseFile)) {
        # At offset zero, an interrupted body starts at zero even when the
        # exception provides no response object. Keep only bounded payload
        # bytes; the manifest digest still gates eventual publication.
        if ($null -eq $response -and $offset -eq 0 -and -not $Metadata -and
            [IO.File]::Exists($responseFile) -and ([IO.FileInfo]$responseFile).Length -le $ExpectedSize) {
            if ([IO.File]::Exists($Target)) { [IO.File]::Delete($Target) }
            [IO.File]::Move($responseFile, $Target)
        }
        throw "Download interrupted; the previous partial file was retained: $Name"
    }
    [void](Get-Deep32PlainPath -Path $responseFile)
    [long]$received = ([IO.FileInfo]$responseFile).Length
    [int]$status = $response.StatusCode
    $range = [string]$response.Headers['Content-Range']
    if ($status -eq 206) {
        if ($ExpectedSize -lt 0 -or $range -cnotmatch '^bytes ([0-9]+)-([0-9]+)/([0-9]+)$') {
            throw "The server sent an invalid Content-Range: $Name"
        }
        [long]$start = $Matches[1]
        [long]$end = $Matches[2]
        [long]$total = $Matches[3]
        if ($start -ne $offset -or $total -ne $ExpectedSize -or $end -lt $start -or
            $end -ge $total -or $received -gt $end - $start + 1) {
            throw "The server sent a mismatched Content-Range: $Name"
        }
        $inputStream = $null
        $outputStream = $null
        try {
            $inputStream = [IO.File]::Open($responseFile, 'Open', 'Read', 'Read')
            $outputStream = [IO.File]::Open($Target, 'OpenOrCreate', 'Write', 'None')
            if ($outputStream.Length -ne $offset) { throw 'A partial download changed during transfer.' }
            [void]$outputStream.Seek($offset, 'Begin')
            [void](Copy-Deep32Stream $inputStream $outputStream $received)
            $outputStream.Flush()
        } finally {
            if ($null -ne $inputStream) { $inputStream.Dispose() }
            if ($null -ne $outputStream) { $outputStream.Dispose() }
        }
    } elseif ($status -eq 200) {
        if (($ExpectedSize -ge 0 -and $received -gt $ExpectedSize) -or ($Metadata -and $received -gt 16777216)) {
            throw "The response file is too large: $Name"
        }
        if ($offset -gt 0 -and $null -eq $failure -and $received -ne $ExpectedSize) {
            throw "The server ignored Range without sending the complete file: $Name"
        }
        # A cut full response can replace a shorter partial safely. Its bytes
        # start at zero, and final size/hash checks still gate publication.
        if ($offset -eq 0 -or $received -gt $offset -or ($null -eq $failure -and $received -eq $ExpectedSize)) {
            if ([IO.File]::Exists($Target)) { [IO.File]::Delete($Target) }
            [IO.File]::Move($responseFile, $Target)
        }
    } else {
        throw "The server returned HTTP $status for $Name"
    }
    if ([IO.File]::Exists($responseFile)) { [IO.File]::Delete($responseFile) }
    if ($null -ne $failure) { throw "Download interrupted; verified range progress was retained: $Name" }
    if ($ExpectedSize -ge 0 -and ([IO.FileInfo]$Target).Length -ne $ExpectedSize) {
        throw "Download incomplete; run the same command to resume: $Name"
    }
}

function Read-Deep32Ascii {
    param([string]$Path)
    [void](Get-Deep32PlainPath -Path $Path)
    if (([IO.FileInfo]$Path).Length -gt 16777216) { throw 'A metadata file is too large.' }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10) { throw 'Metadata must end with LF.' }
    foreach ($value in $bytes) {
        if (($value -lt 32 -and $value -ne 9 -and $value -ne 10) -or $value -gt 126) {
            throw 'Metadata must be ASCII with LF line endings.'
        }
    }
    return [Text.Encoding]::ASCII.GetString($bytes)
}

function ConvertTo-Deep32Quoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Confirm-Deep32Signature {
    param([string]$Cache, [string]$Key)
    $homeDirectory = New-Deep32Directory (Join-Path $Cache 'gpg')
    $sums = Join-Path $Cache 'SHA256SUMS'
    $signature = $sums + '.sig'
    $verifyArguments = @('--no-options', '--homedir', $homeDirectory, '--batch', '--no-auto-key-retrieve',
        '--no-default-keyring', '--keyring', $Key, '--lock-never', '--trust-model', 'always',
        '--status-fd', '1', '--verify', $signature, $sums)
    $gpg = Get-Command gpg -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $gpg) {
        $exact = 'gpg ' + (($verifyArguments | ForEach-Object { ConvertTo-Deep32Quoted $_ }) -join ' ')
        throw ("GPG is missing. Install GnuPG, then run the same download command. Verification command:`n" + $exact)
    }
    [void](Get-Deep32PlainPath -Path $Key)
    $keyArguments = @('--no-options', '--homedir', $homeDirectory, '--batch', '--no-auto-key-retrieve',
        '--no-default-keyring', '--lock-never', '--with-colons', '--show-keys', '--fingerprint', '--', $Key)
    $previousHome = $env:GNUPGHOME
    $previousPreference = $ErrorActionPreference
    try {
        $env:GNUPGHOME = $homeDirectory
        $ErrorActionPreference = 'Continue'
        $keyOutput = @(& $gpg.Source @keyArguments 2>&1 | ForEach-Object { $_.ToString() })
        $keyCode = $LASTEXITCODE
        if ($keyCode -ne 0) { throw 'The supplied release public key could not be inspected.' }
        $primaryCount = 0
        $primarySection = $false
        $fingerprints = New-Object 'Collections.Generic.List[string]'
        foreach ($line in $keyOutput) {
            $fields = $line.Split([char]58)
            if ($fields[0] -ceq 'pub') {
                $primaryCount++
                $primarySection = $true
            } elseif ($fields[0] -ceq 'sub') {
                $primarySection = $false
            } elseif ($fields[0] -ceq 'fpr' -and $primarySection) {
                if ($fields.Count -lt 10 -or $fields[9] -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$') {
                    throw 'The supplied release public key has an invalid primary fingerprint.'
                }
                $fingerprints.Add($fields[9].ToUpperInvariant())
            }
        }
        if ($primaryCount -ne 1 -or $fingerprints.Count -ne 1) {
            throw 'The supplied release public key must contain exactly one primary key and fingerprint.'
        }
        $expectedFingerprint = $fingerprints[0]
        $output = @(& $gpg.Source @verifyArguments 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
    } finally {
        $env:GNUPGHOME = $previousHome
        $ErrorActionPreference = $previousPreference
    }
    if ($code -ne 0) { throw 'The release checksum signature is invalid; no payload was downloaded.' }
    $validCount = 0
    foreach ($line in $output) {
        if ($line -cnotmatch '^\[GNUPG:\] VALIDSIG(?: |$)') { continue }
        $fields = $line.Split([char]32)
        if (($fields.Count -ne 11 -and $fields.Count -ne 12) -or
            $fields[2] -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$') {
            throw 'The release checksum signature has malformed signer status.'
        }
        $primary = $fields[2]
        if ($fields.Count -eq 12) { $primary = $fields[11] }
        if ($primary -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$' -or
            $primary.ToUpperInvariant() -cne $expectedFingerprint) {
            throw 'The release checksum signer does not match the supplied primary key.'
        }
        $validCount++
    }
    if ($validCount -ne 1) { throw 'The release checksum signature is invalid; no payload was downloaded.' }
}

function Assert-Deep32Integer {
    param([object]$Value, [long]$Minimum, [long]$Maximum)
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt $Minimum -or $Value -gt $Maximum) {
        throw 'A manifest integer is invalid.'
    }
}

function Get-Deep32Manifest {
    param([string]$Cache, [string]$Role, [string]$Release)
    $checksums = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    $sumText = Read-Deep32Ascii (Join-Path $Cache 'SHA256SUMS')
    foreach ($line in $sumText.TrimEnd([char]10).Split([char]10)) {
        if ($line -cnotmatch '^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,199})$') { throw 'A root checksum line is invalid.' }
        $hash = $Matches[1]
        $name = $Matches[2]
        Assert-Deep32Name $name
        if ($checksums.ContainsKey($name)) { throw 'Root checksum names are duplicated.' }
        $checksums.Add($name, $hash)
    }
    $assembly = Join-Path $Cache 'ASSEMBLY.json'
    if (-not $checksums.ContainsKey('ASSEMBLY.json') -or (Get-Deep32Hash $assembly) -cne $checksums['ASSEMBLY.json']) {
        throw 'ASSEMBLY.json does not match the signed root checksums.'
    }
    # Parse only after its bytes have been authenticated.
    $assemblyText = Read-Deep32Ascii $assembly
    if (-not $assemblyText.TrimStart().StartsWith('{', [StringComparison]::Ordinal)) { throw 'The assembly manifest must be an object.' }
    if ($PSVersionTable.PSVersion.Major -le 5) {
        Add-Type -AssemblyName System.Web.Extensions
        $serializer = New-Object Web.Script.Serialization.JavaScriptSerializer
        $serializer.MaxJsonLength = 16777216
        $manifest = $serializer.DeserializeObject($assemblyText)
    } else {
        $manifest = ConvertFrom-Json -InputObject $assemblyText -AsHashtable
    }
    if ($manifest -isnot [Collections.IDictionary]) { throw 'The manifest must be an object.' }
    if ($manifest.format -cne 'deep32-release-assembly' -or $manifest.format_version -ne 1 -or
        $manifest.version -cne $Release.Substring(1)) { throw 'The manifest format or release version is invalid.' }
    Assert-Deep32Integer $manifest.format_version 1 1
    Assert-Deep32Name $manifest.architecture
    Assert-Deep32Integer $manifest.part_size_bytes 1 1900000000
    $cap = [long]$manifest.part_size_bytes
    $pending = @()
    if ($manifest.ContainsKey('pending')) {
        if ($manifest.pending -isnot [object[]]) { throw 'Manifest pending roles must be an array.' }
        $pending = $manifest.pending
        if ($pending.Count -gt 2) { throw 'Only disc2 and disc3 may be pending, once each.' }
        $pendingRoles = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        foreach ($pendingRole in $pending) {
            if ($pendingRole -isnot [string] -or $pendingRole -cnotin @('disc2', 'disc3') -or
                -not $pendingRoles.Add($pendingRole)) {
                throw 'Only disc2 and disc3 may be pending, once each.'
            }
        }
    }
    $pendingPrefixes = New-Object 'Collections.Generic.Dictionary[string,object]'
    if ($manifest.ContainsKey('pending_prefixes')) {
        if ($manifest.pending_prefixes -isnot [Collections.IDictionary]) {
            throw 'Manifest pending prefixes must be an object.'
        }
        $pendingPrefixes = $manifest.pending_prefixes
    }
    if ($pendingPrefixes.Count -ne $pending.Count) { throw 'Pending prefixes do not match the pending roles.' }
    foreach ($pendingRole in $pending) {
        $kind = @{disc2 = 'content'; disc3 = 'gpu-pack'}[$pendingRole]
        $expectedPrefix = 'deep32-' + $manifest.version + '-' + $pendingRole + '-' + $kind
        if (-not $pendingPrefixes.ContainsKey($pendingRole) -or $pendingPrefixes[$pendingRole] -isnot [string] -or
            $pendingPrefixes[$pendingRole] -cne $expectedPrefix) { throw ('The pending ' + $pendingRole + ' prefix is invalid.') }
        foreach ($name in $checksums.Keys) {
            if ($name.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'A pending disc has release assets.'
            }
        }
    }
    if ($manifest.discs -isnot [object[]]) { throw 'Manifest discs must be an array.' }
    $discs = $manifest.discs
    if ($discs.Count -ne 3 - $pending.Count) { throw 'The manifest must cover all present and pending disc roles.' }
    $roles = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($name in @('ASSEMBLY.json', 'SHA256SUMS', 'SHA256SUMS.sig')) { [void]$names.Add($name) }
    $selected = $null
    foreach ($disc in $discs) {
        if ($disc -isnot [Collections.IDictionary]) { throw 'A manifest disc must be an object.' }
        if ($disc.role -cnotin @('disc1', 'disc2', 'disc3') -or $pending -ccontains $disc.role -or
            -not $roles.Add([string]$disc.role)) {
            throw 'The manifest disc roles are invalid or duplicated.'
        }
        $kind = @{disc1 = 'workstation'; disc2 = 'content'; disc3 = 'gpu-pack'}[$disc.role]
        if ($disc.kind -cne $kind) { throw 'The manifest disc kind is invalid.' }
        Assert-Deep32Name $disc.file
        if (($disc.role -ceq 'disc1' -and $disc.file -cnotmatch '\.(img|iso)$') -or
            ($disc.role -cne 'disc1' -and $disc.file -cnotmatch '\.iso$')) { throw 'A disc image suffix is invalid.' }
        if (-not $names.Add($disc.file)) { throw 'Manifest file names collide.' }
        Assert-Deep32Integer $disc.size 1 ([long]::MaxValue)
        if ($disc.sha256 -isnot [string] -or $disc.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $disc.split -isnot [bool]) {
            throw 'A disc digest or split flag is invalid.'
        }
        foreach ($field in @('checksums', 'readme')) {
            $name = $disc.$field
            Assert-Deep32Name $name
            if (-not $names.Add($name)) { throw 'Manifest file names collide.' }
        }
        $parts = @($disc.payloads)
        if ($parts.Count -lt 1 -or $parts.Count -gt 999) { throw 'A disc has an invalid part count.' }
        if ($disc.split) {
            Assert-Deep32Name $disc.parts_manifest
            if ($disc.parts_manifest -cne $disc.file + '.parts.tsv' -or -not $names.Add($disc.parts_manifest) -or
                $parts.Count -lt 2 -or $disc.size -le $cap) { throw 'Split metadata is invalid.' }
        } elseif ($null -ne $disc.parts_manifest -or $parts.Count -ne 1 -or $disc.size -gt $cap) {
            throw 'Unsplit metadata is invalid.'
        }
        [long]$total = 0
        for ($index = 0; $index -lt $parts.Count; $index++) {
            $part = $parts[$index]
            Assert-Deep32Name $part.name
            Assert-Deep32Integer $part.size 1 $cap
            if ($part.sha256 -isnot [string] -or $part.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'A part digest is invalid.' }
            $expected = $disc.file
            if ($disc.split) {
                $expected += '.part' + ($index + 1).ToString('000')
                if (-not $names.Add($part.name)) { throw 'Manifest payload names collide.' }
                if ($index -lt $parts.Count - 1 -and $part.size -ne $cap) { throw 'Part ordering or size is invalid.' }
            } elseif ($part.size -ne $disc.size -or $part.sha256 -cne $disc.sha256) {
                throw 'An unsplit payload does not agree with its disc.'
            }
            if ($part.name -cne $expected -or $part.size -gt [long]$disc.size - $total) { throw 'Part names or total size are invalid.' }
            $total += [long]$part.size
            if (-not $checksums.ContainsKey($part.name) -or $checksums[$part.name] -cne $part.sha256) {
                throw 'A payload disagrees with the signed root checksums.'
            }
        }
        if ($total -ne $disc.size) { throw 'The disc part sizes do not add up.' }
        if ($disc.role -ceq $Role) { $selected = $disc }
    }
    foreach ($expectedRole in @('disc1', 'disc2', 'disc3')) {
        if ($pending -cnotcontains $expectedRole -and -not $roles.Contains($expectedRole)) {
            throw 'The manifest must cover all present and pending disc roles.'
        }
    }
    if ($null -eq $selected) {
        if ($pending -ccontains $Role) { return $null }
        throw 'The selected disc is absent.'
    }
    return $selected
}

function Invoke-Deep32Wsl {
    param([string[]]$Arguments)
    & wsl.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'A WSL command failed.' }
}

function Invoke-Deep32Install {
    param([string]$Image, [string]$Destination)
    if (-not (Get-Command Mount-DiskImage -ErrorAction SilentlyContinue) -or
        -not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw 'Disc 3 installation needs Windows Mount-DiskImage and WSL. Enable WSL, then rerun with --install.'
    }
    $attachment = Get-DiskImage -ImagePath $Image -ErrorAction Stop
    $needsAttachment = -not $attachment.Attached
    $acquired = $false
    $drvfsAcquired = $false
    $directoryAcquired = $false
    $mountPoint = '/mnt/deep32-' + [Guid]::NewGuid().ToString('N')
    $distro = 'Deep32Forge'
    try {
        if ($needsAttachment) {
            $attachment = Mount-DiskImage -ImagePath $Image -Access ReadOnly -PassThru -ErrorAction Stop
            $acquired = $true
        }
        $volumes = @($attachment | Get-Volume -ErrorAction Stop | Where-Object { $null -ne $_.DriveLetter -and [string]$_.DriveLetter -ne '' })
        if ($volumes.Count -ne 1) { throw 'The verified disc needs one readable drive letter.' }
        $drive = [string]$volumes[0].DriveLetter + ':'
        $discRoot = $drive + '\'
        $rootfs = Get-Deep32PlainPath -Path (Join-Path $discRoot 'deep32\forms\forge-wsl.tar')
        [void](Get-Deep32PlainPath -Path (Join-Path $discRoot 'deep32\payload') -Directory)
        $existing = @(Invoke-Deep32Wsl -Arguments @('--list', '--quiet'))
        $found = @($existing | ForEach-Object { ([string]$_).Replace([string][char]0, '').Trim() } | Where-Object { $_ -ceq $distro }).Count -gt 0
        if (-not $found) {
            $import = Join-Path $Destination 'Deep32Forge'
            [void](Get-Deep32PlainPath -Path $import -Directory -MayNotExist)
            if ([IO.Directory]::Exists($import) -or [IO.File]::Exists($import)) {
                throw 'The Deep32Forge import directory already exists; it will not be replaced.'
            }
            Invoke-Deep32Wsl -Arguments @('--import', $distro, $import, $rootfs, '--version', '2')
        }
        Invoke-Deep32Wsl -Arguments @('--distribution', $distro, '--user', 'root', '--exec', 'mkdir', '--', $mountPoint)
        $directoryAcquired = $true
        Invoke-Deep32Wsl -Arguments @('--distribution', $distro, '--user', 'root', '--exec', 'mount', '-t', 'drvfs', '-o', 'ro', $drive, $mountPoint)
        $drvfsAcquired = $true
        Invoke-Deep32Wsl -Arguments @('--distribution', $distro, '--user', 'root', '--exec',
            '/opt/deep32-forge/forms/provision-wsl.sh', ($mountPoint + '/deep32/payload'))
    } finally {
        try {
            if ($drvfsAcquired) {
                Invoke-Deep32Wsl -Arguments @('--distribution', $distro, '--user', 'root', '--exec', 'umount', '--', $mountPoint)
            }
            if ($directoryAcquired) {
                Invoke-Deep32Wsl -Arguments @('--distribution', $distro, '--user', 'root', '--exec', 'rmdir', '--', $mountPoint)
            }
        } finally {
            if ($acquired) { Dismount-DiskImage -ImagePath $Image -ErrorAction Stop }
        }
    }
    Write-Output 'Disc 3 installation finished.'
}

function Invoke-Deep32Main {
    param([string[]]$Arguments)
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $role = $null
    $destination = Join-Path (Get-Location).Path 'deep32-downloads'
    $release = 'v0.1.0-alpha.1'
    $key = Join-Path $script:Deep32ScriptDirectory '..\..\install\release-key.gpg'
    $source = $null
    $install = $false
    $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $argument = $Arguments[$index]
        if ($argument -ceq '--install') {
            if (-not $seen.Add($argument)) { throw 'Duplicate option: --install' }
            $install = $true
        } elseif ($argument -cin @('--to', '--release', '--key', '--source')) {
            if (-not $seen.Add($argument) -or $index + 1 -ge $Arguments.Count) { throw "Missing value or duplicate option: $argument" }
            $index++
            $value = $Arguments[$index]
            switch -CaseSensitive ($argument) {
                '--to' { $destination = $value }
                '--release' { $release = $value }
                '--key' { $key = $value }
                '--source' { $source = $value }
            }
        } elseif ($null -eq $role -and $argument -cin @('disc1', 'disc2', 'disc3')) {
            $role = $argument
        } else {
            throw 'Usage: deep32-get.ps1 disc1|disc2|disc3 [--to DIR] [--release v0.1.0-alpha.1] [--install] [--key FILE] [--source LOCAL_DIRECTORY_OR_FILE_URL]'
        }
    }
    if ($null -eq $role) { throw 'Select disc1, disc2 or disc3.' }
    if ($release -cnotmatch '^v[A-Za-z0-9][A-Za-z0-9._-]*$' -or $release.Contains('..')) { throw 'The release tag is unsafe.' }
    if ($install -and $role -cne 'disc3') { throw '--install is supported only for disc3.' }
    $script:Deep32Source = $null
    $script:Deep32Release = $release
    if ($null -ne $source) {
        if ($source -match '^file:') {
            $uri = [Uri]$source
            if (-not $uri.IsFile -or $uri.IsUnc -or ($uri.Host -and $uri.Host -cne 'localhost')) {
                throw '--source file URLs must name a local folder.'
            }
            $source = $uri.LocalPath
        } elseif ($source -match '^[A-Za-z][A-Za-z0-9+.-]*://' -or $source.StartsWith('\\') -or $source.StartsWith('//')) {
            throw '--source accepts only a local folder or file URL.'
        }
        if ([IO.Path]::GetFullPath($source).StartsWith('\\')) { throw '--source must be a local folder.' }
        $script:Deep32Source = Get-Deep32PlainPath -Path $source -Directory
    }
    $key = [IO.Path]::GetFullPath($key)
    $destination = New-Deep32Directory $destination
    $cache = New-Deep32Directory (Join-Path $destination ('.deep32-' + $release))
    foreach ($name in @('ASSEMBLY.json', 'SHA256SUMS', 'SHA256SUMS.sig')) {
        $temporary = Join-Path $cache ($name + '.incoming')
        [void](Get-Deep32PlainPath -Path $temporary -MayNotExist)
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        Invoke-Deep32Fetch -Name $name -Target $temporary -Metadata
        $final = Join-Path $cache $name
        [void](Get-Deep32PlainPath -Path $final -MayNotExist)
        if ([IO.File]::Exists($final)) { [IO.File]::Delete($final) }
        Move-Deep32NewFile $temporary $final
    }
    Confirm-Deep32Signature -Cache $cache -Key $key
    $disc = Get-Deep32Manifest -Cache $cache -Role $role -Release $release
    if ($null -eq $disc) {
        Write-Output ($role + ' is not in this release yet; it will be added to the same release. Check the release page.')
        exit 3
    }
    Write-Output 'Release signature and manifest verified.'
    $image = Join-Path $destination $disc.file
    [void](Get-Deep32PlainPath -Path $image -MayNotExist)
    if ([IO.File]::Exists($image)) {
        Assert-Deep32File $image $disc.size $disc.sha256
        Write-Output 'The verified disc image is already present.'
    } else {
        foreach ($part in @($disc.payloads)) {
            $path = Join-Path $cache $part.name
            [void](Get-Deep32PlainPath -Path $path -MayNotExist)
            if ([IO.File]::Exists($path)) {
                Assert-Deep32File $path $part.size $part.sha256
                Write-Output ('Verified cached ' + $part.name)
                continue
            }
            $partial = $path + '.download'
            Invoke-Deep32Fetch -Name $part.name -Target $partial -ExpectedSize $part.size
            Assert-Deep32File $partial $part.size $part.sha256
            Move-Deep32NewFile $partial $path
            Write-Output ('Downloaded and verified ' + $part.name)
        }
        $joined = Join-Path $cache ($disc.file + '.joining')
        [void](Get-Deep32PlainPath -Path $joined -MayNotExist)
        if ([IO.File]::Exists($joined)) { [IO.File]::Delete($joined) }
        $outputStream = $null
        try {
            $outputStream = [IO.File]::Open($joined, 'CreateNew', 'Write', 'None')
            foreach ($part in @($disc.payloads)) {
                $path = Join-Path $cache $part.name
                Assert-Deep32File $path $part.size $part.sha256
                $inputStream = [IO.File]::Open($path, 'Open', 'Read', 'Read')
                try {
                    $copied = Copy-Deep32Stream $inputStream $outputStream $part.size
                    if ($copied -ne $part.size) { throw 'A part changed during joining.' }
                } finally { $inputStream.Dispose() }
            }
            $outputStream.Flush()
        } finally {
            if ($null -ne $outputStream) { $outputStream.Dispose() }
        }
        Assert-Deep32File $joined $disc.size $disc.sha256
        Move-Deep32NewFile $joined $image
        Write-Output ('Disc image verified: ' + $image)
    }
    if ($install) { Invoke-Deep32Install -Image $image -Destination $destination }
    elseif ($role -ceq 'disc1') { Write-Output 'Write the verified disc 1 image to a USB stick, then boot from that USB stick.' }
    elseif ($role -ceq 'disc2') { Write-Output 'Attach the verified disc 2 content image in Deep32 Workstation and import its content.' }
    else { Write-Output 'Rerun this command with --install to install disc 3 using its WSL form.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-Deep32Main -Arguments $args }
    catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
}
