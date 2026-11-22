"""Knowledge base: deterministic lookup tables built from the KB JSON files (BUILD-P1 section 4).

build_kb(src_dir, db_path) writes $RAG_ROOT/kb/kb.sqlite from rfc_index.json, errata.json,
iana.json, glossary.json, cli_index.json and feature_history.json; a missing file is skipped and
reported. CLI and feature rows carry (doc_key, line); build_kb resolves them to section ids with
the section line ranges of the root's rag.db (rag_core.structure), but only for documents whose
source file is byte-identical to the file the KB producer read (sha256 from <name>.meta.json).

kb.sqlite schema (all JSON columns hold arrays):

    meta          key PK, value: kb_schema_version, built_at, counts (JSON), sources (JSON:
                  file -> {sha256, generator, generated_at} from each .meta.json)
    rfc           rfc PK, title, status, date, obsoletes, obsoleted_by, updates, updated_by,
                  also (JSON: ["BCP 14"]), in_corpus
    rfc_alias     label ("bcp 14", normalized), rfc
    errata        id PK, rfc, status, type, section, summary, url, date
    iana          id PK, registry, code, name, description, refs (JSON), aliases (JSON), url
    iana_key      key (normalized name or alias), iana_id
    glossary      id PK, term, expansion, aliases (JSON), refs (JSON), category
    glossary_key  key (normalized term or alias), glossary_id
    cli           id PK, command, mode, platform, doc_key, line, kind, release, section_id,
                  first_word (first keyword, lowercase), keywords (keywords, space-joined)
    feature       id PK, doc_key, feature, release, info, line, section_id

Keys are normalized with norm(): casefold, and each run of spaces, "_" and "-" becomes one space.

lookup(db, identifier), rfc_info(db, n) and find_command(db, q, platform, release) read it.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from rag_core.sections import slugify


KB_SCHEMA_VERSION = "p1-v1"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE rfc (
    rfc INTEGER PRIMARY KEY, title TEXT, status TEXT, date TEXT,
    obsoletes TEXT NOT NULL, obsoleted_by TEXT NOT NULL,
    updates TEXT NOT NULL, updated_by TEXT NOT NULL,
    also TEXT NOT NULL, in_corpus INTEGER NOT NULL
);
CREATE TABLE rfc_alias (label TEXT NOT NULL, rfc INTEGER NOT NULL);
CREATE TABLE errata (
    id INTEGER PRIMARY KEY, rfc INTEGER NOT NULL, status TEXT, type TEXT,
    section TEXT, summary TEXT, url TEXT, date TEXT
);
CREATE TABLE iana (
    id INTEGER PRIMARY KEY, registry TEXT, code TEXT, name TEXT, description TEXT,
    refs TEXT NOT NULL, aliases TEXT NOT NULL, url TEXT
);
CREATE TABLE iana_key (key TEXT NOT NULL, iana_id INTEGER NOT NULL);
CREATE TABLE glossary (
    id INTEGER PRIMARY KEY, term TEXT, expansion TEXT, aliases TEXT NOT NULL,
    refs TEXT NOT NULL, category TEXT
);
CREATE TABLE glossary_key (key TEXT NOT NULL, glossary_id INTEGER NOT NULL);
CREATE TABLE cli (
    id INTEGER PRIMARY KEY, command TEXT NOT NULL, mode TEXT, platform TEXT,
    doc_key TEXT, line INTEGER, kind TEXT, release TEXT, section_id TEXT,
    first_word TEXT NOT NULL, keywords TEXT NOT NULL
);
CREATE TABLE feature (
    id INTEGER PRIMARY KEY, doc_key TEXT, feature TEXT, release TEXT, info TEXT,
    line INTEGER, section_id TEXT
);
CREATE INDEX idx_rfc_alias ON rfc_alias(label);
CREATE INDEX idx_errata_rfc ON errata(rfc);
CREATE INDEX idx_iana_key ON iana_key(key);
CREATE INDEX idx_glossary_key ON glossary_key(key);
CREATE INDEX idx_cli_first ON cli(first_word);
CREATE INDEX idx_cli_section ON cli(section_id);
CREATE INDEX idx_feature_doc ON feature(doc_key, line);
CREATE INDEX idx_feature_section ON feature(section_id);
"""

KB_FILES = (
    "rfc_index", "errata", "iana", "glossary", "cli_index", "feature_history",
)

KIND_ORDER = {"syntax": 0, "task": 1, "example": 2, "show-output": 3}
ERRATA_SHOWN = ("Verified", "Held for Document Update", "Reported")
MAX_COMMAND_RESULTS = 20


def norm(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text.casefold()).strip()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


# --- CLI command words --------------------------------------------------------------------------

CLI_TOKEN = re.compile(r"<[^<>]*>|[\[\]{}|]|[^\s\[\]{}|<>]+")
PROMPT = re.compile(r"^\s*[A-Za-z0-9_.:/-]+(?:\([^)]*\))?[#>]\s*")


def command_words(command: str) -> list[tuple[str, bool, bool]]:
    """(word, is_argument, optional) for each word of a syntax string such as
    "show ip ospf neighbor [<interface-type> <interface-number>] [detail]"."""
    words: list[tuple[str, bool, bool]] = []
    depth = 0
    for token in CLI_TOKEN.findall(command):
        if token in "[{":
            depth += 1
        elif token in "]}":
            depth = max(0, depth - 1)
        elif token != "|":
            words.append((token.lower(), token.startswith("<"), depth > 0))
    return words


def command_groups(command: str) -> list[tuple[int, ...]]:
    """For each word of command_words(command), the [..] and {..} groups it stands in, outermost
    first, each numbered by its opening bracket: "eap {username <name> | password <password>}"
    gives [(), (1,), (1,), (1,), (1,)]."""
    groups: list[tuple[int, ...]] = []
    stack: list[int] = []
    opened = 0
    for token in CLI_TOKEN.findall(command):
        if token in "[{":
            opened += 1
            stack.append(opened)
        elif token in "]}":
            if stack:
                stack.pop()
        elif token != "|":
            groups.append(tuple(stack))
    return groups


def _keywords(command: str) -> tuple[str, str]:
    """(first required keyword, all keywords space-joined) of a syntax string."""
    words = command_words(command)
    keywords = [w for w, is_arg, _ in words if not is_arg]
    first = next((w for w, is_arg, optional in words if not is_arg and not optional), None)
    return first or (keywords[0] if keywords else ""), " ".join(keywords)


def query_words(q: str) -> list[str]:
    """Words of a typed command: prompt ("R1(config-router)#"), "do" and "| include ..." dropped."""
    q = PROMPT.sub("", q, count=1)
    words = q.lower().split()
    if "|" in words:
        words = words[: words.index("|")]
    if len(words) > 1 and words[0] == "do":
        words = words[1:]
    return words


def _is_value(word: str) -> bool:
    return bool(re.search(r"[\d/:]", word))


def match_command(
    qwords: list[str],
    cwords: list[tuple[str, bool, bool]],
) -> int | None:
    """IOS abbreviation matching: each typed word is a prefix of the command's next keyword, in
    order. Optional words and arguments may be passed over, required keywords may not. A value
    (or a word that no later keyword starts with) fills an argument.

    Returns the number of required keywords left after the typed words (0: a complete
    command), or None when the typed words do not match."""
    return match_arguments(qwords, cwords)[0]


def match_arguments(
    qwords: list[str],
    cwords: list[tuple[str, bool, bool]],
    free: frozenset[int] = frozenset(),
    groups: list[tuple[int, ...]] | None = None,
    keywords: list[tuple[str, str]] | None = None,
) -> tuple[int | None, list[tuple[str, str]]]:
    """match_command's result and the (typed word, placeholder) pairs of the arguments the typed
    words filled, in order ([] when they do not match). `keywords`, when given, receives the
    (typed word, keyword) pairs of the keywords the typed words matched.

    A typed word at an index in `free` (find_command let it go of its keyword prefilter) fills
    an argument only when no optional keyword of that argument's own group was passed over to
    reach it (`groups`: command_groups(command)). "class-map CM-VOICE" fills <class-map-name>
    after passing over [match-all | match-any]; "eap type" does not fill <name> in "eap
    {username <name> | password <password>}", which is the value of the keyword username."""
    j = 0
    filled: list[tuple[str, str]] = []
    for i, w in enumerate(qwords):
        passed: set[int] = set()        # groups of the optional keywords passed over for w
        while True:
            if j == len(cwords):
                return None, []
            word, is_arg, optional = cwords[j]
            j += 1
            if not is_arg and word.startswith(w):
                if keywords is not None:
                    keywords.append((w, word))
                break
            if is_arg and i > 0 and (
                _is_value(w)
                or not any(not a and c.startswith(w) for c, a, _ in cwords[j:])
            ) and not (i in free and groups and passed.intersection(groups[j - 1])):
                filled.append((w, word))
                break
            if not is_arg and not optional:
                return None, []
            if not is_arg and groups and groups[j - 1]:
                passed.add(groups[j - 1][-1])
    return sum(1 for _, is_arg, optional in cwords[j:] if not is_arg and not optional), filled


def version_key(release: str | None) -> tuple[int, ...] | None:
    """(17, 9) for "17.9", (17, 11, 1) for "17.11.1a"; None when there is no number."""
    if not release:
        return None
    numbers = re.findall(r"\d+", release.split("(")[0])
    return tuple(int(n) for n in numbers[:3]) if numbers else None


# Cisco numbers releases by year from 2026: IOS XE and SD-WAN 26.x (26.1, 26.1.1, ...). Every release
# numbered before that (IOS XE 16/17, SD-WAN 20, Catalyst Center 2, ISE 3) is older than any of them.
YEAR_MAJOR = 26


def later_release(have: tuple[int, ...] | None, limit: tuple[int, ...] | None) -> bool:
    """True when version `have` (version_key) is later than `limit`: on the same major line (17.18
    after 17.9), or numbered by year against a limit numbered before (26.x after 17.9 and 20.9;
    27.1 after 26.2). Other lines (SD-WAN 20.15 against IOS XE 17.9) and unknown versions are not."""
    if not have or not limit:
        return False
    if have[0] == limit[0] or min(have[0], limit[0]) >= YEAR_MAJOR:
        return have > limit
    return have[0] >= YEAR_MAJOR > limit[0]


# IOS and IOS XE number their releases 12.x and 15.x (IOS), 16.x and 17.x (IOS XE), then by year
# (YEAR_MAJOR). These platforms have release lines of their own (ISE 3.1, SD-WAN 20.9, Catalyst
# Center 2.3): an IOS release says nothing about their entries.
IOS_MAJORS = frozenset({12, 15, 16, 17})
OUTSIDE_IOS_LINEAGE = frozenset({"ise", "sdwan", "catalyst-center"})


def ios_release(key: tuple[int, ...] | None) -> bool:
    """An IOS or IOS XE release (version_key): 17.9, 16.12, 15.2, 26.1. ISE 3.1, SD-WAN 20.9 and
    Catalyst Center 2.3 are not (nor is IOS XE 3.x, which reads like ISE 3.x)."""
    return bool(key) and (key[0] in IOS_MAJORS or key[0] >= YEAR_MAJOR)


def _like(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# find_command's passes after the first (typed words let go of the keyword prefilter) read at most
# about LET_GO_ROWS index rows in all, in at most MAX_LET_GO passes. Unbounded, a long query of
# common words ran a pass per word over every command it could start: 30 s for "s s s ..." (100
# words, 0.4 s before the passes existed). A typed command needs far less: the corpus-v5 KB's
# syntax and task rows typed back with literal values read at most 26,591 rows and let go of at
# most 21 words.
LET_GO_ROWS = 100_000
MAX_LET_GO = 24

# find_command's cache: (command, words let go) -> (left, filled, abbreviated, other_version), the
# match_arguments result and the rank terms of that command for the typed words
Matched = dict[tuple[str, frozenset[int]], tuple[int | None, list[tuple[str, str]], int, bool]]


def find_command(
    db: sqlite3.Connection,
    q: str,
    platform: str | None = None,
    release: str | None = None,
    limit: int = MAX_COMMAND_RESULTS,
    min_keywords: int = 1,
    whole_keywords: bool = False,
) -> list[dict[str, Any]]:
    """CLI index entries for a typed command, abbreviations allowed ("sh ip ospf nei").

    Ranked by closeness of the match, then syntax > task > example > show-output, then by the
    typed words that only abbreviate their keyword, fewest first: IOS reads a word typed in full as
    that keyword, so "ip ospf mtu-ignore" lists "ip ospf mtu-ignore [disable]" before "ipv6 ospf
    mtu-ignore" and "ip ospf authentication" lists "ip ospf authentication [...]" before "ip ospf
    authentication-key ..." (before, the shorter command came first). This term never moves a row
    past one of another kind or of another closeness, so the kind of the first row and the place of
    the first syntax row are as they were (an abbreviated query such as "sh ip dhcp snooping
    binding" still lists the syntax row before an example that has "sh" literally). Ahead of
    closeness (only the IOS lineage rule below comes first), an entry that reads a typed word as
    another version of it (other_version: "ip" as "ipv6") ranks after every entry that does not:
    "ip ospf priority" lists the IPv4 task "ip ospf priority <number>" before the IPv6 syntax
    "ipv6 ospf priority <number-value>", and "show ip pim neighbor" the IPv4 command before "show
    ipv6 pim ... neighbor". With
    `release`, entries dated no later than it (later_release: same major version, or numbered
    by year against a release numbered before) rank first. `platform` is a prefix ("cat9"
    matches cat9300, cat9500 and cat9k). Every entry carries its platform.

    When no entry matches, a typed word that is no value (no digit, "/" or ":") may still be an
    argument's literal value: "show application status ise" is "show application status
    <application_name>", "vrf definition RED" is "vrf definition <vrf-name>". Such words are let
    go of the keyword prefilter from the last one back, one more each time, until an entry
    matches (at most MAX_LET_GO passes, LET_GO_ROWS rows read); a word let go fills an argument
    only where match_arguments allows it (not the value of an optional keyword passed over). An
    entry where a typed word that is no value fills an argument carries "arguments", the [typed
    word, placeholder] pairs. Such an entry must match at least `min_keywords` typed words as
    keywords, and with `whole_keywords` each one in full: lookup asks for 2 and in full, so that
    a phrase whose first word begins a command ("weight attribute", "key string") or whose words
    abbreviate keywords ("ospf area design": ospfv3 <process-id> area <area-id>) is no command
    with a literal value.

    With an IOS or IOS XE release (ios_release), the entries of OUTSIDE_IOS_LINEAGE platforms
    rank after the others and carry "outside_ios_lineage": true; "show ntp" with release 17.9
    used to list ISE's complete "show ntp" before the IOS XE "show ntp associations". Another
    release (ISE 3.1, SD-WAN 20.9) ranks every platform as before."""
    qwords = query_words(q)
    if not qwords:
        return []
    wanted = version_key(release)
    lineage = ios_release(wanted)
    # the typed words the keyword prefilter may let go of, the last one first
    words = [i for i in range(len(qwords) - 1, 0, -1) if not _is_value(qwords[i])]
    matched: Matched = {}
    parsed: dict[str, list] = {}        # command -> [command_words, command_groups once needed]
    read = 0                            # index rows the passes after the first have read
    for n in range(min(len(words), MAX_LET_GO) + 1):
        if n and read >= LET_GO_ROWS:
            break
        free = frozenset(words[:n])
        results, rows = _find_commands(
            db, qwords, free, platform, wanted, lineage, limit, matched, parsed, min_keywords,
            whole_keywords,
        )
        if results:
            return results
        if n:
            read += rows
    return []


def other_version(word: str, keyword: str) -> bool:
    """True when the typed `word` only abbreviates another version of itself: "ip" of "ipv6" or
    "ipv4", "ospf" of "ospfv3", "ike" of "ikev2". IOS reads "ip" as ip: such a keyword is another
    protocol's command, not the one typed."""
    n = len(word)
    return (len(keyword) > n + 1 and keyword.startswith(word) and keyword[n] == "v"
            and keyword[n + 1:].isdigit())


def _find_commands(
    db: sqlite3.Connection,
    qwords: list[str],
    free: frozenset[int],
    platform: str | None,
    wanted: tuple[int, ...] | None,
    lineage: bool,
    limit: int,
    matched: Matched,
    parsed: dict[str, list],
    min_keywords: int = 1,
    whole_keywords: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """find_command with the typed words at the indexes `free` let go of the keyword prefilter,
    and the number of index rows read; `matched` keeps each command's match_arguments result
    and `parsed` its words across calls."""
    sql = ["SELECT * FROM cli WHERE first_word >= ? AND first_word < ?"]
    params: list[Any] = [qwords[0], qwords[0] + "￿"]
    held = [w for i, w in enumerate(qwords[1:], 1) if not _is_value(w) and i not in free]
    for w in dict.fromkeys(held):       # a word typed twice is one condition
        sql.append("AND (' ' || keywords || ' ') LIKE ? ESCAPE '\\'")
        params.append(f"% {_like(w)}%")
    if platform:
        sql.append("AND platform LIKE ? ESCAPE '\\'")
        params.append(_like(platform.lower()) + "%")

    ranked = []
    rows = 0
    for row in db.execute(" ".join(sql), params):
        rows += 1
        key = (row["command"], free)
        if key not in matched:          # a command recurs across books: match it once
            command = row["command"]
            words = parsed.get(command)
            if words is None:           # (setdefault would parse it again on every pass)
                words = parsed[command] = [command_words(command), None]
            if free and words[1] is None:
                words[1] = command_groups(command)
            typed: list[tuple[str, str]] = []
            left, filled = match_arguments(qwords, words[0], free, words[1] if free else None, typed)
            # a typed word matches a keyword or fills an argument: too few keywords, or one
            # abbreviated where they must be whole, no command
            if free and left is not None and (
                len(qwords) - len(filled) < min_keywords
                or (whole_keywords and any(w != keyword for w, keyword in typed))
            ):
                left, filled = None, []
            # the typed words that only abbreviate their keyword ("ip" of "ipv6"): IOS takes a
            # keyword typed in full over one it abbreviates; and whether one reads as another
            # version of the protocol typed (other_version)
            abbreviated = sum(1 for w, keyword in typed if w != keyword)
            other = any(other_version(w, keyword) for w, keyword in typed)
            matched[key] = (left, filled, abbreviated, other)
        left, filled, abbreviated, other = matched[key]
        if left is None:
            continue
        rel = version_key(row["release"]) if wanted else None
        later = later_release(rel, wanted)
        outside = lineage and row["platform"] in OUTSIDE_IOS_LINEAGE
        ranked.append((
            (outside, other, left, later, KIND_ORDER.get(row["kind"], 9), abbreviated,
             len(row["command"]), row["id"]),
            row,
            filled,
        ))
    ranked.sort(key=lambda item: item[0])

    results: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for (outside, *_), row, filled in ranked:
        key = (row["command"], row["mode"], row["doc_key"])
        if key in seen:
            continue
        seen.add(key)
        result = {
            "command": row["command"], "mode": row["mode"], "doc_key": row["doc_key"],
            "section_id": row["section_id"], "line": row["line"], "kind": row["kind"],
            "release": row["release"], "platform": row["platform"],
        }
        arguments = [[w, placeholder] for w, placeholder in filled if not _is_value(w)]
        if arguments:
            result["arguments"] = arguments
        if outside:
            result["outside_ios_lineage"] = True
        results.append(result)
        if len(results) == limit:
            break
    return results, rows


# --- RFC metadata -------------------------------------------------------------------------------

def rfc_info(db: sqlite3.Connection, n: int) -> dict[str, Any] | None:
    """RFC metadata with its errata; rejected errata are only counted."""
    row = db.execute("SELECT * FROM rfc WHERE rfc = ?", (n,)).fetchone()
    if row is None:
        return None
    errata = [
        {
            "id": e["id"], "status": e["status"], "type": e["type"],
            "section": e["section"], "summary": e["summary"], "url": e["url"],
        }
        for e in db.execute("SELECT * FROM errata WHERE rfc = ? ORDER BY id", (n,))
    ]
    info = {
        "rfc": row["rfc"], "title": row["title"], "status": row["status"],
        "date": row["date"], "in_corpus": bool(row["in_corpus"]),
        **{
            key: json.loads(row[key])
            for key in ("obsoletes", "obsoleted_by", "updates", "updated_by", "also")
        },
        "errata": [e for e in errata if e["status"] != "Rejected"],
        "errata_rejected": sum(1 for e in errata if e["status"] == "Rejected"),
    }
    info["current"] = current_rfcs(db, n, info["obsoleted_by"])
    info["standing"] = standing(info)
    return info


# The RFC Editor's longest "obsoleted by" chain is 32 RFCs (RFC 1083, the Official Protocol Standards
# series); an index with a cycle or a runaway chain is read no further than this many RFCs.
MAX_SUCCESSION = 256


def current_rfcs(db: sqlite3.Connection, n: int, obsoleted_by: list[int]) -> list[dict[str, Any]]:
    """Where RFC n's "obsoleted by" chain ends: each RFC nothing obsoletes that the chain reaches,
    with the RFCs passed on the way. RFC 1583 (obsoleted by 2178, which 2328 obsoletes) ->
    [{"rfc": 2328, "via": [2178]}]; RFC 2178 -> [{"rfc": 2328, "via": []}]; [] when nothing
    obsoletes n. Breadth first, so each "via" is a shortest chain; each RFC is read once, so a
    cycle ends the walk (its RFCs are no end), and at most MAX_SUCCESSION are read. An RFC the
    index does not hold is an end."""
    found: list[dict[str, Any]] = []
    seen = {n, *obsoleted_by}
    queue = [(m, []) for m in obsoleted_by]
    for m, via in queue:                # the list grows as it is read: breadth first
        row = db.execute("SELECT obsoleted_by FROM rfc WHERE rfc = ?", (m,)).fetchone()
        later = json.loads(row["obsoleted_by"]) if row is not None else []
        if not later:
            found.append({"rfc": m, "via": via})
        for k in later:
            if k not in seen and len(seen) < MAX_SUCCESSION:
                seen.add(k)
                queue.append((k, [*via, m]))
    return found


def standing(info: dict[str, Any]) -> str:
    """"Proposed Standard; obsoletes RFC 1587; errata: 2 verified" (the hit "status" line). An RFC
    whose successor is obsoleted in turn also names where the chain ends (info["current"]): "Draft
    Standard; OBSOLETED by RFC 2178; current: RFC 2328 (via 2178)"; with one step only, the line
    is as before."""
    def rfcs(numbers: list[int]) -> str:
        shown = ", ".join(str(n) for n in numbers[:4])
        more = f" +{len(numbers) - 4} more" if len(numbers) > 4 else ""
        return f"RFC {shown}{more}"

    def current(end: dict[str, Any]) -> str:
        via = end["via"]
        if not via:
            return f"RFC {end['rfc']}"
        more = f" +{len(via) - 3} more" if len(via) > 3 else ""
        return f"RFC {end['rfc']} (via {', '.join(str(v) for v in via[:3])}{more})"

    parts = [info.get("status") or "Unknown"]
    if info.get("obsoleted_by"):
        parts.append(f"OBSOLETED by {rfcs(info['obsoleted_by'])}")
        # where the chain ends, when that is further than the RFCs that obsolete this one
        ends = info.get("current") or []
        if any(end["via"] for end in ends):
            shown = ", ".join(current(end) for end in ends[:4])
            parts.append(f"current: {shown}" + (f" +{len(ends) - 4} more" if len(ends) > 4 else ""))
    if info.get("obsoletes"):
        parts.append(f"obsoletes {rfcs(info['obsoletes'])}")
    if info.get("updated_by"):
        parts.append(f"updated by {rfcs(info['updated_by'])}")
    counts = [
        (sum(1 for e in info.get("errata", []) if e["status"] == status), label)
        for status, label in (("Verified", "verified"), ("Held for Document Update", "held"))
    ]
    shown = [f"{n} {label}" for n, label in counts if n]
    if shown:
        parts.append("errata: " + ", ".join(shown))
    return "; ".join(parts)


def section_anchor(text: str) -> str:
    """A section reference as typed -> its anchor, as rag_core.sections names it:
    "9.1.2", "§9.1.2", "Section 1.0" -> "9.1.2", "9.1.2", "1"; "A.1", "Appendix B" ->
    "appendix-a.1", "appendix-b"; an unnumbered heading ("Security Considerations") -> its
    slug. An anchor as emitted ("appendix-a.1", "security-considerations", "3.2-2") is kept."""
    text = re.sub(r"(?i)^(?:§|section\b|sec\.)[\s-]*", "", text.strip()).strip().rstrip(".")
    m = re.fullmatch(r"(?i)appendix[\s-]+([a-z]|[ivx]{1,4}|\d{1,2})((?:\.\d+)*)", text)
    if m:
        return f"appendix-{m.group(1).lower()}{m.group(2)}"
    m = re.fullmatch(r"([A-Za-z])((?:\.\d+)+)", text)
    if m:
        return f"appendix-{m.group(1).lower()}{m.group(2)}"
    if re.fullmatch(r"\d+(?:\.\d+)*", text):
        parts = text.split(".")
        if len(parts) == 2 and parts[1] == "0":
            parts = parts[:1]
        return ".".join(parts)
    if re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", text):
        return text
    return slugify(text)


def errata_anchor(section: str | None) -> str | None:
    """The anchor an erratum's free-text section ("3.2", "Section 3.2", "Appendix A") names."""
    if not section:
        return None
    text = re.sub(r"(?i)^\s*(section|sec\.?|§)\s*", "", section.strip())
    if not re.fullmatch(r"(?i)(appendix\s+)?[a-z0-9]+(\.\d+)*\.?", text):
        return None
    return section_anchor(text)


# --- identifier lookup --------------------------------------------------------------------------

RFC_SECTION = re.compile(
    r"^\s*rfc\s*-?\s*(\d{1,5})\s*"
    r"(?:#\s*(?:section-)?|,?\s*(?:§|sections?\b|sec\.?|s\.)\s*|\s+)"
    r"((?:appendix\s+)?(?:[a-z]|\d+)(?:\.\d+)*)\.?\s*$",
    re.I,
)
RFC_ONLY = re.compile(r"^\s*rfc\s*-?\s*(\d{1,5})\s*$", re.I)
SERIES = re.compile(r"^\s*(bcp|std|fyi)\s*-?\s*(\d{1,5})\s*$", re.I)


def _iana(db: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT DISTINCT iana.* FROM iana_key JOIN iana ON iana.id = iana_key.iana_id
        WHERE iana_key.key = ? ORDER BY iana.id
        """,
        (key,),
    ).fetchall()
    return [
        {
            "kind": "iana", "registry": r["registry"], "code": r["code"], "name": r["name"],
            "description": r["description"], "refs": json.loads(r["refs"]), "url": r["url"],
        }
        for r in rows
    ]


def _glossary(db: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT DISTINCT glossary.* FROM glossary_key
        JOIN glossary ON glossary.id = glossary_key.glossary_id
        WHERE glossary_key.key = ? ORDER BY glossary.id
        """,
        (key,),
    ).fetchall()
    return [
        {
            "kind": "glossary", "term": r["term"], "expansion": r["expansion"],
            "aliases": json.loads(r["aliases"]), "refs": json.loads(r["refs"]),
            "category": r["category"],
        }
        for r in rows
    ]


def _iana_segments(db: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    """"afi 1 safi 128" -> the entries for "afi 1" and "safi 128": the longest known alias at
    each position, and only when the whole identifier is covered."""
    words = key.split()
    results: list[dict[str, Any]] = []
    i = 0
    while i < len(words):
        for j in range(len(words), i, -1):
            found = _iana(db, " ".join(words[i:j]))
            if found:
                results.extend(found)
                i = j
                break
        else:
            return []
    return results


def lookup(db: sqlite3.Connection, identifier: str) -> dict[str, Any]:
    """Resolve an identifier: "RFC4271 9.1.2", "rfc 4271 §9.1.2", "RFC 3101", "BCP 14",
    "type-7 LSA", "LSA type 7", "BGP attribute 9", "ORIGINATOR_ID", "AFI 1 SAFI 128",
    "DSCP EF", "NSSA", "show ip ospf database nssa-external".

    Returns {kind, results}; kind is that of the first result ("rfc", "rfc_section", "bcp",
    "std", "iana", "glossary", "cli") or "unknown". Every result carries its own "kind"; a CLI
    result keeps the index entry's kind (syntax, task, example, show-output) as "cli_kind".
    The CLI fallback reads a typed word as an argument's literal value only after two keywords,
    each typed in full (find_command min_keywords, whole_keywords): "show application status ise"
    resolves, "weight attribute" does not become "weight <value>", nor "ospf area design"
    "ospfv3 <process-id> area <area-id>"."""
    identifier = identifier.strip()

    m = RFC_ONLY.match(identifier)
    if m:
        info = rfc_info(db, int(m.group(1)))
        return {"kind": "rfc", "results": [{"kind": "rfc", **info}] if info else []}

    m = RFC_SECTION.match(identifier)
    if m:
        n, anchor = int(m.group(1)), section_anchor(m.group(2))
        info = rfc_info(db, n)
        if info is None:
            return {"kind": "rfc_section", "results": []}
        errata = [e for e in info["errata"] if errata_anchor(e["section"]) == anchor]
        return {
            "kind": "rfc_section",
            "results": [{
                "kind": "rfc_section", "rfc": n, "title": info["title"],
                "standing": info["standing"], "section": anchor,
                "section_id": f"rfc{n}#{anchor}", "errata": errata,
            }],
        }

    m = SERIES.match(identifier)
    if m:
        series = m.group(1).lower()
        label = f"{series} {int(m.group(2))}"
        numbers = [r["rfc"] for r in db.execute(
            "SELECT rfc FROM rfc_alias WHERE label = ? ORDER BY rfc", (label,)
        )]
        kind = "rfc" if series == "fyi" else series
        return {
            "kind": kind,
            "results": [
                {"kind": kind, "label": label.upper(), **rfc_info(db, n)} for n in numbers
            ],
        }

    key = norm(identifier)
    results = _iana(db, key) + _glossary(db, key)
    if not results and key.endswith("s"):
        results = _iana(db, key[:-1]) + _glossary(db, key[:-1])
    if not results:
        results = _iana_segments(db, key)
    if not results:
        results = [
            {**r, "kind": "cli", "cli_kind": r["kind"]}
            for r in find_command(db, identifier, limit=10, min_keywords=2, whole_keywords=True)
        ]
    return {"kind": results[0]["kind"] if results else "unknown", "results": results}


# --- fact lookups (/v2/lookup?kind=) ------------------------------------------------------------------

FACT_TABLES = {"feature": "feature", "history": "command_history", "default": "cmd_default",
               "deprecation": "deprecation"}
FACT_SUBJECT = {"feature": "feature", "history": "command", "default": "command", "deprecation": "subject"}
FACT_FIELDS = {     # (row field, table column), after kind, doc_key, section_id, line, doc_release
    "feature": (("feature", "feature"), ("release", "release"), ("info", "info")),
    "history": (("command", "command"), ("change", "kind"), ("release", "release"), ("train", "train"),
                ("modification", "modification")),
    "default": (("command", "command"), ("parameter", "parameter"), ("default_value", "default_value"),
                ("sentence", "sentence")),
    "deprecation": (("subject", "subject"), ("replacement", "replacement"), ("release", "release"),
                    ("sentence", "sentence")),
}
MAX_FACT_ROWS = 50
# Kinds whose subject is a CLI command: a leading "no " (the command's negated form) is not part of the match
# (LEAD-RULINGS E-op10), so "no timers bgp" finds the rows of "timers bgp".
FACT_COMMAND_KINDS = frozenset({"history", "default"})


def has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _fact_key(kind: str, text: str) -> str:
    """The form a fact's subject and a lookup's identifier are compared in: norm(), and for a command kind without
    a leading "no "."""
    key = norm(text)
    if kind in FACT_COMMAND_KINDS and key.startswith("no ") and len(key) > 3:
        key = key[3:]
    return key


def _fact_row(kind: str, row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "doc_key": row["doc_key"], "section_id": row["section_id"],
                           "line": row["line"], "doc_release": None}
    for field, column in FACT_FIELDS[kind]:
        out[field] = row[column]
    return out


def lookup_fact(db: sqlite3.Connection, kind: str, identifier: str, limit: int = MAX_FACT_ROWS) -> dict[str, Any]:
    """/v2/lookup?kind= (WP-C C6, WP-E E4): the rows of one fact table for an identifier that is a section id
    (contains "#"), a doc_key with rows in the table, or else text matched as a substring of the kind's subject
    after norm() on both sides. Text matches come exact first, then those the identifier starts, then the rest,
    each group in document order, so the cap of `limit` rows never cuts an exact match (LEAD-RULINGS E-op10).
    A knowledge base without the kind's table answers degraded ["kb"]; doc_release is filled by the route."""
    table, subject = FACT_TABLES[kind], FACT_SUBJECT[kind]
    if not has_table(db, table):
        return {"kind": kind, "results": [], "degraded": ["kb"]}
    ident = identifier.strip()
    order = "ORDER BY doc_key, line, id"
    if "#" in ident:
        rows = db.execute(f"SELECT * FROM {table} WHERE section_id = ? {order} LIMIT ?",                          (ident, limit + 1)).fetchall()
    elif db.execute(f"SELECT 1 FROM {table} WHERE doc_key = ? LIMIT 1", (ident,)).fetchone():        rows = db.execute(f"SELECT * FROM {table} WHERE doc_key = ? {order} LIMIT ?",                          (ident, limit + 1)).fetchall()
    else:
        q = _fact_key(kind, ident)
        words = sorted({w for w in re.split(r"[\s_-]+", q) if w})
        if not words:
            return {"kind": kind, "results": [], "truncated": False}
        where = " AND ".join(f"{subject} LIKE ? ESCAPE '\\'" for _ in words)
        exact: list[sqlite3.Row] = []
        starts: list[sqlite3.Row] = []
        within: list[sqlite3.Row] = []
        for row in db.execute(f"SELECT * FROM {table} WHERE {where} {order}",                              [f"%{_like(w)}%" for w in words]):
            if row[subject] is None:
                continue
            key = _fact_key(kind, row[subject])
            if q not in key:
                continue
            (exact if key == q else starts if key.startswith(q) else within).append(row)
        rows = exact + starts + within
    return {"kind": kind, "results": [_fact_row(kind, r) for r in rows[:limit]], "truncated": len(rows) > limit}


# --- build --------------------------------------------------------------------------------------

def _read_only_uri(path: Path) -> str:
    """SQLite URI of a file opened read-only; as_uri() escapes "?", "#" and "%" in the path."""
    return path.resolve().as_uri() + "?mode=ro"


def _section_index(rag_db: Path | None) -> dict[str, tuple[str, list[int], list[str]]]:
    """doc_key -> (source sha256, section line starts, section ids) from rag.db."""
    if rag_db is None or not rag_db.is_file():
        return {}
    conn = sqlite3.connect(_read_only_uri(rag_db), uri=True)
    try:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_sections'"
        ).fetchone()
        if not has:
            return {}
        index: dict[str, tuple[str, list[int], list[str]]] = {}
        rows = conn.execute(
            """
            SELECT s.doc_key, s.sha256, d.line_start, d.section_id
            FROM doc_sections d JOIN doc_sources s ON s.doc_id = d.doc_id
            ORDER BY s.doc_key, d.ordinal
            """
        )
        for doc_key, sha, line_start, section_id in rows:
            entry = index.setdefault(doc_key, (sha, [], []))
            entry[1].append(line_start)
            entry[2].append(section_id)
        return index
    finally:
        conn.close()


def _resolver(index, meta: dict[str, Any], counts: dict[str, Any], name: str):
    """(doc_key, line) -> section_id, for documents whose source matches the KB's input."""
    produced = {
        s.get("doc_key"): s.get("sha256")
        for s in meta.get("sources", []) if isinstance(s, dict)
    }
    mismatched: set[str] = set()

    def resolve(doc_key: str | None, line: Any) -> str | None:
        entry = index.get(doc_key)
        if entry is None or not isinstance(line, int):
            return None
        sha, starts, ids = entry
        if produced.get(doc_key) not in (None, sha):
            mismatched.add(doc_key)
            return None
        i = bisect.bisect_right(starts, line) - 1
        return ids[i] if i >= 0 else None

    counts[f"{name}_source_mismatch"] = mismatched
    return resolve


def build_kb(
    src_dir: Path,
    db_path: Path,
    *,
    rag_db: Path | None = None,
) -> dict[str, Any]:
    """Build kb.sqlite from the KB JSON files in src_dir; returns row counts.

    rag_db (default: the rag.db of the root db_path lives in, <root>/sqlite/rag.db) supplies
    the section line ranges. The file is written next to db_path and renamed into place, so a
    reader never sees a half-built KB."""
    src_dir, db_path = Path(src_dir), Path(db_path)
    if rag_db is None:
        rag_db = db_path.resolve().parent.parent / "sqlite" / "rag.db"
    index = _section_index(rag_db)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    counts: dict[str, Any] = {"missing": [], "sections_available": bool(index)}
    sources: dict[str, Any] = {}

    def load(name: str) -> tuple[list[dict], dict]:
        path = src_dir / f"{name}.json"
        if not path.is_file():
            counts["missing"].append(f"{name}.json")
            return [], {}
        meta_path = src_dir / f"{name}.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        sources[name] = {k: meta.get(k) for k in ("sha256", "generator", "generated_at")}
        records = json.loads(path.read_text(encoding="utf-8"))
        return [r for r in records if isinstance(r, dict)], meta

    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)

        rfcs, _ = load("rfc_index")
        for r in rfcs:
            conn.execute(
                "INSERT OR REPLACE INTO rfc VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(r["rfc"]), r.get("title"), r.get("status"), r.get("date"),
                    _json(r.get("obsoletes")), _json(r.get("obsoleted_by")),
                    _json(r.get("updates")), _json(r.get("updated_by")),
                    _json(r.get("also")), int(bool(r.get("in_corpus"))),
                ),
            )
            for label in r.get("also") or []:
                conn.execute("INSERT INTO rfc_alias VALUES (?, ?)", (norm(label), int(r["rfc"])))
        counts["rfc"] = len(rfcs)

        errata, _ = load("errata")
        for e in errata:
            conn.execute(
                "INSERT OR REPLACE INTO errata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(e["id"]), int(e["rfc"]), e.get("status"), e.get("type"),
                    e.get("section"), e.get("summary"), e.get("url"), e.get("date"),
                ),
            )
        counts["errata"] = len(errata)

        entries, _ = load("iana")
        for i, e in enumerate(entries, 1):
            conn.execute(
                "INSERT INTO iana VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i, e.get("registry"), str(e.get("code")), e.get("name"),
                    e.get("description"), _json(e.get("refs")), _json(e.get("aliases")),
                    e.get("url"),
                ),
            )
            keys = {norm(k) for k in [e.get("name") or "", *(e.get("aliases") or [])]}
            conn.executemany(
                "INSERT INTO iana_key VALUES (?, ?)", [(k, i) for k in sorted(keys) if k]
            )
        counts["iana"] = len(entries)

        terms, _ = load("glossary")
        for i, g in enumerate(terms, 1):
            conn.execute(
                "INSERT INTO glossary VALUES (?, ?, ?, ?, ?, ?)",
                (
                    i, g.get("term"), g.get("expansion"), _json(g.get("aliases")),
                    _json(g.get("refs")), g.get("category"),
                ),
            )
            keys = {norm(k) for k in [g.get("term") or "", *(g.get("aliases") or [])]}
            conn.executemany(
                "INSERT INTO glossary_key VALUES (?, ?)", [(k, i) for k in sorted(keys) if k]
            )
        counts["glossary"] = len(terms)

        commands, meta = load("cli_index")
        resolve = _resolver(index, meta, counts, "cli")
        resolved = 0
        for i, c in enumerate(commands, 1):
            command = str(c.get("command") or "").strip()
            if not command:
                continue
            first, keywords = _keywords(command)
            section_id = resolve(c.get("doc_key"), c.get("line"))
            resolved += section_id is not None
            conn.execute(
                "INSERT INTO cli VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i, command, c.get("mode"), c.get("platform"), c.get("doc_key"),
                    c.get("line"), c.get("kind"), c.get("release"), section_id,
                    first, keywords,
                ),
            )
        counts["cli"], counts["cli_resolved"] = len(commands), resolved

        features, meta = load("feature_history")
        resolve = _resolver(index, meta, counts, "feature")
        resolved = 0
        for i, f in enumerate(features, 1):
            section_id = resolve(f.get("doc_key"), f.get("line"))
            resolved += section_id is not None
            conn.execute(
                "INSERT INTO feature VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    i, f.get("doc_key"), f.get("feature"), f.get("release"),
                    f.get("info"), f.get("line"), section_id,
                ),
            )
        counts["feature_history"], counts["feature_resolved"] = len(features), resolved

        for name in ("cli", "feature"):
            counts[f"{name}_source_mismatch"] = sorted(counts[f"{name}_source_mismatch"])
        built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("kb_schema_version", KB_SCHEMA_VERSION),
                ("built_at", built_at),
                ("counts", json.dumps(counts)),
                ("sources", json.dumps(sources)),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, db_path)
    return counts


def open_kb(path: Path) -> sqlite3.Connection | None:
    """Read-only connection to kb.sqlite, or None when the root has no KB."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(_read_only_uri(path), uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def table_counts(db: sqlite3.Connection) -> dict[str, int]:
    return {
        name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("rfc", "errata", "iana", "glossary", "cli", "feature")
    }
