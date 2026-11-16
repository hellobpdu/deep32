# Expanded answer (the default layout)

This layout is the default for every answer. Use the compact template in `${CLAUDE_SKILL_DIR}/citation-style.md`
only when the user asks for a short or quick answer. Everything else in citation-style.md still applies: cite forms,
quote labels, markers, 25-word quotes.

An answer teaches the way a senior network engineer explains a feature to a colleague: the mechanism from first
principles, the configuration and the proof that it works, a worked example, what production and the platform
change, and the edge cases, with every technical claim cited and the explanation written as plain, causal prose.
The search modes (Full, Half, Light) are separate from this layout: they set how much a search brings back, never how
much the answer explains.

## Theory pass (extra research, before you write)

At the default depth this pass is folded into `quick-default.md`'s bounded rounds (its material comes from the
sections those rounds read; no extra calls beyond them). The full pass below runs only for "thorough", "deep" or
"insane".

After the method's recall and reading steps, research what the sections below need, as many calls as the topic needs
(each section's material is grounded, or it is a gap said as such; nothing is written from memory to fill one):

1. Read the mechanism where it is defined: the RFC section with the state machine, message format, algorithm or
   timer, and the Cisco "Information About ..." or overview section for the same feature (`get_section`).
2. Run one `search_many` with 2 to 4 dense paraphrases of the *why* and *how*, for example "why an OSPF router
   waits in ExStart when MTUs differ", to find explanatory passages the first recall missed.
3. Configuration: every command the answer will show, with `find_command` at release 17.9 (read each row's
   `platform` and `release`; the `release` argument ranks rows, it does not filter them), its mode, its defaults and
   its usage guidelines from the command-reference entry (`get_section`).
4. Verification: the show, debug and clear commands that prove the behaviour, the fields and counters they list, and
   any sample output the corpus has (with its source and release: one sample illustrates, it does not fix the format).
5. Production and platform: the Restrictions, Prerequisites, Guidelines and Recommendations sections; Design Zone
   `cvd-*` and white paper `wp-*` documents (`list_documents`) where the corpus has them; release drift (the 17.9 pin,
   17.18, Catalyst 9300 26.x), searched without `release_max` beside the filtered queries.
6. Edge cases: the "unless", "except" and "if more than one" clauses of the sections read, how the feature meets its
   neighbours, and `lookup_rfc` for the updates and errata of every RFC you cite.
7. Keep collecting Evidence past the method's "two independent sources agree" stop until you have 5 bullets from
   3 sections, or the corpus has nothing more on the topic.
8. Run `verify_quote` on every new quote, one call each.

## Layout

```
**Answer.** About 250 to 400 words. Direct, technical, pinned to the release, and it corrects a false premise
first. Lead with the conclusion, then explain why it holds, in prose.

**How it works.** The heart of the answer, about 300 to 700 words of teaching prose: the mechanism from first
principles. What it is and where it lives (control, data or management plane; which process, table or database);
its moving parts in the order they act (messages, states, timers, decisions); what changes the outcome; every term
defined on first use; and the neighbouring mechanism it is most often confused with, with the cited line that
separates them. A table when it makes a sequence or a comparison visible.

**Configuration and verification.** When the topic can be configured or observed: the configuration in a code block,
one command per line with the mode it runs in, then what each line does and the defaults it leans on; then the show
and debug commands that prove it, what healthy and broken output looks like (as the docs describe it), and the
fields or counters to watch.

**Worked example.** When the topic is a process: a short scenario traced step by step (a packet, a route, a
neighbour, a policy decision), one step or row each, with the outcome and the rule that decides it.

**In production.** When practice differs from the textbook: the recommended design, the operational risks, scale
limits and platform or release dependencies.

**Edge cases and pitfalls.** Exceptions, interactions with neighbouring features, failure modes and the classic
misreadings.

**Evidence**
- 5 to 8 bullets when the corpus has them, from at least 3 sections; when the topic has both, at least one RFC and
  one Cisco document. One claim per bullet, in the citation-style.md form.

**RFC vs Cisco.** As in citation-style.md.

**Further reading.** 2 to 5 more sections worth reading next, as `[cite] file:line` entries.

**Confidence and gaps.** As in citation-style.md.

**Read.** Section ids read, then the search mode and depth. It stays the last line.
```

Where an instruction file asks for an **Analysis** section after the evidence, In production and Edge cases and
pitfalls are its body: head them with **Analysis** there, after Evidence, and keep the order otherwise.

## Size: scale to the question, never pad

- A fact, a default or one command: about 500 to 800 words. Answer, a shorter How it works, Configuration and
  verification when it applies, Evidence and the closing sections.
- A mechanism, a feature or a configuration: about 900 to 1,600 words, every section that has something real to say.
- A design, a troubleshooting case or a question across several protocols: about 1,500 to 2,500 words, every
  section, with the worked example and the verification at full depth.
- The long form, when the user asks for it ("verbose", "long form", "chapter"): every section at full depth, a
  chapter of about 2,500 to 4,000 words, still cited throughout and never padded.
- A section with nothing real to say is left out (no Worked example for a pure default, no In production when the
  textbook is the practice). "Short", "quick" or "brief" means the compact template; "deep" or "insane" change the
  retrieval (the method's deep mode), and the deeper material then fills these same sections.

## Writing it

- Corpus facts carry their cite, and a quote marker when quoted.
- Your own explanation is welcome in How it works, the Worked example, In production and Edge cases: that is the
  point of them. End every paragraph that relies on it with `(unsourced)`, so background is never mistaken for a
  corpus fact.
- Never use background for a default value, a timer, a command's syntax, a release or a platform limit. Those
  always need a citation, or they go to Gaps.
- Explain causes, not just facts: say why the protocol behaves this way and what would happen otherwise. A citation
  supports an explanation and never replaces one; no paragraph is only quotes.
- Commands go in code blocks, one per line; acronyms are spelled out on first use; tables are used where a sequence or
  a comparison reads better as one.
- Never claim a test, a lab result or an output that was not seen: what an output would look like is the docs'
  description, said so.
- If the background and a cited passage disagree, the cited passage wins. Say so, and propose a lab check (the
  commands to run and what each outcome would show).
- No filler: no praise for the question, no restating it, no closing summary that repeats the sections.

## Other answer types

- **Troubleshooting:** keep the ranked hypotheses of `${CLAUDE_SKILL_DIR}/troubleshooting.md`, then add How it works
  (the mechanism that produces the symptom), Configuration and verification (the commands that isolate each
  hypothesis), the expanded Evidence and Further reading.
- **Design questions:** answer the core sub-question in this layout, then each further protocol in turn with the
  same method (one concept-map chain per feature).

## Keeping the markers machine-readable

- Keep each quote and its `(label · file:line)` marker on the same line. Never wrap a quote across lines.
- On a line that carries a marker, put terms in backticks, not double quotes.
- Further reading takes no double quotes and no labelled markers.
