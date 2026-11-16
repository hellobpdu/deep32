# Deep retrieval engine: wider, deeper rounds

This file changes only **how much** SKILL.md section 2 retrieves, strictly from the knowledge base and with the same
method: it replaces section 2's budget
("about 8 to 15 tool calls") and its early stop ("once two independent sources agree"). Everything else stays as it
is: section 1 (the tool check), the rules of section 2 (identifiers, documents' terms, `find_command` at 17.9, whole
sections, `lookup_rfc` standing, RFC vs Cisco, `verify_quote`, the `cite` attribute), section 3, `citation-style.md`,
`expanded-answer.md` and the answer's shape. It runs on the deep32 tools only: "not in corpus" where the documents
are silent, never the web.

## Who runs which depth

| Depth | Who | Rounds | Stop |
|---|---|---|---|
| `default` | a deep32 answer when the user says "thorough" | 1 (wide) and 2 (threads) | after round 2 |
| `deep` | the user says "deep" or "insane" (both mean the same) | 1, 2, 3 ... | two rounds in a row that bring no new section into a search's top 5 hits, or every facet out of budget (section 3) |

## 0. Constants, budgets and time

- **`MAX_PARALLEL_CALLS = 4`**: never more search calls in flight, which here means at most 4 deep32 tool calls in
  one message (Claude Code runs the calls of one message in parallel and waits for all of them). The engine still
  runs them one at a time, so the gain is fewer model steps, not faster searches. If it is ever raised, change it
  here and in the Codex copy to the same value. `verify_quote` calls are exempt from the round budgets; they keep
  the same cap per message. No background subagent makes search calls while you run a round, so the cap holds for
  the whole session.
- **Confirmations** are outside the round budgets too, like `verify_quote`: `rag_status`, `find_command` for each
  command you will cite, `lookup_rfc` for each RFC you will cite. They keep the 4-per-message cap.

| Per round | default | deep |
|---|---|---|
| calls you plan that explore (searches, `lookup_id`, `grep_corpus`, `get_outline`), in messages of 4 | 8, then 8 | 12 |
| sections read whole: the strongest new ones plus nearby Restrictions / Defaults, one message | 4, then 4 | 4 |
| search `limit` / `max_chars` (evidence searches give no `limit`, section 8) | none, deep-32 (16 on a gateway without it) / 500 | none, deep-32 (20 without it) / 400 |
| section read `max_chars` | 9000 | 9000 |

- **Time (measured in Claude Code on 4 questions under load):** this depth took 63-210 s more than a section 2
  answer, mean 141 s; the search wait was under 25 s per answer, so the cost is model steps over a larger context.

## 1. Round 1: wide and parallel

1. Plan 8 to 12 sub-queries in the documents' words
   (`${CLAUDE_SKILL_DIR}/query-planning.md`), one or more for each angle that applies:

| Angle | Mode | Example (an NSSA ABR's forwarding address) |
|---|---|---|
| identifiers: feature, command, message, state and field names, RFC numbers | bm25 | `area nssa translate type7 suppress-fa` |
| the question as asked | dense | why the ABR sets the forwarding address to 0.0.0.0 |
| the mechanism, step by step | dense | how an NSSA ABR translates type-7 LSAs into type-5 |
| defaults, prerequisites and restrictions | bm25 | `NSSA restrictions`, `nssa default`, `NSSA prerequisites` |
| the governing RFC against the Cisco text (`docs`: the RFC and the Cisco doc_keys of the best hits) | bm25, dense | `forwarding address P-bit translation` |
| troubleshooting: show, debug and syslog output, error strings | bm25 | `show ip ospf database nssa-external` |
| adjacent features and interactions | dense | NSSA with summarization and filtering at the ABR |
| counter-evidence: not supported, exceptions, caveats | bm25 | `NSSA not supported` |
| the guides' own headings (natural queries miss guide sections the outline reaches) | bm25, `docs`: the Cisco guides of the best hits | `Restrictions for <feature>`, `Information About <feature>`, `Prerequisites for <feature>`, `Feature History for <feature>` |

2. Batch them into 2 or 3 `search_many` calls of 3 to 6 queries each, grouped so one filter fits each call (the
   RFC-vs-Cisco group with its `docs`; `release_max: "17.9"` where section 2 pins it). In the same
   round: `lookup_id` for each identifier, `find_command` (release 17.9) for each command the question names, and
   `lookup_rfc` for the governing RFC. Send them as messages of at most 4 calls. A `lookup_id` row from an opt-in
   collection (`${CLAUDE_SKILL_DIR}/collections.md`) is opt-in material too: skip it unless the question is one that
   file covers.
   **`release_max` narrows; it never decides.** Use it only on a group whose Cisco doc_keys include a 17.9 book (`c9300-179-*`, `c9400-179-*`, `c9k-179-*`, `iosxe-179-*`), and send the same
   queries unfiltered in the same round: the filter removes every newer document whole. Skip it where no
   pinned-release book exists: the C9300 Layer 2, VLAN, QoS, interface and hardware, system-management and IP-services
   guides are 17.18.x and 26.x only (`${CLAUDE_SKILL_DIR}/release-drift.md`). A hit newer than the pin is labelled
   with its release, never dropped. On an ISE configuration group the cap is `release_max: "3.1"`, never `"17.9"`
   (ISE automation follows collections.md).
3. Drop every hit already seen (by its `id`, the chunk id) or inside a section already read. Then, in one message,
   read whole the strongest new sections (best rank, hit by the most queries in its `q` attribute, at most 2 per
   document) and the nearby Restrictions, Prerequisites, Defaults, Usage Guidelines and Feature History sections the
   hits point to, within the round's read budget; the reads' `prev` / `next` ids name more for round 2. A hit's
   `also in` line and a deep answer's tiers: section 8.

## 2. Round 2: follow the threads

From what round 1 read, collect the leads (RFC numbers, show / debug / clear commands, syslog messages
`%FACILITY-SEVERITY-MNEMONIC`, "no hit" sub-queries, partial sections, Restrictions and Defaults not read yet) and run
targeted calls:
- each RFC a Cisco text implements or names: `lookup_rfc` (standing, errata), then `get_section` on the RFC section
  the Cisco text maps to; for an RFC read in round 1, the Cisco side;
- commands and features named in restrictions, notes, see-also and usage guidelines: `find_command` (release 17.9),
  `lookup_id`;
- each fix or workaround the reads suggest (a knob, an ACL, a PACL): one bm25 query for its restrictions on the
  pinned platform (`release_max: "17.9"` under section 1 step 2's rule: the same query unfiltered beside it, and
  unfiltered alone where no 17.9 book covers the area);
- error strings and syslog messages: `grep_corpus` (with `docs` when you know the document);
- each "no hit" sub-query: alone with `search_rag` and `docs` from the best hits so far;
- adjacent sections: a partial read continued at its offset, Restrictions and Defaults not read yet, the sibling a
  section refers to (`get_outline(doc, under=<chapter>)` finds them);
- each default, timer, limit or command the answer states: read its command-reference entry (the section
  of its `find_command` row: the row only points at it) and set it against the configuration guide's Defaults or
  Restrictions and any TechNote sample. Agreeing: cite both. Differing: quote both, mark it **disputed**, and give the
  lab check;
- a default, value, exception or restriction the answer will state that you have seen only in a hit
  window: read its section whole (or continue it at its offset) before it goes into Evidence or the concept map.
  These reads take the first places of the round's reads; one still open when that budget ends is read
  before the quote check, outside the budget like a confirmation;
- when no section read in round 1 has a heading that names the feature, start this round with
  `get_outline(<the likeliest Cisco guide>, depth=1)`, then `get_outline(doc, under=<chapter>)` and the section
  that names it, inside the round budget;
- a call that came back with an error: run it again.

Round 2 always runs, small when round 1 left few leads. It is also `expanded-answer.md`'s theory pass: spend that
pass's extra calls only on what the two rounds left open. Then build the concept map (section 5), check the quotes
(section 7) and write.

## 3. Deep and insane

Rounds 1 and 2 as above, then round after round: each takes its new sub-queries from the new hits' terms and the
leads (section 2), plus the reads they need, at most 12 exploring calls per round plus confirmations.
- **What counts as new:** a section that no earlier hit or read showed, among a search's top 5 hits. New chunk ids
  inside sections already seen, lower-ranked hits and the sections a lookup points at do not count. Keep the count per
  round in your ledger.
- **Stop** when two rounds in a row bring nothing new, when every facet is out of budget, or when you judge that the
  last two rounds added no new relevant evidence.
- **Facet budgets (the backstop):** split the question into facets (each feature or mechanism, plus the RFC text and
  counter-evidence); a facet has 20 calls (its searches, reads and lookups), extended to 40 while it still finds new
  sections, and closes after 2 searches in a row with nothing new. Confirmations are never cut by a facet budget.
- Every 4 rounds, post one line for the user (rounds, calls, open facets) and go on. You run every round yourself;
  never delegate retrieval to another agent.
Build section 5's concept map for each topic before writing, and say in Confidence and gaps how many rounds ran, why
they stopped (two dry rounds, budgets, your judgement) and which facets stayed unsaturated.


## 5. The concept map (before you write)

From the hits and sections you read, assemble a grounded mechanism chain for the question's feature or protocol (one
chain per feature for a multi-part question):
1. **What it is:** the definition, and where it lives (process, table, plane).
2. **How it works, step by step:** messages, states, their order, timers, and each step's exceptions and tie-breaks
   (the unless, except and if-more-than-one clauses of the text you read).
3. **Defaults and prerequisites:** the RFC default and the IOS XE default side by side, the prerequisites, and the
   release they hold for. The release comes from a `find_command` row at 17.9 or a 17.9-pinned book. A Feature
   Information row in a rolling 17.x guide is not release evidence, and a command missing from the 17.9 books is a
   gap to state. A default or syntax claim also cites the command reference's Command Default or Syntax Description
   entry beside the configuration guide: `c9300-179-cr` at 17.9, or the `iosxe-cr-*` entry that
   `find_command` returns (a rolling IOS XE 17 reference: label it as that book's, not as 17.9 evidence), with its
   Command History row for the release. If they disagree, quote both and mark the claim **disputed**.
4. **What interacts with it:** the features that change its outcome, and the order in which they bite.
5. **How it breaks:** symptom and cause, from restrictions, caveats and troubleshooting text, and where the symptom
   first shows (which device, which state or counter), so each cause sits under the symptom it produces.
6. **How to verify:** the command (its `find_command` row) and what healthy and broken output show.
7. **Where the RFC and Cisco differ**, or "No difference found".

Every node cites a hit you retrieved (`cite` and `local`). A link that no hit states is labelled inference (in How it
works, "(unsourced)", as `expanded-answer.md` says); a node with no hit says "not in corpus". A discriminator taken
from one sample output (one TechNote's show output, or an older release) is inference too, unless the pinned
release's text states it. The map is working material: it feeds the existing analysis (How it works, RFC vs Cisco)
and the choice of Evidence bullets. It never adds a section, reorders the answer or changes its shape; show it only
when the user asks for it.

## 6. Running a round (parallel tool calls)

- A round is two or three messages: the planned searches and lookups (at most 4 calls per message), then one
  message of reads chosen from their hits (the strongest new sections and the nearby Restrictions / Defaults).
- Keep a ledger in your notes as you go: chunk ids seen, sections read (in full, or the offset to continue), RFCs
  looked up, commands found, and per round the number of new chunks and sections. Never re-read a section read in
  full; continue a partial one at its offset.
- Keep every result under Claude Code's size cap (over it, a result is saved to a file with a 2,000-character
  preview): `max_chars` as in section 0, never more than 40000 for a read.
- The Read line lists the sections of every round (and a `RAG:` line, where the layout has one, counts every call).

## 7. Quote check

Check each quote the answer will carry once, at most 4 `verify_quote` calls per message (labels and markers as
`${CLAUDE_SKILL_DIR}/citation-style.md` says). Pick the quotes from the concept map and the Evidence bullets first, so
no call checks a quote you then drop. Fill the spare slots of round 2's messages with the quotes round 1's reads
already gave you.

## 8. Copies (`also in`) and deep tiers

When the engine folds identical text (gateway 2.4.2 and later), a hit can be followed by the line
`also in (same text as hit N): doc_key [section] release R; ...`. The listed copies carry the hit's text word for word:
- Cite the copy at the pinned release when one is listed (the pins of release-drift.md: IOS XE 17.9.x, SD-WAN 20.9,
  Catalyst Center 2.3, ISE 3.1 as shipped):
  run `verify_quote` against that copy's section id and print its `local`. Write the other releases as "same text
  in 26.x / 17.18", which is no release drift for that passage. Prefer the pinned copy's section for a whole-section
  read.
- A copy is the same text: never a second independent source, a new section for the stop rule (section 3), or a
  separate Evidence bullet.

When results carry tier headers (`tier 1 (hits 1-8): ...`, `tier 2 (hits 9-16): ...`, `tier 3 (hits 17-32): ...`: an
evidence search sent without `limit` to gateway 2.4.3 or later (deep-32, below), or a search sent with `deep=true`):
- Tier 1 is the evidence: the same hits as without deep (full windows at the search's `max_chars`, exactly what the
  same search gives with `limit` 8). Rank, read and cite from tier 1 first, as always.
- Tiers 2 and 3 are candidates, not evidence. Never cite a tier 2 or tier 3 snippet on its own: read its section first
  (`get_section(ref=<the hit's section>)`), then cite from that text and check quotes with `verify_quote` against that
  section. A tier 3 hit has no cite: take it from `get_section(ref=..., format="json")` (its `cite` field), or build it
  from the doc and path as for any section.
- Tier 2 (hits 9-16) gives windows of at most 800 characters, never more than `max_chars` (500 at section
  0's value), with `cite` and `id`. Tier 3 (hits 17-32) gives snippets of at most 250 characters with no `cite` and no
  chunk `id`: use its doc and `section` ref, and keep it in the ledger (section 6) by its section. Never quote from a
  tier 2 or tier 3 window, not even a few words.
- `reranked="no"` marks a hit the reranker never scored (most of tier 3; every hit when the reranker is
  off or degraded): an unranked candidate, whose rank says nothing about how good it is.
- If tier 1 answers the question, use the deeper hits only to catch a contradiction or a platform or release
  difference, and mention one only after reading its section. Open the 1 to 3 candidates most likely to matter
  (matching doc, matching pinned release, a heading that names the feature), never all of them; those reads count in
  the round's reads.
- The release pins still apply: check a candidate's `release` before you read it.
- If a deep hit and a tier 1 hit disagree, read both sections before you say anything, then give the conflict with
  both cites. Never average the two.
- In JSON format the same hits carry `tier`, `reranked` and, for tiers 2 and 3, `get_section`: the same rules.

**Deep-32: evidence searches give no `limit`.** Send every evidence
search of sections 1 to 3 (`search_many` or `search_rag`: round 1's batches, round 2's targeted searches and "no hit"
sub-queries, the theory pass's search, each deeper round's searches) **without `limit` and without `deep`**, with
section 0's `max_chars`. Gateway 2.4.3 and later answers a search that gives neither with deep-32: the 32 tiered
hits above, about 17,000 to 19,000 characters at `max_chars` 500 (a `limit` 16 reply is about 12,000), well under the
size cap of section 6. An explicit `limit` gets exactly that many plain hits and `deep=false` the usual 8, as before.
- **Narrow lookups keep an explicit small `limit`** (3 is usual): re-finding a passage you already know, to get the
  section id for `verify_quote`; checking that one command, identifier or exact phrase occurs; a search inside the
  outline check (`${CLAUDE_SKILL_DIR}/outline-check.md`). A search that collects evidence is never narrow.
- **A gateway without deep-32** (before 2.4.3) answers a search without `limit` with at most 8 plain hits and no
  tier header line. When `rag_status`'s `edge:` line names a version before 2.4.3, or an evidence search sent without
  `limit` comes back with hits and no tier header (a narrow lookup's plain reply says nothing about the gateway),
  give `limit` 16 (20 at depth deep) on this answer's remaining evidence searches; every hit of such a reply is
  evidence.

**Modes: Full (32 hits), Half (16) and Light (8).** The search size of an answer at any depth. The user picks one by
saying full, half or light mode (or `/deep32 half: ...`). It holds for the rest of that answer; full is the
default.
- **Full** (default): evidence searches give no `limit`, as above (deep-32: the 32 tiered hits on gateway 2.4.3).
- **Half:** give `limit` 16 on every evidence search: 16 plain hits, no tiers.
- **Light:** give `limit` 8 on every evidence search: 8 plain hits, no tiers.
- In every mode, narrow lookups keep their own small `limit`. The tier rules above apply only when a reply carries
  tier headers. In half and light mode every hit is evidence, and the old-gateway fallback never comes up, because
  the limit is always given.
  Depth and mode are separate: "deep, light mode" runs section 3's rounds with `limit` 8. When the mode is not full,
  name it in the answer's last line ("half").
