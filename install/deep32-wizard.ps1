<#
deep32-wizard.ps1: the full-screen setup wizard for Deep32 on Windows (the twin of deep32-wizard.sh).

  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1            set up this PC, one step at a time
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -Demo      the whole wizard against a recorded setup: no network, no changes
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -Plain     deep32-setup.ps1 with its normal tagged output
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -VmGuide   the Workstation VM, step by step, for
      Proxmox VE, VMware, VirtualBox, Hyper-V, KVM or another machine (-Kit FOLDER names the download folder)
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -SendDisc2 send this PC's verified disc 2 to a Workstation over your tailnet

The wizard drives install\deep32-setup.ps1 and shows each step on one screen. It takes the same role and options
(check, forge, workstation, headend, -Yes, -DataRoot, -Name, -Workstation, -Agent) and its own: -Plain, -Demo,
-Theme dark|light, -Ascii and -Help. It runs deep32-setup.ps1 unchanged, with its tagged lines, when the input or
output is redirected, TERM is dumb, NO_COLOR is set or -Plain is given, so agents and CI read the same lines as
before. It works in Windows PowerShell 5.1 and PowerShell 7, in Windows Terminal and the classic console.

Keys: arrows choose, Enter continues, Esc goes back, L shows the log, ? shows help, Q quits.
The log of every run is kept in %LOCALAPPDATA%\Deep32-setup.
#>
param(
    [Parameter(Position = 0)][string]$Role = '',
    [switch]$Yes,
    [string]$DataRoot = '',
    [string]$Name = '',
    [string]$Workstation = '',
    [string]$Agent = '',
    [switch]$Plain,
    [switch]$Demo,
    [switch]$SendDisc2,
    [string]$Theme = '',
    [switch]$Ascii,
    [switch]$Help,
    [string]$DemoReplay = '',
    [switch]$Events,
    [string]$PlanDigest = '',
    [string]$Settings = '',
    [switch]$ChecksOnly,
    [switch]$GuessRole,
    [switch]$PrintMode,
    [string]$Decode = '',
    [switch]$VmGuide,
    [string]$Kit = '',
    [string]$Hypervisor = ''
)
$ErrorActionPreference = 'Continue'
$script:ProfClock = [Diagnostics.Stopwatch]::StartNew()   # DEEP32_WIZARD_PROFILE=FILE writes timings there (tests and tuning)
$script:Prof = [System.Collections.Generic.List[string]]::new()
$WizHere = [IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
$WizRepo = [IO.Path]::GetDirectoryName($WizHere)
$WizSelf = Join-Path $WizHere 'deep32-wizard.ps1'
$WizSetup = Join-Path $WizHere 'deep32-setup.ps1'
$script:DemoFixed = 0
$E = [char]27

# ---------------------------------------------------------------------------------------------------------------
# 1. Mode and the plain fallback.

function Get-WizardMode {  # plain or tui: the full screen only on a real console that can show it
    if ($Plain) { return 'plain' }
    if ($env:NO_COLOR) { return 'plain' }
    if ($env:TERM -eq 'dumb') { return 'plain' }
    if ($env:DEEP32_WIZARD_FORCE_TTY -ne '1') {
        if ([Console]::IsOutputRedirected -or [Console]::IsInputRedirected) { return 'plain' }
    }
    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { return 'plain' }
    return 'tui'
}

function Get-SetupArguments {  # the options meant for deep32-setup.ps1, as a hashtable for splatting
    $pass = @{}
    foreach ($k in 'Role', 'Yes', 'DataRoot', 'Name', 'Workstation', 'Agent') {
        if ($script:Bound.ContainsKey($k)) { $pass[$k] = $script:Bound[$k] }
    }
    return $pass
}

# ---------------------------------------------------------------------------------------------------------------
# 2. Helpers shared by the screen, the tests and the replay.

function Get-NowMs { return [long]([DateTime]::UtcNow - [DateTime]'1970-01-01').TotalMilliseconds }

function Format-Bytes([long]$n) {  # decimal units as INSTALL.md writes them (1.9 GB, 640 MB)
    if ($n -ge 1000000000) { $v = [long][math]::Floor(($n + 50000000) / 100000000); return ('{0}.{1} GB' -f [math]::Floor($v / 10), ($v % 10)) }
    if ($n -ge 1000000) { return ('{0} MB' -f [long][math]::Floor(($n + 500000) / 1000000)) }
    if ($n -ge 1000) { return ('{0} kB' -f [long][math]::Floor(($n + 500) / 1000)) }
    return ('{0} bytes' -f $n)
}

function Format-Eta([long]$s) {  # a plain time left
    if ($s -lt 60) { return 'less than a minute left' }
    if ($s -lt 3600) { return ('about {0} min left' -f [long][math]::Floor(($s + 59) / 60)) }
    $h = [long][math]::Floor($s / 3600); $m = [long][math]::Floor((($s % 3600) + 59) / 60)
    if ($m -eq 60) { $h++; $m = 0 }
    if ($m -eq 0) { return ('about {0} h left' -f $h) }
    return ('about {0} h {1} min left' -f $h, $m)
}

function Format-Clock([long]$s) { if ($s -lt 0) { $s = 0 }; return ('{0}:{1:00}' -f [long][math]::Floor($s / 60), ($s % 60)) }

function Get-BarCells([long]$p, [int]$w) {  # whole cells and a half cell for a bar w cells wide at p per mille
    if ($p -lt 0) { $p = 0 }; if ($p -gt 1000) { $p = 1000 }
    $halves = [long][math]::Floor(($p * $w * 2 + 500) / 1000)
    return @([long][math]::Floor($halves / 2), [int]($halves % 2))
}

function Get-WindowRate([string]$samples) {  # bytes per second over the last 30 s of 'T:B T:B' samples: @(rate, spanMs); rate -1 until 3 s of them
    $list = $samples.Split([char[]]' ', [StringSplitOptions]::RemoveEmptyEntries)
    if ($list.Count -eq 0) { return @(-1, 0) }
    $last = $list[$list.Count - 1].Split(':'); $t1 = [long]$last[0]; $b1 = [long]$last[1]
    $first = $null; foreach ($x in $list) { $q = $x.Split(':'); if ([long]$q[0] -ge $t1 - 30000) { $first = $q; break } }
    $span = $t1 - [long]$first[0]
    if ($span -lt 3000) { return @(-1, $span) }
    $rate = [long][math]::Floor(($b1 - [long]$first[1]) * 1000 / $span); if ($rate -lt 0) { $rate = 0 }
    return @($rate, $span)
}

function Get-EtaSeconds([long]$remain, [long]$rate, [long]$span) {  # seconds left, or -1 while it means nothing (unknown total, no speed, under 10 s)
    if ($remain -lt 0 -or $rate -le 0 -or $span -lt 10000) { return -1 }
    return [long][math]::Floor(($remain + $rate - 1) / $rate)
}

function Get-StripLit([long]$p) { if ($p -lt 0) { $p = 0 }; if ($p -gt 1000) { $p = 1000 }; return [int][math]::Floor($p * 32 / 1000) }

function Get-Permille([long]$a, [long]$b) {
    if ($b -le 0) { return 0 }; if ($a -ge $b) { return 1000 }
    return [long][math]::Floor($a * 1000 / $b)
}

function ConvertFrom-EventLine([string]$line) {  # a flat {"deep32":...} line into a hashtable, or $null
    if (-not $line.StartsWith('{"deep32":')) { return $null }
    try { $o = $line | ConvertFrom-Json } catch { return $null }
    $h = @{}
    foreach ($p in $o.PSObject.Properties) {
        $v = $p.Value
        if ($v -is [bool]) { $v = ([string]$v).ToLowerInvariant() }
        $h[$p.Name] = [string]$v
    }
    return $h
}

# ---------------------------------------------------------------------------------------------------------------
# 3. The error and guidance table (install\deep32-wizard-errors.tsv, shared with deep32-wizard.sh).

function Import-ErrorTable {
    if ($script:ErrLoaded) { return }
    $script:ErrLoaded = $true
    $script:ErrRows = [System.Collections.Generic.List[object]]::new()
    $file = Join-Path $WizHere 'deep32-wizard-errors.tsv'
    $os = 'windows'; if ($env:DEEP32_WIZARD_OS) { $os = $env:DEEP32_WIZARD_OS }
    if (-not (Test-Path -LiteralPath $file)) { return }
    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($line in [IO.File]::ReadAllLines($file)) {
        if (-not $line -or $line.StartsWith('#') -or -not $line.Trim()) { continue }
        $f = $line.TrimEnd("`r").Split("`t")
        if ($f.Count -lt 6) { continue }
        $rows.Add($f)
    }
    $mine = @{}
    foreach ($f in $rows) { if ($f[2] -eq $os) { $mine[$f[0] + ' ' + $f[1]] = $true } }
    foreach ($f in $rows) {
        if ($f[2] -eq $os) { } elseif ($f[2] -eq 'any') { if ($mine.ContainsKey($f[0] + ' ' + $f[1])) { continue } } else { continue }
        $script:ErrRows.Add([pscustomobject]@{ Kind = $f[0]; Key = $f[1]; Src = $f[3]; Mean = $f[4]; Fix = $f[5] })
    }
}

$script:OkPattern = '^(\[(ok|info|you)\] |Verified |Completed from mirror |Optional manual torrent: |Reusing verified |Write the verified |Attach the verified |Selected GPU capability|VM artefacts written: |Prerequisites passed |Split Disc 3 installation includes |Checking read-only prerequisites|Mounting |Running the disc3 Docker form|Disc3 loader returned|Next: review GPU pack)'

function Get-CodePattern([string]$code) {
    $k = [regex]::Escape($code)
    return '(^|[^A-Za-z0-9_.-])' + $k + '([^A-Za-z0-9_.-]|\.([^A-Za-z0-9]|$)|$)'
}

function Resolve-Failure([string]$statuses, [string[]]$lines) {  # matched rows, best first, as @(@(row, line), ...)
    Import-ErrorTable
    # Ranked: a reason code, then a message from a deeper tool, then a deep32-setup message, an exit status, the catch-all.
    $r1 = @(); $r2 = @(); $r3 = @(); $r4 = @(); $catch = $null; $seen = @{}
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        $line = $lines[$i]
        if ($line.StartsWith('{"deep32":')) { continue }
        if ($line -cmatch $script:OkPattern) { continue }  # progress and success lines never explain a failure
        for ($j = 0; $j -lt $script:ErrRows.Count; $j++) {
            $row = $script:ErrRows[$j]
            if ($row.Kind -eq 'code') {
                if ($row.Key -cmatch '^[a-z]+$' -and $statuses -notlike '*connect:*') { continue }
                if (-not $seen.ContainsKey($j) -and $line -cmatch (Get-CodePattern $row.Key)) { $r1 += , @($j, $line); $seen[$j] = 1 }
            } elseif ($row.Kind -eq 'text') {
                if ($row.Key -eq '.') { $catch = $j; continue }
                if ($seen.ContainsKey($j)) { continue }
                if ($line -cmatch $row.Key) {
                    if ($row.Src -eq 'setup') { $r3 += , @($j, $line) } else { $r2 += , @($j, $line) }
                    $seen[$j] = 1
                }
            }
        }
    }
    foreach ($stat in $statuses.Split([char[]]' ', [StringSplitOptions]::RemoveEmptyEntries)) {
        if ($stat.EndsWith(':0')) { continue }
        for ($j = 0; $j -lt $script:ErrRows.Count; $j++) {
            $row = $script:ErrRows[$j]
            if ($row.Kind -eq 'exit' -and $row.Key -eq $stat -and -not $seen.ContainsKey($j)) {
                $r4 += , @($j, (($stat -split ':')[0] + ' exit ' + ($stat -split ':')[1])); $seen[$j] = 1
            }
        }
    }
    $all = @($r1 + $r2 + $r3 + $r4)
    if ($null -ne $catch) { $all += , @($catch, '') }
    return , $all
}

function Get-FixSteps([int]$row) { return , $script:ErrRows[$row].Fix.Split([string[]]@(' | '), [StringSplitOptions]::RemoveEmptyEntries) }

function Get-Guidance([string]$text) {  # the first text row matching a [fix] line, or -1
    Import-ErrorTable
    for ($j = 0; $j -lt $script:ErrRows.Count; $j++) {
        $row = $script:ErrRows[$j]
        if ($row.Kind -eq 'text' -and $row.Key -ne '.' -and $text -cmatch $row.Key) { return $j }
    }
    return -1
}

# ---------------------------------------------------------------------------------------------------------------
# 4. The demo replay: plays install\deep32-wizard-demo.txt in place of deep32-setup.ps1.

function Invoke-DemoReplay([string]$scenario) {
    $out = [Console]::Out
    $attempt = 1; if ($env:DEEP32_WIZARD_DEMO_ATTEMPT) { $attempt = [int]$env:DEEP32_WIZARD_DEMO_ATTEMPT }
    $os = 'windows'; if ($env:DEEP32_WIZARD_OS -and $env:DEEP32_WIZARD_OS -ne 'windows') { $os = 'unix' }
    $dir = 'C:\Users\you\deep32-downloads'; if ($env:DEEP32_WIZARD_DEMO_DIR) { $dir = $env:DEEP32_WIZARD_DEMO_DIR }
    $root = 'C:\Deep32'; if ($env:DEEP32_WIZARD_DEMO_ROOT) { $root = $env:DEEP32_WIZARD_DEMO_ROOT }
    $py = 'C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe'
    $answer = ''; $skip = [System.Collections.Generic.List[int]]::new(); $skip.Add(0)
    $inside = $false
    foreach ($raw in [IO.File]::ReadAllLines((Join-Path $WizHere 'deep32-wizard-demo.txt'))) {
        $line = $raw.TrimEnd("`r")
        if ($line.StartsWith('=== ')) { if ($inside) { break }; if ($line.Substring(4) -eq $scenario) { $inside = $true }; continue }
        if (-not $inside -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('.if ')) {
            $c = $line.Substring(4); $on = $false
            switch ($c) {
                'yes' { $on = $answer -match '^[Yy]' } 'no' { $on = $answer -notmatch '^[Yy]' }
                'first' { $on = $attempt -eq 1 } 'retry' { $on = $attempt -gt 1 }
                'digest' { $on = [bool]$PlanDigest } 'nodigest' { $on = -not $PlanDigest }
                'windows' { $on = $os -eq 'windows' } 'unix' { $on = $os -eq 'unix' }
                'fixed' { $on = $env:DEEP32_WIZARD_DEMO_FIXED -eq '1' }
            }
            if ($skip[$skip.Count - 1] -eq 1 -or -not $on) { $skip.Add(1) } else { $skip.Add(0) }
            continue
        }
        if ($line -eq '.endif') { if ($skip.Count -gt 1) { $skip.RemoveAt($skip.Count - 1) }; continue }
        if ($skip[$skip.Count - 1] -eq 1) { continue }
        $line = $line.Replace('{dir}', $dir).Replace('{root}', $root).Replace('{py}', $py).Replace('{tab}', "`t")
        if ($line.StartsWith('.sleep ')) { Start-Sleep -Milliseconds ([int]([double]::Parse($line.Substring(7), [Globalization.CultureInfo]::InvariantCulture) * 1000)) }
        elseif ($line.StartsWith('.json ')) { if ($Events) { $out.WriteLine($line.Substring(6)); $out.Flush() } }
        elseif ($line.StartsWith('.exit ')) { $out.Flush(); exit ([int]$line.Substring(6)) }
        elseif ($line.StartsWith('.ask ')) {
            $rest = $line.Substring(5); $id = $rest.Split(' ')[0]; $q = $rest.Substring($id.Length + 1)
            if ($Events) { $out.WriteLine('{"deep32":"ask","id":"' + $id + '","text":"' + $q + '"}') } else { $out.Write($q + ' [y/N] ') }
            $out.Flush(); $answer = [Console]::In.ReadLine(); if ($null -eq $answer) { $answer = '' }
        } elseif ($line.StartsWith('.prompt ')) {
            [Console]::Error.Write($line.Substring(8)); [Console]::Error.Flush()
            if (-not [Console]::IsInputRedirected) { while ($true) { $k = [Console]::ReadKey($true); if ($k.Key -eq 'Enter') { break } } }
            else { $null = [Console]::In.ReadLine() }
            [Console]::Error.WriteLine()
        } elseif ($line.StartsWith('.dl ')) {
            $f = $line.Substring(4).Split(' ')
            $size = [long]$f[1]; $secs = [double]::Parse($f[2], [Globalization.CultureInfo]::InvariantCulture)
            $from = 0; $to = 1000; if ($f.Count -gt 3) { $from = [int]$f[3] }; if ($f.Count -gt 4) { $to = [int]$f[4] }
            $steps = [int][math]::Floor($secs) * 4; if ($steps -lt 2) { $steps = 2 }
            for ($k = 0; $k -le $steps; $k++) {
                $b = [long][math]::Floor($size / 1000) * [long]($from + [math]::Floor(($to - $from) * $k / $steps))
                if ($Events) { $out.WriteLine(('{{"deep32":"demo-progress","file":"{0}","bytes":{1},"size":{2}}}' -f $f[0], $b, $size)); $out.Flush() }
                if ($k -lt $steps) { Start-Sleep -Milliseconds 250 }
            }
        } else { $out.WriteLine($line); $out.Flush() }
    }
    exit 0
}

function New-DemoRun([string]$scenario, [bool]$digest) {  # one recorded run, played by Step-DemoRun from the main loop
    if (-not $script:DemoLines) { $script:DemoLines = [IO.File]::ReadAllLines((Join-Path $WizHere 'deep32-wizard-demo.txt')) }
    $lines = [System.Collections.Generic.List[string]]::new(); $inside = $false
    foreach ($raw in $script:DemoLines) {
        $line = $raw.TrimEnd("`r")
        if ($line.StartsWith('=== ')) { if ($inside) { break }; if ($line.Substring(4) -eq $scenario) { $inside = $true }; continue }
        if ($inside -and -not $line.StartsWith('#')) { $lines.Add($line) }
    }
    $skip = [System.Collections.Generic.List[int]]::new(); $skip.Add(0)
    return @{ Lines = $lines; Pos = 0; Due = 0; Waiting = $false; Answer = ''; Skip = $skip; Digest = $digest; Dl = $null
        Os = $(if ($env:DEEP32_WIZARD_OS -and $env:DEEP32_WIZARD_OS -ne 'windows') { 'unix' } else { 'windows' }) }
}

function Step-DemoRun {  # play the recorded lines that are due now; questions wait for Send-Answer
    $d = $G.DemoRun
    $dir = 'C:\Users\you\deep32-downloads'; $root = 'C:\Deep32'; $py = 'C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe'
    for ($n = 0; $n -lt 200 -and $G.DemoRun; $n++) {
        $now = Get-NowMs
        if ($d.Waiting -or $now -lt $d.Due) { return }
        if ($d.Dl) {
            $x = $d.Dl
            $b = [long][math]::Floor($x.Size / 1000) * [long]($x.From + [math]::Floor(($x.To - $x.From) * $x.K / $x.Steps))
            Read-RunLine ('{{"deep32":"demo-progress","file":"{0}","bytes":{1},"size":{2}}}' -f $x.File, $b, $x.Size)
            $x.K++; if ($x.K -gt $x.Steps) { $d.Dl = $null } else { $d.Due = $now + 250 }
            continue
        }
        if ($d.Pos -ge $d.Lines.Count) { Complete-Run 0; return }
        $line = $d.Lines[$d.Pos]; $d.Pos++
        if ($line.StartsWith('.if ')) {
            $c = $line.Substring(4); $on = $false
            switch ($c) {
                'yes' { $on = $d.Answer -match '^[Yy]' } 'no' { $on = $d.Answer -notmatch '^[Yy]' }
                'first' { $on = $G.AttemptNow -le 1 } 'retry' { $on = $G.AttemptNow -gt 1 }
                'digest' { $on = $d.Digest } 'nodigest' { $on = -not $d.Digest }
                'windows' { $on = $d.Os -eq 'windows' } 'unix' { $on = $d.Os -eq 'unix' }
                'fixed' { $on = $script:DemoFixed -eq 1 }
            }
            if ($d.Skip[$d.Skip.Count - 1] -eq 1 -or -not $on) { $d.Skip.Add(1) } else { $d.Skip.Add(0) }
            continue
        }
        if ($line -eq '.endif') { if ($d.Skip.Count -gt 1) { $d.Skip.RemoveAt($d.Skip.Count - 1) }; continue }
        if ($d.Skip[$d.Skip.Count - 1] -eq 1) { continue }
        $line = $line.Replace('{dir}', $dir).Replace('{root}', $root).Replace('{py}', $py).Replace('{tab}', "`t")
        if ($line.StartsWith('.sleep ')) { $d.Due = $now + [long]([double]::Parse($line.Substring(7), [Globalization.CultureInfo]::InvariantCulture) * 1000) }
        elseif ($line.StartsWith('.json ')) { Read-RunLine $line.Substring(6) }
        elseif ($line.StartsWith('.exit ')) { Complete-Run ([int]$line.Substring(6)); return }
        elseif ($line.StartsWith('.ask ')) {
            $rest = $line.Substring(5); $id = $rest.Split(' ')[0]; $q = $rest.Substring($id.Length + 1)
            $d.Waiting = $true
            Read-RunLine ('{"deep32":"ask","id":"' + $id + '","text":"' + $q + '"}')
        } elseif ($line.StartsWith('.prompt ')) { }
        elseif ($line.StartsWith('.dl ')) {
            $dlf = $line.Substring(4).Split(' ')
            $secs = [double]::Parse($dlf[2], [Globalization.CultureInfo]::InvariantCulture)
            $steps = [int][math]::Floor($secs) * 4; if ($steps -lt 2) { $steps = 2 }
            $d.Dl = @{ File = $dlf[0]; Size = [long]$dlf[1]; From = $(if ($dlf.Count -gt 3) { [int]$dlf[3] } else { 0 }); To = $(if ($dlf.Count -gt 4) { [int]$dlf[4] } else { 1000 }); Steps = $steps; K = 0 }
        } else { Read-RunLine $line }
    }
}

# ---------------------------------------------------------------------------------------------------------------
# 5. Terminal: glyphs and colours.

function Set-Glyphs {
    $mode = 'safe'
    if ($env:WT_SESSION -or $env:TERM_PROGRAM -eq 'vscode' -or $env:ConEmuANSI -eq 'ON') { $mode = 'rich' }
    if ($Ascii) { $mode = 'ascii' }
    if ($env:DEEP32_WIZARD_GLYPHS) { $mode = $env:DEEP32_WIZARD_GLYPHS }
    $script:GlyphMode = $mode
    $c = { param($n) [string][char]$n }
    switch ($mode) {
        'rich' {
            $script:Gl = @{ TL = & $c 0x256D; TR = & $c 0x256E; BL = & $c 0x2570; BR = & $c 0x256F; H = & $c 0x2500; V = & $c 0x2502; TT = & $c 0x252C; BT = & $c 0x2534
                OK = & $c 0x2713; FIX = '!'; FAIL = & $c 0x00D7; CUR = & $c 0x25CF; PEND = & $c 0x25CB; SKIP = & $c 0x2013; INFO = & $c 0x00B7; YOU = & $c 0x2192; DOT = & $c 0x00B7
                MARK = & $c 0x258D; BAR = & $c 0x2501; BARH = & $c 0x2578; TRACK = & $c 0x2501; WIDE = (& $c 0x2584) * 3; MED = & $c 0x2584; NARROW = & $c 0x2596
                ELL = & $c 0x2026; QUOTE = & $c 0x2503; UP = & $c 0x2191; DOWN = & $c 0x2193; CURSOR = & $c 0x258F }
            $script:Spin = @(0x280B, 0x2819, 0x2839, 0x2838, 0x283C, 0x2834, 0x2826, 0x2827, 0x2807, 0x280F | ForEach-Object { [string][char]$_ })
        }
        'safe' {
            $script:Gl = @{ TL = & $c 0x250C; TR = & $c 0x2510; BL = & $c 0x2514; BR = & $c 0x2518; H = & $c 0x2500; V = & $c 0x2502; TT = & $c 0x252C; BT = & $c 0x2534
                OK = & $c 0x221A; FIX = '!'; FAIL = & $c 0x00D7; CUR = & $c 0x25CF; PEND = & $c 0x25CB; SKIP = '-'; INFO = & $c 0x00B7; YOU = & $c 0x2192; DOT = & $c 0x00B7
                MARK = & $c 0x258C; BAR = & $c 0x2588; BARH = & $c 0x258C; TRACK = & $c 0x2591; WIDE = (& $c 0x2588) * 3; MED = & $c 0x2588; NARROW = & $c 0x258C
                ELL = & $c 0x2026; QUOTE = & $c 0x2502; UP = & $c 0x2191; DOWN = & $c 0x2193; CURSOR = '_' }
            $script:Spin = @('|', '/', '-', '\')
        }
        default {
            $script:Gl = @{ TL = '+'; TR = '+'; BL = '+'; BR = '+'; H = '-'; V = '|'; TT = '+'; BT = '+'; OK = '+'; FIX = '!'; FAIL = 'x'; CUR = '*'; PEND = 'o'; SKIP = '-'
                INFO = '.'; YOU = '>'; DOT = '.'; MARK = '>'; BAR = '#'; BARH = '#'; TRACK = '.'; WIDE = '==='; MED = '='; NARROW = '-'; ELL = '~'; QUOTE = '|'; UP = '^'; DOWN = 'v'; CURSOR = '_' }
            $script:Spin = @('|', '/', '-', '\')
        }
    }
}

function Get-C256([int]$r, [int]$g, [int]$b) {
    $lv = @(0, 95, 135, 175, 215, 255)
    $q = { param($v) if ($v -lt 48) { 0 } elseif ($v -lt 115) { 1 } else { [int][math]::Floor(($v - 35) / 40) } }
    $ri = & $q $r; $gi = & $q $g; $bi = & $q $b
    $d1 = [math]::Pow($r - $lv[$ri], 2) + [math]::Pow($g - $lv[$gi], 2) + [math]::Pow($b - $lv[$bi], 2)
    $avg = [int][math]::Floor(($r + $g + $b) / 3)
    $k = if ($avg -gt 238) { 23 } elseif ($avg -lt 8) { 0 } else { [int][math]::Floor(($avg - 8) / 10) }
    $gv = 8 + 10 * $k
    $d2 = [math]::Pow($r - $gv, 2) + [math]::Pow($g - $gv, 2) + [math]::Pow($b - $gv, 2)
    if ($d2 -lt $d1) { return 232 + $k }
    return 16 + 36 * $ri + 6 * $gi + $bi
}

function Get-Sgr([string]$kind, [string]$hex, [int]$basic) {
    $r = [Convert]::ToInt32($hex.Substring(0, 2), 16); $g = [Convert]::ToInt32($hex.Substring(2, 2), 16); $b = [Convert]::ToInt32($hex.Substring(4, 2), 16)
    $base = if ($kind -eq 'fg') { 38 } else { 48 }
    switch ($script:Depth) {
        'truecolor' { return "$E[$base;2;$r;$g;${b}m" }
        '256' { return "$E[$base;5;$(Get-C256 $r $g $b)m" }
        default { if ($kind -eq 'fg') { return "$E[${basic}m" } else { return "$E[$($basic + 10)m" } }
    }
}

function Set-Palette {  # a calm palette for dark or light backgrounds; body text keeps the console's own colour
    $d = $script:ThemeName -eq 'dark'
    $S = @{ '0' = "$E[0m"; B = "$E[1m"; U = "$E[4m" }
    $defs = @(
        @('MUTED', 'fg', '8b949e', 90, '59636e', 90), @('FAINT', 'fg', '6e7681', 90, '818b98', 90), @('BORDER', 'fg', '30363d', 90, 'd0d7de', 37),
        @('ACCENT', 'fg', '4493f8', 94, '0969da', 34), @('ACCENTT', 'fg', '58a6ff', 94, '0b5cad', 34), @('OK', 'fg', '3fb950', 92, '1a7f37', 32),
        @('WARN', 'fg', 'd29922', 93, '9a6700', 33), @('ERR', 'fg', 'f85149', 91, 'd1242f', 31), @('TRACK', 'fg', '262c36', 90, 'd8dee4', 37),
        @('STRIP1', 'fg', '4493f8', 94, '0969da', 34), @('STRIP2', 'fg', '2e5f9e', 34, '6ba5e9', 94), @('STRIP3', 'fg', '203d63', 34, 'abccf2', 36),
        @('SELBG', 'bg', '152439', 40, 'ddf4ff', 47), @('CHIPBG', 'bg', '161b22', 40, 'f6f8fa', 47), @('WARNBG', 'bg', '2b2111', 40, 'fff8c5', 47),
        @('ERRBG', 'bg', '2d1517', 40, 'ffebe9', 47), @('BTNBG', 'bg', '1f6feb', 44, '0969da', 44), @('BTNFG', 'fg', 'ffffff', 97, 'ffffff', 97),
        @('IDLEBG', 'bg', '21262d', 100, 'eaeef2', 47), @('IDLEFG', 'fg', 'c9d1d9', 97, '1f2328', 30))
    foreach ($x in $defs) { if ($d) { $S[$x[0]] = Get-Sgr $x[1] $x[2] $x[3] } else { $S[$x[0]] = Get-Sgr $x[1] $x[4] $x[5] } }
    if ($script:Depth -eq '16') { foreach ($k in 'SELBG', 'CHIPBG', 'WARNBG', 'ERRBG') { $S[$k] = '' } }
    $S.TITLE = $S.B; $S.WORD = $S.B; $S.WORD32 = $S.B + $S.ACCENT
    $script:St = $S
}

# ---------------------------------------------------------------------------------------------------------------
# 6. State.

$StepIds = @('check', 'role', 'download', 'install', 'pair', 'done')
$StepNames = @('Check this machine', 'Choose role', 'Download and verify', 'Install', 'Pair', 'Done')
$RoleIds = @('forge', 'workstation', 'headend')
$RoleNames = @('Forge', 'Workstation host', 'Agent PC')
$PartKeys = @('ws', 'forge', 'agent')
$PartNames = @('Workstation', 'Forge', 'Agent PC')

function New-List { return , ([System.Collections.Generic.List[string]]::new()) }

function Initialize-State {
    $script:G = @{
        StepState = @('current', 'pending', 'pending', 'pending', 'pending', 'pending'); StepNote = @('', '', '', '', '', '')
        Stage = 0; View = 'intro'; Overlay = ''; Focus = 0; Sel = 0; Scroll = 0; LogScroll = 0
        Role = ''; Guess = ''; GuessGpu = ''; CheckedRole = ''; CheckFor = ''; Problems = 0; Fixable = (New-List)
        ItTag = (New-List); ItText = (New-List); Log = (New-List); Recent = (New-List); LastRaw = ''; RunLines = (New-List)
        Dl = ([System.Collections.Generic.List[object]]::new()); DlDir = ''; DlDisc = ''; DlClass = ''; DlMirrors = ''; DlMirrorsAt = 0; DlPollAt = 0
        Plan = (New-List); PlanDigest = ''; PlanIn = $false; Digest = ''; Typed = ''; Consent = $false
        PairCode = ''; PairPeer = ''; PairUntil = 0; Paired = $false; SigninUrl = ''; AskId = ''; AskText = ''
        Proc = $null; OutTask = $null; ErrTask = $null; OutEof = $false; ErrEof = $false; RunKind = ''; Statuses = ''; RunStatus = 0
        FailTitle = ''; FailRows = @(); FailStage = 0; VmLine = ''; Activity = ''; StatusMsg = ''; Attempt = @(0, 0, 0); AttemptNow = 1; SoftDone = ''
        FormKeys = @(); FormLabel = @(); FormHelp = @(); Btns = @(); BtnCount = 1
        Keys = (New-List); Snap = ''; Until = ''; UntilAt = 0; WaitUntil = 0; Dirty = $true; Quit = $false; Interrupted = $false; PlainAfter = $false
        Tick = 0; Pulse = 0; Perm = 0; Resume = ''; Rs = @{}; HandoverStatus = 1; Phase = 0
        Xs = (New-List); XfLast = 0; XfStart = 0; PhaseAt = 0; SkipParts = $false; Next = @(); NextOffer = ''; NextFile = ''; NextWritten = ''
        GuessHv = ''; GuessAgent = ''; Parts = @{}; Recheck = $false
        VmHv = ''; VmWhere = ''; VmNet = 'bridged'; VmDone = $false; VmSkipped = @(); Vs = $null; VmI = 0; VmFor = ''; VmStepsFile = ''
        VmWOpts = @(); VmNOpts = @(); NetRows = $null; NetHostIf = ''; NetSwitch = ''; PvePin = ''; PveSeen = ''; TsSnap = @{}; TsPolls = 0; TsPollAt = 0; TsEvery = 5000
        FormTitle = ''; FormPat = @(); FormPErr = @()
    }
    if ($Role -and $Role -ne 'check') { $G.Role = $Role }
}

function Get-RoleIndex([string]$r) { for ($i = 0; $i -lt 3; $i++) { if ($RoleIds[$i] -eq $r) { return $i } }; return 0 }

function Set-Stage([int]$n) {
    for ($i = 0; $i -lt 6; $i++) {
        $s = $G.StepState[$i]
        if ($i -lt $n) { if ($s -ne 'skipped' -and $s -ne 'later') { $G.StepState[$i] = 'done' } }
        elseif ($i -eq $n) { $G.StepState[$i] = 'current' }
        else { if ($s -ne 'skipped' -and $s -ne 'later') { $G.StepState[$i] = 'pending' } }
    }
    $G.Stage = $n; $G.Dirty = $true
}

function Update-Progress {
    $base = @(0, 120, 190, 690, 850, 970); $span = @(110, 60, 500, 160, 120, 30); $i = $G.Stage; $part = 0
    switch ($StepIds[$i]) {
        'download' { $tot = 0; $have = 0; foreach ($d in $G.Dl) { $tot += $d.Size; $have += $d.Have }; $part = Get-Permille $have $tot }
        'check' { if ($G.View -eq 'checks') { $part = 1000 } else { $part = 400 } }
        'install' { if ($G.Consent) { $part = 500 } }
        'pair' { if ($G.PairCode) { $part = 600 } }
        'done' { $part = 1000 }
    }
    $G.Perm = $base[$i] + [math]::Floor($span[$i] * $part / 1000)
    if ($StepIds[$i] -eq 'done') { $G.Perm = 1000 }
}

# ---------------------------------------------------------------------------------------------------------------
# 7. Running deep32-setup (or the demo replay) and reading what it says.

function Write-WizLog([string]$line) {
    $G.Log.Add($line)
    if ($G.Log.Count -gt 6000) { $G.Log.RemoveRange(0, 1000) }
    if ($script:LogFile) { try { [IO.File]::AppendAllText($script:LogFile, $line + "`n") } catch { } }
}

function Get-HostExe { return (Get-Process -Id $PID).Path }

function ConvertTo-ArgText([string[]]$a) {  # a Windows command line for these arguments
    $parts = foreach ($x in $a) {
        if ($x -eq '') { '""' } elseif ($x -notmatch '[\s"]') { $x } else { '"' + ($x -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
    }
    return ($parts -join ' ')
}

function Start-Run([string]$kind, [string[]]$setupArgs) {  # start deep32-setup.ps1 -Events as a child (the demo plays in-process)
    Add-Prof "run-start $kind"
    if ($G.Proc) { Stop-Run }   # one setup at a time: a new code stops the previous setup and its pairing helper
    if ($Demo) {
        $sc = switch ($kind) { 'guess' { 'guess' } { $_ -in 'look', 'fix' } { 'check-' + $G.CheckFor } default { $G.Role } }
        $G.RunKind = $kind; $G.RunStatus = 0; $G.Statuses = ''; $G.RunLines = New-List; $G.LastStep = $null
        Write-WizLog ('--- ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' deep32-setup ' + ($setupArgs -join ' '))
        $G.DemoRun = New-DemoRun $sc ($setupArgs -contains '-PlanDigest')
        $G.Proc = 'demo'; $G.Dirty = $true
        return
    }
    if ($false) {
        $sc = switch ($kind) { 'guess' { 'guess' } { $_ -in 'look', 'fix' } { 'check-' + $G.CheckFor } default { $G.Role } }
        $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WizSelf, '-DemoReplay', $sc, '-Events') + $setupArgs
    } else {
        $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WizSetup, '-Events') + $setupArgs
    }
    $G.RunKind = $kind; $G.RunStatus = 0; $G.Statuses = ''; $G.RunLines = New-List; $G.LastStep = $null
    Write-WizLog ('--- ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' deep32-setup ' + ($setupArgs -join ' '))
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = Get-HostExe
    $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.WorkingDirectory = $script:WorkDir
    $psi.EnvironmentVariables['DEEP32_WIZARD_DEMO_ATTEMPT'] = [string]$G.AttemptNow
    $psi.EnvironmentVariables['DEEP32_WIZARD_DEMO_FIXED'] = [string]$script:DemoFixed
    $G.Proc = [System.Diagnostics.Process]::Start($psi)
    $G.OutTask = $G.Proc.StandardOutput.ReadLineAsync(); $G.ErrTask = $G.Proc.StandardError.ReadLineAsync()
    $G.OutEof = $false; $G.ErrEof = $false; $G.Dirty = $true
}

function Send-Answer([string]$a) {
    if ($G.DemoRun) { $G.DemoRun.Answer = $a; $G.DemoRun.Waiting = $false; Write-WizLog "> answered $a"; return }
    if ($G.Proc) { try { $G.Proc.StandardInput.WriteLine($a); $G.Proc.StandardInput.Flush() } catch { } }
    Write-WizLog "> answered $a"
}

function Receive-RunOutput {  # read what the child printed so far, without waiting
    if (-not $G.Proc) { return }
    if ($G.DemoRun) { Step-DemoRun; return }
    for ($n = 0; $n -lt 400; $n++) {
        $got = $false
        if (-not $G.OutEof -and $G.OutTask.IsCompleted) {
            $l = $G.OutTask.Result
            if ($null -eq $l) { $G.OutEof = $true } else { Read-RunLine $l; $G.OutTask = $G.Proc.StandardOutput.ReadLineAsync() }
            $got = $true
        }
        if (-not $G.ErrEof -and $G.ErrTask.IsCompleted) {
            $l = $G.ErrTask.Result
            if ($null -eq $l) { $G.ErrEof = $true } else { Read-RunLine $l; $G.ErrTask = $G.Proc.StandardError.ReadLineAsync() }
            $got = $true
        }
        if ($G.OutEof -and $G.ErrEof) { Complete-Run; return }
        if (-not $got) { return }
    }
}

function Complete-Run([int]$demoStatus = 0) {
    if ($G.DemoRun) { $stat = $demoStatus; $G.DemoRun = $null; $G.Proc = $null }
    else { $G.Proc.WaitForExit(); $stat = $G.Proc.ExitCode; $G.Proc.Dispose(); $G.Proc = $null }
    Write-WizLog "--- deep32-setup exit $stat"
    $G.RunStatus = $stat; $G.Statuses += " setup:$stat"
    $kind = $G.RunKind; $G.RunKind = ''
    Complete-RunKind $kind $stat
    $G.Dirty = $true
}

function Stop-Run {
    if ($G.DemoRun) { $G.DemoRun = $null; $G.Proc = $null; return }
    if (-not $G.Proc) { return }
    try { & taskkill.exe /PID $G.Proc.Id /T /F 2>$null | Out-Null } catch { }
}

function Read-RunLine([string]$line) {
    if (-not $script:SawLine) { $script:SawLine = $true; Add-Prof 'first-line' }
    Write-WizLog $line
    $G.RunLines.Add($line); if ($G.RunLines.Count -gt 400) { $G.RunLines.RemoveRange(0, 100) }
    $G.Dirty = $true
    if ($line.StartsWith('{"deep32":')) { $ev = ConvertFrom-EventLine $line; if ($ev) { Read-RunEvent $ev }; return }
    Update-Phase $line
    if ($line -match '^\[(ok|fix|you|fail|info)\] (.*)$') {
        $G.ItTag.Add($Matches[1]); $G.ItText.Add($Matches[2])
        if ($Matches[1] -ne 'fail') { $G.Activity = $Matches[2] }
        return
    }
    if ($line.Trim()) { $G.Recent.Add($line); if ($G.Recent.Count -gt 6) { $G.Recent.RemoveAt(0) }; $G.LastRaw = $line }
    if ($line -match '(https://login\.tailscale\.com/[A-Za-z0-9/._?=-]+)' -or ($G.View -eq 'signin' -and -not $G.SigninUrl -and $line -match '(https://[A-Za-z0-9.-]+/[A-Za-z0-9/._?=%-]*)')) { $G.SigninUrl = $Matches[1]; $G.View = 'signin'; return }
    if ($line -eq '{' -and $G.RunKind -eq 'role') { $G.Plan = New-List; $G.Plan.Add('{'); $G.PlanIn = $true; return }
    if ($G.PlanIn) {
        $G.Plan.Add($line)
        if ($line -cmatch '^  "digest": "([0-9a-f]{64})"') { $G.PlanDigest = $Matches[1] }
        if ($line -eq '}') {
            $G.PlanIn = $false; Set-Stage 3
            if (-not $G.Digest -or $G.Digest -ne $G.PlanDigest) { $G.Activity = 'The installer has shown its plan' }
            else { $G.Consent = $true; $G.View = 'install'; $G.Activity = 'Plan accepted; installing' }
        }
        return
    }
    if ($line -match '^Verified local image (.*)$') { Set-DlState $Matches[1] 'done' }
    elseif ($line -match '^Verified (.*) from mirror ') { Set-DlState $Matches[1] 'done' }
    elseif ($line.StartsWith('Completed from mirror ')) { Set-DlCurrent 'done' }
    elseif ($line.StartsWith('Download interrupted')) { Set-DlCurrent 'stopped' }
    elseif ($line -match '^VM artefacts written: (.*?); review') { $G.VmLine = $Matches[1] }
    elseif ($line -match '^(Mounting |Running the disc3 Docker form|Waiting for the verified disc3 loader|The verified disc3 loader)') { Set-Stage 3; $G.View = 'install'; $G.Activity = $line }
    elseif ($line.StartsWith('Checking read-only prerequisites')) { $G.Activity = 'Checking this host again before any change' }
    elseif ($line -match ': waiting for ') { $G.Activity = $line }
}

function Update-Phase([string]$line) {  # how far the install has come, from the installer's own lines
    if ($G.RunKind -ne 'role') { return }
    $n = -1
    if ($G.Role -eq 'forge') {
        if ($line.StartsWith('Checking read-only prerequisites') -and $G.Consent) { $n = 1 }
        elseif ($line.StartsWith('Mounting ')) { $n = 2 }
        elseif ($line -eq 'Running the disc3 Docker form' -or $line.StartsWith('Waiting for the verified disc3 loader')) { $n = 3 }
        elseif ($line.StartsWith('The verified disc3 loader returned successfully') -or $line.StartsWith('Disc3 loader returned')) { $n = 4 }
        elseif ($line -eq '[ok] Forge installed and running') { $n = 5 }
        elseif ($line.StartsWith('[ok] door answers')) { $n = 6 }
    } elseif ($line.StartsWith('VM artefacts written')) { $n = 2 }
    if ($n -gt $G.Phase) { $G.Phase = $n; $G.PhaseAt = Get-NowMs }
}

function Open-Ask {  # a question opens with "Not now" chosen, and keys typed before it are not its answer
    $G.Overlay = 'ask'; $G.Focus = 1; $G.AskPending = $false
    if ($script:RealTty) { try { while ([Console]::KeyAvailable) { [void][Console]::ReadKey($true) } } catch { } }
}

function Read-RunEvent($ev) {
    switch ($ev.deep32) {
        'role' { $G.Guess = $ev.role; $G.GuessGpu = $ev.gpu; $G.GuessHv = $ev.hyperv; if ($ev.agents) { $G.GuessAgent = 'yes' } }
        'step' {
            switch ($ev.step) { 'check' { $G.LastStep = 0 } 'download' { $G.LastStep = 2 } 'install' { $G.LastStep = 3 } 'pair' { $G.LastStep = 4 } }
            switch ($ev.step) {
                'check' { if ($G.RunKind -eq 'role') { $G.View = 'running' } }
                'download' { Set-Stage 2; $G.View = 'download' }
                'install' { Set-Stage 3; $G.View = 'install' }
                'pair' { Set-Stage 4; $G.View = 'pairwait' }
            }
        }
        'ask' {
            $G.AskId = $ev.id; $G.AskText = $ev.text
            if ($G.RunKind -eq 'look') { $G.Fixable.Add($ev.id); Send-Answer 'n' } elseif ($G.Overlay) { $G.AskPending = $true } else { Open-Ask }
        }
        'signin' { $G.View = 'signin'; $G.SigninUrl = '' }
        'run' {
            if ($ev.tool -eq 'get') {
                $G.Activity = 'Getting ' + $ev.disc
                if ($ev.action -eq 'prepare-vm') { $G.Activity = 'Writing the VM settings' }
                else { $G.DlDir = $ev.dir; $G.DlDisc = $ev.disc; $G.DlClass = $ev.class; $G.DlMirrors = '' }
            }
        }
        'exit' { $G.Statuses += ' ' + $ev.tool + ':' + $ev.status }
        'pair' { $G.PairCode = $ev.code; $G.PairPeer = $ev.peer; $m = 5; if ($ev.minutes) { $m = [int]$ev.minutes }; $G.PairUntil = (Get-NowMs) + $m * 60000; Set-Stage 4; $G.View = 'pair'; $G.Focus = 0 }
        'paired' { $G.Paired = $true }
        'checked' { if ($ev.problems -match '^\d{1,9}$') { $G.Problems = [int]$ev.problems } }
        'demo-progress' { if ($Demo -and $ev.bytes -match '^\d{1,15}$' -and $ev.size -match '^\d{1,15}$') { Update-Dl $ev.file ([long]$ev.bytes) ([long]$ev.size) } }
    }
}

# Downloads: one row per file, fed by the demo's progress events or by watching the download folder.

function Find-Dl([string]$name) { foreach ($d in $G.Dl) { if ($d.Name -eq $name) { return $d } }; return $null }

function Update-Dl([string]$name, [long]$have, [long]$size) {
    $now = Get-NowMs
    $d = Find-Dl $name
    if (-not $d) { $d = [pscustomobject]@{ Name = $name; Size = $size; Have = 0; State = 'wait'; Rate = 0; T = $now; B = $have }; $G.Dl.Add($d) }
    if ($size -gt 0) { $d.Size = $size }
    if ($d.State -ne 'done') {
        if ($size -gt 0 -and $have -ge $size) { $d.State = 'check' } elseif ($have -gt $d.Have) { $d.State = 'down' }
    }
    $dt = $now - $d.T; $db = $have - $d.B
    if ($dt -ge 900 -and $db -ge 0) {
        $rate = [long][math]::Floor($db * 1000 / $dt)
        if ($d.Rate -eq 0) { $d.Rate = $rate } else { $d.Rate = [long][math]::Floor(($d.Rate * 7 + $rate * 3) / 10) }
        $d.T = $now; $d.B = $have
    }
    $d.Have = $have; $G.Dirty = $true
}

function Set-DlState([string]$path, [string]$state) {
    $n = $path.TrimEnd('.'); $n = $n.Substring([math]::Max($n.LastIndexOf('\'), $n.LastIndexOf('/')) + 1)
    $d = Find-Dl $n
    if (-not $d) { $d = [pscustomobject]@{ Name = $n; Size = 0; Have = 0; State = 'wait'; Rate = 0; T = 0; B = 0 }; $G.Dl.Add($d) }
    $d.State = $state
    if ($state -eq 'done' -and $d.Size -gt 0) { $d.Have = $d.Size }
    $G.Dirty = $true
}

function Set-DlCurrent([string]$state) { foreach ($d in $G.Dl) { if ($d.State -in 'down', 'check', 'wait') { Set-DlState $d.Name $state; return } } }

function Read-MirrorFiles([string]$file, [string]$disc, [string]$cls) {  # name and size of the files this download selects
    try { $m = [IO.File]::ReadAllText($file) | ConvertFrom-Json } catch { return @() }
    $rows = @()
    foreach ($x in @($m.discs)) { if ($x.role -eq $disc) { $rows += , @($x.file, [long]$x.size) } }
    if ($disc -eq 'disc3') { foreach ($x in @($m.bundles)) { if ($x.bundle_id -in 'core', 'ingestion', ('embedder-' + $cls)) { $rows += , @($x.file, [long]$x.size) } } }
    return , $rows
}

function Update-DlPoll {  # watch the download folder of a real run: partial .download files grow, finished files appear
    if (-not $G.DlDir -or $Demo) { return }
    $now = Get-NowMs; if ($now - $G.DlPollAt -lt 1000) { return }; $G.DlPollAt = $now
    if (-not $G.DlMirrors -or $now - $G.DlMirrorsAt -gt 10000) {
        $best = Get-ChildItem -LiteralPath $G.DlDir -Filter '.deep32-*' -Directory -Force -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter 'revision-*' -Directory -ErrorAction SilentlyContinue } |
            ForEach-Object { Join-Path $_.FullName 'MIRRORS.json' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1
        if ($best) {
            $G.DlMirrors = $best; $G.DlMirrorsAt = $now
            foreach ($r in (Read-MirrorFiles $best $G.DlDisc $G.DlClass)) { if (-not (Find-Dl $r[0])) { Update-Dl $r[0] 0 $r[1] } }
        }
    }
    foreach ($d in $G.Dl.ToArray()) {
        if ($d.State -eq 'done') { continue }
        $f = Join-Path $G.DlDir $d.Name
        $it = Get-Item -LiteralPath $f -ErrorAction SilentlyContinue
        if ($it) { Update-Dl $d.Name $it.Length $d.Size; if ($d.State -ne 'done') { $d.State = 'check' }; continue }
        $p = $f + '.download'
        $it = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue
        if ($it) { Update-Dl $d.Name $it.Length $d.Size }
    }
    if ($G.Dl.Count -eq 0) {
        foreach ($p in @(Get-ChildItem -LiteralPath $G.DlDir -Filter '*.download' -File -ErrorAction SilentlyContinue)) {
            Update-Dl ($p.Name.Substring(0, $p.Name.Length - 9)) $p.Length 0
        }
    }
}

# ---------------------------------------------------------------------------------------------------------------
# 8. What each run means for the flow.

function Start-Checks([string]$kind) {  # this PC's checks for CheckFor ('look' answers every question no)
    $G.ItTag = New-List; $G.ItText = New-List; $G.Fixable = New-List; $G.Problems = 0; $G.Sel = 0; $G.Scroll = 0
    Set-Stage 0; $G.View = 'checking'; $G.CheckedRole = $G.CheckFor
    $a = @($G.CheckFor, '-ChecksOnly')
    if ($G.CheckFor -eq 'forge' -and $script:F.Name) { $a += @('-Name', $script:F.Name) }
    if ($Yes -and $kind -eq 'fix') { $a += '-Yes' }   # the look never changes the machine
    Start-Run $kind $a
}

function Get-CheckNote {
    $ok = 0; $fx = 0
    foreach ($t in $G.ItTag) { if ($t -eq 'ok') { $ok++ } elseif ($t -in 'fix', 'fail') { $fx++ } }
    if ($fx) { return "$ok passed, $fx to fix" }
    return "$ok passed"
}

function Complete-RunKind([string]$kind, [int]$code) {
    switch ($kind) {
        'guess' { if (-not $G.Role) { $G.Role = $G.Guess }; $G.CheckFor = $G.Role; Start-Checks 'look' }
        'look' { $G.View = 'checks'; $G.Focus = 0; $G.Sel = 0; for ($k = 0; $k -lt $G.ItTag.Count; $k++) { if ($G.ItTag[$k] -in 'fix', 'fail') { $G.Sel = $k; break } }; if ($G.Problems -eq 0 -and $code -ne 0) { $G.Problems = 1 }; $G.StepNote[0] = Get-CheckNote }
        'fix' { if ($code -eq 0) { $script:DemoFixed = 1 }; $G.CheckFor = $G.CheckedRole; Start-Checks 'look' }
        'role' { Complete-RoleRun $code }
    }
}

function Complete-RoleRun([int]$code) {
    $joined = ($G.RunLines -join "`n")
    if ($code -eq 0) {
        if ($G.View -eq 'done') { return }
        if ($G.Role -eq 'forge' -and $G.PairCode -and -not $G.Paired) { Set-Stage 4; $G.View = 'pair' } elseif ($G.Role -eq 'workstation') { Start-VmGuide } else { Complete-Wizard }
        return
    }
    if ($G.Role -eq 'forge' -and $joined -like '*Plan consent required*' -and $G.PlanDigest) {
        Set-Stage 3; $G.View = 'plan'; $G.Typed = ''; $G.Focus = 0; $G.Scroll = 0
        if ($G.Digest -and $G.Digest -ne $G.PlanDigest) { $G.StatusMsg = 'The plan changed since you typed its digest; read it again.' }
        $G.Digest = ''; $G.StepNote[2] = Get-DlNote; return
    }
    if ($G.Role -eq 'forge' -and $joined -like '*the Forge is already paired*') {
        $G.SoftDone = 'No pairing window opened. If this Forge is already paired with your Workstation, it is done.'
        Complete-Wizard; return
    }
    Show-Failure
}

function Show-Failure {
    if ($null -ne $G.LastStep -and $G.LastStep -lt $G.Stage) { $G.StepState[$G.Stage] = 'pending'; $G.Stage = $G.LastStep }
    $G.FailStage = $G.Stage; $G.StepState[$G.Stage] = 'failed'
    $G.FailTitle = $StepNames[$G.Stage] + ' stopped'
    $lines = $G.RunLines.ToArray(); if ($lines.Count -gt 80) { $lines = $lines[($lines.Count - 80)..($lines.Count - 1)] }
    $G.FailRows = Resolve-Failure $G.Statuses $lines
    $G.View = 'fail'; $G.Focus = 0; $G.Dirty = $true
    Save-State 'failed'
}

function Complete-Wizard {
    for ($i = 0; $i -lt 5; $i++) { if ($G.StepState[$i] -notin 'skipped', 'later') { $G.StepState[$i] = 'done' } }
    $G.StepState[5] = 'current'; $G.Stage = 5; $G.View = 'done'; $G.Focus = 0; $G.Dirty = $true; $G.ReachedDone = $true
    Save-State 'done'
}

function Get-DlNote { $n = $G.Dl.Count; if (-not $n) { return '' }; $d = 0; foreach ($x in $G.Dl) { if ($x.State -eq 'done') { $d++ } }; return "$d of $n files" }

function Start-RoleRun {
    $a = @($G.Role)
    if ($Yes) { $a += '-Yes' }
    switch ($G.Role) {
        'forge' {
            if ($F.Root) { $a += @('-DataRoot', $F.Root) }
            if ($F.Name) { $a += @('-Name', $F.Name) }
            if (Write-ForgeSettings) { $a += @('-Settings', $script:SettingsFile) }
            if ($G.Digest) { $a += @('-PlanDigest', $G.Digest) }
        }
        'headend' { Invoke-ConnectHandover; return }
    }
    $G.ItTag = New-List; $G.ItText = New-List; $G.Recent = New-List; $G.Activity = 'Starting'; $G.Plan = New-List; $G.PlanDigest = ''; $G.Phase = 0
    $G.Xs = New-List; $G.XfStart = 0; $G.XfLast = 0; $G.PhaseAt = Get-NowMs
    if (-not $G.Consent) { $G.Dl = [System.Collections.Generic.List[object]]::new() }
    $ri = Get-RoleIndex $G.Role; $G.Attempt[$ri]++; $G.AttemptNow = $G.Attempt[$ri]
    Set-Stage 2; $G.View = 'running'
    if ($G.Role -eq 'forge' -and $G.Digest) { Set-Stage 3 }
    Save-State 'running'
    Start-Run 'role' $a
}

function Write-ForgeSettings {  # the Forge's non-secret settings for the installer, in the wizard's state folder
    if ($G.Role -ne 'forge') { return $false }
    $script:SettingsFile = Join-Path $script:StateDir 'forge-settings.json'
    if ($Demo) { return $true }
    $ct = 'false'; if ($F.Ct -eq 'yes') { $ct = 'true' }
    $json = "{`n  ""format"": ""deep32-settings"",`n  ""format_version"": 1,`n  ""role"": ""forge"",`n  ""node_name"": ""$($F.Name)"",`n  ""user_name"": ""$($F.User)"",`n  ""time_zone"": ""$($F.Tz)"",`n  ""keyboard"": ""$($F.Kb)"",`n  ""profile"": ""$($F.Profile)"",`n  ""transport"": ""tailnet"",`n  ""publish"": ""serve-https"",`n  ""forge_accept_ct"": $ct,`n  ""written_by"": ""deep32-wizard""`n}`n"
    [IO.File]::WriteAllText($script:SettingsFile, $json, ([System.Text.UTF8Encoding]::new($false)))
    return $true
}

function Save-State([string]$status) {
    if ($Demo -or -not $script:StateDir) { return }
    $t = "role=$($G.Role)`nstage=$($G.Stage)`nstatus=$status`nwhen=$(Get-Date -Format 'yyyy-MM-dd HH:mm')`nroot=$($F.Root)`nname=$($F.Name)`nws=$($F.Url)`nagent=$($F.Agent)`ntz=$($F.Tz)`nuser=$($F.User)`nct=$($F.Ct)`n"
    try { [IO.File]::WriteAllText((Join-Path $script:StateDir 'wizard.state'), $t) } catch { }
}

function Import-State {
    $G.Resume = ''
    if ($Demo) { return }
    $f = Join-Path $script:StateDir 'wizard.state'
    if (-not (Test-Path -LiteralPath $f)) { return }
    foreach ($l in [IO.File]::ReadAllLines($f)) { $i = $l.IndexOf('='); if ($i -gt 0) { $G.Rs[$l.Substring(0, $i)] = $l.Substring($i + 1) } }
    if ($G.Rs['status'] -eq 'done') { return }
    if ($G.Rs['role']) { $G.Resume = $G.Rs['role'] }
}

# Connect reads the pairing code from the console itself, so the screen goes back to plain text while it runs.

function Invoke-Handover([string]$title, [string[]]$argv) {
    Exit-Screen
    [Console]::Out.Write("`r`n  $($St.B)$title$($St['0'])`r`n`r`n")
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = Get-HostExe; $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.WorkingDirectory = $script:WorkDir
    $psi.EnvironmentVariables['DEEP32_WIZARD_DEMO_ATTEMPT'] = [string]$G.AttemptNow
    $p = [System.Diagnostics.Process]::Start($psi)
    # Characters, not lines: "Type merge to approve this file plan: " and [y/N] show before the answer is typed.
    $ob = [char[]]::new(4096); $eb = [char[]]::new(4096)
    $o = $p.StandardOutput.ReadAsync($ob, 0, 4096); $e2 = $p.StandardError.ReadAsync($eb, 0, 4096); $oe = $false; $ee = $false
    $pending = [System.Text.StringBuilder]::new()
    while (-not ($oe -and $ee)) {
        $busy = $false
        if (-not $oe -and $o.IsCompleted) { $n = $o.Result; if ($n -le 0) { $oe = $true } else { $t = [string]::new($ob, 0, $n); [Console]::Out.Write($t); [Console]::Out.Flush(); [void]$pending.Append($t); $o = $p.StandardOutput.ReadAsync($ob, 0, 4096) }; $busy = $true }
        if (-not $ee -and $e2.IsCompleted) { $n = $e2.Result; if ($n -le 0) { $ee = $true } else { $t = [string]::new($eb, 0, $n); [Console]::Out.Write($t); [Console]::Out.Flush(); [void]$pending.Append($t); $e2 = $p.StandardError.ReadAsync($eb, 0, 4096) }; $busy = $true }
        $text = $pending.ToString(); $cut = $text.LastIndexOf("`n")
        if ($cut -ge 0) { foreach ($l in $text.Substring(0, $cut).Split("`n")) { Write-WizLog $l.TrimEnd("`r") }; [void]$pending.Remove(0, $cut + 1) }
        if (-not $busy) { Start-Sleep -Milliseconds 20 }
    }
    if ($pending.Length) { Write-WizLog $pending.ToString() }
    $p.WaitForExit(); $G.HandoverStatus = $p.ExitCode
    [Console]::Out.Write("`r`n  $($St.MUTED)Press Enter to go back to the setup screen.$($St['0']) ")
    if (-not [Console]::IsInputRedirected) { while ($true) { $k = [Console]::ReadKey($true); if ($k.Key -eq 'Enter') { break } } }
    Enter-Screen
}

function Invoke-ConnectHandover {
    Set-Stage 3; $G.StepState[2] = 'skipped'
    $ri = Get-RoleIndex 'headend'; $G.Attempt[$ri]++; $G.AttemptNow = $G.Attempt[$ri]
    Save-State 'running'
    $title = 'Connect runs here in plain text. When it asks for the code, type the code the Workstation shows (it is not shown as you type).'
    if ($Demo) { $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WizSelf, '-DemoReplay', 'headend') }
    else {
        $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WizSetup, 'headend', '-Workstation', $F.Url)
        if ($F.Agent -and $F.Agent -ne 'auto') { $argv += @('-Agent', $F.Agent) }
        if ($Yes) { $argv += '-Yes' }
    }
    $before = $G.Log.Count
    Invoke-Handover $title $argv
    $G.RunLines = New-List; for ($i = $before; $i -lt $G.Log.Count; $i++) { $G.RunLines.Add($G.Log[$i]) }
    $G.Statuses = " setup:$($G.HandoverStatus)"
    if ($G.HandoverStatus -eq 0) { $G.StepState[3] = 'done'; $G.StepState[4] = 'done'; $G.Paired = $true; Complete-Wizard }
    else { Set-Stage 3; Show-Failure }
}

# ---------------------------------------------------------------------------------------------------------------
# 9. Drawing. Every line is exactly W cells; only lines that changed are written.

function Set-Layout {
    $script:Top = 0; if ($script:H -ge 28) { $script:Top = 1 }
    $script:RW = 26; if ($script:W -ge 110) { $script:RW = 28 }
    $script:PW = $script:W - 7 - $script:RW
    $script:BoxTop = $script:Top + 3; $script:BoxBot = $script:H - 3; $script:CH = $script:BoxBot - $script:BoxTop - 1
    $script:CW = $script:PW - 4
}

# Panel lines: PL starts one, PA appends a styled piece (clipped), PE pads it to the panel width and keeps it.
function PL([string]$bg = '') { $script:Lb = [System.Text.StringBuilder]::new(); [void]$Lb.Append($bg + '  '); $script:Lw = 2; $script:Lbg = $bg }
function PA([string]$style, [string]$t) {
    $room = $PW - 2 - $Lw
    if ($room -le 0) { return }
    if ($t.Length -gt $room) { $t = $t.Substring(0, $room - 1) + $Gl.ELL }
    [void]$Lb.Append($Lbg + $style + $t + $St['0']); $script:Lw += $t.Length
}
function PRaw([string]$s) { [void]$Lb.Append($s) }
function PGap([int]$n) { if ($Lw -lt $n) { [void]$Lb.Append($Lbg + (' ' * ($n - $Lw)) + $St['0']); $script:Lw = $n } }
function PE { $n = $PW - $Lw; if ($n -gt 0) { [void]$Lb.Append($Lbg + (' ' * $n) + $St['0']) }; $Panel.Add($Lb.ToString()) }
function PBlank { $Panel.Add(' ' * $PW) }
function PLine([string]$style, [string]$t) { PL; PA $style $t; PE }
function PDrop { if ($Panel.Count) { $Panel.RemoveAt($Panel.Count - 1) } }
function PFill { while ($Panel.Count -lt $CH) { PBlank } }
function PBottom([int]$n) { $target = $CH - $n - 1; while ($Panel.Count -lt $target) { PBlank } }

function Get-Wrapped([int]$width, [string]$text) {
    if ($width -lt 8) { $width = 8 }
    $out = [System.Collections.Generic.List[string]]::new(); $line = ''
    foreach ($w in $text.Split([char[]]' ', [StringSplitOptions]::RemoveEmptyEntries)) {
        if (-not $line) { $line = $w } elseif ($line.Length + 1 + $w.Length -le $width) { $line += ' ' + $w } else { $out.Add($line); $line = $w }
        while ($line.Length -gt $width) { $out.Add($line.Substring(0, $width)); $line = $line.Substring($width) }
    }
    if ($line) { $out.Add($line) }
    if ($out.Count -eq 0) { $out.Add('') }
    return , $out
}

function PPara([string]$style, [string]$text, [int]$ind = 0) {
    foreach ($l in (Get-Wrapped ($CW - $ind) $text)) { PL; PA '' (' ' * $ind); PA $style $l; PE }
}

function PCmd([string]$text) {  # append text to the open line, `commands` in the accent colour
    $parts = $text.Split('`')
    for ($i = 0; $i -lt $parts.Count; $i++) {
        if ($i -gt 0) { $script:CmdIn = -not $CmdIn }
        if ($parts[$i]) { if ($CmdIn) { PA $St.ACCENTT $parts[$i] } else { PA '' $parts[$i] } }
    }
}

function PSteps([int]$max, [string[]]$steps) {  # numbered steps; prose wraps, each `command` gets a line of its own
    for ($i = 0; $i -lt $steps.Count -and $i -lt $max; $i++) { PStep ($i + 1) '' 5 $steps[$i] }
}

function PStep([string]$num, [string]$label, [int]$col, [string]$text) {  # one numbered item; a bold label before the text when given
    $script:StepFirst = $true
    $lead = {
        PL
        if ($script:StepFirst) { if ($num -match '^[0-9]') { PA $St.ACCENT (' {0}.  ' -f $num) } else { PA $St.FAINT (' {0}   ' -f $num) }; if ($label) { PA $St.B $label }; $script:StepFirst = $false }
        PGap ($col + 2)
    }
    $parts = $text.Split('`')
    for ($p = 0; $p -lt $parts.Count; $p++) {
        if ($p % 2 -eq 0) {
            $prose = $parts[$p].TrimStart(' ', ',', '.', ';', ':')
            if ($prose -and $prose -notmatch '^[.,;:]$') { foreach ($l in (Get-Wrapped ($CW - $col) $prose)) { . $lead; PA '' $l; PE } }
        } elseif ($parts[$p]) {
            $cmd = $parts[$p]; $room = $CW - 7; $keepCol = $col; $col = 5   # whole, broken only at spaces when it is too long
            while ($cmd.Length -gt $room) {
                $part = $cmd.Substring(0, $room); $cut = $part.LastIndexOf(' '); if ($cut -gt 10) { $part = $part.Substring(0, $cut) }
                . $lead; PA $St.ACCENTT ('  ' + $part); PE
                $cmd = '  ' + $cmd.Substring($part.Length).TrimStart(' ')
            }
            . $lead; PA $St.ACCENTT ('  ' + $cmd); PE
            $col = $keepCol
        }
    }
}

function PTitle([string]$title, [string]$sub = '', [string]$icon = '', [string]$istyle = '') {
    PBlank
    PL; if ($icon) { PA ($istyle + $St.B) $icon; PA '' ' ' }; PA $St.TITLE $title; PE
    if ($sub) { PPara $St.MUTED $sub }
    PBlank
}

function PButtons([string[]]$labels) {
    if ($G.Focus -ge $labels.Count) { $G.Focus = 0 }
    PL
    for ($i = 0; $i -lt $labels.Count; $i++) {
        if ($i -eq $G.Focus) { PA ($St.BTNBG + $St.BTNFG + $St.B) (' ' + $labels[$i] + ' ') } else { PA ($St.IDLEBG + $St.IDLEFG) (' ' + $labels[$i] + ' ') }
        PA '' '  '
    }
    PE; $G.BtnCount = $labels.Count
}

function BxTop([string]$style, [string]$title) { $hz = $Gl.H * $CW; PL; PA $style ($Gl.TL + $Gl.H + ' '); PA ($style + $St.B) $title; PA $style (' ' + $hz.Substring(0, $CW - 5 - $title.Length) + $Gl.TR); PE }
function BxBot([string]$style, [string]$tail = '') { $hz = $Gl.H * $CW; PL; PA $style ($Gl.BL + $hz.Substring(0, $CW - 2 - $tail.Length)); if ($tail) { PA $St.FAINT $tail }; PA $style $Gl.BR; PE }
function BxOpen([string]$style) { PL; PA $style $Gl.V; PA '' '  '; $script:BxStyle = $style }
function BxClose { PGap ($PW - 3); PA $BxStyle $Gl.V; PE }
function BxText([string]$style, [string]$text) {
    $script:CmdIn = $false
    foreach ($l in (Get-Wrapped ($CW - 6) $text)) { BxOpen $BxStyle; if ($style) { PA $style $l } else { PCmd $l }; BxClose }
}
function BxButtons([string[]]$labels) {
    BxOpen $BxStyle
    for ($i = 0; $i -lt $labels.Count; $i++) {
        if ($i -eq $G.Focus) { PA ($St.BTNBG + $St.BTNFG + $St.B) (' ' + $labels[$i] + ' ') } else { PA ($St.IDLEBG + $St.IDLEFG) (' ' + $labels[$i] + ' ') }
        PA '' '  '
    }
    BxClose; $G.BtnCount = $labels.Count
}

function Get-ShortPath([string]$p) {
    $tmp = [IO.Path]::GetTempPath().TrimEnd([char]92)
    if ($tmp -and $p.StartsWith($tmp, [StringComparison]::OrdinalIgnoreCase)) { return '$env:TEMP' + $p.Substring($tmp.Length) }
    if ($env:LOCALAPPDATA -and $p.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) { return '$env:LOCALAPPDATA' + $p.Substring($env:LOCALAPPDATA.Length) }
    if ($env:USERPROFILE -and $p.StartsWith($env:USERPROFILE, [StringComparison]::OrdinalIgnoreCase)) { return '$env:USERPROFILE' + $p.Substring($env:USERPROFILE.Length) }
    return $p
}

function Build-Rail {
    $script:Rail = [System.Collections.Generic.List[string]]::new()
    $Rail.Add(' ' * $RW)
    for ($i = 0; $i -lt 6; $i++) {
        $s = $G.StepState[$i]; $mark = ' '; $cap = $G.StepNote[$i]
        switch ($s) {
            'done' { $ic = $Gl.OK; $is = $St.OK; $ls = $St.MUTED }
            'current' { $ic = $Gl.CUR; $is = $St.ACCENT; $ls = $St.B; $mark = $Gl.MARK }
            'failed' { $ic = $Gl.FAIL; $is = $St.ERR; $ls = $St.B; $mark = $Gl.MARK }
            'skipped' { $ic = $Gl.SKIP; $is = $St.FAINT; $ls = $St.FAINT; if (-not $cap) { $cap = 'not needed here' } }
            'later' { $ic = $Gl.PEND; $is = $St.FAINT; $ls = $St.MUTED; if (-not $cap) { $cap = 'on the Workstation' } }
            default { $ic = $Gl.PEND; $is = $St.FAINT; $ls = $St.MUTED }
        }
        if ($i -eq 1 -and $G.Role -and $s -eq 'done' -and -not $cap) { $cap = $RoleNames[(Get-RoleIndex $G.Role)] }
        if ($i -eq 2 -and $s -eq 'current' -and -not $cap) { $cap = Get-DlNote }
        if ($i -eq 4 -and $s -eq 'current' -and $G.PairCode -and -not $G.Paired) { $cap = 'code, ' + (Format-Clock ([math]::Floor(($G.PairUntil - (Get-NowMs)) / 1000))) + ' left' }
        $ms = $St.ACCENT; if ($s -eq 'failed') { $ms = $St.ERR }
        $Rail.Add($ms + $mark + $St['0'] + ' ' + $is + $ic + $St['0'] + '  ' + $ls + $StepNames[$i] + $St['0'] + (' ' * ($RW - 5 - $StepNames[$i].Length)))
        if ($cap) {
            if ($cap.Length -gt $RW - 6) { $cap = $cap.Substring(0, $RW - 7) + $Gl.ELL }
            $Rail.Add('     ' + $St.MUTED + $cap + $St['0'] + (' ' * ($RW - 5 - $cap.Length)))
        } else { $Rail.Add(' ' * $RW) }
        if ($CH -ge 20) { $Rail.Add(' ' * $RW) }
    }
    while ($Rail.Count -lt $CH) { $Rail.Add(' ' * $RW) }
    if ($Demo) { $tag = 'Demo: nothing changes'; $Rail[$CH - 2] = '  ' + $St.WARN + $tag + $St['0'] + (' ' * ($RW - 2 - $tag.Length)) }
}

function Build-Header {
    $right = $script:VersionId
    if ($G.Role -and $G.Stage -ge 1) { $right = $RoleNames[(Get-RoleIndex $G.Role)] + '  ' + $Gl.DOT + '  ' + $right }
    $script:Hdr1 = '  ' + $St.WORD + 'D E E P ' + $St['0'] + $St.WORD32 + '3 2' + $St['0'] + '   ' + $St.MUTED + 'setup' + $St['0'] +
        (' ' * ($W - 21 - $right.Length - 3)) + $St.MUTED + $right + $St['0'] + '   '
    Update-Progress
    $lit = Get-StripLit $G.Perm
    $sb = [System.Text.StringBuilder]::new(); [void]$sb.Append('  '); $vis = 0
    for ($i = 0; $i -lt 32; $i++) {
        if ($i -lt 8) { $c = $Gl.WIDE; $s = $St.STRIP1 } elseif ($i -lt 16) { $c = $Gl.MED; $s = $St.STRIP2 } else { $c = $Gl.NARROW; $s = $St.STRIP3 }
        if ($i -ge $lit) { $s = $St.TRACK; if ($i -eq $lit -and $G.Pulse -and $G.Proc) { $s = $St.STRIP3 } }
        [void]$sb.Append($s + $c + $St['0']); $vis += $c.Length
        if ($i -lt 16) { [void]$sb.Append(' '); $vis++ }
    }
    $pct = '{0}%' -f [math]::Floor($G.Perm / 10); $sw = 'Step {0} of 6' -f ($G.Stage + 1)
    if ($W - 2 - $vis - $sw.Length - $pct.Length - 5 -ge 2) { [void]$sb.Append((' ' * ($W - 2 - $vis - $sw.Length - $pct.Length - 5)) + $St.MUTED + $sw + $St['0'] + '  ' + $St.B + $pct + $St['0'] + '   ') }
    else { [void]$sb.Append((' ' * ($W - 2 - $vis - $pct.Length - 3)) + $St.B + $pct + $St['0'] + '   ') }
    $script:Hdr2 = $sb.ToString()
}

function Build-Footer {
    $v = $G.View; if ($G.Overlay) { $v = $G.Overlay }
    $keys = switch ($v) {
        'log' { @(($Gl.UP + $Gl.DOWN), 'scroll', 'PgUp/PgDn', 'page', 'Esc', 'close') }
        'help' { @('Esc', 'close') }
        'quit' { @('Enter', 'choose', 'Esc', 'stay') }
        { $_ -in 'ask', 'vmconfirm', 'vmpin' } { @('Left/Right', 'choose', 'Enter', 'confirm') }
        'vmsecret' { @('type', 'the secret', 'Enter', 'use', 'Esc', 'cancel') }
        { $_ -in 'hv', 'where', 'net' } { @(($Gl.UP + $Gl.DOWN), 'choose', 'Enter', 'continue', 'Esc', 'back', 'L', 'log', 'Q', 'quit') }
        'vmform' { @(($Gl.UP + $Gl.DOWN), 'field', 'Enter', 'continue', 'Esc', 'back') }
        'vmstep' { @('Enter', 'check', 'S', 'skip', 'PgDn', 'more', 'Esc', 'step back', 'L', 'log', 'Q', 'quit') }
        'role' { @(($Gl.UP + $Gl.DOWN), 'choose', 'Enter', 'continue', 'Esc', 'back', 'L', 'log', '?', 'help', 'Q', 'quit') }
        'form' { @(($Gl.UP + $Gl.DOWN), 'field', 'Enter', 'continue', 'Esc', 'back') }
        { $_ -in 'checking', 'running', 'download', 'install', 'pairwait', 'signin' } { @('L', 'log', '?', 'help', 'Q', 'quit') }
        'plan' { @('type', 'the digest', 'Enter', 'install', 'Esc', 'back', 'L', 'log', 'Q', 'quit') }
        'checks' { @(($Gl.UP + $Gl.DOWN), 'details', 'Enter', 'choose', 'Esc', 'back', 'L', 'log', '?', 'help', 'Q', 'quit') }
        'parts' { @(($Gl.UP + $Gl.DOWN), 'part', 'Left/Right', 'answer', 'Enter', 'continue', 'Esc', 'back', 'Q', 'quit') }
        'runintro' { if ($G.Role -eq 'forge') { @(($Gl.UP + $Gl.DOWN), 'scroll', 'Enter', 'back', 'L', 'log', '?', 'help', 'Q', 'quit') } else { @('Enter', 'continue', 'Esc', 'back', 'L', 'log', '?', 'help', 'Q', 'quit') } }
        'done' { if ($G.NextOffer) { @(($Gl.UP + $Gl.DOWN), 'scroll', 'N', 'set up the next part here', 'Enter', 'choose', 'L', 'log', 'Q', 'quit') } else { @(($Gl.UP + $Gl.DOWN), 'scroll', 'Enter', 'close', 'L', 'log', 'Q', 'quit') } }
        default { @('Enter', 'continue', 'Esc', 'back', 'L', 'log', '?', 'help', 'Q', 'quit') }
    }
    $sb = [System.Text.StringBuilder]::new(); [void]$sb.Append('   '); $vis = 3
    for ($i = 0; $i -lt $keys.Count; $i += 2) {
        [void]$sb.Append($St.B + $keys[$i] + $St['0'] + ' ' + $St.MUTED + $keys[$i + 1] + $St['0'] + '    '); $vis += $keys[$i].Length + $keys[$i + 1].Length + 5
    }
    if ($vis -gt $W) { $sb = [System.Text.StringBuilder]::new(); [void]$sb.Append('   '); $vis = 3 }
    $script:Foot = $sb.ToString() + (' ' * ($W - $vis))
    $msg = $G.StatusMsg; $logp = ''
    if ($script:LogFile) { $logp = 'Log  ' + (Get-ShortPath $script:LogFile); if ($Demo) { $logp = 'Demo log  ' + (Get-ShortPath $script:LogFile) } }
    $most = [math]::Floor($W / 2) + 6
    if ($logp.Length -gt $most) { $logp = $Gl.ELL + $logp.Substring($logp.Length - ($most - 1)) }
    $room = $W - 6 - $logp.Length
    if ($msg.Length -gt $room) { if ($room -gt 1) { $msg = $msg.Substring(0, $room - 1) + $Gl.ELL } else { $msg = '' } }
    $script:StatusL = '   ' + $St.WARN + $msg + $St['0'] + (' ' * ($W - 6 - $msg.Length - $logp.Length)) + $St.FAINT + $logp + $St['0'] + '   '
}

function Build-Frame {
    $script:Frame = [string[]]::new($H)
    $blank = ' ' * $W
    for ($i = 0; $i -lt $H; $i++) { $Frame[$i] = $blank }
    if ($W -lt 80 -or $H -lt 24) { Build-Small; return }
    Build-Header; $Frame[$Top] = $Hdr1; $Frame[$Top + 1] = $Hdr2
    Build-Footer; $Frame[$H - 2] = $StatusL; $Frame[$H - 1] = $Foot
    $script:Panel = [System.Collections.Generic.List[string]]::new()
    if ($G.Overlay -in 'log', 'help') {
        $hz = $Gl.H * ($W - 6)
        $Frame[$BoxTop] = '  ' + $St.BORDER + $Gl.TL + $hz + $Gl.TR + $St['0'] + '  '
        $Frame[$BoxBot] = '  ' + $St.BORDER + $Gl.BL + $hz + $Gl.BR + $St['0'] + '  '
        $save = $PW; $script:PW = $W - 6; $script:CW = $PW - 4
        if ($G.Overlay -eq 'log') { View-Log } else { View-Help }
        PFill
        for ($i = 0; $i -lt $CH; $i++) { $Frame[$BoxTop + 1 + $i] = '  ' + $St.BORDER + $Gl.V + $St['0'] + $Panel[$i] + $St.BORDER + $Gl.V + $St['0'] + '  ' }
        $script:PW = $save; $script:CW = $PW - 4
        return
    }
    $hr = $Gl.H * $RW; $hp = $Gl.H * $PW
    $Frame[$BoxTop] = '  ' + $St.BORDER + $Gl.TL + $hr + $Gl.TT + $hp + $Gl.TR + $St['0'] + '  '
    $Frame[$BoxBot] = '  ' + $St.BORDER + $Gl.BL + $hr + $Gl.BT + $hp + $Gl.BR + $St['0'] + '  '
    Build-Rail
    switch ($G.View) {
        'intro' { View-Intro } 'checking' { View-Checks } 'checks' { View-Checks } 'signin' { View-Signin } 'role' { View-Role } 'parts' { View-Parts }
        'form' { View-Form } 'runintro' { View-RunIntro } 'running' { View-Running } 'download' { View-Download } 'plan' { View-Plan }
        'install' { View-Install } 'pairwait' { View-Install } 'pair' { View-Pair } 'fail' { View-Fail } 'done' { View-Done }
        'hv' { View-Hv } 'vmkit' { View-VmKit } 'where' { View-Where } 'net' { View-Net } 'tsnote' { View-TsNote } 'vmform' { View-Form }
        'vmstep' { View-VmStep } 'vmsum' { View-VmSum }
    }
    if ($G.Overlay -eq 'ask') { View-Ask } elseif ($G.Overlay -eq 'quit') { View-Quit } elseif ($G.Overlay -in 'vmconfirm', 'vmpin', 'vmsecret') { View-VmBox }
    PFill
    for ($i = 0; $i -lt $CH; $i++) { $Frame[$BoxTop + 1 + $i] = '  ' + $St.BORDER + $Gl.V + $St['0'] + $Rail[$i] + $St.BORDER + $Gl.V + $St['0'] + $Panel[$i] + $St.BORDER + $Gl.V + $St['0'] + '  ' }
}

function Build-Small {
    $lines = @(@('Make the window bigger', $St.B), @("The setup needs at least 80 columns and 24 rows; this window is $W x $H.", $St.MUTED), @('Press P to continue in plain text, or Q to quit.', $St.MUTED))
    $r = [math]::Max(0, [math]::Floor($H / 2) - 2)
    $rows = @($r, ($r + 2), ($r + 3))
    for ($k = 0; $k -lt 3; $k++) {
        if ($rows[$k] -ge $H) { continue }
        $t = $lines[$k][0]; if ($t.Length -gt $W) { $t = $t.Substring(0, $W) }
        $pad = [math]::Max(0, [math]::Floor(($W - $t.Length) / 2))
        $Frame[$rows[$k]] = (' ' * $pad) + $lines[$k][1] + $t + $St['0'] + (' ' * ($W - $pad - $t.Length))
    }
}

function Add-Prof([string]$what) { if ($env:DEEP32_WIZARD_PROFILE) { $script:Prof.Add(('{0} {1:N1}' -f $what, $script:ProfClock.Elapsed.TotalMilliseconds)) } }

function Write-Frame {  # write the lines that changed since the last frame, inside one synchronized update
    $t0 = $script:ProfClock.Elapsed.TotalMilliseconds
    Build-Frame
    $t1 = $script:ProfClock.Elapsed.TotalMilliseconds
    $sb = [System.Text.StringBuilder]::new()
    for ($i = 0; $i -lt $H; $i++) {
        if (-not $script:Full -and $script:Prev -and $i -lt $Prev.Count -and $Frame[$i] -ceq $Prev[$i]) { continue }
        [void]$sb.Append("$E[$($i + 1);1H" + $Frame[$i])
    }
    $script:Prev = $Frame; $script:Full = $false
    if ($G.Snap) { [void]$sb.Append("$E]7777;snap;$($G.Snap)" + [char]7); $G.Snap = '' }
    if ($sb.Length) {  # synchronized output where the terminal is known to take it; the classic console gets the plain lines
        if ($env:WT_SESSION -or $env:TERM_PROGRAM) { Write-Out ("$E[?2026h" + $sb.ToString() + "$E[?2026l") } else { Write-Out $sb.ToString() }
    }
    $G.Dirty = $false
    if ($env:DEEP32_WIZARD_PROFILE) {
        $t2 = $script:ProfClock.Elapsed.TotalMilliseconds
        if (-not $script:FirstFrame) { $script:FirstFrame = $true; Add-Prof 'first-frame' }
        $script:Prof.Add(('frame {0}x{1} build {2:N1} write {3:N1}' -f $W, $H, ($t1 - $t0), ($t2 - $t1)))
    }
}

function Write-Out([string]$s) { $b = $script:Utf8.GetBytes($s); $script:StdOut.Write($b, 0, $b.Length); $script:StdOut.Flush() }

# ---------------------------------------------------------------------------------------------------------------
# 10. The screens.

function View-Intro {
    PTitle 'Check this machine' 'The setup reads what this machine has before anything changes. It stops and tells you whenever something needs you.'
    PLine $St.B 'What happens'
    PSteps 5 @('Check this machine: read-only, a few seconds.', 'Choose its role: Forge (the GPU node), Workstation host or agent PC.',
        'Download and verify the signed discs this role needs.', 'Install: you read the plan first and agree to it.', 'Pair: a short code typed on the Workstation.')
    PBlank
    if ($G.Resume) {
        PPara $St.WARN ("Last time the {0} setup stopped ({1}). Resume keeps its settings; finished downloads are kept." -f $RoleNames[(Get-RoleIndex $G.Resume)], $G.Rs['when'])
        PBottom 1; PButtons @('Resume', 'Start over', 'Quit')
    } else { PBottom 1; PButtons @('Start the check', 'Quit') }
}

function Get-ItemIcon([string]$tag) {
    switch ($tag) {
        'ok' { return @($Gl.OK, $St.OK) } 'fix' { return @($Gl.FIX, ($St.WARN + $St.B)) } 'fail' { return @($Gl.FAIL, $St.ERR) }
        'you' { return @($Gl.YOU, $St.ACCENT) } default { return @($Gl.INFO, $St.FAINT) }
    }
}

function View-Checks {
    $rn = $RoleNames[(Get-RoleIndex $G.CheckedRole)]
    if ($G.View -eq 'checking') { PTitle 'Check this machine' "Reading what this machine has for the $rn role. Nothing changes in this step." }
    else {
        $fx = 0; foreach ($x in $G.ItTag) { if ($x -eq 'fix' -or $x -eq 'fail') { $fx++ } }
        if ($fx) { $s = ''; if ($fx -gt 1) { $s = 's' }; PTitle 'Check this machine' "For the $rn role. $fx thing$s to fix; each one says what to do." }
        else { PTitle 'Check this machine' "Ready for the $rn role. Nothing was changed." $Gl.OK $St.OK }
    }
    $n = $G.ItTag.Count; $rows = $CH - $Panel.Count - 3
    if ($G.View -eq 'checks') { $rows -= 7 }
    if ($rows -lt 3) { $rows = 3 }
    if ($G.Sel -ge $n) { $G.Sel = $n - 1 }; if ($G.Sel -lt 0) { $G.Sel = 0 }
    if ($G.Sel -lt $G.Scroll) { $G.Scroll = $G.Sel }
    if ($G.Sel -ge $G.Scroll + $rows) { $G.Scroll = $G.Sel - $rows + 1 }
    if ($G.View -eq 'checking' -and $n -gt $rows) { $G.Scroll = $n - $rows }
    for ($i = $G.Scroll; $i -lt $n -and $i -lt $G.Scroll + $rows; $i++) {
        $ic = Get-ItemIcon $G.ItTag[$i]
        if ($G.View -eq 'checks' -and $i -eq $G.Sel) { PL $St.SELBG; PA $St.ACCENT $Gl.MARK; PA $ic[1] $ic[0]; PA '' '  '; PA $St.B $G.ItText[$i]; PE }
        else {
            PL; PA '' ' '; PA $ic[1] $ic[0]; PA '' '  '
            if ($G.ItTag[$i] -in 'fix', 'fail') { PA '' $G.ItText[$i] } else { PA $St.MUTED $G.ItText[$i] }
            PE
        }
    }
    if ($G.View -eq 'checking') {
        PBlank; PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; $a = $G.Activity; if (-not $a) { $a = 'Checking' }; PA $St.MUTED ('  ' + $a); PE
        return
    }
    if ($n -gt $rows) { PL; PA $St.FAINT ('   ' + $Gl.UP + $Gl.DOWN + ' ' + ($n - $rows) + ' more'); PE }
    PBlank
    PBottom 7
    if ($n) {
        $tag = $G.ItTag[$G.Sel]; $text = $G.ItText[$G.Sel]
        if ($tag -in 'fix', 'fail') {
            $gi = Get-Guidance $text
            if ($gi -ge 0) { PL; PA $St.B 'What to do'; PE; PSteps 4 (Get-FixSteps $gi) } else { PPara '' $text }
        } else { PPara $St.MUTED $text }
    }
    while ($Panel.Count -gt $CH - 2) { PDrop }
    PBottom 1
    $b = @('Continue'); if ($G.Fixable.Count) { $b = @('Fix here', 'Continue') }; $b += 'Check again'
    $G.Btns = $b; PButtons $b
}

function View-Signin {
    PTitle 'Sign in to Tailscale' 'This machine joins your tailnet. Tailscale is waiting for the sign-in; the setup goes on by itself afterwards.'
    PL; PA $St.ACCENT ' 1.  '; PA '' 'On any device (a phone works), open this address:'; PE
    PBlank
    $url = $G.SigninUrl; if (-not $url) { $url = 'waiting for Tailscale to print the address' }
    $inner = [math]::Min($url.Length + 4, $CW - 7)
    if ($url.Length -gt $inner - 4) { $url = $url.Substring(0, $inner - 5) + $Gl.ELL }
    $hz = $Gl.H * $inner
    PL; PA '' '     '; PA $St.ACCENT ($Gl.TL + $hz + $Gl.TR); PE
    PL; PA '' '     '; PA $St.ACCENT $Gl.V; PA $St.CHIPBG '  '
    if ($G.SigninUrl) {
        if ($GlyphMode -eq 'rich') { PRaw ("$E]8;;" + $G.SigninUrl + "$E\") }
        PA ($St.CHIPBG + $St.ACCENTT + $St.B) $url
        if ($GlyphMode -eq 'rich') { PRaw ("$E]8;;$E\") }
    } else { PA ($St.CHIPBG + $St.MUTED) $url }
    PA $St.CHIPBG (' ' * ($inner - 2 - $url.Length)); PA $St.ACCENT $Gl.V; PE
    PL; PA '' '     '; PA $St.ACCENT ($Gl.BL + $hz + $Gl.BR); PE
    PBlank
    $rest = @('Sign in with your own Tailscale account: the same tailnet the Workstation and your other Deep32 machines use.',
        'Approve this machine if your tailnet asks for approval. Then come back here.')
    for ($i = 0; $i -lt 2; $i++) {
        $k = 0; foreach ($l in (Get-Wrapped ($CW - 5) $rest[$i])) { PL; if ($k -eq 0) { PA $St.ACCENT (' {0}.  ' -f ($i + 2)) } else { PA '' '     ' }; PA '' $l; PE; $k = 1 }
    }
    PBottom 1
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED '  Waiting for the sign-in to finish'; PE
}

function View-Role {
    PTitle 'Choose role' 'What should this machine be? You can run the setup again later on each of your other machines.'
    $desc = @('The GPU node: embeddings, reranker and PDF converter in Docker Desktop. Needs an NVIDIA card; pairs with the Workstation.',
        'Downloads and checks discs 1 and 2 and writes the Hyper-V settings for the Workstation VM. Creates no VM by itself.',
        'Connects Claude Code or Codex on this PC to your Workstation, with a pairing code.')
    for ($i = 0; $i -lt 3; $i++) {
        $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
        PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK; PA $St.ACCENT $Gl.CUR } else { PA '' ' '; PA $St.FAINT $Gl.PEND }
        PA '' '  '; PA $St.B $RoleNames[$i]
        if ($RoleIds[$i] -eq $G.Guess) { PGap ($PW - 12); PA $St.OK 'suggested' }
        PE
        foreach ($l in (Get-Wrapped ($CW - 4) $desc[$i])) { PL $bg; PA '' '    '; if ($sel) { PA '' $l } else { PA $St.MUTED $l }; PE }
        PBlank
    }
    if ($G.Guess) {
        $hint = 'The check suggests Forge: an NVIDIA card was found'
        if ($G.Guess -eq 'forge' -and $G.GuessGpu) { $hint += " (class $($G.GuessGpu))" }
        if ($G.Guess -eq 'headend') { $hint = 'The check suggests Agent PC: Claude Code or Codex is installed here' }
        if ($G.Guess -eq 'workstation') { $hint = 'The check suggests Workstation host: no NVIDIA card was found' }
        PPara $St.FAINT ($hint + '.')
    }
}

function Set-Form {
    switch ($G.Role) {
        'forge' {
            $G.FormKeys = @('Name', 'Root', 'Tz', 'User', 'Ct')
            $G.FormLabel = @('Forge name', 'Data folder', 'Time zone', 'Local user', 'Name in public logs')
            $G.FormHelp = @('Its Tailscale name. It must start with deep32- for the Workstation to offer it.',
                'Where the Forge keeps its data. Use the same folder for every later action.', 'For the Forge services.',
                'The account the Forge services run as.',
                'HTTPS certificates publish this Forge name in public Certificate Transparency logs. Type yes to agree; the Forge needs HTTPS.')
        }
        'workstation' { $G.FormKeys = @('Hv'); $G.FormLabel = @('Hypervisor'); $G.FormHelp = @('hyper-v on Windows: a Generation 1 VM. The VM settings are written for it; no VM is created.') }
        'headend' {
            $G.FormKeys = @('Url', 'Agent'); $G.FormLabel = @('Workstation address', 'Agent')
            $G.FormHelp = @('The https://...ts.net/mcp address the Workstation printed when you published it (on the Workstation, as root: d32-publish publish --accept-ct).',
                'claude, codex or both. auto picks what is installed here.')
        }
    }
}

function Test-Form {  # what is wrong with the form, or ''
    switch ($G.Role) {
        'forge' {
            if ($F.Ct -ne 'yes') { return 'The Forge publishes over HTTPS; type yes to agree that its name appears in public logs.' }
            if ($F.User -cnotmatch '^[a-z_][a-z0-9_-]{0,30}$' -or $F.User -eq 'root') { return 'The local user must be a plain account name, not root.' }
            if ($F.Tz -cnotmatch '^[A-Za-z_]+(/[A-Za-z_+-]+)*$') { return 'The time zone looks wrong; use a name such as Europe/London or Etc/UTC.' }
            if ($F.Root -notmatch '^[A-Za-z]:\\') { return 'The data folder must be a full path, such as C:\Deep32.' }
            if ($F.Name -cnotmatch '^deep32-[a-z0-9-]{1,32}$') { return 'The Forge name must start with deep32-, use only a-z, 0-9 and -, and be at most 39 characters.' }
            if ($F.Name -cmatch '(sk-[a-z0-9]|password|passwd|token|secret|cookie|private-?key|auth-?key|pair(ing)?-?code|[a-f0-9]{32})') { return 'The installer refuses names that look like secrets (token, secret, password, key, cookie); choose another name.' }
        }
        'workstation' { if ($F.Hv -cne 'hyper-v') { return 'On Windows the VM files are for Hyper-V; the hypervisor is hyper-v.' } }
        'headend' {
            if ($F.Agent -cnotmatch '^(auto|claude|codex|both)$') { return 'Agent: auto, claude, codex or both.' }
            if ($F.Url -notmatch '^https://.+/mcp$') { return 'The address must start with https:// and end with /mcp.' }
        }
    }
    return ''
}

function View-Form {
    $ftitle = $RoleNames[(Get-RoleIndex $G.Role)] + ' settings'; if ($G.View -eq 'vmform') { $ftitle = $G.FormTitle }
    PTitle $ftitle 'Check these, then continue. Up and down choose a field; type to change it.'
    $labw = 22
    for ($i = 0; $i -lt $G.FormKeys.Count; $i++) {
        $val = [string]$F[$G.FormKeys[$i]]; $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
        PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK } else { PA '' ' ' }
        if ($sel) { PA $St.B (' ' + $G.FormLabel[$i]) } else { PA $St.MUTED (' ' + $G.FormLabel[$i]) }
        PGap ($labw + 2); PA $St.CHIPBG ' '
        if ($sel) { PA ($St.CHIPBG + $St.B) $val; PA ($St.CHIPBG + $St.ACCENT) $Gl.CURSOR } else { PA $St.CHIPBG $val }
        PA $St.CHIPBG ' '; PE
        if ($sel) { foreach ($l in (Get-Wrapped ($CW - $labw - 1) $G.FormHelp[$i].Replace('`', ''))) { PL; PGap ($labw + 3); PA $St.FAINT $l; PE } }
    }
    PBlank
    if ($G.View -eq 'vmform') { $err = Test-VmForm } else { $err = Test-Form }; if ($err) { PPara $St.WARN $err }
    PBottom 1; $G.Btns = @('Continue', 'Back'); PButtons $G.Btns
}

function View-RunIntro {
    if ($G.Role -eq 'forge') { View-ForgeByHand; return }
    switch ($G.Role) {
        'forge' {
            PTitle 'Download and verify' 'What happens next on this PC, in order:'
            $cls = $G.GuessGpu; if (-not $cls) { $cls = 'of this card' }
            $steps = @("Download the GPU pack for class ${cls}: the shared core, the embedder and the ingestion image, into $(Get-ShortPath $WorkDir)\deep32-downloads.",
                'Check each image against the signed release. A stopped download resumes where it stopped.',
                "Show the installer's plan: folders, ports and images. Nothing is installed until you type its digest.",
                "Install into $($F.Root), start the Forge and show the pairing code.")
        }
        'workstation' {
            PTitle 'Download and verify' 'What happens next on this PC, in order:'
            $steps = @("Download disc 1 (Workstation, about 1.9 GB) and disc 2 (content, about 19.6 GB) into $(Get-ShortPath (Join-Path (Split-Path -Parent $WorkDir) 'deep32-downloads')).",
                'Check each disc against the signed release. A stopped download resumes where it stopped.',
                "Write the VM files and the commands that create it for $($F.Hv). No VM is created or started.")
        }
        'headend' {
            PTitle 'Connect your agent' 'What happens next on this PC, in order:'
            $ag = $F.Agent; if ($ag -eq 'auto') { $ag = 'your agent' }
            $steps = @("Connect checks $ag and the Workstation gateway at $($F.Url).",
                'On the Workstation, open Pairing and choose Headend. It shows a ten-character code that works once, for five minutes.',
                'The screen switches to plain text while Connect runs. When it asks, type the code; it is not shown as you type.',
                'Connect asks before it changes any existing agent files. Then this screen comes back.')
        }
    }
    PSteps 5 $steps
    PBlank
    if ($G.Role -eq 'forge' -and -not $Demo) { PPara $St.MUTED 'The Forge install needs this PowerShell to run as administrator; the check said whether it does.' }
    PBottom 1
    if ($G.Role -eq 'headend') { $G.Btns = @('Start Connect', 'Back') } else { $G.Btns = @('Start', 'Back') }
    PButtons $G.Btns
}

function Get-ForgeByHand {  # INSTALL.md section 5 (Windows) with the options the alpha.2 installer asks for
    $cls = $G.GuessGpu; if (-not $cls) { $cls = 'CLASS' }
    $get = 'powershell -NoProfile -ExecutionPolicy Bypass -File src\release-tools\deep32-get.ps1 disc3 --gpu-class ' + $cls
    return @(
        @('In PowerShell, not as administrator, in this folder, download disc 3 for this card:', ($get + ' --ingestion --to .\deep32-downloads')),
        @('In PowerShell as administrator (Start, type PowerShell, right-click, Run as administrator), install from the folder that holds the verified images and the signed release files:',
            ($get + ' --journey J1 --install --data-root C:\Deep32 --from FOLDER --confirm-tailnet YOUR-TAILNET.ts.net --confirm-desktop-isolation --docker-storage-path DIR')),
        @('FOLDER is where step 1 put the images, YOUR-TAILNET your tailnet name and DIR the folder of Docker Desktop''s disk image. The installer names any other option it needs; this wizard''s error notes explain its messages.', ''))
}

function Save-ForgeByHand {  # the same steps as a text file, so the commands can be copied whole
    $path = Join-Path $StateDir 'forge-by-hand.txt'
    if ($G.ByHandFile -eq $path) { return }
    $sb = [System.Text.StringBuilder]::new(); $n = 0
    [void]$sb.AppendLine('Deep32 ' + $VersionId + ': the Forge on Windows, by hand (INSTALL.md section 5)')
    foreach ($st2 in (Get-ForgeByHand)) { $n++; [void]$sb.AppendLine(''); [void]$sb.AppendLine("$n. " + $st2[0]); if ($st2[1]) { [void]$sb.AppendLine('   ' + $st2[1]) } }
    try { [IO.File]::WriteAllText($path, $sb.ToString(), $Utf8); $G.ByHandFile = $path } catch { $G.ByHandFile = '' }
}

function View-ForgeByHand {
    PTitle 'The Forge on Windows: two commands by hand' 'In this release deep32-setup.ps1 cannot install the Windows Forge: it starts the installer without options it requires. Run these instead:'
    Save-ForgeByHand
    $headLines = $script:Panel
    $script:Panel = [System.Collections.Generic.List[string]]::new()
    $n = 0
    foreach ($st2 in (Get-ForgeByHand)) { $n++; $t = $st2[0]; if ($st2[1]) { $t += ' `' + $st2[1] + '`' }; PStep $n '' 5 $t }
    $body = $script:Panel
    $script:Panel = $headLines
    $room = [math]::Max(3, $CH - $Panel.Count - 3); $total = $body.Count
    if ($G.Scroll -gt $total - $room) { $G.Scroll = $total - $room }; if ($G.Scroll -lt 0) { $G.Scroll = 0 }
    for ($i = $G.Scroll; $i -lt $total -and $i -lt $G.Scroll + $room; $i++) { $Panel.Add($body[$i]) }
    PBottom 2
    $more = $total - $G.Scroll - $room; $tail = ''; if ($more -gt 0) { $tail = "$($Gl.DOWN) $more more lines   " }
    PL; PA $St.FAINT $tail; if ($G.ByHandFile) { PA $St.FAINT 'Saved: '; PA $St.MUTED (Get-ShortPath $G.ByHandFile) }; PE
    $G.Btns = @('Back'); PButtons $G.Btns
}

function View-Running {
    PTitle ('Setting up the ' + $RoleNames[(Get-RoleIndex $G.Role)]) 'Checking this machine again before anything changes.'
    $n = $G.ItTag.Count; $rows = $CH - $Panel.Count - 3; $start = 0; if ($n -gt $rows) { $start = $n - $rows }
    for ($i = $start; $i -lt $n; $i++) { $ic = Get-ItemIcon $G.ItTag[$i]; PL; PA '' ' '; PA $ic[1] $ic[0]; PA '' '  '; PA $St.MUTED $G.ItText[$i]; PE }
    PBottom 1; $a = $G.Activity; if (-not $a) { $a = 'Working' }
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED ('  ' + $a); PE
}

function View-Download {
    $what = 'Disc 3: the GPU pack images for this card, from the signed release.'
    if ($G.Role -eq 'workstation') { $what = 'Disc 1 (Workstation) and disc 2 (content), from the signed release.' }
    if ($G.Consent) { $what = 'Checking the downloaded images again before the install.' }
    PTitle 'Download and verify' $what
    $tot = 0; $have = 0; $nd = 0; $nf = $G.Dl.Count; $known = $true; $bw = [math]::Max(10, $CW - 28)
    if ($nf -eq 0) { PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED '  Reading the signed release'; PE }
    foreach ($d in $G.Dl) {
        $tot += $d.Size; $have += $d.Have; if ($d.Size -eq 0) { $known = $false }
        switch ($d.State) {
            'done' { $stl = 'verified ' + $Gl.OK; $sts = $St.OK; $nd++ } 'check' { $stl = 'verifying'; $sts = $St.ACCENT } 'down' { $stl = 'downloading'; $sts = $St.ACCENT }
            'stopped' { $stl = 'stopped ' + $Gl.FAIL; $sts = $St.ERR } default { $stl = 'waiting'; $sts = $St.FAINT }
        }
        $label = $d.Name; $pre = 'deep32-' + $script:VersionId + '-'; if ($label.StartsWith($pre)) { $label = $label.Substring($pre.Length) }
        $room = $CW - $stl.Length - 3; if ($label.Length -gt $room) { $label = $label.Substring(0, $room - 1) + $Gl.ELL }
        PL; if ($d.State -eq 'wait') { PA $St.MUTED $label } else { PA $St.B $label }; PGap ($PW - 2 - $stl.Length); PA $sts $stl; PE
        $pm = Get-Permille $d.Have $d.Size
        $bc = Get-BarCells $pm $bw
        $bs = $St.ACCENT; if ($d.State -eq 'done') { $bs = $St.OK }; if ($d.State -eq 'stopped') { $bs = $St.ERR }
        $fill = $Gl.BAR * $bc[0]; if ($bc[1]) { $fill += $Gl.BARH }
        $track = $Gl.TRACK * ($bw - $bc[0] - $bc[1])
        if ($bc[1] -and $GlyphMode -eq 'rich' -and $track.Length) { $track = ' ' + $track.Substring(1) }
        $hv = Format-Bytes $d.Have; $sz = Format-Bytes $d.Size
        switch ($d.State) { 'done' { $info = $sz } 'wait' { $info = "$sz to get" } 'check' { $info = "$sz, check in progress" } default { $info = "$hv of $sz  $($Gl.DOT)  $([math]::Floor($pm / 10))%" } }
        if ($d.Size -eq 0) { $info = "$hv so far" }
        PL; PA $bs $fill; PA $St.TRACK $track; PA '' '  '; PA $St.MUTED $info; PE
        if ($CH -ge 20) { PBlank }
    }
    # The whole transfer: size and files done and to go, the speed over the last 30 s, the time left once it
    # means something, and the time so far. What the tools do not report is said, not guessed. The pieces are
    # packed into as few lines as the panel is wide, so none is cut off in a small window.
    $now = Get-NowMs; $wr = Get-WindowRate ($G.Xs -join ' '); $rate = $wr[0]; $span = $wr[1]
    $soFar = ''; if ($G.XfStart) { $soFar = (Format-Clock ([math]::Floor(($now - $G.XfStart) / 1000))) + ' so far' }
    # the window is 30 s once that much has been seen; before that it says how long it really is
    $winS = [math]::Min(30, [math]::Floor(($span + 500) / 1000))
    $speed = 'measuring the speed'; if ($rate -ge 0) { $speed = (Format-Bytes $rate) + "/s over the last $winS s" }
    $dot = '  ' + $Gl.DOT + '  '
    $dlHead = ''; $dlBold = ''; $dlParts = [System.Collections.Generic.List[string]]::new()
    if ($nf -gt 0 -and $known) {
        $pct = [math]::Floor((Get-Permille $have $tot) / 10); $remain = $tot - $have
        $eta = Get-EtaSeconds $remain $rate $span
        $dlHead = 'Total  '; $dlBold = (Format-Bytes $have) + ' of ' + (Format-Bytes $tot)
        $dlParts.Add($dlHead + $dlBold); $dlParts.Add("$pct%"); $dlParts.Add("$nd of $nf files done")
        if ($G.Consent) { $dlParts.Add('the check reports only finished images') }
        else { $dlParts.Add((Format-Bytes $remain) + ' to go'); $dlParts.Add($speed); if ($eta -ge 0) { $dlParts.Add((Format-Eta $eta)) } }
    } elseif ($nf -gt 0) {
        $dlHead = 'Downloaded  '; $dlBold = Format-Bytes $have
        $dlParts.Add($dlHead + $dlBold); $dlParts.Add("$nd of $nf files done"); $dlParts.Add('the total size is not reported yet'); $dlParts.Add($speed)
    }
    if ($nf -gt 0 -and $soFar) { $dlParts.Add($soFar) }
    $dlLines = [System.Collections.Generic.List[string]]::new(); $dlLine = ''
    foreach ($dlp in $dlParts) {
        if (-not $dlLine) { $dlLine = $dlp } elseif ($dlLine.Length + $dot.Length + $dlp.Length -le $CW) { $dlLine += $dot + $dlp } else { $dlLines.Add($dlLine); $dlLine = $dlp }
    }
    if ($dlLine) { $dlLines.Add($dlLine) }
    if ($dlLines.Count -eq 0) { $dlLines.Add(' '); $dlLines.Add(' ') }
    PBottom ($dlLines.Count + 1)
    for ($i = 0; $i -lt $dlLines.Count; $i++) {
        if ($i -eq 0 -and $dlHead) { PL; PA $St.MUTED $dlHead; PA $St.B $dlBold; PA $St.MUTED $dlLines[0].Substring($dlHead.Length + $dlBold.Length); PE }
        else { PL; PA $St.MUTED $dlLines[$i]; PE }
    }
    $last = $G.LastRaw; if (-not $last) { $last = $G.Activity }; if (-not $last) { $last = 'Working' }
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.FAINT ('  ' + $last); PE
}

function Add-XferSample {  # twice a second while files move: the total so far, kept for the last 30 s
    $now = Get-NowMs
    if ($now - $G.XfLast -lt 500) { return }
    $G.XfLast = $now
    if ($G.Dl.Count -eq 0) { return }
    $have = 0; foreach ($d in $G.Dl) { $have += $d.Have }
    if (-not $G.XfStart) { $G.XfStart = $now }
    $G.Xs.Add("${now}:$have")
    while ($G.Xs.Count -gt 2 -and [long]($G.Xs[0].Split(':')[0]) -lt $now - 31000) { $G.Xs.RemoveAt(0) }
}


function View-Plan {
    PTitle 'Install' "The Forge installer's plan. Read it, then type its digest to install."
    $box = [math]::Max(3, $CH - $Panel.Count - 10)
    $rows = [System.Collections.Generic.List[string]]::new()
    foreach ($l in $G.Plan) {
        if ($l -match '^( *)"([^"]+)": (.*)$') {
            $ind = $Matches[1]; $k = $Matches[2]; $v = $Matches[3].TrimEnd(','); if ($v.StartsWith('"')) { $v = $v.Substring(1) }; if ($v.EndsWith('"')) { $v = $v.Substring(0, $v.Length - 1) }
            if ($k -in 'digest', 'sha256', 'media_sha256') { continue }
            if ($v -in '{', '[') { $v = '' }
            $pre = ''; if ($ind.Length -gt 2) { $pre = $ind.Substring(2) }
            $rows.Add($pre + $k + "`t" + $v)
        } elseif ($l -match '^ +"([^"]+)",?$') { $rows.Add('      ' + $Matches[1] + "`t") }
    }
    if ($G.Scroll -gt $rows.Count - $box) { $G.Scroll = $rows.Count - $box }; if ($G.Scroll -lt 0) { $G.Scroll = 0 }
    BxTop $St.BORDER 'plan'
    $shown = 0; $kw = 26
    for ($i = $G.Scroll; $i -lt $rows.Count -and $shown -lt $box; $i++) {
        $parts = $rows[$i].Split("`t"); $k = $parts[0]; $v = $parts[1]
        $max = $CW - $kw - 5; if ($v.Length -gt $max) { $v = $v.Substring(0, $max - 1) + $Gl.ELL }
        BxOpen $St.BORDER; PA $St.MUTED $k; PGap ($kw + 2); PA '' $v; BxClose
        $shown++
    }
    $more = $rows.Count - $G.Scroll - $shown; $tail = ''; if ($more -gt 0) { $tail = " $($Gl.DOWN) $more more, PgDn " }
    BxBot $St.BORDER $tail
    PBlank
    PPara '' "Installing writes to $($F.Root), loads the Docker images and starts the Forge services. To agree, type this digest:"
    $d = $G.PlanDigest; $g1 = @(); $g2 = @()
    for ($j = 0; $j -lt 32; $j += 4) { $g1 += $d.Substring($j, 4) }; for ($j = 32; $j -lt 64; $j += 4) { $g2 += $d.Substring($j, 4) }
    PL; PA '' '   '; PA ($St.CHIPBG + $St.ACCENTT + $St.B) (' ' + ($g1 -join ' ') + ' '); PE
    PL; PA '' '   '; PA ($St.CHIPBG + $St.ACCENTT + $St.B) (' ' + ($g2 -join ' ') + ' '); PE
    $t = $G.Typed; $out = $false
    PL; PA $St.ACCENT ' > '
    for ($j = 0; $j -lt $t.Length; $j++) {
        if ($j -eq 32) { PE; PL; PA '' '   ' }
        if ($j -gt 0 -and $j % 4 -eq 0 -and $j -ne 32) { PA '' ' ' }
        if ($t[$j] -ceq $d[$j]) { PA $St.OK ([string]$t[$j]) } else { PA ($St.ERR + $St.U) ([string]$t[$j]); $out = $true }
    }
    if ($t.Length -lt 64) { PA $St.ACCENT $Gl.CURSOR }; PE
    if ($t.Length -le 32) { PBlank }
    PBottom 1
    if ($G.Typed -ceq $G.PlanDigest) { $G.Btns = @('Install', 'Back'); PButtons $G.Btns }
    elseif ($out) { PLine $St.ERR 'A character does not match; Backspace fixes it.' }
    else { PLine $St.FAINT ('{0} characters to go. Spaces are fine; pasting works.' -f (64 - $G.Typed.Length)) }
}

function View-Install {
    if ($G.View -eq 'pairwait') { PTitle 'Pair' 'The Forge is installed. Its pairing window opens in a moment.' }
    elseif ($G.Role -eq 'workstation') { PTitle 'Install' 'Writing the VM files for the Workstation.' }
    else { PTitle 'Install' 'Installing the Forge from the checked images. This takes a few minutes.' }
    if ($G.Role -eq 'forge') { $ph = @('Plan accepted', 'This host checked again', 'Images mounted read-only', 'Forge loader running', 'Settings handed over', 'Forge started; its doors answer with a token request') }
    else { $ph = @('Disc 1 checked again', 'VM files and commands written') }
    $cur = $G.Phase; if ($G.Role -eq 'forge' -and $cur -lt 1) { $cur = 1 }
    $spent = Format-Clock ([math]::Floor(((Get-NowMs) - $G.PhaseAt) / 1000))
    for ($i = 0; $i -lt $ph.Count; $i++) {
        PL; PA '' ' '
        if ($i -lt $cur) { PA $St.OK $Gl.OK; PA '' '  '; PA $St.MUTED $ph[$i] }
        elseif ($i -eq $cur) { PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA '' '  '; PA $St.B $ph[$i]; PA $St.MUTED ('  ' + $Gl.DOT + '  ' + $spent + ' so far') }
        else { PA $St.FAINT $Gl.PEND; PA '' '  '; PA $St.FAINT $ph[$i] }
        PE
    }
    PPara $St.FAINT 'The installer reports no progress inside these steps, so the wizard shows the time so far and no percentage or time left.'
    PBlank
    PL; PA $St.FAINT 'What the installer says'; PE
    $from = [math]::Max(0, $G.Recent.Count - 4)
    for ($i = $from; $i -lt $G.Recent.Count; $i++) { PL; PA $St.FAINT ('  ' + $G.Recent[$i].Replace("`t", '    ')); PE }
    PBottom 1; $a = $G.Activity; if (-not $a) { $a = 'Working' }
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED ('  ' + $a); PE
}


function View-Pair {
    PTitle 'Pair' 'The Forge waits for the Workstation. The code works once, for five minutes.'
    $left = [math]::Floor(($G.PairUntil - (Get-NowMs)) / 1000)
    $c = $G.PairCode; $code = $c.Substring(0, [math]::Min(5, $c.Length)) + '  ' + $Gl.DOT + '  ' + $(if ($c.Length -gt 5) { $c.Substring(5) } else { '' })
    $spaced = ($code.ToCharArray() | ForEach-Object { [string]$_ }) -join ' '
    if ($spaced.Length + 13 -gt $CW) { $spaced = $code }
    $inner = $spaced.Length + 6; $hz = $Gl.H * $inner
    PL; PA '' '   '; PA $St.ACCENT ($Gl.TL + $hz + $Gl.TR); PE
    PL; PA '' '   '; PA $St.ACCENT $Gl.V; PA $St.CHIPBG '   '; PA ($St.CHIPBG + $St.B + $St.ACCENTT) $spaced; PA $St.CHIPBG '   '; PA $St.ACCENT $Gl.V; PE
    PL; PA '' '   '; PA $St.ACCENT ($Gl.BL + $hz + $Gl.BR); PE
    PL; PA '' '    '
    if ($left -gt 0) { $s = $St.MUTED; if ($left -lt 60) { $s = $St.WARN }; PA $s ('Valid for {0} more' -f (Format-Clock $left)) }
    else { PA $St.ERR 'This code has expired. Choose New code; it runs the Forge setup again.' }
    PE
    PBlank
    $peer = $G.PairPeer; if (-not $peer) { $peer = 'this Forge' }
    PSteps 4 @('On the Workstation, open Pairing and choose Forge. From its terminal: `doas /usr/local/d32/sbin/d32-pairdesk -forge`', "Pick $peer.",
        'Type the code above. Spaces and the dot between the groups are fine.', 'Wait until the Workstation says the pairing is saved, then choose Paired here.')
    PBottom 1
    if ($left -gt 0) { $G.Btns = @('Paired', 'New code (runs the setup again)') } else { $G.Btns = @('New code (runs the setup again)', 'Paired') }
    PButtons $G.Btns
}

function View-Fail {
    PTitle $G.FailTitle '' $Gl.FAIL $St.ERR; PDrop
    PL; PA $St.B 'What the tools said'; PE
    $shown = 0
    foreach ($m in $G.FailRows) {
        if ($shown -ge 3) { break }
        $raw = $m[1]; if (-not $raw -or $raw -match '^\S+ exit \d+$') { continue }
        PL $St.ERRBG; PA $St.ERR ($Gl.QUOTE + ' '); PA '' $raw; PE; $shown++
    }
    $sl = @(); foreach ($stt in $G.Statuses.Split([char[]]' ', [StringSplitOptions]::RemoveEmptyEntries)) { if ($stt.EndsWith(':0')) { continue }; $n = ($stt -split ':')[0]; if ($n -in 'setup', 'get', 'inventory') { $n = 'deep32-' + $n }; $sl += "$n exit $(($stt -split ':')[1])" }
    PL $St.ERRBG; PA $St.ERR ($Gl.QUOTE + ' '); PA $St.MUTED ($sl -join '   '); PE
    PBlank
    if ($G.FailRows.Count) {
        $r = $G.FailRows[0][0]
        PL; PA $St.B 'What it means'; PE
        PPara '' $script:ErrRows[$r].Mean
        PBlank
        PL; PA $St.B 'What to do'; PE
        PSteps 5 (Get-FixSteps $r)
        if ($G.FailRows.Count -gt 1 -and $script:ErrRows[$G.FailRows[1][0]].Kind -ne 'exit' -and $script:ErrRows[$G.FailRows[1][0]].Key -ne '.') { PBlank; PPara $St.FAINT ('Also reported: ' + $script:ErrRows[$G.FailRows[1][0]].Mean) }
    }
    while ($Panel.Count -gt $CH - 3) { PDrop }
    PBottom 2
    PL; PA $St.FAINT 'Full log  '; PA $St.MUTED (Get-ShortPath $script:LogFile); PE
    $G.Btns = @('Try again', 'Back', 'Show the log'); PButtons $G.Btns
}

function View-Done {
    $head = switch ($G.Role) { 'forge' { 'This PC is now the Forge' } 'workstation' { 'The Workstation files are ready' } default { 'Your agent is connected' } }
    if ($G.SoftDone) { PTitle 'Done' $G.SoftDone $Gl.OK $St.OK } else { PTitle $head '' $Gl.OK $St.OK; PDrop }
    $did = switch ($G.Role) {
        'forge' { @('checked', ('{0} images verified' -f $G.Dl.Count), 'installed', $(if ($G.Paired) { 'paired' } else { 'code shown' })) }
        'workstation' { $wd = @('checked', 'discs 1 and 2 verified', 'VM files written'); if ($G.VmDone) { $wd += 'VM guide done' }; $wd }
        default { @('checked', 'agent paired', 'eleven tools checked') }
    }
    PL; foreach ($x in $did) { PA $St.OK ($Gl.OK + ' '); PA $St.MUTED $x; PA '' '  ' }; PE
    PBlank
    Update-NextPlan
    $steps = 0; foreach ($x in $G.Next) { if (-not $x.StartsWith('note|')) { $steps++ } }
    PL; PA $St.B "What's next"; if ($steps) { PA $St.MUTED '  in this order, on the machine named at the left' }; PE
    if (-not $steps) { PPara '' 'Nothing else is needed: every part you listed is set up.' }
    # The steps go into their own list, so a long one scrolls inside the panel and never pushes the buttons off.
    $headLines = $script:Panel
    $script:Panel = [System.Collections.Generic.List[string]]::new()
    $n = 0
    foreach ($it in $G.Next) {
        $fl = $it.Split('|'); $kind = $fl[0]; $where = $fl[1]; $text = $fl[2]; $cmds = $fl[3]
        $cmdText = ''; if ($cmds) { $cmdText = ' `' + ($cmds -replace '\^', '` `') + '`' }
        if ($kind -eq 'offer') { $n++; PStep $n $where 20 $text; PL; PGap 22; PA ($St.BTNBG + $St.BTNFG + $St.B) ' N '; PA $St.ACCENT '  press N to do this now, here'; PE }
        elseif ($kind -eq 'note') { PStep $Gl.DOT $where 20 ($text + $cmdText) }
        else { $n++; PStep $n $where 20 ($text + $cmdText) }
    }
    $body = $script:Panel
    $script:Panel = $headLines
    $room = [math]::Max(3, $CH - $Panel.Count - 3); $total = $body.Count
    if ($G.Scroll -gt $total - $room) { $G.Scroll = $total - $room }; if ($G.Scroll -lt 0) { $G.Scroll = 0 }
    for ($i = $G.Scroll; $i -lt $total -and $i -lt $G.Scroll + $room; $i++) { $Panel.Add($body[$i]) }
    PBottom 2
    $more = $total - $G.Scroll - $room; $tail = ''; if ($more -gt 0) { $tail = "$($Gl.DOWN) $more more lines   " }
    PL; PA $St.FAINT $tail; PA $St.FAINT 'Saved with every command: '; PA $St.MUTED (Get-ShortPath $G.NextFile); PE
    if ($G.NextOffer) { $G.Btns = @(('Set up the ' + $RoleNames[(Get-RoleIndex $G.NextOffer)] + ' here'), 'Close') } else { $G.Btns = @('Close') }
    PButtons $G.Btns
}

function Get-PartOf([string]$role) { switch ($role) { 'workstation' { 'ws' } 'headend' { 'agent' } default { $role } } }
function Get-RoleOf([string]$part) { switch ($part) { 'ws' { 'workstation' } 'agent' { 'headend' } default { $part } } }

function Get-Caps {  # what this PC could run: CapOs, CapShell, CapHv (Hyper-V for the VM), CapAgent; the GPU class comes from the guess
    $script:CapShell = 'ps'; $script:CapOs = 'windows'
    if ($env:DEEP32_WIZARD_OS) { $script:CapOs = $env:DEEP32_WIZARD_OS }
    if ($Demo) { $script:CapHv = $(if ($G.GuessHv) { $G.GuessHv } else { 'no' }); $script:CapAgent = $(if ($G.GuessAgent) { 'yes' } else { 'no' }); return }
    $script:CapHv = $(if (Get-Command New-VM -ErrorAction SilentlyContinue) { 'yes' } else { 'no' })
    $script:CapAgent = $(if ((Get-Command claude -ErrorAction SilentlyContinue) -or (Get-Command codex -ErrorAction SilentlyContinue)) { 'yes' } else { 'no' })
}

function Test-CanRun([string]$role) {  # this wizard can set ROLE up on this PC, by deep32-setup's own role and OS rules
    $here = "$($script:CapOs):$($script:CapShell)"; $native = $here -in 'linux:bash', 'windows:ps'
    switch ($role) {
        'forge' { return [bool]($G.GuessGpu -and $here -eq 'linux:bash') }   # deep32-setup.ps1 cannot finish the alpha.2 Windows Forge
        'workstation' { return [bool]($script:CapHv -eq 'yes' -and $native) }
        'headend' { return [bool]($script:CapAgent -eq 'yes' -and ($native -or $here -eq 'macos:bash')) }
    }
    return $false
}

function Set-PartDefaults {  # first answers for Your machines, from the guess and what this PC can run
    if ($G.Parts.ContainsKey('forge')) { return }
    $G.Parts['forge'] = $(if (Test-CanRun 'forge') { 'here' } else { 'unknown' })
    $G.Parts['ws'] = $(if ($G.Guess -eq 'workstation' -and (Test-CanRun 'workstation')) { 'here' } else { 'other' })
    $G.Parts['agent'] = $(if (Test-CanRun 'headend') { 'here' } else { 'other' })
    if ($G.Role) { $G.Parts[(Get-PartOf $G.Role)] = 'here' }
}

function Get-PartLabel([string]$v) {
    switch ($v) { 'here' { 'Set it up on this machine' } 'other' { 'Set it up on another machine' } 'done' { 'Already set up' } 'none' { 'Not using it' } default { "I don't know yet" } }
}

function Step-Part([string]$dir) {
    $key = $PartKeys[$G.Sel]
    $vals = @('here', 'other', 'done', 'none', 'unknown'); if ($key -eq 'ws') { $vals = @('here', 'other', 'done', 'unknown') }
    $i = [array]::IndexOf($vals, [string]$G.Parts[$key]); if ($i -lt 0) { $i = 0 }
    if ($dir -eq 'right') { $i = ($i + 1) % $vals.Count } else { $i = ($i - 1 + $vals.Count) % $vals.Count }
    $G.Parts[$key] = $vals[$i]
}

function Complete-Parts {  # Your machines answered: the role to set up here first, in journey order
    foreach ($key in $PartKeys) { if ($G.Parts[$key] -eq 'here' -and (Test-CanRun (Get-RoleOf $key))) { $G.Role = Get-RoleOf $key; break } }
    if (-not $G.Role) { $G.Role = $G.Guess }
    if ($Role -in 'forge', 'workstation', 'headend') { $G.Role = $Role }   # a role named on the command line wins
    $G.View = 'role'; $G.Sel = Get-RoleIndex $G.Role; $G.Focus = 0
}

function View-Parts {
    PTitle 'Your machines' 'Which parts do you have, and where? The answers decide what this run sets up here and what the wizard tells you to do on the other machines.'
    $hint = @('The Workstation runs as a VM on a Windows PC with Hyper-V or a Linux PC with KVM.', 'The Forge needs an NVIDIA card. Without one, search works on keywords only.',
        'The computer where you use Claude Code or Codex.')
    for ($i = 0; $i -lt 3; $i++) {
        $key = $PartKeys[$i]; $label = Get-PartLabel $G.Parts[$key]; $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
        PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK } else { PA '' ' ' }
        PA $St.B (' ' + $PartNames[$i]); PGap 22
        if ($sel) { PA $St.ACCENT '< '; PA ($St.CHIPBG + $St.B) (" $label "); PA $St.ACCENT ' >' } else { PA $St.MUTED "  $label" }
        PE
        if ($sel) {
            foreach ($l in (Get-Wrapped ($CW - 21) $hint[$i])) { PL; PGap 23; PA $St.FAINT $l; PE }
            if ($G.Parts[$key] -eq 'here' -and -not (Test-CanRun (Get-RoleOf $key))) {
                $why = switch ($key) {
                    'forge' { 'this machine cannot run the Forge: it needs an NVIDIA card, on Windows (or Linux with the bash wizard)' }
                    'ws' { 'this machine has no hypervisor for the Workstation VM (Hyper-V on Windows, KVM on Linux)' }
                    default { 'Claude Code or Codex is not installed here yet' }
                }
                foreach ($l in (Get-Wrapped ($CW - 21) "Note: $why.")) { PL; PGap 23; PA $St.WARN $l; PE }
            }
        }
        PBlank
    }
    PBottom 1; $G.Btns = @('Continue', 'Back'); PButtons $G.Btns
}

function Get-NextPlan([string]$role, [hashtable]$parts, [bool]$paired, [string]$peer, [string]$vm, [string]$wsHere = '', [bool]$vmDone = $false, [string]$vmSkip = '') {
    # wsHere: the VM files this session wrote before going on to another part here; their steps stay on the list.
    # What is left, in INSTALL.md's order (Workstation, its import, Forge and its pairing, publication, the agent PC and
    # its pairing, then the optional relay), each as KIND|WHERE|TEXT|CMD^CMD|ROLE.
    $out = [System.Collections.Generic.List[string]]::new()
    $ws = $parts['ws']; $fg = $parts['forge']; $ag = $parts['agent']
    if (-not $ws) { $ws = 'unknown' }; if (-not $fg) { $fg = 'unknown' }; if (-not $ag) { $ag = 'unknown' }
    switch ($role) { 'workstation' { $ws = 'done' } 'forge' { $fg = 'done' } 'headend' { $ag = 'done' } }
    $get = 'git clone https://github.com/hellobpdu/deep32.git && cd deep32 && bash install/deep32-wizard.sh^git clone https://github.com/hellobpdu/deep32.git; cd deep32; powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1'
    if (($role -eq 'workstation' -or $wsHere) -and $vmDone) {
        if ($vmSkip) { $out.Add("step|Workstation VM|Do the VM guide's steps you skipped ($vmSkip). Each command is in its steps file in the VM folder, or run the guide again:|bash install/deep32-wizard.sh --vm-guide^powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -VmGuide|") }
    } elseif ($role -eq 'workstation' -or $wsHere) {
        $v = $vm; if ($role -ne 'workstation') { $v = $wsHere }; if (-not $v) { $v = 'the VM folder' }
        $out.Add("step|This PC|Create the Workstation VM: read this file, change its 160 GB disk to 200 GB (the New-VHD or qemu-img line), then run its commands (Linux as root, Windows in PowerShell as administrator).|$v/commands.txt|")
        $out.Add('step|Workstation VM|Boot it from disc 1 and answer the installer. Install to the empty 200 GB disk, not the installer disk.||')
        $out.Add('step|Workstation|At first boot set the passwords and sign in to Tailscale with the address it shows.||')
        $out.Add('step|Workstation|Give it disc 2 in one of three ways: as a read-only CD drive (attach the disc 2 image to the VM); on a USB stick of 32 GB or more, with the image written to it by balenaEtcher or dd; or over the network, with Get Disc 2 over the Network in First Start, from a PC that sends it or from the release mirror. The import starts by itself: search works once the knowledge base is in, and the Library carries on in the background. From a terminal you can check it, and resume it if it stopped:|/usr/local/d32/libexec/d32-import-report^doas /usr/local/d32/sbin/d32-import-resume|')
        $out.Add('note|This PC|To send disc 2 from this PC over your tailnet, press S on this screen, or run it later (Linux as root, Windows in PowerShell as administrator):|bash install/deep32-wizard.sh --send-disc2^powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -SendDisc2|')
        $out.Add('note|This PC|The VM guide shows these steps for Proxmox VE, VMware, VirtualBox, Hyper-V, KVM or another machine, and checks each one:|bash install/deep32-wizard.sh --vm-guide^powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-wizard.ps1 -VmGuide|')
    } elseif ($ws -eq 'here' -and (Test-CanRun 'workstation')) {
        $out.Add('offer|This PC|Set up the Workstation host here: download discs 1 and 2 and write the VM files.||workstation')
    } elseif ($ws -ne 'done') {
        $out.Add("step|Workstation PC|On the PC that will host the Workstation VM (Windows with Hyper-V, or Linux with KVM), get Deep32 and run the wizard; choose Workstation host:|$get|")
    }
    if ($role -ne 'workstation' -and $ws -ne 'done') {
        $out.Add('step|Workstation|Install it from disc 1, sign it in to Tailscale at first boot, and wait until its Import Report shows the knowledge base in use before you pair anything with it. The Library can still be importing.||')
    }
    $pn = $peer; if (-not $pn) { $pn = 'the Forge' }
    $pairf = "Workstation|Once the knowledge base is in use, pair the Forge: open Pairing, choose Forge, pick $pn and type the code the Forge shows. Or from a terminal on the Workstation:|doas /usr/local/d32/sbin/d32-pairdesk -forge|"
    $gpuon = 'Workstation|Pairing turns the Forge models on. As root on the Workstation console, check that both show verified:|/usr/local/d32/sbin/d32-gpu-ext status|'
    if ($fg -eq 'done') { if ($role -eq 'forge') { if (-not $paired) { $out.Add("step|$pairf") }; $out.Add("step|$gpuon") } }
    elseif ($fg -eq 'none') { $out.Add('note|Forge|No Forge: search works on keywords only. A Forge can be added later.||') }
    else {
        if ($fg -eq 'here' -and (Test-CanRun 'forge')) { $out.Add('offer|This PC|Set up the Forge here: download, check and install disc 3, then show its pairing code.||forge') }
        else { $out.Add("step|GPU PC|On the PC with the NVIDIA card (Linux with Docker, or Windows with Docker Desktop), get Deep32 and run the wizard; choose Forge:|$get|") }
        $out.Add("step|$pairf"); $out.Add("step|$gpuon")
    }
    if ($ag -ne 'none' -and $ag -ne 'done') {
        $out.Add('step|Workstation|As root on the Workstation console, publish the gateway for agents (HTTPS names appear in public certificate logs). It prints the address the agent PC needs:|/usr/local/d32/sbin/d32-publish publish --accept-ct|')
        if ($ag -eq 'here' -and (Test-CanRun 'headend')) { $out.Add('offer|This PC|Set up the agent PC here: Connect pairs Claude Code or Codex with the Workstation.||headend') }
        elseif ($ag -eq 'here') { $out.Add('step|This PC|Install Claude Code or Codex here and sign in to it, then run the wizard again and choose Agent PC.||') }
        else { $out.Add("step|Agent PC|On the computer with Claude Code or Codex, get Deep32 and run the wizard; choose Agent PC and give it the address from publication:|$get|") }
        $out.Add('step|Workstation|When Connect asks for its code, open Pairing and choose Headend, or from a terminal:|doas /usr/local/d32/sbin/d32-pairdesk -headend|')
    }
    if ($ag -ne 'none') {
        $out.Add('note|Agent PC|Optional, for the Ask window: the relay (adapters/relay/README.md). Install it with --no-autostart and sign in, stop its service, then pair:|adapters/connect.sh --relay pair --relay-id INSTALL_ID --agent claude^powershell -NoProfile -ExecutionPolicy Bypass -File adapters\connect.ps1 -Relay pair -RelayId INSTALL_ID -Agent claude|')
        $out.Add("note|Workstation|At the same time, on the Workstation's text console (Ctrl+Alt+F1), run this and pick the agent PC. Then compare the ten words both sides show and type each side's words at the other side's prompt:|doas /usr/local/d32/sbin/d32-pairdesk -relay|")
    }
    return , $out
}

function Update-NextPlan {  # the plan for this screen, and the same list with every command in a text file to copy across
    $vm = ''; if ($G.VmLine) { $vm = Get-ShortPath $G.VmLine }
    $G.Next = Get-NextPlan $G.Role $G.Parts ([bool]$G.Paired) $G.PairPeer $vm ([string]$G.WsHereVm) ([bool]$G.VmDone) ((@($G.VmSkipped)) -join '; ')
    $G.NextOffer = ''; foreach ($it in $G.Next) { if ($it.StartsWith('offer|')) { $G.NextOffer = $it.Split('|')[4]; break } }
    $G.NextFile = Join-Path $script:StateDir 'next-steps.txt'
    if ($G.NextWritten -eq $G.Role) { return }
    $G.NextWritten = $G.Role
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append("Deep32: what's next after the $($G.Role) setup on this machine ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))`r`n`r`n")
    $n = 0
    foreach ($it in $G.Next) {
        $fl = $it.Split('|')
        if ($fl[0] -eq 'note') { [void]$sb.Append("- $($fl[1]): $($fl[2])`r`n") } else { $n++; [void]$sb.Append("$n. $($fl[1]): $($fl[2])`r`n") }
        if ($fl[0] -eq 'offer') { [void]$sb.Append("   (on this machine: run the wizard again and choose this role)`r`n") }
        foreach ($c in $fl[3].Split([char[]]'^', [StringSplitOptions]::RemoveEmptyEntries)) { [void]$sb.Append("     $c`r`n") }
        [void]$sb.Append("`r`n")
    }
    try { [IO.File]::WriteAllText($G.NextFile, $sb.ToString()) } catch { }
}

function Invoke-ContinueHere([string]$next) {  # N: set up the next part on this same machine, keeping what is already known
    $G.Parts[(Get-PartOf $G.Role)] = 'done'
    if ($G.Role -eq 'workstation') { $G.WsHereVm = 'the VM folder'; if ($G.VmLine) { $G.WsHereVm = Get-ShortPath $G.VmLine } }
    $G.Role = $next; $G.SkipParts = $true; $G.NextWritten = ''
    $G.StepState = @('current', 'pending', 'pending', 'pending', 'pending', 'pending'); $G.StepNote = @('', '', '', '', '', '')
    $G.Dl = [System.Collections.Generic.List[object]]::new(); $G.DlDir = ''
    $G.Consent = $false; $G.Digest = ''; $G.Plan = New-List; $G.PlanDigest = ''; $G.PairCode = ''; $G.PairPeer = ''; $G.Paired = $false
    $G.SoftDone = ''; $G.VmLine = ''; $G.Scroll = 0
    Set-Form
    $G.CheckFor = $next; Start-Checks 'look'
}


function View-Ask {
    $yesLabel = 'Yes'
    switch ($G.AskId) {
        'tailscale-signin' { $yesLabel = 'Yes, start the sign-in'; $expl = 'This runs `tailscale up` on this PC. Tailscale opens a sign-in page in your browser; the PC joins your tailnet once you sign in. Nothing else changes.' }
        'tailscale-rename' { $yesLabel = 'Yes, rename it'; $expl = 'This renames the PC on your tailnet with `tailscale set --hostname`. The Workstation only offers Forges whose name starts with deep32-.' }
        default { $expl = 'deep32-setup asks before it changes this PC.' }
    }
    $keep = [math]::Min(5, $Panel.Count); while ($Panel.Count -gt $keep) { PDrop }
    BxTop $St.ACCENT 'Before this step'
    $script:BxStyle = $St.ACCENT; BxText $St.B $G.AskText
    BxOpen $St.ACCENT; BxClose
    BxText '' $expl
    BxOpen $St.ACCENT; BxClose
    BxButtons @($yesLabel, 'Not now')
    BxBot $St.ACCENT
}

function View-Quit {
    $keep = [math]::Min(5, $Panel.Count); while ($Panel.Count -gt $keep) { PDrop }
    $msg = 'Nothing more changes. Run the wizard again to pick up where it stopped; finished downloads are kept.'
    if ($G.Proc) { $msg = 'A step is running. Quitting stops it and everything it started; finished downloads and verified parts are kept, and the next run resumes.' }
    BxTop $St.WARN 'Quit the setup?'
    $script:BxStyle = $St.WARN; BxText '' $msg
    BxOpen $St.WARN; BxClose
    BxButtons @('Stay', 'Quit')
    BxBot $St.WARN
}

function View-Log {
    PBlank
    PL; PA $St.B 'Log'; PA $St.MUTED ('   everything deep32-setup and its tools printed  ' + $Gl.DOT + '  ' + (Get-ShortPath $script:LogFile)); PE
    PBlank
    $rows = $CH - 4; $n = $G.Log.Count
    if ($G.LogScroll -gt $n - $rows) { $G.LogScroll = $n - $rows }; if ($G.LogScroll -lt 0) { $G.LogScroll = 0 }
    $start = [math]::Max(0, $n - $rows - $G.LogScroll)
    for ($i = $start; $i -lt $n -and $i -lt $start + $rows; $i++) {
        $l = $G.Log[$i]; $s = ''
        if ($l.StartsWith('[ok]')) { $s = $St.OK } elseif ($l.StartsWith('[fix]')) { $s = $St.WARN } elseif ($l.StartsWith('[fail]')) { $s = $St.ERR }
        elseif ($l.StartsWith('[you]')) { $s = $St.ACCENT } elseif ($l.StartsWith('{"deep32":') -or $l.StartsWith('---')) { $s = $St.FAINT }
        PL; PA $s ($l.Replace("`t", '    ').Replace([string]$E, '')); PE
    }
}

function View-Help {
    PBlank; PL; PA $St.B 'Help'; PE; PBlank
    $k = @('Enter', 'continue, or press the chosen button', 'Esc', 'go back one screen', ($Gl.UP + ' ' + $Gl.DOWN), 'choose a line, a role or a field',
        'Left Right Tab', 'choose a button', 'L', 'show everything the setup printed (the log)', 'Q', 'quit; the next run picks up where this one stopped', '?', 'this help')
    for ($i = 0; $i -lt $k.Count; $i += 2) { PL; PA ($St.ACCENT + $St.B) ('  ' + $k[$i]); PGap 22; PA '' $k[$i + 1]; PE }
    PBlank
    PPara $St.MUTED 'The wizard runs install\deep32-setup.ps1 and shows its steps. For the same plain tagged lines an agent reads, run the wizard with -Plain, or run deep32-setup.ps1 itself. INSTALL.md describes every step.'
}

# ---------------------------------------------------------------------------------------------------------------
# 10b. The Workstation VM guide (the twin of deep32-wizard.sh's section 10b).
#
# After the Workstation host role, or straight away with -VmGuide, the wizard asks which hypervisor runs the
# Workstation VM and where, reads this PC's network, then shows one exact step at a time. Every hypervisor value,
# step, rule and fix comes from install\deep32-wizard-hv.tsv, which deep32-wizard.sh reads too. Enter checks a step:
# the hypervisor's own read-only query on this PC, the optional read-only Proxmox API token, or `tailscale status
# --json`; what the wizard cannot see, the person confirms. The wizard never creates, changes, starts or stops a VM.
# It writes only into the kit folder (steps-<hv>.txt, a VMware disk descriptor, a VirtualBox copy of the installer)
# and never writes to the signed image.

function Split-HvLine([string]$line) { $fl = $line.Split("`t"); for ($i = 0; $i -lt $fl.Count; $i++) { if ($fl[$i] -eq '-') { $fl[$i] = '' } }; return , $fl }

function Import-HvData {  # install\deep32-wizard-hv.tsv into $script:Hd (lists and maps)
    if ($script:Hd) { return }
    $d = @{ Ids = (New-List); Names = (New-List); Tested = (New-List); Here = (New-List); Local = (New-List); Tools = (New-List); Nets = (New-List)
        Fields = [System.Collections.Generic.List[object]]::new(); Steps = [System.Collections.Generic.List[object]]::new()
        Rules = [System.Collections.Generic.List[object]]::new(); Text = @{}; Fact = @{}; Fix = @{} }
    foreach ($raw in [IO.File]::ReadAllLines((Join-Path $WizHere 'deep32-wizard-hv.tsv'))) {
        $line = $raw.TrimEnd("`r")
        if (-not $line.Trim() -or $line.StartsWith('#')) { continue }
        $x = Split-HvLine $line
        switch ($x[0]) {
            'hv' { $d.Ids.Add($x[1]); $d.Names.Add($x[2]); $d.Tested.Add($x[3]); $d.Here.Add($x[4]); $d.Local.Add($x[5]); $d.Tools.Add($x[6]); $d.Nets.Add($x[7]) }
            'field' { $d.Fields.Add([pscustomobject]@{ Hv = $x[1]; Where = $x[2]; Key = $x[3]; Label = $x[4]; Def = $x[5]; Pat = $x[6]; Err = $x[7]; Help = $x[8] }) }
            'step' { $d.Steps.Add([pscustomobject]@{ Hv = $x[1]; Where = $x[2]; Net = $x[3]; Os = $x[4]; Id = $x[5]; Check = $x[6]; Title = $x[7]; Place = $x[8]; Body = $x[9]; Look = $x[10] }) }
            'rule' { $d.Rules.Add([pscustomobject]@{ Hv = $x[1]; Phase = $x[2]; Fact = $x[3]; Op = $x[4]; Val = $x[5]; Code = $x[6] }) }
            'fact' { $d.Fact[$x[1]] = $x[2] }
            'fix' { $d.Fix[$x[1] + ' ' + $x[2]] = $x[3] }
            'text' { $d.Text[$x[1]] = $x[2] }
        }
    }
    $script:Hd = $d
}

function Get-HvIndex([string]$id) { for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($Hd.Ids[$i] -eq $id) { return $i } }; return -1 }

function Set-VmOs {  # VmOs: windows or unix, for the steps, the paths and the demo
    $script:VmOs = 'windows'
    if ($env:DEEP32_WIZARD_OS -and $env:DEEP32_WIZARD_OS -ne 'windows') { $script:VmOs = 'unix' }
    $script:Sep = '\'; if ($VmOs -eq 'unix') { $script:Sep = '/' }
}

function Get-VmBase([string]$p) { $n = $p.Substring($p.LastIndexOf('/') + 1); return $n.Substring($n.LastIndexOf('\') + 1) }

function Find-Hypervisors {  # HvFound and HvCmd: each hypervisor found on this PC (read-only: commands, install folders, the vmms service)
    $script:HvFound = New-List; $script:HvCmd = New-List
    for ($i = 0; $i -lt $Hd.Ids.Count; $i++) {
        $id = $Hd.Ids[$i]; $found = 'no'; $cmd = ''
        if ($null -ne $env:DEEP32_WIZARD_HV_FOUND) { if ((' ' + $env:DEEP32_WIZARD_HV_FOUND + ' ').Contains(" $id ")) { $found = 'yes' } }
        elseif ($Demo) { if (@('unix:virtualbox', 'unix:kvm', 'windows:hyper-v', 'windows:virtualbox') -contains "$($VmOs):$id") { $found = 'yes' } }
        else {
            foreach ($t in $Hd.Tools[$i].Split([char[]]',', [StringSplitOptions]::RemoveEmptyEntries)) {
                if ($t -eq 'Get-VM') {
                    if ((Get-Command Get-VM -ErrorAction SilentlyContinue) -and ((Get-Service vmms -ErrorAction SilentlyContinue).Status -eq 'Running')) { $found = 'yes'; $cmd = 'Get-VM'; break }
                } elseif ($t.Contains('%')) {
                    $p = [Environment]::ExpandEnvironmentVariables($t)
                    if (-not $p.Contains('%') -and (Test-Path -LiteralPath $p)) { $found = 'yes'; $cmd = $p; break }
                } else {
                    $c = Get-Command $t -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($c) { $found = 'yes'; $cmd = $c.Source; break }
                }
            }
        }
        $HvFound.Add($found); $HvCmd.Add($cmd)
    }
}

function Find-Kit([string]$from = '') {  # the verified disc 1 image and deep32-get's VM folder, from a download or VM folder
    $script:VmKit = @{ Dir = ''; Dl = ''; Image = ''; Sha = ''; Bytes = ''; GetHv = ''; Copy = ''; Disc2 = ''; Disc2Ok = $false }
    if ($Demo) {
        $dl = '/home/you/deep32-downloads'; $hv = 'kvm'; if ($VmOs -eq 'windows') { $dl = 'C:\Users\you\deep32-downloads'; $hv = 'hyper-v' }
        $VmKit.Dl = $dl; $VmKit.GetHv = $hv; $VmKit.Dir = $dl + $Sep + 'deep32-0.1.0-alpha.2-disc1-workstation-amd64-' + $hv
        $VmKit.Image = $dl + $Sep + 'deep32-0.1.0-alpha.2-disc1-workstation-amd64.img'; $VmKit.Bytes = '1876951040'; $VmKit.Sha = $Hd.Text['demo-sha256']
        $VmKit.Copy = $VmKit.Dir + $Sep + 'installer.img'; if ($hv -eq 'hyper-v') { $VmKit.Copy = $VmKit.Dir + $Sep + 'installer.vhd' }
        $VmKit.Disc2 = $dl + $Sep + 'deep32-0.1.0-alpha.2-disc2-content.iso'; $VmKit.Disc2Ok = $true
        return $true
    }
    if (-not $from) { $from = $G.VmLine; if (-not $from) { $from = Join-Path (Split-Path -Parent $WizRepo) 'deep32-downloads' } }
    $best = ''
    if (Test-Path -LiteralPath (Join-Path $from 'recipe.json')) { $best = $from }
    else {
        $first = ''
        foreach ($sfx in '-kvm', '-hyper-v') {
            foreach ($dd in @(Get-ChildItem -LiteralPath $from -Directory -Filter ('*' + $sfx) -ErrorAction SilentlyContinue)) {
                if (-not (Test-Path -LiteralPath (Join-Path $dd.FullName 'recipe.json'))) { continue }
                if (-not $first) { $first = $dd.FullName }
                if ($dd.Name.EndsWith('-' + $G.VmHv)) { $best = $dd.FullName }
            }
        }
        if (-not $best) { $best = $first }
    }
    if (-not $best) { return $false }
    foreach ($l in [IO.File]::ReadAllLines((Join-Path $best 'recipe.json'))) {
        if ($l -cmatch '"installer_sha256": "([0-9a-f]{64})"') { $VmKit.Sha = $Matches[1] }
        if ($l -cmatch '"installer_bytes": ([0-9]{1,15})') { $VmKit.Bytes = $Matches[1] }
        if ($l -cmatch '"hypervisor": "(kvm|hyper-v)"') { $VmKit.GetHv = $Matches[1] }
    }
    $VmKit.Dir = (Get-Item -LiteralPath $best).FullName.TrimEnd('\'); $VmKit.Dl = Split-Path -Parent $VmKit.Dir
    $stem = Split-Path -Leaf $VmKit.Dir; if ($stem.EndsWith('-' + $VmKit.GetHv)) { $stem = $stem.Substring(0, $stem.Length - $VmKit.GetHv.Length - 1) }
    $VmKit.Image = Join-Path $VmKit.Dl ($stem + '.img')
    $VmKit.Copy = Join-Path $VmKit.Dir 'installer.img'; if ($VmKit.GetHv -eq 'hyper-v') { $VmKit.Copy = Join-Path $VmKit.Dir 'installer.vhd' }
    $VmKit.Disc2 = Join-Path $VmKit.Dl ($stem.Replace('disc1-workstation-amd64', 'disc2-content') + '.iso')
    $VmKit.Disc2Ok = Test-Path -LiteralPath $VmKit.Disc2
    return [bool]($VmKit.Sha -and $VmKit.Bytes -and (Test-Path -LiteralPath $VmKit.Image))
}

function Set-VmVars {  # VF: the values the steps' {placeholders} take
    $v = @{}
    $v['image'] = $VmKit.Image; $v['imagename'] = Get-VmBase $VmKit.Image; $v['sha256'] = $VmKit.Sha; $v['bytes'] = $VmKit.Bytes
    if ($VmKit.Bytes -match '^[0-9]{1,15}$') { $v['sectors'] = [string][math]::Floor([long]$VmKit.Bytes / 512); $v['mib'] = [string]([math]::Floor([long]$VmKit.Bytes / 1048576)) }
    $v['kit'] = $VmKit.Dir; $v['dl'] = $VmKit.Dl; $v['copy'] = $VmKit.Copy; $v['copyname'] = Get-VmBase $VmKit.Copy
    $v['disc2'] = $VmKit.Disc2; $v['disc2name'] = Get-VmBase $VmKit.Disc2; $v['gethv'] = $VmKit.GetHv; $v['sep'] = $Sep
    $v['hvname'] = ''; if ($script:Vhi -ge 0) { $v['hvname'] = $Hd.Names[$script:Vhi] }
    $v['net'] = $G.VmNet; $v['netlabel'] = $Hd.Text['net-' + $G.VmHv + '-' + $G.VmNet]; if (-not $v['netlabel']) { $v['netlabel'] = $G.VmNet }
    foreach ($fd in $Hd.Fields) { $v[$fd.Key] = [string]$F['vm_' + $fd.Key] }
    $script:VF = $v
    $script:VF['netlabel'] = Expand-Vm $v['netlabel']
}

function Expand-Vm([string]$t) {  # TEXT with every {name} from VF filled in
    if (-not $t -or -not $t.Contains('{')) { return $t }
    foreach ($k in $VF.Keys) { $t = $t.Replace('{' + $k + '}', [string]$VF[$k]) }
    return $t
}

function Build-VmSteps {  # the steps for this hypervisor, place, network and OS, filled in
    $G.Vs = [System.Collections.Generic.List[object]]::new()
    foreach ($s in $Hd.Steps) {
        if ($s.Hv -ne $G.VmHv) { continue }
        if ($s.Where -and $s.Where -ne '*' -and $s.Where -ne $G.VmWhere) { continue }
        if ($s.Net -and $s.Net -ne '*' -and $s.Net -ne $G.VmNet) { continue }
        if ($s.Os -and $s.Os -ne '*' -and $s.Os -ne $VmOs) { continue }
        $G.Vs.Add([pscustomobject]@{ Id = $s.Id; Check = $s.Check; Title = (Expand-Vm $s.Title); Place = (Expand-Vm $s.Place); Body = (Expand-Vm $s.Body)
            Look = (Expand-Vm $s.Look); State = 'pending'; Res = ''; Code = '' })
    }
    $G.VmI = 0
}

function Get-VmItems([string]$body) { if (-not $body) { return , @() }; return , $body.Split([string[]]@(' | '), [StringSplitOptions]::None) }

function Get-VmPlainItem([string]$prefix, [string]$item) {  # one item as plain lines, each `command` on a line of its own
    $sb = [System.Text.StringBuilder]::new(); $first = $true
    $parts = $item.Split('`')
    for ($p = 0; $p -lt $parts.Count; $p++) {
        if ($p % 2 -eq 0) {
            $prose = $parts[$p].TrimStart(' ', ',', '.', ';', ':').TrimEnd(' ')
            if ($prose -and $prose -notmatch '^[.,:;]$') {
                if ($first) { [void]$sb.Append($prefix + $prose + "`n"); $first = $false } else { [void]$sb.Append('       ' + $prose + "`n") }
            }
        } elseif ($parts[$p]) {
            if ($first) { [void]$sb.Append($prefix.TrimEnd(' ') + "`n"); $first = $false }
            [void]$sb.Append('         ' + $parts[$p] + "`n")
        }
    }
    return $sb.ToString()
}

function Get-VmTestedWord { if ($Hd.Tested[$script:Vhi] -eq 'yes') { return 'boot-tested' }; return 'not boot-tested yet' }

function Get-VmStepsText {  # every step as plain text: the steps file uses it
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append(('Deep32 Workstation VM on {0} ({1})' -f $Hd.Names[$Vhi], (Get-VmTestedWord)) + "`n")
    [void]$sb.Append(('Disc 1 image: {0}' -f $VmKit.Image) + "`n" + ('  sha256 {0}, {1} bytes' -f $VmKit.Sha, $VmKit.Bytes) + "`n")
    [void]$sb.Append("The wizard creates, changes, starts and stops no VM; every command here is yours to run.`n`n")
    for ($i = 0; $i -lt $G.Vs.Count; $i++) {
        $s = $G.Vs[$i]
        [void]$sb.Append(('{0}. {1}' -f ($i + 1), $s.Title) + "`n")
        if ($s.Place) { [void]$sb.Append('   Where: ' + $s.Place + "`n") }
        $k = 0; foreach ($it in (Get-VmItems $s.Body)) { $k++; [void]$sb.Append((Get-VmPlainItem ('   ' + $k + ') ') $it)) }
        if ($s.Look) { [void]$sb.Append('   Look for: ' + $s.Look + "`n") }
        [void]$sb.Append("`n")
    }
    return $sb.ToString()
}

function Write-VmSteps {  # steps-<hv>.txt in the kit folder; the demo writes nothing
    $G.VmStepsFile = $VmKit.Dir + $Sep + 'steps-' + $G.VmHv + '.txt'
    if ($Demo) { return }
    try { [IO.File]::WriteAllText($G.VmStepsFile, (Get-VmStepsText).Replace("`n", "`r`n"), [System.Text.UTF8Encoding]::new($false)); Write-WizLog ('--- vm guide: wrote ' + $G.VmStepsFile) } catch { $G.VmStepsFile = '' }
}

function Get-DemoLines([string]$name) {  # the recorded output vm-NAME-N (N counts the reads; the last one repeats) or vm-NAME
    if (-not $script:DemoLines) { $script:DemoLines = [IO.File]::ReadAllLines((Join-Path $WizHere 'deep32-wizard-demo.txt')) }
    if (-not $script:DemoN) { $script:DemoN = @{} }
    $n = 1; if ($DemoN.ContainsKey($name)) { $n = $DemoN[$name] + 1 }; $DemoN[$name] = $n
    if (-not $script:DemoNames) { $script:DemoNames = @{}; foreach ($l in $DemoLines) { if ($l.StartsWith('=== ')) { $DemoNames[$l.Substring(4).TrimEnd("`r")] = $true } } }
    $want = 'vm-' + $name
    for ($k = $n; $k -ge 1; $k--) { if ($DemoNames.ContainsKey("vm-$name-$k")) { $want = "vm-$name-$k"; break } }
    $out = New-List; $inside = $false; $skip = [System.Collections.Generic.List[int]]::new(); $skip.Add(0)
    foreach ($raw in $DemoLines) {
        $line = $raw.TrimEnd("`r")
        if ($line.StartsWith('=== ')) { if ($inside) { break }; if ($line.Substring(4) -ceq $want) { $inside = $true }; continue }
        if (-not $inside -or $line.StartsWith('#')) { continue }
        if ($line -eq '.if windows') { if ($VmOs -eq 'windows' -and $skip[$skip.Count - 1] -eq 0) { $skip.Add(0) } else { $skip.Add(1) }; continue }
        if ($line -eq '.if unix') { if ($VmOs -eq 'unix' -and $skip[$skip.Count - 1] -eq 0) { $skip.Add(0) } else { $skip.Add(1) }; continue }
        if ($line -eq '.endif') { if ($skip.Count -gt 1) { $skip.RemoveAt($skip.Count - 1) }; continue }
        if ($skip[$skip.Count - 1] -eq 1 -or -not $line) { continue }
        $out.Add($line.Replace('{tab}', "`t"))
    }
    return , $out
}

# What a VM's settings say, as the facts the rules in the data file read (the same names as deep32-wizard.sh).

function Format-VmBytes([long]$b) { return ('{0} ({1} bytes)' -f (Format-Bytes $b), $b) }

function Get-UnitBytes([string]$n, [string]$u) {  # NUMBER (whole or with a fraction) times the binary unit K M G T
    $mult = [long]1; switch ($u) { 'K' { $mult = 1024 } 'M' { $mult = 1048576 } 'G' { $mult = 1073741824 } 'T' { $mult = 1099511627776 } }
    $whole = $n; $frac = ''; $dot = $n.IndexOf('.'); if ($dot -ge 0) { $whole = $n.Substring(0, $dot); $frac = $n.Substring($dot + 1) }
    if ($frac.Length -gt 6) { $frac = $frac.Substring(0, 6) }
    $digits = [long][math]::Pow(10, $frac.Length); $fv = 0; if ($frac) { $fv = [long]$frac }
    return [long]$whole * $mult + [long][math]::Floor($fv * $mult / $digits)
}

function Set-DiskFacts([string]$first, [string[]]$disks) {  # target_bytes, installer and boot_first from DEV:BYTES items
    $big = ''; $bigb = -1; $small = ''; $smallb = -1; $count = 0
    foreach ($d in $disks) {
        $i = $d.LastIndexOf(':'); $dev = $d.Substring(0, $i); $bs = $d.Substring($i + 1)
        if ($bs -notmatch '^[0-9]{1,15}$') { continue }
        $b = [long]$bs; $count++
        if ($b -gt $bigb) { $bigb = $b; $big = $dev }
        if ($smallb -lt 0 -or $b -lt $smallb) { $smallb = $b; $small = $dev }
    }
    if (-not $big) { $Fact['installer'] = 'no'; $FShow['installer'] = 'no disk'; return }
    $Fact['target_bytes'] = [string]$bigb; $FShow['target_bytes'] = ('{0}, {1}' -f $big, (Format-VmBytes $bigb))
    if ($disks.Count -ge 2 -or $smallb -lt 10000000000) { $Fact['installer'] = 'yes'; $FShow['installer'] = ('{0}, {1}' -f $small, (Format-Bytes $smallb)) }
    else { $Fact['installer'] = 'no'; $FShow['installer'] = 'not attached'; $small = '' }
    if ($first) {
        if ($first -ceq $big -and $big -cne $small) { $Fact['boot_first'] = 'target' } elseif ($first -ceq $small) { $Fact['boot_first'] = 'installer' } else { $Fact['boot_first'] = 'other' }
        $FShow['boot_first'] = 'first boot device ' + $first
    }
}

function New-Facts { $script:Fact = @{}; $script:FShow = @{} }

function Read-PveFacts([string[]]$lines) {  # a Proxmox VE VM configuration (qm config, or the API's data as KEY: VALUE lines)
    New-Facts
    $Fact['firmware'] = 'bios'; $FShow['firmware'] = 'bios not set (seabios)'; $Fact['display'] = 'std'; $FShow['display'] = 'vga not set (std)'
    $Fact['ostype'] = 'other'; $FShow['ostype'] = 'ostype not set (other)'; $Fact['scsihw'] = 'lsi'; $FShow['scsihw'] = 'scsihw not set (lsi)'
    $Fact['memory_mb'] = '512'; $FShow['memory_mb'] = 'memory not set (512 MiB)'
    $cds = ''; $boot = ''; $sockets = '1'; $cores = '1'; $first = ''; $disks = @()
    foreach ($raw in $lines) {
        $l = $raw.TrimEnd("`r")
        if ($l -cnotmatch '^([a-z0-9_-]+): ?(.*)$') { continue }
        $k = $Matches[1]; $v = $Matches[2]
        if ($k -ceq 'bios') { $Fact['firmware'] = 'bios'; if ($v -ceq 'ovmf') { $Fact['firmware'] = 'uefi' }; $FShow['firmware'] = 'bios: ' + $v }
        elseif ($k -ceq 'ostype') { $Fact['ostype'] = $v; $FShow['ostype'] = 'ostype: ' + $v }
        elseif ($k -ceq 'cpu') { $t = $v; if ($t.StartsWith('cputype=')) { $t = $t.Substring(8) }; $t = $t.Split(',')[0]; $Fact['cpu'] = $t; $FShow['cpu'] = 'cpu: ' + $t }
        elseif ($k -ceq 'cores') { $cores = $v } elseif ($k -ceq 'sockets') { $sockets = $v }
        elseif ($k -ceq 'memory') { $t = $v; if ($t.StartsWith('current=')) { $t = $t.Substring(8) }; $t = $t.Split(',')[0]; $Fact['memory_mb'] = $t; $FShow['memory_mb'] = "memory: $t MiB" }
        elseif ($k -ceq 'scsihw') { $Fact['scsihw'] = $v; $FShow['scsihw'] = 'scsihw: ' + $v }
        elseif ($k -ceq 'vga') { $t = $v; if ($t.StartsWith('type=')) { $t = $t.Substring(5) }; $t = $t.Split(',')[0]; $FShow['display'] = 'vga: ' + $t; if ($t -ceq 'virtio' -or $t -ceq 'virtio-gl') { $Fact['display'] = 'virtio-gpu' } else { $Fact['display'] = $t } }
        elseif ($k -ceq 'net0') {
            $t = $v.Split(',')[0]; if ($t.StartsWith('model=')) { $t = $t.Substring(6) }; $t = $t.Split('=')[0]; $Fact['nic'] = $t; $FShow['nic'] = 'net0: ' + $t
            if ((',' + $v + ',') -cmatch ',bridge=([^,]+),') { $FShow['nic'] += ' on ' + $Matches[1] }
        }
        elseif ($k -ceq 'boot') { if ($v -cmatch '(^|,)order=([^,]+)') { $boot = $Matches[2]; $first = $boot.Split(';')[0] } }
        elseif ($k -cmatch '^(ide[0-9]|sata[0-9]|scsi[0-9]|scsi[0-9][0-9]|virtio[0-9]|virtio[0-9][0-9])$') {
            $file = $v.Split(',')[0]
            if ((',' + $v + ',').Contains(',media=cdrom,')) { if ($file -cne 'none') { $fv = $file.Substring($file.IndexOf(':') + 1); $cds += ' ' + (Get-VmBase $fv) }; continue }
            if ((',' + $v + ',') -cmatch ',size=([0-9]+(\.[0-9]+)?)([KMGT]?),') { $disks += ($k + ':' + (Get-UnitBytes $Matches[1] $Matches[3])) }
        }
    }
    if ($cores -match '^[0-9]{1,15}$' -and $sockets -match '^[0-9]{1,15}$') { $Fact['cores'] = [string]([long]$cores * [long]$sockets); $FShow['cores'] = "cores: $cores, sockets: $sockets" }
    Set-DiskFacts $first $disks
    if ($boot) { $FShow['boot_first'] = 'boot: order=' + $boot }
    $cds = $cds.Trim(); $Fact['cdrom'] = $cds; $FShow['cdrom'] = $cds; if (-not $cds) { $FShow['cdrom'] = 'no CD drive with an image' }
}

function Read-VboxFacts([string[]]$lines, [hashtable]$cap) {  # VBoxManage showvminfo --machinereadable (disk sizes in $cap, by path)
    New-Facts
    $ctl = @{}; $disks = @(); $cds = ''; $first = ''; $firstKey = ''
    foreach ($raw in $lines) { if ($raw.TrimEnd("`r") -cmatch '^storagecontrollername([0-9]+)="(.*)"$') { $ctl[$Matches[2]] = [int]$Matches[1] } }
    foreach ($raw in $lines) {
        $l = $raw.TrimEnd("`r")
        if ($l -cmatch '^"(.+)-([0-9]+)-([0-9]+)"="(.*)"$') {
            $name = $Matches[1]; $port = [int]$Matches[2]; $dev = [int]$Matches[3]; $path = $Matches[4]
            if ($name.EndsWith('-ImageUUID') -or -not $ctl.ContainsKey($name)) { continue }
            if ($path -ceq 'none' -or $path -ceq 'emptydrive' -or -not $path) { continue }
            $b = Get-VmBase $path
            if ($b.ToLowerInvariant().EndsWith('.iso')) { $cds += ' ' + $b; continue }
            $key = '{0:000}{1:000}{2:000}' -f $ctl[$name], $port, $dev
            $size = 'x'; if ($cap.ContainsKey($path)) { $size = [string]$cap[$path] }
            $disks += ("$name-$port-$($dev):" + $size)
            if (-not $firstKey -or [string]::CompareOrdinal($key, $firstKey) -lt 0) { $firstKey = $key; $first = "$name-$port-$dev" }
            continue
        }
        if ($l -cnotmatch '^([A-Za-z0-9_-]+)="?([^"]*)"?$') { continue }
        $k = $Matches[1]; $v = $Matches[2]
        switch -CaseSensitive ($k) {
            'firmware' { $Fact['firmware'] = 'bios'; if ($v.StartsWith('EFI')) { $Fact['firmware'] = 'uefi' }; $FShow['firmware'] = 'firmware=' + $v }
            'chipset' { $Fact['chipset'] = $v; $FShow['chipset'] = 'chipset=' + $v }
            'memory' { $Fact['memory_mb'] = $v; $FShow['memory_mb'] = 'memory=' + $v }
            'cpus' { $Fact['cores'] = $v; $FShow['cores'] = 'cpus=' + $v }
            'nictype1' { $Fact['nic'] = $v; $FShow['nic'] = 'nictype1=' + $v }
            'graphicscontroller' { $Fact['display'] = $v; $FShow['display'] = 'graphicscontroller=' + $v }
            'nic1' {
                $FShow['netmode'] = 'nic1=' + $v
                if ($v -ceq 'bridged') { $Fact['netmode'] = 'bridged' } elseif ($v -ceq 'nat' -or $v -ceq 'natnetwork') { $Fact['netmode'] = 'nat' }
                elseif ($v -ceq 'hostonly' -or $v -ceq 'hostonlynetwork' -or $v -ceq 'intnet') { $Fact['netmode'] = 'hostonly' } else { $Fact['netmode'] = 'none' }
            }
        }
    }
    Set-DiskFacts $first $disks
    $cds = $cds.Trim(); $Fact['cdrom'] = $cds; $FShow['cdrom'] = $cds; if (-not $cds) { $FShow['cdrom'] = 'no DVD with an image' }
}

function Read-VmxFacts([string[]]$lines) {  # a VMware .vmx file (only keys Broadcom documents)
    New-Facts
    $file = @{}; $type = @{}; $present = @{}; $order = New-List; $cds = ''; $inst = 'no'; $tgt = ''
    foreach ($raw in $lines) {
        $l = $raw.TrimEnd("`r")
        if ($l -cnotmatch '^([A-Za-z0-9_.:]+) *= *"(.*)"$') { continue }
        $k = $Matches[1].ToLowerInvariant(); $v = $Matches[2]
        if ($k -eq 'memsize') { $Fact['memory_mb'] = $v; $FShow['memory_mb'] = 'memsize = ' + $v }
        elseif ($k -eq 'numvcpus') { $Fact['cores'] = $v; $FShow['cores'] = 'numvcpus = ' + $v }
        elseif ($k -eq 'ethernet0.virtualdev') { $Fact['nic'] = $v; $FShow['nic'] = 'ethernet0.virtualDev = ' + $v }
        elseif ($k.EndsWith('.filename')) { $dv = $k.Substring(0, $k.Length - 9); if (-not $file.ContainsKey($dv)) { $order.Add($dv) }; $file[$dv] = $v }
        elseif ($k.EndsWith('.devicetype')) { $type[$k.Substring(0, $k.Length - 11)] = $v }
        elseif ($k.EndsWith('.present')) { $present[$k.Substring(0, $k.Length - 8)] = $v.ToUpperInvariant() }
    }
    foreach ($dv in $order) {
        if ($present[$dv] -eq 'FALSE') { continue }
        $b = Get-VmBase $file[$dv]
        if ($type[$dv] -ceq 'cdrom-image') { $cds += ' ' + $b; continue }
        if (-not $b.ToLowerInvariant().EndsWith('.vmdk')) { continue }
        if ($b -ceq 'installer.vmdk' -or $b -ceq 'installer-esxi.vmdk') { $inst = 'yes' } else { $tgt = $b }
    }
    $Fact['installer'] = $inst; if ($inst -eq 'yes') { $FShow['installer'] = 'installer.vmdk attached' } else { $FShow['installer'] = 'not attached' }
    if ($inst -eq 'yes') { $Fact['boot_first'] = 'installer'; $FShow['boot_first'] = 'the installer disk is attached' }
    elseif ($tgt) { $Fact['boot_first'] = 'target'; $FShow['boot_first'] = $tgt }
    $cds = $cds.Trim(); $Fact['cdrom'] = $cds; $FShow['cdrom'] = $cds; if (-not $cds) { $FShow['cdrom'] = 'no CD/DVD with an ISO image' }
}

function Read-HyperVFacts([string[]]$lines) {  # Hyper-V's VM as KEY: VALUE lines (Get-HyperVLines writes them from Get-VM and friends)
    New-Facts
    $disks = @(); $cds = ''; $first = ''; $firstKey = ''; $sw = @{}; $adapter = ''; $legacy = ''
    foreach ($raw in $lines) {
        $l = $raw.TrimEnd("`r")
        if ($l -cnotmatch '^([a-z_]+): (.*)$') { continue }
        $k = $Matches[1]; $v = $Matches[2]
        switch -CaseSensitive ($k) {
            'generation' { $Fact['generation'] = $v; $FShow['generation'] = 'Generation ' + $v }
            'processors' { $Fact['cores'] = $v; $FShow['cores'] = 'ProcessorCount ' + $v }
            'memory_mb' { $Fact['memory_mb'] = $v; $FShow['memory_mb'] = "MemoryStartup $v MiB" }
            'disk' {
                if ($v -cmatch '^(IDE|SCSI) ([0-9]+) ([0-9]+) (.*) ([0-9]{1,15})$') {
                    $dev = $Matches[1] + ' ' + $Matches[2] + ':' + $Matches[3]
                    $key = '{0}{1:000}{2:000}' -f $Matches[1], [int]$Matches[2], [int]$Matches[3]
                    $disks += ($dev + ':' + $Matches[5])
                    if (-not $firstKey -or [string]::CompareOrdinal($key, $firstKey) -lt 0) { $firstKey = $key; $first = $dev }
                }
            }
            'dvd' { if ($v -cmatch '^(IDE|SCSI) [0-9]+ [0-9]+ (.+)$') { $cds += ' ' + (Get-VmBase $Matches[2]) } }
            'adapter' { if ($v -cmatch '^(.*) (true|false)$') { $adapter = $Matches[1]; $legacy = $Matches[2] } }
            'switch' { if ($v -cmatch '^(.*) (External|Internal|Private)( nat)?$') { $sw[$Matches[1]] = $Matches[2] + $Matches[3] } }
        }
    }
    if ($adapter -or $legacy) {
        $Fact['nic'] = 'synthetic'; if ($legacy -eq 'true') { $Fact['nic'] = 'legacy' }; $FShow['nic'] = 'IsLegacy ' + $legacy
        $t = $sw[$adapter]; $FShow['netmode'] = "switch $adapter ($t)"
        if ($t -ceq 'External') { $Fact['netmode'] = 'bridged' } elseif ($t -ceq 'Internal nat') { $Fact['netmode'] = 'nat' } elseif ($t) { $Fact['netmode'] = 'hostonly' } else { $Fact['netmode'] = 'none'; $FShow['netmode'] = 'not connected to a switch' }
    }
    Set-DiskFacts $first $disks
    $cds = $cds.Trim(); $Fact['cdrom'] = $cds; $FShow['cdrom'] = $cds; if (-not $cds) { $FShow['cdrom'] = 'no DVD drive with an image' }
}

function Invoke-VmRules([string]$phase) {  # this hypervisor's rules for PHASE against the facts: RuleOut lines and RuleCode
    $script:RuleOut = New-List; $script:RuleCode = ''
    $own = @{}; foreach ($r in $Hd.Rules) { if ($r.Hv -ceq $G.VmHv -and $r.Phase -ceq $phase) { $own[$r.Fact] = $true } }
    foreach ($r in $Hd.Rules) {
        if ($r.Phase -cne $phase) { continue }
        if ($r.Hv -ceq '*') { if ($own.ContainsKey($r.Fact)) { continue } } elseif ($r.Hv -cne $G.VmHv) { continue }
        if (-not $Fact.ContainsKey($r.Fact)) { continue }
        $have = [string]$Fact[$r.Fact]; $val = Expand-Vm $r.Val; $ok = $false
        switch -CaseSensitive ($r.Op) {
            'eq' { $ok = $have -ceq $val } 'ne' { $ok = $have -cne $val }
            'ge' { $ok = ($have -match '^[0-9]{1,15}$' -and $val -match '^[0-9]{1,15}$' -and [long]$have -ge [long]$val) }
            'in' { $ok = (',' + $val + ',').Contains(',' + $have + ',') }
            'has' { $ok = (' ' + $have + ' ').Contains(' ' + $val + ' ') }
        }
        $label = $Hd.Fact[$r.Fact]; if (-not $label) { $label = $r.Fact }
        $show = $FShow[$r.Fact]; if (-not $show) { $show = $have }
        if ($ok) { $RuleOut.Add("ok`t$label`t$show`t") } else { $RuleOut.Add("err`t$label`t$show`t" + $r.Code); if (-not $RuleCode) { $script:RuleCode = $r.Code } }
    }
}

# The checks. Each sets the current step's State: ok, error (Code names the error table row), waiting (the tailnet
# join), job (the VirtualBox copy), or ask-confirm, ask-pin, ask-secret when the screen must ask first.

function Get-VmStep { return $G.Vs[$G.VmI] }
function Add-VmRes([string]$line) { $s = Get-VmStep; $s.Res += $line + "`n" }

function Complete-VmRules {  # the rule results for the current step: ok, or error with the first code
    $s = Get-VmStep; $s.Res = ''
    foreach ($r in $RuleOut) { Add-VmRes $r }
    if ($RuleCode) { $s.State = 'error'; $s.Code = $RuleCode } else { $s.State = 'ok'; $s.Code = '' }
    if ($RuleOut.Count -eq 0) { $s.State = 'ask-confirm'; $s.Res = "info`t" + $Hd.Text['ui-nofacts'] + "`t`t`n" }
}

function Set-VmFail([string]$code, [string]$label = '', [string]$shown = '') {
    $s = Get-VmStep; $s.State = 'error'; $s.Code = $code
    if ($label) { Add-VmRes ("err`t$label`t$shown`t$code") }
}

function Invoke-VmCheck {  # check the current step
    $s = Get-VmStep; $s.Res = ''; $s.Code = ''
    Write-WizLog ('--- vm guide: check step {0} {1} ({2})' -f ($G.VmI + 1), $s.Id, $s.Check)
    $c = $s.Check
    if ($c -eq 'confirm') { $s.State = 'ask-confirm' }
    elseif ($c -eq 'image') { Test-KitImage }
    elseif ($c -eq 'copy') { Test-KitCopy }
    elseif ($c -eq 'vmdk') { Write-KitVmdk }
    elseif ($c -eq 'vdi') { Start-KitVdi }
    elseif ($c.StartsWith('vm:')) { Test-VmSettings ($c.Substring(3).Split('+')[0]) }
    elseif ($c -eq 'join') { Start-TsJoin }
    else { $s.State = 'ask-confirm' }
}

function Get-FileLength([string]$p) { if ($Demo) { return $null }; $it = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue; if ($it -and -not $it.PSIsContainer) { return [long]$it.Length }; return $null }

function Test-KitImage {  # the verified disc 1 image is where the kit says, with the size the signed release gives
    $size = $null; if ($Demo) { $size = [long]$VmKit.Bytes } else { $size = Get-FileLength $VmKit.Image }
    if ($null -eq $size) { Set-VmFail 'KIT_MISSING' $Hd.Fact['image'] $VmKit.Image; return }
    if ([string]$size -ne $VmKit.Bytes) { Set-VmFail 'KIT_MISSING' $Hd.Fact['image'] ("$size bytes, the release says " + $VmKit.Bytes); return }
    Add-VmRes ("ok`t" + $Hd.Fact['image'] + "`t" + $VmKit.Image + ", $size bytes`t")
    Add-VmRes ("ok`t" + $Hd.Fact['sha256'] + "`t" + $VmKit.Sha + " (checked by deep32-get)`t")
    (Get-VmStep).State = 'ok'
}

function Test-KitCopy {  # deep32-get's recipe for this hypervisor: the installer copy, recipe.json and commands.txt
    if ($VmKit.GetHv -ne $G.VmHv) { Set-VmFail 'KIT_MISSING' $Hd.Fact['copy'] ($VmKit.Dir + ' holds the ' + $VmKit.GetHv + ' files'); return }
    $want = [long]$VmKit.Bytes; if ($VmKit.GetHv -eq 'hyper-v') { $want += 512 }
    $size = $want; if (-not $Demo) { $size = Get-FileLength $VmKit.Copy }
    if ($null -eq $size -or $size -ne $want) { Set-VmFail 'KIT_MISSING' $Hd.Fact['copy'] ($VmKit.Copy + ' ' + $size); return }
    foreach ($n in 'recipe.json', 'commands.txt') { if (-not $Demo -and -not (Test-Path -LiteralPath ($VmKit.Dir + $Sep + $n))) { Set-VmFail 'KIT_MISSING' $Hd.Fact['copy'] ($VmKit.Dir + $Sep + $n); return } }
    Add-VmRes ("ok`t" + $Hd.Fact['copy'] + "`t" + $VmKit.Copy + ", $size bytes`t")
    (Get-VmStep).State = 'ok'
}

function Write-KitVmdk {  # the VMware descriptor next to deep32-get's installer copy (a wrapper: the copy is not changed)
    $d = $VmKit.Dir + $Sep + 'installer.vmdk'
    $size = [long]$VmKit.Bytes; if (-not $Demo) { $size = Get-FileLength $VmKit.Copy }
    if ($null -eq $size -or $VmKit.Bytes -notmatch '^[0-9]{1,15}$' -or $size -lt [long]$VmKit.Bytes) { Set-VmFail 'KIT_MISSING' $Hd.Fact['copy'] $VmKit.Copy; return }
    $sectors = [long][math]::Floor([long]$VmKit.Bytes / 512); $cyl = [long][math]::Floor($sectors / (16 * 63)); if ($cyl -gt 16383) { $cyl = 16383 }
    $text = (Expand-Vm $Hd.Text['vmdk-text']).Replace('{cyl}', [string]$cyl)
    if (-not $Demo) {
        try { [IO.File]::WriteAllText($d, $text.Replace('\n', "`n") + "`n", [System.Text.ASCIIEncoding]::new()) } catch { Set-VmFail 'KIT_COPY_FAILED' $Hd.Fact['vmdk'] $d; return }
        $back = [IO.File]::ReadAllText($d)
        if (-not $back.Contains('RW ' + $sectors + ' FLAT "' + $VF['copyname'] + '" 0')) { Set-VmFail 'KIT_COPY_FAILED' $Hd.Fact['vmdk'] $d; return }
        Write-WizLog ('--- vm guide: wrote {0} (RW {1} FLAT {2})' -f $d, $sectors, $VF['copyname'])
    }
    Add-VmRes ("ok`t" + $Hd.Fact['vmdk'] + "`t$d`t")
    Add-VmRes ("ok`t" + $Hd.Fact['copy'] + "`t" + $VF['copyname'] + ', ' + $VmKit.Bytes + " bytes from offset 0, $sectors sectors`t")
    (Get-VmStep).State = 'ok'
}

function Start-KitVdi {  # VBoxManage convertfromraw: a converted copy of the verified image in the kit folder, run in the background
    $script:VdiOut = $VmKit.Dir + $Sep + 'installer.vdi'
    $s = Get-VmStep
    if ($Demo) { $s.State = 'job'; $script:JobProc = 'demo'; $script:JobUntil = (Get-NowMs) + 2000; return }
    $vb = $HvCmd[(Get-HvIndex 'virtualbox')]
    if ($G.VmWhere -ne 'here' -or -not $vb) { $s.State = 'ask-confirm'; Add-VmRes ("info`t" + $Hd.Text['ui-vdi-elsewhere'] + "`t`t"); return }
    if (Test-Path -LiteralPath $VdiOut) { Complete-KitVdi 0; return }
    $script:JobLog = Join-Path $StateDir 'convertfromraw.log'
    $argv = @('convertfromraw', $VmKit.Image, $VdiOut, '--format=VDI', '--variant=Fixed')
    Write-WizLog ('--- vm guide: ' + $vb + ' ' + ($argv -join ' '))
    $psi = [System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName = $vb; $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.CreateNoWindow = $true
    $script:JobProc = [System.Diagnostics.Process]::Start($psi); $script:JobOut = $JobProc.StandardOutput.ReadToEndAsync(); $script:JobErr = $JobProc.StandardError.ReadToEndAsync()
    $s.State = 'job'
}

function Update-KitVdi {  # the background copy finished: check it
    $s = Get-VmStep; if ($s.State -ne 'job') { return }
    if ($JobProc -eq 'demo') { if ((Get-NowMs) -ge $JobUntil) { $script:JobProc = $null; Complete-KitVdi 0 }; return }
    if (-not $JobProc.HasExited) { return }
    $JobProc.WaitForExit(); $code = $JobProc.ExitCode
    foreach ($l in ($JobOut.Result + $JobErr.Result).Split("`n")) { if ($l.Trim()) { Write-WizLog $l.TrimEnd("`r") } }
    $JobProc.Dispose(); $script:JobProc = $null
    Complete-KitVdi $code
}

function Complete-KitVdi([int]$code) {  # a fixed-size VDI holds the whole image, so it is at least as large as the image
    $s = Get-VmStep; $s.Res = ''
    $size = $null; if ($Demo) { $size = [long]$VmKit.Bytes + 2097152 } else { $size = Get-FileLength $VdiOut }
    if ($code -ne 0 -or $null -eq $size -or $size -lt [long]$VmKit.Bytes) { $shown = $VdiOut; if ($null -ne $size) { $shown += " ($size bytes)" }; Set-VmFail 'KIT_COPY_FAILED' $Hd.Fact['vdi'] $shown; $G.Dirty = $true; return }
    Add-VmRes ("ok`t" + $Hd.Fact['vdi'] + "`t$VdiOut, $size bytes (the image is " + $VmKit.Bytes + ")`t")
    $s.State = 'ok'; $G.Dirty = $true
}

function Test-VmLocal {  # this hypervisor runs here and the wizard can read its VM settings from this shell
    if ($G.VmWhere -ne 'here') { return $false }
    $i = Get-HvIndex $G.VmHv
    switch ($Hd.Local[$i]) { 'vbox' { return $HvFound[$i] -eq 'yes' } 'hyperv' { return $HvFound[$i] -eq 'yes' } 'vmx' { return [bool]$F['vm_vmx'] } }
    return $false
}

function Invoke-VmTool([string]$key, [string[]]$argv) {  # one read-only query of this hypervisor's tool (the demo plays KEY): $script:ToolOut, $true on success
    $script:ToolOut = New-List
    if ($Demo) { $script:ToolOut = Get-DemoLines $key; Write-WizLog ('--- vm guide (demo): ' + ($argv -join ' ')); return $true }
    $cmd = $HvCmd[(Get-HvIndex $G.VmHv)]; if (-not $cmd) { return $false }
    Write-WizLog ('--- vm guide: ' + $cmd + ' ' + ($argv -join ' '))
    $psi = [System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName = $cmd; $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.CreateNoWindow = $true
    try { $p = [System.Diagnostics.Process]::Start($psi) } catch { return $false }
    $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync(); $p.WaitForExit()
    foreach ($l in ($o.Result + $e.Result).Split("`n")) { $t = $l.TrimEnd("`r"); if ($t) { $ToolOut.Add($t); Write-WizLog $t } }
    return ($p.ExitCode -eq 0)
}

function Read-VboxVm {  # the VM's settings from VBoxManage (read-only queries)
    if (-not (Invoke-VmTool 'vbox-info' @('showvminfo', $F['vm_vm'], '--machinereadable'))) { $first = $F['vm_vm']; if ($ToolOut.Count) { $first = $ToolOut[0] }; Set-VmFail 'VM_NOT_FOUND' $Hd.Fact['vm'] $first; return $false }
    $info = $ToolOut.ToArray(); $cap = @{}
    foreach ($raw in $info) {   # each attached disk's capacity (attached media are already registered with VirtualBox)
        if ($raw.TrimEnd("`r") -cnotmatch '^"(.+)-[0-9]+-[0-9]+"="(.*)"$') { continue }
        $path = $Matches[2]; if ($Matches[1].EndsWith('-ImageUUID')) { continue }
        $low = $path.ToLowerInvariant(); if ($low -eq 'none' -or $low -eq 'emptydrive' -or $low.EndsWith('.iso')) { continue }
        $b = Get-VmBase $path; $stem = $b; if ($b.LastIndexOf('.') -gt 0) { $stem = $b.Substring(0, $b.LastIndexOf('.')) }
        if (-not (Invoke-VmTool ('vbox-medium-' + $stem) @('showmediuminfo', 'disk', $path))) { continue }
        foreach ($m in $ToolOut) { if ($m -cmatch '^Capacity: +([0-9]+) MBytes') { $cap[$path] = [long]$Matches[1] * 1048576 } }
    }
    Read-VboxFacts $info $cap
    return $true
}

function Read-VmxVm {  # the .vmx the person named (read, never written)
    if ($Demo) { Read-VmxFacts (Get-DemoLines 'vmx').ToArray(); return $true }
    if (-not (Test-Path -LiteralPath $F['vm_vmx'])) { Set-VmFail 'VM_NOT_FOUND' $Hd.Fact['vm'] $F['vm_vmx']; return $false }
    Write-WizLog ('--- vm guide: read ' + $F['vm_vmx'])
    Read-VmxFacts ([IO.File]::ReadAllLines($F['vm_vmx']))
    return $true
}

function Get-HyperVLines([string]$name) {  # Hyper-V's VM as the lines Read-HyperVFacts reads (Get-VM and friends, all read-only)
    $out = New-List
    $vm = Get-VM -Name $name -ErrorAction Stop
    $out.Add('generation: ' + $vm.Generation); $out.Add('processors: ' + $vm.ProcessorCount); $out.Add('memory_mb: ' + [long]($vm.MemoryStartup / 1048576))
    foreach ($d in @(Get-VMHardDiskDrive -VMName $name)) {
        $sz = 0; try { $sz = (Get-VHD -Path $d.Path -ErrorAction Stop).Size } catch { }
        $out.Add(('disk: {0} {1} {2} {3} {4}' -f $d.ControllerType, $d.ControllerNumber, $d.ControllerLocation, $d.Path, $sz))
    }
    foreach ($d in @(Get-VMDvdDrive -VMName $name)) { if ($d.Path) { $out.Add(('dvd: {0} {1} {2} {3}' -f $d.ControllerType, $d.ControllerNumber, $d.ControllerLocation, $d.Path)) } }
    $nat = @(Get-NetNat -ErrorAction SilentlyContinue).Count -gt 0
    foreach ($a in @(Get-VMNetworkAdapter -VMName $name)) { $out.Add(('adapter: {0} {1}' -f $a.SwitchName, ([string]$a.IsLegacy).ToLowerInvariant())); break }
    foreach ($sw in @(Get-VMSwitch)) { $t = [string]$sw.SwitchType; if ($t -eq 'Internal' -and $nat) { $t += ' nat' }; $out.Add(('switch: {0} {1}' -f $sw.Name, $t)) }
    return , $out
}

function Read-HyperVVm {
    $lines = $null
    if ($Demo) { $lines = Get-DemoLines 'hyperv'; Write-WizLog ('--- vm guide (demo): Get-VM ' + $F['vm_vm']) }
    else {
        Write-WizLog ('--- vm guide: Get-VM, Get-VMHardDiskDrive, Get-VHD, Get-VMDvdDrive, Get-VMNetworkAdapter, Get-VMSwitch ' + $F['vm_vm'])
        try { $lines = Get-HyperVLines $F['vm_vm'] } catch { Set-VmFail 'VM_NOT_FOUND' $Hd.Fact['vm'] ($_.Exception.Message.Split("`n")[0]); return $false }
        foreach ($l in $lines) { Write-WizLog $l }
    }
    Read-HyperVFacts $lines.ToArray()
    return $true
}

function Test-VmSettings([string]$phase) {  # read the VM's settings (here, or through the Proxmox API) and apply the rules
    $s = Get-VmStep
    if (Test-VmLocal) {
        $ok = $false
        switch ($Hd.Local[(Get-HvIndex $G.VmHv)]) { 'vbox' { $ok = Read-VboxVm } 'vmx' { $ok = Read-VmxVm } 'hyperv' { $ok = Read-HyperVVm } }
        if (-not $ok) { return }
        Invoke-VmRules $phase; Complete-VmRules; return
    }
    if ($G.VmHv -eq 'proxmox' -and $F['vm_token']) {
        if (-not $G.PvePin) { $s.State = 'ask-pin'; Get-PveFingerprint; return }
        if (-not $script:PveSecret) { $s.State = 'ask-secret'; return }
        Get-PveConfig $phase; return
    }
    $s.State = 'ask-confirm'
}

function Invoke-PveApi([string]$mode, [string]$pathq = '', [string]$secret = '') {  # fp or get over TLS to https://host:8006, pinned
    # The callback only records the certificate; nothing is sent until its SHA-256 matches the fingerprint the person
    # compared. The secret goes only into the Authorization header of that one request and is never logged.
    $res = @{ Fp = ''; Status = 0; Reason = ''; Body = ''; Error = '' }
    $tcp = $null; $ssl = $null
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $ar = $tcp.BeginConnect($F['vm_host'], 8006, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(10000)) { $res.Error = 'no answer in 10 s'; return $res }
        $tcp.EndConnect($ar)
        $script:PveCert = $null
        $cb = [System.Net.Security.RemoteCertificateValidationCallback] { param($sender, $cert, $chain, $errors) $script:PveCert = $cert.GetRawCertData(); return $true }
        $ssl = [System.Net.Security.SslStream]::new($tcp.GetStream(), $false, $cb)
        $ssl.ReadTimeout = 10000; $ssl.WriteTimeout = 10000
        $ssl.AuthenticateAsClient($F['vm_host'])
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($PveCert)
        $res.Fp = (($hash | ForEach-Object { $_.ToString('X2') }) -join ':')
        if ($mode -eq 'fp') { return $res }
        if ($res.Fp -cne $G.PvePin) { return $res }
        $req = 'GET ' + $pathq + " HTTP/1.0`r`nHost: " + $F['vm_host'] + ":8006`r`nAuthorization: PVEAPIToken=" + $F['vm_token'] + '=' + $secret + "`r`nConnection: close`r`n`r`n"
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($req); $ssl.Write($bytes, 0, $bytes.Length); $ssl.Flush()
        $ms = [System.IO.MemoryStream]::new(); $buf = [byte[]]::new(16384)
        while (($n = $ssl.Read($buf, 0, $buf.Length)) -gt 0) { $ms.Write($buf, 0, $n) }
        $text = [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
        $cut = $text.IndexOf("`r`n`r`n"); $head = $text; if ($cut -ge 0) { $head = $text.Substring(0, $cut); $res.Body = $text.Substring($cut + 4) }
        if ($head -cmatch '^HTTP/1\.[01] ([0-9]{3}) ?([^\r\n]*)') { $res.Status = [int]$Matches[1]; $res.Reason = $Matches[2] }
    } catch { $res.Error = $_.Exception.Message.Split("`n")[0].Trim() }
    finally { if ($ssl) { $ssl.Dispose() }; if ($tcp) { $tcp.Close() } }
    return $res
}

function Get-PveFingerprint {  # the Proxmox node's certificate fingerprint, for the person to compare
    $G.PveSeen = ''
    if ($Demo) { $G.PveSeen = $Hd.Text['demo-pve-fp']; return }
    Write-WizLog ('--- vm guide: TLS certificate of ' + $F['vm_host'] + ':8006')
    $r = Invoke-PveApi 'fp'
    if ($r.Error -or -not $r.Fp) { $e = $r.Error; if (-not $e) { $e = 'no certificate' }; Set-VmFail 'PVE_UNREACHABLE' $Hd.Fact['api'] ($F['vm_host'] + ':8006: ' + $e); return }
    $G.PveSeen = $r.Fp
}

function Get-PveConfig([string]$phase) {  # GET the VM's configuration with the token, over the pinned certificate
    $pathq = '/api2/json/nodes/' + $F['vm_node'] + '/qemu/' + $F['vm_vmid'] + '/config'
    Write-WizLog ('--- vm guide: GET https://' + $F['vm_host'] + ':8006' + $pathq + ' with token ' + $F['vm_token'] + ' (the secret is not logged)')
    $cfg = New-List; $status = 0; $reason = ''
    if ($Demo) { $cfg = Get-DemoLines 'pve-config'; $status = 200 }
    else {
        $r = Invoke-PveApi 'get' $pathq $script:PveSecret
        if ($r.Error) { Set-VmFail 'PVE_UNREACHABLE' $Hd.Fact['api'] ($F['vm_host'] + ':8006: ' + $r.Error); return }
        if ($r.Fp -cne $G.PvePin) { $G.PvePin = ''; Set-VmFail 'PVE_TLS_MISMATCH' $Hd.Fact['api'] $r.Fp; return }
        $status = $r.Status; $reason = ($r.Reason + ' ' + $r.Body).Replace("`n", ' ').Replace("`t", ' '); if ($reason.Length -gt 300) { $reason = $reason.Substring(0, 300) }
        if ($status -eq 200) {
            try { $o = $r.Body | ConvertFrom-Json } catch { $o = $null }
            if ($o -and $o.data) { foreach ($p in ($o.data.PSObject.Properties | Sort-Object Name)) { $cfg.Add($p.Name + ': ' + [string]$p.Value) } }
        }
    }
    Write-WizLog ('--- vm guide: API status ' + $status + ' ' + $reason)
    if ($status -eq 200) { Read-PveFacts $cfg.ToArray(); Invoke-VmRules $phase; Complete-VmRules }
    elseif ($status -eq 401 -or $status -eq 403) { $script:PveSecret = ''; Set-VmFail 'PVE_TOKEN_DENIED' $Hd.Fact['api'] ("HTTP $status $reason") }
    elseif ($reason.Contains('does not exist')) { Set-VmFail 'VM_NOT_FOUND' $Hd.Fact['vm'] $reason }
    else { Set-VmFail 'PVE_UNREACHABLE' $Hd.Fact['api'] ("HTTP $status $reason") }
}

function Read-TsState([string]$demoName = 'ts') {  # this PC's Tailscale from `tailscale status --json`
    $script:Ts = @{ State = ''; Tailnet = ''; Suffix = ''; Certs = ''; Peers = [System.Collections.Generic.List[object]]::new() }
    $raw = ''
    if ($Demo) { $raw = (Get-DemoLines $demoName) -join "`n" }
    else {
        if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) { $Ts.State = 'missing'; return }
        $raw = (& tailscale status --json 2>$null) -join "`n"
    }
    $o = $null; try { $o = $raw | ConvertFrom-Json } catch { }
    if (-not $o) { $Ts.State = 'missing'; return }
    $Ts.State = [string]$o.BackendState; if (-not $Ts.State) { $Ts.State = 'missing' }
    if ($o.CurrentTailnet) { $Ts.Tailnet = [string]$o.CurrentTailnet.Name }
    $Ts.Suffix = [string]$o.MagicDNSSuffix
    if ($o.CertDomains) { $Ts.Certs = [string]@($o.CertDomains)[0] }
    if ($o.Peer) { foreach ($p in $o.Peer.PSObject.Properties) { $Ts.Peers.Add([pscustomobject]@{ Key = $p.Name; Host = [string]$p.Value.HostName; Online = ([string]$p.Value.Online).ToLowerInvariant() }) } }
}

function Save-TsSnapshot { Read-TsState 'ts'; $G.TsSnap = @{}; foreach ($p in $Ts.Peers) { $G.TsSnap[$p.Key] = $true } }

function Start-TsJoin {  # wait for a new online machine named like the Workstation (only when this PC is on the tailnet)
    $s = Get-VmStep
    Read-TsState 'ts'
    if ($Ts.State -ne 'Running') { $s.State = 'ask-confirm'; $s.Code = 'TS_NOT_HERE'; Add-VmRes ("info`t" + $Hd.Fact['tailscale'] + "`t" + $Ts.State + ': ' + $Hd.Text['ui-ts-not-here'] + "`tTS_NOT_HERE"); return }
    $s.State = 'waiting'; $G.TsPolls = 0; $G.TsPollAt = 0; Update-TsJoin
}

function Update-TsJoin {  # one look at the tailnet while the step waits
    $s = Get-VmStep; if ($s.State -ne 'waiting') { return }
    $now = Get-NowMs; if ($now - $G.TsPollAt -lt $G.TsEvery) { return }
    $G.TsPollAt = $now; $G.TsPolls++; $G.Dirty = $true
    Read-TsState 'ts-poll'
    $want = $F['vm_wsname'].ToLowerInvariant()
    foreach ($p in $Ts.Peers) {
        if ($G.TsSnap.ContainsKey($p.Key) -or $p.Host.ToLowerInvariant() -ne $want) { continue }
        if ($p.Online -eq 'true') {
            $s.Res = ''; $tn = ''; if ($Ts.Tailnet) { $tn = '(' + $Ts.Tailnet + ') ' }
            Add-VmRes ("ok`t" + $Hd.Fact['tailscale'] + "`t" + $p.Host + ' is on your tailnet ' + $tn + "and online`t")
            $s.State = 'ok'; Write-WizLog ('--- vm guide: ' + $p.Host + ' joined the tailnet'); return
        }
    }
}

function Stop-TsJoin { $s = Get-VmStep; $s.Res = ''; Set-VmFail 'TS_PEER_MISSING' $Hd.Fact['tailscale'] ($F['vm_wsname'] + ' ' + $Hd.Text['ui-ts-missing']) }

function Get-NetInfo {  # NetRows ("label TAB value"): this PC's LAN, the hypervisor's networks and Tailscale, read-only
    $G.NetRows = New-List; $G.NetHostIf = ''; $G.NetSwitch = ''
    if ($Demo) {
        foreach ($r in (Get-DemoLines 'net')) {
            if ($r.StartsWith("hostif`t")) { $G.NetHostIf = $r.Substring(7) } elseif ($r.StartsWith("switch`t")) { $G.NetSwitch = $r.Substring(7) } else { $G.NetRows.Add($r) }
        }
    } elseif ($VmOs -eq 'windows') {
        $cfg = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway }) | Select-Object -First 1
        if ($cfg) {
            $a = @($cfg.IPv4Address)[0]
            $dhcp = (Get-NetIPInterface -InterfaceIndex $cfg.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).Dhcp
            $how = 'static'; if ([string]$dhcp -eq 'Enabled') { $how = 'from DHCP' }
            $G.NetRows.Add("This PC`t" + $a.IPAddress + '/' + $a.PrefixLength + ' on ' + $cfg.InterfaceAlias + ', ' + $how)
            $G.NetRows.Add("Gateway`t" + @($cfg.IPv4DefaultGateway)[0].NextHop)
            $dns = @($cfg.DNSServer | Where-Object { $_.AddressFamily -eq 2 } | ForEach-Object { $_.ServerAddresses }) -join ' '
            if (-not $dns) { $dns = 'not found' }
            $G.NetRows.Add("DNS`t$dns")
        } else { $G.NetRows.Add("This PC`tno IPv4 default gateway found") }
        $i = Get-HvIndex 'hyper-v'
        if ($HvFound[$i] -eq 'yes') {
            $sws = @(Get-VMSwitch -ErrorAction SilentlyContinue)
            $G.NetRows.Add("Hyper-V switches`t" + ((@($sws | ForEach-Object { $_.Name + ' (' + $_.SwitchType + ')' })) -join ', '))
            $nat = @(Get-NetNat -ErrorAction SilentlyContinue | ForEach-Object { $_.Name + ' ' + $_.InternalIPInterfaceAddressPrefix }) -join ', '
            if (-not $nat) { $nat = 'none' }; $G.NetRows.Add("NAT networks`t$nat")
            $ext = @($sws | Where-Object { [string]$_.SwitchType -eq 'External' }) | Select-Object -First 1
            if ($ext) { $G.NetSwitch = $ext.Name } elseif ($sws.Count) { $G.NetSwitch = $sws[0].Name }
        }
        $i = Get-HvIndex 'virtualbox'
        if ($HvFound[$i] -eq 'yes') {
            $b = @(& $HvCmd[$i] list bridgedifs 2>$null | Where-Object { $_ -cmatch '^Name: +' } | ForEach-Object { $_ -creplace '^Name: +', '' })
            $hoi = @(& $HvCmd[$i] list hostonlyifs 2>$null | Where-Object { $_ -cmatch '^Name: +' } | ForEach-Object { $_ -creplace '^Name: +', '' })
            $G.NetRows.Add("VirtualBox bridged`t" + ($b -join '; ')); $G.NetRows.Add("VirtualBox host-only`t" + ($hoi -join '; '))
            if ($b.Count) { $G.NetHostIf = $b[0] }
        }
        $i = Get-HvIndex 'vmware'
        if ($HvFound[$i] -eq 'yes') {
            $vn = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'VMnet' } | ForEach-Object { $_.Name }) -join ', '
            if (-not $vn) { $vn = 'none' }; $G.NetRows.Add("VMware host networks`t$vn (VMnet0 bridged, VMnet1 host-only, VMnet8 NAT)")
        }
    } else { $G.NetRows.Add("This PC`tnot read on this system") }
    Read-TsState 'ts'
    switch ($Ts.State) {
        'Running' { $tn = $Ts.Tailnet; if (-not $tn) { $tn = 'your tailnet' }; $sf = $Ts.Suffix; if (-not $sf) { $sf = 'none' }; $hc = 'off'; if ($Ts.Certs) { $hc = 'on' }; $t = "signed in to $tn, MagicDNS suffix $sf, HTTPS certificates $hc" }
        'missing' { $t = 'not installed on this PC' }
        default { $t = 'installed, ' + $Ts.State }
    }
    $G.NetRows.Add("Tailscale here`t$t")
    Save-TsSnapshot
}

# The guide's flow: Start-VmGuide, then the screens hv, where, net, tsnote, vmform, the steps (vmstep) and vmsum.

function Start-VmGuide {  # start the VM guide, after the Workstation host role or straight from -VmGuide
    Import-HvData; Set-VmOs; Find-Hypervisors
    $G.VmHv = ''; $G.VmWhere = ''; $G.VmNet = 'bridged'; $G.VmDone = $false; $G.VmI = 0; $G.PvePin = ''; $G.PveSeen = ''; $script:PveSecret = ''
    $G.VmFor = ''; $G.VmStepsFile = ''; $script:JobProc = $null; $G.Vs = [System.Collections.Generic.List[object]]::new(); $G.Scroll = 0
    $G.TsEvery = 5000; if ($Demo) { $G.TsEvery = 1000 }
    $script:Vhi = -1; $pick = -1
    for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($Hd.Ids[$i] -eq $Hypervisor) { $pick = $i } }
    if ($pick -lt 0) { for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($HvFound[$i] -eq 'yes') { $pick = $i; break } } }
    if ($pick -lt 0) { $pick = 0 }
    if (-not $G.Role) { $G.Role = 'workstation' }
    Set-Stage 3; $G.StepState[4] = 'later'; $G.StepNote[3] = 'VM guide'
    $G.View = 'hv'; $G.Sel = $pick; $G.Focus = 0; $G.Dirty = $true
    $found = @(); for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($HvFound[$i] -eq 'yes') { $found += $Hd.Ids[$i] } }
    Write-WizLog ('--- vm guide: started; found on this PC: ' + ($found -join ' ') + ' ')
}

function Get-VmWhereOpts {
    $o = New-List; $h = $Hd.Here[$Vhi]
    if ($h -eq 'always' -or ($h -eq 'found' -and $HvFound[$Vhi] -eq 'yes') -or (($h -eq 'windows' -or $h -eq 'unix') -and $VmOs -eq $h)) { $o.Add('here') }
    $o.Add('other'); $G.VmWOpts = $o
}

function Select-VmHv {  # the hypervisor is chosen: read the kit, then ask where the VM runs
    $G.VmHv = $Hd.Ids[$G.Sel]; $script:Vhi = $G.Sel
    if ($G.VmFor -ne $G.VmHv) { foreach ($fd in $Hd.Fields) { $F.Remove('vm_' + $fd.Key) }; $G.VmFor = $G.VmHv }
    Write-WizLog ('--- vm guide: hypervisor ' + $G.VmHv)
    if (-not (Find-Kit $Kit)) { $G.View = 'vmkit'; $G.Focus = 0; return }
    Get-VmWhereOpts
    if ($G.VmWOpts.Count -eq 1) { $G.VmWhere = $G.VmWOpts[0]; Show-VmNet }
    else { $G.View = 'where'; $G.Sel = 0; if ($HvFound[$Vhi] -ne 'yes') { $G.Sel = 1 }; if ($G.Sel -ge $G.VmWOpts.Count) { $G.Sel = 0 }; $G.Focus = 0 }
}

function Get-VmNetOpts { $n = $Hd.Nets[$Vhi]; if (-not $n) { $n = 'bridged' }; $G.VmNOpts = $n.Split(',') }

function Show-VmNet {  # read the network (read-only) and show it
    Get-NetInfo; Get-VmNetOpts
    $G.Sel = 0; for ($i = 0; $i -lt $G.VmNOpts.Count; $i++) { if ($G.VmNOpts[$i] -eq $G.VmNet) { $G.Sel = $i } }
    $G.VmNet = $G.VmNOpts[$G.Sel]; $G.View = 'net'; $G.Focus = 0; $G.Scroll = 0
}

function Get-VmxDefault {  # where Workstation keeps a VM named Deep32Workstation (Broadcom: Documents\Virtual Machines, ~/vmware)
    if ($Demo) { if ($VmOs -eq 'windows') { return 'C:\Users\you\Documents\Virtual Machines\Deep32Workstation\Deep32Workstation.vmx' }; return '/home/you/vmware/Deep32Workstation/Deep32Workstation.vmx' }
    return (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Virtual Machines\Deep32Workstation\Deep32Workstation.vmx')
}

function Set-VmForm {  # the settings this hypervisor and place need, as the form's fields ($F['vm_<key>'])
    $keys = New-List; $labels = New-List; $helps = New-List; $pats = New-List; $perrs = New-List
    Set-VmVars
    foreach ($fd in $Hd.Fields) {
        if ($fd.Hv -ne '*' -and $fd.Hv -ne $G.VmHv) { continue }
        if ($fd.Where -and $fd.Where -ne '*' -and $fd.Where -ne $G.VmWhere -and $fd.Where -ne $G.VmNet) { continue }
        $k = 'vm_' + $fd.Key
        if (-not $F[$k]) { $F[$k] = $fd.Def.Replace('{hostif}', $G.NetHostIf).Replace('{switch}', $G.NetSwitch).Replace('{vmxdefault}', (Get-VmxDefault)) }
        $keys.Add($k); $labels.Add($fd.Label); $helps.Add($fd.Help); $pats.Add($fd.Pat); $perrs.Add($fd.Err)
    }
    $G.FormKeys = $keys.ToArray(); $G.FormLabel = $labels.ToArray(); $G.FormHelp = $helps.ToArray(); $G.FormPat = $pats.ToArray(); $G.FormPErr = $perrs.ToArray()
    $G.FormTitle = $Hd.Names[$Vhi] + ": the VM's settings"
}

function Test-VmForm {  # what is wrong with the VM settings, or ''
    for ($i = 0; $i -lt $G.FormKeys.Count; $i++) {
        if (-not $G.FormPat[$i]) { continue }
        if ([string]$F[$G.FormKeys[$i]] -cnotmatch $G.FormPat[$i]) { return $G.FormPErr[$i] }
    }
    return ''
}

function Start-VmSteps {  # the settings are in: fill the steps, write the steps file, show the first step
    Set-VmVars
    $VF['nodeimage'] = $VmKit.Image; if ($G.VmWhere -eq 'other') { $VF['nodeimage'] = '/var/tmp/' + $VF['imagename'] }
    $VF['vdi'] = $VmKit.Dir + $Sep + 'installer.vdi'; $VF['ts-stall'] = $Hd.Text['ts-stall']
    Build-VmSteps; Write-VmSteps
    $G.VmI = 0; $G.Scroll = 0; $G.View = 'vmstep'; $G.Focus = 0
    Write-WizLog ('--- vm guide: {0} on {1} ({2}), {3} steps' -f $G.VmHv, $G.VmWhere, $G.VmNet, $G.Vs.Count)
}

function Get-VmStateWord([string]$state, [string]$code) {
    switch ($state) { 'ok' { return 'OK' } 'error' { return 'ERROR ' + $code } 'confirmed' { return 'YOU CONFIRMED' } 'skipped' { return 'SKIPPED' } 'waiting' { return 'WAITING' } 'job' { return 'WORKING' } }
    return 'NOT CHECKED'
}

function Get-VmFix([string]$code) {  # the code's meaning and fix steps, this hypervisor's own fix first, filled in: @(meaning, steps)
    Import-ErrorTable
    $mean = ''; $steps = @()
    for ($j = 0; $j -lt $ErrRows.Count; $j++) { if ($ErrRows[$j].Kind -eq 'code' -and $ErrRows[$j].Key -ceq $code) { $mean = $ErrRows[$j].Mean; $steps = Get-FixSteps $j; break } }
    $own = $Hd.Fix[$G.VmHv + ' ' + $code]; if ($own) { $steps = $own.Split([string[]]@(' | '), [StringSplitOptions]::RemoveEmptyEntries) }
    $filled = @(); foreach ($x in $steps) { $filled += (Expand-Vm $x) }
    return @((Expand-Vm $mean), $filled)
}

function View-Hv {
    PTitle 'Workstation VM: the hypervisor' 'Which hypervisor will run the Workstation VM? The wizard looked for each one on this PC (read-only).'
    for ($i = 0; $i -lt $Hd.Ids.Count; $i++) {
        $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
        PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK; PA $St.ACCENT $Gl.CUR } else { PA '' ' '; PA $St.FAINT $Gl.PEND }
        PA '' '  '; PA $St.B $Hd.Names[$i]
        if ($HvFound[$i] -eq 'yes') { PA '' '   '; PA $St.OK 'found on this PC' }
        if ($Hd.Tested[$i] -eq 'yes') { $tag = 'boot-tested'; $ts2 = $St.OK } else { $tag = 'not boot-tested yet'; $ts2 = $St.FAINT }
        PGap ($PW - 3 - $tag.Length); PA $ts2 $tag; PE
    }
    PBlank
    PPara $St.MUTED $Hd.Text['hv-' + $Hd.Ids[$G.Sel]]
    PBottom 1; $G.Btns = @('Continue', 'Later'); PButtons $G.Btns
}

function View-VmKit {
    PTitle 'Workstation VM: no verified disc 1 here' "The guide needs deep32-get's VM folder (recipe.json and the installer copy) next to the verified disc 1 image." $Gl.FAIL $St.ERR
    $fx = Get-VmFix 'KIT_MISSING'
    PPara '' $fx[0]; PBlank
    PL; PA $St.B 'What to do'; PE
    PSteps 4 $fx[1]
    PBottom 1; $G.Btns = @('Check again', 'Back'); PButtons $G.Btns
}

function View-Where {
    PTitle 'Where does the VM run?' $Hd.Names[$Vhi]
    Set-VmVars
    for ($i = 0; $i -lt $G.VmWOpts.Count; $i++) {
        $o = $G.VmWOpts[$i]; $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
        PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK; PA $St.ACCENT $Gl.CUR } else { PA '' ' '; PA $St.FAINT $Gl.PEND }
        PA '' '  '; PA $St.B $Hd.Text['where-' + $o]; PE
        if ($o -eq 'here') {
            if ($HvFound[$Vhi] -eq 'yes' -or $Hd.Local[$Vhi] -eq 'vmx') { $d = $Hd.Text['where-here-ok'].Replace('{tool}', $Hd.Text['tool-' + $Hd.Local[$Vhi]]) } else { $d = $Hd.Text['where-here-missing'] }
        } else { $d = $Hd.Text['where-other-' + $G.VmHv]; if (-not $d) { $d = $Hd.Text['where-other-text'] } }
        foreach ($l in (Get-Wrapped ($CW - 4) (Expand-Vm $d))) { PL $bg; PA '' '    '; if ($sel) { PA '' $l } else { PA $St.MUTED $l }; PE }
        PBlank
    }
}

function View-Net {
    PTitle 'Workstation VM: the network' "This PC's network, read only, and how the VM connects."
    foreach ($r in $G.NetRows) { $tab = $r.IndexOf("`t"); PL; PA $St.MUTED (' ' + $r.Substring(0, $tab)); PGap 24; PA '' $r.Substring($tab + 1); PE }
    PBlank
    PL; PA $St.B $Hd.Text['net-title']; PE
    Set-VmVars
    if ($G.VmNOpts.Count -eq 1) {
        $t = $Hd.Text['net-' + $G.VmHv + '-only']; if (-not $t) { $t = $Hd.Text['net-only'] }
        PPara '' (Expand-Vm $t); PPara $St.MUTED $Hd.Text['net-' + $G.VmNOpts[0]]
    } else {
        for ($i = 0; $i -lt $G.VmNOpts.Count; $i++) {
            $o = $G.VmNOpts[$i]; $sel = $i -eq $G.Sel; $bg = ''; if ($sel) { $bg = $St.SELBG }
            $lab = $Hd.Text['net-' + $G.VmHv + '-' + $o]; if (-not $lab) { $lab = $o }
            PL $bg; if ($sel) { PA $St.ACCENT $Gl.MARK; PA $St.ACCENT $Gl.CUR } else { PA '' ' '; PA $St.FAINT $Gl.PEND }
            PA '' '  '; PA $St.B $Hd.Text['net-label-' + $o]; PA $St.MUTED (': ' + (Expand-Vm $lab))
            if ($o -eq 'bridged') { PA '' '   '; PA $St.OK 'recommended' }
            PE
        }
        PPara $St.MUTED $Hd.Text['net-' + $G.VmNOpts[$G.Sel]]
    }
    PPara $St.WARN $Hd.Text['net-hostonly']
    while ($Panel.Count -gt $CH - 2) { PDrop }
    PBottom 1; $G.Btns = @('Continue', 'Back'); PButtons $G.Btns
}

function View-TsNote {
    PTitle $Hd.Text['ts-title'] ''
    PDrop
    for ($i = 1; $i -le 4; $i++) { PStep $Gl.DOT '' 4 $Hd.Text['ts-' + $i] }
    PBottom 1; $G.Btns = @('Continue', 'Back'); PButtons $G.Btns
}

function Add-VmResultLines([int]$i) {  # the result of step I as panel lines (state word, the rows, and for an error its meaning and fix)
    $s = $G.Vs[$i]; $sw = Get-VmStateWord $s.State $s.Code
    switch ($s.State) {
        'ok' { PL; PA ($St.OK + $St.B) ($Gl.OK + ' ' + $sw); PE }
        'error' { PL; PA ($St.ERR + $St.B) ($Gl.FAIL + ' ' + $sw); PE }
        'confirmed' { PL; PA ($St.WARN + $St.B) ($Gl.YOU + ' ' + $sw); PA $St.MUTED ('  ' + $s.Look); PE }
        'skipped' { PL; PA ($St.FAINT + $St.B) ($Gl.SKIP + ' ' + $sw); PA $St.FAINT '  listed again at the end'; PE }
        'waiting' { $looks = "looked $($G.TsPolls) times"; if ($G.TsPolls -eq 1) { $looks = 'looked once' }
            PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED ('  Waiting for ' + $F['vm_wsname'] + ' to join your tailnet  ' + $Gl.DOT + '  ' + $looks); PE }
        'job' { PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED '  VBoxManage convertfromraw is writing the copy'; PE }
        default { $hint = 'Enter checks this step.'; if ($s.Check -eq 'confirm') { $hint = 'Enter shows what to look for; you confirm it.' }; PL; PA $St.FAINT $hint; PE; return }
    }
    $n = 0
    foreach ($row in $s.Res.Split("`n")) {
        if (-not $row) { continue }
        $x = $row.Split("`t"); if ($x.Count -lt 3) { continue }
        if ($s.State -eq 'error' -and $x[0] -eq 'ok') { continue }
        if ($n -ge 12) { break }
        if ($x[0] -eq 'ok') { PL; PA $St.OK (' ' + $Gl.OK); PA $St.MUTED ('  ' + $x[1]); PGap 24; PA $St.MUTED $x[2]; PE }
        elseif ($x[0] -eq 'err') { PL; PA $St.ERR (' ' + $Gl.FAIL); PA '' ('  ' + $x[1]); PGap 24; PA '' $x[2]; PE }
        else { PL; PA $St.FAINT (' ' + $Gl.INFO); PA $St.MUTED ('  ' + $x[1]); PGap 24; PA $St.MUTED $x[2]; PE }
        $n++
    }
    if ($s.State -eq 'error') {
        $fx = Get-VmFix $s.Code
        if ($fx[0]) { PPara $St.MUTED $fx[0] }
        PL; PA $St.B 'What to do'; PE; PSteps 3 $fx[1]
    }
}

function View-VmStep {
    $i = $G.VmI; $s = $G.Vs[$i]; $n = $G.Vs.Count
    PTitle $s.Title ('Step {0} of {1}  {2}  {3}, {4}  {2}  {5}' -f ($i + 1), $n, $Gl.DOT, $Hd.Names[$Vhi], (Get-VmTestedWord), $s.Place)
    $headLines = $script:Panel
    $script:Panel = [System.Collections.Generic.List[string]]::new(); Add-VmResultLines $i; $res = $script:Panel
    $most = $CH - $headLines.Count - 5; if ($res.Count -gt $most) { $res.RemoveRange($most, $res.Count - $most) }
    $script:Panel = [System.Collections.Generic.List[string]]::new(); $k = 0
    foreach ($it in (Get-VmItems $s.Body)) { $k++; PStep $k '' 5 $it }
    $body = $script:Panel
    $script:Panel = $headLines
    $room = [math]::Max(2, $CH - $Panel.Count - $res.Count - 4); $total = $body.Count
    if ($G.Scroll -gt $total - $room) { $G.Scroll = $total - $room }; if ($G.Scroll -lt 0) { $G.Scroll = 0 }
    for ($j = $G.Scroll; $j -lt $total -and $j -lt $G.Scroll + $room; $j++) { $Panel.Add($body[$j]) }
    $more = $total - $G.Scroll - $room
    if ($more -gt 0) { PL; PA $St.FAINT ('  ' + $Gl.DOWN + " $more more lines, PgDn"); PE }
    PBottom ($res.Count + 1)
    foreach ($l in $res) { $Panel.Add($l) }
    switch ($s.State) {
        { $_ -in 'ok', 'confirmed', 'skipped' } { if ($i -eq $n - 1) { $G.Btns = @('Finish', 'Check again') } else { $G.Btns = @('Next', 'Check again') } }
        'waiting' { $G.Btns = @('Stop waiting', 'Skip') }
        'job' { $G.Btns = @() }
        default { $G.Btns = @('Check', 'Skip') }
    }
    if ($G.Btns.Count) { PButtons $G.Btns } else { PBlank }
}

function View-VmSum {
    PTitle 'Workstation VM: where it stands' ($Hd.Names[$Vhi] + ', ' + (Get-VmTestedWord) + '. Every step, as it ended:')
    $sk = @()
    for ($i = 0; $i -lt $G.Vs.Count; $i++) {
        $s = $G.Vs[$i]; $sw = Get-VmStateWord $s.State $s.Code
        switch ($s.State) { 'ok' { $ic = $Gl.OK; $is = $St.OK } 'error' { $ic = $Gl.FAIL; $is = $St.ERR } 'confirmed' { $ic = $Gl.YOU; $is = $St.WARN } default { $ic = $Gl.SKIP; $is = $St.FAINT; $sk += ('{0}. {1}' -f ($i + 1), $s.Title) } }
        PL; PA $is (' ' + $ic); PA '' ('  {0}. {1}' -f ($i + 1), $s.Title); PGap ($PW - 3 - $sw.Length); PA $is $sw; PE
    }
    PBlank
    if ($sk.Count) { PL; PA $St.B 'Skipped, still to do'; PE; foreach ($x in $sk) { PL; PA $St.WARN ('   ' + $x); PE }; PBlank }
    if ($G.VmStepsFile) {
        if ($Demo) { PPara $St.FAINT ('Every command is in steps-' + $G.VmHv + '.txt in the kit folder (the demo writes no file).') }
        else { PL; PA $St.FAINT 'Every command: '; PA $St.MUTED (Get-ShortPath $G.VmStepsFile); PE }
    }
    PBottom 1; $G.Btns = @('Done', 'Back'); PButtons $G.Btns
}

function View-VmBox {  # the questions a step asks over the screen: what to look for, the certificate, the token's secret
    $keep = [math]::Min(5, $Panel.Count); while ($Panel.Count -gt $keep) { PDrop }
    $s = Get-VmStep
    switch ($G.Overlay) {
        'vmconfirm' {
            BxTop $St.WARN 'Confirm this step'; $script:BxStyle = $St.WARN
            BxText $St.B ('Look for: ' + $s.Look)
            if ($s.Code -eq 'TS_NOT_HERE') { BxOpen $St.WARN; BxClose; $t = $Hd.Text['ui-ts-not-here']; BxText '' ($t.Substring(0, 1).ToUpperInvariant() + $t.Substring(1)) }
            if ($s.Check -ne 'confirm' -and $s.Code -ne 'TS_NOT_HERE' -and -not $s.Check.EndsWith('+look')) { BxOpen $St.WARN; BxClose; BxText '' $Hd.Text['ui-nofacts'] }
            BxOpen $St.WARN; BxClose; BxButtons @('Yes, I see it', 'Not yet'); BxBot $St.WARN
        }
        'vmpin' {
            BxTop $St.ACCENT "The node's certificate"; $script:BxStyle = $St.ACCENT
            BxText '' ('Proxmox uses its own certificate. Its SHA-256 fingerprint at https://' + $F['vm_host'] + ':8006 is:')
            BxText $St.B $G.PveSeen
            BxText '' 'Compare it with the node: in the web UI, the node, Certificates; or on the node `pvenode cert info` (pve-ssl.pem). If it matches, the wizard trusts this certificate for this session only.'
            BxOpen $St.ACCENT; BxClose; BxButtons @('It matches', 'It does not'); BxBot $St.ACCENT
        }
        'vmsecret' {
            BxTop $St.ACCENT 'API token secret'; $script:BxStyle = $St.ACCENT
            BxText '' ('Type or paste the secret of ' + $F['vm_token'] + '. It stays in memory for this session only: it is never shown, logged or saved.')
            BxOpen $St.ACCENT; PA $St.CHIPBG (' ' + ($Gl.DOT * $script:SecretTyped.Length)); PA ($St.CHIPBG + $St.ACCENT) ($Gl.CURSOR + ' '); BxClose
            BxOpen $St.ACCENT; BxClose; BxButtons @('Use it', 'Cancel'); BxBot $St.ACCENT
        }
    }
}

function Complete-VmCheck {  # after a check ran: open the box it asks for, or stay
    $s = Get-VmStep
    switch ($s.State) {
        'ask-confirm' { $G.Overlay = 'vmconfirm'; $G.Focus = 0 }
        'ask-pin' { if ($G.PveSeen) { $G.Overlay = 'vmpin'; $G.Focus = 0 } }
        'ask-secret' { $G.Overlay = 'vmsecret'; $G.Focus = 0; $script:SecretTyped = '' }
    }
    if ($s.State -eq 'error' -and $s.Code -eq 'PVE_UNREACHABLE') { $G.PveSeen = '' }
    if ($s.State -eq 'ok' -and $s.Check.StartsWith('vm:') -and $s.Check.EndsWith('+look')) { $G.Overlay = 'vmconfirm'; $G.Focus = 0 }
}

function Move-VmNext {  # the next step, or the summary after the last one
    if ($G.VmI -lt $G.Vs.Count - 1) { $G.VmI++; $G.Scroll = 0; $G.Focus = 0; $G.StepNote[3] = 'VM step {0} of {1}' -f ($G.VmI + 1), $G.Vs.Count }
    else { $G.View = 'vmsum'; $G.Focus = 0 }
}

function Complete-VmGuide {  # the guide is over: the usual Done screen lists what is left
    $G.VmDone = $true; $G.VmSkipped = @()
    foreach ($s in $G.Vs) { if ($s.State -ne 'ok' -and $s.State -ne 'confirmed') { $G.VmSkipped += $s.Title } }
    Write-WizLog ('--- vm guide: finished; not done: ' + $G.VmSkipped.Count)
    Complete-Wizard
}

function Stop-VmGuide { $G.VmDone = $false; Write-WizLog '--- vm guide: put off'; Complete-Wizard }

function Exit-VmNet { if ($G.VmWOpts.Count -gt 1) { $G.View = 'where'; $G.Sel = 0; if ($G.VmWhere -eq 'other') { $G.Sel = 1 } } else { $G.View = 'hv'; $G.Sel = $Vhi }; $G.Focus = 0 }

function Set-VmSkipped { $s = Get-VmStep; $s.State = 'skipped'; $s.Res = ''; Write-WizLog ('--- vm guide: step {0} skipped' -f ($G.VmI + 1)); $G.Focus = 0 }

function Invoke-VmKey([string]$k) {  # keys for the guide's screens and boxes
    $nav = $k -in 'left', 'right', 'tab', 'btab'
    switch ($G.Overlay) {
        'vmconfirm' {
            if ($nav) { Move-Button $k; return } elseif ($k -in 'y', 'Y') { $G.Focus = 0; $k = 'enter' } elseif ($k -in 'n', 'N', 'esc') { $G.Focus = 1; $k = 'enter' }
            if ($k -eq 'enter') {
                $G.Overlay = ''; $s = Get-VmStep
                if ($G.Focus -eq 0) { if ($s.State -ne 'ok') { $s.Res += "info`t" + $Hd.Fact['seen'] + "`t" + $s.Look + "`t`n" }; $s.State = 'confirmed'; Write-WizLog ('--- vm guide: step {0} confirmed by you' -f ($G.VmI + 1)) }
                else { $s.State = 'pending'; $G.StatusMsg = 'Not confirmed yet. Do the step, then press Enter again.' }
                $G.Focus = 0
            }
            return
        }
        'vmpin' {
            if ($nav) { Move-Button $k; return } elseif ($k -in 'y', 'Y') { $G.Focus = 0; $k = 'enter' } elseif ($k -in 'n', 'N', 'esc') { $G.Focus = 1; $k = 'enter' }
            if ($k -eq 'enter') {
                $G.Overlay = ''
                if ($G.Focus -eq 0) { $G.PvePin = $G.PveSeen; Write-WizLog ('--- vm guide: certificate ' + $G.PvePin + ' pinned for this session'); Invoke-VmCheck; Complete-VmCheck }
                else { (Get-VmStep).State = 'pending'; $G.PveSeen = ''; Set-VmFail 'PVE_TLS_MISMATCH' $Hd.Fact['api'] 'the fingerprint did not match' }
            }
            return
        }
        'vmsecret' {
            if ($k -eq 'enter') { $G.Overlay = ''; if ($G.Focus -eq 1) { (Get-VmStep).State = 'pending'; $script:SecretTyped = '' } else { $script:PveSecret = $script:SecretTyped; $script:SecretTyped = ''; Invoke-VmCheck; Complete-VmCheck } }
            elseif ($k -eq 'esc') { $G.Overlay = ''; (Get-VmStep).State = 'pending'; $script:SecretTyped = '' }
            elseif ($nav) { Move-Button $k }
            elseif ($k -eq 'bs') { if ($script:SecretTyped.Length) { $script:SecretTyped = $script:SecretTyped.Substring(0, $script:SecretTyped.Length - 1) } }
            elseif ($k -eq 'ctrl-u') { $script:SecretTyped = '' }
            elseif ($k.Length -eq 1 -and [int][char]$k -ge 32) { $script:SecretTyped += $k }
            return
        }
    }
    switch ($G.View) {
        'hv' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- } } elseif ($k -eq 'down') { if ($G.Sel -lt $Hd.Ids.Count - 1) { $G.Sel++ } }
            elseif ($nav) { Move-Button $k } elseif ($k -eq 'esc') { Stop-VmGuide }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { Stop-VmGuide } else { Select-VmHv } }
        }
        'vmkit' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'hv'; $G.Sel = Get-HvIndex $G.VmHv; $G.Focus = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { $G.View = 'hv'; $G.Sel = Get-HvIndex $G.VmHv; $G.Focus = 0 } else { $G.Sel = $Vhi; Select-VmHv } }
        }
        'where' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- } } elseif ($k -eq 'down') { if ($G.Sel -lt $G.VmWOpts.Count - 1) { $G.Sel++ } }
            elseif ($k -eq 'esc') { $G.View = 'hv'; $G.Sel = $Vhi; $G.Focus = 0 }
            elseif ($k -eq 'enter') { $G.VmWhere = $G.VmWOpts[$G.Sel]; Write-WizLog ('--- vm guide: the VM runs ' + $G.VmWhere); Show-VmNet }
        }
        'net' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- }; $G.VmNet = $G.VmNOpts[$G.Sel] }
            elseif ($k -eq 'down') { if ($G.Sel -lt $G.VmNOpts.Count - 1) { $G.Sel++ }; $G.VmNet = $G.VmNOpts[$G.Sel] }
            elseif ($nav) { Move-Button $k } elseif ($k -eq 'esc') { Exit-VmNet }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { Exit-VmNet } else { $G.VmNet = $G.VmNOpts[$G.Sel]; Write-WizLog ('--- vm guide: network ' + $G.VmNet); $G.View = 'tsnote'; $G.Focus = 0 } }
        }
        'tsnote' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'net'; $G.Focus = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { $G.View = 'net'; $G.Focus = 0 } else { Set-VmForm; $G.View = 'vmform'; $G.Sel = 0; $G.Focus = 0 } }
        }
        'vmform' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'tsnote'; $G.Focus = 0 }
            elseif ($k -eq 'enter') {
                if ($G.Focus -eq 1) { $G.View = 'tsnote'; $G.Focus = 0; return }
                $err = Test-VmForm; if ($err) { $G.StatusMsg = $err; return }
                Start-VmSteps
            }
        }
        'vmstep' {
            $s = Get-VmStep; $state = $s.State
            if ($k -eq 'up') { if ($G.Scroll -gt 0) { $G.Scroll-- } } elseif ($k -eq 'down') { $G.Scroll++ }
            elseif ($k -eq 'pgup') { $G.Scroll = [math]::Max(0, $G.Scroll - 8) } elseif ($k -eq 'pgdn') { $G.Scroll += 8 }
            elseif ($nav) { Move-Button $k }
            elseif ($k -in 's', 'S') { if ($state -ne 'job') { Set-VmSkipped } }
            elseif ($k -eq 'esc') {
                if ($state -eq 'waiting') { $s.State = 'pending' }
                if ($G.VmI -gt 0) { $G.VmI--; $G.Scroll = 0; $G.Focus = 0 } else { Set-VmForm; $G.View = 'vmform'; $G.Sel = 0; $G.Focus = 0 }
            }
            elseif ($k -eq 'enter') {
                if ($state -eq 'job') { }
                elseif ($state -eq 'waiting') { if ($G.Focus -eq 0) { Stop-TsJoin } else { $s.State = 'skipped' }; $G.Focus = 0 }
                elseif ($state -in 'ok', 'confirmed', 'skipped') { if ($G.Focus -eq 0) { Move-VmNext } else { Invoke-VmCheck; Complete-VmCheck } }
                else { if ($G.Focus -eq 1) { Set-VmSkipped } else { Invoke-VmCheck; Complete-VmCheck; $G.Focus = 0 } }
            }
        }
        'vmsum' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'vmstep'; $G.Focus = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { $G.View = 'vmstep'; $G.Focus = 0 } else { Complete-VmGuide } }
        }
    }
}

function Update-VmGuide {  # each pass of the loop: the tailnet wait and the VirtualBox copy
    if ($G.View -ne 'vmstep' -or -not $G.Vs -or $G.Vs.Count -eq 0) { return }
    switch ((Get-VmStep).State) { 'waiting' { Update-TsJoin } 'job' { Update-KitVdi } }
}

# The same guide as plain lines (-VmGuide when the full screen is off): one question per line, answers read from
# standard input, the same steps, checks and results.

function Write-Plain([string]$s) { [Console]::Out.Write($s.Replace("`n", [Environment]::NewLine)); [Console]::Out.Flush() }

function Read-PlainAnswer([string]$prompt) {  # one answer line into $script:Ans (answers read from a pipe are echoed, so a log shows them)
    Write-Plain $prompt
    $a = [Console]::In.ReadLine()
    if ($null -eq $a) { $script:PlainEof = $true; $a = '' }
    $script:Ans = $a.TrimEnd("`r")
    if ([Console]::IsInputRedirected) { Write-Plain ($Ans + "`n") }
    if ($PlainEof -and -not $Ans) { Write-Plain "`nThe answers ended; the guide stops here. Run it again to go on.`n"; return $false }
    return $true
}

function Read-PlainChoice([int]$def, [int]$count) {  # a number from 1 to COUNT, empty for DEFAULT, into $script:Pick
    for ($tries = 0; $tries -lt 3; $tries++) {
        if (-not (Read-PlainAnswer "Number [$def]: ")) { return $false }
        if (-not $Ans) { $script:Pick = $def; return $true }
        if ($Ans -match '^[0-9]+$' -and [int]$Ans -ge 1 -and [int]$Ans -le $count) { $script:Pick = [int]$Ans; return $true }
        if ($tries -lt 2) { Write-Plain ("Type a number from 1 to $count.`n") }
    }
    return $false
}

function Write-PlainResult([int]$i) {  # the result of step I as plain lines
    $s = $G.Vs[$i]
    Write-Plain ('Result: ' + (Get-VmStateWord $s.State $s.Code) + "`n")
    foreach ($row in $s.Res.Split("`n")) {
        if (-not $row) { continue }
        $x = $row.Split("`t"); if ($x.Count -lt 4) { continue }
        if ($x[0] -eq 'ok') { Write-Plain ('  ok     ' + $x[1] + ': ' + $x[2] + "`n") } elseif ($x[0] -eq 'err') { Write-Plain ('  ERROR  ' + $x[1] + ': ' + $x[2] + ' (' + $x[3] + ")`n") } else { Write-Plain ('  note   ' + $x[1] + ': ' + $x[2] + "`n") }
    }
    if ($s.State -eq 'error') {
        $fx = Get-VmFix $s.Code
        if ($fx[0]) { Write-Plain ('What it means: ' + $fx[0] + "`n") }
        Write-Plain "What to do:`n"; $k = 0; foreach ($it in $fx[1]) { $k++; Write-Plain (Get-VmPlainItem "  $k) " $it) }
    }
}

function Wait-PlainStep {  # after Invoke-VmCheck: answer the questions it asks, wait for what it waits for
    for ($guard = 0; $guard -lt 8; $guard++) {
        $s = Get-VmStep
        switch ($s.State) {
            'ask-confirm' {
                if ($s.Code -eq 'TS_NOT_HERE') { Write-Plain ('Tailscale: ' + $Hd.Text['ui-ts-not-here'] + "`n") }
                Write-Plain ('Look for: ' + $s.Look + "`n")
                if (-not (Read-PlainAnswer 'Did you see it? [y/N] ')) { return $false }
                if ($Ans -match '^[Yy]') { $s.Res += "info`t" + $Hd.Fact['seen'] + "`t" + $s.Look + "`t`n"; $s.State = 'confirmed' }
                else { $s.State = 'pending'; Write-Plain "Not confirmed yet. Do the step, then check it again.`n" }
                return $true
            }
            'ask-pin' {
                if (-not $G.PveSeen) { return $true }
                Write-Plain ("The node's certificate at https://" + $F['vm_host'] + ":8006 has this SHA-256 fingerprint:`n  " + $G.PveSeen + "`n")
                Write-Plain "Compare it with the node: in the web UI, the node, Certificates; or on the node: pvenode cert info (pve-ssl.pem).`n"
                if (-not (Read-PlainAnswer 'Does it match? [y/N] ')) { return $false }
                if ($Ans -match '^[Yy]') { $G.PvePin = $G.PveSeen; Write-Plain "Pinned for this session.`n"; Invoke-VmCheck }
                else { $G.PveSeen = ''; $s.State = 'pending'; Set-VmFail 'PVE_TLS_MISMATCH' $Hd.Fact['api'] 'the fingerprint did not match'; return $true }
            }
            'ask-secret' {
                Write-Plain ('Secret of ' + $F['vm_token'] + ' (kept in memory for this session only; not shown, logged or saved): ')
                $sec = ''
                if ([Console]::IsInputRedirected) { $sec = [Console]::In.ReadLine(); if ($null -eq $sec) { $sec = ''; $script:PlainEof = $true } }
                else { while ($true) { $key = [Console]::ReadKey($true); if ($key.Key -eq 'Enter') { break }; if ($key.Key -eq 'Backspace') { if ($sec.Length) { $sec = $sec.Substring(0, $sec.Length - 1) } } elseif ([int]$key.KeyChar -ge 32) { $sec += $key.KeyChar } } }
                Write-Plain "`n"; $sec = $sec.TrimEnd("`r")
                if (-not $sec) { $s.State = 'pending'; return $true }
                $script:PveSecret = $sec; Invoke-VmCheck
            }
            'waiting' {
                Write-Plain (('Waiting for {0} to join your tailnet (this PC looks every {1} s, for up to {2} minutes).' -f $F['vm_wsname'], [math]::Floor($G.TsEvery / 1000), [math]::Floor($TsLimit / 60)) + "`n")
                $start = Get-NowMs
                while ($s.State -eq 'waiting') {
                    $G.TsPollAt = 0; Update-TsJoin
                    if ($s.State -ne 'waiting') { break }
                    if ((Get-NowMs) - $start -ge $TsLimit * 1000) { Stop-TsJoin; break }
                    if ($Demo) { Start-Sleep -Milliseconds 200 } else { Start-Sleep -Milliseconds $G.TsEvery }
                }
                return $true
            }
            'job' {
                Write-Plain "VBoxManage convertfromraw is writing the copy.`n"
                while ($s.State -eq 'job') { Update-KitVdi; if ($s.State -eq 'job') { Start-Sleep -Milliseconds 500 } }
                return $true
            }
            default { return $true }
        }
    }
    return $true
}

function Invoke-VmPlain {  # the whole guide on plain lines
    $script:PlainEof = $false; $script:TsLimit = 600
    Initialize-State; $G.Role = 'workstation'
    $script:F = @{}
    Import-HvData; Import-ErrorTable; Set-VmOs; Find-Hypervisors; $G.VmNet = 'bridged'; $G.PvePin = ''; $G.PveSeen = ''; $script:PveSecret = ''
    $G.TsEvery = 5000; if ($Demo) { $G.TsEvery = 1000 }
    $demoNote = ''; if ($Demo) { $demoNote = ' (demo: no hypervisor is asked and nothing changes)' }
    Write-Plain ("Deep32 Workstation VM guide$demoNote`nThe wizard only reads: it never creates, changes, starts or stops a VM.`n`n")
    $hvDefault = -1
    for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($Hd.Ids[$i] -eq $Hypervisor) { $hvDefault = $i } }
    if ($hvDefault -lt 0) { for ($i = 0; $i -lt $Hd.Ids.Count; $i++) { if ($HvFound[$i] -eq 'yes') { $hvDefault = $i; break } } }
    if ($hvDefault -lt 0) { $hvDefault = 0 }
    Write-Plain "Which hypervisor runs the Workstation VM?`n"
    for ($i = 0; $i -lt $Hd.Ids.Count; $i++) {
        $script:Vhi = $i; $fnd = ''; if ($HvFound[$i] -eq 'yes') { $fnd = ', found on this PC' }
        Write-Plain (('  {0}) {1}, {2}{3}' -f ($i + 1), $Hd.Names[$i], (Get-VmTestedWord), $fnd) + "`n")
    }
    if (-not (Read-PlainChoice ($hvDefault + 1) $Hd.Ids.Count)) { return 1 }
    $G.Sel = $Pick - 1; $G.VmHv = $Hd.Ids[$G.Sel]; $script:Vhi = $G.Sel
    Write-Plain ($Hd.Text['hv-' + $G.VmHv] + "`n")
    if (-not (Find-Kit $Kit)) {
        $fx = Get-VmFix 'KIT_MISSING'; Write-Plain ("No verified disc 1 with deep32-get's VM folder here.`n" + $fx[0] + "`nWhat to do:`n")
        $k = 0; foreach ($it in $fx[1]) { $k++; Write-Plain (Get-VmPlainItem "  $k) " $it) }
        return 1
    }
    Get-VmWhereOpts
    if ($G.VmWOpts.Count -gt 1) {
        Write-Plain "Where does the VM run?`n"
        for ($i = 0; $i -lt $G.VmWOpts.Count; $i++) { Write-Plain (('  {0}) {1}' -f ($i + 1), $Hd.Text['where-' + $G.VmWOpts[$i]]) + "`n") }
        $wdef = 1; if ($HvFound[$Vhi] -ne 'yes') { $wdef = 2 }
        if (-not (Read-PlainChoice $wdef $G.VmWOpts.Count)) { return 1 }
        $G.VmWhere = $G.VmWOpts[$Pick - 1]
    } else { $G.VmWhere = $G.VmWOpts[0] }
    Write-Plain "`nThe network, read on this PC:`n"
    Get-NetInfo; Get-VmNetOpts
    foreach ($r in $G.NetRows) { $tab = $r.IndexOf("`t"); Write-Plain ('  ' + $r.Substring(0, $tab).PadRight(22) + ' ' + $r.Substring($tab + 1) + "`n") }
    Set-VmVars
    Write-Plain ($Hd.Text['net-title'] + ":`n")
    if ($G.VmNOpts.Count -eq 1) {
        $G.VmNet = $G.VmNOpts[0]; $t = $Hd.Text['net-' + $G.VmHv + '-only']; if (-not $t) { $t = $Hd.Text['net-only'] }
        Write-Plain ('  ' + (Expand-Vm $t) + "`n  " + $Hd.Text['net-' + $G.VmNet] + "`n")
    } else {
        for ($i = 0; $i -lt $G.VmNOpts.Count; $i++) { $o = $G.VmNOpts[$i]; Write-Plain (('  {0}) {1}: {2}' -f ($i + 1), $Hd.Text['net-label-' + $o], (Expand-Vm $Hd.Text['net-' + $G.VmHv + '-' + $o])) + "`n") }
        foreach ($o in $G.VmNOpts) { Write-Plain ('     ' + $Hd.Text['net-' + $o] + "`n") }
        if (-not (Read-PlainChoice 1 $G.VmNOpts.Count)) { return 1 }
        $G.VmNet = $G.VmNOpts[$Pick - 1]
    }
    Write-Plain ('  ' + $Hd.Text['net-hostonly'] + "`n`n" + $Hd.Text['ts-title'] + ":`n")
    for ($i = 1; $i -le 4; $i++) { Write-Plain ('  - ' + $Hd.Text['ts-' + $i] + "`n") }
    Write-Plain "`nThe VM's settings (Enter keeps the value in brackets):`n"
    Set-VmForm
    for ($i = 0; $i -lt $G.FormKeys.Count; $i++) {
        Write-Plain ('  ' + $G.FormHelp[$i].Replace('`', '') + "`n")
        $tries = 0
        while ($true) {
            if (-not (Read-PlainAnswer ($G.FormLabel[$i] + ' [' + [string]$F[$G.FormKeys[$i]] + ']: '))) { return 1 }
            if ($Ans) { $F[$G.FormKeys[$i]] = $Ans }
            if (-not $G.FormPat[$i] -or [string]$F[$G.FormKeys[$i]] -cmatch $G.FormPat[$i]) { break }
            Write-Plain ($G.FormPErr[$i] + "`n"); $tries++; if ($tries -ge 3) { return 1 }
        }
    }
    Start-VmSteps
    $n = $G.Vs.Count
    Write-Plain ("`n" + $Hd.Names[$Vhi] + ', ' + (Get-VmTestedWord) + ": $n steps.")
    if ($Demo) { Write-Plain (' The steps file is steps-' + $G.VmHv + ".txt in the kit folder (the demo writes no file).`n") }
    else { $sf = $G.VmStepsFile; if (-not $sf) { $sf = 'the kit folder' }; Write-Plain (" Every command is also in $sf`n") }
    $G.VmI = 0
    while ($G.VmI -lt $n) {
        $s = Get-VmStep
        Write-Plain ("`nStep {0} of {1}: {2}`n" -f ($G.VmI + 1), $n, $s.Title)
        if ($s.Place) { Write-Plain ('Where: ' + $s.Place + "`n") }
        $k = 0; foreach ($it in (Get-VmItems $s.Body)) { $k++; Write-Plain (Get-VmPlainItem "  $k) " $it) }
        $back = $false
        while ($true) {
            if (-not (Read-PlainAnswer 'Enter checks this step; s skips it, b goes back, q stops: ')) { return 1 }
            if ($Ans -in 'q', 'Q') { Write-Plain "Stopped. Run the guide again to go on.`n"; return 1 }
            if ($Ans -in 'b', 'B') { if ($G.VmI -gt 0) { $G.VmI-- }; $back = $true; break }
            if ($Ans -in 's', 'S') { $s.State = 'skipped'; $s.Res = ''; Write-PlainResult $G.VmI; break }
            if ($Ans -eq '') {
                Invoke-VmCheck; if (-not (Wait-PlainStep)) { return 1 }
                if ($s.State -eq 'ok' -and $s.Check.StartsWith('vm:') -and $s.Check.EndsWith('+look')) { $s.State = 'ask-confirm'; if (-not (Wait-PlainStep)) { return 1 } }
                Write-PlainResult $G.VmI
                if ($s.State -in 'ok', 'confirmed') { break }
                continue
            }
            Write-Plain "Press Enter, or type s, b or q.`n"
        }
        if (-not $back) { $G.VmI++ }
    }
    Write-Plain "`nWhere it stands:`n"
    $sk = 0
    for ($i = 0; $i -lt $n; $i++) { $s = $G.Vs[$i]; Write-Plain (('  {0}. {1} {2}' -f ($i + 1), $s.Title.PadRight(44), (Get-VmStateWord $s.State $s.Code)) + "`n"); if ($s.State -ne 'ok' -and $s.State -ne 'confirmed') { $sk++ } }
    if ($sk) { Write-Plain "Skipped, still to do:`n"; for ($i = 0; $i -lt $n; $i++) { $s = $G.Vs[$i]; if ($s.State -ne 'ok' -and $s.State -ne 'confirmed') { Write-Plain (('  {0}. {1}' -f ($i + 1), $s.Title) + "`n") } } }
    Write-Plain "`nNext: pair the Forge and the agent PC with the Workstation (the wizard's What's next, or INSTALL.md).`n"
    return 0
}

# ---------------------------------------------------------------------------------------------------------------
# 11. Keys.

function Read-WizKey {  # the next key as a name (up, enter, ...) or the character, or $null
    if (-not [Console]::KeyAvailable) { return $null }
    $k = [Console]::ReadKey($true)
    if (($k.Modifiers -band [ConsoleModifiers]::Control) -and $k.Key -eq 'C') { return 'ctrl-c' }
    if (($k.Modifiers -band [ConsoleModifiers]::Control) -and $k.Key -eq 'U') { return 'ctrl-u' }
    switch ($k.Key) {
        'UpArrow' { return 'up' } 'DownArrow' { return 'down' } 'LeftArrow' { return 'left' } 'RightArrow' { return 'right' }
        'Enter' { return 'enter' } 'Escape' { return 'esc' } 'Backspace' { return 'bs' } 'PageUp' { return 'pgup' } 'PageDown' { return 'pgdn' }
        'Home' { return 'home' } 'End' { return 'end' } 'Delete' { return 'del' }
        'Tab' { if ($k.Modifiers -band [ConsoleModifiers]::Shift) { return 'btab' }; return 'tab' }
    }
    if ($k.KeyChar -and [int]$k.KeyChar -ge 32) { return [string]$k.KeyChar }
    return 'ignore'
}

function Read-ScriptKey {  # the next key from DEEP32_WIZARD_KEYS (tests and screenshots), or $null
    $now = Get-NowMs
    if ($G.Until) {
        if ((" $($G.View) $($G.Overlay) ").Contains(" $($G.Until) ") -or $now -gt $G.UntilAt) {
            if ($now -gt $G.UntilAt) { Write-WizLog "--- script: gave up waiting for $($G.Until)" }
            $G.Until = ''
        } else { return $null }
    }
    if ($G.WaitUntil -and $now -lt $G.WaitUntil) { return $null }
    $G.WaitUntil = 0
    if ($G.Keys.Count -eq 0) { if (-not $G.Proc) { $G.Quit = $true }; return $null }
    $t = $G.Keys[0]; $G.Keys.RemoveAt(0)
    if ($t.StartsWith('wait:')) { $G.WaitUntil = $now + [long]([double]::Parse($t.Substring(5), [Globalization.CultureInfo]::InvariantCulture) * 1000); return $null }
    if ($t.StartsWith('waitms:')) { $G.WaitUntil = $now + [long]$t.Substring(7); return $null }
    if ($t.StartsWith('until:')) { $G.Until = $t.Substring(6); $G.UntilAt = $now + 90000; return $null }
    if ($t.StartsWith('snap:')) { $G.Snap = $t.Substring(5); $G.Dirty = $true; return $null }
    if ($t.StartsWith('size:')) { $p = $t.Substring(5).Split('x'); $script:W = [int]$p[0]; $script:H = [int]$p[1]; Set-Layout; $script:Full = $true; Write-Out "$E[2J"; $G.Dirty = $true; return $null }
    if ($t.StartsWith('text:')) { $s = $t.Substring(5); for ($i = $s.Length - 1; $i -ge 0; $i--) { $G.Keys.Insert(0, 'char:' + $s[$i]) }; return $null }
    if ($t.StartsWith('char:')) { return $t.Substring(5) }
    if ($t -eq 'space') { return ' ' }
    if ($t -eq 'crash') { throw 'the test asked for an error' }
    return $t
}

function Move-Button([string]$k) {
    if ($k -in 'left', 'btab') { if ($G.Focus -gt 0) { $G.Focus-- } }
    elseif ($k -in 'right', 'tab') { if ($G.Focus -lt $G.BtnCount - 1) { $G.Focus++ } }
}

function Invoke-Key([string]$k) {
    $G.Dirty = $true; $G.StatusMsg = ''
    if ($W -lt 80 -or $H -lt 24) {
        if ($G.Proc) { return }   # a running step goes on; the screen comes back when the window is bigger
        if ($k -in 'p', 'P') { $G.PlainAfter = $true; $G.Quit = $true } elseif ($k -in 'q', 'Q', 'ctrl-c') { $G.Quit = $true }; return
    }
    if ($G.Overlay -in 'vmconfirm', 'vmpin', 'vmsecret') { Invoke-VmKey $k; return }
    switch ($G.Overlay) {
        'log' {
            switch ($k) { 'up' { $G.LogScroll++ } 'down' { if ($G.LogScroll -gt 0) { $G.LogScroll-- } } 'pgup' { $G.LogScroll += $CH - 5 } 'pgdn' { $G.LogScroll = [math]::Max(0, $G.LogScroll - $CH + 5) }
                'home' { $G.LogScroll = $G.Log.Count } 'end' { $G.LogScroll = 0 } default { if ($k -in 'esc', 'l', 'L', 'q', 'Q', 'enter') { $G.Overlay = '' } } }
            if (-not $G.Overlay -and $G.AskPending) { Open-Ask }
            return
        }
        'help' { $G.Overlay = ''; if ($G.AskPending) { Open-Ask }; return }
        'quit' { if ($k -in 'left', 'right', 'tab', 'btab') { Move-Button $k } elseif ($k -eq 'esc') { $G.Overlay = '' } elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { $G.Quit = $true } else { $G.Overlay = '' } } elseif ($k -in 'q', 'Q', 'ctrl-c') { $G.Quit = $true }
            if (-not $G.Overlay -and $G.AskPending) { Open-Ask }; return }
        'ask' {
            if ($k -in 'left', 'right', 'tab', 'btab') { Move-Button $k; return }
            if ($k -in 'y', 'Y') { $G.Focus = 0; $k = 'enter' } elseif ($k -in 'n', 'N', 'esc') { $G.Focus = 1; $k = 'enter' }
            if ($k -eq 'enter') { if ($G.Focus -eq 0) { Send-Answer 'y' } else { Send-Answer 'n' }; $G.Overlay = ''; $G.Focus = 0 }
            return
        }
    }
    if ($G.View -eq 'plan' -and $G.Typed -cne $G.PlanDigest) {
        if ($k -eq 'bs') { if ($G.Typed.Length) { $G.Typed = $G.Typed.Substring(0, $G.Typed.Length - 1) }; return }
        if ($k -eq 'ctrl-u') { $G.Typed = ''; return }
        if ($k.Length -eq 1 -and $k -match '^[0-9a-fA-F]$') { if ($G.Typed.Length -lt 64) { $G.Typed += $k.ToLowerInvariant() }; return }
        if ($k -in ' ', '-') { return }
    }
    if ($G.View -eq 'form' -or $G.View -eq 'vmform') {
        $key = $G.FormKeys[$G.Sel]
        if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- }; return }
        if ($k -eq 'down') { if ($G.Sel -lt $G.FormKeys.Count - 1) { $G.Sel++ }; return }
        if ($k -eq 'bs') { $v = [string]$F[$key]; if ($v.Length) { $F[$key] = $v.Substring(0, $v.Length - 1) }; return }
        if ($k -eq 'ctrl-u') { $F[$key] = ''; return }
        if ($k.Length -eq 1 -and [int][char]$k -ge 32) { $F[$key] = [string]$F[$key] + $k; return }
    }
    if ($k -in 'l', 'L') { $G.Overlay = 'log'; $G.LogScroll = 0; return }
    if ($k -eq '?') { $G.Overlay = 'help'; return }
    if ($k -in 'q', 'Q', 'ctrl-c') { if ($G.View -eq 'done') { $G.Quit = $true } else { $G.Overlay = 'quit'; $G.Focus = 0 }; return }
    if ($G.View -in 'hv', 'vmkit', 'where', 'net', 'tsnote', 'vmform', 'vmstep', 'vmsum') { Invoke-VmKey $k; return }
    $nav = $k -in 'left', 'right', 'tab', 'btab'
    switch ($G.View) {
        'intro' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.Overlay = 'quit'; $G.Focus = 0 }
            elseif ($k -eq 'enter') {
                if ($G.Resume) { switch ($G.Focus) { 0 { Restore-Resume; Start-Guess } 1 { $G.Resume = ''; Start-Guess } 2 { $G.Overlay = 'quit'; $G.Focus = 1 } } }
                else { if ($G.Focus -eq 0) { Start-Guess } else { $G.Overlay = 'quit'; $G.Focus = 1 } }
            }
        }
        'checks' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- } } elseif ($k -eq 'down') { if ($G.Sel -lt $G.ItTag.Count - 1) { $G.Sel++ } }
            elseif ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'intro'; $G.Focus = 0 }
            elseif ($k -in 'f', 'F') { if ($G.Fixable.Count) { Start-Checks 'fix' } } elseif ($k -in 'r', 'R') { Start-Checks 'look' }
            elseif ($k -eq 'enter') {
                switch ($G.Btns[$G.Focus]) {
                    'Fix here' { Start-Checks 'fix' }
                    'Check again' { Start-Checks 'look' }
                    'Continue' {
                        Set-Stage 1; $G.Focus = 0; $G.StepNote[0] = Get-CheckNote; Get-Caps
                        if ($G.Recheck) { $G.Recheck = $false; $G.View = 'form'; $G.Sel = 0 }
                        elseif ($G.SkipParts) { $G.View = 'role'; $G.Sel = Get-RoleIndex $G.Role } else { Set-PartDefaults; $G.View = 'parts'; $G.Sel = 0 }
                    }
                }
            }
        }
        'role' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- } } elseif ($k -eq 'down') { if ($G.Sel -lt 2) { $G.Sel++ } }
            elseif ($k -in '1', '2', '3') { $G.Sel = [int]$k - 1 }
            elseif ($k -eq 'esc') { if ($G.SkipParts) { Set-Stage 0; $G.View = 'checks' } else { $G.View = 'parts' }; $G.Focus = 0; $G.Sel = 0 }
            elseif ($k -eq 'enter') { if ($G.Role -ne $RoleIds[$G.Sel]) { $G.Consent = $false; $G.Digest = '' }; $G.Role = $RoleIds[$G.Sel]; Set-Form; $G.View = 'form'; $G.Sel = 0; $G.Focus = 0 }
        }
        'form' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'esc') { $G.View = 'role'; $G.Sel = Get-RoleIndex $G.Role; $G.Focus = 0 }
            elseif ($k -eq 'enter') {
                if ($G.Focus -eq 1) { $G.View = 'role'; $G.Sel = Get-RoleIndex $G.Role; $G.Focus = 0; return }
                $err = Test-Form; if ($err) { $G.StatusMsg = $err; return }
                if ($G.Role -ne $G.CheckedRole) { $G.Recheck = $true; $G.CheckFor = $G.Role; Start-Checks 'look'; return }
                if ($G.Role -eq 'headend') { $G.StepState[2] = 'skipped' }
                if ($G.Role -eq 'workstation') { $G.StepState[4] = 'later' }
                if ($G.Role -eq 'headend') { Set-Stage 3 } else { Set-Stage 2 }
                $G.View = 'runintro'; $G.Focus = 0; $G.Scroll = 0
            }
        }
        'runintro' {
            if ($G.Role -eq 'forge' -and $k -in 'up', 'down', 'pgup', 'pgdn') {
                switch ($k) { 'up' { if ($G.Scroll -gt 0) { $G.Scroll-- } } 'down' { $G.Scroll++ } 'pgup' { $G.Scroll = [math]::Max(0, $G.Scroll - 8) } 'pgdn' { $G.Scroll += 8 } }
            } elseif ($nav) { Move-Button $k } elseif ($k -eq 'esc') { Set-Stage 1; $G.View = 'form'; $G.Sel = 0; $G.Focus = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1 -or $G.Role -eq 'forge') { Set-Stage 1; $G.View = 'form'; $G.Focus = 0 } else { Start-RoleRun } }
        }
        'plan' {
            if ($nav) { Move-Button $k } elseif ($k -eq 'up') { if ($G.Scroll -gt 0) { $G.Scroll-- } } elseif ($k -eq 'down') { $G.Scroll++ }
            elseif ($k -eq 'pgup') { $G.Scroll = [math]::Max(0, $G.Scroll - 8) } elseif ($k -eq 'pgdn') { $G.Scroll += 8 }
            elseif ($k -eq 'esc') { Set-Stage 1; $G.View = 'form'; $G.Sel = 0; $G.Focus = 0 }
            elseif ($k -eq 'enter') {
                if ($G.Typed -ceq $G.PlanDigest -and $G.Focus -eq 0) { $G.Digest = $G.Typed; $G.Consent = $true; Start-RoleRun }
                elseif ($G.Focus -eq 1) { Set-Stage 1; $G.View = 'form'; $G.Focus = 0 }
            }
        }
        'pair' {
            if ($nav) { Move-Button $k }
            elseif ($k -eq 'enter') { if ($G.Btns[$G.Focus] -eq 'Paired') { $G.Paired = $true; Complete-Wizard } else { $G.PairCode = ''; Start-RoleRun } }
        }
        'fail' {
            if ($nav) { Move-Button $k } elseif ($k -in 'r', 'R') { Invoke-Retry } elseif ($k -eq 'esc') { Exit-Failure }
            elseif ($k -eq 'enter') { switch ($G.Focus) { 0 { Invoke-Retry } 1 { Exit-Failure } 2 { $G.Overlay = 'log'; $G.LogScroll = 0 } } }
        }
        'parts' {
            if ($k -eq 'up') { if ($G.Sel -gt 0) { $G.Sel-- } } elseif ($k -eq 'down') { if ($G.Sel -lt 2) { $G.Sel++ } }
            elseif ($k -in 'left', 'right') { Step-Part $k } elseif ($k -in 'tab', 'btab') { Move-Button $k }
            elseif ($k -eq 'esc') { Set-Stage 0; $G.View = 'checks'; $G.Focus = 0; $G.Sel = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { Set-Stage 0; $G.View = 'checks'; $G.Focus = 0; $G.Sel = 0 } else { Complete-Parts } }
        }
        'done' {
            if ($k -eq 'up') { if ($G.Scroll -gt 0) { $G.Scroll-- } } elseif ($k -eq 'down') { $G.Scroll++ }
            elseif ($k -eq 'pgup') { $G.Scroll = [math]::Max(0, $G.Scroll - 8) } elseif ($k -eq 'pgdn') { $G.Scroll += 8 }
            elseif ($nav) { Move-Button $k }
            elseif ($k -in 'n', 'N') { if ($G.NextOffer) { Invoke-ContinueHere $G.NextOffer } }
            elseif ($k -in 's', 'S') { if (($G.Next -join "`n").Contains(' -SendDisc2')) { Invoke-SendLoop 'back' } }
            elseif ($k -eq 'enter') { if ($G.NextOffer -and $G.Focus -eq 0) { Invoke-ContinueHere $G.NextOffer } else { $G.Quit = $true } }
            elseif ($k -eq 'esc') { $G.Quit = $true }
        }
    }
}

function Invoke-Retry { if ($G.FailStage -eq 0) { $G.CheckFor = $G.CheckedRole; Start-Checks 'look' } elseif ($G.Role -eq 'headend') { Invoke-ConnectHandover } else { Start-RoleRun } }
function Exit-Failure {
    Set-Stage 1; $G.StepState[$G.FailStage] = 'pending'; $G.View = 'form'; $G.Sel = 0; $G.Focus = 0
    $G.Consent = $false; $G.Digest = ''; $G.Dl = [System.Collections.Generic.List[object]]::new()
}
function Start-Guess { Set-Stage 0; $G.View = 'checking'; $G.ItTag = New-List; $G.ItText = New-List; $G.Activity = 'Looking at this PC'; $G.CheckedRole = $G.Role; if (-not $G.CheckedRole) { $G.CheckedRole = 'forge' }; Start-Run 'guess' @('-GuessRole') }
function Restore-Resume {
    $G.Role = $G.Rs['role']
    foreach ($p in @(@('root', 'Root'), @('name', 'Name'), @('ws', 'Url'), @('agent', 'Agent'), @('tz', 'Tz'), @('user', 'User'), @('ct', 'Ct'))) { if ($G.Rs[$p[0]]) { $F[$p[1]] = $G.Rs[$p[0]] } }
}

# ---------------------------------------------------------------------------------------------------------------
# 12. Start, the loop, and the way out.

function Enable-Vt {  # VT output for the classic console (Windows Terminal has it on)
    if ([Console]::IsOutputRedirected) { return }
    if ($env:WT_SESSION) { return }
    try { if ($Host.UI.SupportsVirtualTerminal) { return } } catch { }
    try {
        if (-not ('Deep32Wizard.Native' -as [type])) {
            Add-Type -Namespace Deep32Wizard -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)] public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool GetConsoleMode(IntPtr h, out uint mode);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool SetConsoleMode(IntPtr h, uint mode);
'@
        }
        $h = [Deep32Wizard.Native]::GetStdHandle(-11); $m = [uint32]0
        if ([Deep32Wizard.Native]::GetConsoleMode($h, [ref]$m)) {
            $script:ConsoleMode = $m
            [void][Deep32Wizard.Native]::SetConsoleMode($h, ($m -bor 0x0004 -bor 0x0001))
        }
    } catch { }
}

function Restore-Vt {
    if ($null -eq $script:ConsoleMode) { return }
    try { $h = [Deep32Wizard.Native]::GetStdHandle(-11); [void][Deep32Wizard.Native]::SetConsoleMode($h, $script:ConsoleMode) } catch { }
}

function Enter-Screen {
    if ($script:RealTty) {
        try { [Console]::TreatControlCAsInput = $true } catch { }
        try { while ([Console]::KeyAvailable) { [void][Console]::ReadKey($true) } } catch { }  # keys typed or pasted before the screen opened
    }
    Write-Out "$E[?1049h$E[?25l$E[2J"
    $script:Full = $true; $script:Prev = $null; $G.Dirty = $true
}

function Exit-Screen {
    Write-Out "$E[0m$E[?2026l$E[?25h$E[?1049l"
    if ($script:RealTty) { try { [Console]::TreatControlCAsInput = $false } catch { } }
}

function Get-TerminalTheme {  # Windows Terminal answers OSC 11 with its background colour; a light one gets the light palette
    if (-not $env:WT_SESSION -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected) { return }
    try {
        [Console]::Out.Write("$E]11;?$E\"); [Console]::Out.Flush()
        $reply = [System.Text.StringBuilder]::new(); $until = (Get-NowMs) + 200
        while ((Get-NowMs) -lt $until) { if ([Console]::KeyAvailable) { [void]$reply.Append([Console]::ReadKey($true).KeyChar) } else { Start-Sleep -Milliseconds 10 } }
        if ($reply.ToString() -match 'rgb:([0-9a-fA-F]{2})[0-9a-fA-F]*/([0-9a-fA-F]{2})[0-9a-fA-F]*/([0-9a-fA-F]{2})') {
            $l = ([Convert]::ToInt32($Matches[1], 16) * 299 + [Convert]::ToInt32($Matches[2], 16) * 587 + [Convert]::ToInt32($Matches[3], 16) * 114) / 1000
            if ($l -gt 140) { $script:ThemeName = 'light' }
        }
    } catch { }
}

function Get-WizSize {
    if ($env:DEEP32_WIZARD_COLS) { if (-not $script:Sized) { $script:W = [int]$env:DEEP32_WIZARD_COLS; $script:H = 24; if ($env:DEEP32_WIZARD_ROWS) { $script:H = [int]$env:DEEP32_WIZARD_ROWS }; $script:Sized = $true }; return }
    try { $script:W = [Console]::WindowWidth; $script:H = [Console]::WindowHeight } catch { $script:W = 80; $script:H = 24 }
}

function Invoke-Wizard {
    Set-Glyphs
    $script:Depth = 'truecolor'; if ($env:DEEP32_WIZARD_COLOR) { $script:Depth = $env:DEEP32_WIZARD_COLOR }
    $script:ThemeName = 'dark'; if ($env:DEEP32_WIZARD_THEME) { $script:ThemeName = $env:DEEP32_WIZARD_THEME } elseif (-not $Theme) { Get-TerminalTheme }; if ($Theme) { $script:ThemeName = $Theme }
    Set-Palette
    Add-Prof 'palette'
    $script:Utf8 = [System.Text.UTF8Encoding]::new($false)
    $script:StdOut = [Console]::OpenStandardOutput()
    $script:VersionId = 'unknown'; try { $script:VersionId = ([IO.File]::ReadAllLines((Join-Path $WizRepo 'VERSION')))[0].Trim() } catch { }
    $base = $env:LOCALAPPDATA; if (-not $base) { $base = [IO.Path]::GetTempPath() }
    $script:StateDir = Join-Path $base 'Deep32-setup'
    if ($Demo) { $script:StateDir = Join-Path ([IO.Path]::GetTempPath()) ('deep32-wizard-demo-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)) }
    [void](New-Item -ItemType Directory -Force -Path $script:StateDir -ErrorAction SilentlyContinue)
    $script:LogFile = Join-Path $script:StateDir ('wizard-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    try { [IO.File]::WriteAllText($script:LogFile, '') } catch { $script:LogFile = '' }
    $script:WorkDir = $WizRepo
    $root = $DataRoot; if (-not $root) { $root = 'C:\Deep32' }
    $nm = $Name; if (-not $nm) { $nm = 'deep32-forge' }
    $tz = 'Etc/UTC'; if ($Demo) { $tz = 'Europe/London' }
    $url = $Workstation; if (-not $url -and $Demo) { $url = 'https://deep32-ws.tail4c2e.ts.net/mcp' }
    $ag = $Agent; if (-not $ag) { $ag = 'auto' }
    $ct = ''; if ($Demo) { $ct = 'yes' }
    $script:F = @{ Root = $root; Name = $nm; Tz = $tz; User = 'deep32'; Kb = 'us'; Profile = 'two-machines'; Ct = $ct; Hv = 'hyper-v'; Url = $url; Agent = $ag }
    Initialize-State
    Import-State
    if ($Decode) {
        $lines = [IO.File]::ReadAllLines($Decode)
        $stat = ''
        foreach ($l in $lines) { if ($l.StartsWith('{"deep32":"exit"')) { $ev = ConvertFrom-EventLine $l; $stat += ' ' + $ev.tool + ':' + $ev.status } }
        $ss = '1'; if ($env:DEEP32_WIZARD_SETUP_STATUS) { $ss = $env:DEEP32_WIZARD_SETUP_STATUS }
        $stat += " setup:$ss"
        foreach ($m in (Resolve-Failure $stat $lines)) { [Console]::Out.WriteLine($script:ErrRows[$m[0]].Kind + ' ' + $script:ErrRows[$m[0]].Key) }
        Remove-Item -LiteralPath $script:LogFile -ErrorAction SilentlyContinue
        return 0
    }
    $scripted = $null -ne $env:DEEP32_WIZARD_KEYS
    if ($scripted) { foreach ($t in $env:DEEP32_WIZARD_KEYS.Split(',')) { if ($t) { $G.Keys.Add($t) } } }
    $script:RealTty = -not $scripted -and -not [Console]::IsInputRedirected
    if ($VmGuide) { Start-VmGuide }
    $oldEnc = $null
    if (-not [Console]::IsOutputRedirected) { try { $oldEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = $script:Utf8 } catch { } }
    Enable-Vt
    Get-WizSize; Set-Layout
    try {
        Enter-Screen
        if ($SendDisc2) { Invoke-SendLoop 'direct'; $G.Quit = $true; $G.Dirty = $false }
        $lastAnim = 0
        while (-not $G.Quit) {
            if ($G.Proc) { Receive-RunOutput }
            if ($G.Quit) { break }
            Update-DlPoll; Update-VmGuide
            if ($G.View -in 'download', 'running') { Add-XferSample }
            $now = Get-NowMs
            if ($now - $lastAnim -ge 120) {
                $lastAnim = $now; $G.Tick++; if ($G.Tick % 4 -eq 0) { $G.Pulse = 1 - $G.Pulse }
                if ($G.View -in 'checking', 'running', 'download', 'install', 'pairwait', 'signin', 'pair') { $G.Dirty = $true }
                if ($G.View -eq 'vmstep' -and $G.Vs -and (Get-VmStep).State -in 'waiting', 'job') { $G.Dirty = $true }
                if (-not $env:DEEP32_WIZARD_COLS) { $ow = $W; $oh = $H; Get-WizSize; if ($ow -ne $W -or $oh -ne $H) { Set-Layout; $script:Full = $true; Write-Out "$E[2J"; $G.Dirty = $true } }
            }
            if ($G.Dirty) { Write-Frame }
            $k = if ($scripted) { Read-ScriptKey } else { Read-WizKey }
            if ($null -ne $k) { if ($k -ne 'ignore') { Invoke-Key $k } } else { Start-Sleep -Milliseconds 30 }
        }
        if ($G.Dirty) { Write-Frame }
    } finally {
        Stop-Run
        Exit-Screen
        if ($env:DEEP32_WIZARD_PROFILE) {
            try { $boot = [int]((Get-Date) - (Get-Process -Id $PID).StartTime).TotalMilliseconds - [int]$script:ProfClock.Elapsed.TotalMilliseconds; $script:Prof.Insert(0, "powershell-start-to-script $boot") } catch { }
            [IO.File]::WriteAllLines($env:DEEP32_WIZARD_PROFILE, $script:Prof)
        }
        Restore-Vt
        if ($null -ne $oldEnc) { try { [Console]::OutputEncoding = $oldEnc } catch { } }
        if ($script:LogFile) { [Console]::Out.WriteLine('The setup log is in ' + $script:LogFile) }
    }
    if ($G.PlainAfter) { $pass = Get-SetupArguments; & $WizSetup @pass; return $LASTEXITCODE }
    if ($G.ReachedDone) { return 0 }
    if ($G.RunStatus) { return $G.RunStatus }
    return 1
}

# ---------------------------------------------------------------------------------------------------------------
# 13. Send disc 2 to a Workstation (-SendDisc2; DISC2-NETWORK-DESIGN.md), the twin of section 13 in deep32-wizard.sh.
#     This PC serves its verified disc 2 with Tailscale Serve: HTTPS, tailnet only, this one file at
#     /d32-disc2-CODE/<file> on port 9447, where CODE is ten characters from A-Z2-7 that the person types on the
#     Workstation. The command is the one in tailscale.com/kb/1242 (flags before the target; a file target needs an
#     absolute path and an Administrator console, kb/1312). It runs without --bg, as this PowerShell's own child and
#     inside a Windows job that ends it when PowerShell ends in any way: kb/1242 keeps a --bg serve "until you disable
#     it", while this one ends with the wizard. The wizard then checks that Tailscale no longer lists the path, and
#     turns it off with kb/1242's `off` form if it still does. Before it says the code, the wizard asks its own tailnet
#     address for the file's headers: the whole size must come back.

$SendPort = 9447
$SendAlphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
$SendStepIds = @('disc2', 'tailscale', 'serve', 'probe', 'sending', 'done')
$SendStepNames = @('Disc 2 on this PC', 'Tailscale here', 'Start sending', 'Check the address', 'Workstation downloads', 'Stopped')
$SendDemoJson = '{"BackendState":"Running","TailscaleIPs":["100.101.102.103"],"Self":{"DNSName":"vmhost.tail4c2e.ts.net.","TailscaleIPs":["100.101.102.103","fd7a:115c:a1e0::1"]},"CertDomains":["vmhost.tail4c2e.ts.net"]}'

function Get-SendOs {  # linux, macos or windows (DEEP32_WIZARD_OS wins, as for the error table)
    switch ($env:DEEP32_WIZARD_OS) { 'linux' { return 'linux' } 'macos' { return 'macos' } }
    return 'windows'
}

function New-SendCode {  # ten symbols from A-Z2-7, one random byte each (32 divides 256: no bias)
    if ($Demo) { return 'R4NDQ7KX2M' }
    $bytes = [byte[]]::new(10)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); $rng.Dispose()
    return -join ($bytes | ForEach-Object { $SendAlphabet[$_ % 32] })
}

function Format-SendCode([string]$c) { return $c.Substring(0, 5) + '-' + $c.Substring(5, 5) }   # as the person reads and types it

function Read-SendStatus([string]$json) {  # from `tailscale status --json`, read as text the way the bash twin reads it:
    # State (BackendState), Name (this PC's MagicDNS name), Short (its first label, the name the Workstation lists),
    # Ip (its tailnet IPv4) and Cert (the tailnet has HTTPS certificates on)
    $r = @{ State = ''; Name = ''; Short = ''; Ip = ''; Cert = $false }
    if ($json -cmatch '"BackendState"\s*:\s*"([A-Za-z]*)"') { $r.State = $Matches[1] }
    $self = ''; $at = $json.IndexOf('"Self"')
    if ($at -ge 0) { $self = $json.Substring($at + 6); $end = $self.IndexOf('"Peer"'); if ($end -ge 0) { $self = $self.Substring(0, $end) } }
    if ($self -cmatch '"DNSName"\s*:\s*"([^"]*)"') { $n = $Matches[1]; if ($n.EndsWith('.')) { $n = $n.Substring(0, $n.Length - 1) }; $r.Name = $n }
    if ($self -cmatch '"TailscaleIPs"\s*:\s*\[([^\]]*)\]') { if ($Matches[1] -cmatch '"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})"') { $r.Ip = $Matches[1] } }
    if ($json -cmatch '"CertDomains"\s*:\s*\[\s*"') { $r.Cert = $true }
    $r.Short = $r.Name.Split('.')[0]
    return $r
}

function Test-SendBusy([string]$json) {  # what uses port 9447 in `tailscale serve status --json`, or ''
    if (-not ($json.Contains('"' + $SendPort + '"') -or $json.Contains(':' + $SendPort + '"'))) { return '' }
    if ($json.Contains('/d32-disc2-')) { return "Tailscale already serves a disc 2 on port $SendPort, from another Send disc 2" }
    return "Tailscale already serves something else on port $SendPort"
}

function Get-SendClass([string]$text) {  # the code for what `tailscale serve` printed when it stopped
    $t = $text.ToLowerInvariant()
    foreach ($p in 'must be root', 'operator', 'access denied', 'serve config denied', 'local admin', 'administrator', 'permission denied') { if ($t.Contains($p)) { return 'send-needs-root' } }
    foreach ($p in 'already in use', 'address in use', 'already serving', 'port is in use') { if ($t.Contains($p)) { return 'send-port-busy' } }
    return 'send-serve-refused'
}

function Test-SendHead([string]$text, [long]$size) {  # @(result, said): 0 the whole file came back (200 with its length,
    # or 206 with its total), 1 a server answered with something else, 2 nothing usable answered yet
    $code = ''; $len = ''; $tot = ''; $last = ''
    foreach ($raw in $text.Split("`n")) {
        $x = $raw.TrimEnd("`r"); if ($x.Trim()) { $last = $x }; $x = $x.ToLowerInvariant()
        if ($x -cmatch '^http/[0-9.]+ ([0-9]{3})') { $code = $Matches[1]; $len = ''; $tot = '' }
        elseif ($x -cmatch '^content-length:\s*([0-9]+)') { $len = $Matches[1] }
        elseif ($x -cmatch '^content-range:\s*bytes [0-9]+-[0-9]+/([0-9]+)') { $tot = $Matches[1] }
    }
    if (-not $code) { if (-not $last) { $last = 'no answer' }; return @(2, $last) }
    if ($code -eq '200') {
        $shown = $len; if (-not $shown) { $shown = 'none' }; $said = "HTTP 200 with Content-Length $shown; disc 2 has $size bytes"
        if ($len -eq [string]$size) { return @(0, $said) }; return @(1, $said)
    }
    if ($code -eq '206') {
        $shown = $tot; if (-not $shown) { $shown = 'an unknown number of' }; $said = "HTTP 206 for $shown bytes in all; disc 2 has $size bytes"
        if ($tot -eq [string]$size) { return @(0, $said) }; return @(1, $said)
    }
    return @(2, "HTTP $code")
}

function Find-SendRow([string]$code) {  # the error table row for one of the send's codes, or -1
    Import-ErrorTable
    for ($j = 0; $j -lt $script:ErrRows.Count; $j++) { if ($script:ErrRows[$j].Kind -eq 'code' -and $script:ErrRows[$j].Key -eq $code) { return $j } }
    return -1
}

function Invoke-SendTool([string]$exe, [string[]]$argv) {  # run a tool to its end (30 s at most): both its streams as one string
    try {
        $psi = [System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName = $exe; $psi.Arguments = ConvertTo-ArgText $argv
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false); $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
        $p = [System.Diagnostics.Process]::Start($psi); $p.StandardInput.Close()
        $o = $p.StandardOutput.ReadToEndAsync(); $e2 = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit(30000)) { try { & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null } catch { } }
        $text = $o.Result + $e2.Result; $p.Dispose()
        return $text
    } catch { return '' }
}

function Find-SendDisc {  # the verified disc 2 in this PC's download folder (deep32-get publishes it only once it is
    # verified): SendFile, SendDir, SendIso and SendSize, or $false and SendWhy when it is not there
    $G.SendFile = 'deep32-' + $script:VersionId + '-disc2-content.iso'; $G.SendWhy = ''; $G.SendSize = [long]0
    $G.SendDir = Join-Path (Split-Path -Parent $WizRepo) 'deep32-downloads'
    if ($Demo) { $G.SendDir = 'C:\Users\you\deep32-downloads'; $G.SendIso = Join-Path $G.SendDir $G.SendFile; $G.SendSize = [long]19638284288; return $true }
    $G.SendIso = Join-Path $G.SendDir $G.SendFile
    $item = Get-Item -LiteralPath $G.SendIso -Force -ErrorAction SilentlyContinue
    if (-not $item -or $item.PSIsContainer) {
        if (Test-Path -LiteralPath ($G.SendIso + '.download')) { $G.SendWhy = "disc 2 is not finished in $(Get-ShortPath $G.SendDir): only its partial download is there" }
        else { $G.SendWhy = "no $($G.SendFile) in $(Get-ShortPath $G.SendDir)" }
        return $false
    }
    $G.SendSize = [long]$item.Length
    $best = Get-ChildItem -LiteralPath $G.SendDir -Filter '.deep32-*' -Directory -Force -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter 'revision-*' -Directory -ErrorAction SilentlyContinue } |
        ForEach-Object { Join-Path $_.FullName 'MIRRORS.json' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1
    if ($best) {
        foreach ($row in (Read-MirrorFiles $best 'disc2' '')) {
            if ($row[0] -eq $G.SendFile -and [long]$row[1] -ne $G.SendSize) { $G.SendWhy = "$($G.SendFile) has $($G.SendSize) bytes; the signed release says $($row[1])"; return $false }
        }
    }
    if ($G.SendSize -le 0) { $G.SendWhy = "$($G.SendFile) is empty"; return $false }
    return $true
}

function Test-SendAdmin {  # $true when Tailscale lets this session serve a file (kb/1312: an Administrator console on
    # Windows, root on Linux); SendWho says why (empty when only Tailscale can tell), SendWhy why not
    $G.SendWhy = ''; $G.SendWho = 'Runs as administrator, so Tailscale may serve the file'
    if ((Get-SendOs) -ne 'windows') { $G.SendWho = 'Runs as root, so Tailscale may serve the file' }
    if ($Demo -or $env:DEEP32_WIZARD_TEST_ADMIN -eq '1') { return $true }
    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { $G.SendWho = ''; return $true }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { return $true }
    $G.SendWhy = 'this PowerShell does not run as administrator'
    return $false
}

function Test-SendPort {  # what takes port 9447 here (a Tailscale serve or a listening program), or ''
    $why = Test-SendBusy (Invoke-SendTool $G.SendTs @('serve', 'status', '--json'))
    if ($why) { return $why }
    if ((Get-SendOs) -eq 'windows') {
        try { foreach ($ep in [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()) { if ($ep.Port -eq $SendPort) { return "a program on this PC listens on port $SendPort" } } } catch { }
    }
    return ''
}

function Add-SendJob($proc) {  # tie the serve to this PowerShell: when PowerShell ends in any way, Windows ends the serve
    try {
        if (-not ('Deep32Wizard.SendJob' -as [type])) {
            Add-Type -Namespace Deep32Wizard -Name SendJob -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern IntPtr CreateJobObject(IntPtr attributes, string name);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);
[DllImport("kernel32.dll", SetLastError = true)] public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
'@
        }
        if (-not $script:SendJob) {
            $job = [Deep32Wizard.SendJob]::CreateJobObject([IntPtr]::Zero, $null)
            $size = 144; if ([IntPtr]::Size -eq 4) { $size = 112 }   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            $mem = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)
            try {
                for ($b = 0; $b -lt $size; $b++) { [Runtime.InteropServices.Marshal]::WriteByte($mem, $b, 0) }
                [Runtime.InteropServices.Marshal]::WriteInt32($mem, 16, 0x2000)   # LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if ([Deep32Wizard.SendJob]::SetInformationJobObject($job, 9, $mem, [uint32]$size)) { $script:SendJob = $job }
            } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($mem) }
        }
        if ($script:SendJob) { [void][Deep32Wizard.SendJob]::AssignProcessToJobObject($script:SendJob, $proc.Handle) }
    } catch { }
}

function Start-SendServe {  # make the code and start the serve as this PowerShell's own child
    $G.SendCode = New-SendCode; $G.SendShown = Format-SendCode $G.SendCode
    $G.SendPath = '/d32-disc2-' + $G.SendCode + '/' + $G.SendFile; $G.SendUrl = 'https://' + $G.SendName + ':' + $SendPort + $G.SendPath
    $G.SendLive = $true; $G.SendStartError = ''
    if ($Demo) { return }
    $argv = @('serve', '--yes', "--https=$SendPort", "--set-path=$($G.SendPath)", $G.SendIso)
    Write-WizLog ('--- ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' tailscale ' + ($argv -join ' '))
    $psi = [System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName = $G.SendTs; $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false   # no CreateNoWindow: the serve shares this console, so closing the window ends it too
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false); $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    try {
        $G.SendProc = [System.Diagnostics.Process]::Start($psi)
        $G.SendProc.StandardInput.Close()
        Add-SendJob $G.SendProc
        $G.SendOutTask = $G.SendProc.StandardOutput.ReadLineAsync(); $G.SendErrTask = $G.SendProc.StandardError.ReadLineAsync()
    } catch { $G.SendProc = $null; $G.SendStartError = $_.Exception.Message }
}

function Update-SendOutput([bool]$final = $false) {  # collect what the serve printed so far (all of it once it has ended)
    foreach ($s in 'Out', 'Err') {
        for ($n = 0; $n -lt 200; $n++) {
            $task = $G['Send' + $s + 'Task']; if (-not $task) { break }
            if (-not $task.IsCompleted) { if (-not $final -or -not $task.Wait(2000)) { break } }
            $line = $task.Result
            if ($null -eq $line) { $G['Send' + $s + 'Task'] = $null; break }
            $G.SendLines.Add($line); if ($G.SendLines.Count -gt 50) { $G.SendLines.RemoveAt(0) }
            if ($s -eq 'Out') { $G.SendOutTask = $G.SendProc.StandardOutput.ReadLineAsync() } else { $G.SendErrTask = $G.SendProc.StandardError.ReadLineAsync() }
        }
    }
}

function Get-SendServeLines {  # the last three lines the serve printed that say something
    if ($G.SendProc) { Update-SendOutput ($G.SendProc.HasExited) }
    $all = @($G.SendLines | ForEach-Object { $_.TrimEnd("`r") } | Where-Object { $_.Trim() })
    if ($all.Count -gt 3) { $all = $all[($all.Count - 3)..($all.Count - 1)] }
    return , $all
}

function Start-SendProbe {  # ask this PC's own tailnet address for the file's headers, in the background
    $argv = @('-sS', '-I', '--max-time', '20', '--ssl-no-revoke')
    if ($G.SendIp) { $argv += @('--resolve', ($G.SendName + ':' + $SendPort + ':' + $G.SendIp)) }
    $argv += $G.SendUrl
    $psi = [System.Diagnostics.ProcessStartInfo]::new(); $psi.FileName = $G.SendCurl; $psi.Arguments = ConvertTo-ArgText $argv
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    try {
        $G.SendProbe = [System.Diagnostics.Process]::Start($psi); $G.SendProbe.StandardInput.Close()
        $G.SendProbeOut = $G.SendProbe.StandardOutput.ReadToEndAsync(); $G.SendProbeErr = $G.SendProbe.StandardError.ReadToEndAsync()
    } catch { $G.SendProbe = $null; $G.SendLast = $_.Exception.Message; $G.SendNext = (Get-NowMs) + 2000 }
}

function Clear-SendPath {  # after the serve ended: Tailscale must no longer list this send's path; if it does, turn it off
    if (-not $G.SendLive) { return }
    $G.SendLive = $false; $G.SendLeft = $false
    if ($Demo) { return }
    for ($i = 0; $i -lt 3; $i++) {
        if (-not (Invoke-SendTool $G.SendTs @('serve', 'status', '--json')).Contains($G.SendPath)) { return }
        Start-Sleep -Milliseconds 500
    }
    Write-WizLog ('--- send disc 2: Tailscale still lists ' + $G.SendPath + '; turning it off')
    [void](Invoke-SendTool $G.SendTs @('serve', '--yes', "--https=$SendPort", "--set-path=$($G.SendPath)", 'off'))
    if ((Invoke-SendTool $G.SendTs @('serve', 'status', '--json')).Contains($G.SendPath)) { $G.SendLeft = $true }
}

function Stop-Send {  # end the self-check and the serve, then make sure Tailscale no longer lists the path (safe to repeat)
    if ($G.SendProbe) { try { if (-not $G.SendProbe.HasExited) { & taskkill.exe /PID $G.SendProbe.Id /T /F 2>$null | Out-Null } } catch { }; $G.SendProbe = $null }
    if ($G.SendProc) {
        try { if (-not $G.SendProc.HasExited) { & taskkill.exe /PID $G.SendProc.Id /T /F 2>$null | Out-Null; [void]$G.SendProc.WaitForExit(5000) } } catch { }
        $G.SendProc = $null; $G.SendOutTask = $null; $G.SendErrTask = $null
        Write-WizLog '--- send disc 2: the serve ended'
    }
    Clear-SendPath
}

function Add-SendItem([string]$tag, [string]$text) {  # one line of the send's progress (printed with its tag in plain mode)
    $G.SendTag.Add($tag); $G.SendText.Add($text); Write-WizLog "[$tag] $text"
    if ($G.SendPlain) { [Console]::Out.WriteLine("[$tag] $text") }
    $G.Dirty = $true
}

function Set-SendFail([string]$code, [string[]]$lines = @()) {  # stop everything this send started and say why, from the error table
    Stop-Send
    $G.SendPhase = 'failed'; $G.SendFailCode = $code; $G.SendFailLines = @($lines); $G.SendRow = Find-SendRow $code
    $G.StepState[$G.Stage] = 'failed'; $G.View = 'sendfail'; $G.Focus = 0; $G.Dirty = $true
    foreach ($l in $lines) { Write-WizLog $l }
    Write-WizLog "[fail] Send disc 2 stopped (code: $code)"
    if (-not $G.SendPlain) { return }
    foreach ($l in $lines) { [Console]::Out.WriteLine('[info] ' + $l) }
    if ($G.SendRow -ge 0) {
        [Console]::Out.WriteLine(('[fail] {0} (code: {1})' -f $script:ErrRows[$G.SendRow].Mean, $code))
        foreach ($s in (Get-FixSteps $G.SendRow)) { [Console]::Out.WriteLine('[you] ' + $s.Replace('`', '')) }
    } else { [Console]::Out.WriteLine("[fail] Send disc 2 stopped (code: $code)") }
}

function Stop-SendDied {  # the serve ended by itself: say what Tailscale printed
    $lines = Get-SendServeLines
    if (-not $lines.Count) { $lines = @('tailscale serve ended without a message') }
    Set-SendFail (Get-SendClass ($lines -join ' ')) $lines
}

function Show-SendReady {  # the self-check passed: show the code and what to do on the Workstation
    $G.SendPhase = 'sending'; Set-Stage 4; $G.StepNote[4] = 'code ' + $G.SendShown; $G.View = 'sending'; $G.Focus = 0
    $G.SendSince = Get-NowMs
    if ($G.SendHow -eq 'direct') { $G.ReachedDone = $true }
    $say = "On the Workstation, choose Get Disc 2 over the Network, pick $($G.SendShort), type $($G.SendShown)"
    Write-WizLog "[you] $say"
    if ($G.SendPlain) { [Console]::Out.WriteLine("[you] $say"); [Console]::Out.WriteLine('[info] Sending; press Enter to stop (Ctrl+C also stops it and turns the serve off)') }
}

function Step-SendCheck {  # the checks, one per step of the loop, so each shows as it passes
    switch ($G.SendK) {
        0 {
            Set-Stage 0
            if (-not (Find-SendDisc)) { Set-SendFail 'send-no-disc2' @($G.SendWhy); return }
            $G.StepNote[0] = Format-Bytes $G.SendSize
            Add-SendItem 'ok' ('Disc 2 is here: ' + $G.SendFile + ', ' + (Format-Bytes $G.SendSize)); break
        }
        1 {
            Set-Stage 1
            if ($Demo) { $js = $SendDemoJson }
            else {
                $cmd = Get-Command tailscale -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                if (-not $cmd) { Set-SendFail 'send-no-tailscale' @('tailscale: command not found'); return }
                $G.SendTs = $cmd.Source
                $js = Invoke-SendTool $G.SendTs @('status', '--json')
            }
            $sts = Read-SendStatus $js
            $G.SendState = $sts.State; $G.SendName = $sts.Name; $G.SendShort = $sts.Short; $G.SendIp = $sts.Ip; $G.SendCert = $sts.Cert
            if (-not $Demo -and $G.SendState -ne 'Running') {
                $said = $G.SendState; if (-not $said) { $said = ($js -split "`n")[0].TrimEnd("`r") }
                Set-SendFail 'send-signed-out' @("Tailscale says: $said"); return
            }
            $G.StepNote[1] = $G.SendShort
            Add-SendItem 'ok' "Tailscale is signed in here as $($G.SendShort) ($($G.SendName))"; break
        }
        2 {
            if (-not $G.SendName -or -not $G.SendCert) { Set-SendFail 'send-serve-refused' @('tailscale status lists no HTTPS certificate domain: MagicDNS or HTTPS certificates are off'); return }
            Add-SendItem 'ok' 'HTTPS certificates are on in your tailnet'; break
        }
        3 {
            if (-not (Test-SendAdmin)) { Set-SendFail 'send-needs-root' @($G.SendWhy); return }
            if ($G.SendWho) { Add-SendItem 'ok' $G.SendWho }; break
        }
        4 {
            Set-Stage 2
            if (-not $Demo) { $why = Test-SendPort; if ($why) { Set-SendFail 'send-port-busy' @($why); return } }
            $G.StepNote[2] = "port $SendPort"
            Add-SendItem 'ok' "Port $SendPort is free here"; break
        }
        5 {
            Start-SendServe
            if (-not $Demo -and -not $G.SendProc) { Set-SendFail 'send-serve-refused' @('tailscale serve did not start: ' + $G.SendStartError); return }
            Add-SendItem 'info' "Sending with Tailscale Serve on port $SendPort, tailnet only"
            Set-Stage 3; $G.SendPhase = 'probe'; $G.SendLast = 'no answer yet'
            $now = Get-NowMs; $G.SendUntil = $now + 90000; $G.SendNext = $now + 700; break
        }
    }
    $G.SendK++
}

function Step-SendProbe {  # the self-check: try the own address until the whole file answers, or give up after 90 s
    $sz = Format-Bytes $G.SendSize
    if ($Demo) { Add-SendItem 'ok' "This machine answers at its own address with the whole disc 2 ($sz)"; Show-SendReady; return }
    if ($G.SendProc -and $G.SendProc.HasExited) { Stop-SendDied; return }
    $now = Get-NowMs
    if ($G.SendProbe) {
        if (-not $G.SendProbe.HasExited) { return }
        $text = $G.SendProbeOut.Result + $G.SendProbeErr.Result; $G.SendProbe.Dispose(); $G.SendProbe = $null
        $res = Test-SendHead $text $G.SendSize
        if ($res[0] -eq 0) { Add-SendItem 'ok' "This machine answers at its own address with the whole disc 2 ($sz)"; Show-SendReady; return }
        if ($res[0] -eq 1) { Set-SendFail 'send-self-check' @($G.SendUrl, $res[1]); return }
        $G.SendLast = $res[1]
        if ($now -ge $G.SendUntil) {
            $lines = Get-SendServeLines; $joined = ($lines -join ' ').ToLowerInvariant()
            if ($joined.Contains('login.tailscale.com') -or $joined.Contains('not enabled') -or $joined.Contains('to enable')) { Set-SendFail 'send-serve-refused' $lines }
            else { Set-SendFail 'send-self-check' @($G.SendUrl, ('no whole answer in 90 s; the last: ' + $G.SendLast)) }
            return
        }
        $G.SendNext = $now + 2000; return
    }
    if ($now -ge $G.SendNext) {
        $cmd = Get-Command curl -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cmd) { Set-SendFail 'send-self-check' @('curl is not installed here; the self-check needs it'); return }
        $G.SendCurl = $cmd.Source; Start-SendProbe
    }
}

function Step-Send {  # one step of the send: the checks in turn, the self-check, then the watch while it sends
    if ($G.SendPhase -notin 'checks', 'probe', 'sending') { return }
    $now = Get-NowMs
    if ($now -lt $G.SendDue) { return }
    if ($Demo) { $G.SendDue = $now + 450 }
    switch ($G.SendPhase) {
        'checks' { Step-SendCheck; break }
        'probe' { Step-SendProbe; break }
        'sending' { if ($G.SendProc) { Update-SendOutput; if ($G.SendProc.HasExited) { Stop-SendDied } }; break }
    }
    $G.Dirty = $true
}

function Start-Send {  # start (or start again) from the first check
    Stop-Send
    $G.SendTag = New-List; $G.SendText = New-List; $G.SendK = 0; $G.SendPhase = 'checks'; $G.SendDue = 0; $G.SendFailCode = ''; $G.SendFailLines = @(); $G.SendRow = -1
    $G.SendCode = ''; $G.SendShown = ''; $G.SendPath = ''; $G.SendLeft = $false; $G.SendLive = $false; $G.SendLines = New-List
    $G.StepState = @('current', 'pending', 'pending', 'pending', 'pending', 'pending'); $G.StepNote = @('', '', '', '', '', ''); $G.Stage = 0
    $G.View = 'send'; $G.Focus = 0; $G.Dirty = $true
    Write-WizLog ('--- ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' send disc 2')
}

function Complete-Send {  # Enter while sending: stop, and say that the serve is off
    Stop-Send
    $G.SendPhase = 'stopped'; Set-Stage 5; $G.View = 'sent'; $G.Focus = 0
    if ($G.SendLeft) { Add-SendItem 'fail' ("Tailscale still lists the path; turn it off: ``tailscale serve --https=$SendPort --set-path=$($G.SendPath) off``") }
    else { Add-SendItem 'ok' 'Sending stopped; the serve is off and the code no longer works' }
}

function Invoke-SendPlain {  # -SendDisc2 without the full screen: the same steps as tagged lines; Enter (or Ctrl+C) stops
    Initialize-State; $script:LogFile = ''; $script:RealTty = $false
    $script:VersionId = 'unknown'; try { $script:VersionId = ([IO.File]::ReadAllLines((Join-Path $WizRepo 'VERSION')))[0].Trim() } catch { }
    Import-ErrorTable
    $G.SendPlain = $true; $G.SendHow = 'plain'
    try {
        [Console]::Out.WriteLine('[info] Send disc 2 to a Workstation: this machine shares its verified disc 2 over your tailnet while this runs')
        Start-Send
        while ($G.SendPhase -in 'checks', 'probe') { Step-Send; Start-Sleep -Milliseconds 50 }
        if ($G.SendPhase -ne 'sending') { return 1 }
        $task = $null
        if ([Console]::IsInputRedirected) { $task = [IO.StreamReader]::new([Console]::OpenStandardInput()).ReadLineAsync() }
        while ($G.SendPhase -eq 'sending') {
            if ($task) {
                if ($task.IsCompleted) {
                    if ($null -ne $task.Result -or $Demo) { break }
                    $task = $null   # the input ended: send until Ctrl+C or the end of this process
                }
            } elseif (-not [Console]::IsInputRedirected) {
                $stop = $false
                while ([Console]::KeyAvailable) { if ([Console]::ReadKey($true).Key -eq 'Enter') { $stop = $true } }
                if ($stop) { break }
            }
            Start-Sleep -Milliseconds 200
            Step-Send
        }
        if ($G.SendPhase -ne 'sending') { return 1 }
        Complete-Send
        if ($G.SendLeft) { return 1 }
        return 0
    } finally { Stop-Send }
}

# The full screen: the wizard's own header, rail, footer and dialogs, with the send's six steps on the rail and its own
# loop, so it opens from the command line (-SendDisc2) or over the Done screen (S) and goes back there.

function Invoke-SendLoop([string]$how) {  # direct or back
    $keep = @{ Ids = $script:StepIds; Names = $script:StepNames; State = $G.StepState; Note = $G.StepNote; Stage = $G.Stage; View = $G.View
        Focus = $G.Focus; Scroll = $G.Scroll; Pair = $G.PairCode }
    $script:StepIds = $SendStepIds; $script:StepNames = $SendStepNames; $G.PairCode = ''
    $G.SendHow = $how; $G.SendPlain = $false; $G.SendDone = $false; $G.Overlay = ''; $G.StatusMsg = ''
    $scripted = $null -ne $env:DEEP32_WIZARD_KEYS
    try {
        Start-Send
        $lastAnim = 0
        while (-not $G.Quit -and -not $G.SendDone) {
            Step-Send
            $now = Get-NowMs
            if ($now - $lastAnim -ge 120) {
                $lastAnim = $now; $G.Tick++; if ($G.Tick % 4 -eq 0) { $G.Pulse = 1 - $G.Pulse }
                if ($G.SendPhase -in 'checks', 'probe', 'sending') { $G.Dirty = $true }
                if (-not $env:DEEP32_WIZARD_COLS) { $ow = $W; $oh = $H; Get-WizSize; if ($ow -ne $W -or $oh -ne $H) { Set-Layout; $script:Full = $true; Write-Out "$E[2J"; $G.Dirty = $true } }
            }
            if ($G.Dirty) { Write-SendFrame }
            $k = if ($scripted) { Read-ScriptKey } else { Read-WizKey }
            if ($null -ne $k) { if ($k -ne 'ignore') { Invoke-SendKey $k } } else { Start-Sleep -Milliseconds 30 }
        }
    } finally {
        Stop-Send
        $script:StepIds = $keep.Ids; $script:StepNames = $keep.Names; $G.StepState = $keep.State; $G.StepNote = $keep.Note; $G.Stage = $keep.Stage
        $G.View = $keep.View; $G.Focus = $keep.Focus; $G.Scroll = $keep.Scroll; $G.PairCode = $keep.Pair; $G.Overlay = ''; $G.StatusMsg = ''
        $script:Full = $true; $G.Dirty = $true
    }
}

function Exit-Send { if ($G.SendHow -eq 'direct') { $G.Quit = $true } else { $G.SendDone = $true } }

function Invoke-SendKey([string]$k) {  # one key on the send screen
    $G.Dirty = $true; $G.StatusMsg = ''
    if ($W -lt 80 -or $H -lt 24) { if ($k -in 'q', 'Q', 'ctrl-c') { $G.Quit = $true }; return }
    switch ($G.Overlay) {
        'log' {
            switch ($k) { 'up' { $G.LogScroll++ } 'down' { if ($G.LogScroll -gt 0) { $G.LogScroll-- } } 'pgup' { $G.LogScroll += $CH - 5 } 'pgdn' { $G.LogScroll = [math]::Max(0, $G.LogScroll - $CH + 5) }
                'home' { $G.LogScroll = $G.Log.Count } 'end' { $G.LogScroll = 0 } default { if ($k -in 'esc', 'l', 'L', 'q', 'Q', 'enter') { $G.Overlay = '' } } }
            return
        }
        'help' { $G.Overlay = ''; return }
        'quit' {
            if ($k -in 'left', 'right', 'tab', 'btab') { Move-Button $k } elseif ($k -eq 'esc') { $G.Overlay = ''; $G.Focus = 0 }
            elseif ($k -eq 'enter') { if ($G.Focus -eq 1) { $G.Quit = $true } else { $G.Overlay = ''; $G.Focus = 0 } } elseif ($k -in 'q', 'Q') { $G.Quit = $true }
            return
        }
    }
    if ($k -in 'l', 'L') { $G.Overlay = 'log'; $G.LogScroll = 0; return }
    if ($k -eq '?') { $G.Overlay = 'help'; return }
    if ($k -in 'q', 'Q', 'ctrl-c') { if ($G.SendPhase -eq 'stopped') { $G.Quit = $true } else { $G.Overlay = 'quit'; $G.Focus = 0 }; return }
    if ($G.SendPhase -eq 'sending') { if ($k -eq 'enter') { Complete-Send } elseif ($k -eq 'esc') { $G.StatusMsg = 'Enter stops sending; Q quits.' }; return }
    if ($G.SendPhase -in 'stopped', 'failed') {
        if ($k -in 'left', 'right', 'tab', 'btab') { Move-Button $k } elseif ($k -in 'r', 'R') { Start-Send } elseif ($k -eq 'esc') { Exit-Send }
        elseif ($k -eq 'enter') {
            $label = $G.Btns[$G.Focus]
            if ($label -in 'Send again', 'Try again') { Start-Send } elseif ($label -in 'Close', 'Back') { Exit-Send } elseif ($label -eq 'Show the log') { $G.Overlay = 'log'; $G.LogScroll = 0 }
        }
    }
}

function Write-SendFrame {  # write the lines that changed, as Write-Frame does, with the send screen in the panel
    Build-SendFrame
    $sb = [System.Text.StringBuilder]::new()
    for ($i = 0; $i -lt $H; $i++) {
        if (-not $script:Full -and $script:Prev -and $i -lt $Prev.Count -and $Frame[$i] -ceq $Prev[$i]) { continue }
        [void]$sb.Append("$E[$($i + 1);1H" + $Frame[$i])
    }
    $script:Prev = $Frame; $script:Full = $false
    if ($G.Snap) { [void]$sb.Append("$E]7777;snap;$($G.Snap)" + [char]7); $G.Snap = '' }
    if ($sb.Length) { if ($env:WT_SESSION -or $env:TERM_PROGRAM) { Write-Out ("$E[?2026h" + $sb.ToString() + "$E[?2026l") } else { Write-Out $sb.ToString() } }
    $G.Dirty = $false
}

function Build-SendFrame {  # the wizard's frame (header, rail, footer, log and help) with the send screen in the panel
    Build-Frame
    if ($W -lt 80 -or $H -lt 24) { Build-SendSmall; return }
    if ($G.Overlay -in 'log', 'help') { return }
    Build-SendFoot; $Frame[$H - 1] = $script:Foot
    $script:Panel = [System.Collections.Generic.List[string]]::new()
    View-Send
    if ($G.Overlay -eq 'quit') { View-SendQuit }
    PFill
    for ($i = 0; $i -lt $CH; $i++) { $Frame[$BoxTop + 1 + $i] = '  ' + $St.BORDER + $Gl.V + $St['0'] + $Rail[$i] + $St.BORDER + $Gl.V + $St['0'] + $Panel[$i] + $St.BORDER + $Gl.V + $St['0'] + '  ' }
}

function Build-SendSmall {  # the small-window note says what Q does here
    $msg = 'Press Q to quit.'
    if ($G.SendPhase -in 'checks', 'probe', 'sending') { $msg = 'Sending goes on. Make the window bigger to see it, or press Q to stop and quit.' }
    $r = [math]::Max(0, [math]::Floor($H / 2) - 2); if ($r + 3 -ge $H) { return }
    $t = $msg; if ($t.Length -gt $W) { $t = $t.Substring(0, $W) }
    $pad = [math]::Max(0, [math]::Floor(($W - $t.Length) / 2))
    $Frame[$r + 3] = (' ' * $pad) + $St.MUTED + $t + $St['0'] + (' ' * ($W - $pad - $t.Length))
}

function Build-SendFoot {  # the keys of the send screen, into Foot
    $back = 'back'; if ($G.SendHow -eq 'direct') { $back = 'close' }
    $keys = @('L', 'log', '?', 'help', 'Q', 'quit')
    if ($G.SendPhase -eq 'sending') { $keys = @('Enter', 'stop sending', 'L', 'log', 'Q', 'quit') }
    elseif ($G.SendPhase -in 'stopped', 'failed') { $keys = @('Left/Right', 'choose', 'Enter', 'confirm', 'Esc', $back, 'L', 'log', 'Q', 'quit') }
    $sb = [System.Text.StringBuilder]::new(); [void]$sb.Append('   '); $vis = 3
    for ($i = 0; $i -lt $keys.Count; $i += 2) {
        [void]$sb.Append($St.B + $keys[$i] + $St['0'] + ' ' + $St.MUTED + $keys[$i + 1] + $St['0'] + '    '); $vis += $keys[$i].Length + $keys[$i + 1].Length + 5
    }
    if ($vis -gt $W) { $sb = [System.Text.StringBuilder]::new(); [void]$sb.Append('   '); $vis = 3 }
    $script:Foot = $sb.ToString() + (' ' * ($W - $vis))
}

function View-Send {  # the checks as they pass; then the code and what to do on the Workstation
    if ($G.SendPhase -eq 'failed') { View-SendFail; return }
    if ($G.SendPhase -eq 'sending') { View-SendCode; return }
    if ($G.SendPhase -eq 'stopped') { PTitle 'Sending stopped' 'The serve is off and the code no longer works. Disc 2 stays in the download folder; send it again whenever you like.' $Gl.OK $St.OK }
    else { PTitle 'Send disc 2 to a Workstation' 'This machine shares its verified disc 2 with your Workstation over your tailnet while this screen stays open.' }
    $rows = $CH - $Panel.Count - 4; if ($G.SendPhase -eq 'probe') { $rows -= 2 }
    $headLines = $script:Panel; $script:Panel = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $G.SendTag.Count; $i++) {   # each item wrapped; the newest stay when they do not all fit
        $ic = Get-ItemIcon $G.SendTag[$i]; $k = 0
        foreach ($l in (Get-Wrapped ($CW - 4) $G.SendText[$i])) {
            PL; if ($k -eq 0) { PA '' ' '; PA $ic[1] $ic[0]; PA '' '  ' } else { PA '' '    ' }
            if ($G.SendTag[$i] -eq 'fail') { PA '' $l } else { PA $St.MUTED $l }
            PE; $k = 1
        }
    }
    $body = $script:Panel; $script:Panel = $headLines
    for ($i = [math]::Max(0, $body.Count - $rows); $i -lt $body.Count; $i++) { $Panel.Add($body[$i]) }
    if ($G.SendPhase -eq 'stopped') {
        $back = 'Back'; if ($G.SendHow -eq 'direct') { $back = 'Close' }
        PBottom 1; $G.Btns = @('Send again', $back); PButtons $G.Btns
        return
    }
    PBlank
    if ($G.SendPhase -eq 'probe') { PPara $St.FAINT 'The first HTTPS start on this machine can take a minute while Tailscale gets its certificate.' }
    PBottom 1
    $act = 'Checking'; if ($G.SendPhase -eq 'probe') { $act = 'Checking that this machine answers at its own address' }
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED ('  ' + $act); PE
}

function View-SendCode {  # sending: the code, the one line to follow on the Workstation, and the time so far
    PTitle 'Send disc 2 to a Workstation' "Sending on port $SendPort, to your tailnet only." $Gl.OK $St.OK
    $inner = $G.SendShown.Length + 6; $hz = $Gl.H * $inner
    PL; PA '' '   '; PA $St.ACCENT ($Gl.TL + $hz + $Gl.TR); PE
    PL; PA '' '   '; PA $St.ACCENT $Gl.V; PA $St.CHIPBG '   '; PA ($St.CHIPBG + $St.B + $St.ACCENTT) $G.SendShown; PA $St.CHIPBG '   '; PA $St.ACCENT $Gl.V; PE
    PL; PA '' '   '; PA $St.ACCENT ($Gl.BL + $hz + $Gl.BR); PE
    PBlank
    PPara $St.B "On the Workstation, choose Get Disc 2 over the Network, pick $($G.SendShort), type $($G.SendShown)"
    PBlank
    PSteps 2 @("Get Disc 2 over the Network is on First Start. From the Workstation's terminal: ``doas /usr/local/d32/sbin/d32-content-fetch``",
        'Keep this screen open until the Workstation says the download is complete; Enter then stops sending.')
    while ($Panel.Count -gt $CH - 3) { PDrop }
    PBottom 2
    $spent = Format-Clock ([math]::Floor(((Get-NowMs) - $G.SendSince) / 1000))
    PL; PA $St.ACCENT $Spin[$G.Tick % $Spin.Count]; PA $St.MUTED ('  Sending  ' + $Gl.DOT + '  ' + $spent + ' so far  ' + $Gl.DOT + '  ' + (Format-Bytes $G.SendSize) + '  ' + $Gl.DOT + '  port ' + $SendPort); PE
    $G.Btns = @('Stop sending'); PButtons $G.Btns
}

function View-SendFail {  # stopped on a problem: what the tools said, what it means and what to do, from the error table
    PTitle 'Send disc 2 stopped' '' $Gl.FAIL $St.ERR; PDrop
    PL; PA $St.B 'What the tools said'; PE
    $shown = 0
    foreach ($l in $G.SendFailLines) {
        if ($shown -ge 3) { break }; if (-not $l) { continue }
        PL $St.ERRBG; PA $St.ERR ($Gl.QUOTE + ' '); PA '' $l; PE; $shown++
    }
    PL $St.ERRBG; PA $St.ERR ($Gl.QUOTE + ' '); PA $St.MUTED ('code ' + $G.SendFailCode); PE
    PBlank
    if ($G.SendRow -ge 0) {
        PL; PA $St.B 'What it means'; PE
        PPara '' $script:ErrRows[$G.SendRow].Mean
        PBlank
        PL; PA $St.B 'What to do'; PE
        PSteps 5 (Get-FixSteps $G.SendRow)
    }
    while ($Panel.Count -gt $CH - 3) { PDrop }
    PBottom 2
    PL; PA $St.FAINT 'Full log  '; PA $St.MUTED (Get-ShortPath $script:LogFile); PE
    $back = 'Back'; if ($G.SendHow -eq 'direct') { $back = 'Close' }
    $G.Btns = @('Try again', $back, 'Show the log'); PButtons $G.Btns
}

function View-SendQuit {  # the quit question over the send screen
    $keep = [math]::Min(5, $Panel.Count); while ($Panel.Count -gt $keep) { PDrop }
    $msg = 'Nothing more changes. Disc 2 stays in the download folder.'
    if ($G.SendPhase -in 'checks', 'probe', 'sending') { $msg = 'Quitting stops sending and turns the serve off; the code then no longer works. Disc 2 stays in the download folder.' }
    BxTop $St.WARN 'Quit the setup?'
    $script:BxStyle = $St.WARN; BxText '' $msg
    BxOpen $St.WARN; BxClose
    BxButtons @('Stay', 'Quit')
    BxBot $St.WARN
}

# ---------------------------------------------------------------------------------------------------------------

$script:Bound = $PSBoundParameters
if ($MyInvocation.InvocationName -ne '.') {
    if ($DemoReplay) { Invoke-DemoReplay $DemoReplay }
    if ($Help) { Get-Content -LiteralPath $WizSelf -TotalCount 18 | Select-Object -Skip 1 | ForEach-Object { $_ }; exit 0 }
    $mode = Get-WizardMode
    if ($PrintMode) { [Console]::Out.WriteLine($mode); exit 0 }
    if ($SendDisc2 -and $mode -ne 'tui') { $sendCode = Invoke-SendPlain | Select-Object -Last 1; exit $sendCode }
    if ($mode -ne 'tui' -and $VmGuide) {
        $base = $env:LOCALAPPDATA; if (-not $base) { $base = [IO.Path]::GetTempPath() }
        $script:StateDir = Join-Path $base 'Deep32-setup'
        if ($Demo) { $script:StateDir = Join-Path ([IO.Path]::GetTempPath()) ('deep32-wizard-demo-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)) }
        [void](New-Item -ItemType Directory -Force -Path $script:StateDir -ErrorAction SilentlyContinue)
        $script:LogFile = ''; $script:WorkDir = $WizRepo
        $code = Invoke-VmPlain
        if ($Demo) { Remove-Item -LiteralPath $script:StateDir -Recurse -Force -ErrorAction SilentlyContinue }
        exit ([int]@($code)[-1])
    }
    if ($mode -ne 'tui' -and -not $Decode) {
        $pass = Get-SetupArguments
        if ($Demo) { $script:PlanDigest = '3f2a9c417be021d5c0de86b19a4471f2e53b0c9d8a6e1f37b24c95d0e8a1f6c3'; Invoke-DemoReplay 'forge' }
        & $WizSetup @pass
        exit $LASTEXITCODE
    }
    $code = Invoke-Wizard
    exit $code
}
