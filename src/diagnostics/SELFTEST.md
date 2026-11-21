# Selftest integration contract

`deep32 selftest` compares the installed signed public bundle through the
installation's own MCP gateway. A headend supplies `--agent codex|claude` and
`--profile-dir ABSOLUTE_PATH`; `--json` and `--details` are the only other
options. A Forge prints the design's Workstation/headend direction and exits 2.
There is no reference writer, calibration mode, question-file, URL, token,
retrieval-mode, tolerance or trust override in the command.

The existing launcher templates dispatch through `deep32_doctor.py` and
`deep32_diag.cli`. They still require the owning installer to bind and seal
`@DEEP32_DIAGNOSTIC_PYTHON@`. Source execution has no qualified installed loader:
it produces INCOMPLETE, exit 2, without reading a supplied profile. J13-J15 and
the headend owner must supply the compiled loader; source fixtures do not make
an installed command ready.

## Installed owner interface

The owner supplies `selftest.SelftestEvidence`, never CLI-derived trust data.
It binds the installed role and the exact selected agent/profile, an
`authenticate(budget)` capability, and `open_session(budget)`. Authentication
must finish before `open_session` reads the protected local or selected-profile
credential. It must call `selftest_trust.authenticate_bundle`, which returns a
sealed `TrustedBundle`. A plain contract `Bundle` cannot run.

The trust reader accepts ten exact installed slots: `manifest` (SHA256SUMS),
`signature`, `keyring`, `verifier`, `asset_map`, `questions`, `reference`,
`questions_schema`, `reference_schema`, and `result_schema`. It reuses J05's
pinned primary fingerprint, qualified direct-gpgv interface, safe existing-file
reader, and snapshot rechecks. The signed root must cover the exact five flat
selftest assets and ASSET-MAP. Schema bytes must also match the independently
compiled schema hashes. The map validates only the fixed disc 1 and
Workstation/headend destinations; it cannot select a new file to read or write.
No online update or keyring discovery occurs. Installer ownership and protection
against replacement while gpgv reads remain qualified platform responsibilities.

`open_session` returns `SelftestSession(client, attest, unchanged, utc_now)`.
The client is `SelftestMCPClient`, using that same run's `SelftestBudget`, the
protected memory-only credential and qualified transport. Headends accept only
the enrolled canonical HTTPS authority. Workstations can use that authority or
an exact installer-recorded literal IPv4/IPv6 loopback endpoint. No DNS name or
fallback is accepted for loopback. No agent process or model turn is invoked.

The authenticated live `attest(deadline)` provider supplies doctor's measured
`gpu_ext`/`embed`/`rerank` snapshot, including verified identity/revision, GPU
device, measurement flag and fresh UTC checked time. Cached legacy flags are
insufficient. Each observation is bounded to 10 seconds and must be no older
than that observation window. `unchanged(deadline)` rechecks the selected
profile, credential, installed runtime and endpoint snapshots; it returns True
only when the protected bindings still agree. These capabilities require native
qualification; setting a boolean in a fixture grants no native authority.

## Retrieval and bounds

The shared MCP JSON/SSE/session parser is reused. The transport receives
`SelftestRequest.connect_deadline` (at most five seconds) for DNS/TCP/TLS and
`deadline` for the remaining operation; waiting for HTTP response headers can
include server computation and is governed by the latter. It must enforce
certificate validation, no proxy/redirect/persistent cache/debug logging,
interruptible operations and cancellation of owned handles. Responses remain
capped at 4 MiB. All native transport and verifier qualification is an owner
input; no native socket/process implementation is introduced here.

Initialisation takes at most five seconds per operation, status and attestation
10, and search 30. One monotonic 900-second deadline includes trust, readiness,
all serial searches, final checks and cleanup. No retries occur. Console and
termination signals unwind the owned observations and restore signal handlers.

The fixed signed hybrid/deep/auto-rerank/debug/2400-character request adds only
the question text and optional collection. Labels, spans and document keys are
never sent as filters. Initial/final status checks compare complete effective
settings, generation/fingerprint, every required backend, closed dense/reranker
breakers and no degradation. Both live model identities must match before and
after. The gateway does not echo a settings/request fingerprint in search
responses: the client fixes its sent profile and compares settings at the two
status boundaries. Search arrays retain delivered order, unique valid hit IDs,
literal text/doc keys, expected generation, no degradation, and reranking
evidence for the first min(10, length) hits.

The scorer accepts only exact document plus case-sensitive decoded-Unicode
substring matches among the first ten delivered passages. Alias metadata and
expanded sections receive no credit. Exact rational metrics and the signed
1/50 tolerance are compared before display rounding. Reference counts/sums
must recompute and be attainable at ranks 1-10. The signed reference asserts
three complete identical calibration runs; the separate lead producer supplies
their actual receipts.

Exit 0 means complete/pass, 1 complete/below threshold, and 2 INCOMPLETE. Every
failure discards accumulated metrics/details and reports measured:null and zero
completed questions. JSON and text come from the same sealed result. No remote
prose, passage, question text, label, endpoint or credential is published.

## Remaining release inputs

The lead supplies the public questions, validates their spans and rights,
creates the separate signed three-run calibration, and binds final reference
bytes into the release root and installed state. No real set is included here.
`fixture_only:true` is accepted only by explicit Python test-harness calls and
is refused by the shipped command.

The supplied D3 result schema requires a nonzero question count and numeric
reference even when trust failed before any reference could be read. Such
INCOMPLETE reports use the static bundle ID `unavailable`, count 1 and zero
reference placeholders, explicitly identified in the failed signed-reference
row; measured stays null. The lead should review that pre-trust schema limitation.
No comparison is made with those placeholders.
