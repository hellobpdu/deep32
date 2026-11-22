# Outline check: look before you write "not in corpus"

**Triggers:** a point the question asks about is left open, in any wording: "not in corpus", "not in the corpus",
"not found", "the docs don't say", "couldn't find", "unverified in the corpus", "I didn't run the outline check". Each
one means sections 1 to 3 below, before the reply is written.

"Not in corpus" is a claim about the corpus, and it needs evidence like any other claim. Long guides file a feature
under a chapter whose name the question does not use, so a served section can settle a point that no search reached.
This check reads the outlines of the likeliest documents before a gap is written. It adds no section to the answer.

## When

- After the last retrieval round (`${CODEX_HOME}/skills/deep32/quick-default.md`, or `${CODEX_HOME}/skills/deep32/deep-engine.md` for
  thorough and deep) and before the quote check. Run it for each point the answer needs that the rounds left open:
  - a Gaps entry;
  - an Evidence item;
  - a concept-map node that would say "not in corpus";
  - a part of the question left unanswered;
  - a sentence saying a document or command is missing from the corpus, or found only in an older release's document.
- Order: the points the question asks about first, then Evidence items, then concept-map nodes. The budget (section 4)
  decides the rest.

## 1. The likeliest documents (at most 2 per open point)

Take one document from each of the first two sources, so the check never rests only on the search that missed:
1. A Cisco doc_key that `${CODEX_HOME}/skills/deep32/blueprint.md` names for the topic (SKILL.md section 2, step 2): the
   first on the topic's line that fits the question's platform, in the release order of release-drift.md.
2. A Cisco doc_key of the closest hits so far (the best-ranked one, or the one that matched the most queries), when
   it is not the first document.
3. Only when one of these gives no document: the Cisco doc_keys that `lookup_id` returned for the question's
   identifiers, then `list_documents` with the `doc_type` (cisco or rfc). Pick the title that names the feature's
   area: an IP routing, security or high-availability guide, for example.

Prefer the configuration guide of the pinned release train (IOS XE 17.x for the 17.9 pin) and its command reference
over an older release's book or a TechNote. A document whose outline you already read in this answer for this point
counts as checked: reuse that outline. A section read without its outline does not: a gap is written only after
`get_outline` ran for that point.

## 2. The steps per document (about 3 calls)

1. `get_outline(doc, depth=1)` gives the chapters. Choose the chapter that names the feature, or its area, in the
   documents' own words, not the question's (`${CODEX_HOME}/skills/deep32/query-planning.md`).
2. `get_outline(doc, under=<chapter id>)` gives that chapter's sections. Find the heading that names the feature, its
   parameters or its defaults.
3. `get_section(ref=<that section id>)`. When the point is a limit or a default value, read its Restrictions, Defaults
   or "Information about" sibling in the same message.

Use the ledger of `${CODEX_HOME}/skills/deep32/deep-engine.md` (section 6). Never fetch an outline or read a section you
already have; continue a partial read at its offset. If no chapter in the list could hold the point, stop on that
document after step 1.

## 3. The outcome

- **The section settles the point.** It is ordinary evidence. Quote it, check the quote with `verify_quote` along
  with the others, and cite it with its `cite` and `local`. The gap is gone, so don't mention the check.
- **It does not.** Keep the gap, and say what you checked in the Gaps line (or the node, or the Evidence item):
  `not in corpus (outline checked: <doc_key> > <chapter>; <doc_key> > <chapter>)`. If no chapter matched, write
  `<doc_key> > no matching chapter`.
- **One label per open point, and it says what was done.** Write `outline checked:` only when `get_outline` ran on
  that document in this answer. A label that says the documents do not state the point (`not in corpus`,
  `lab-only`, `inference`, `release drift`) is written only after that, except the budget label, which says the check
  did not run. Each point left open carries exactly one of:
  - `not in corpus (outline checked: <doc_key> > <chapter>)`;
  - `lab-only (no document states it)`, with one lab check: the 17.9 command (confirmed with `find_command`) and
    what each outcome would show, labelled "lab check, not evidence";
  - `inference (from [cite] + [cite])`;
  - `release drift (only in <doc_key> <release>)`;
  - `disputed` (the documents disagree: quote both);
  - `not in corpus (outline not checked: budget)`.

  A side detail counts too: an unlabelled "not in corpus" anywhere in the reply is a defect. A `grep_corpus` that ran
  out of its scan budget, or came back truncated, proves nothing absent.
- **Never fill a gap from memory or the web.** The Web sources line of SKILL.md holds here word for word.

## 4. Budget

- The check sits outside every round budget: outside the default's round cap (`${CODEX_HOME}/skills/deep32/quick-default.md`)
  and, in thorough and deep, outside the rounds like the confirmations (`${CODEX_HOME}/skills/deep32/deep-engine.md`,
  section 0). It has its own caps:
  - up to 2 documents per open point;
  - about 3 calls per document;
  - about 12 calls per answer in all.
- Once the cap is reached, mark each point left as "not in corpus (outline not checked: budget)".
- `MAX_PARALLEL_CALLS = 4` (deep-engine.md section 0) still holds.
  - In Claude Code, never send more search calls in one message than that. Send the documents' outlines together,
    then their chapter outlines, then the reads.
  - In Codex, make the calls after the round scripts and before the quote script, never while a script runs.
