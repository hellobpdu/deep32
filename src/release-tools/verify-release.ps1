param(
    [Parameter(Mandatory = $true)][string]$ReleaseDirectory,
    [Parameter(Mandatory = $true)][string]$Fingerprint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-SafeName([string]$Name) {
    if ($Name.Length -gt 200 -or $Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $Name.Contains('..')) {
        throw 'Invalid flat file name.'
    }
}

function Assert-PlainPath([string]$Path, [bool]$Directory) {
    $full = [IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        $attributes = [IO.File]::GetAttributes($cursor)
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Links and reparse points are not accepted.' }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $attributes = [IO.File]::GetAttributes($full)
    if ((($attributes -band [IO.FileAttributes]::Directory) -ne 0) -ne $Directory) { throw 'Unexpected file type.' }
    return $full
}

function Read-AsciiLines([string]$Path) {
    if (([IO.FileInfo]$Path).Length -gt 1048576) { throw 'Metadata file is too large.' }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10) { throw 'Expected LF-terminated text.' }
    foreach ($value in $bytes) {
        if (($value -lt 32 -and $value -ne 9 -and $value -ne 10) -or $value -gt 126) { throw 'Expected ASCII text with LF line endings.' }
    }
    $text = [Text.Encoding]::ASCII.GetString($bytes)
    return ,$text.Substring(0, $text.Length - 1).Split([char]10)
}

function Get-FileHashNative([string]$Path) {
    [void](Assert-PlainPath $Path $false)
    $stream = $null
    $hash = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $hash = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $hash) { $hash.Dispose() }
    }
}

function Read-Checksums([string]$Path) {
    $entries = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($line in (Read-AsciiLines $Path)) {
        if ($line -cnotmatch '^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$') { throw 'Malformed checksum line.' }
        $digest = $Matches[1]
        $name = $Matches[2]
        Assert-SafeName $name
        if ($entries.ContainsKey($name)) { throw ('Duplicate checksum entry: ' + $name) }
        $entries.Add($name, $digest)
    }
    if ($entries.Count -eq 0) { throw 'Empty checksum file.' }
    return ,$entries
}

function Read-PositiveSize([string]$Text) {
    if ($Text -cnotmatch '^[1-9][0-9]*$') { throw 'Invalid positive byte count.' }
    $value = [long]0
    if (-not [long]::TryParse($Text, [ref]$value)) { throw 'Byte count is out of range.' }
    return $value
}

function Read-PartsManifest([string]$Path) {
    $lines = Read-AsciiLines $Path
    if ($lines.Count -lt 3 -or $lines.Count -gt 1001 -or $lines[0] -cne 'deep32-parts-v1') { throw 'Invalid parts manifest header or length.' }
    $file = $lines[1].Split([char]9)
    if ($file.Count -ne 4 -or $file[0] -cne 'file' -or $file[3] -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid joined-file declaration.' }
    Assert-SafeName $file[1]
    $fileSize = Read-PositiveSize $file[2]
    if ([IO.Path]::GetFileName($Path) -cne ($file[1] + '.parts.tsv')) { throw 'Manifest name does not match its image.' }
    $parts = New-Object 'System.Collections.Generic.List[object]'
    $total = [long]0
    $firstSize = [long]0
    for ($index = 2; $index -lt $lines.Count; $index++) {
        $fields = $lines[$index].Split([char]9)
        $number = $index - 1
        if ($fields.Count -ne 4 -or $fields[0] -cne 'part' -or $fields[3] -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid part declaration.' }
        Assert-SafeName $fields[1]
        $expectedName = $file[1] + '.part' + $number.ToString('000', [Globalization.CultureInfo]::InvariantCulture)
        if ($fields[1] -cne $expectedName) { throw 'Part names must be ordered and contiguous from 001.' }
        $size = Read-PositiveSize $fields[2]
        if ($size -gt 1900000000) { throw 'Part exceeds the release asset size cap.' }
        if ($number -eq 1) { $firstSize = $size }
        if (($index -lt $lines.Count - 1 -and $size -ne $firstSize) -or $size -gt $firstSize) { throw 'Part sizes are inconsistent.' }
        if ($size -gt ($fileSize - $total)) { throw 'Part sizes exceed the joined-file size.' }
        $total += $size
        $parts.Add([pscustomobject]@{ Name = $fields[1]; Size = $size; Hash = $fields[3] })
    }
    if ($total -ne $fileSize) { throw 'Part sizes do not add up to the joined-file size.' }
    return [pscustomobject]@{ Name = $file[1]; Size = $fileSize; Hash = $file[3]; Parts = $parts }
}

function Read-AssemblyState([string]$Path) {
    # The caller has already checked this file against the signed root hashes.
    $assemblyText = (Read-AsciiLines $Path) -join "`n"
    if (-not $assemblyText.TrimStart().StartsWith('{', [StringComparison]::Ordinal)) { throw 'The assembly manifest must be an object.' }
    if ($PSVersionTable.PSVersion.Major -le 5) {
        Add-Type -AssemblyName System.Web.Extensions
        $serializer = New-Object Web.Script.Serialization.JavaScriptSerializer
        $serializer.MaxJsonLength = 1048576
        $manifest = $serializer.DeserializeObject($assemblyText)
    } else {
        $manifest = ConvertFrom-Json -InputObject $assemblyText -AsHashtable
    }
    if ($manifest -isnot [Collections.IDictionary] -or
        $manifest.format -cne 'deep32-release-assembly' -or
        ($manifest.format_version -isnot [int] -and $manifest.format_version -isnot [long]) -or
        $manifest.format_version -ne 1 -or
        $manifest.version -isnot [string]) { throw 'Invalid assembly manifest format.' }
    Assert-SafeName $manifest.version
    $pending = @()
    if ($manifest.ContainsKey('pending')) {
        if ($manifest.pending -isnot [object[]]) { throw 'Assembly pending roles must be an array.' }
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
    $prefixes = New-Object 'Collections.Generic.Dictionary[string,object]'
    if ($manifest.ContainsKey('pending_prefixes')) {
        if ($manifest.pending_prefixes -isnot [Collections.IDictionary]) {
            throw 'Assembly pending prefixes must be an object.'
        }
        $prefixes = $manifest.pending_prefixes
    }
    if ($prefixes.Count -ne $pending.Count) { throw 'Pending prefixes do not match the pending roles.' }
    $pendingPrefixValues = New-Object 'Collections.Generic.List[string]'
    foreach ($pendingRole in $pending) {
        $kind = @{disc2 = 'content'; disc3 = 'gpu-pack'}[$pendingRole]
        $pendingPrefix = 'deep32-' + $manifest.version + '-' + $pendingRole + '-' + $kind
        if (-not $prefixes.ContainsKey($pendingRole) -or $prefixes[$pendingRole] -isnot [string] -or
            $prefixes[$pendingRole] -cne $pendingPrefix) { throw ('The pending ' + $pendingRole + ' prefix is invalid.') }
        $pendingPrefixValues.Add($pendingPrefix)
    }
    if ($manifest.discs -isnot [object[]] -or $manifest.discs.Count -ne 3 - $pending.Count) {
        throw 'The assembly must cover all present and pending disc roles.'
    }
    $roles = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $children = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
    foreach ($disc in $manifest.discs) {
        if ($disc -isnot [Collections.IDictionary] -or
            $disc.role -isnot [string] -or $disc.role -cnotin @('disc1', 'disc2', 'disc3') -or
            $pending -ccontains $disc.role -or -not $roles.Add($disc.role)) {
            throw 'Assembly disc roles are invalid, duplicated or pending.'
        }
        if ($disc.checksums -isnot [string]) { throw 'Invalid assembly disc checksum name.' }
        Assert-SafeName $disc.checksums
        if ($children.ContainsKey($disc.checksums)) { throw 'Assembly disc checksum names are duplicated.' }
        $children.Add($disc.checksums, $disc.role.Substring(4))
    }
    foreach ($expectedRole in @('disc1', 'disc2', 'disc3')) {
        if ($pending -cnotcontains $expectedRole -and -not $roles.Contains($expectedRole)) {
            throw 'The assembly must cover all present and pending disc roles.'
        }
    }
    return [pscustomobject]@{
        Children = $children
        PendingPrefixes = $pendingPrefixValues.ToArray()
        PendingRoles = @($pending | Sort-Object)
        PendingCount = $pending.Count
    }
}
function Assert-Signature([string]$ChecksumPath, [string]$ExpectedFingerprint, [string]$GpgPath) {
    $signaturePath = Assert-PlainPath ($ChecksumPath + '.sig') $false
    [void](Assert-PlainPath $ChecksumPath $false)
    # Capture native stderr as ErrorRecord objects; only stdout contains status-fd 1.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0
        $status = @(& $GpgPath --batch --no-auto-key-retrieve --status-fd 1 --verify $signaturePath $ChecksumPath 2>&1)
        $nativeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($nativeExit -ne 0) { throw ('Signature verification failed with gpg exit code ' + $nativeExit + '.') }
    $validCount = 0
    foreach ($item in $status) {
        if ($item -isnot [string]) { continue }
        if ($item -cmatch '^\[GNUPG:\] VALIDSIG(?: |$)') {
            $fields = $item.Split([char]32)
            if (($fields.Count -ne 11 -and $fields.Count -ne 12) -or $fields[2] -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$') {
                throw 'Malformed gpg VALIDSIG status.'
            }
            $primary = $fields[2]
            if ($fields.Count -eq 12) { $primary = $fields[11] }
            if ($primary -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$' -or $primary.ToUpperInvariant() -cne $ExpectedFingerprint) {
                throw 'Signature primary fingerprint does not match.'
            }
            $validCount++
        }
    }
    if ($validCount -ne 1) { throw 'gpg must report exactly one valid signature.' }
}

try {
    if ($Fingerprint -cnotmatch '^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$') { throw 'Expected a 40- or 64-digit primary fingerprint.' }
    $expectedFingerprint = $Fingerprint.ToUpperInvariant()
    $releasePath = Assert-PlainPath $ReleaseDirectory $true
    $gpgCommand = Get-Command gpg -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $gpgPath = $gpgCommand.Source
    $rootPath = [IO.Path]::Combine($releasePath, 'SHA256SUMS')
    Assert-Signature $rootPath $expectedFingerprint $gpgPath
    $root = Read-Checksums $rootPath
    $files = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in [IO.Directory]::GetFileSystemEntries($releasePath)) {
        $full = Assert-PlainPath $path $false
        $name = [IO.Path]::GetFileName($full)
        Assert-SafeName $name
        if ($files.ContainsKey($name)) { throw 'Release has colliding file names.' }
        $files.Add($name, $full)
    }
    foreach ($name in $root.Keys) {
        if ($name -ceq 'SHA256SUMS' -or $name.EndsWith('.sig', [StringComparison]::OrdinalIgnoreCase)) { throw 'Root checksum contains an excluded file.' }
        if (-not $files.ContainsKey($name) -or [IO.Path]::GetFileName($files[$name]) -cne $name) { throw ('Missing or case-mismatched file: ' + $name) }
        if ((Get-FileHashNative $files[$name]) -cne $root[$name]) { throw ('Checksum failed: ' + $name) }
    }
    $assemblyState = $null
    if ($files.ContainsKey('ASSEMBLY.json')) {
        if (-not $root.ContainsKey('ASSEMBLY.json') -or
            [IO.Path]::GetFileName($files['ASSEMBLY.json']) -cne 'ASSEMBLY.json') {
            throw 'ASSEMBLY.json is absent from the signed root checksums or has the wrong case.'
        }
        $assemblyState = Read-AssemblyState $files['ASSEMBLY.json']
        foreach ($pendingPrefix in $assemblyState.PendingPrefixes) {
            foreach ($name in $files.Keys) {
                if ($name.StartsWith($pendingPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'A pending disc has release assets.'
                }
            }
        }
    }
    $manifests = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)
    $joined = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($name in $files.Keys) {
        if (-not $name.EndsWith('.parts.tsv', [StringComparison]::Ordinal)) { continue }
        if (-not $root.ContainsKey($name)) { throw ('Manifest is absent from root checksums: ' + $name) }
        $manifest = Read-PartsManifest $files[$name]
        if ($joined.ContainsKey($manifest.Name)) { throw 'Duplicate joined-image declaration.' }
        if ($root.ContainsKey($manifest.Name)) { throw 'Root checksum contains a joined image.' }
        $manifests.Add($name, $manifest)
        $joined.Add($manifest.Name, $manifest)
        foreach ($part in $manifest.Parts) {
            if (-not $root.ContainsKey($part.Name) -or $root[$part.Name] -cne $part.Hash -or ([IO.FileInfo]$files[$part.Name]).Length -ne $part.Size) {
                throw ('Manifest part differs from root checksums: ' + $part.Name)
            }
        }
        if ($files.ContainsKey($manifest.Name)) {
            if (([IO.FileInfo]$files[$manifest.Name]).Length -ne $manifest.Size -or (Get-FileHashNative $files[$manifest.Name]) -cne $manifest.Hash) {
                throw ('Joined image verification failed: ' + $manifest.Name)
            }
        }
    }
    foreach ($name in $files.Keys) {
        if ($name -ceq 'SHA256SUMS' -or $name.EndsWith('.sig', [StringComparison]::OrdinalIgnoreCase) -or $joined.ContainsKey($name)) { continue }
        if (-not $root.ContainsKey($name)) { throw ('File is absent from root checksums: ' + $name) }
    }
    $children = @($files.Keys | Where-Object { $_.EndsWith('-SHA256SUMS', [StringComparison]::Ordinal) } | Sort-Object)
    $expectedChildCount = 3
    if ($null -ne $assemblyState) { $expectedChildCount = 3 - $assemblyState.PendingCount }
    if ($children.Count -ne $expectedChildCount) { throw 'Unexpected number of per-disc checksum files.' }
    $allowedSignatures = @('SHA256SUMS.sig') + @($children | ForEach-Object { $_ + '.sig' })
    foreach ($name in $files.Keys) {
        if ($name.EndsWith('.sig', [StringComparison]::OrdinalIgnoreCase) -and $allowedSignatures -cnotcontains $name) {
            throw ('Unexpected signature file: ' + $name)
        }
    }
    $roles = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $childMembers = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    $discPrefixes = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($childName in $children) {
        if ($childName -cnotmatch '^deep32-[A-Za-z0-9._-]+-disc(?:(1)-workstation-[A-Za-z0-9._-]+|(2)-content|(3)-gpu-pack)-SHA256SUMS$') {
            throw 'Invalid per-disc checksum file name.'
        }
        $role = ''
        foreach ($number in 1..3) { if ($Matches.ContainsKey($number) -and $Matches[$number]) { $role = [string]$number } }
        if (-not $roles.Add($role)) { throw 'Duplicate disc role.' }
        if ($null -ne $assemblyState -and
            (-not $assemblyState.Children.ContainsKey($childName) -or $assemblyState.Children[$childName] -cne $role)) {
            throw 'Per-disc checksum role differs from the assembly manifest.'
        }
        if (-not $root.ContainsKey($childName)) { throw 'Per-disc checksum file is absent from root checksums.' }
        Assert-Signature $files[$childName] $expectedFingerprint $gpgPath
        $child = Read-Checksums $files[$childName]
        $prefix = $childName.Substring(0, $childName.Length - '-SHA256SUMS'.Length)
        [void]$discPrefixes.Add($prefix)
        $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        [void]$expected.Add($prefix + '-README.md')
        $imageCandidates = @($prefix + '.iso')
        if ($role -eq '1') { $imageCandidates += $prefix + '.img' }
        $imageCount = 0
        foreach ($image in $imageCandidates) {
            if ($manifests.ContainsKey($image + '.parts.tsv')) {
                $imageCount++
                [void]$expected.Add($image + '.parts.tsv')
                foreach ($part in $manifests[$image + '.parts.tsv'].Parts) { [void]$expected.Add($part.Name) }
            } elseif ($root.ContainsKey($image)) {
                $imageCount++
                [void]$expected.Add($image)
            }
        }
        if ($imageCount -ne 1 -or $child.Count -ne $expected.Count) { throw 'Per-disc checksums do not declare exactly one image and its release files.' }
        foreach ($name in $child.Keys) {
            if (-not $expected.Contains($name) -or -not $root.ContainsKey($name) -or $child[$name] -cne $root[$name]) {
                throw ('Per-disc checksum differs from release files: ' + $name)
            }
            if ((Get-FileHashNative $files[$name]) -cne $child[$name]) { throw ('Per-disc checksum failed: ' + $name) }
            if (-not $childMembers.Add($name)) { throw 'A file belongs to more than one disc.' }
        }
    }
    foreach ($name in $manifests.Keys) {
        if (-not $childMembers.Contains($name)) { throw 'A parts manifest must belong to a disc.' }
    }
    foreach ($name in $root.Keys) {
        if ($name -cmatch '\.part[0-9]{3}$' -and -not $childMembers.Contains($name)) { throw ('A part is absent from its disc checksums: ' + $name) }
        foreach ($prefix in $discPrefixes) {
            if (($name.StartsWith($prefix + '.', [StringComparison]::Ordinal) -or $name.StartsWith($prefix + '-', [StringComparison]::Ordinal)) -and
                $name -cne ($prefix + '-SHA256SUMS') -and -not $childMembers.Contains($name)) {
                throw ('A disc file is absent from its disc checksums: ' + $name)
            }
        }
    }
    if ($null -ne $assemblyState) {
        foreach ($pendingRole in $assemblyState.PendingRoles) {
            Write-Output ($pendingRole + ': pending (not in this release yet)')
        }
    }
    Write-Output 'Release signatures and SHA256 checksums verified.'
    exit 0
} catch {
    [Console]::Error.WriteLine('Release verification failed: ' + $_.Exception.Message)
    exit 1
}
