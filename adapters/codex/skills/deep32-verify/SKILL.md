---
name: deep32-verify
description: Deep, corpus-grounded verification and reading output lens for a networking topic or keywords. Use only when the user invokes $deep32-verify.
---

# $deep32-verify: Verification and reading output

Use this lens only when the user invokes `$deep32-verify`; never apply it implicitly. The scope is the text after
the command; with nothing after it, ask for the topic or keywords and do no research until they are given.

Research with the deep32 research skill (`${CODEX_HOME}/skills/deep32/SKILL.md`) at deep depth (its deep-engine.md
round script with `DEPTH = "deep"`), whether or not the user says "deep", with all of its evidence rules: retrieve
first, cite every claim, `verify_quote` every quote, "not in corpus" where the documents are silent, never the web.

Weight the analysis toward proving state from output:
- **The verification set** per subtopic: every show command that matters, ordered from summary to detail, each
  checked with `find_command`.
- **Field by field**: for each command, the fields that matter, what each means, the healthy value, the values that
  signal a fault, and the RFC or design concept each field maps to.
- **Invariants to observables**: each invariant of the mechanism, paired with the exact command and output fragment
  that proves it.
- **Output traps**: counters since last clear, cached or stale entries, per-VRF vs global views, platform
  differences in output, fields renamed between the pinned release and 26.x.
- **Annotated output**: output built only from a corpus sample checked with `verify_quote`, every changed field
  marked and the whole labelled constructed, with the field that decides each reading.
- **Debug vs show**: when a show cannot prove it and a debug or event trace is needed, and how to scope it safely.

Everything this lens adds beyond a retrieved passage (a table row, a scenario, a recommendation, a pattern) is cited,
or labelled `inference (from [cite] + [cite])` with the sources it rests on. Constructed output (a configuration,
show output, a log line) is built only from a sample checked with `verify_quote`, with every field you changed
marked, and is labelled constructed: never present it as seen on a device.
