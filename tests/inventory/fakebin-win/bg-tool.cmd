@echo off
REM bg-tool.cmd: a fake command-line tool for the inventory tests (never a real tool).
REM What: prints a version line and exits at once, but first starts slow-child.ps1 in the
REM   background with start /b, so that child (and the grandchild it starts) keep this
REM   tool's output open long after the tool itself has ended.
REM Usage: bg-tool.cmd --version   (only from run_tests.py, with D32_TEST_MARK set)
REM Exit codes: 0
start "" /b "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0slow-child.ps1" child
echo bg-tool version 7.8.9
exit /b 0
