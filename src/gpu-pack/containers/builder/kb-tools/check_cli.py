#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

from build_cli import TOKEN, literal, prompt_match
from vendor_common import DEFAULT_SRC, KB_DIR, discover

SPOT_CHECKS = [
    # (query, corpus regex or None to derive one from the query)
    ("area nssa translate type7 always", None),
    ("bgp additional-paths install", None),
    ("ip nhrp redirect", None),
    ("ip nhrp shortcut", None),
    ("eigrp stub leak-map", None),
    ("show ip ospf database nssa-external", None),
    ("ip verify unicast source reachable-via rx", None),
    ("ip sla", r"\bip sla \d"),
    ("event manager applet", None),
    ("sh ip ospf nei", r"\bshow ip ospf neighbor"),
    ("router ospf", None),
    ("area stub no-summary", None),
    ("redistribute ospf", None),
    ("distribute-list prefix", None),
    ("match ip address prefix-list", None),
    ("neighbor route-reflector-client", None),
    ("neighbor remote-as", None),
    ("bgp bestpath as-path multipath-relax", None),
    ("ip prefix-list", None),
    ("show ip bgp summary", None),
    ("show ip eigrp topology", None),
    ("ip dhcp snooping", None),
    ("ip nat inside source list", None),
    ("track ip sla reachability", None),
    ("vrf definition", None),
    ("tunnel mode gre multipoint", None),
    ("ntp server", None),
    ("ip ospf authentication message-digest", None),
    ("ip access-list extended", None),
    ("show sdwan omp routes", None),
    ("key chain", None),
    ("snmp-server community", None),
    ("bfd interval", None),
    ("ipv6 unicast-routing", None),
    ("show ip route vrf", None),
]


@lru_cache(maxsize=None)
def items(command: str) -> tuple[tuple[str, bool, bool], ...]:
    """(token, is_placeholder, skippable) for each token of a normalized command; group syntax is dropped and
    every token inside [] or {} counts as skippable."""
    out, depth = [], 0
    for t in TOKEN.findall(command):
        if t in "[{":
            depth += 1
        elif t in "]}":
            depth -= 1
        elif t != "|":
            out.append((t.lower(), t.startswith("<"), depth > 0 or t.startswith("<")))
    return tuple(out)


VALUE_LIKE = re.compile(r"[\d.:/A-Z\"]")


def match_score(query: tuple[str, ...], values: tuple[bool, ...], exact: tuple[bool, ...], cmd_items: tuple):
    """Score (keywords matched, exact keywords, -keyword-like tokens taken by placeholders, -required keywords
    left over) when every query token matches in order, else None. IOS rules: a query token matches a keyword
    it abbreviates, unless it is itself a common keyword (exact[i]: "ip" never matches "ipv6"); a placeholder
    takes a value, or a word that no later keyword of the command starts with; tokens in [] / {} and
    placeholders may be skipped."""
    @lru_cache(maxsize=None)
    def f(i: int, j: int):
        if i == len(query):
            return 0, 0, 0, -sum(1 for _, ph, skip in cmd_items[j:] if not skip)
        if j == len(cmd_items):
            return None
        tok, ph, skip = cmd_items[j]
        q, cands = query[i], []
        if ph:
            later = any(not p and t.startswith(q) for t, p, _ in cmd_items[j + 1:])
            if values[i] or not later:
                r = f(i + 1, j + 1)
                if r:
                    cands.append((r[0], r[1], r[2] - (not values[i]), r[3]))
        elif tok == q or (not exact[i] and tok.startswith(q)):
            r = f(i + 1, j + 1)
            if r:
                cands.append((r[0] + 1, r[1] + (tok == q), r[2], r[3]))
        if skip:
            r = f(i, j + 1)
            if r:
                cands.append(r)
        return max(cands) if cands else None
    return f(0, 0)


def keyword_vocab(index: list[dict]) -> Counter:
    """Keyword -> number of distinct commands that contain it."""
    return Counter(t for cmd in {rec["command"] for rec in index} for t in {t for t, ph, _ in items(cmd) if not ph})


def exact_only(word: str, vocab: Counter) -> bool:
    """A word that is a common keyword itself is not an abbreviation ("ip" vs "ipv6"; but "sh" -> "show")."""
    longer = max((n for t, n in vocab.items() if t != word and t.startswith(word)), default=0)
    return vocab[word] > 0 and vocab[word] >= 0.2 * longer


def find(index: list[dict], query: str, vocab: Counter, platform: str | None = None,
         strict: bool = True) -> list[tuple]:
    """Records matching `query`, best first. strict: no query word may be taken by a placeholder unless it
    looks like a value (digits, capitals, dots, colons, slashes)."""
    q = tuple(query.lower().split())
    values = tuple(bool(VALUE_LIKE.search(t)) for t in query.split())
    exact = tuple(exact_only(t, vocab) for t in q)
    hits = []
    for rec in index:
        if platform and rec["platform"] != platform:
            continue
        s = match_score(q, values, exact, items(rec["command"]))
        if s is not None and (s[2] == 0 or not strict):
            hits.append((s, rec))
    kind_rank = {"syntax": 0, "task": 1, "show-output": 2, "example": 3}
    hits.sort(key=lambda h: (tuple(-x for x in h[0]), kind_rank[h[1]["kind"]], h[1]["doc_key"], h[1]["line"]))
    return hits


def corpus_regex(query: str) -> str:
    """The query's words in order on one line, up to two other words between them, [ { may precede a word."""
    return r"\b" + r"(?:\s+\S+){0,2}?\s+[\[{]?".join(re.escape(t) for t in query.split()) + r"(?![\w-])"


@lru_cache(maxsize=None)
def word_start(keyword: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}")


def in_order(keywords: list[str], text: str) -> bool:
    """Each keyword occurs after the previous one, starting a word (converter glue such as 'login-Configure'
    still counts as 'login')."""
    pos = 0
    for kw in keywords:
        m = word_start(kw).search(text, pos)
        if not m:
            return False
        pos = m.end()
    return True


def line_check(index: list[dict], lines: dict[str, list[str]],
               rules: frozenset = frozenset()) -> list[tuple[dict, str, str]]:
    """(record, problem, source line) for each record that fails:
    - "not a prompt": an example's or show-output's line holds no device prompt (build_cli.prompt_match with the
      index's `rules`; under unprompted-config an example's line may be a configuration line without one);
    - "keywords not in order": the record's literal keywords are not found in order in the prompt's command
      (examples) or on the line (syntax, task), plus the next three lines (wrapped commands);
    - "unbalanced brackets", "glued prompt" (')#' in the command), "dotted placeholder" ('<...x>')."""
    bad = []
    for rec in index:
        src = lines[rec["doc_key"]]
        text = src[rec["line"] - 1] if 0 < rec["line"] <= len(src) else ""
        after = " ".join(t for t in src[rec["line"]: rec["line"] + 3] if not t.startswith("```"))
        cmd = rec["command"]
        hay = f"{text} {after}"
        problem = None
        if rec["kind"] in ("example", "show-output"):
            m = prompt_match(text, False, rules)
            if m:
                hay = f"{m.group('cmd')} {after}"
            elif not ("unprompted-config" in rules and rec["kind"] == "example"):
                problem = "not a prompt"
        keywords = [t.lower() for t in TOKEN.findall(cmd) if literal(t) and re.search(r"[a-z0-9]", t, re.I)]
        if problem is None and not in_order(keywords, hay.lower()):
            problem = "keywords not in order"
        elif cmd.count("[") != cmd.count("]") or cmd.count("{") != cmd.count("}"):
            problem = "unbalanced brackets"
        elif ")#" in cmd:
            problem = "glued prompt"
        elif re.search(r"<\.\.\.|\.\.\.>", cmd):
            problem = "dotted placeholder"
        if problem:
            bad.append((rec, problem, text))
    return bad


SCHEMAS = {"cli_index": {"command", "mode", "platform", "doc_key", "line", "kind", "release"},
           "feature_history": {"doc_key", "feature", "release", "info", "line"}}
META_KEYS = {"source_urls", "fetched_at", "generator", "counts", "sha256"}
KINDS = {"syntax", "task", "example", "show-output"}


def schema_check(path: Path, records: list[dict], lines: dict[str, list[str]]) -> list[str]:
    """BUILD-P1 section 4 shape of <name>.json and its .meta.json (keys, kinds, line range, sha256)."""
    keys = SCHEMAS[path.stem]
    problems = [f"{path.name}: record {i} {r.get('doc_key')}:{r.get('line')} bad keys or line" for i, r in
                enumerate(records) if set(r) != keys or not isinstance(r["line"], int) or r["doc_key"] not in lines
                or not 0 < r["line"] <= len(lines[r["doc_key"]]) or r.get("kind", "syntax") not in KINDS]
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    if not META_KEYS <= set(meta):
        problems.append(f"{path.name}: meta lacks {sorted(META_KEYS - set(meta))}")
    if meta.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        problems.append(f"{path.name}: meta sha256 does not match the file")
    return problems


def feature_line_check(features: list[dict], lines: dict[str, list[str]]) -> list[dict]:
    """Feature rows whose line holds neither their release nor the start of their feature name."""
    return [f for f in features if f["release"] not in lines[f["doc_key"]][f["line"] - 1]
            and f["feature"][:30] not in lines[f["doc_key"]][f["line"] - 1]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate cli_index.json and feature_history.json against the corpus.")
    ap.add_argument("--index", type=Path, default=KB_DIR / "cli_index.json")
    ap.add_argument("--features", type=Path, default=KB_DIR / "feature_history.json")
    ap.add_argument("--src", action="append", type=Path)
    ap.add_argument("--max-bad-lines", type=int, default=0)
    ap.add_argument("--show", type=int, default=3, help="hits to print per spot check")
    args = ap.parse_args(argv)
    index = json.loads(args.index.read_text(encoding="utf-8"))
    features = json.loads(args.features.read_text(encoding="utf-8"))
    paths = discover(args.src or DEFAULT_SRC)
    lines = {k: p.read_text(encoding="utf-8").split("\n") for k, p in paths.items()}

    problems = schema_check(args.index, index, lines) + schema_check(args.features, features, lines)
    print(f"schema/meta check: {len(problems)} problems")
    for p in problems[:10]:
        print(f"  {p}")
    if problems:
        return 1
    loose = feature_line_check(features, lines)
    print(f"feature_history lines: {len(features) - len(loose)}/{len(features)} hold their release or name")
    rules = frozenset(json.loads(args.index.with_suffix(".meta.json").read_text(encoding="utf-8"))["counts"]
                      .get("rules", {}).get("rules", []))          # the parser rules the index was built with
    bad = line_check(index, lines, rules)
    print(f"line check: {len(index) - len(bad)}/{len(index)} records verified against their source line; "
          f"failures by problem: {dict(Counter(p for _, p, _ in bad).most_common())}")
    for rec, problem, text in bad[:10]:
        print(f"  BAD ({problem}) {rec['doc_key']}:{rec['line']} {rec['kind']} {rec['command'][:100]!r} | "
              f"line: {text[:100]!r}")

    failures = 0
    vocab = keyword_vocab(index)
    print(f"\nspot checks ({len(SPOT_CHECKS)}):")
    for query, rx in SPOT_CHECKS:
        pattern = re.compile(rx or corpus_regex(query), re.I)
        in_corpus = sum(1 for src in lines.values() for ln in src if pattern.search(ln))
        hits = find(index, query, vocab)
        kinds = sorted({h[1]["kind"] for h in hits})
        platforms = sorted({h[1]["platform"] for h in hits})
        status = "ok" if hits else ("GAP (not in corpus)" if in_corpus == 0 else "MISS")
        failures += status == "MISS"
        print(f"- {query!r}: {status}; {len(hits)} index hits, {in_corpus} corpus lines; kinds {kinds}; "
              f"platforms {platforms}")
        for score, rec in hits[: args.show]:
            print(f"    {rec['doc_key']}:{rec['line']} {rec['kind']} [{rec['mode']}] ({rec['release']}) "
                  f"{rec['command']}")
    print(f"\nspot-check misses: {failures}; bad lines: {len(bad)}; unanchored feature rows: {len(loose)}")
    return 1 if failures or len(bad) + len(loose) > args.max_bad_lines else 0


if __name__ == "__main__":
    sys.exit(main())
