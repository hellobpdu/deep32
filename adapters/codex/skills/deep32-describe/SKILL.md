---
name: deep32-describe
description: Deep, corpus-grounded mechanism and understanding lens for a networking topic or keywords. Use only when the user invokes $deep32-describe.
---

# $deep32-describe: Mechanism and understanding

Use this lens only when the user invokes `$deep32-describe`; never apply it implicitly. The scope is the text after
the command; with nothing after it, ask for the topic or keywords and do no research until they are given.

Research with the deep32 research skill (`${CODEX_HOME}/skills/deep32/SKILL.md`) at deep depth (its deep-engine.md
round script with `DEPTH = "deep"`), whether or not the user says "deep", with all of its evidence rules: retrieve
first, cite every claim, `verify_quote` every quote, "not in corpus" where the documents are silent, never the web.

Weight the analysis toward a mental model that holds however the question is worded:
- **First principles**: the problem the protocol or feature solves, and the design choices the RFC or Cisco design
  documents give for solving it that way.
- **Mechanism**: message formats, state machines, algorithms, timers and tables, walked through as a step-by-step
  sequence (a packet or message walk) with citations.
- **RFC vs Cisco**: where the implementation adds, omits or changes defaults compared with the standard.
- **Compare and contrast**: a table against the nearest sibling technologies (versions, alternatives, predecessors)
  covering purpose, transport, security, scale, defaults and failure behaviour.
- **Misconceptions**: the wrong mental models people hold, and the cited evidence that breaks each.
- **The general pattern** behind the topic, and where else in networking it repeats.
- **A short plain-words summary** of the mechanism, and one lab check that makes it visible (the debugs or captures
  that show the state machine), proposed, never run.
