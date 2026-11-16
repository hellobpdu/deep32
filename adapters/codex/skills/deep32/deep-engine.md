# Deep retrieval engine: wider, deeper rounds

This file changes only **how much** SKILL.md section 2 retrieves, strictly from the knowledge base and with the same
method: it replaces section 2's budget
("about 8 to 15 tool calls") and its early stop ("once two independent sources agree"). Everything else stays as it
is: section 1 (the tool check), the rules of section 2 (identifiers, documents' terms, `find_command` at 17.9, whole
sections, `lookup_rfc` standing, RFC vs Cisco, `verify_quote`, the `cite` attribute), section 3, `citation-style.md`,
`expanded-answer.md` and the answer's shape (Evidence, then the analysis, then the `RAG:` line). It runs on the
deep32 tools only: "not in corpus" where the documents are silent, never the web. The Claude twin of this skill
runs the same rounds with parallel tool calls instead of the code-mode scripts below.

## Who runs which depth

| Depth | Who | Rounds | Stop |
|---|---|---|---|
| `default` | a deep32 answer when the user says "thorough" | 1 (wide) and 2 (threads) | after round 2 |
| `deep` | the user says "deep" or "insane" (both mean the same) | 1, 2, 3 ... | two rounds in a row that bring no new section into a search's top 5 hits, or every branch closed or out of budget (section 3) |

## 0. Constants, budgets and time

- **`MAX_PARALLEL_CALLS = 4`**: never more search calls in flight. The engine still runs them one at a time, so the
  gain is fewer model steps, not faster searches. If it is ever raised, change it here, in both scripts and in the
  Claude twin, to the same value. Run one engine or quote script at a time, make no other search call while one
  runs, and spawn no other agent during an engine run (section 3), so the cap holds for the whole thread, not only
  inside one script.
- **Confirmations** are outside the per-round budget, as `verify_quote` is: `rag_status`, `find_command` for each
  command you will cite, `lookup_rfc` for each RFC you will cite. Put them in a round's `JOBS`; the script runs them
  whatever the budget (still at most 4 in flight) and counts them in its calls.

| Per round | default | deep |
|---|---|---|---|
| calls you write that explore (searches, `lookup_id`, `grep_corpus`, `get_outline`, your own reads) | 8, then 10 | 12 |
| strongest new sections the script reads whole | 4, then 3 | 4 |
| nearby Restrictions / Defaults sections it reads | 2, then 1 | 2 |
| search `limit` / `max_chars`, unless you set them (evidence searches give no `limit`, section 8) | none, deep-32 (16 once a gateway without it answers) / 500 | none, deep-32 (20 likewise) / 400 |
| section read `max_chars` | 9000 | 9000 |

- **Time (measured in Claude Code on 4 questions under load):** there, this depth took 63-210 s more than a
  section 2 answer, mean 141 s; the search wait was under 25 s per answer, so the cost is model steps over a larger
  context. In Codex the default adds about one round step and one quote step to a section 2 answer.

## 1. Round 1: wide and parallel

1. Plan 8 to 12 sub-queries in the documents' words (`query-planning.md`), one or more for each angle that applies:

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
   round: `lookup_id` for each identifier, `find_command` (release 17.9) for each command the question names,
   `lookup_rfc` for the governing RFC, and `rag_status` if this thread has not called it yet. A `lookup_id` row from
   an opt-in collection (`collections.md`) is opt-in material too: skip it unless the question is one `collections.md`
   covers.
   **`release_max` narrows; it never decides.** Use it only on a group whose Cisco doc_keys include a 17.9 book (`c9300-179-*`, `c9400-179-*`, `c9k-179-*`, `iosxe-179-*`), and send the same
   queries unfiltered in the same round: the filter removes every newer document whole. Skip it where no
   pinned-release book exists: the C9300 Layer 2, VLAN, QoS, interface and hardware, system-management and IP-services
   guides are 17.18.x and 26.x only (`release-drift.md`). A hit newer than the pin is labelled with its release, never dropped.
   On an ISE configuration group the cap is `release_max: "3.1"`, never `"17.9"` (ISE automation follows
   `collections.md`).
3. The round script (section 6) runs them at most 4 at a time and drops every hit already seen (by chunk id) or
   inside a section already read. Then it reads whole the strongest new sections (best fused rank, hit by the most
   queries, at most 2 per document) and the nearby Restrictions, Prerequisites, Defaults, Usage Guidelines and
   Feature History sections that the hits or the reads' `prev` / `next` point to. It prints its summary first (calls,
   what was new, what it skipped, the leads, the next step), then the new hits and the sections. A hit's `also in`
   line and a deep answer's tiers: section 8.

## 2. Round 2: follow the threads

From what round 1 read (the script's `leads:` list and your own reading), run targeted calls:
- each RFC a Cisco text implements or names: `lookup_rfc` (standing, errata), then `get_section` on the RFC section
  the Cisco text maps to; for an RFC read in round 1, the Cisco side;
- commands and features named in restrictions, notes, see-also and usage guidelines: `find_command` (release 17.9),
  `lookup_id`;
- each fix or workaround the reads suggest (a knob, an ACL, a PACL): one bm25 query for its restrictions on the
  pinned platform (`release_max: "17.9"` under section 1 step 2's rule: the same query unfiltered beside it, and
  unfiltered alone where no 17.9 book covers the area);
- error strings and syslog messages: `grep_corpus` (with `docs` when you know the document);
- each "no hit" sub-query: alone with `search_rag` and `docs` from the best hits so far;
- adjacent sections: a partial read continued, Restrictions and Defaults not read yet, the sibling a section refers
  to (`get_outline(doc, under=<chapter>)` finds them);
- commands that `collections.md` names newer-version and differences pages for: the script's leads are Cisco-shaped,
  so add these reads yourself. For each such command the answer uses, read whole the newer-version page's section of
  every parameter the answer relies on or the question names, its Notes, and the differences page's section for its
  area. The answer then names every version difference these sections document for those commands and parameters,
  not only the ones the question lists, and what each of those parameters does as documented (a credential
  parameter, for example: sent up front, or only when the server sends an authentication challenge);
- each default, timer, limit or command the answer states: read its command-reference entry (the section
  of its `find_command` row: the row only points at it) and set it against the configuration guide's Defaults or
  Restrictions and any TechNote sample. Agreeing: cite both. Differing: quote both, mark it **disputed**, and give the
  lab check;
- a default, value, exception or restriction the answer will state that you have seen only in a hit
  window: read its section whole (or continue it at its offset) before it goes into Evidence or the concept map.
  Add them as your own `get_section` jobs here; one still open when the budget ends is read as a plain call
  before the quote script, outside the budget like a confirmation;
- when no section read in round 1 has a heading that names the feature, start this round with
  `get_outline(<the likeliest Cisco guide>, depth=1)`, then `get_outline(doc, under=<chapter>)` and the section
  that names it, inside the round budget;
- a call that came back with an error: the script did not mark it done, so the same call may run again.

Round 2 always runs, small when round 1 left few leads. It is also `expanded-answer.md`'s theory pass: spend that
pass's extra calls only on what the two rounds left open. Then build the concept map (section 5), check the quotes
(section 7) and write.

## 3. Deep and insane

"deep" and "insane" mean the same: split the question into topics and branches (a `foundations` branch for the
defining overview, Prerequisites, Restrictions, Defaults and RFC operation sections, and a `counter` branch for
counter-evidence), with Coverage in the answer. The engine drives the rounds: run them with the round script and
`DEPTH = "deep"`, every job tagged with its branch (`branch: "<name>"`; an untagged job counts as branch `main`).
- Round 1 covers section 1's angles for every branch. Each later round takes its new sub-queries from the new hits'
  terms and the leads (section 2), plus the reads they need. At most 12 exploring calls per round, plus
  confirmations; the summary comes first, so a long round never cuts it off.
- **What counts as new:** a section that no earlier hit or read showed, among a search's top 5 hits. New chunk ids
  inside sections already seen, lower-ranked hits and the sections a lookup points at do not count (the summary
  still prints them).
- **Stop** when two whole rounds in a row bring nothing new (the script prints `DRY`), when every branch is closed
  or out of budget (it says so), or when you judge that the last two rounds added no new relevant evidence.
- **Branch budgets, the backstop:** a branch closes after 2 searches in a row with nothing
  new; it has 20 calls (searches, its reads and lookups), extended to 40 while it still finds new sections
  (`foundations`: 40, then up to 60). The script enforces them; a branch that spent its maximum while still finding
  new sections is "unsaturated" in Coverage. Confirmations are never cut by a branch budget.
- **No other agents:** the rounds do all retrieval, so at most 4 search calls are ever in flight. Every 4 rounds the
  script asks for one commentary line for the user (rounds, calls, open branches); post it and go on.
- Build section 5's concept map for each topic before writing. Coverage gives, per branch, the calls used and
  whether it ran dry, ran out of budget or stayed unsaturated, and how many rounds ran.


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
release's text states it. The map is working material: it feeds the existing analysis (How it works, RFC vs Cisco,
the Analysis section) and the choice of Evidence bullets. It never adds a section, reorders the answer or changes its
shape; show it only when the user asks for it.

## 6. Round script (code mode)

One round per `exec`. Copy the script exactly and edit only `KEY`, `DEPTH`, `MODE` (section 8, Modes), `JOBS` (and
`SEED_READ` in round 1).
`store` and `load` keep the ledger between the rounds of this thread; a sub-agent has its own. A new question in the
same thread gets a new `KEY`. A call is marked done only when it succeeds. The `RAG:` line counts the calls in the
script's summary line plus the quote checks and any call made outside the scripts.

```js
// @exec: {"max_output_tokens": 30000}
// deep32 deep engine: ONE round per exec. Edit only KEY, DEPTH, MODE and JOBS (and SEED_READ in round 1).
const KEY = "engine";      // one ledger per question: a new question in this thread gets a new key ("engine-q2")
const DEPTH = "default";   // "default" (the user said thorough) | "deep" (the user said deep or insane)
const MODE = "full";       // "full" (deep-32) | "half" (limit 16) | "light" (limit 8): the user said full, half or light mode
const JOBS = [
  // { tool: "search_many", args: { queries: [{ text: "area nssa translate type7 suppress-fa", mode: "bm25" }, { text: "why the ABR sets the forwarding address to zero", mode: "dense" }] } },
  // { tool: "lookup_id", args: { identifier: "LSA type 7" } },
  // { tool: "find_command", args: { command: "show ip ospf database nssa-external", release: "17.9" } },
  // { tool: "lookup_rfc", args: { rfc: 3101 } },
  // { tool: "grep_corpus", args: { pattern: "%OSPF-4-", docs: ["iosxe-179-sysmsg-adj"] } },
  // { tool: "get_section", args: { ref: "rfc3101#2.3" } },
  // deep mode: add branch: "<name>" to every job ("foundations" has the larger budget)
];
const SEED_READ = [];      // round 1 only: section ids already read in full in this thread
// ---- engine: do not edit below this line ----
const MAX_PARALLEL_CALLS = 4;  // search calls in flight, never more (the engine runs them one at a time)
const PLAN = {                 // per round (the last entry repeats): JOBS calls, auto reads, Restrictions/Defaults reads
  default: { rounds: 2, jobs: [8, 10], reads: [4, 3], near: [2, 1], limit: 16, chars: 500, read: 9000, deep32: true },
  deep: { rounds: Infinity, jobs: [12], reads: [4], near: [2], limit: 20, chars: 400, read: 9000, deep32: true },
}[DEPTH];
// deep32: an evidence search goes without limit, so gateway 2.4.3+ answers it with deep-32 (32
// hits in three tiers); limit is then sent only once an edge without deep-32 answered untiered (L.flat), as before. A
// job's own limit (a narrow lookup) is kept.
// modes: the evidence-search limit of a default or deep round. "full" sends none (deep-32 above),
// "half" 16 and "light" 8 plain hits. Round 1 fixes it for the question (the ledger keeps it); a job's own limit is
// kept in every mode
const MODES = { full: undefined, half: 16, light: 8 };
// confirmations: outside the JOBS budget (as verify_quote is), still under MAX_PARALLEL_CALLS and still counted
const CONFIRM = new Set(["rag_status", "find_command", "lookup_rfc"]);
// deep: the per-branch budgets as the backstop (base, then extended while the branch still finds new sections)
const BRANCH = { base: 20, max: 40 }, FOUNDATIONS = { base: 40, max: 60 };
// "new" for the stop test: a section never seen before among a search's top TOP_NEW hits. SNIP: a hit shows its whole
// window (the search's max_chars; the engine centres the window on the best line)
const OUT_CHARS = 100000, SEARCH_SHARE = 0.5, SNIP = PLAN?.chars ?? 500, OTHER_MAX = 6000, PER_DOC = 2, NO_NEW_STOP = 2,
  TOP_NEW = 5, CHECKPOINT = 4;
// deep-32 candidates (tiers 2 and 3) take what the tier 1 lines left of the search share, at most CAND_SHARE of the
// output per step, so the section reads keep their size
const CAND_SHARE = 0.2;
// a folded copy at a release pin (as shipped: IOS XE 17.9, SD-WAN 20.9, Catalyst Center 2.3, ISE 3.1; change PIN with
// the pins table of release-drift.md): its section is read and cited
const PIN = /\[([^\]\s]+#[^\]\s]+)\] release (?:17\.9|20\.9|2\.3|3\.1)(?![0-9])/;
const NEAR = /restriction|prerequisite|limitation|default|guideline|usage|caveat|feature-history|feature-information/i;
const STOP = "a|an|the|that|this|these|those|how|what|which|why|when|where|whether|if|it|its|you|your|up|in|on|of|to"
  + "|for|as|is|are|be|by|and|or|with|then|from|only|any|some|more|no|not|command|commands|output|again|also";
const CLI = new RegExp("\\b(?:show|debug|clear)(?: (?!(?:" + STOP + ")\\b)[a-z][a-z0-9-]*){1,6}", "g");
const at = (a, r) => a[Math.min(r - 1, a.length - 1)];
const asText = o => typeof o === "string" ? o
  : Array.isArray(o?.content) ? o.content.map(c => c?.text ?? "").join("\n") : JSON.stringify(o);
const attrs = s => Object.fromEntries([...s.matchAll(/(\w+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
const isSearch = t => t === "search_many" || t === "search_rag";
const short = a => JSON.stringify(a).slice(0, 120);
const sigOf = j => j.tool === "lookup_rfc" ? `lookup_rfc:${String(j.args.rfc).replace(/\D/g, "")}`
  : j.tool === "find_command" ? `find_command:${String(j.args.command).toLowerCase().trim()}`
  : `${j.tool}:${JSON.stringify(j.args)}`;
const capOf = b => b === "foundations" ? FOUNDATIONS : BRANCH;

async function round() {
  if (!PLAN) return `DEPTH must be "default" or "deep", not ${JSON.stringify(DEPTH)}: nothing run.`;
  if (!["full", "half", "light"].includes(MODE)) return `MODE must be "full", "half" or "light", not ${JSON.stringify(MODE)}: nothing run.`;
  const L = load(KEY) ?? { round: 0, seen: {}, read: Object.fromEntries(SEED_READ.map(s => [s, "full"])),
    sec: Object.fromEntries(SEED_READ.map(s => [s, 0])), touched: {}, done: {}, novel: [], calls: 0, dry: false,
    stop: "", br: {} };
  L.sec ??= {}; L.novel ??= [];
  if (PLAN.deep32) L.mode ??= MODE;  // round 1 fixes the mode for this question
  const mode = PLAN.deep32 ? L.mode : "full", evLimit = MODES[mode];
  const r = L.round + 1;
  if (L.dry || L.stop || r > PLAN.rounds)
    return `== engine ${DEPTH}: ${L.dry ? `dry since round ${L.round}` : L.stop ? L.stop : `its ${PLAN.rounds} rounds are done`};`
      + ` nothing run. Build the concept map, check the quotes and write. search calls so far: ${L.calls} (quote checks not counted).`;
  const out = [], skipped = [], cands = new Map(), reads = [];
  const leads = { rfc: new Set(), cmd: new Set(), msg: new Set(), noHit: [], partial: new Set(), near: new Set() };
  let novel = 0, freshChunks = 0, freshReads = 0, freshRefs = 0, used = 0, calls = 0, cut = 0, flat = false;
  const unshown = new Set(), tier3 = new Set();
  const brOf = b => (L.br[b] ??= { calls: 0, noNew: 0, closed: false, last: 1 });
  const spent = (b, pending = 0) => {  // deep only: the branch's budget is used up
    const x = L.br[b]; if (!x) return false;
    const c = capOf(b), n = x.calls + pending;
    return n >= c.max || (n >= c.base && !(x.last > 0));
  };

  async function pool(jobs) {
    const res = new Array(jobs.length);
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(MAX_PARALLEL_CALLS, jobs.length) }, async () => {
      while (next < jobs.length) {
        const k = next++, j = jobs[k];
        try { res[k] = { j, t: asText(await tools["mcp__deep32__" + j.tool](j.args)) }; }
        catch (e) { res[k] = { j, err: String(e?.message ?? e) }; }
      }
    }));
    calls += jobs.length;
    for (const j of jobs) if (j.branch) brOf(j.branch).calls++;
    // a call is "done" (never re-run with the same arguments) only once it succeeded: an error may be retried
    for (const { j, err } of res) if (!err && j.tool !== "get_section" && j.sig) L.done[j.sig] = r;
    return res;
  }
  function scanLeads(text, self) {
    const selfRfc = /^rfc(\d+)#/.exec(self ?? "")?.[1];
    for (const m of text.matchAll(/\bRFC[ -]?(\d{3,5})\b/g))
      if (m[1] !== selfRfc && !L.done[`lookup_rfc:${m[1]}`]) leads.rfc.add(m[1]);
    for (const m of text.matchAll(CLI)) if (!L.done[`find_command:${m[0]}`]) leads.cmd.add(m[0]);
    for (const m of text.matchAll(/%[A-Z][A-Z0-9_]+-[0-7]-[A-Z0-9_]+/g)) leads.msg.add(m[0]);
  }
  function touch(t) {  // sections a lookup, find_command or grep row points at
    for (const m of t.matchAll(/section_id: ([^\s;|\]]+#[^\s;|\]]+)|\[([^\s\]]+#[^\s\]]+)\]/g)) {
      const s = m[1] ?? m[2];
      if (!L.touched[s]) { L.touched[s] = r; freshRefs++; }
    }
  }
  // a hit shown: seen from now on, its section a read candidate (a tier 3 hit alone never makes it an automatic read),
  // its text scanned for leads; 1 when its section is new among the top TOP_NEW hits
  function keep(h, j) {
    const { key, a, n, tier, ai, pin } = h;
    let top = 0;
    L.seen[key] = r;
    if (a.section) {
      if (L.sec[a.section] === undefined) { L.sec[a.section] = r; if (n <= TOP_NEW) top = 1; }
      L.touched[a.section] ??= r;
      // a copy is the same text: never a new section later; a copy at a release pin is the section read whole
      if (ai) for (const s of ai.matchAll(/\[([^\]\s]+#[^\]\s]+)\]/g)) { L.sec[s[1]] ??= r; L.touched[s[1]] ??= r; }
      const rs = pin && L.read[pin] !== "full" ? pin : a.section;
      const c = cands.get(rs) ?? { score: 0, doc: rs.split("#")[0], near: NEAR.test(rs), branch: j.branch, ev: false };
      c.score += 1 / n + 0.25 * (a.q ?? "").split(",").filter(Boolean).length;
      if (tier < 3 || !PLAN.deep32) c.ev = true;  // tiers 1 and 2 are evidence candidates; tier 3 is read first
      cands.set(rs, c);
    }
    used += h.line.length;
    scanLeads(h.body, a.section);
    return top;
  }
  const head = b => `${b.tag} ${short(b.j.args.queries ?? b.j.args.query)}: ${b.fresh} new (${b.top} new sections in the top ${TOP_NEW}), `
    + `${b.dup} already seen${b.hidden ? `, ${b.hidden} not shown (output budget; still unseen)` : ""}`
    + (b.pq ? `\n  ${b.pq}` : "")
    + (b.tiers.length ? `\n  deep tiers: ${b.tiers.map(([k, lo, hi]) => `tier ${k} = hits ${lo}-${hi}`).join(", ")} (tiers 2 and 3: candidates, read the section before citing)` : "");
  function render(b) {  // a search's block: its summary line, then the hit lines shown (b.pre: reserved when deferred)
    const s = head(b) + (b.lines.length ? "\n" + b.lines.join("\n") : "");
    used += s.length - b.lines.reduce((n, l) => n + l.length, 0) - (b.pre ?? 0);
    return s;
  }
  // deep-32 candidates (tiers 2 and 3) of this step's searches, once every search's tier 1 is in: what tier 1 left of
  // the search share, at most CAND_SHARE of the output, offered in rank order across the searches (each search's next
  // candidate in turn). One that does not fit is not shown and not marked seen, so a later round can still surface it.
  function fit(blocks) {
    let room = Math.min(OUT_CHARS * CAND_SHARE, OUT_CHARS * SEARCH_SHARE - used);
    const most = Math.max(...blocks.map(b => b.later.length));
    for (let k = 0; k < most; k++) for (const b of blocks) {
      const h = b.later[k];
      if (!h) continue;
      if (L.seen[h.key]) { b.dup++; continue; }
      if (h.line.length > room) { b.hidden++; cut++; continue; }
      room -= h.line.length; b.fresh++; freshChunks++;
      keep(h, b.j); b.lines.push(h.line);
    }
    for (const b of blocks) out[out.indexOf(b)] = render(b);
  }
  function show(res, origin) {
    const blocks = [];
    for (const { j, t, err } of res) {
      const tag = `[${origin}${j.branch ? " " + j.branch : ""}] ${j.tool}`;
      if (err) { out.push(`${tag} ${short(j.args)} ERROR: ${err} (not marked done: a later round may retry it)`); used += 200; continue; }
      if (isSearch(j.tool)) {
        const lines = [], later = [];
        let fresh = 0, dup = 0, top = 0, hidden = 0, nHits = 0;
        // gateway 2.4.2+: the copies the engine folded into hit n, on their own line after its </hit>
        //   "also in (same text as hit n): doc_key [section] release R; ..."
        const alsoIn = new Map([...t.matchAll(/^also in \(same text as hit (\d+)\): (.*)$/gm)].map(x => [Number(x[1]), x[2].trim()]));
        // a deep answer (gateway 2.4.3+ without limit, or deep=true): a "tier T (hits a-b): ..." line before each
        // tier's first hit; tier 1 is the evidence, tiers 2 and 3 are candidates (placed by fit, after every tier 1)
        const tiers = [...t.matchAll(/^tier ([123]) \(hits (\d+)-(\d+)\)/gm)].map(x => [Number(x[1]), Number(x[2]), Number(x[3])]);
        const tierOf = n => (tiers.find(([, lo, hi]) => n >= lo && n <= hi) ?? [0])[0];
        for (const m of t.matchAll(/<hit\b([^>]*)>([\s\S]*?)<\/hit>/g)) {
          nHits++;
          const a = attrs(m[1]), key = a.id ?? `${a.section}|${m[2].slice(0, 120)}`;
          const n = Number(/\bn=(\d+)/.exec(m[1])?.[1] ?? 30);
          if (L.seen[key] || L.read[a.section] === "full") { dup++; continue; }
          const tier = tierOf(n), ai = alsoIn.get(n), pin = ai ? PIN.exec(ai)?.[1] : undefined;
          const line = `- ${a.section} | ${a.cite ?? `${a.doc ?? String(a.section ?? "").split("#")[0]} (no cite: take it from its section)`}`
            + `${a.release ? " | rel " + a.release : ""}${a.status ? " | " + a.status : ""}`
            + (tier > 1 ? ` | tier ${tier}: a candidate, not evidence (read its section before citing it)` : "")
            + `\n  ${m[2].trim().replace(/\s+/g, " ").slice(0, SNIP)}`
            + (ai ? `\n  also in (same text; cite the pinned-release copy): ${ai}` : "");
          const h = { key, a, n, tier, ai, pin, line, body: m[2] };
          if (tier > 1 && PLAN.deep32) { later.push(h); continue; }
          // over the search share of the output: not shown and not marked seen, so a later round can still surface it;
          // a new section among the top hits still counts as new (the material is there, only unshown)
          if (used + line.length > OUT_CHARS * SEARCH_SHARE) {
            hidden++;
            if (a.section && n <= TOP_NEW && L.sec[a.section] === undefined && !unshown.has(a.section)) { unshown.add(a.section); top++; }
            continue;
          }
          fresh++; top += keep(h, j); lines.push(line);
        }
        freshChunks += fresh; novel += top; cut += hidden;
        if (j.branch) {
          const x = brOf(j.branch);
          x.last = top; x.noNew = top ? 0 : x.noNew + 1;
          if (x.noNew >= NO_NEW_STOP) x.closed = true;
        }
        // a gateway without deep-32 (before 2.4.3) answers a search without limit untiered
        if (PLAN.deep32 && nHits && !tiers.length && j.args.limit === undefined && j.args.deep !== false) flat = true;
        const pq = (t.match(/^per query:.*$/m) ?? [""])[0];
        for (const q of pq.matchAll(/(?:^per query: |; )(\d+)(?: \(weight [^)]*\))? → no hit/g))
          leads.noHit.push(j.args.queries?.[Number(q[1]) - 1]?.text ?? j.args.query);
        const b = { tag, j, fresh, dup, top, hidden, pq, tiers, lines, later };
        // a block whose candidates wait for fit: its summary line counts now, so the next search's tier 1 and fit see it
        if (later.length) { b.pre = head(b).length + lines.length; used += b.pre; blocks.push(b); out.push(b); } else out.push(render(b));
      } else if (j.tool === "get_section") {
        const a = attrs((t.match(/<section\b([^>]*)>/) ?? ["", ""])[1]);
        const m = (a.chars ?? "").match(/\d+-(\d+)\/(\d+)/);
        const ref = j.args.ref, done = !m || Number(m[1]) >= Number(m[2]);
        if (L.read[ref] === undefined) freshReads++;
        L.read[ref] = done ? "full" : Number(m[1]);
        L.touched[ref] ??= r; L.sec[ref] ??= r;
        if (a.section && a.section !== ref) { L.read[a.section] = L.read[ref]; L.touched[a.section] ??= r; L.sec[a.section] ??= r; }
        if (!done) leads.partial.add(`${ref} (continue at offset ${m[1]} of ${m[2]})`);
        for (const nb of [a.prev, a.next]) if (nb && NEAR.test(nb) && L.read[nb] !== "full") reads.push(nb);
        const body = (t.match(/<section\b[^>]*>([\s\S]*?)<\/section>/) ?? [, t])[1];
        scanLeads(body, ref);
        out.push(`${tag} ${ref}${done ? "" : ` (partial ${a.chars})`}\n${t}`); used += t.length;
      } else {
        touch(t);
        const s = `${tag} ${short(j.args)}\n${t.length > OTHER_MAX ? t.slice(0, OTHER_MAX) + "\n[cut at " + OTHER_MAX + " chars]" : t}`;
        out.push(s); used += s.length;
      }
    }
    if (blocks.length) fit(blocks);
  }
  const readJob = (ref, origin, budgetLeft, n, branch) => {
    const st = L.read[ref];
    const max = Math.max(1500, Math.min(PLAN.read, Math.floor(budgetLeft / Math.max(1, n))));
    return { tool: "get_section", origin, ...(branch ? { branch } : {}),
      args: { ref, max_chars: max, ...(typeof st === "number" ? { offset: st } : {}) } };
  };

  // phase 1: the jobs you wrote
  const run = [], sigs = new Set(), pend = {};
  let budgeted = 0;
  for (const j of JOBS) {
    j.args ??= {};
    if (DEPTH === "deep") j.branch ??= "main";
    const confirm = CONFIRM.has(j.tool);
    if (j.tool === "verify_quote") { skipped.push("verify_quote: use the quote-check script"); continue; }
    if (!confirm && j.branch && L.br[j.branch]?.closed) { skipped.push(`${j.branch}: branch closed (2 searches in a row with no new section in their top ${TOP_NEW})`); continue; }
    if (!confirm && DEPTH === "deep" && spent(j.branch, pend[j.branch] ?? 0)) {
      skipped.push(`${j.branch}: branch budget spent (${L.br[j.branch].calls} calls; Coverage: ${L.br[j.branch].calls >= capOf(j.branch).max ? "unsaturated" : "ran dry at its budget"})`); continue;
    }
    if (j.tool === "get_section") {
      const st = L.read[j.args.ref];
      if (st === "full") { skipped.push(`${j.args.ref}: already read in full`); continue; }
      if (typeof st === "number" && j.args.offset == null) j.args.offset = st;
      j.args.max_chars ??= PLAN.read;
      j.sig = `get_section:${j.args.ref}:${j.args.offset ?? 0}`;
    } else {
      j.sig = sigOf(j);
      if (L.done[j.sig]) { skipped.push(`${j.tool} ${short(j.args)}: already run in round ${L.done[j.sig]}`); continue; }
    }
    if (sigs.has(j.sig)) { skipped.push(`${j.tool} ${short(j.args)}: listed twice in this round`); continue; }
    if (!confirm) {
      if (budgeted >= at(PLAN.jobs, r)) { skipped.push(`${j.tool} ${short(j.args)}: over this round's ${at(PLAN.jobs, r)}-call budget`); continue; }
      budgeted++;
    }
    sigs.add(j.sig);
    if (j.branch) pend[j.branch] = (pend[j.branch] ?? 0) + 1;
    if (isSearch(j.tool)) {
      if (!PLAN.deep32 || (evLimit === undefined && L.flat)) j.args.limit ??= PLAN.limit;
      else if (evLimit !== undefined) j.args.limit ??= evLimit;
      j.args.max_chars ??= PLAN.chars;
    }
    run.push(j);
  }
  show(await pool(run), "job");
  const flatNow = flat && !L.flat;
  if (flatNow) L.flat = r;

  // phase 2: the strongest new sections, read whole (at most PER_DOC per document)
  const mine = new Set(run.filter(j => j.tool === "get_section").map(j => j.args.ref));
  const ranked = [...cands.entries()].filter(([s]) => L.read[s] !== "full" && !mine.has(s)).sort((x, y) => y[1].score - x[1].score);
  const perDoc = {}, auto = [];
  for (const [s, c] of ranked) if (!c.ev) tier3.add(s);  // hit in tier 3 only (deep-32): a lead, never an automatic read
  for (const [s, c] of ranked) {
    if (auto.length >= at(PLAN.reads, r)) break;
    if (!c.ev || c.near || (perDoc[c.doc] ?? 0) >= PER_DOC) continue;
    perDoc[c.doc] = (perDoc[c.doc] ?? 0) + 1;
    auto.push(s);
  }
  const nearHits = ranked.filter(([s, c]) => c.near && c.ev && !auto.includes(s)).map(([s]) => s);
  const left = () => OUT_CHARS - used;
  show(await pool(auto.map(s => readJob(s, "read", left() * 0.7, auto.length, cands.get(s)?.branch))), "read");

  // phase 3: nearby Restrictions, Prerequisites, Defaults, Usage Guidelines and Feature History sections
  const near = [...new Set([...nearHits, ...reads])].filter(s => L.read[s] !== "full" && !mine.has(s) && !auto.includes(s));
  const take = near.slice(0, at(PLAN.near, r));
  for (const s of near.slice(take.length)) leads.near.add(s);
  show(await pool(take.map(s => readJob(s, "near", left() * 0.9, take.length, cands.get(s)?.branch))), "near");

  // the ledger and the summary (printed first, so it survives a cut output)
  L.round = r; L.calls += calls; L.novel.push(novel);
  const dry = DEPTH === "deep" && L.novel.length >= NO_NEW_STOP && L.novel.slice(-NO_NEW_STOP).every(x => x === 0);
  const brs = Object.entries(L.br);
  const allDone = DEPTH === "deep" && brs.length > 0 && brs.every(([b, x]) => x.closed || spent(b));
  L.dry = dry;
  if (!dry && allDone) L.stop = `every branch closed or spent its budget at round ${r}`;
  store(KEY, L);
  const list = (name, s, n = 8) => s.size || s.length ? `\n- ${name}: ${[...s].slice(0, n).join("; ")}` : "";
  const leadText = list("RFCs to lookup_rfc (standing, errata) and read where the Cisco text maps to them", leads.rfc)
    + list("commands to confirm with find_command (release 17.9)", leads.cmd)
    + list("messages to grep_corpus", leads.msg)
    + list('"no hit" queries: send each alone with search_rag (docs= the doc_keys of the best hits)', leads.noHit.filter(Boolean))
    + list("partial sections to continue", leads.partial)
    + list("Restrictions/Defaults sections not read yet", leads.near)
    + list("sections hit only in tier 3 (unranked snippets): read the 1 to 3 that matter as your own get_section jobs", tier3);
  const last = r >= PLAN.rounds;
  let next = dry ? `DRY: two rounds in a row brought no new section into a search's top ${TOP_NEW}. Stop: build the concept map, check the quotes, write.`
    : allDone ? "Every branch is closed or has spent its budget. Stop: build the concept map, check the quotes, write (Coverage: per branch, dry or unsaturated)."
    : last ? "Rounds done. Build the concept map, check the quotes (quote-check script), write."
    : DEPTH === "deep" ? `Next: round ${r + 1} (up to ${at(PLAN.jobs, r + 1)} calls plus confirmations), with new sub-queries from the new hits' terms and the leads below, and the reads they need. Stop early if you judge the last two rounds added no new relevant evidence.`
    : `Next: round ${r + 1} (up to ${at(PLAN.jobs, r + 1)} calls plus confirmations): follow the threads below and in what you read (RFC vs Cisco, see-also, restrictions, adjacent sections).`;
  if (DEPTH === "deep" && !dry && !allDone && r % CHECKPOINT === 0)
    next += ` CHECKPOINT (round ${r}): post one commentary line for the user (rounds, calls, open branches), then go on.`;
  const branches = brs.map(([b, x]) => `${b} ${x.calls}${x.closed ? " (dry)" : spent(b) ? " (budget spent)" : ""}`).join(", ");
  const summary = `== engine ${DEPTH} round ${r}${PLAN.rounds === Infinity ? "" : "/" + PLAN.rounds}: ${calls} calls (${L.calls} so far; quote checks not counted)`
    + (mode !== "full" ? ` | ${mode} mode: evidence searches send limit ${evLimit}` : "")
    + ` | new: ${novel} sections in a top-${TOP_NEW} hit, ${freshChunks} chunks, ${freshReads} sections read, ${freshRefs} sections referenced`
    + ` | rounds' new top-${TOP_NEW} sections: ${L.novel.join(", ")}`
    + (branches ? ` | branches: ${branches}` : "")
    + (flatNow ? `\nedge without deep-32: a search without limit came back untiered (at most 8 hits), so this question's later evidence searches send limit ${PLAN.limit}` : "")
    + (cut ? `\n${cut} new hits not shown (output budget): they stay unseen, so a later round can surface them` : "")
    + (PLAN.deep32 && MODE !== mode ? `\nmode: ${mode}, as round 1 set it for this question (MODE ${JSON.stringify(MODE)} ignored; a new question gets a new KEY)` : "")
    + (skipped.length ? `\nskipped: ${skipped.join("; ")}` : "")
    + (leadText ? `\nleads:${leadText}` : "")
    + `\n${next}`;
  return summary + "\n\n== results\n\n" + out.join("\n\n");
}
text(await round());
```

## 7. Quote-check script

Check each quote the answer will carry once, in one script, before writing; the script keeps at most 4
`verify_quote` calls in flight (labels and markers as `citation-style.md` says). Pick the quotes from the concept map
and the Evidence bullets first, so no call checks a quote you then drop. There are no spare slots to fill in code
mode: the round script refuses `verify_quote` jobs, and this script checks every quote, round 1's included, in one
step.

```js
// @exec: {"max_output_tokens": 6000}
// Quote check: verify_quote calls are exempt from the round budgets. Edit only QUOTES.
const QUOTES = [
  // { ref: "section-or-chunk-id", quote: "the exact text you will quote" },
];
const MAX_PARALLEL_CALLS = 4;  // the same cap as the round script
const asText = o => typeof o === "string" ? o
  : Array.isArray(o?.content) ? o.content.map(c => c?.text ?? "").join("\n") : JSON.stringify(o);
const rows = new Array(QUOTES.length);
let next = 0;
await Promise.all(Array.from({ length: Math.min(MAX_PARALLEL_CALLS, QUOTES.length) }, async () => {
  while (next < QUOTES.length) {
    const k = next++, q = QUOTES[k];
    try {
      const t = asText(await tools.mcp__deep32__verify_quote(q));
      const r = (t.match(/result="(\w+)"/) ?? [, "?"])[1], loc = (t.match(/local="([^"]*)"/) ?? [, "-"])[1];
      rows[k] = `${r} | ${loc} | ${q.ref} | ${q.quote.slice(0, 90)}`;
    } catch (e) { rows[k] = `ERROR | - | ${q.ref} | ${String(e?.message ?? e)}`; }
  }
}));
text(rows.join("\n") + `\n== ${QUOTES.length} verify_quote calls (result | local | ref | quote)`);
```

## 8. Copies (`also in`) and deep tiers

When the engine folds identical text (gateway 2.4.2 and later), a hit can be followed by the line
`also in (same text as hit N): doc_key [section] release R; ...` (the round script prints it under the hit as
`also in (same text; cite the pinned-release copy): ...`, marks the copies' sections seen, and reads the pinned
copy's section instead of the hit's). The listed copies carry the hit's text word for word:
- Cite the copy at the pinned release when one is listed (IOS XE 17.9.x, SD-WAN 20.9, Catalyst Center 2.3, ISE 3.1):
  run `verify_quote` against that copy's section id and print its `local`. Write the other releases as "same text
  in 26.x / 17.18", which is no release drift for that passage. Prefer the pinned copy's section for a whole-section
  read.
- A copy is the same text: never a second independent source, a new section for the stop rule (section 3), or a
  separate Evidence bullet.

When results carry tier headers (`tier 1 (hits 1-8): ...`, `tier 2 (hits 9-16): ...`, `tier 3 (hits 17-32): ...`: an
evidence search sent without `limit` to gateway 2.4.3 or later (deep-32, below), or a search sent with `deep=true`),
the round script lists them under the search as `deep tiers:` and marks every tier 2 and tier 3 hit as a candidate:
- Tier 1 is the evidence: the same hits as without deep (full windows at the search's `max_chars`, exactly what the
  same search gives with `limit` 8). Rank, read and cite from tier 1 first, as always.
- Tiers 2 and 3 are candidates, not evidence. Never cite a tier 2 or tier 3 snippet on its own: read its section first
  (`get_section(ref=<the hit's section>)`), then cite from that text and check quotes with `verify_quote` against that
  section. A tier 3 hit has no cite: take it from `get_section(ref=..., format="json")` (its `cite` field), or build it
  from the doc and path as for any section.
- Tier 2 (hits 9-16) gives windows of at most 800 characters, never more than `max_chars` (500 at section
  0's value), with `cite` and `id`. Tier 3 (hits 17-32) gives snippets of at most 250 characters with no `cite` and no
  chunk `id`: use its doc and `section` ref (the script prints `(no cite: take it from its section)`). Never quote from
  a tier 2 or tier 3 window, not even a few words.
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

**Deep-32: evidence searches give no `limit`.** The round script sends
every evidence search of a `default` or `deep` round (`search_many` or `search_rag`) **without `limit`** unless the job
sets one, with section 0's `max_chars`, so in your `JOBS` leave `limit` and `deep` out of evidence searches (round 1's
batches, round 2's targeted searches and "no hit" sub-queries, the theory pass's search, each deeper round's
searches). Gateway 2.4.3 and later answers a search that gives neither with deep-32: the 32 tiered hits above, about
17,000 to 19,000 characters at `max_chars` 500 (a `limit` 16 reply is about 12,000). An explicit `limit` gets exactly
that many plain hits and `deep: false` the usual 8, as before.
- **Narrow lookups keep an explicit small `limit`** in their job or plain call (3 is usual): re-finding a passage you
  already know, to get the section id for the quote script; checking that one command, identifier or exact phrase
  occurs; a search inside the outline check (`outline-check.md`). A search that collects evidence is never narrow.
- **What the script shows:** tier 1 lines keep the search share of the output, as hits did before. The tier 2 and 3
  candidates then take what tier 1 left of it, at most a fifth of the output per round (`CAND_SHARE`), offered in rank
  order across the round's searches, so the sections it reads keep their size; a candidate that does not fit is "not
  shown (output budget; still unseen)", and a later round can surface it. A section hit only in tier 3 never takes
  one of the script's automatic reads: it comes back in `leads:` ("sections hit only in tier 3"); read the 1 to 3 that
  matter as your own `get_section` jobs in the next round.
- **A gateway without deep-32** (before 2.4.3) answers a search without `limit` with
  at most 8 plain hits and no tier header line. From the first such reply on, the script sends `limit` 16 (20 at depth
  `deep`) on this question's later evidence searches and says so in its summary (`edge without deep-32: ...`); every
  hit of such a reply is evidence. When `rag_status`'s `edge:` line already names a version before 2.4.3,
  write `limit: 16` (20 at depth `deep`) into every round's evidence jobs yourself (a reply to a job that gave its own
  `limit` cannot show the script that the edge is old, so it would send round 2 without one).

**Modes: Full (32 hits), Half (16) and Light (8).** The search size of a `default` or `deep` round. The user picks one by
saying full, half or light mode (or `$deep32 half: ...`). It holds for the rest of that answer; full is the default.
Set the round script's `MODE` (`"full"`, `"half"`, `"light"`) in round 1. The script keeps round 1's mode for the
question's later rounds, and its summary line names the mode when it is not full.
- **Full** (default): evidence searches send no `limit`, as above (deep-32: 32 tiered hits on gateway 2.4.3).
- **Half:** evidence searches send `limit` 16: 16 plain hits, no tiers.
- **Light:** evidence searches send `limit` 8: 8 plain hits, no tiers.
- In every mode, narrow lookups keep their own small `limit`. The tier rules above apply only when a reply carries
  tier headers. In half and light mode every hit is evidence, and the old-gateway fallback never comes up, because the limit is always sent. Depth and
  mode are separate: "deep, light mode" runs section 3's rounds with `limit` 8. When the mode is not full, name it in
  the answer's last line ("half").
