---
name: deep32-design
description: Deep, corpus-grounded design trade-offs lens for a networking topic or keywords. Use only when the user invokes $deep32-design.
---

# $deep32-design: Design trade-offs

Use this lens only when the user invokes `$deep32-design`; never apply it implicitly. The scope is the text after
the command; with nothing after it, ask for the topic or keywords and do no research until they are given.

Research with the deep32 research skill (`${CODEX_HOME}/skills/deep32/SKILL.md`) at deep depth (its deep-engine.md
round script with `DEPTH = "deep"`), whether or not the user says "deep", with all of its evidence rules: retrieve
first, cite every claim, `verify_quote` every quote, "not in corpus" where the documents are silent, never the web.

Weight the analysis toward choosing between options and defending the choice:
- **Requirements** that drive the decision: scale, convergence, security, operations, failure domains, cost.
- **Options**: every reasonable design option, cited, with how each one meets or misses each requirement. Prefer
  Cisco design guides (CVDs, design zone) and RFC operational guidance as evidence.
- **Decision matrix**: options against requirements, then the recommendation and the conditions that would change
  it. Each cell is cited or labelled inference; the recommendation is labelled inference with the cites it rests on.
- **Failure domains**: what fails together, the blast radius of each failure, and how each option contains it.
- **Anti-patterns**: designs that look attractive but fail, with the cited reason.
- **Worked scenarios**: two or three realistic situations with their constraints, the option that fits and why the
  others lose, each step cited or labelled inference.
- **Prove it**: a lab check that demonstrates the key trade-off (convergence time, scale limit or failure behaviour)
  with the commands that measure it, proposed, never run.

Everything this lens adds beyond a retrieved passage (a table row, a scenario, a recommendation, a pattern) is cited,
or labelled `inference (from [cite] + [cite])` with the sources it rests on. Constructed output (a configuration,
show output, a log line) is built only from a sample checked with `verify_quote`, with every field you changed
marked, and is labelled constructed: never present it as seen on a device.
