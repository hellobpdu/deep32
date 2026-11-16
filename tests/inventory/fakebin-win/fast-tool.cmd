@echo off
REM fast-tool.cmd: a fake command-line tool for the inventory tests (never a real tool).
REM What: prints a version line at once, so the normal path of Invoke-Tool can be checked.
REM Usage: fast-tool.cmd --version
REM Exit codes: 0
echo fast-tool version 4.5.6
exit /b 0
