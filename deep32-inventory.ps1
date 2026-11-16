<#
deep32-inventory.ps1: what this Windows machine has, and which Deep32 install profile fits it.

What it does:
  report (default)  Reads the machine: processor (model, maker, family/model/stepping,
                    AVX, AVX2, AVX-512F, cores and threads, fast and efficient cores),
                    memory, disks and free space, graphics cards (model, memory, driver,
                    compute capability through nvidia-smi when present), virtualisation
                    in firmware, SLAT, IOMMU where readable, hypervisors (Hyper-V,
                    VirtualBox, VMware), WSL2 (registry and files only; wsl.exe is never
                    run), Docker (client version only), Tailscale, Claude Code, Codex and
                    the GitHub CLI (presence and version only), and the Deep32 ports in
                    use. Then the verdict from install\matrix.json: the best profile, two
                    runner-ups, how each VM would be sized, what is missing.
                    Read-only, no administrator rights, a few seconds. Writes nothing;
                    the .NET compiler behind Add-Type keeps and removes its own temp files.
                    A tool asked for its version that does not answer in time (15 s; 20 s
                    for the GitHub CLI sign-in check) is ended together with every process
                    it started (a .cmd tool's node too), and no other process. So is a tool
                    that ends in time but leaves a process of its own holding its output
                    open past the limit (the output read so far is kept).
  bench quick       About 2 minutes (--seconds N to shorten): processor single-thread and
                    all-thread scores (compiled C#), memory copy speed, disk sequential
                    and random speed on one temporary file in the system temp folder.
                    The file never takes more than 5% of the free space, the disk part is
                    skipped if it would leave less than 10 GB free, and the file is
                    deleted by the system when its handle closes (also if the run is
                    stopped). Refuses to start while gaming mode is on, or while a
                    gaming-mode state file exists that it cannot read.
  No mode calls a language model or changes anything on the machine.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File deep32-inventory.ps1 [report] [options]
  powershell -NoProfile -ExecutionPolicy Bypass -File deep32-inventory.ps1 bench quick [options]
  Options:
    --json            print one compact JSON object (contract C10) instead of the page
    --net             report only: also check that the sites an install needs answer
                      (HEAD requests) and whether the GitHub CLI is signed in
    --seconds N       bench only: total time, 1 to 600 (default 120)
    --matrix FILE     install matrix to use (default: install\matrix.json beside this file)
    --facts FILE      report only: skip the probes and judge the facts in FILE (a JSON
                      object with a "facts" member, as this program prints); for tests
    --guard-file FILE bench only: one more gaming-mode state file to obey, besides the
                      one the DEEP32_GUARD_FILE environment variable names, if set
    --version, --help
  Options and modes may also be written in capitals (PowerShell custom).
  JSON files are read as UTF-8 unless they start with a byte-order mark (UTF-8, UTF-16 or
  UTF-32). The JSON this program prints is pure ASCII (other characters as \u escapes).

Environment:
  DEEP32_GUARD_FILE: the gaming-mode state file bench quick obeys. There is no built-in
  default: when it is unset or empty and no --guard-file is given, no state file is read.

Test hook (read-only; unset in normal use):
  DEEP32_INVENTORY_TEST_GUARD_ONLY=1: bench quick checks the gaming-mode guard and stops there
  without measuring anything (exit 0 with "guard":"clear", or 3 as usual); only in that case
  DEEP32_INVENTORY_TEST_DEFAULT_GUARD replaces the DEEP32_GUARD_FILE state file.

Exit codes:
  0  done
  1  other error (for example the matrix is missing or unreadable)
  2  bad arguments
  3  refused (gaming mode is on, or its state cannot be read)
#>

$ErrorActionPreference = 'Continue'
Set-StrictMode -Off
# Windows PowerShell 5.1 adds a Count member to arrays that can leak into ConvertTo-Json; drop it for this process only.
try { Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue } catch { }

$ToolVersion = '0.1.0'
$Schema = 'deep32.inventory.v1'
# Gaming-mode state file the bench obeys: only the one the DEEP32_GUARD_FILE environment variable names
# (none when it is unset or empty); --guard-file adds one more. There is no built-in default path.
$DefaultGuardFile = $env:DEEP32_GUARD_FILE
$UserAgent = 'deep32-inventory/' + $ToolVersion

function Write-Err([string]$m) { [Console]::Error.WriteLine('deep32-inventory: ' + $m) }
function Out-Line([string]$s) { [Console]::Out.WriteLine($s) }
function Resolve-ArgPath([string]$p) {
    # A relative path given on the command line, made absolute. Windows PowerShell 5.1 starts in $PSHOME
    # when the folder it was started from has [ or ] in its name; the process's own folder is right then.
    if ([System.IO.Path]::IsPathRooted($p)) { return $p }
    $base = $ExecutionContext.SessionState.Path.CurrentFileSystemLocation.ProviderPath
    if ($base -eq $PSHOME -and [Environment]::CurrentDirectory -ne $PSHOME) { $base = [Environment]::CurrentDirectory }
    return [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($base, $p))
}

function Show-Usage {
    Out-Line 'usage: deep32-inventory.ps1 [report] [--json] [--net] [--matrix FILE] [--facts FILE]'
    Out-Line '       deep32-inventory.ps1 bench quick [--seconds N] [--json] [--guard-file FILE]'
    Out-Line 'exit codes: 0 done, 1 other error, 2 bad arguments, 3 refused'
}

# ---------------------------------------------------------------- arguments
$Mode = $null; $Sub = $null; $Json = $false; $Net = $false; $Seconds = 120; $SecondsGiven = $false
$MatrixPath = $null; $FactsPath = $null; $GuardFile = $DefaultGuardFile; $GuardExtra = $null
$GuardOnly = ($env:DEEP32_INVENTORY_TEST_GUARD_ONLY -eq '1')
if ($GuardOnly -and $env:DEEP32_INVENTORY_TEST_DEFAULT_GUARD) { $GuardFile = $env:DEEP32_INVENTORY_TEST_DEFAULT_GUARD }
$argList = @($args)
$i = 0
while ($i -lt $argList.Count) {
    $a = [string]$argList[$i]
    $al = $a.ToLowerInvariant()
    if ($al -eq '--json' -or $al -eq '-json') { $Json = $true }
    elseif ($al -eq '--net' -or $al -eq '-net') { $Net = $true }
    elseif ($al -eq '--help' -or $al -eq '-h' -or $al -eq '-help' -or $al -eq '/?') { Show-Usage; exit 0 }
    elseif ($al -eq '--version' -or $al -eq '-version') { Out-Line ('deep32-inventory ' + $ToolVersion); exit 0 }
    elseif ($al -eq '--seconds' -or $al -eq '-seconds' -or $al -eq '--matrix' -or $al -eq '-matrix' -or
            $al -eq '--facts' -or $al -eq '-facts' -or $al -eq '--guard-file' -or $al -eq '-guard-file') {
        if ($i + 1 -ge $argList.Count) { Write-Err ($a + ' needs a value'); Show-Usage; exit 2 }
        $v = [string]$argList[$i + 1]; $i++
        if ($v -eq '') { Write-Err ($a + ' needs a value'); Show-Usage; exit 2 }
        if ($al -like '*seconds') {
            # digits only, as the shell version takes them (no sign, no spaces)
            if ($v -cnotmatch '\A[0-9]{1,9}\z') { Write-Err '--seconds must be a whole number from 1 to 600'; exit 2 }
            $n = [int]$v
            if ($n -lt 1 -or $n -gt 600) { Write-Err '--seconds must be a whole number from 1 to 600'; exit 2 }
            $Seconds = $n; $SecondsGiven = $true
        }
        elseif ($al -like '*matrix') { $MatrixPath = $v }
        elseif ($al -like '*facts') { $FactsPath = $v }
        else { $GuardExtra = $v }
    }
    elseif ($al.StartsWith('-')) { Write-Err ('unknown option ' + $a); Show-Usage; exit 2 }
    elseif ($null -eq $Mode) { $Mode = $al }
    elseif ($null -eq $Sub) { $Sub = $al }
    else { Write-Err ('unexpected argument ' + $a); Show-Usage; exit 2 }
    $i++
}
if ($null -eq $Mode) { $Mode = 'report' }
if ($Mode -eq 'report') {
    if ($null -ne $Sub) { Write-Err ('unexpected argument ' + $Sub); exit 2 }
    if ($SecondsGiven) { Write-Err '--seconds belongs to bench quick'; exit 2 }
}
elseif ($Mode -eq 'bench') {
    if ($Sub -eq 'system' -or $Sub -eq 'stress') { Write-Err ('bench ' + $Sub + ' is not in this version (it comes with an installed system)'); exit 2 }
    if ($Sub -ne 'quick') { Write-Err 'bench needs quick'; Show-Usage; exit 2 }
    if ($Net) { Write-Err '--net belongs to report'; exit 2 }
    if ($null -ne $FactsPath) { Write-Err '--facts belongs to report'; exit 2 }
}
else { Write-Err ('unknown mode ' + $Mode); Show-Usage; exit 2 }
if ($null -eq $MatrixPath) { $MatrixPath = Join-Path $PSScriptRoot 'install\matrix.json' } else { $MatrixPath = Resolve-ArgPath $MatrixPath }
if ($null -ne $FactsPath) { $FactsPath = Resolve-ArgPath $FactsPath }
if ($null -ne $GuardExtra) { $GuardExtra = Resolve-ArgPath $GuardExtra }

# ---------------------------------------------------------------- small helpers
function Get-Ascii($s) {
    if ($null -eq $s) { return $null }
    $t = ([string]$s) -replace '[^\x20-\x7E]', ''
    $t = ($t -replace '\s+', ' ').Trim()
    if ($t -eq '') { return $null }
    return $t
}
function Get-Ver($text) {
    if ($null -eq $text) { return $null }
    if ([string]$text -match '(\d+\.\d+(\.\d+)?)') { return $Matches[1] }
    return $null
}
function Read-TextFile([string]$path) {
    # UTF-8 unless the file starts with a byte-order mark; relative paths are taken from the current location
    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
    return [System.IO.File]::ReadAllText($full)
}
function ConvertTo-AsciiJson($obj, [int]$depth) {
    # Compact JSON with every character outside ASCII written as a \u escape, so the console code page cannot change it
    $s = $obj | ConvertTo-Json -Depth $depth -Compress
    return [regex]::Replace($s, '[^\x00-\x7F]', { param($m) '\u' + ([int][char]$m.Value).ToString('x4') })
}
function Get-GB($bytes, [int]$digits) {
    if ($null -eq $bytes) { return $null }
    return [math]::Round(([double]$bytes) / 1073741824.0, $digits)
}
function Find-Tool([string]$Name, [string[]]$Extra) {
    try {
        $c = @(Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue |
               Where-Object { @('.exe', '.cmd', '.bat') -contains ([string]$_.Extension).ToLowerInvariant() })
        if ($c.Count -gt 0) { return [string]$c[0].Path }
    } catch { }
    foreach ($e in $Extra) { if ($e -and (Test-Path -LiteralPath $e -PathType Leaf)) { return $e } }
    return $null
}
function Stop-StartedTree([System.Diagnostics.Process]$Root, [long]$RootTicks) {
    # Ends a process this program started ($Root, created at UTC ticks $RootTicks) and every process
    # descended from it (a .cmd tool runs in cmd.exe, which starts node, which may start more), and
    # no other process. A process joins the tree only when (1) its parent is already in the tree,
    # (2) it was created no earlier than that parent, so an older process whose own parent once had
    # the same id is never taken, and (3) the process this program opens is still the one the system
    # listed (same id and same start time). A handle to every process of the tree stays open until
    # the end, so Windows cannot give one of their ids to another process meanwhile. Each process is
    # ended before its children are listed, so it cannot start new ones afterwards. If the process
    # list cannot be read at all, taskkill /T /F ends the tree of the started process (still alive
    # then, so its id is its own), and the started process is ended directly as well.
    $tree = New-Object System.Collections.ArrayList
    $rootUs = $RootTicks - ($RootTicks % 10)
    [void]$tree.Add(@{ id = $Root.Id; us = $rootUs; p = $Root })
    $listOk = $true
    try { [void](Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + [int]$Root.Id) -Property ProcessId -ErrorAction Stop) } catch { $listOk = $false }
    if (-not $listOk) {
        try {
            $kpsi = New-Object System.Diagnostics.ProcessStartInfo
            $kpsi.FileName = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            $kpsi.Arguments = '/T /F /PID ' + [int]$Root.Id
            $kpsi.UseShellExecute = $false; $kpsi.CreateNoWindow = $true
            $kpsi.RedirectStandardOutput = $true; $kpsi.RedirectStandardError = $true
            $k = [System.Diagnostics.Process]::Start($kpsi)
            $ko = $k.StandardOutput.ReadToEndAsync(); $ke = $k.StandardError.ReadToEndAsync()
            if (-not $k.WaitForExit(15000)) { try { $k.Kill() } catch { } }
            $k.Dispose()
        } catch { }
        try { $Root.Kill() } catch { }
        return
    }
    try { $Root.Kill() } catch { }
    try { [void]$Root.WaitForExit(5000) } catch { }
    $i = 0
    while ($i -lt $tree.Count -and $tree.Count -lt 512) {
        $n = $tree[$i]; $i++
        $rows = @()
        try { $rows = @(Get-CimInstance -ClassName Win32_Process -Filter ('ParentProcessId=' + [int]$n.id) -Property ProcessId, CreationDate -ErrorAction Stop) } catch { $rows = @() }
        foreach ($row in $rows) {
            if ($null -eq $row -or $null -eq $row.CreationDate) { continue }
            $cid = [int]$row.ProcessId
            $seen = $false
            foreach ($t in $tree) { if ($t.id -eq $cid) { $seen = $true; break } }
            if ($seen) { continue }
            $ct = ([datetime]$row.CreationDate).ToUniversalTime().Ticks
            $cus = $ct - ($ct % 10)
            if ($cus -lt $n.us) { continue }
            $cp = $null; $sus = $null
            try {
                $cp = [System.Diagnostics.Process]::GetProcessById($cid)
                [void]$cp.Handle
                $st = $cp.StartTime.ToUniversalTime().Ticks
                $sus = $st - ($st % 10)
            } catch { if ($cp) { try { $cp.Dispose() } catch { } }; continue }
            if ([math]::Abs($sus - $cus) -gt 10) { try { $cp.Dispose() } catch { }; continue }
            [void]$tree.Add(@{ id = $cid; us = $sus; p = $cp })
            try { $cp.Kill() } catch { }
            try { [void]$cp.WaitForExit(5000) } catch { }
        }
    }
    for ($j = 1; $j -lt $tree.Count; $j++) { try { $tree[$j].p.Dispose() } catch { } }
}
function Invoke-Tool([string]$Path, [string]$Arguments, [int]$TimeoutMs) {
    # Runs a program with its output captured. If it overruns, ends it and every process it started
    # (Stop-StartedTree), and nothing else. The same applies when the program itself ends in time but
    # a process it started keeps its output open past the limit (a .cmd tool that leaves a background
    # child): the output read so far is returned once that tree is ended.
    if (-not $Path) { return $null }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $ext = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
        if ($ext -eq '.cmd' -or $ext -eq '.bat') {
            $psi.FileName = $env:ComSpec
            $psi.Arguments = '/d /c ""' + $Path + '" ' + $Arguments + '"'
        } else {
            $psi.FileName = $Path
            $psi.Arguments = $Arguments
        }
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $p = [System.Diagnostics.Process]::Start($psi)
        $born = $null
        try { $born = $p.StartTime.ToUniversalTime().Ticks } catch { }
        $o = $p.StandardOutput.ReadToEndAsync()
        $e = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($TimeoutMs)) {
            if ($null -ne $born) { Stop-StartedTree $p ([long]$born) } else { try { $p.Kill() } catch { } }
            return $null
        }
        $p.WaitForExit()
        # The output ends only when every process holding it has closed it; wait for that only as long
        # as the time limit allows (at least half a second), then end what the program started.
        $left = $TimeoutMs - [int]$sw.ElapsedMilliseconds
        if ($left -lt 500) { $left = 500 }
        $drained = $false
        try { $drained = [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($o, $e), [int]$left) } catch { $drained = $true }
        if (-not $drained) {
            if ($null -ne $born) { Stop-StartedTree $p ([long]$born) }
            try { [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($o, $e), 2000) } catch { }
            if (-not ($o.IsCompleted -and $e.IsCompleted)) { return $null }
        }
        return @{ code = $p.ExitCode; out = [string]$o.Result; err = [string]$e.Result }
    } catch { return $null }
}
function Get-ToolVersion([string]$Name, [string[]]$Extra, [string]$Arguments) {
    # false = not installed, a version string = installed, 'present' = installed, version unreadable
    $path = Find-Tool $Name $Extra
    if (-not $path) { return $false }
    $r = Invoke-Tool $path $Arguments 15000
    if ($null -eq $r) { return 'present' }
    $first = (($r.out + "`n" + $r.err) -split "`r?`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1)
    $v = Get-Ver $first
    if ($v) { return $v }
    return 'present'
}

# ---------------------------------------------------------------- native helpers (Add-Type)
$NativeSource = @'
using System;
using System.Runtime.InteropServices;
public static class D32InvNative1 {
    [DllImport("kernel32.dll")]
    public static extern bool IsProcessorFeaturePresent(uint feature);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetLogicalProcessorInformationEx(int relationship, IntPtr buffer, ref uint length);
    static int PopCount(ulong v) { int c = 0; while (v != 0) { c += (int)(v & 1UL); v >>= 1; } return c; }
    // {cores, threads, hybrid 0/1, fast cores, fast threads, efficient cores, efficient threads}
    public static int[] Topology() {
        if (IntPtr.Size != 8) return null;
        uint len = 0;
        GetLogicalProcessorInformationEx(0, IntPtr.Zero, ref len);
        if (len == 0) return null;
        IntPtr buf = Marshal.AllocHGlobal((int)len);
        try {
            if (!GetLogicalProcessorInformationEx(0, buf, ref len)) return null;
            int n = 0; int[] effs = new int[4096]; int[] lps = new int[4096];
            int off = 0; int maxEff = 0; int minEff = 255;
            while (off < (int)len && n < 4096) {
                int rel = Marshal.ReadInt32(buf, off);
                int size = Marshal.ReadInt32(buf, off + 4);
                if (size <= 0) break;
                if (rel == 0) {
                    int eff = Marshal.ReadByte(buf, off + 9);
                    int gc = Marshal.ReadInt16(buf, off + 30);
                    int lp = 0;
                    for (int g = 0; g < gc; g++) { lp += PopCount((ulong)Marshal.ReadInt64(buf, off + 32 + g * 16)); }
                    effs[n] = eff; lps[n] = lp; n++;
                    if (eff > maxEff) maxEff = eff;
                    if (eff < minEff) minEff = eff;
                }
                off += size;
            }
            int threads = 0, fc = 0, ft = 0, ec = 0, et = 0;
            for (int i = 0; i < n; i++) {
                threads += lps[i];
                if (effs[i] == maxEff) { fc++; ft += lps[i]; } else { ec++; et += lps[i]; }
            }
            return new int[] { n, threads, (maxEff != minEff) ? 1 : 0, fc, ft, ec, et };
        } finally { Marshal.FreeHGlobal(buf); }
    }
}
'@

$BenchSource = @'
using System;
using System.Diagnostics;
using System.Threading;
using System.Runtime.InteropServices;
public static class D32InvBench1 {
    static volatile int sink;
    static long Spin(long ms) {
        Stopwatch sw = Stopwatch.StartNew();
        ulong x = 88172645463325252UL; double acc = 0.0; long iters = 0;
        while (sw.ElapsedMilliseconds < ms) {
            for (int i = 0; i < 65536; i++) {
                x ^= x << 13; x ^= x >> 7; x ^= x << 17;
                acc += (double)(x & 0xFFFFUL) * 1.0000001;
            }
            iters += 65536;
        }
        sink = (int)acc ^ (int)x;
        return iters;
    }
    // {single-thread Mops, all-thread Mops}
    public static double[] Cpu(int msEach, int threads) {
        Stopwatch sw = Stopwatch.StartNew();
        long it = Spin(msEach);
        double st = it / sw.Elapsed.TotalSeconds / 1e6;
        long[] counts = new long[threads];
        Thread[] ts = new Thread[threads];
        for (int t = 0; t < threads; t++) {
            int idx = t;
            ts[t] = new Thread(() => { counts[idx] = Spin(msEach); });
        }
        sw = Stopwatch.StartNew();
        for (int t = 0; t < threads; t++) ts[t].Start();
        for (int t = 0; t < threads; t++) ts[t].Join();
        double secs = sw.Elapsed.TotalSeconds;
        long sum = 0; for (int t = 0; t < threads; t++) sum += counts[t];
        return new double[] { st, sum / secs / 1e6 };
    }
    // GB copied per second (each byte read once and written once)
    public static double MemCopy(int ms, int mb) {
        byte[] a = new byte[mb * 1048576]; byte[] b = new byte[mb * 1048576];
        for (int i = 0; i < a.Length; i += 4096) { a[i] = (byte)i; b[i] = 1; }
        Buffer.BlockCopy(a, 0, b, 0, a.Length);
        Stopwatch sw = Stopwatch.StartNew(); long bytes = 0;
        while (sw.ElapsedMilliseconds < ms) { Buffer.BlockCopy(a, 0, b, 0, a.Length); bytes += a.Length; }
        return bytes / sw.Elapsed.TotalSeconds / 1e9;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool WriteFile(IntPtr h, IntPtr buf, uint n, out uint done, IntPtr ov);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool ReadFile(IntPtr h, IntPtr buf, uint n, out uint done, IntPtr ov);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetFilePointerEx(IntPtr h, long dist, out long newPos, uint method);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr VirtualAlloc(IntPtr addr, UIntPtr size, uint type, uint protect);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool VirtualFree(IntPtr addr, UIntPtr size, uint type);

    // {bytes in file, seq write MB/s, seq read MB/s, random 4K read IOPS, random 4K write IOPS}
    // One handle, unbuffered and write-through, deleted by the system when the handle closes.
    public static double[] Disk(string path, long maxBytes, int msW, int msR, int msRR, int msRW) {
        const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
        const uint CREATE_NEW = 1;
        const uint NO_BUFFERING = 0x20000000, WRITE_THROUGH = 0x80000000, DELETE_ON_CLOSE = 0x04000000;
        const int BLOCK = 1048576; const int SMALL = 4096;
        IntPtr h = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, IntPtr.Zero, CREATE_NEW,
                               NO_BUFFERING | WRITE_THROUGH | DELETE_ON_CLOSE, IntPtr.Zero);
        if (h == new IntPtr(-1)) throw new Exception("cannot create the temporary file, error " + Marshal.GetLastWin32Error());
        IntPtr buf = VirtualAlloc(IntPtr.Zero, (UIntPtr)BLOCK, 0x3000, 0x04);
        try {
            if (buf == IntPtr.Zero) throw new Exception("cannot allocate the disk buffer");
            ulong x = 0x9E3779B97F4A7C15UL;
            for (int i = 0; i < BLOCK; i += 8) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; Marshal.WriteInt64(buf, i, (long)x); }
            uint done; long pos;
            long written = 0;
            Stopwatch sw = Stopwatch.StartNew();
            while (written + BLOCK <= maxBytes && sw.ElapsedMilliseconds < msW) {
                if (!WriteFile(h, buf, BLOCK, out done, IntPtr.Zero) || done != BLOCK) throw new Exception("write failed");
                written += BLOCK;
            }
            double wsecs = sw.Elapsed.TotalSeconds;
            double seqW = written / 1048576.0 / Math.Max(wsecs, 0.001);
            if (written < BLOCK) return new double[] { 0, 0, 0, 0, 0 };
            SetFilePointerEx(h, 0, out pos, 0);
            long read = 0;
            sw = Stopwatch.StartNew();
            while (read < written && sw.ElapsedMilliseconds < msR) {
                if (!ReadFile(h, buf, BLOCK, out done, IntPtr.Zero) || done == 0) break;
                read += done;
            }
            double seqR = read / 1048576.0 / Math.Max(sw.Elapsed.TotalSeconds, 0.001);
            long pages = written / SMALL;
            Random rnd = new Random(32);
            long ops = 0;
            sw = Stopwatch.StartNew();
            while (sw.ElapsedMilliseconds < msRR) {
                long off = ((long)(rnd.NextDouble() * pages)) * SMALL;
                SetFilePointerEx(h, off, out pos, 0);
                if (!ReadFile(h, buf, SMALL, out done, IntPtr.Zero)) throw new Exception("random read failed");
                ops++;
            }
            double rr = ops / Math.Max(sw.Elapsed.TotalSeconds, 0.001);
            ops = 0;
            sw = Stopwatch.StartNew();
            while (sw.ElapsedMilliseconds < msRW) {
                long off = ((long)(rnd.NextDouble() * pages)) * SMALL;
                SetFilePointerEx(h, off, out pos, 0);
                if (!WriteFile(h, buf, SMALL, out done, IntPtr.Zero)) throw new Exception("random write failed");
                ops++;
            }
            double rw = ops / Math.Max(sw.Elapsed.TotalSeconds, 0.001);
            return new double[] { written, seqW, seqR, rr, rw };
        } finally {
            if (buf != IntPtr.Zero) VirtualFree(buf, UIntPtr.Zero, 0x8000);
            CloseHandle(h);
        }
    }
}
'@

$script:NativeOk = $false
function Initialize-Native {
    if ($script:NativeOk) { return $true }
    try {
        if (-not ('D32InvNative1' -as [type])) { Add-Type -TypeDefinition $NativeSource -Language CSharp -ErrorAction Stop }
        $script:NativeOk = $true
    } catch { $script:NativeOk = $false }
    return $script:NativeOk
}

# ---------------------------------------------------------------- probes
function Get-CpuGen([string]$name) {
    if (-not $name) { return $null }
    if ($name -match 'Core\(TM\) Ultra \d+ (\d)\d\d') { return ('Intel Core Ultra series ' + $Matches[1]) }
    if ($name -match 'Core\(TM\) i[3579]-(\d{3,5})') {
        $d = $Matches[1]
        if ($d.Length -eq 5) { $g = $d.Substring(0, 2) }
        elseif ($d.Length -eq 4 -and $d.StartsWith('1')) { $g = $d.Substring(0, 2) }
        elseif ($d.Length -eq 4) { $g = $d.Substring(0, 1) }
        else { $g = '1' }
        return ('Intel Core generation ' + [int]$g)
    }
    if ($name -match 'Xeon.*E([357])-\d{4}[A-Z]*\s+v(\d)') { return ('Intel Xeon E' + $Matches[1] + ' v' + $Matches[2]) }
    if ($name -match 'Xeon.*E([357])-\d{4}') { return ('Intel Xeon E' + $Matches[1] + ' v1') }
    if ($name -match 'Ryzen (Threadripper )?(PRO )?\d* ?(\d)\d{3}') { return ('AMD Ryzen ' + $Matches[3] + '000 series') }
    return $null
}

function Get-Facts {
    $notes = New-Object System.Collections.ArrayList
    $nativeOk = Initialize-Native
    if (-not $nativeOk) { [void]$notes.Add('instruction sets and core types unavailable: Add-Type failed') }

    # operating system
    $os = [ordered]@{ family = 'windows'; name = $null; edition = $null; version = $null; build = $null; arch = $null; wsl_guest = $false }
    $osCim = $null
    try { $osCim = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop } catch { }
    try {
        $cv = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
        $ed = [string]$cv.EditionID
        if ($ed -like 'Core*') { $os.edition = 'home' }
        elseif ($ed -like 'Professional*') { $os.edition = 'pro' }
        elseif ($ed -like 'Enterprise*') { $os.edition = 'enterprise' }
        elseif ($ed -like 'Education*') { $os.edition = 'education' }
        elseif ($ed -like 'Server*') { $os.edition = 'server' }
        elseif ($ed) { $os.edition = $ed.ToLowerInvariant() }
        $os.version = Get-Ascii $cv.DisplayVersion
        if ($cv.CurrentBuild) { $os.build = [int]$cv.CurrentBuild }
    } catch { }
    if ($osCim) {
        $os.name = Get-Ascii ([string]$osCim.Caption -replace '^Microsoft\s+', '')
        if (-not $os.build -and $osCim.BuildNumber) { $os.build = [int]$osCim.BuildNumber }
    }
    $pa = $env:PROCESSOR_ARCHITEW6432
    if (-not $pa) { $pa = $env:PROCESSOR_ARCHITECTURE }
    switch -regex ([string]$pa) { '^AMD64$' { $os.arch = 'x64' } '^ARM64$' { $os.arch = 'arm64' } '^x86$' { $os.arch = 'x86' } default { $os.arch = $null } }

    # machine
    $hw = [ordered]@{ vendor = $null; model = $null; virtual = $null }
    $cs = $null
    try { $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop } catch { }
    if ($cs) {
        $hw.vendor = Get-Ascii $cs.Manufacturer
        $hw.model = Get-Ascii $cs.Model
        $hw.virtual = ((([string]$cs.Manufacturer) + ' ' + ([string]$cs.Model)) -match 'Virtual Machine|VMware|VirtualBox|KVM|QEMU|Xen|Bochs|Parallels|HVM domU')
    }

    # processor
    $cpu = [ordered]@{ vendor = $null; model = $null; family = $null; model_id = $null; stepping = $null; gen = $null
                       sockets = $null; cores = $null; threads = $null; p_cores = $null; e_cores = $null; p_threads = $null
                       avx = $null; avx2 = $null; avx512f = $null }
    $procs = @()
    try { $procs = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop) } catch { }
    if ($procs.Count -gt 0) {
        $p0 = $procs[0]
        $mf = [string]$p0.Manufacturer
        if ($mf -match 'Intel') { $cpu.vendor = 'intel' } elseif ($mf -match 'AMD') { $cpu.vendor = 'amd' } elseif ($mf) { $cpu.vendor = Get-Ascii $mf.ToLowerInvariant() }
        $cpu.model = Get-Ascii $p0.Name
        if ([string]$p0.Caption -match 'Family\s+(\d+)\s+Model\s+(\d+)\s+Stepping\s+(\d+)') {
            $cpu.family = [int]$Matches[1]; $cpu.model_id = [int]$Matches[2]; $cpu.stepping = [int]$Matches[3]
        }
        $cpu.gen = Get-CpuGen ([string]$cpu.model)
        $cpu.sockets = $procs.Count
        $c = 0; $t = 0
        foreach ($p in $procs) { $c += [int]$p.NumberOfCores; $t += [int]$p.NumberOfLogicalProcessors }
        $cpu.cores = $c; $cpu.threads = $t
    }
    if ($nativeOk) {
        try {
            $cpu.avx = [bool][D32InvNative1]::IsProcessorFeaturePresent(39)
            $cpu.avx2 = [bool][D32InvNative1]::IsProcessorFeaturePresent(40)
            $cpu.avx512f = [bool][D32InvNative1]::IsProcessorFeaturePresent(41)
        } catch { }
        try {
            $topo = [D32InvNative1]::Topology()
            if ($null -ne $topo) {
                if (-not $cpu.cores) { $cpu.cores = $topo[0] }
                if (-not $cpu.threads) { $cpu.threads = $topo[1] }
                if ($topo[2] -eq 1) { $cpu.p_cores = $topo[3]; $cpu.p_threads = $topo[4]; $cpu.e_cores = $topo[5] }
                else { $cpu.e_cores = 0 }
            }
        } catch { }
    }

    # memory
    $mem = [ordered]@{ total_gb = $null; installed_gb = $null; free_gb = $null }
    if ($cs) { $mem.total_gb = Get-GB $cs.TotalPhysicalMemory 1 }
    try {
        $sum = 0.0
        foreach ($m in @(Get-CimInstance -ClassName Win32_PhysicalMemory -ErrorAction Stop)) { $sum += [double]$m.Capacity }
        if ($sum -gt 0) { $mem.installed_gb = [int][math]::Round($sum / 1073741824.0) }
    } catch { }
    if ($osCim -and $osCim.FreePhysicalMemory) { $mem.free_gb = Get-GB (([double]$osCim.FreePhysicalMemory) * 1024.0) 1 }

    # disks
    $disks = New-Object System.Collections.ArrayList
    $media = @{}
    try {
        $phys = @{}
        foreach ($pd in @(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_PhysicalDisk -ErrorAction Stop)) {
            $phys[[string]$pd.DeviceId] = [int]$pd.MediaType
        }
        foreach ($pt in @(Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_Partition -ErrorAction Stop)) {
            $dl = [string]$pt.DriveLetter
            if ($dl -and $dl.Trim() -ne '' -and [int][char]$dl[0] -ne 0) {
                $k = [string]$pt.DiskNumber
                if ($phys.ContainsKey($k)) { $media[$dl.ToUpperInvariant() + ':'] = $phys[$k] }
            }
        }
    } catch { }
    try {
        foreach ($ld in @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop)) {
            $id = ([string]$ld.DeviceID).ToUpperInvariant()
            $ssd = $null
            if ($media.ContainsKey($id)) {
                $mt = $media[$id]
                if ($mt -eq 4 -or $mt -eq 5) { $ssd = $true } elseif ($mt -eq 3) { $ssd = $false }
            }
            [void]$disks.Add([ordered]@{ mount = $id; size_gb = Get-GB $ld.Size 1; free_gb = Get-GB $ld.FreeSpace 1; ssd = $ssd })
        }
    } catch { $disks = $null }

    # graphics
    $gpus = New-Object System.Collections.ArrayList
    $gpuOk = $false
    $smiRows = $null
    $smi = Find-Tool 'nvidia-smi' @((Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'))
    if ($smi) {
        $r = Invoke-Tool $smi '--query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits' 15000
        $withCc = $true
        if ($null -eq $r -or $r.code -ne 0) {
            $r = Invoke-Tool $smi '--query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits' 15000
            $withCc = $false
        }
        if ($null -ne $r -and $r.code -eq 0) {
            $smiRows = New-Object System.Collections.ArrayList
            foreach ($line in ($r.out -split "`r?`n")) {
                if ($line.Trim() -eq '') { continue }
                $f = @($line.Split(',') | ForEach-Object { $_.Trim() })
                $memGb = $null; $mib = 0.0
                if ($f.Count -ge 2 -and [double]::TryParse($f[1], [ref]$mib)) { $memGb = [math]::Round($mib / 1024.0, 1) }
                $cc = $null
                if ($withCc -and $f.Count -ge 4 -and $f[3] -match '^\d+\.\d+$') { $cc = $f[3] }
                $drv = $null; if ($f.Count -ge 3) { $drv = Get-Ascii $f[2] }
                [void]$smiRows.Add([ordered]@{ name = Get-Ascii $f[0]; vendor = 'nvidia'; mem_gb = $memGb; driver = $drv; cc = $cc })
            }
        }
    }
    try {
        $regMem = @{}
        $classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
        foreach ($k in @(Get-ChildItem -LiteralPath $classKey -ErrorAction SilentlyContinue)) {
            if ($k.PSChildName -notmatch '^\d{4}$') { continue }
            try {
                $pp = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction Stop
                $desc = [string]$pp.DriverDesc
                $q = $pp.'HardwareInformation.qwMemorySize'
                if ($desc -and $null -ne $q) { $regMem[$desc] = [double]$q }
            } catch { }
        }
        $vcs = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)
        $gpuOk = $true
        foreach ($vc in $vcs) {
            $pnp = [string]$vc.PNPDeviceID
            if ($pnp -notlike 'PCI\*') { continue }
            $vendor = 'other'
            if ($pnp -match 'VEN_10DE') { $vendor = 'nvidia' } elseif ($pnp -match 'VEN_1002') { $vendor = 'amd' } elseif ($pnp -match 'VEN_8086') { $vendor = 'intel' }
            if ($vendor -eq 'nvidia' -and $null -ne $smiRows) { continue }
            $name = Get-Ascii $vc.Name
            $memGb = $null
            if ($regMem.ContainsKey([string]$vc.Name)) { $memGb = Get-GB $regMem[[string]$vc.Name] 1 }
            elseif ($vc.AdapterRAM -and [double]$vc.AdapterRAM -gt 0) { $memGb = Get-GB $vc.AdapterRAM 1 }
            $drv = Get-Ascii $vc.DriverVersion
            if ($vendor -eq 'nvidia' -and $drv -match '^\d+\.\d+\.\d+\.\d+$') {
                $digits = ($drv -replace '\.', '')
                if ($digits.Length -ge 5) { $tail = $digits.Substring($digits.Length - 5); $drv = $tail.Substring(0, 3) + '.' + $tail.Substring(3) }
            }
            [void]$gpus.Add([ordered]@{ name = $name; vendor = $vendor; mem_gb = $memGb; driver = $drv; cc = $null })
        }
    } catch { }
    if ($null -ne $smiRows) { $ix = 0; foreach ($row in $smiRows) { $gpus.Insert($ix, $row); $ix++ }; $gpuOk = $true }
    if (-not $gpuOk) { $gpus = $null }

    # virtualisation
    $virt = [ordered]@{ firmware = $null; slat = $null; iommu = $null; hypervisor = $null }
    if ($cs) { $virt.hypervisor = [bool]$cs.HypervisorPresent }
    if ($procs.Count -gt 0) {
        $fw = $procs[0].VirtualizationFirmwareEnabled
        $sl = $procs[0].SecondLevelAddressTranslationExtensions
        if ($virt.hypervisor -eq $true -and $hw.virtual -ne $true) {
            # A running hypervisor hides these flags from the host; it could not run without them.
            $virt.firmware = $true; $virt.slat = $true
        } else {
            if ($null -ne $fw) { $virt.firmware = [bool]$fw }
            if ($null -ne $sl) { $virt.slat = [bool]$sl }
            if ($hw.virtual -eq $true) {
                if ($virt.firmware -ne $true) { $virt.firmware = $null }
                if ($virt.slat -ne $true) { $virt.slat = $null }
            }
        }
    }
    try {
        $dg = Get-CimInstance -Namespace 'root/Microsoft/Windows/DeviceGuard' -ClassName Win32_DeviceGuard -ErrorAction Stop
        if (@($dg.AvailableSecurityProperties) -contains 3) { $virt.iommu = $true }
    } catch { }

    # hypervisors
    $hyp = [ordered]@{ hyperv = $false; kvm = $false; proxmox = $false; virtualbox = $false; vmware = $false; libvirt = $false }
    try { if (Get-Service -Name 'vmms' -ErrorAction SilentlyContinue) { $hyp.hyperv = $true } } catch { $hyp.hyperv = $null }
    $vb = @()
    if ($env:VBOX_MSI_INSTALL_PATH) { $vb += (Join-Path $env:VBOX_MSI_INSTALL_PATH 'VBoxManage.exe') }
    $vb += (Join-Path $env:ProgramFiles 'Oracle\VirtualBox\VBoxManage.exe')
    foreach ($p in $vb) { if (Test-Path -LiteralPath $p -PathType Leaf) { $hyp.virtualbox = $true } }
    if (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Oracle\VirtualBox') { $hyp.virtualbox = $true }
    $pf86 = ${env:ProgramFiles(x86)}
    foreach ($p in @((Join-Path $env:ProgramFiles 'VMware\VMware Workstation\vmware.exe'), (Join-Path $env:ProgramFiles 'VMware\VMware Player\vmplayer.exe'))) {
        if (Test-Path -LiteralPath $p -PathType Leaf) { $hyp.vmware = $true }
    }
    if ($pf86) {
        foreach ($p in @((Join-Path $pf86 'VMware\VMware Workstation\vmware.exe'), (Join-Path $pf86 'VMware\VMware Player\vmplayer.exe'))) {
            if (Test-Path -LiteralPath $p -PathType Leaf) { $hyp.vmware = $true }
        }
    }

    # WSL2: registry and files only
    $wsl = [ordered]@{ present = $false; wsl2_distros = 0 }
    try {
        $n2 = 0
        foreach ($d in @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue)) {
            try { $dp = Get-ItemProperty -LiteralPath $d.PSPath -ErrorAction Stop; if ([int]$dp.Version -eq 2) { $n2++ } } catch { }
        }
        $wsl.wsl2_distros = $n2
        $svc = $null
        try { $svc = Get-Service -Name 'WslService' -ErrorAction SilentlyContinue } catch { }
        if ($n2 -gt 0 -or $svc -or (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'WSL\wslservice.exe') -PathType Leaf)) { $wsl.present = $true }
    } catch { $wsl.present = $null }

    # Docker (client version only; never contacts or starts a daemon)
    $docker = [ordered]@{ present = $false; client = $null; nvidia = $null }
    $dv = Get-ToolVersion 'docker' @() '--version'
    if ($dv -ne $false) { $docker.present = $true; if ($dv -ne 'present') { $docker.client = $dv } }

    # tools
    $tools = [ordered]@{
        tailscale = Get-ToolVersion 'tailscale' @((Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe')) 'version'
        claude    = Get-ToolVersion 'claude' @() '--version'
        codex     = Get-ToolVersion 'codex' @() '--version'
        gh        = Get-ToolVersion 'gh' @((Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe')) '--version'
    }

    # ports
    $ports = [ordered]@{ checked = $false; busy = @() }
    try {
        $want = @(8765, 8766, 8443, 8081, 8082, 7452)
        $listen = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        $busy = New-Object System.Collections.ArrayList
        foreach ($ep in $listen) { if (($want -contains $ep.Port) -and -not ($busy -contains $ep.Port)) { [void]$busy.Add([int]$ep.Port) } }
        $sorted = @($busy | Sort-Object)
        $ports.checked = $true; $ports.busy = $sorted
    } catch { }

    $facts = [ordered]@{ os = $os; hw = $hw; cpu = $cpu; mem = $mem; disks = $disks; gpus = $gpus; virt = $virt
                         hyp = $hyp; wsl = $wsl; docker = $docker; tools = $tools; ports = $ports; net = $null }
    return @{ facts = $facts; notes = $notes }
}

function Get-NetFacts {
    # Any HTTP answer, error codes and redirects included, counts as reachable.
    $net = [ordered]@{}
    $ProgressPreference = 'SilentlyContinue'
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    $sites = [ordered]@{
        github = 'https://github.com/'
        huggingface = 'https://huggingface.co/'
        ubuntu = 'http://archive.ubuntu.com/ubuntu/'
        nvidia = 'https://developer.download.nvidia.com/compute/cuda/repos/'
        tailscale = 'https://login.tailscale.com/'
    }
    foreach ($k in $sites.Keys) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $ok = $false; $code = $null
        try {
            $resp = Invoke-WebRequest -Uri $sites[$k] -Method Head -UseBasicParsing -UserAgent $UserAgent -TimeoutSec 8 -ErrorAction Stop
            $ok = $true; $code = [int]$resp.StatusCode
        } catch {
            $wr = $_.Exception.Response
            if ($null -ne $wr) { $ok = $true; try { $code = [int]$wr.StatusCode } catch { } }
        }
        $net[$k] = [ordered]@{ ok = $ok; code = $code; ms = [int]$sw.ElapsedMilliseconds }
    }
    $signed = $null
    $gh = Find-Tool 'gh' @((Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'))
    if ($gh) {
        $r = Invoke-Tool $gh 'auth status --hostname github.com' 20000
        if ($null -ne $r) { $signed = ($r.code -eq 0) }
    } else { $signed = $false }
    $net['gh_signed_in'] = $signed
    return $net
}

# ---------------------------------------------------------------- verdict engine (contract C1 rules)
function Test-Num($x) {
    return ($x -is [int] -or $x -is [long] -or $x -is [double] -or $x -is [decimal] -or $x -is [single] -or
            $x -is [int16] -or $x -is [byte] -or $x -is [uint16] -or $x -is [uint32] -or $x -is [uint64])
}
function Test-Same($a, $b) {
    if ((Test-Num $a) -and (Test-Num $b)) { if ([double]$a -eq [double]$b) { return 1 }; return 0 }
    if (($a -is [bool]) -and ($b -is [bool])) { if ($a -eq $b) { return 1 }; return 0 }
    if (($a -is [string]) -and ($b -is [string])) { if ($a -ceq $b) { return 1 }; return 0 }
    return 0
}
function Get-Fact([string]$n) {
    $x = $null
    if ($script:D.ContainsKey($n)) { $x = $script:D[$n] } elseif ($script:F.ContainsKey($n)) { $x = $script:F[$n] }
    if ($x -is [array]) { return ,$x }
    return $x
}
function Test-Triple($c) {
    $name = [string]$c[0]; $op = [string]$c[1]
    $v = Get-Fact $name
    if ($op -eq 'present') { if ($null -ne $v) { return 1 }; return 0 }
    if ($op -eq 'absent') { if ($null -eq $v) { return 1 }; return 0 }
    if ($op -eq 'nottrue') { if (($v -is [bool]) -and $v) { return 0 }; return 1 }
    if ($op -eq 'notfalse') { if (($v -is [bool]) -and (-not $v)) { return 0 }; return 1 }
    if ($null -eq $v) { return -1 }
    $w = $null
    if ($c.Count -ge 3) { $w = $c[2] }
    if ($op -eq 'eq') { return (Test-Same $v $w) }
    if ($op -eq 'ne') { return (1 - (Test-Same $v $w)) }
    if ($op -eq 'in' -or $op -eq 'nin') {
        $hit = 0
        foreach ($x in @($w)) { if ((Test-Same $v $x) -eq 1) { $hit = 1; break } }
        if ($op -eq 'in') { return $hit }
        return (1 - $hit)
    }
    if ($op -eq 'ge' -or $op -eq 'gt' -or $op -eq 'le' -or $op -eq 'lt') {
        if (-not (Test-Num $v) -or -not (Test-Num $w)) { return -1 }
        $dv = [double]$v; $dw = [double]$w
        if ($op -eq 'ge') { if ($dv -ge $dw) { return 1 }; return 0 }
        if ($op -eq 'gt') { if ($dv -gt $dw) { return 1 }; return 0 }
        if ($op -eq 'le') { if ($dv -le $dw) { return 1 }; return 0 }
        if ($dv -lt $dw) { return 1 }; return 0
    }
    if ($op -eq 're') {
        if (-not ($v -is [string])) { return -1 }
        if ([regex]::IsMatch($v.ToLowerInvariant(), [string]$w)) { return 1 }
        return 0
    }
    return -1
}
function Test-All($list) {
    $u = $false
    foreach ($c in @($list)) {
        $r = Test-Cond $c
        if ($r -eq 0) { return 0 }
        if ($r -eq -1) { $u = $true }
    }
    if ($u) { return -1 }
    return 1
}
function Test-Cond($c) {
    if ($c -is [System.Management.Automation.PSCustomObject]) {
        $any = $c.PSObject.Properties['any']
        if ($any) {
            $u = $false
            foreach ($x in @($any.Value)) { $r = Test-Cond $x; if ($r -eq 1) { return 1 }; if ($r -eq -1) { $u = $true } }
            if ($u) { return -1 }
            return 0
        }
        $all = $c.PSObject.Properties['all']
        if ($all) { return (Test-All $all.Value) }
        return -1
    }
    if ($c -is [array]) {
        if ($c.Count -ge 2 -and ($c[0] -is [string])) { return (Test-Triple $c) }
        return (Test-All $c)
    }
    return -1
}
function Add-Flat($h, [string]$prefix, $o) {
    if ($o -is [System.Management.Automation.PSCustomObject]) {
        foreach ($pr in $o.PSObject.Properties) {
            $k = $pr.Name
            if ($prefix) { $k = $prefix + '.' + $pr.Name }
            Add-Flat $h $k $pr.Value
        }
        return
    }
    if ($prefix) { $h[$prefix] = $o }
}
function Get-Alloc1($range, [double]$avail) {
    $mx = [int]$range[1]; $mn = [int]$range[0]
    if ($mx -le $avail) { return $mx }
    if ($mn -le $avail) { return $mn }
    return $null
}
function Get-Alloc2($wr, $fr, [double]$avail) {
    $tries = @(@([int]$wr[1], [int]$fr[1]), @([int]$wr[1], [int]$fr[0]), @([int]$wr[0], [int]$fr[1]), @([int]$wr[0], [int]$fr[0]))
    foreach ($t in $tries) { if (($t[0] + $t[1]) -le $avail) { return ,$t } }
    return $null
}

function New-Verdict($factsObj, $M) {
    # fact names are case-sensitive, as in the shell version
    $script:F = New-Object System.Collections.Hashtable -ArgumentList ([System.StringComparer]::Ordinal)
    $script:D = New-Object System.Collections.Hashtable -ArgumentList ([System.StringComparer]::Ordinal)
    Add-Flat $script:F '' $factsObj

    # processor class
    $cpuClass = $null
    foreach ($cl in @($M.cpu_classes)) { if ((Test-All $cl.when) -eq 1) { $cpuClass = $cl; break } }
    if ($cpuClass) { $script:D['cpu.class'] = [string]$cpuClass.id; $script:D['cpu.ws'] = [string]$cpuClass.workstation; $script:D['cpu.forge'] = [string]$cpuClass.forge }
    else { $script:D['cpu.class'] = $null; $script:D['cpu.ws'] = $null; $script:D['cpu.forge'] = $null }

    # graphics: class every NVIDIA card, keep the best
    $gpus = Get-Fact 'gpus'
    $best = $null; $bestScore = -1; $bestMem = -1.0; $anyNv = $false
    if ($null -ne $gpus) {
        foreach ($g in @($gpus)) {
            if ([string]$g.vendor -ne 'nvidia') { continue }
            $anyNv = $true
            $nm = ''
            if ($null -ne $g.name) { $nm = ([string]$g.name).ToLowerInvariant() }
            $cls = $null
            foreach ($gc in @($M.gpu_classes)) {
                foreach ($pat in @($gc.names)) { if ($nm -ne '' -and [regex]::IsMatch($nm, [string]$pat)) { $cls = $gc; break } }
                if ($cls) { break }
            }
            if (-not $cls -and $null -ne $g.cc) {
                $ccs = [string]$g.cc
                foreach ($gc in @($M.gpu_classes)) { if (@($gc.cc) -contains $ccs) { $cls = $gc; break } }
            }
            $forge = $null
            if ($cls) { $forge = [string]$cls.forge }
            $score = 1
            if ($forge -eq 'yes') { $score = 3 } elseif ($forge -eq 'experimental') { $score = 2 } elseif ($forge -eq 'no') { $score = 0 }
            $memv = -1.0
            if (Test-Num $g.mem_gb) { $memv = [double]$g.mem_gb }
            if ($score -gt $bestScore -or ($score -eq $bestScore -and $memv -gt $bestMem)) {
                $best = @{ g = $g; cls = $cls; forge = $forge; mem = $g.mem_gb }
                $bestScore = $score; $bestMem = $memv
            }
        }
    }
    $gpuNv = $null; $gpuUsable = $null
    if ($null -ne $gpus) { $gpuNv = $anyNv; if (-not $anyNv) { $gpuUsable = $false } }
    $gClass = $null; $gForge = $null; $gTei = $null; $gUp = $null; $gMem = $null; $gTier = $null; $gDrv = $null
    if ($best) {
        if ($best.cls) { $gClass = [string]$best.cls.id; $gTei = $best.cls.tei; $gUp = [string]$best.cls.upstream } else { $gClass = 'unknown' }
        $gForge = $best.forge
        if ($gForge -eq 'yes' -or $gForge -eq 'experimental') { $gpuUsable = $true } elseif ($gForge -eq 'no') { $gpuUsable = $false } else { $gpuUsable = $null }
        if (Test-Num $best.mem) { $gMem = $best.mem }
        if ($null -ne $best.g.driver) { $gDrv = [string]$best.g.driver }
        if ($null -ne $gMem) {
            foreach ($tr in @($M.gpu_mem_tiers)) { if ([double]$gMem -ge [double]$tr.min_gb) { $gTier = [string]$tr.id; break } }
        }
    }
    $script:D['gpu.nvidia'] = $gpuNv; $script:D['gpu.usable'] = $gpuUsable; $script:D['gpu.class'] = $gClass
    $script:D['gpu.forge'] = $gForge; $script:D['gpu.tei'] = $gTei; $script:D['gpu.upstream'] = $gUp
    $script:D['gpu.mem_gb'] = $gMem; $script:D['gpu.mem_tier'] = $gTier; $script:D['gpu.driver'] = $gDrv

    # memory basis, sizing threads, tier
    $basis = $null
    $inst = Get-Fact 'mem.installed_gb'; $tot = Get-Fact 'mem.total_gb'
    # doubles, not Int32: a facts file may hold any number (the shell version's awk has no limit either)
    if ((Test-Num $inst) -and [double]$inst -gt 0) { $basis = [math]::Floor([double]$inst + 0.5) }
    elseif (Test-Num $tot) { $basis = [math]::Ceiling([double]$tot / 2.0) * 2 }
    $script:D['mem.basis_gb'] = $basis
    $st = $null
    $ec = Get-Fact 'cpu.e_cores'; $pt = Get-Fact 'cpu.p_threads'; $th = Get-Fact 'cpu.threads'
    if ((Test-Num $ec) -and [double]$ec -gt 0 -and (Test-Num $pt)) { $st = [math]::Truncate([double]$pt) } elseif (Test-Num $th) { $st = [math]::Truncate([double]$th) }
    $script:D['size.threads'] = $st
    $S = $M.sizing
    $tier = $null; $tierObj = $null
    $sizeBoth = $null; $wsFits = $null; $forgeFits = $null
    if ($null -ne $st -and $null -ne $basis) {
        foreach ($t in @($S.tiers)) { if ($st -ge [int]$t.min_threads -and $basis -ge [int]$t.min_mem_gb) { $tier = [int]$t.tier; $tierObj = $t } }
        $at = $st - [int]$S.host_keeps.threads; $am = $basis - [int]$S.host_keeps.mem_gb
        $sizeBoth = ($tier -ge 1)
        $wsFits = ($at -ge [int]$S.ws_alone_min.threads -and $am -ge [int]$S.ws_alone_min.mem_gb)
        $forgeFits = ($at -ge [int]$S.forge_alone_min.threads -and $am -ge [int]$S.forge_alone_min.mem_gb)
    }
    $script:D['size.tier'] = $tier; $script:D['size.both'] = $sizeBoth; $script:D['size.ws_fits'] = $wsFits; $script:D['size.forge_fits'] = $forgeFits

    # disks
    $disks = Get-Fact 'disks'
    $dfree = $null; $dssd = $null
    if ($null -ne $disks) {
        $nTrue = 0; $nFalse = 0; $n = 0
        foreach ($dk in @($disks)) {
            $n++
            if ((Test-Num $dk.free_gb) -and ($null -eq $dfree -or [double]$dk.free_gb -gt [double]$dfree)) { $dfree = $dk.free_gb }
            if ($dk.ssd -is [bool]) { if ($dk.ssd) { $nTrue++ } else { $nFalse++ } }
        }
        if ($nTrue -gt 0) { $dssd = $true } elseif ($n -gt 0 -and $nFalse -eq $n) { $dssd = $false }
    }
    $script:D['disk.free_gb'] = $dfree; $script:D['disk.ssd'] = $dssd

    # ports
    $checked = Get-Fact 'ports.checked'
    $busyList = Get-Fact 'ports.busy'
    $busyCount = $null
    if ($checked -eq $true) { $busyCount = 0 }
    foreach ($pp in @($M.ports)) {
        $key = 'port.' + [string]$pp.port
        if ($checked -eq $true) {
            $isBusy = $false
            foreach ($b in @($busyList)) { if ((Test-Num $b) -and [double]$b -eq [double]$pp.port) { $isBusy = $true } }
            $script:D[$key] = $isBusy
            if ($isBusy) { $busyCount++ }
        } else { $script:D[$key] = $null }
    }
    $script:D['ports.busy_count'] = $busyCount

    # known hardware profile
    $hwp = $null
    foreach ($h in @($M.hw_profiles)) { if ((Test-All $h.when) -eq 1) { $hwp = [string]$h.id; break } }
    $script:D['hw.profile'] = $hwp

    # profiles
    $entries = New-Object System.Collections.ArrayList
    $ruled = New-Object System.Collections.ArrayList
    $idx = 0
    foreach ($p in @($M.profiles)) {
        $idx++
        if ((Test-All $p.match) -ne 1) { continue }
        $missing = New-Object System.Collections.ArrayList
        $unknown = New-Object System.Collections.ArrayList
        $auto = New-Object System.Collections.ArrayList
        $confirm = New-Object System.Collections.ArrayList
        $hard = New-Object System.Collections.ArrayList
        foreach ($rid in @($p.requires)) {
            $req = $M.requirements.PSObject.Properties[[string]$rid].Value
            $r = Test-All $req.when
            $kind = [string]$req.kind
            if ($r -eq 1) { continue }
            if ($kind -eq 'auto') { [void]$auto.Add([string]$rid); continue }
            if ($r -eq 0) { [void]$missing.Add([string]$rid); if ($kind -eq 'hardware') { [void]$hard.Add([string]$rid) } }
            elseif ($kind -eq 'confirm') { [void]$confirm.Add([string]$rid) }
            else { [void]$unknown.Add([string]$rid) }
        }
        if ($hard.Count -gt 0) { [void]$ruled.Add([ordered]@{ p = [string]$p.id; why = @($hard) }); continue }
        # fit, and its place in the order: ready 0, unconfirmed 1, changes 2 (fit comes before rank)
        $fit = 'ready'; $fitOrder = 0
        if ($missing.Count -gt 0) { $fit = 'changes'; $fitOrder = 2 } elseif ($unknown.Count -gt 0) { $fit = 'unconfirmed'; $fitOrder = 1 }
        # machines (single machine first): 2 when the layout needs another machine for the
        # Workstation or the Forge. Only a JSON number of 2 or more under the exact name "machines" counts,
        # as in the shell version; absent, 1, or anything else is one machine.
        $machines = 1
        foreach ($mp in @($p.PSObject.Properties | Where-Object { $_.Name -ceq 'machines' })) {
            if ((Test-Num $mp.Value) -and [double]$mp.Value -ge 2) { $machines = 2 }
        }
        # sizing
        $ws = $null; $fo = $null
        $kindSize = [string]$p.size
        if ($null -ne $tier) {
            $at = $st - [int]$S.host_keeps.threads; $am = $basis - [int]$S.host_keeps.mem_gb
            if ($kindSize -eq 'both' -and $tier -ge 1) {
                $t2 = Get-Alloc2 $tierObj.ws.threads $tierObj.forge.threads $at
                $m2 = Get-Alloc2 $tierObj.ws.mem_gb $tierObj.forge.mem_gb $am
                if ($null -ne $t2 -and $null -ne $m2) { $ws = @($t2[0], $m2[0]); $fo = @($t2[1], $m2[1]) }
            }
            if ($kindSize -eq 'ws' -or $kindSize -eq 'each') {
                $a1 = Get-Alloc1 $tierObj.ws.threads $at; $b1 = Get-Alloc1 $tierObj.ws.mem_gb $am
                if ($null -ne $a1 -and $null -ne $b1) { $ws = @($a1, $b1) }
            }
            if ($kindSize -eq 'forge' -or $kindSize -eq 'each') {
                if ($null -ne $tierObj.forge) { $ftr = $tierObj.forge.threads; $fmr = $tierObj.forge.mem_gb }
                else { $ftr = @($S.forge_alone_min.threads, $S.forge_alone_min.threads); $fmr = @($S.forge_alone_min.mem_gb, $S.forge_alone_min.mem_gb) }
                $a1 = Get-Alloc1 $ftr $at; $b1 = Get-Alloc1 $fmr $am
                if ($null -ne $a1 -and $null -ne $b1) { $fo = @($a1, $b1) }
            }
        }
        $alt = 0; if ($machines -ge 2) { $alt = 1 }
        [void]$entries.Add(@{ idx = $idx; alt = $alt; fitorder = $fitOrder; rank = [int]$p.rank; nmiss = $missing.Count
            e = [ordered]@{ p = [string]$p.id; target = [string]$p.target; status = [string]$p.status; machines = $machines
                            fit = $fit; missing = @($missing); unknown = @($unknown); auto = @($auto); confirm = @($confirm)
                            size = [ordered]@{ ws = $ws; forge = $fo } } })
    }
    # one-machine profiles before the alternatives that need another machine (single machine first), then fit, then
    # the profile's rank, then fewer missing, then matrix order (matrix "ranking")
    $sorted = @($entries | Sort-Object -Property @{ Expression = { $_.alt } }, @{ Expression = { $_.fitorder } }, @{ Expression = { $_.rank } }, @{ Expression = { $_.nmiss } }, @{ Expression = { $_.idx } })
    $ranked = New-Object System.Collections.ArrayList
    foreach ($se in ($sorted | Select-Object -First 3)) { [void]$ranked.Add($se.e) }
    $bestId = $null; $fitAll = 'none'
    if ($ranked.Count -gt 0) { $bestId = $ranked[0].p; $fitAll = $ranked[0].fit }

    $warn = New-Object System.Collections.ArrayList
    foreach ($w in @($M.warnings)) { if ((Test-All $w.when) -eq 1) { [void]$warn.Add([string]$w.id) } }

    return [ordered]@{
        matrix = [string]$M.matrix_version
        best = $bestId
        fit = $fitAll
        cpu = [ordered]@{ class = $script:D['cpu.class']; ws = $script:D['cpu.ws']; forge = $script:D['cpu.forge'] }
        gpu = [ordered]@{ class = $gClass; forge = $gForge; tei = $gTei; upstream = $gUp; mem_gb = $gMem; mem_tier = $gTier }
        size = [ordered]@{ threads = $st; mem_gb = $basis; tier = $tier }
        ranked = @($ranked)
        ruled_out = @($ruled)
        warnings = @($warn)
    }
}

# ---------------------------------------------------------------- the page for a person
function Format-Val($v) { if ($null -eq $v) { return 'unavailable' }; if ($v -is [bool]) { if ($v) { return 'yes' }; return 'no' }; return [string]$v }
function Format-Tool($v) { if ($null -eq $v) { return 'unavailable' }; if ($v -is [bool] -and -not $v) { return 'not found' }; return [string]$v }
function Get-ReqText($M, [string]$id) {
    $r = $M.requirements.PSObject.Properties[$id]
    if (-not $r) { return $id }
    $t = [string]$r.Value.need
    if ($r.Value.do) { $t = $t + ': ' + [string]$r.Value.do }
    return $t
}
function Write-Page($fo, $verdict, $M, $notes, [string]$modeLabel) {
    Out-Line ('Deep32 inventory ' + $ToolVersion + ' (' + $modeLabel + ')')
    $o = $fo.os
    if ($o) { Out-Line ('System     ' + (Format-Val $o.name) + ', version ' + (Format-Val $o.version) + ', build ' + (Format-Val $o.build) + ', ' + (Format-Val $o.arch)) }
    $h = $fo.hw
    if ($h) {
        $vm = 'no'; if ($h.virtual -eq $true) { $vm = 'yes' } elseif ($null -eq $h.virtual) { $vm = 'unknown' }
        Out-Line ('Machine    ' + (Format-Val $h.vendor) + ' ' + (Format-Val $h.model) + '; itself a VM: ' + $vm)
    }
    $c = $fo.cpu
    if ($c) {
        Out-Line ('Processor  ' + (Format-Val $c.model) + ' (' + (Format-Val $c.vendor) + ', family ' + (Format-Val $c.family) + ' model ' + (Format-Val $c.model_id) + ' stepping ' + (Format-Val $c.stepping) + ')')
        $line = '           ' + (Format-Val $c.cores) + ' cores, ' + (Format-Val $c.threads) + ' threads'
        if ($null -ne $c.p_cores) { $line += ' (' + $c.p_cores + ' fast cores, ' + $c.p_threads + ' fast threads, ' + $c.e_cores + ' efficient cores)' }
        $line += '; AVX ' + (Format-Val $c.avx) + ', AVX2 ' + (Format-Val $c.avx2) + ', AVX-512F ' + (Format-Val $c.avx512f)
        if ($c.gen) { $line += '; ' + $c.gen }
        Out-Line $line
    }
    $mm = $fo.mem
    if ($mm) { Out-Line ('Memory     ' + (Format-Val $mm.installed_gb) + ' GB installed, ' + (Format-Val $mm.total_gb) + ' GB visible, ' + (Format-Val $mm.free_gb) + ' GB free') }
    if ($null -eq $fo.disks) { Out-Line 'Disks      unavailable' }
    else {
        $parts = @()
        foreach ($d in @($fo.disks)) {
            $kind = ''
            if ($d.ssd -eq $true) { $kind = ', solid-state' } elseif ($d.ssd -eq $false) { $kind = ', spinning' }
            $parts += ((Format-Val $d.mount) + ' ' + (Format-Val $d.size_gb) + ' GB, ' + (Format-Val $d.free_gb) + ' GB free' + $kind)
        }
        if ($parts.Count -eq 0) { $parts = @('none found') }
        Out-Line ('Disks      ' + ($parts -join '; '))
    }
    if ($null -eq $fo.gpus) { Out-Line 'Graphics   unavailable' }
    else {
        $first = $true
        foreach ($g in @($fo.gpus)) {
            $lbl = '           '; if ($first) { $lbl = 'Graphics   ' }
            $line = $lbl + (Format-Val $g.name) + ' (' + (Format-Val $g.vendor) + '), ' + (Format-Val $g.mem_gb) + ' GB, driver ' + (Format-Val $g.driver)
            if ($g.cc) { $line += ', compute ' + $g.cc }
            Out-Line $line; $first = $false
        }
        if ($first) { Out-Line 'Graphics   none found' }
    }
    $v = $fo.virt
    if ($v) { Out-Line ('Virtual    in firmware ' + (Format-Val $v.firmware) + ', SLAT ' + (Format-Val $v.slat) + ', IOMMU ' + (Format-Val $v.iommu) + ', hypervisor running ' + (Format-Val $v.hypervisor)) }
    $y = $fo.hyp
    if ($y) {
        $w = $fo.wsl
        $wtxt = 'unavailable'; if ($w) { $wtxt = (Format-Val $w.present) + ' (' + (Format-Val $w.wsl2_distros) + ' WSL2 distributions)' }
        Out-Line ('Hypervisor Hyper-V ' + (Format-Val $y.hyperv) + ', KVM ' + (Format-Val $y.kvm) + ', Proxmox ' + (Format-Val $y.proxmox) + ', VirtualBox ' + (Format-Val $y.virtualbox) + ', VMware ' + (Format-Val $y.vmware) + '; WSL2 ' + $wtxt)
    }
    $dk = $fo.docker
    if ($dk) { Out-Line ('Docker     ' + (Format-Val $dk.present) + ', client ' + (Format-Val $dk.client) + ', NVIDIA runtime ' + (Format-Val $dk.nvidia)) }
    $t = $fo.tools
    if ($t) { Out-Line ('Tools      Tailscale ' + (Format-Tool $t.tailscale) + '; Claude Code ' + (Format-Tool $t.claude) + '; Codex ' + (Format-Tool $t.codex) + '; GitHub CLI ' + (Format-Tool $t.gh)) }
    $pp = $fo.ports
    if ($pp) {
        if ($pp.checked -ne $true) { Out-Line 'Ports      unavailable' }
        elseif (@($pp.busy).Count -eq 0) { Out-Line 'Ports      8765, 8766, 8443, 8081, 8082, 7452 all free' }
        else {
            $b = @()
            foreach ($n in @($pp.busy)) {
                $use = ''
                foreach ($q in @($M.ports)) { if ((Test-Num $n) -and [double]$q.port -eq [double]$n) { $use = ' (' + [string]$q.use + ')' } }
                $b += ([string]$n + $use)
            }
            Out-Line ('Ports      in use: ' + ($b -join ', '))
        }
    }
    if ($null -ne $fo.net) {
        $nn = @()
        foreach ($pr in $fo.net.PSObject.Properties) {
            if ($pr.Name -eq 'gh_signed_in') { continue }
            $s = 'no answer'; if ($pr.Value.ok) { $s = 'answers in ' + $pr.Value.ms + ' ms' }
            $nn += ($pr.Name + ' ' + $s)
        }
        Out-Line ('Network    ' + ($nn -join '; ') + '; GitHub CLI signed in ' + (Format-Val $fo.net.gh_signed_in))
    }
    Out-Line ''
    if ($null -eq $verdict) { Out-Line 'Verdict    unavailable (no matrix)'; return }
    $pmap = @{}
    foreach ($p in @($M.profiles)) { $pmap[[string]$p.id] = $p }
    $cpuTxt = 'unknown'; if ($verdict.cpu.class) { $cpuTxt = $verdict.cpu.class + ' (Workstation ' + $verdict.cpu.ws + ', Forge ' + $verdict.cpu.forge + ')' }
    $gpuTxt = 'no NVIDIA card found, or none readable'
    if ($verdict.gpu.class) { $gpuTxt = $verdict.gpu.class + ' (Forge ' + (Format-Val $verdict.gpu.forge) + ', embeddings image ' + (Format-Val $verdict.gpu.tei) + ', upstream ' + (Format-Val $verdict.gpu.upstream) + ', memory tier ' + (Format-Val $verdict.gpu.mem_tier) + ')' }
    Out-Line ('Classes    processor ' + $cpuTxt + '; card ' + $gpuTxt)
    Out-Line ('Sizing     ' + (Format-Val $verdict.size.threads) + ' threads and ' + (Format-Val $verdict.size.mem_gb) + ' GB count; tier ' + (Format-Val $verdict.size.tier))
    if (-not $verdict.best) { Out-Line 'Verdict    no profile fits this machine'; return }
    $first = $true
    foreach ($e in @($verdict.ranked)) {
        $p = $pmap[$e.p]
        $lbl = 'Next       '; if ($first) { $lbl = 'Verdict    ' }
        $two = ''; if ($e.machines -ge 2) { $two = '; needs another machine' }
        Out-Line ($lbl + $e.p + ': ' + [string]$p.name + ' (target ' + $e.target + ', ' + $e.status + '); fit: ' + $e.fit + $two)
        $sz = @()
        if ($null -ne $e.size.ws) { $sz += ('Workstation ' + $e.size.ws[0] + ' threads, ' + $e.size.ws[1] + ' GB') }
        if ($null -ne $e.size.forge) { $sz += ('Forge ' + $e.size.forge[0] + ' threads, ' + $e.size.forge[1] + ' GB') }
        if ($sz.Count -gt 0) { Out-Line ('             size: ' + ($sz -join '; ')) }
        if ($first) {
            Out-Line ('             parts: Workstation ' + [string]$p.parts.workstation + '; Forge ' + [string]$p.parts.forge + '; relay ' + [string]$p.parts.relay)
            foreach ($id in @($e.missing)) { Out-Line ('             missing: ' + (Get-ReqText $M $id)) }
            foreach ($id in @($e.unknown)) { Out-Line ('             not checked: ' + (Get-ReqText $M $id)) }
            foreach ($id in @($e.auto)) { Out-Line ('             installer: ' + (Get-ReqText $M $id)) }
            foreach ($id in @($e.confirm)) { Out-Line ('             to confirm: ' + (Get-ReqText $M $id)) }
        } elseif (@($e.missing).Count -gt 0) { Out-Line ('             missing: ' + (@($e.missing) -join ', ')) }
        $first = $false
    }
    foreach ($r in @($verdict.ruled_out)) { Out-Line ('Ruled out  ' + $r.p + ': ' + (@($r.why) -join ', ')) }
    foreach ($wid in @($verdict.warnings)) {
        foreach ($w in @($M.warnings)) { if ([string]$w.id -eq $wid) { Out-Line ('Note       ' + [string]$w.text) } }
    }
    foreach ($n in @($notes)) { Out-Line ('Note       ' + $n) }
}

# ---------------------------------------------------------------- modes
function Read-Matrix([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $m = (Read-TextFile $path) | ConvertFrom-Json
    # Case-sensitive, as in the shell version: the member is "schema" exactly and its value the string
    # "deep32.matrix.v1" exactly ("Schema" or "Deep32.Matrix.V1" is not a matrix).
    $schemaOk = $false
    if ($m -is [System.Management.Automation.PSCustomObject]) {
        foreach ($sp in @($m.PSObject.Properties | Where-Object { $_.Name -ceq 'schema' })) {
            if (($sp.Value -is [string]) -and -not ($sp.Value -cne 'deep32.matrix.v1')) { $schemaOk = $true }
        }
    }
    if (-not $schemaOk) { throw ('not a deep32 install matrix: ' + $path) }
    return $m
}
function Get-UtcStamp { return [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') }

function Invoke-Report {
    $notes = New-Object System.Collections.ArrayList
    $M = $null; $matrixErr = $null
    try { $M = Read-Matrix $MatrixPath } catch { $matrixErr = $_.Exception.Message }
    if ($null -eq $M -and $null -eq $matrixErr) { $matrixErr = 'install matrix not found: ' + $MatrixPath }

    if ($null -ne $FactsPath) {
        if (-not (Test-Path -LiteralPath $FactsPath -PathType Leaf)) { Write-Err ('facts file not found: ' + $FactsPath); return 2 }
        $doc = $null
        try { $doc = (Read-TextFile $FactsPath) | ConvertFrom-Json -ErrorAction Stop } catch { $doc = $null }
        # the same rule as the shell version: a JSON object whose member "facts" (exact case) is an object
        $fo = $null
        if ($doc -is [System.Management.Automation.PSCustomObject]) {
            $fp = @($doc.PSObject.Properties | Where-Object { $_.Name -ceq 'facts' })
            if ($fp.Count -gt 0 -and ($fp[0].Value -is [System.Management.Automation.PSCustomObject])) { $fo = $fp[0].Value }
        }
        if ($null -eq $fo) { Write-Err 'the facts are not a JSON object with a "facts" member'; return 1 }
        [void]$notes.Add('facts read from a file; no probe ran')
    } else {
        $g = Get-Facts
        foreach ($n in $g.notes) { [void]$notes.Add($n) }
        if ($Net) { $g.facts.net = Get-NetFacts }
        $fo = ($g.facts | ConvertTo-Json -Depth 12 -Compress) | ConvertFrom-Json
    }
    $verdict = $null
    if ($null -ne $M) { $verdict = New-Verdict $fo $M } else { [void]$notes.Add($matrixErr) }
    if ($Json) {
        $out = [ordered]@{ schema = $Schema; tool = $ToolVersion; impl = 'ps1'; mode = 'report'; at = (Get-UtcStamp)
                           facts = $fo; verdict = $verdict; notes = @($notes) }
        if ($null -eq $verdict) { $out.verdict = [ordered]@{ error = 'matrix-unavailable' } }
        Out-Line (ConvertTo-AsciiJson $out 14)
    } else {
        Write-Page $fo $verdict $M $notes 'report'
    }
    if ($null -eq $M) { Write-Err $matrixErr; return 1 }
    return 0
}

function Test-GuardFile([string]$path) {
    # $null when the file is not there or its top-level "mode" is a string other than "on" (any case);
    # 'gaming-mode' when it is "on"; 'gaming-state-unreadable' for anything else (the safe side).
    if (-not $path) { return $null }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $g = $null
    try { $g = (Read-TextFile $path) | ConvertFrom-Json -ErrorAction Stop } catch { return 'gaming-state-unreadable' }
    if (-not ($g -is [System.Management.Automation.PSCustomObject])) { return 'gaming-state-unreadable' }
    $mp = @($g.PSObject.Properties | Where-Object { $_.Name -ceq 'mode' })
    if ($mp.Count -eq 0 -or -not ($mp[0].Value -is [string])) { return 'gaming-state-unreadable' }
    if (([string]$mp[0].Value).ToLowerInvariant() -eq 'on') { return 'gaming-mode' }
    return $null
}
function Test-Gaming {
    # Returns $null when the bench may run, else the reason it may not. The DEEP32_GUARD_FILE state
    # file is read when that variable is set; --guard-file adds one more.
    $r = Test-GuardFile $GuardFile
    if ($r) { return $r }
    return (Test-GuardFile $GuardExtra)
}

function Invoke-BenchQuick {
    $why = Test-Gaming
    if ($why) {
        if ($Json) { Out-Line (ConvertTo-AsciiJson ([ordered]@{ schema = $Schema; tool = $ToolVersion; impl = 'ps1'; mode = 'bench-quick'; at = (Get-UtcStamp); refused = $why }) 3) }
        else { Out-Line ('Refused: ' + $why + ' (the bench does not run while gaming mode is on or its state cannot be read)') }
        return 3
    }
    if ($GuardOnly) {
        if ($Json) { Out-Line (ConvertTo-AsciiJson ([ordered]@{ schema = $Schema; tool = $ToolVersion; impl = 'ps1'; mode = 'bench-quick'; at = (Get-UtcStamp); guard = 'clear' }) 3) }
        else { Out-Line 'Guard      clear (test hook: nothing was measured)' }
        return 0
    }
    $notes = New-Object System.Collections.ArrayList
    $total = [double]$Seconds * 1000.0
    $msCpu = [int]($total * 0.15); $msMem = [int]($total * 0.10)
    $msW = [int]($total * 0.20); $msR = [int]($total * 0.10); $msRR = [int]($total * 0.15); $msRW = [int]($total * 0.15)
    try { if (-not ('D32InvBench1' -as [type])) { Add-Type -TypeDefinition $BenchSource -Language CSharp -ErrorAction Stop } }
    catch { Write-Err ('cannot compile the bench: ' + $_.Exception.Message); return 1 }
    $threads = [Environment]::ProcessorCount

    $cpuRes = [D32InvBench1]::Cpu($msCpu, $threads)
    $cpu = [ordered]@{ st_mops = [math]::Round($cpuRes[0], 1); mt_mops = [math]::Round($cpuRes[1], 1); threads = $threads
                       scaling = [math]::Round($cpuRes[1] / [math]::Max($cpuRes[0], 0.001), 2) }

    $memMb = 256; if ($Seconds -lt 60) { $memMb = 128 }
    try {
        $osCim = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        $freeMb = [double]$osCim.FreePhysicalMemory / 1024.0
        $cap = [int]([math]::Floor($freeMb * 0.05 / 2.0))
        if ($cap -lt $memMb) { $memMb = [math]::Max(16, $cap) }
    } catch { }
    $memRes = [D32InvBench1]::MemCopy($msMem, $memMb)
    $mem = [ordered]@{ copy_gbs = [math]::Round($memRes, 2); buffer_mb = $memMb }

    $tmpDir = [System.IO.Path]::GetTempPath()
    $disk = [ordered]@{ where = 'temp'; free_gb = $null; file_mb = $null; seq_write_mbs = $null; seq_read_mbs = $null
                        rand_read_iops = $null; rand_write_iops = $null; skipped = $null }
    try {
        $root = [System.IO.Path]::GetPathRoot($tmpDir)
        $di = New-Object System.IO.DriveInfo($root)
        $free = [double]$di.AvailableFreeSpace
        $disk.free_gb = Get-GB $free 1
        $want = 1073741824.0; if ($Seconds -lt 60) { $want = 268435456.0 }
        $cap = [math]::Floor($free * 0.05 / 1048576.0) * 1048576.0
        $size = [math]::Min($want, $cap)
        if (($free - $size) -lt 10737418240.0) { $disk.skipped = 'less than 10 GB would stay free' }
        elseif ($size -lt 67108864.0) { $disk.skipped = 'too little room for a test file' }
        else {
            $path = Join-Path $tmpDir ('deep32-bench-' + $PID + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8) + '.tmp')
            try {
                $d = [D32InvBench1]::Disk($path, [long]$size, $msW, $msR, $msRR, $msRW)
                $disk.file_mb = [int]($d[0] / 1048576.0)
                $disk.seq_write_mbs = [math]::Round($d[1], 1); $disk.seq_read_mbs = [math]::Round($d[2], 1)
                $disk.rand_read_iops = [int]$d[3]; $disk.rand_write_iops = [int]$d[4]
            } finally {
                if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
            }
            if ($null -ne $disk.rand_read_iops -and $disk.rand_read_iops -lt 500) { [void]$notes.Add('random reads look like a spinning disk; solid-state storage is strongly recommended') }
        }
    } catch { $disk.skipped = 'disk test failed: ' + (Get-Ascii $_.Exception.Message) }

    $g = Get-Facts
    $gpus = $g.facts.gpus
    [void]$notes.Add('scores compare only with runs of the same method; no reference rows yet')
    if ($Json) {
        $out = [ordered]@{ schema = $Schema; tool = $ToolVersion; impl = 'ps1'; mode = 'bench-quick'; at = (Get-UtcStamp); seconds = $Seconds
                           bench = [ordered]@{ method = 'csharp'; cpu = $cpu; mem = $mem; disk = $disk }; gpus = $gpus; notes = @($notes) }
        Out-Line (ConvertTo-AsciiJson $out 10)
    } else {
        Out-Line ('Deep32 inventory ' + $ToolVersion + ' (bench quick, ' + $Seconds + ' s, method csharp)')
        Out-Line ('Processor  single thread ' + $cpu.st_mops + ' Mops/s; all ' + $threads + ' threads ' + $cpu.mt_mops + ' Mops/s (x' + $cpu.scaling + ')')
        Out-Line ('Memory     copy ' + $mem.copy_gbs + ' GB/s (' + $memMb + ' MB buffers)')
        if ($disk.skipped) { Out-Line ('Disk       skipped: ' + $disk.skipped) }
        else { Out-Line ('Disk       temp folder, ' + $disk.file_mb + ' MB file: write ' + $disk.seq_write_mbs + ' MB/s, read ' + $disk.seq_read_mbs + ' MB/s, random 4K read ' + $disk.rand_read_iops + ' IOPS, write ' + $disk.rand_write_iops + ' IOPS') }
        if ($null -eq $gpus) { Out-Line 'Graphics   unavailable' }
        else { foreach ($x in @($gpus)) { Out-Line ('Graphics   ' + (Format-Val $x.name) + ', ' + (Format-Val $x.mem_gb) + ' GB, driver ' + (Format-Val $x.driver)) } }
        foreach ($n in $notes) { Out-Line ('Note       ' + $n) }
    }
    return 0
}

try {
    if ($Mode -eq 'report') { $rc = Invoke-Report } else { $rc = Invoke-BenchQuick }
    exit $rc
} catch {
    Write-Err ('error: ' + $_.Exception.Message)
    exit 1
}
