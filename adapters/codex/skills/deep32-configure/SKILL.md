---
name: deep32-configure
description: Deep, corpus-grounded configuration lens for a networking topic or keywords. Use only when the user invokes $deep32-configure.
---

# $deep32-configure: Configuration

Use this lens only when the user invokes `$deep32-configure`; never apply it implicitly. The scope is the text after
the command; with nothing after it, ask for the topic or keywords and do no research until they are given.

Research with the deep32 research skill (`${CODEX_HOME}/skills/deep32/SKILL.md`) at deep depth (its deep-engine.md
round script with `DEPTH = "deep"`), whether or not the user says "deep", with all of its evidence rules: retrieve
first, cite every claim, `verify_quote` every quote, "not in corpus" where the documents are silent, never the web.

Weight the analysis toward building it right the first time:
- **Minimal correct configuration** for each subtopic at the IOS XE pin, every command checked with `find_command`
  (mode, syntax, default), with the 26.x differences called out.
- **Order of operations**: what must exist first (keys, ACLs, VRFs, AAA, NTP, interfaces), and what breaks or
  silently does nothing when the order is wrong.
- **Defaults**: which defaults you must override, which you must leave alone, and which change between releases or
  platforms.
- **Configurations that look right but fail**: ones that parse cleanly but do not work (wrong mode, wrong direction,
  wrong VRF, missing keyword, an ACL that implicitly denies), each with the cited reason.
- **Removal and change**: how the `no` form behaves, what lingers, and what needs a reset or re-key.
- **Verify after every step**: the show command and the expected output fragment that proves each step worked.
