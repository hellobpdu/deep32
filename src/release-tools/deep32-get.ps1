# PowerShell 5.1 or later. Download public Deep32 release assets without an account.
# Exit codes: 0 success, 1 refusal or failure, 3 selected disc pending.
$script:Deep32ReleaseBase = 'https://github.com/hellobpdu/deep32/releases/download'
$script:Deep32ScriptDirectory = $PSScriptRoot
$script:Deep32PythonExecutable = $null

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

function Assert-Deep32SplitLocalPath {
    param([string]$Path)
    $devices = '\A(?:con|prn|aux|nul|clock\$|conin\$|conout\$|(?:com|lpt)[0-9\u00b9\u00b2\u00b3])(?:\.|\z)'
    if ($Path -cnotmatch '\A[A-Za-z]:[\\/]' -or $Path.Substring(2) -match '[\x00-\x1f\x7f:<>"|?*]' -or
            @($Path.Substring(3) -split '[\\/]' | Where-Object { $_ -match '[. ]$' -or $_ -match $devices }).Count) {
        throw 'Use absolute local Windows paths for the split release folder, data and backing directories.'
    }
}

function New-Deep32Directory {
    param([string]$Path)
    $full = Get-Deep32PlainPath -Path $Path -Directory -MayNotExist
    [void][IO.Directory]::CreateDirectory($full)
    return Get-Deep32PlainPath -Path $full -Directory
}

function Get-Deep32Hash {
    param([string]$Path, [IO.Stream]$ReadHandle)
    $stream = $ReadHandle
    $digest = $null
    try {
        if (-not $ReadHandle) {
            $full = Get-Deep32PlainPath -Path $Path
            $stream = [IO.File]::Open($full, 'Open', 'Read', 'Read')
        }
        $stream.Position = 0
        $digest = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($digest.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if (-not $ReadHandle -and $null -ne $stream) { $stream.Dispose() }
        if ($null -ne $digest) { $digest.Dispose() }
    }
}

function Open-Deep32VerifiedMedia {
    param([string]$Path, [string]$Source, [long]$Size, [string]$Hash)
    Assert-Deep32SplitLocalPath $Path
    Assert-Deep32SplitLocalPath $Source
    $full = Get-Deep32PlainPath -Path $Path
    $parent = [IO.Path]::GetDirectoryName($full).TrimEnd([char[]]@('\', '/'))
    if ($parent -ine $Source.TrimEnd([char[]]@('\', '/'))) { throw 'A verified ISO is outside the consented release folder.' }
    $stream = [IO.File]::Open($full, 'Open', 'Read', 'Read')
    try {
        [void](Get-Deep32PlainPath -Path $full)
        if ($stream.Length -ne $Size -or (Get-Deep32Hash -ReadHandle $stream) -cne $Hash) {
            throw 'A split Forge ISO changed after verification.'
        }
        return $stream
    } catch {
        $stream.Dispose()
        throw
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

function Get-Deep32Python {
    if ($script:Deep32PythonExecutable) { return $script:Deep32PythonExecutable }
    $candidates = @()
    if ($env:DEEP32_PYTHON) { $candidates = @($env:DEEP32_PYTHON) }
    else { $candidates = @(Get-Command python3.exe,python.exe -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }) }
    foreach ($candidate in $candidates) {
        if ([IO.Path]::GetExtension($candidate) -ine '.exe' -or $candidate -match '[\\/]WindowsApps[\\/]') { continue }
        # Installed Python launchers may themselves be links. The no-reparse
        # rule applies to release/settings targets, not the selected toolchain.
        $candidate = [IO.Path]::GetFullPath($candidate)
        if (-not [IO.File]::Exists($candidate)) { continue }
        & $candidate -I -B -c 'import sys;sys.exit(sys.version_info<(3,10))' 2>$null
        if ($LASTEXITCODE -eq 0) { $script:Deep32PythonExecutable = $candidate; return $candidate }
    }
    # the Python requirement and corrective action apply to each host.
    Write-Output "Install Python 3.10 or later using this host's package manager or the Python vendor download; reopen PowerShell and run this command again."
    exit 10
}

function Invoke-Deep32ManifestHelper {
    param([string[]]$Arguments)
    $python = Get-Deep32Python
    & $python -I -B (Join-Path $script:Deep32ScriptDirectory 'deep32_get_manifest.py') @Arguments
    if ($LASTEXITCODE -eq 10) { exit 10 }
    if ($LASTEXITCODE -ne 0) { throw 'Release operation refused. Next: correct the reported input and rerun the same command.' }
}

function Invoke-Deep32WholeHelper {
    param([string[]]$Arguments, [switch]$Multiple)
    $python = Get-Deep32Python
    $lines = @(& $python -I -B (Join-Path $script:Deep32ScriptDirectory 'deep32_get_whole.py') @Arguments)
    $result = $LASTEXITCODE
    if ($result -in @(3, 10)) { exit $result }
    if ($result -ne 0) { throw 'Release operation refused. Next: correct the reported input and rerun the same command.' }
    # The shared helper alone authenticates/selects and transfers the images.
    # Its stdout contains literal TSV records; diagnostic text uses stderr.
    if (($Multiple -and ($lines.Count -lt 2 -or $lines.Count -gt 4)) -or
        (-not $Multiple -and $lines.Count -ne 1)) { throw 'The shared downloader returned invalid image records.' }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($line in $lines) {
        $fields = @(([string]$line).Split([char]9))
        [long]$size = 0
        if ($fields.Count -ne 4 -or -not [long]::TryParse($fields[2], [ref]$size) -or $size -lt 1 -or
            $fields[3] -cnotmatch '^[0-9a-f]{64}$') { throw 'The shared downloader returned an invalid image record.' }
        Assert-Deep32Name $fields[1]
        if (-not $names.Add($fields[1])) { throw 'The shared downloader returned a duplicate image record.' }
        $image = Get-Deep32PlainPath -Path $fields[0]
        if ([IO.Path]::GetFileName($image) -cne $fields[1]) { throw 'The shared downloader returned an invalid image name.' }
        Assert-Deep32File $image $size $fields[3]
        @{ image = $image; disc = @{ file = $fields[1]; size = $size; sha256 = $fields[3] } }
    }
}

function Confirm-Deep32Signature {
    param([string]$Cache, [string]$Key, [string]$Fingerprint, [string]$Gpg)
    $arguments = @('authenticate', $Cache, $Key, $Fingerprint)
    if ($Gpg) { $arguments += $Gpg }
    Invoke-Deep32ManifestHelper -Arguments $arguments
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

function Invoke-Deep32DockerLoader {
    param([string[]]$Arguments)
    $python = Get-Deep32Python
    & $python -I -B (Join-Path $script:Deep32ScriptDirectory 'deep32_settings.py') run-loader -- $python -I -B @Arguments
    if ($LASTEXITCODE -eq 11) { exit 11 }
    if ($LASTEXITCODE -ne 0) { throw 'The Docker Desktop loader failed. Next: check the GPU pack status and recovery commands.' }
}

function Invoke-Deep32Settings {
    param([string[]]$Arguments)
    $python = Get-Deep32Python
    & $python -I -B (Join-Path $script:Deep32ScriptDirectory 'deep32_settings.py') @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'The settings handoff was refused. Next: correct the non-secret choices and review the plan again.' }
}

function Test-Deep32InstallPrerequisites {
    param([string]$Journey, [string]$InternalForgeEvidence)
    $engine = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    $inventory = Join-Path $script:Deep32ScriptDirectory '..\..\install\deep32-inventory.ps1'
    Write-Host 'Checking read-only prerequisites for this GPU host'
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $inventory,
        '-Journey', $Journey, '-Role', 'gpu-host', '-CheckPrereqs')
    if ($InternalForgeEvidence) { $arguments += @('-InternalForgeEvidence', $InternalForgeEvidence) }
    & $engine @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'Prerequisites need operator action; no installation change was made by this stage.'
        Write-Output ('Next: powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-inventory.ps1 -Journey ' + $Journey + ' -Role gpu-host -CheckPrereqs')
        exit 10
    }
}

function Test-Deep32SignedForgePrerequisites {
    param([hashtable]$Evidence, [hashtable]$SettingsPlan)
    $identity = $SettingsPlan.plan.identity
    $arguments = @('--source', $Evidence.source, '--gpu-class', $Evidence.gpu_class,
        '--forge-data-root', $identity.data_root, '--key', $Evidence.key, '--fingerprint', $Evidence.fingerprint,
        '--plan', $SettingsPlan.path, '--accept-plan', $SettingsPlan.accepted, '--journey', $identity.journey)
    if ($Evidence.gpg) { $arguments += @('--gpg', $Evidence.gpg) }
    $python = Get-Deep32Python
    $helper = Join-Path $script:Deep32ScriptDirectory 'forge_preflight_windows.py'
    & $python -I -B $helper @arguments
    if ($LASTEXITCODE -eq 10) { exit 10 }
    if ($LASTEXITCODE -ne 0) { throw 'The signed Windows Forge prerequisites could not be verified. Review the local release folder and plan.' }
}

function Test-Deep32SplitForgePrerequisites {
    param([hashtable]$Evidence, [hashtable]$SettingsPlan)
    $observation = @(Test-Deep32SignedForgePrerequisites -Evidence $Evidence -SettingsPlan $SettingsPlan)
    if ($observation.Count -ne 1 -or $observation[0] -isnot [string] -or $observation[0].Length -gt 65535 -or
            $observation[0] -cnotmatch '\A\{[^\r\n]*\}\z' -or $observation[0] -cmatch '[^\x00-\x7f]') {
        throw 'The signed Windows Forge prerequisite probe returned invalid observations.'
    }
    $path = Join-Path $SettingsPlan.directory 'forge-evidence.json'
    [void](Get-Deep32PlainPath -Path $path -MayNotExist)
    $empty = [IO.File]::Open($path, 'CreateNew', 'Write', 'None')
    try {
        $empty.Dispose()
        Set-Deep32PrivatePath -Path $path
        [IO.File]::WriteAllText($path, $observation[0] + "`n", [Text.Encoding]::ASCII)
        Test-Deep32InstallPrerequisites -Journey $SettingsPlan.plan.identity.journey -InternalForgeEvidence $path
    } finally { [IO.File]::Delete($path) }
}

function Set-Deep32PrivatePath {
    param([string]$Path, [switch]$Directory, [switch]$Inherit)
    [void](Get-Deep32PlainPath -Path $Path -Directory:$Directory)
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'
    if ($Directory) { $acl = New-Object Security.AccessControl.DirectorySecurity }
    else { $acl = New-Object Security.AccessControl.FileSecurity }
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($owner, $system)) {
        if ($Directory -and $Inherit) {
            $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                [Security.AccessControl.InheritanceFlags]::ObjectInherit
            $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inheritance, 'None', 'Allow')
        } else {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
        }
        $acl.AddAccessRule($rule)
    }
    # Use the host ACL APIs directly so protected staging also works when
    # a parent shell's module path prevents Security cmdlet auto-loading.
    if ($PSVersionTable.PSEdition -eq 'Core') {
        if ($Directory) { [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($Path), $acl) }
        else { [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($Path), $acl) }
    } elseif ($Directory) { [IO.Directory]::SetAccessControl($Path, $acl) }
    else { [IO.File]::SetAccessControl($Path, $acl) }
}

function New-Deep32SettingsPlan {
    param([string]$ImageHash, [string]$Destination, [hashtable]$Options, [object[]]$MediaComponents)
    $arguments = @('plan', '--media-sha256', $ImageHash, '--destination', $Destination,
        '--journey', $Options.journey, '--form', $Options.form, '--platform', $Options.platform,
        '--docker-role', $Options.docker_role, '--data-root', $Options.data_root) + @($Options.settings_arguments)
    foreach ($media in $MediaComponents) {
        $arguments += @('--media-component', $media.component, [string]$media.disc.size, $media.disc.sha256)
    }
    if ($Options.docker_role -ceq 'full') { $arguments += @('--content', $Options.content) }
    if ($Options.docker_storage_path) { $arguments += @('--docker-storage-path', $Options.docker_storage_path) }
    if ($Options.confirm_tailnet) { $arguments += @('--confirm-tailnet', $Options.confirm_tailnet) }
    if ($Options['desktop_isolation_confirmed']) { $arguments += '--confirm-desktop-isolation' }
    if (-not $Options.non_interactive -and -not [Console]::IsInputRedirected) { $arguments += '--interactive' }
    $lines = @(Invoke-Deep32Settings -Arguments $arguments)
    $planText = ($lines -join "`n") + "`n"
    $plan = ConvertFrom-Json -InputObject $planText
    Write-Host $planText
    $accepted = $Options.accept_plan
    if (-not $accepted -and -not $Options.non_interactive -and -not [Console]::IsInputRedirected) {
        $accepted = Read-Host 'Review this target and settings. Type the plan digest to install'
    }
    if ($accepted -cne $plan.digest) { throw 'Plan consent required. Next: rerun with --accept-plan and the displayed digest.' }
    # Create only after every choice and consent has been validated. The helper
    # verifies this owner/System-only ACL before writing settings or its record.
    $stageParent = $Destination
    if ($MediaComponents) { $stageParent = [IO.Path]::GetTempPath() }
    $stage = Join-Path $stageParent ('.settings-' + [Guid]::NewGuid().ToString('N'))
    [void](New-Deep32Directory $stage)
    try {
        Set-Deep32PrivatePath -Path $stage -Directory
        $path = Join-Path $stage 'plan.json'
        $empty = [IO.File]::Open($path, 'CreateNew', 'Write', 'None')
        $empty.Dispose()
        Set-Deep32PrivatePath -Path $path
        [IO.File]::WriteAllText($path, $planText, [Text.Encoding]::ASCII)
        Invoke-Deep32Settings -Arguments @('stage', '--plan', $path, '--accept-plan', $accepted, '--directory', $stage) | Write-Host
        return @{ path = $path; plan = $plan; directory = $stage; accepted = $accepted }
    } catch {
        Remove-Deep32SettingsStage -Directory $stage
        throw
    }
}

function Remove-Deep32SettingsStage {
    param([string]$Directory)
    [void](Get-Deep32PlainPath -Path $Directory -Directory)
    foreach ($name in @('plan.json', 'settings.json', 'settings-deployment.json', 'deployment.json', 'forge-evidence.json')) {
        $path = Join-Path $Directory $name
        [void](Get-Deep32PlainPath -Path $path -MayNotExist)
        if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
    }
    [IO.Directory]::Delete($Directory, $false)
}

function Assert-Deep32InstallReady {
    param([hashtable]$SettingsPlan, [switch]$RawMedia)
    if ($null -eq $SettingsPlan -or -not $SettingsPlan.plan -or -not $SettingsPlan.path) {
        throw 'Validated settings and plan consent are required before the Docker Desktop loader.'
    }
    if ((-not $RawMedia -and -not (Get-Command Mount-DiskImage -ErrorAction SilentlyContinue)) -or
        -not (Get-Command docker.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Disc 3 installation needs local media access and Docker Desktop using Linux containers with its WSL2 backend and NVIDIA GPU support.'
    }
}

function Get-Deep32MountedKit {
    param($Attachment)
    $volumes = @($Attachment | Get-Volume -ErrorAction Stop | Where-Object {
        $null -ne $_.DriveLetter -and [string]$_.DriveLetter -ne ''
    })
    if ($volumes.Count -ne 1) { throw 'The verified disc needs one readable drive letter.' }
    $discRoot = [string]$volumes[0].DriveLetter + ':\'
    return Get-Deep32PlainPath -Path $discRoot -Directory
}

function Get-Deep32DockerLoaderArguments {
    param([string]$Kit, [string]$Payload, [hashtable]$SettingsPlan)
    $provision = Get-Deep32PlainPath -Path (Join-Path $Kit 'forms\provision.py')
    $settings = Get-Deep32PlainPath -Path (Join-Path $SettingsPlan.directory 'settings.json')
    $identity = $SettingsPlan.plan.identity
    $arguments = @($provision, '--form', 'docker', '--kit', $Kit,
        '--platform', 'windows', '--docker-role', $identity.docker_role, '--payload', $Payload,
        '--settings', $settings, '--data-root', $identity.data_root)
    if ($identity.docker_role -ceq 'full') {
        $content = Get-Deep32PlainPath -Path $identity.content -Directory
        $arguments += @('--content', $content)
    }
    if ($identity.PSObject.Properties['docker_storage_path']) {
        $arguments += @('--docker-storage-path', $identity.docker_storage_path)
    }
    return $arguments
}

function Invoke-Deep32Install {
    param([string]$Image, [string]$Destination, [hashtable]$SettingsPlan)
    Assert-Deep32InstallReady -SettingsPlan $SettingsPlan
    $attachment = Get-DiskImage -ImagePath $Image -ErrorAction Stop
    $acquired = $false
    $loaderStarted = $false
    try {
        if (-not $attachment.Attached) {
            $attachment = Mount-DiskImage -ImagePath $Image -Access ReadOnly -PassThru -ErrorAction Stop
            $acquired = $true
        }
        $kit = Get-Deep32MountedKit -Attachment $attachment
        $payload = Get-Deep32PlainPath -Path (Join-Path $kit 'deep32\payload') -Directory
        $loaderArguments = @(Get-Deep32DockerLoaderArguments -Kit $kit -Payload $payload -SettingsPlan $SettingsPlan)
        Test-Deep32InstallPrerequisites -Journey $SettingsPlan.plan.identity.journey
        $loaderStarted = $true
        Invoke-Deep32DockerLoader -Arguments $loaderArguments
    } finally {
        # Both roles bind verified model files from the media. Retain the
        # attachment after handoff, including interruption, for start/resume.
        if ($acquired -and -not $loaderStarted) { Dismount-DiskImage -ImagePath $Image -ErrorAction Stop }
    }
    Write-Output 'Disc 3 Docker Desktop loader returned; settings handoff completed.'
    Write-Output 'Keep the verified GPU pack media attached for start and resume; full also retains content media.'
    Write-Output 'Next: review the GPU pack status and the host publication request before authorising Tailscale Serve.'
}

function Invoke-Deep32WindowsIsoJoin {
    param([string[]]$Arguments)
    $python = Get-Deep32Python
    & $python -I -B (Join-Path $script:Deep32ScriptDirectory 'deep32_download.py') @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'The verified Forge media could not be read and joined.' }
}

function Invoke-Deep32SplitInstall {
    param([object[]]$MediaComponents, [hashtable]$SettingsPlan, [hashtable]$Evidence)
    Assert-Deep32InstallReady -SettingsPlan $SettingsPlan -RawMedia
    if ($MediaComponents.Count -ne 3 -or -not $SettingsPlan.accepted) {
        throw 'Validated plan consent and three verified Forge media components are required.'
    }
    $identity = $SettingsPlan.plan.identity
    $tailnet = $identity.PSObject.Properties['tailnet_sha256']
    if (-not $Evidence -or -not $Evidence.source -or -not $tailnet -or $tailnet.Value -cnotmatch '^[0-9a-f]{64}$') {
        throw 'A local signed release folder and a reviewed tailnet are required for split Windows Forge installation.'
    }
    $tar = Get-Deep32PlainPath -Path (Join-Path ([Environment]::GetFolderPath('System')) 'tar.exe')
    Test-Deep32SplitForgePrerequisites -Evidence $Evidence -SettingsPlan $SettingsPlan
    $handles = [Collections.Generic.List[IO.FileStream]]::new()
    $ownedWorkspace = $null
    try {
        foreach ($media in $MediaComponents) {
            $handles.Add((Open-Deep32VerifiedMedia -Path $media.image -Source $Evidence.source -Size $media.disc.size -Hash $media.disc.sha256))
        }
        $paths = @(Invoke-Deep32Settings -Arguments @('media-cache', '--plan', $SettingsPlan.path,
            '--accept-plan', $SettingsPlan.accepted))
        if ($paths.Count -ne 1) { throw 'The verified media cache could not be selected.' }
        $output = Get-Deep32PlainPath -Path $paths[0] -Directory -MayNotExist
        $cache = Get-Deep32PlainPath -Path ([IO.Path]::GetDirectoryName($output)) -Directory
        $workspace = Join-Path $cache ('.windows-iso-' + [Guid]::NewGuid().ToString('N'))
        if ([IO.File]::Exists($workspace) -or [IO.Directory]::Exists($workspace)) {
            throw 'The Windows ISO workspace name was already in use.'
        }
        [void][IO.Directory]::CreateDirectory($workspace)
        $ownedWorkspace = $workspace
        try {
            Set-Deep32PrivatePath -Path $workspace -Directory -Inherit
            $joinArguments = @('--core', $handles[0].Name, '--embedder', $handles[1].Name, '--ingestion', $handles[2].Name,
                '--gpu-class', $MediaComponents[1].component.Substring(9), '--output', $output, '--data-root', $identity.data_root,
                '--windows-workspace', $workspace, '--bsdtar', $tar)
            Invoke-Deep32WindowsIsoJoin -Arguments $joinArguments
            $payload = Get-Deep32PlainPath -Path (Join-Path $output 'payload') -Directory
            $kit = Get-Deep32PlainPath -Path (Join-Path $workspace 'kit') -Directory
            $loaderArguments = @(Get-Deep32DockerLoaderArguments -Kit $kit -Payload $payload -SettingsPlan $SettingsPlan)
            Test-Deep32SplitForgePrerequisites -Evidence $Evidence -SettingsPlan $SettingsPlan
            Invoke-Deep32DockerLoader -Arguments $loaderArguments
        } finally {
            if ($ownedWorkspace) {
                $cleanup = Get-Deep32PlainPath -Path $ownedWorkspace -Directory
                if ([IO.Path]::GetDirectoryName($cleanup) -ine $cache -or
                        [IO.Path]::GetFileName($cleanup) -cnotmatch '^\.windows-iso-[0-9a-f]{32}$') {
                    throw 'The owned Windows ISO workspace could not be identified for cleanup.'
                }
                Remove-Item -LiteralPath $cleanup -Recurse -Force -ErrorAction Stop
            }
        }
    } finally {
        foreach ($handle in $handles) { $handle.Dispose() }
    }
    Write-Output 'Disc 3 Docker Desktop loader returned; settings handoff completed.'
    Write-Output "Keep the joined verified media cache for recovery: $output"
    Write-Output 'Next: review the GPU pack status and the host publication request before authorizing Tailscale Serve.'
}

function Invoke-Deep32Main {
    param([string[]]$Arguments)
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $role = $null
    $destination = Join-Path (Get-Location).Path 'deep32-downloads'
    $release = 'v0.1.0-alpha.3'
    $key = Join-Path $script:Deep32ScriptDirectory '..\..\install\release-key.gpg'
    $source = $null
    $repository = 'hellobpdu/deep32'
    $gpuClass = ''
    $ingestion = $false
    $full = $false
    $install = $false
    $gpg = ''; $fingerprint = 'B205B406548627AABF5250D8A078C5F25D025DD6'; $prepareVm = $false; $hypervisor = ''
    $installOptions = @{ journey = ''; form = 'docker'; platform = 'windows'; docker_role = 'forge'; data_root = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Deep32'); content = ''; docker_storage_path = '';
        non_interactive = $false; accept_plan = ''; confirm_tailnet = ''; desktop_isolation_confirmed = $false; settings_arguments = @() }
    $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $argument = $Arguments[$index]
        # PowerShell's native spelling and the portable long options share the
        # same duplicate/ambiguity checks, including the source alias.
        if ($argument -ieq '-From') { $argument = '--from' }
        elseif ($argument -ieq '-Source') { $argument = '--source' }
        elseif ($argument -ieq '-To') { $argument = '--to' }
        if ($argument -cin @('--help', '-h')) {
            Write-Output 'deep32-get.ps1 disc1|disc2|disc3 [--to DIR | --from DIR] [--release TAG] [--repo OWNER/NAME] [--key PUBLIC.asc|PUBLIC.gpg] [--gpg FILE] [--fingerprint HEX] [--gpu-class CLASS] [--ingestion] [--full]';
            Write-Output '--from DIR (PowerShell -From) verifies local media in place without copying or network; --source is its alias.';
            Write-Output 'disc1 --prepare-vm --hypervisor hyper-v|kvm writes artefacts and commands only.';
            Write-Output 'disc3 --install --journey J1|J2|J3|J4 [--data-root DIR] [--docker-storage-path DIR] [--settings FILE] [--node-name NAME --user-name NAME --time-zone ZONE --keyboard LAYOUT --profile ID --transport tailnet|loopback --publish serve-https] [--form docker --platform windows --docker-role forge|full] [--content DIR (full only)] [--confirm-tailnet SUFFIX] [--confirm-desktop-isolation] [--non-interactive --accept-plan DIGEST]'; return
        } elseif ($argument -cin @('--install', '--non-interactive', '--prepare-vm', '--ingestion', '--full', '--confirm-desktop-isolation')) {
            if (-not $seen.Add($argument)) { throw 'Duplicate option.' }
            if ($argument -ceq '--install') { $install = $true }
            elseif ($argument -ceq '--prepare-vm') { $prepareVm = $true }
            elseif ($argument -ceq '--ingestion') { $ingestion = $true }
            elseif ($argument -ceq '--full') { $full = $true }
            elseif ($argument -ceq '--confirm-desktop-isolation') { $installOptions.desktop_isolation_confirmed = $true }
            else { $installOptions.non_interactive = $true }
        } elseif ($argument -cin @('--status', '--resume', '--remove', '--pair-headend', '--pairing-console')) {
            Write-Output 'This lifecycle action needs Phase B media. Next: use the status and recovery commands in src/gpu-pack/PACK.md.'
            exit 10
        } elseif ($argument -cin @('--to', '--from', '--release', '--repo', '--gpu-class', '--key', '--source', '--gpg', '--fingerprint', '--hypervisor', '--journey', '--settings', '--node-name',
            '--user-name', '--time-zone', '--keyboard', '--profile', '--transport', '--publish', '--form', '--platform', '--docker-role', '--accept-plan', '--confirm-tailnet', '--data-root', '--content', '--docker-storage-path')) {
            if (-not $seen.Add($argument) -or $index + 1 -ge $Arguments.Count) { throw "Missing value or duplicate option: $argument" }
            $index++
            $value = $Arguments[$index]
            if ($value.Length -eq 0) { throw "Missing value: $argument" }
            if ($value -match '[\x00-\x1f\x7f]') { throw "Control characters are refused in option values: $argument" }
            switch -CaseSensitive ($argument) {
                '--to' { $destination = $value }
                '--release' { $release = $value }
                '--repo' { $repository = $value }
                '--gpu-class' { $gpuClass = $value }
                '--key' { $key = $value }
                '--from' { $source = $value }
                '--source' { $source = $value }
                '--gpg' { $gpg = $value }
                '--fingerprint' { $fingerprint = $value }
                '--hypervisor' { $hypervisor = $value }
                '--journey' { $installOptions.journey = $value }
                '--form' { $installOptions.form = $value }
                '--platform' { $installOptions.platform = $value }
                '--docker-role' { $installOptions.docker_role = $value }
                '--data-root' { $installOptions.data_root = $value }
                '--content' { $installOptions.content = $value }
                '--docker-storage-path' { $installOptions.docker_storage_path = $value }
                '--accept-plan' { $installOptions.accept_plan = $value }
                '--confirm-tailnet' { $installOptions.confirm_tailnet = $value }
                default { $installOptions.settings_arguments += @($argument, $value) }
            }
        } elseif ($null -eq $role -and $argument -cin @('disc1', 'disc2', 'disc3')) {
            $role = $argument
        } else {
            throw 'Usage: deep32-get.ps1 disc1|disc2|disc3 [--to DIR | --from DIR] [--release v0.1.0-alpha.3] [--install] [--key FILE]'
        }
    }
    if ($null -eq $role) { throw 'Select disc1, disc2 or disc3.' }
    if ($seen.Contains('--from') -and $seen.Contains('--source')) { throw 'Select only one of --from and --source.' }
    if ($null -ne $source -and $seen.Contains('--to')) { throw '--from/--source and explicit --to cannot be combined.' }
    if (($gpuClass -or $ingestion -or $full) -and $role -cne 'disc3') { throw 'GPU class and add-on selection apply only to disc3.' }
    if ($release -cnotmatch '^v[A-Za-z0-9][A-Za-z0-9._-]*$' -or $release.Contains('..')) { throw 'The release tag is unsafe.' }
    if ($repository -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$' -or
        $repository.Contains('..')) { throw 'The release repository is unsafe.' }
    if ($install -and $role -cne 'disc3') { throw '--install is supported only for disc3.' }
    $splitInstall = $install -and $release -cne 'v0.1.0-alpha.1'
    if ($installOptions.confirm_tailnet -and -not $splitInstall) { throw '--confirm-tailnet applies only to split Windows Forge installation.' }
    if ($installOptions.desktop_isolation_confirmed -and -not $splitInstall) { throw '--confirm-desktop-isolation applies only to split Windows Forge installation.' }
    if ($role -ceq 'disc3' -and $release -cne 'v0.1.0-alpha.1' -and -not $gpuClass) { throw 'disc3 requires the observed --gpu-class; no class fallback is permitted.' }
    if ($installOptions.journey -and $installOptions.journey -cnotmatch '^J[1-5]$') { throw 'Invalid journey.' }
    if ($install) {
        if ($installOptions.journey -cnotmatch '^J[1234]$') { throw 'Select --journey J1, J2, J3 or J4 for the GPU loader.' }
        if ($installOptions.form -cne 'docker' -or $installOptions.platform -cne 'windows' -or $installOptions.docker_role -cnotin @('forge', 'full')) {
            Write-Output 'Windows GPU installation uses Docker Desktop. Next: select --form docker --platform windows with Forge (default) or explicit --docker-role full.'
            exit 10
        }
        if ($installOptions.docker_role -ceq 'full') {
            if (-not $installOptions.content) { throw 'Select --content DIR containing the signed content medium for the full Docker role.' }
            $installOptions.content = Get-Deep32PlainPath -Path $installOptions.content -Directory
        } elseif ($installOptions.journey -ceq 'J3' -or $installOptions.content) {
            throw 'J3 and --content require the explicit --docker-role full choice.'
        }
        if ($splitInstall -and ($installOptions.docker_role -cne 'forge' -or $full)) {
            throw 'Split disc 3 installation supports the Forge role.'
        }
        if ($splitInstall) {
            if (-not $installOptions.desktop_isolation_confirmed) { throw 'Supply --confirm-desktop-isolation after reviewing Docker Desktop WSL integration for unrelated distributions.' }
            if (-not $installOptions.docker_storage_path) { throw 'Select the exact existing Docker Desktop backing directory with --docker-storage-path.' }
            Assert-Deep32SplitLocalPath $installOptions.data_root
            Assert-Deep32SplitLocalPath $installOptions.docker_storage_path
            if ($source -and $source -notmatch '^file:') { Assert-Deep32SplitLocalPath $source }
        }
        $installOptions.data_root = Get-Deep32PlainPath -Path $installOptions.data_root -Directory -MayNotExist
        if ($installOptions.docker_storage_path) { $installOptions.docker_storage_path = Get-Deep32PlainPath -Path $installOptions.docker_storage_path -Directory }
    }
    if ($prepareVm -and ($role -cne 'disc1' -or $hypervisor -cnotin @('hyper-v', 'kvm'))) { throw '--prepare-vm requires disc1 --hypervisor hyper-v|kvm.' }
    if ($hypervisor -and -not $prepareVm) { throw '--hypervisor requires --prepare-vm.' }
    if ($install -and -not $splitInstall) { Test-Deep32InstallPrerequisites -Journey $installOptions.journey }
    if ($splitInstall) {
        if (-not $source) {
            throw 'Download disc 3 with --gpu-class CLASS --ingestion as a standard user, then install from the signed release folder in elevated PowerShell with --from DIR.'
        }
        if (-not $installOptions.confirm_tailnet -and ($installOptions.non_interactive -or [Console]::IsInputRedirected)) {
            throw 'Confirm the intended tailnet with --confirm-tailnet EXPECTED_SUFFIX for non-interactive split Windows Forge installation.'
        }
        $python = Get-Deep32Python
        & $python -I -B -c 'import sys; sys.exit(2 if not sys.executable.isascii() else int(sys.version_info < (3, 12)))'
        if ($LASTEXITCODE -eq 2) {
            throw 'Choose DEEP32_PYTHON at an ASCII-only path for the retained Forge startup hook.'
        }
        if ($LASTEXITCODE -ne 0) {
            throw 'Split Forge installation requires Windows Python 3.12 or later. Set DEEP32_PYTHON to that interpreter and retry.'
        }
        if ($installOptions.data_root -match '[^\x00-\x7f]') {
            throw 'Choose --data-root at an ASCII-only path for the retained Forge startup hook.'
        }
        if ($installOptions.docker_storage_path -match '[^\x00-\x7f]') {
            throw 'Specify the existing ASCII-only Docker Desktop backing directory with --docker-storage-path for the retained Forge startup hook.'
        }
        $ingestion = $true
        Write-Output 'Selecting the signed ingestion image required for split Forge installation.'
    }
    $script:Deep32Source = $null
    $script:Deep32Release = $release
    $script:Deep32ReleaseBase = 'https://github.com/' + $repository + '/releases/download'
    if ($null -ne $source) {
        if ($source -match '[\x00-\x1f\x7f]') { throw '--from/--source must not contain control characters.' }
        if ($source -match '^file:') {
            $uri = [Uri]$source
            if (-not $uri.IsFile -or $uri.IsUnc -or ($uri.Host -and $uri.Host -cne 'localhost') -or $uri.Query -or $uri.Fragment) {
                throw '--source file URLs must name a local folder.'
            }
            $source = $uri.LocalPath
        } elseif ($source -match '^[A-Za-z][A-Za-z0-9+.-]*://' -or $source.StartsWith('\\') -or $source.StartsWith('//')) {
            throw '--source accepts only a local folder or file URL.'
        }
        if ($splitInstall) { Assert-Deep32SplitLocalPath $source }
        if ([IO.Path]::GetFullPath($source).StartsWith('\\')) { throw '--source must be a local folder.' }
        $script:Deep32Source = Get-Deep32PlainPath -Path $source -Directory
    }
    $key = [IO.Path]::GetFullPath($key)
    if ($script:Deep32Source -or $release -cne 'v0.1.0-alpha.1') {
        $wholeArguments = @($role, '--release', $release, '--repo', $repository, '--key', $key, '--fingerprint', $fingerprint)
        if ($gpg) { $wholeArguments += @('--gpg', $gpg) }
        if ($gpuClass) { $wholeArguments += @('--gpu-class', $gpuClass) }
        if ($ingestion) { $wholeArguments += '--ingestion' }
        if ($full) { $wholeArguments += '--full' }
        if ($script:Deep32Source) {
            $wholeArguments += @('--from', $script:Deep32Source)
            # Local verification itself never creates a destination. VM or
            # installation stages may create their explicit output afterwards.
            if ($prepareVm -or $install) { $destination = Get-Deep32PlainPath -Path $destination -Directory -MayNotExist }
        } else {
            $destination = New-Deep32Directory $destination
            $cache = New-Deep32Directory (Join-Path $destination ('.deep32-' + $release))
            $wholeArguments += @('--to', $destination, '--cache', $cache)
        }
        $verified = @(Invoke-Deep32WholeHelper -Arguments $wholeArguments -Multiple:($role -ceq 'disc3' -and $release -cne 'v0.1.0-alpha.1'))
        $image = $verified[0].image
        $disc = $verified[0].disc
        if ($splitInstall) {
            if ($script:Deep32Source) {
                $destination = Get-Deep32PlainPath -Path ([IO.Path]::GetDirectoryName($image)) -Directory
            }
            $components = @('core', "embedder-$gpuClass", 'ingestion')
            if ($verified.Count -ne $components.Count) {
                throw 'The signed selection must include the core, selected embedder and ingestion images.'
            }
            $mediaComponents = @(for ($index = 0; $index -lt $components.Count; $index++) {
                @{ component = $components[$index]; image = $verified[$index].image; disc = $verified[$index].disc }
            })
        }
    } else {
    if ($gpuClass -or $ingestion -or $full) { throw 'GPU class and add-on selection require whole-image release metadata.' }
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
    Confirm-Deep32Signature -Cache $cache -Key $key -Fingerprint $fingerprint -Gpg $gpg
    $disc = Get-Deep32Manifest -Cache $cache -Role $role -Release $release
    if ($null -eq $disc) {
        Write-Output ($role + ' is not in this release yet; it will be added to the same release. Check the release page.')
        exit 3
    }
    Write-Output 'Release signature and manifest verified.'
    $storageArguments = @('storage', (Join-Path $cache 'ASSEMBLY.json'), (Join-Path $cache 'SHA256SUMS'), $role, $release, $destination, $cache, 'ps1')
    if ($hypervisor) { $storageArguments += $hypervisor }
    Invoke-Deep32ManifestHelper -Arguments $storageArguments
    Invoke-Deep32ManifestHelper -Arguments @('publication-check', $destination)
    Invoke-Deep32ManifestHelper -Arguments @('publication-check', $cache)
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
        $joined = Join-Path $destination ($disc.file + '.joining')
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
    }
    # A source medium can disappear or change after initial verification. Check
    # it again immediately before handing it to a mutating downstream stage.
    if ($prepareVm) {
        Assert-Deep32File $image $disc.size $disc.sha256
        $destination = New-Deep32Directory $destination
        Invoke-Deep32ManifestHelper -Arguments @('prepare-vm', $image, ([string]$disc.size), $disc.sha256, $hypervisor, $destination)
    } elseif ($splitInstall) {
        $policy = @{ DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'; DOCKER_CONTEXT = $null;
            DOCKER_TLS = $null; DOCKER_TLS_VERIFY = $null; DOCKER_CERT_PATH = $null; TS_SOCKET = $null }
        $previous = @{}
        foreach ($name in $policy.Keys) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
        try {
            foreach ($name in $policy.Keys) { [Environment]::SetEnvironmentVariable($name, $policy[$name], 'Process') }
            $evidence = @{ source = $script:Deep32Source; gpu_class = $gpuClass; key = $key; fingerprint = $fingerprint; gpg = $gpg }
            $settingsPlan = New-Deep32SettingsPlan -ImageHash $disc.sha256 -Destination $destination -Options $installOptions -MediaComponents $mediaComponents
            try { Invoke-Deep32SplitInstall -MediaComponents $mediaComponents -SettingsPlan $settingsPlan -Evidence $evidence }
            finally { Remove-Deep32SettingsStage -Directory $settingsPlan.directory }
        } finally {
            foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
        }
    } elseif ($install) {
        $settingsPlan = New-Deep32SettingsPlan -ImageHash $disc.sha256 -Destination $destination -Options $installOptions
        try {
            Assert-Deep32File $image $disc.size $disc.sha256
            Invoke-Deep32Install -Image $image -Destination $destination -SettingsPlan $settingsPlan
        }
        finally { Remove-Deep32SettingsStage -Directory $settingsPlan.directory }
    }
    elseif ($role -ceq 'disc1') { Write-Output 'Write the verified disc 1 image to a USB stick, then boot from that USB stick.' }
    elseif ($role -ceq 'disc2') { Write-Output 'Attach the verified disc 2 content image in Deep32 Workstation and import its content.' }
    else { Write-Output 'Selected GPU capability packs verified.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-Deep32Main -Arguments $args }
    catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
}
