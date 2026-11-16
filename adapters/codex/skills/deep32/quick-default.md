# Quick default (every answer unless the user asks for more)

Target: about 2 to 4 minutes per answer, about 4 with long form. Same method, same evidence rules, fewer rounds.

## What changes

- **Read only what the question needs.** SKILL.md, this file, expanded-answer.md and citation-style.md. Do NOT read
  `deep-engine.md` (it belongs to thorough, deep and insane). Read query-planning, troubleshooting, collections,
  outline-check, rfc-pitfalls or release-drift only when SKILL.md's triggers for them fire.
- **Except section 8 of deep-engine.md** ("Copies (`also in`) and deep tiers", about 7 KB): read that one section
  before the first round (find it by its heading), so Full mode's 32 hits in three tiers are read and cited right.
- **No helpers, sub-agents or forks.** The whole answer happens in this thread.
- **Retrieval rounds by search mode: Light (limit 8) up to 6 rounds, Half (limit 16) up to 5, Full (32, the
  default) up to 4, plus one quote batch.** A cap, not a target: stop as soon as every point the answer rests on has
  a section read. Each round is ONE code-mode cell in Codex, or one parallel tool batch in Claude Code, with up to 4
  search calls in flight. Plan the whole round before it, never call one tool per turn.

## The rounds

1. **Recall** (one batch): `rag_status` (first answer of the session only), `lookup_id` for each identifier, one
   `search_many` with 4 to 6 queries (identifiers and exact CLI `mode: "bm25"`, paraphrases of the what AND the why
   `mode: "dense"`), `find_command` (release 17.9) for each command the answer will show. Search modes as SKILL.md
   says: Full (no `limit`, the default), Half (`limit` 16), Light (`limit` 8).
2. **Read** (one batch): `get_section` for the 3 to 6 best sections (the mechanism's defining section, the command
   reference entries, the nearby Restrictions / Defaults when the question is about them), with `max_chars` about
   8000 each so the round stays small; `lookup_rfc` for every RFC you will cite; `grep_corpus` for exact show-output
   lines when needed. Tier 2 and 3 hits are read here before they are cited.
3. **Follow the threads and close the gaps** (rounds 3 up to the mode's cap, one batch each, only while they bring
   something the answer needs): searches at the mode's limit for threads rounds 1 and 2 opened, reads of the
   sections they point to, or the outline check before any "not in corpus". Long form (verbose, chapter) may use
   these rounds for the configuration, verification and production material its extra sections need.
4. **Quotes** (one batch): `verify_quote` for every quote the answer carries, all in one batch.

Stop there. The expanded-answer theory pass is folded into these rounds (its material comes from the sections read),
not run as extra calls. If the answer still has an open point, it goes to Confidence and gaps, said as such, and the
last line may offer "say thorough or deep for the full engine".

Degraded search: if any tool reply begins with a "degraded:" line (dense, reranker, kb or structure), the search ran
below full strength. Say so: put one line at the top of the answer, "Search degraded: <list> (dense down =
keyword-only recall; reranker down = unreranked order)", and repeat the state in the closing line. Never present an
answer built on degraded search as full-strength.

## Writing

The expanded-answer.md layout and sizes stay (long form included): comprehensive, explanatory, cited. Write once,
from what is in the thread; do not re-read results already seen.

## More depth, on request

- **thorough**: `deep-engine.md`'s wide first round, one thread-following round and the concept map, plus the full
  theory pass.
- **deep** / **insane**: the deep engine until two rounds in a row bring nothing new.
