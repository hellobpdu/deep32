<#
slow-child.ps1: the long-running part of the fake tool slow-tool.cmd (inventory timeout test only).

What it does:
  Writes "<process id> <start time in UTC ticks>" to $env:D32_TEST_MARK\<role>.pid, so the test
  can check afterwards whether this exact process is still running. As "child" it first starts
  itself again as "grandchild" (hidden, no shell), then both sleep for $env:D32_TEST_SLEEP seconds
  (default 60). It reads and writes nothing else and opens no network connection.

Usage:
  powershell -NoProfile -File slow-child.ps1 child|grandchild

Exit codes:
  0  slept to the end (the test normally ends it before that)
  2  bad arguments or D32_TEST_MARK not set
#>
param([string]$Role)
if (($Role -ne 'child' -and $Role -ne 'grandchild') -or -not $env:D32_TEST_MARK) { exit 2 }
$secs = 60
if ($env:D32_TEST_SLEEP -match '^[0-9]{1,3}$') { $secs = [int]$env:D32_TEST_SLEEP }
$me = [System.Diagnostics.Process]::GetCurrentProcess()
if ($Role -eq 'child') {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $psi.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" grandchild'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    [void][System.Diagnostics.Process]::Start($psi)
}
$line = [string]$me.Id + ' ' + [string]$me.StartTime.ToUniversalTime().Ticks
[System.IO.File]::WriteAllText((Join-Path $env:D32_TEST_MARK ($Role + '.pid')), $line)
Start-Sleep -Seconds $secs
exit 0
