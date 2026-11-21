# Doctor source integration

The source command accepts `deep32 doctor --role workstation|forge|headend`
and `--json`. Forge alone accepts `--data-root ROOT`. Headend requires
`--agent codex|claude --profile-dir PROFILE`. Roots must be absolute. Missing,
duplicate, unknown or role-incompatible arguments produce an `arguments FAIL`
report with exit 2. `--json` produces one schema document and no progress
prose. There are no token, environment, URL, repair or fixture options.

`deep32_diag.cli.main` and `doctor.run_doctor` return the report exit code and
the sealed report respectively. The Python-only `DoctorEvidence` integration
seam contains the authenticated `InstallReceipt`, `CommonEvidence`, typed
Workstation/Forge/headend evidence, and protected exact-slot snapshots.
Workstation and Forge reuse their existing policy modules; headend reuses
`headend_rows` and `headend_pairing_row`. Storage budgets must match the
receipt exactly before filesystem grouping. Empty peer evidence cannot remove
the required peer check. The table order and JSON order are identical.

An installed observation owner must gather every observation using the same
`runner.Budget`: 90 seconds total, five seconds reserved for reporting, ten
seconds per operation, at most four in flight. Pure assembly checks that
budget between stages. SnapshotSet rechecks every retained marker through
`ExistingFiles.unchanged`; a missing/unqualified/empty capability yields WARN,
a changed marker yields WARN, and a deadline/runtime error or handled
interrupt yields incomplete `doctor FAIL`. No arbitrary Python callback can
establish interruptible native cancellation. Synthetic qualification flags
prove policy only and never establish native trust.

When supplying CLI evidence, its authenticated role/platform must match the
selection. A Forge `--data-root` must equal its private compiled evidence
binding. Headend agent/profile must equal the selected profile before its
policy is used; a mismatch yields installation FAIL. No selected input is
searched and no ordinary agent home is consulted. Default source execution
has no qualified installed evidence loader: it returns installation FAIL
without reading the supplied profile or data path. An omitted role may be
filled only by the injected authenticated receipt, never by discovery.

`deep32` and `deep32.ps1` are launcher templates. They deliberately refuse
their unresolved `@DEEP32_DIAGNOSTIC_PYTHON@` binding with exit 2. The owning
packaging job must replace that token with its sealed absolute Python 3.11+
interpreter path and seal the sibling `deep32_doctor.py` and package. There is
no PATH search, runtime environment override, installer or fallback. The
isolated script uses `-I -B`; its import directory derives only from that
sealed script. A launcher that cannot start Python cannot produce a doctor
schema document and reports only its fixed runtime-start failure on stderr.

The following owner inputs remain required before claiming an installed
doctor: frozen receipt/root integration and transaction writers; independently
authenticated inventory/content/model expectations; exact installed path and
interpreter/script origin binding; native denied-write verifier/command/HTTPS,
ACL/DPAPI and cancellation qualifications; protected peer identity/pairlock and
live route readers; runtime model/device and peer time evidence; and installer
path hooks on each role's media. This task does not invent any of them or
certify actual host health. Owning J13-J15 packaging jobs install the command
and preserve these boundaries.
