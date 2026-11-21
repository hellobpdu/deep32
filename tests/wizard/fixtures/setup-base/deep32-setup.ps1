<#
deep32-setup.ps1: set up one Windows machine for Deep32 with one command (the Windows twin of deep32-setup.sh).

  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1              work out what this PC should be
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 check        only check; changes nothing
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 forge        GPU PC: check, download, install the Forge
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 workstation  Hyper-V PC: get discs 1 and 2, write the VM settings
  powershell -NoProfile -ExecutionPolicy Bypass -File install\deep32-setup.ps1 headend      agent PC: connect Claude Code or Codex

Options: -Yes (accept the safe fixes without asking), -DataRoot DIR (Forge data, default C:\Deep32),
         -Name NAME (Tailscale name for the Forge when the current one doesn't start with deep32-),
         -Workstation URL (headend: the https://.../mcp address the Workstation printed at publication),
         -Agent claude|codex|both (headend).

It stops only for what a person has to do: sign in to Tailscale, accept the installer's plan, and type the pairing
code on the Workstation. Every line starts with a tag, so an agent running it can read the result cheaply:
  [ok] passed   [fix] fixed or needs fixing   [you] a step for you   [fail] stopped   [info] progress
#>
param(
    [Parameter(Position = 0)][string]$Role = '',
    [switch]$Yes,
    [string]$DataRoot = 'C:\Deep32',
    [string]$Name = '',
    [string]$Workstation = '',
    [string]$Agent = ''
)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$get = Join-Path $repo 'src\release-tools\deep32-get.ps1'
$script:problems = 0

function Ok([string]$m)   { Write-Output "[ok] $m" }
function Fix([string]$m)  { Write-Output "[fix] $m" }
function You([string]$m)  { Write-Output "[you] $m" }
function Info([string]$m) { Write-Output "[info] $m" }
function Fail([string]$m) { Write-Output "[fail] $m"; exit 1 }
function Need([string]$m) { $script:problems++; Fix $m }

if ($Role -and @('check', 'forge', 'workstation', 'headend') -notcontains $Role) { Fail "unknown role '$Role' (check, forge, workstation or headend)" }
if ($Agent -and @('claude', 'codex', 'both') -notcontains $Agent) { Fail "-Agent takes claude, codex or both" }

function Confirm-Step([string]$question) {
    if ($Role -eq 'check') { return $false }  # check only reports; it never changes the machine
    if ($Yes) { return $true }
    if ([Console]::IsInputRedirected) { return $false }
    $a = Read-Host "$question [y/N]"
    return ($a -match '^[Yy]')
}

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {  # the first python that really runs (skips the Microsoft Store placeholder)
    if ($env:DEEP32_PYTHON) {
        & $env:DEEP32_PYTHON -I -c "import sys, ssl" 2>$null
        if ($LASTEXITCODE -eq 0) { return $env:DEEP32_PYTHON }
    }
    foreach ($c in @('python3.exe', 'python.exe')) {
        foreach ($cmd in @(Get-Command $c -CommandType Application -ErrorAction SilentlyContinue)) {
            & $cmd.Source -I -c "import sys, ssl" 2>$null
            if ($LASTEXITCODE -eq 0) { return $cmd.Source }
        }
    }
    return $null
}

function Test-PythonAtLeast([string]$py, [int]$minor) {
    & $py -I -c "import sys; raise SystemExit(sys.version_info < (3, $minor))" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-TsStatus {
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) { return $null }
    $raw = & tailscale status --json 2>$null
    if (-not $raw) { return $null }
    try { return (($raw -join "`n") | ConvertFrom-Json) } catch { return $null }
}

function Check-Tailscale([string]$forRole) {
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        Need 'Tailscale is not installed: https://tailscale.com/download/windows'; return
    }
    $s = Get-TsStatus
    if (-not $s -or $s.BackendState -ne 'Running') {
        Need 'Tailscale is not signed in'
        if (Confirm-Step 'Start Tailscale sign-in now?') {
            You 'sign in with your account in the browser window Tailscale opens'
            if ($forRole -eq 'forge') { $n = $Name; if (-not $n) { $n = 'deep32-forge' }; & tailscale up --hostname $n }
            else { & tailscale up }
            $s = Get-TsStatus
        }
        if (-not $s -or $s.BackendState -ne 'Running') { return }
        $script:problems--
    }
    Ok 'Tailscale signed in'
    if ($s.CurrentTailnet -and $s.CurrentTailnet.MagicDNSEnabled) { Ok 'MagicDNS on' } else { Need 'turn on MagicDNS: admin console, DNS page' }
    if ($forRole -ne 'forge') { return }  # only the Forge publishes services and has to be offered by name
    $host_ = ''
    if ($s.Self -and $s.Self.DNSName) { $host_ = ($s.Self.DNSName -split '\.')[0] }
    if ($host_ -like 'deep32-*') { Ok "Tailscale name $host_ starts with deep32-" }
    else {
        $new = $Name; if (-not $new) { $new = 'deep32-forge' }
        Need "Tailscale name '$host_' must start with deep32- for the Workstation to offer it"
        if (Confirm-Step "Rename this PC to $new on your tailnet?") {
            & tailscale set --hostname $new
            if ($LASTEXITCODE -eq 0) { $script:problems--; Ok "renamed to $new" } else { Fix "rename it on the admin console's Machines page" }
        }
    }
    if ($s.CertDomains -and @($s.CertDomains).Count -gt 0) { Ok 'HTTPS certificates on' }
    else { Need 'turn on HTTPS certificates: admin console, DNS page, HTTPS Certificates' }
}

function Check-Common {
    $script:py = Find-Python
    if ($script:py -and (Test-PythonAtLeast $script:py 10)) { Ok "Python for the download tools ($script:py)" }
    else { Need 'install Python 3.12 or newer from python.org (tick "Add python.exe to PATH"), then open a new PowerShell (or set DEEP32_PYTHON to a Python 3.12+ path)' }
}

function Get-GpuClass {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return '' }
    $best = ''
    foreach ($cc in @(& nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null)) {
        $c = switch -Regex ($cc.Trim()) {
            '^7\.5$' { 'turing' } '^8\.0$' { 'ampere80' } '^8\.[67]$' { 'ampere86' } '^8\.9$' { 'ada' }
            '^9\.' { 'hopper' } '^10\.' { 'blackwell100' } '^12\.' { 'blackwell120' } default { '' }
        }
        if ($c -and -not $best) { $best = $c }
    }
    return $best
}

function Get-VramFloorMiB {  # the smallest card the Forge supports: install\matrix.json, the first gpu_mem_tiers row
    try {
        $m = Get-Content -Raw -Path (Join-Path $here 'matrix.json') | ConvertFrom-Json
        return [int][math]::Round([double]$m.gpu_mem_tiers[0].min_gb * 1024)
    } catch { return 10752 }
}

function Check-Forge {
    Check-Common
    if ($script:py -and (Test-PythonAtLeast $script:py 12)) { Ok 'Python is 3.12 or newer for the Forge installer' }
    elseif ($script:py) { Need 'the Forge installer needs Python 3.12 or newer from python.org' }
    $class = Get-GpuClass
    if ($class) {
        $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
        Ok "$gpuName, class $class"
        $drv = (& nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1).Trim()
        try { if ([version]$drv -ge [version]'580.65') { Ok "driver $drv" } else { Need "NVIDIA driver $drv is older than 580.65; update it" } }
        catch { Info "driver $drv (could not compare its version)" }
        $floor = Get-VramFloorMiB
        $mib = 0
        foreach ($v in @(& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null)) { if ([int]$v -gt $mib) { $mib = [int]$v } }
        if ($mib -ge $floor) { Ok "$mib MiB of GPU memory (the Forge needs $floor)" }
        else { Need "the card has $mib MiB of GPU memory; the Forge needs at least $floor MiB (docs\HARDWARE.md)" }
    } else {
        Need "no supported NVIDIA card found (compute capability 7.5 or newer); this PC can't be a Forge"
    }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $os = (& docker info --format '{{.OperatingSystem}}' 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $os) {
            if ($os -match 'Docker Desktop') { Ok 'Docker Desktop running' } else { Info "Docker reports '$os'" }
            $store = (& docker info --format '{{.Driver}} {{json .DriverStatus}}' 2>$null | Select-Object -First 1)
            if ($store -match 'io\.containerd\.snapshotter' -or $store -match '^overlayfs') {
                Need ("Docker Desktop keeps images in its containerd store; the alpha.2 Forge loads only into the classic store. " +
                      "In Docker Desktop: Settings, General, turn off 'Use containerd for pulling and storing images', then Apply & restart. " +
                      "Images and containers kept in the containerd store stay hidden until you turn it back on")
            } elseif ($store) { Ok 'Docker uses the classic image store' }
            & docker compose version 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { Ok 'Docker Compose v2' } else { Need 'Docker Compose v2 is missing: update Docker Desktop' }
            Info 'Docker Desktop must use the WSL 2 backend; GPU support comes with it on current NVIDIA drivers'
        } else { Need 'Docker Desktop is installed but not running: start it and wait until it says Engine running' }
    } else { Need 'install Docker Desktop with the WSL 2 backend: https://docs.docker.com/desktop/setup/install/windows-install/' }
    if (Test-Admin) { Ok 'running as administrator (the install loads Docker images and adds a startup task)' }
    else { Need 'run this from an elevated PowerShell (right-click PowerShell, Run as administrator)' }
    Check-Tailscale 'forge'
}

function Get-PairCode {  # the current five-minute code: status\windows-pair (owner and SYSTEM only), else status\status
    $w = Join-Path $DataRoot 'status\windows-pair'
    if (Test-Path -LiteralPath $w) {
        $line = (Get-Content -LiteralPath $w -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($line -and $line.Trim() -match '^[A-Z2-7]{10}$') { return $line.Trim() }
    }
    $f = Join-Path $DataRoot 'status\status'
    if (-not (Test-Path -LiteralPath $f)) { return '' }
    $m = Select-String -LiteralPath $f -Pattern 'pair=([A-Z2-7]{10})' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return ''
}

function Test-Door([string]$url) {
    try { $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 20; return [int]$r.StatusCode }
    catch [System.Net.WebException] { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } return 0 }
    catch { return 0 }
}

function Do-Forge {
    Check-Forge
    if ($script:problems -gt 0) { Fail 'fix the [fix] lines above, then run this again' }
    $class = Get-GpuClass
    Info "downloading, verifying and installing the GPU pack for $class into $DataRoot"
    You 'the installer shows its plan (folders, ports, images, sizes) and waits for you to accept it'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $get disc3 --gpu-class $class --install --journey J1 --data-root $DataRoot
    if ($LASTEXITCODE -ne 0) { Fail 'the Forge install stopped; its message above names the cause' }
    Ok 'Forge installed and running'
    $s = Get-TsStatus
    $fqdn = ''
    if ($s -and $s.Self) { $fqdn = ($s.Self.DNSName).TrimEnd('.') }
    foreach ($port in 443, 8443) {
        $code = Test-Door "https://${fqdn}:$port/health"
        if ($code -eq 401) { Ok "door answers on https://${fqdn}:$port and asks for a token" }
        else { Need "https://${fqdn}:$port answered '$code' instead of 401" }
    }
    $short = ''
    if ($fqdn) { $short = ($fqdn -split '\.')[0] }
    $pairTool = Join-Path $repo 'src\release-tools\forge_pair_windows.py'
    if (-not (Test-Path -LiteralPath $pairTool)) {
        You 'pair the Forge with the Workstation as INSTALL.md section 8 describes'
        return
    }
    Info 'opening the Forge pairing window (five minutes)'
    $proc = Start-Process -FilePath $script:py -PassThru -NoNewWindow `
        -ArgumentList @('-I', '-B', "`"$pairTool`"", '--data-root', "`"$DataRoot`"")
    $code = ''
    for ($i = 0; $i -lt 30 -and -not $code -and -not $proc.HasExited; $i++) { $code = Get-PairCode; if (-not $code) { Start-Sleep -Seconds 2 } }
    if (-not $code) {
        if ($proc.HasExited -and $proc.ExitCode -eq 0) { Ok 'the Forge is already paired'; return }
        Fail 'no pairing window opened; the message above names the cause'
    }
    You "on the Workstation: open Pairing, choose Forge, pick $short and type the code $code (valid five minutes)"
    $proc.WaitForExit()
    if ($proc.ExitCode -eq 0) { Ok 'paired with the Workstation' }
    else { Fail 'the pairing window closed without pairing; run this again for a new code' }
}

function Do-Workstation {
    Check-Common  # the Hyper-V host itself needs no Tailscale: the Workstation VM joins your tailnet at its first boot
    if (Get-Command New-VM -ErrorAction SilentlyContinue) { Ok 'Hyper-V is available' }
    else { Need 'turn on Hyper-V: Windows Features, Hyper-V (Windows 10/11 Pro or Enterprise), then restart' }
    if ($script:problems -gt 0) { Fail 'fix the [fix] lines above, then run this again' }
    $dir = Join-Path (Split-Path -Parent $repo) 'deep32-downloads'
    Info "downloading and verifying disc 1 (Workstation) and disc 2 (content) into $dir"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $get disc1 --to $dir
    if ($LASTEXITCODE -ne 0) { Fail 'disc 1 download stopped' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $get disc2 --to $dir
    if ($LASTEXITCODE -ne 0) { Fail 'disc 2 download stopped' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $get disc1 --to $dir --prepare-vm --hypervisor hyper-v
    if ($LASTEXITCODE -ne 0) { Fail 'writing the VM settings stopped' }
    You 'create the Generation 1 VM with the commands above, boot it from disc 1, and answer the installer (INSTALL.md section 6)'
    You 'at first boot, set the passwords, sign in to Tailscale, and leave disc 2 in the drive for the import'
}

function Do-Headend {
    Check-Common
    Check-Tailscale 'headend'
    $a = $Agent
    if (-not $a) {
        $c = [bool](Get-Command claude -ErrorAction SilentlyContinue); $x = [bool](Get-Command codex -ErrorAction SilentlyContinue)
        if ($c -and $x) { $a = 'both' } elseif ($x) { $a = 'codex' } elseif ($c) { $a = 'claude' } else { Fail 'install Claude Code or Codex first' }
    }
    if ($Workstation -notmatch '^https://.+/mcp$') {
        Fail 'pass the gateway address the Workstation printed when you published it: -Workstation https://<name>.<tailnet>.ts.net/mcp (on the Workstation, as root: d32-publish publish --accept-ct)'
    }
    You 'Connect asks for a ten-character code: on the Workstation open Pairing and choose Headend, then type the code the Workstation shows into Connect here'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'adapters\connect.ps1') -Agent $a -Workstation $Workstation
    if ($LASTEXITCODE -ne 0) { Fail 'connecting the agent stopped' }
}

function Get-GuessedRole {
    if (Get-GpuClass) { return 'forge' }
    if ((Get-Command claude -ErrorAction SilentlyContinue) -or (Get-Command codex -ErrorAction SilentlyContinue)) { return 'headend' }
    return 'workstation'
}

if (-not $Role) { $Role = Get-GuessedRole; Info "this PC looks like a $Role host (name another role to override)" }
switch ($Role) {
    'check' { Check-Forge; if ($script:problems -eq 0) { Ok 'ready for the Forge' } else { Fail "$($script:problems) thing(s) to fix" } }
    'forge' { Do-Forge }
    'workstation' { Do-Workstation }
    'headend' { Do-Headend }
}
