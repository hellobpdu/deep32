@echo off
REM slow-tool.cmd: a fake command-line tool for the inventory timeout test (never a real tool).
REM What: behaves like a .cmd wrapper around node that hangs: cmd.exe starts a long-running
REM   child (Windows PowerShell running slow-child.ps1), which starts a grandchild of its own;
REM   both write their process id and start time into the folder named by D32_TEST_MARK.
REM Usage: slow-tool.cmd --version   (only from run_tests.py, with D32_TEST_MARK set)
REM Exit codes: whatever the child returns; normally the test's time limit ends it first.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0slow-child.ps1" child
