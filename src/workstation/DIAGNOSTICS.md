# Workstation diagnostic placement

<!-- public implementation documentation below. -->
The image binds `/usr/local/bin/deep32` to OpenBSD ksh, the packaged
`/usr/local/bin/python3.14`, and the root-owned diagnostic entry point
`/usr/local/d32/diagnostics/deep32_doctor.py`.
The executable uses `-I -B`, preserves arguments and the diagnostic exit code,
and fixes `deep32 doctor` to the Workstation role.
Supply doctor options after `doctor`; the installed launcher supplies
`--role workstation`, so an additional role option is rejected by the shared
parser.
`deep32 selftest` reaches the same dispatcher when the merged diagnostic runtime
includes its selftest implementation; unsupported commands retain the shared
dispatcher's argument-error report.

Use the image's Python 3.14 package because the diagnostics are standard-library
Python 3.11+ code and do not need the engine's CPython 3.12 environment, wheels,
service imports, or service credentials.
Isolation ignores Python environment variables, the user site, and the working
directory; the shared entry point adds only its resolved installed directory for
package imports, and `-B` prevents diagnostic bytecode writes.
There is no interpreter fallback or environment override.

`diagnostics-map.json` defines the source, transformed-tree, and installed paths,
required runtime members, ownership, and modes for the canonical build stages.
Map the shared entry point, recursive `deep32_diag/**/*.py` package files, and
direct `schemas/*.json` files from `src/diagnostics` in the merged checkout into
`diagnostics/` in the transformed tree, then install that runtime in the node set.
Exclude template launchers, documentation, tests, caches, and public selftest data
from this runtime mapping; public selftest questions and reference data require
their own authenticated release bindings.
Install runtime directories as root:wheel 0755 and runtime files as root:wheel
0444; install the launcher as root:wheel 0555 in the site set.
A missing runtime member stops the build instead of producing a launcher without
its package.

This placement preserves the shared diagnostic evidence boundary: packaging the
command does not qualify a native observer or authenticate an installation
receipt.
The current shared dispatcher returns `installation FAIL` without a qualified
installed evidence owner, and selftest remains incomplete without its
authenticated inputs.
Offline launch and import checks establish packaging compatibility; native
OpenBSD runtime and qualified healthy-host checks remain operator work.
