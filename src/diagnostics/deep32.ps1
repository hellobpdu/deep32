# Launcher template. The owning installer binds and seals this interpreter.
$ErrorActionPreference = 'Stop'
$script:Deep32DiagnosticPython = '@DEEP32_DIAGNOSTIC_PYTHON@'
try {
    if ($script:Deep32DiagnosticPython -cnotmatch '^[A-Za-z]:[\\/]') {
        throw 'Unbound diagnostic runtime.'
    }
    & $script:Deep32DiagnosticPython -I -B ([IO.Path]::Combine($PSScriptRoot, 'deep32_doctor.py')) @args
    exit $LASTEXITCODE
} catch {
    # launcher failure before the diagnostic runtime can start.
    [Console]::Error.WriteLine('The Python diagnostic runtime could not start.')
    exit 2
}
