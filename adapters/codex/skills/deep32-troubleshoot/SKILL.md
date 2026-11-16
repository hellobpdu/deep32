---
name: deep32-troubleshoot
description: Deep, corpus-grounded troubleshooting lens for a networking topic or keywords. Use only when the user invokes $deep32-troubleshoot.
---

# $deep32-troubleshoot: Troubleshooting

Use this lens only when the user invokes `$deep32-troubleshoot`; never apply it implicitly. The scope is the text
after the command; with nothing after it, ask for the topic or keywords and do no research until they are given.

Research with the deep32 research skill (`${CODEX_HOME}/skills/deep32/SKILL.md`) at deep depth (its deep-engine.md
round script with `DEPTH = "deep"`), whether or not the user says "deep", with all of its evidence rules: retrieve
first, cite every claim, `verify_quote` every quote, "not in corpus" where the documents are silent, never the web.

Weight the analysis toward finding faults fast and proving the root cause:
- **Isolation method** per topic: the order of checks (dependencies first, then control plane, then data plane), as
  a decision tree where each branch is one command and the output that sends you left or right.
- **Good vs bad output**: for each key show and debug command, the fields that matter, what healthy looks like and
  what each failure looks like, taken from corpus output samples where they exist.
- **Syslog and error signatures**: the exact messages (%FACILITY-SEV-MNEMONIC) and the cause each one points to.
- **Failure-mode table**: every dependency broken one at a time, plus two compound faults where one symptom hides
  another.
- **Red herrings**: symptoms that point at the wrong layer, and the evidence that clears each one.
- **Safe debugging**: conditional debugs, ACL-scoped debugs, and the cost of a debug on a busy device. Flag
  disruptive commands; never run them.
- **Break-fix cases**: a few realistic faults, each with the symptom, then the isolating command and the cited
  evidence.
