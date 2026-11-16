param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [string]$OutputDirectory = ''
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
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Links and reparse points are not accepted.'
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $attributes = [IO.File]::GetAttributes($full)
    $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
    if ($isDirectory -ne $Directory) { throw 'Unexpected file type.' }
    return $full
}

function Read-AsciiLines([string]$Path) {
    if (([IO.FileInfo]$Path).Length -gt 1048576) { throw 'Manifest is too large.' }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0 -or $bytes[$bytes.Length - 1] -ne 10) { throw 'Expected LF-terminated text.' }
    foreach ($value in $bytes) {
        if (($value -lt 32 -and $value -ne 9 -and $value -ne 10) -or $value -gt 126) { throw 'Expected ASCII text with LF line endings.' }
    }
    $text = [Text.Encoding]::ASCII.GetString($bytes)
    return ,$text.Substring(0, $text.Length - 1).Split([char]10)
}

function Read-PositiveSize([string]$Text) {
    if ($Text -cnotmatch '^[1-9][0-9]*$') { throw 'Invalid positive byte count.' }
    $value = [long]0
    if (-not [long]::TryParse($Text, [ref]$value)) { throw 'Byte count is out of range.' }
    return $value
}

function Read-PartsManifest([string]$Path) {
    $lines = Read-AsciiLines $Path
    if ($lines.Count -lt 3 -or $lines.Count -gt 1001 -or $lines[0] -cne 'deep32-parts-v1') {
        throw 'Invalid parts manifest header or length.'
    }
    $file = $lines[1].Split([char]9)
    if ($file.Count -ne 4 -or $file[0] -cne 'file' -or $file[3] -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Invalid joined-file declaration.'
    }
    Assert-SafeName $file[1]
    $fileSize = Read-PositiveSize $file[2]
    if ([IO.Path]::GetFileName($Path) -cne ($file[1] + '.parts.tsv')) { throw 'Manifest name does not match its image.' }
    $parts = New-Object 'System.Collections.Generic.List[object]'
    $total = [long]0
    $firstSize = [long]0
    for ($index = 2; $index -lt $lines.Count; $index++) {
        $fields = $lines[$index].Split([char]9)
        $number = $index - 1
        if ($fields.Count -ne 4 -or $fields[0] -cne 'part' -or $fields[3] -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Invalid part declaration.'
        }
        Assert-SafeName $fields[1]
        $expectedName = $file[1] + '.part' + $number.ToString('000', [Globalization.CultureInfo]::InvariantCulture)
        if ($fields[1] -cne $expectedName) { throw 'Part names must be ordered and contiguous from 001.' }
        $size = Read-PositiveSize $fields[2]
        if ($size -gt 1900000000) { throw 'Part exceeds the release asset size cap.' }
        if ($number -eq 1) { $firstSize = $size }
        if (($index -lt $lines.Count - 1 -and $size -ne $firstSize) -or $size -gt $firstSize) {
            throw 'Part sizes are inconsistent.'
        }
        if ($size -gt ($fileSize - $total)) { throw 'Part sizes exceed the joined-file size.' }
        $total += $size
        $parts.Add([pscustomobject]@{ Name = $fields[1]; Size = $size; Hash = $fields[3] })
    }
    if ($total -ne $fileSize) { throw 'Part sizes do not add up to the joined-file size.' }
    return [pscustomobject]@{ Name = $file[1]; Size = $fileSize; Hash = $file[3]; Parts = $parts }
}

function Get-HexHash([byte[]]$Bytes) {
    return [BitConverter]::ToString($Bytes).Replace('-', '').ToLowerInvariant()
}

$temporary = $null
$outputStream = $null
$wholeHash = $null
try {
    $manifestPath = Assert-PlainPath $Manifest $false
    $sourceDirectory = [IO.Path]::GetDirectoryName($manifestPath)
    $metadata = Read-PartsManifest $manifestPath
    if (-not $OutputDirectory) { $OutputDirectory = $sourceDirectory }
    $destinationDirectory = Assert-PlainPath $OutputDirectory $true
    $destination = [IO.Path]::Combine($destinationDirectory, $metadata.Name)
    if ([IO.File]::Exists($destination) -or [IO.Directory]::Exists($destination)) { throw 'Output already exists.' }
    if ([string]::Equals($destination, $manifestPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output collides with the manifest.' }
    foreach ($part in $metadata.Parts) {
        $partPath = Assert-PlainPath ([IO.Path]::Combine($sourceDirectory, $part.Name)) $false
        if ([string]::Equals($destination, $partPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output collides with a part.' }
        if (([IO.FileInfo]$partPath).Length -ne $part.Size) { throw ('Wrong part size: ' + $part.Name) }
    }
    $temporary = [IO.Path]::Combine($destinationDirectory, '.deep32-join-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $outputStream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $wholeHash = [Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] 1048576
    foreach ($part in $metadata.Parts) {
        $partPath = Assert-PlainPath ([IO.Path]::Combine($sourceDirectory, $part.Name)) $false
        $inputStream = $null
        $partHash = $null
        try {
            $inputStream = [IO.File]::Open($partPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            if ($inputStream.Length -ne $part.Size) { throw ('Wrong part size: ' + $part.Name) }
            $partHash = [Security.Cryptography.SHA256]::Create()
            $copied = [long]0
            while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $copied += $read
                if ($copied -gt $part.Size) { throw ('Part grew while reading: ' + $part.Name) }
                [void]$partHash.TransformBlock($buffer, 0, $read, $buffer, 0)
                [void]$wholeHash.TransformBlock($buffer, 0, $read, $buffer, 0)
                $outputStream.Write($buffer, 0, $read)
            }
            [void]$partHash.TransformFinalBlock((New-Object byte[] 0), 0, 0)
            if ($copied -ne $part.Size -or (Get-HexHash $partHash.Hash) -cne $part.Hash) {
                throw ('Part verification failed: ' + $part.Name)
            }
        } finally {
            if ($null -ne $inputStream) { $inputStream.Dispose() }
            if ($null -ne $partHash) { $partHash.Dispose() }
        }
    }
    [void]$wholeHash.TransformFinalBlock((New-Object byte[] 0), 0, 0)
    if ($outputStream.Length -ne $metadata.Size -or (Get-HexHash $wholeHash.Hash) -cne $metadata.Hash) {
        throw 'Joined image verification failed.'
    }
    $outputStream.Flush($true)
    $outputStream.Dispose()
    $outputStream = $null
    [void](Assert-PlainPath $destinationDirectory $true)
    [IO.File]::Move($temporary, $destination)
    $temporary = $null
    Write-Output ('Joined and verified: ' + $metadata.Name)
    Write-Output 'Authenticate the release manifest with verify-release before using the image.'
    exit 0
} catch {
    [Console]::Error.WriteLine('Join failed: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $outputStream) { $outputStream.Dispose() }
    if ($null -ne $wholeHash) { $wholeHash.Dispose() }
    if ($null -ne $temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
