#!/usr/bin/env python3
"""Public knowledge-base source helper."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import build_features
from vendor_common import (DEFAULT_MANIFESTS, DEFAULT_SRC, GENERIC_PLATFORMS, HEADING, KB_DIR, PLATFORMS,
                          TRAIN_PREFERENCE, CorpusError, Doc, Release, discover, doc_platforms, doc_release,
                          find_releases, load_manifest, preferred_release, read_doc, source_record)
from kbcommon import write_output

GENERATOR = "staging/kb/build_cli.py v2"
DEFAULT_STYLES = Path("/var/lib/deep32-forge/kb-work/styles")

# --- tokens and syntax normalization ------------------------------------------------------------------------
TOKEN = re.compile(r"<[^<>\s]+>|[\[\]{}|]|[^\s\[\]{}|]+")
CLI_WORD = re.compile(r"[a-z][a-z0-9_.:/-]*")
STOP = {"the", "this", "that", "is", "are", "which", "when", "used", "use", "command", "following", "if", "you",
        "your", "must", "will", "can", "enables", "configures", "specifies", "displays", "an", "of", "by"}
FUNCTION_WORDS = {"to", "be", "the", "a", "on", "for"}
GLUE = r"((?:^|\s)[a-z][a-z0-9-]*[a-z0-9])-[A-Z][a-z]+ "    # "login-Configure ", not "Auto-Template "
PROSE_MARK = re.compile(rf"\(on [^()]* only\)\.?$|{GLUE}[a-z]")   # "(on vEdge routers only)", "login-Configure banner"
KEYWORD_LIKE_IDS = {"router-id", "instance-id", "cluster-id", "domain-name", "system-id"}
ARG_SHAPE = re.compile(r"-(id|name|number|num|address|addr|mask|value|string|seconds|milliseconds|minutes|hours|"
                       r"length|len)$|^(number|name|value|string|seconds|milliseconds|minutes|hours|word|line|text)$"
                       r"|^[A-Z][A-Z0-9]*(-[A-Za-z0-9]+)*$|^[A-Za-z]+-[A-Z]|^[A-X]\.[A-X]|^X:X")
Norm = Callable[..., "str | None"]


class Styles:
    """Argument/keyword votes from document_styles.py lexicons: per doc first, then all docs together.
    rules v4 (style-argshape, strong-single, typed-keywords): the per-document votes are kept too, so that a single
    document's typography (bold user input in a command reference's examples) no longer decides another document's
    rows on its own."""

    def __init__(self, styles_dir: Path | None, rules: frozenset = frozenset()):
        self.docs: dict[str, tuple[dict, dict]] = {}
        self.uni: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self.bi: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self.rules = rules
        self.uni_docs: dict[str, list[tuple[str, int, int]]] = defaultdict(list)   # rules v4: tok -> [(doc, a, kw)]
        if styles_dir and styles_dir.is_dir():
            for p in sorted(styles_dir.glob("*.json")):
                d = json.loads(p.read_text(encoding="utf-8"))
                self.docs[d["doc_key"]] = (d["bigram"], d["unigram"])
                for table, merged in ((d["unigram"], self.uni), (d["bigram"], self.bi)):
                    for k, (a, kw) in table.items():
                        merged[k][0] += a
                        merged[k][1] += kw
                if rules & V4_STYLE_RULES:
                    for k, (a, kw) in d["unigram"].items():
                        self.uni_docs[k].append((d["doc_key"], a, kw))

    def vote(self, doc_key: str, prev: str | None, tok: str) -> bool | None:
        """True = argument, False = keyword, None = no evidence."""
        doc_bi, doc_uni = self.docs.get(doc_key, ({}, {}))
        pair = f"{prev} {tok}" if prev else None
        for table, key in ((doc_bi, pair), (self.bi, pair), (doc_uni, tok), (self.uni, tok)):
            if key is not None and key in table:
                a, kw = table[key]
                if a != kw:
                    if table is self.uni and kw > a and self.lone_bold(tok, doc_key):
                        return None                      # rules v4 (style-argshape): the ARG_SHAPE heuristic decides
                    return a > kw
        return None

    def lone_bold(self, tok: str, doc_key: str) -> bool:
        """rules v4 (style-argshape): an argument-shaped word ("login-string", ARG_SHAPE) that only one other
        document sets in bold ("banner login login-string" in a terminal-services command reference) is no keyword
        by the all-docs unigram table; a bigram ("next-hop peer-address") or two documents still make it one."""
        if "style-argshape" not in self.rules or tok in KEYWORD_LIKE_IDS or not re.fullmatch(r"[a-z][a-z0-9-]*", tok) \
                or not ARG_SHAPE.search(tok):
            return False
        return sum(1 for d, _, kw in self.uni_docs.get(tok, ()) if kw and d != doc_key) < 2

    def keyword_word(self, tok: str) -> bool:
        """rules v4 (typed-keywords): the styles set the word in bold more often than in italics."""
        a, kw = self.uni.get(tok, (0, 0))
        return kw > a

    def strong_keyword(self, tok: str, doc_key: str | None = None) -> bool:
        """Set as a keyword nearly everywhere ("ospf": 1951 bold, 2 italic), so a template placeholder
        such as <protocol> must not take it from an example. rules v4 (strong-single): not when a single document
        sets it in bold and that document is another one than the example's ("CS", "sshkeys", "XXXXX": bold user
        input in the examples of one command reference)."""
        a, kw = self.uni.get(tok, (0, 0))
        strong = kw >= 3 and kw >= 20 * a
        if strong and "strong-single" in self.rules:
            bold = {d for d, _, k in self.uni_docs.get(tok, ()) if k}
            if len(bold) == 1 and doc_key not in bold:
                return False
        return strong


def is_arg(tok: str, prev: str | None, doc_key: str, styles: Styles) -> bool:
    if tok.startswith("<"):
        return True
    if re.fullmatch(r"[\d.:/-]+", tok):                  # literal numbers in syntax ("external 1") are keywords
        return False
    v = styles.vote(doc_key, prev, tok)
    if v is not None:
        return v
    return tok not in KEYWORD_LIKE_IDS and bool(ARG_SHAPE.search(tok))


def join_tokens(toks: list[str]) -> str:
    out = ""
    for t in toks:
        out += t if not out or out.endswith(("[", "{")) or t in ("]", "}") else " " + t
    return out


def balance(toks: list[str]) -> list[str]:
    """Drop unmatched closers and empty groups, close groups left open (PDF rows cut syntax lines)."""
    out, stack = [], []
    for t in toks:
        if t in "[{":
            stack.append(t)
        elif t in "]}":
            if not stack or {"[": "]", "{": "}"}[stack[-1]] != t:
                continue
            stack.pop()
            if out and out[-1] in "[{":                  # empty group
                out.pop()
                continue
        out.append(t)
    out += [{"[": "]", "{": "}"}[s] for s in reversed(stack)]
    while out and out[-1] == "|":
        out.pop()
    return out


def top_level_comma(toks: list[str]) -> bool:
    """A comma outside [] and {} is prose; inside them it is list syntax ("vlan vlan-id [, | -]")."""
    depth = 0
    for t in toks:
        depth += (t in "[{") - (t in "]}")
        if depth <= 0 and "," in t:
            return True
    return False


def normalize_syntax(text: str, doc_key: str, styles: Styles, vetted: bool = False) -> str | None:
    """Syntax text from a doc ("area area-id nssa translate type7 always") -> "area <area-id> nssa ..." or None
    when the text is prose rather than a command. vetted: a command-reference syntax line, which may be long
    ("show ip mroute" has over 100 tokens)."""
    text = re.sub(r"\s+Step \d+\s*$", "", EMBEDDED_PROMPT.split(text, 1)[0].strip()).strip(" ;")
    if text.endswith(".") and not text.endswith("..."):
        text = text[:-1].rstrip()
    toks = TOKEN.findall(text)
    if not toks or len(toks) > (300 if vetted else 80) or not CLI_WORD.fullmatch(toks[0]) or toks[0].endswith(":"):
        return None                                      # "local6.info: 18-Oct-2020 ..." is a log line
    words = [t.lower() for t in toks if t not in "[]{}|"]
    plain = not re.search(r"[\[\]{}|]", text)            # "[for <prefix-acl> [to <peer-acl>]]" is syntax
    if sum(w in STOP for w in words) >= 2 or (plain and sum(w in FUNCTION_WORDS for w in words) >= 3) \
            or re.search(r"[a-z]\. [A-Z]", text) or top_level_comma(toks) or PROSE_MARK.search(text):
        return None
    out, prev = [], None
    for i, t in enumerate(toks):
        dots = len(t) > 3 and (t.endswith("...") or t.startswith("..."))   # "access-list-number...", "[...vlan]"
        base = t.strip(".") if dots else t
        # "[,vlan-id [-vlan-id]]": a list/range mark glued to an argument; a flag such as "boot -n" stays as is
        lead = re.match(r",[A-Za-z]|-[A-Za-z]\w*-", base) and is_arg(base[1:], prev, doc_key, styles)
        if t in "[]{}|":
            out.append(t)
        elif i == 0 or (i == 1 and prev in ("no", "default")):
            out.append(t)                                # the command name itself is never an argument
        elif lead:
            out += [base[0], f"<{base[1:]}>"]
            base = base[1:]
        elif is_arg(base, prev, doc_key, styles):
            out.append(base if base.startswith("<") else f"<{base}>")
        else:
            out.append(base)
        if dots:
            out.append("...")
        if t not in "[]{}|":
            prev = base
    toks = group_top_level_choice(balance(out))
    return join_tokens(toks) if toks else None


OUTPUT_MODIFIERS = {"include", "exclude", "begin", "section", "count", "redirect", "append", "tee", "format", "i", "s"}


def group_top_level_choice(toks: list[str]) -> list[str]:
    """PDFs sometimes lose the braces around a choice: "tunnel source ip-address | type number" ->
    "tunnel source {<ip-address> | <type> <number>}". Output modifiers ("| include ...") are left alone."""
    depth, top = 0, []
    for i, t in enumerate(toks):
        depth += (t in "[{") - (t in "]}")
        if t == "|" and depth == 0 and not (i + 1 < len(toks) and toks[i + 1] in OUTPUT_MODIFIERS):
            top.append(i)
    if not top:
        return toks
    first = next(i for i, t in enumerate(toks) if not literal(t))
    return toks[:first] + ["{"] + toks[first:] + ["}"] if first < top[0] else toks


def literal(tok: str) -> bool:
    """A keyword token: not group syntax, not a <placeholder>, not the "..." repeat marker."""
    return tok not in "[]{}|" and not tok.startswith("<") and tok != "..."


def skeleton(command: str) -> list[str]:
    """Literal keywords outside [optional] groups: "show ip ospf [<pid>] database nssa-external" ->
    [show, ip, ospf, database, nssa-external]."""
    out, depth = [], 0
    for t in TOKEN.findall(command):
        if t in "[]":
            depth += 1 if t == "[" else -1
        elif depth == 0 and literal(t):
            out.append(t.lower())
    return out


# --- values in example commands ------------------------------------------------------------------------------
IFTYPES = (r"GigabitEthernet|FastEthernet|TenGigabitEthernet|TwentyFiveGigE|FortyGigabitEthernet|HundredGigE|"
           r"AppGigabitEthernet|Ethernet|Serial|Loopback|Tunnel|Vlan|Port-channel|Dialer|Virtual-Template|BDI|Null|"
           r"Gi|Fa|Te|Tw|Fo|Hu|Lo|Tu|Po|Se|Vl|Et")
# only glued to their number ("ge0/4", "LISP0.201", "e0/0"): "ge 24" and "tunnel mode gre ip" hold keywords
GLUED_IFTYPES = (r"ge|eth|ATM|POS|BVI|BRI|Hssi|LISP|gre|vmi|nve|VirtualPortGroup|Virtual-?Access|pseudowire|Cellular|"
                 r"Multilink|Async|Twe|(?:Two|Five|TwentyFive|Fifty)GigabitEthernet")
IFNAME = re.compile(rf"(?:(?:{IFTYPES}|{GLUED_IFTYPES})\d+|[egsf]\d+/\d+)(?:[/.:]\d+)*", re.I)
IFTYPE = re.compile(rf"(?:{IFTYPES})", re.I)
IF_ARG = re.compile(r"interface|intf|<if[->]|type|name")          # placeholders that take an interface name
VALUE_TYPES = [
    (re.compile(r"\d{1,3}(\.\d{1,3}){3}/\d{1,2}"), "<ipv4-prefix>"),
    (re.compile(r"\d{1,3}(\.\d{1,3}){3}"), "<ipv4>"),
    (re.compile(r"(?=.*:.*:)[0-9A-Fa-f:]+(/\d{1,3})?"), "<ipv6>"),
    (re.compile(r"[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}"), "<mac>"),
    (re.compile(r"\d+"), "<number>"),
    (re.compile(r"\d+:\d+"), "<nn:nn>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),
    (re.compile(r'"[^"]*"'), "<string>"),
]


def type_values(toks: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if IFNAME.fullmatch(t) or (IFTYPE.fullmatch(t) and i + 1 < len(toks)
                                   and re.fullmatch(r"\d+([/.:]\d+)*", toks[i + 1])):
            out.append("<interface>")
            i += 1 if IFNAME.fullmatch(t) else 2
            continue
        out.append(next((name for rx, name in VALUE_TYPES if rx.fullmatch(t)), t))
        i += 1
    return out


# --- templates: syntax/task commands matched against example commands ---------------------------------------
@dataclass
class Template:
    command: str
    rx: re.Pattern
    groups: list[str]            # placeholder name of regex group g0, g1, ...


def compile_template(command: str) -> Template | None:
    parts, groups, depth = [], [], 0
    for t in TOKEN.findall(command):
        if t in "[{":
            parts.append("(?:")
            depth += 1
        elif t in "]}":
            parts.append(")?" if t == "]" else ")")
            depth -= 1
        elif t == "|":
            parts.append("|" if depth else r"\| ")         # a top-level "|" is an output pipe, not a choice
        elif t.startswith("<"):
            parts.append(rf"(?P<g{len(groups)}>\S+ )")
            groups.append(t)
        else:
            parts.append(re.escape(t.lower()) + " ")
    try:
        return Template(command, re.compile("(?:" + "".join(parts) + ")", re.I), groups)
    except re.error:
        return None


ADDRESS_ARG = re.compile(r"(?:^|-)(?:ip|ipv4|ipv6)?-?(?:address|addr)$")   # rules v4 (typed-keywords)


class TemplateIndex:
    """Syntax/task commands by their leading keywords; example commands are fitted to the best one.
    rules v4 (typed-keywords, pipe-values, task-numbers, cr-modes): the index also knows which documents registered
    each template and whether a command-reference entry did, and judges a fit for the example's document."""

    def __init__(self, styles: Styles, rules: frozenset = frozenset()):
        self.by_prefix: dict[tuple, list[Template]] = defaultdict(list)
        self.seen: set[str] = set()
        self.styles = styles
        self.cache: dict = {}
        self.keywords: Counter = Counter()   # word -> number of templates that have it as a literal keyword
        self.modes: dict[str, Counter] = defaultdict(Counter)   # template -> modes of its rows (rules v3)
        self.rules = rules
        self.v4 = bool(rules & (V4_TEMPLATE_RULES | V4_STYLE_RULES))   # a fit depends on the example's document
        self.tdocs: dict[str, set[str]] = defaultdict(set)          # rules v4: template -> documents that have it
        self.cr: set[str] = set()                                   # rules v4: templates of command-reference entries
        self.cr_docs: dict[str, set[str]] = defaultdict(set)        # rules v4: template -> its command references
        self.cr_modes: dict[str, Counter] = defaultdict(Counter)    # rules v4: template -> its entries' Command Modes
        self.platforms: dict[str, str] = {}                         # rules v4: doc_key -> platform (set by build)

    def add(self, command: str, mode: str | None = None, doc_key: str | None = None, syntax: bool = False):
        """syntax: a command-reference entry's syntax row (rules v4 keeps those apart; doc_key likewise)."""
        if mode:
            self.modes[command][mode] += 1
        if self.v4 and len(command) <= 400:
            if doc_key is not None:
                self.tdocs[command].add(doc_key)
            if syntax:
                self.cr.add(command)
                if doc_key is not None:
                    self.cr_docs[command].add(doc_key)
                if mode:
                    self.cr_modes[command][mode] += 1
            self.cache.clear()
        if command in self.seen or len(command) > 400:
            return
        self.seen.add(command)
        self.keywords.update({t.lower() for t in TOKEN.findall(command) if literal(t)})
        tpl = compile_template(command)
        if tpl is None:
            return
        lead = []
        for t in TOKEN.findall(command):
            if not literal(t) or len(lead) == 3:
                break
            lead.append(t.lower())
        self.by_prefix[tuple(lead)].append(tpl)

    def swallows(self, placeholder: str, word: str, n_lit: int, doc_key: str | None = None) -> bool:
        """Whether a placeholder taking `word` would swallow a keyword: a word the styles set as a keyword nearly
        everywhere, a literal keyword of 20+ templates, or of 3+ templates when the fit has a single literal
        keyword ("match interface Ethernet0/0" is not "match <type> <number>"). A value with digits, or the
        argument's own name typed as the value ("password" for <password>), is never a keyword here.
        rules v4: pipe-values: an output pipe "|" is never a value ("show running-config | section router eigrp 1"
        is not "show running-config <options> section ..."); typed-keywords: an address argument (<ip-address>,
        <peer-address>) takes no plain word that the styles set in bold more often than in italics ("master
        branch" is not "master <ip-address>")."""
        if "pipe-values" in self.rules and word == "|":
            return True
        if re.search(r"\d", word) or word == placeholder[1:-1].lower():
            return False
        if "typed-keywords" in self.rules and ADDRESS_ARG.search(placeholder[1:-1].lower()) \
                and CLI_WORD.fullmatch(word) and not re.search(r"[:.]", word) and self.styles.keyword_word(word):
            return True
        n = self.keywords[word]
        return self.styles.strong_keyword(word, doc_key) or n >= 20 or (n >= 3 and n_lit < 2)

    def normalize(self, cmd: str, doc_key: str | None = None) -> tuple[str, bool]:
        """Example command -> (normalized command, whether a template matched)."""
        key = (cmd, doc_key) if self.v4 else cmd
        if key not in self.cache:
            self.cache[key] = self._normalize(cmd, doc_key)
        return self.cache[key]

    def best_fit(self, cmd: str, doc_key: str | None = None,
                 cr_only: bool = False) -> tuple[Template, re.Match, int] | None:
        """(template, match, words used) of the template an example command fits best, or None.
        A fit ranks by (whole command used, literal keywords matched, words used); a tie keeps the template met
        first. rules v4 (task-numbers): a number that only task steps set as a keyword ("option 150 ip ip-address"
        in a CUCME guide, "vlan 2", "line vty 0 98") is not counted as a matched literal keyword, and such a fit
        loses a tie. cr_only (rules v4, cr-modes): only command-reference templates."""
        low = [t.lower() for t in cmd.split()]
        text = " ".join(low) + " "
        best = None
        for k in (3, 2, 1):
            for tpl in self.by_prefix.get(tuple(low[:k]), ()):
                if cr_only and tpl.command not in self.cr:
                    continue
                m = tpl.rx.fullmatch(text) or tpl.rx.match(text)
                if not m or any(m.start(f"g{i}") == 0 for i in range(len(tpl.groups))):
                    continue
                full = m.end() == len(text)
                used = text[: m.end()].count(" ")
                taken = [(g, m.group(f"g{i}").strip()) for i, g in enumerate(tpl.groups) if m.group(f"g{i}")]
                n_lit = used - len(taken)
                numbered = False
                if "task-numbers" in self.rules and tpl.command not in self.cr:
                    starts = {m.start(f"g{i}") for i in range(len(tpl.groups)) if m.group(f"g{i}")}
                    pos = 0
                    for w in low[:used]:
                        if pos not in starts and w.isdigit():
                            n_lit -= 1
                            numbered = True
                        pos += len(w) + 1
                if not full and n_lit < 2:
                    continue
                if any(self.swallows(g, word, n_lit, doc_key) for g, word in taken):
                    continue
                score = (full, n_lit, used) + ((not numbered,) if "task-numbers" in self.rules else ())
                if best is None or score > best[0]:
                    best = (score, tpl, m, used)
        return best[1:] if best else None

    def fits(self, cmd: str, doc_key: str | None = None) -> bool:
        """The whole command fits a template, every word of it (rules v3: is a step in an ISE book ISE CLI?)."""
        fit = self.best_fit(cmd, doc_key)
        return fit is not None and fit[2] == len(cmd.split())

    def mode_of(self, cmd: str, doc_key: str | None = None) -> str | None:
        """The mode of the template an example command fits, when that template's rows agree on one (rules v3:
        configuration without a prompt). rules v4 (cr-modes): when that template is a single other document's task
        step (its mode taken from that step's example or inferred from its procedure), and a command-reference
        template of the example's own document or platform family with the same literal keywords fits and states
        one other Command Mode, the command reference wins ("ip sla schedule" is global configuration, not the
        config-ip-sla-echo a dial-backup guide's example shows; "type echo protocol ipIcmpEcho" under "ip sla
        monitor" is config-sla-monitor, not that guide's config-rtr)."""
        fit = self.best_fit(cmd, doc_key)
        modes = self.modes.get(fit[0].command) if fit else None
        mode = next(iter(modes)) if modes and len(modes) == 1 else None
        if "cr-modes" in self.rules and mode and fit[0].command not in self.cr \
                and len(self.tdocs.get(fit[0].command, ())) == 1 and doc_key not in self.tdocs[fit[0].command]:
            crfit = self.best_fit(cmd, doc_key, cr_only=True)
            cr_modes = self.cr_modes.get(crfit[0].command) if crfit else None
            fam = ReleaseResolver.family(self.platforms.get(doc_key, ""))
            if cr_modes and len(cr_modes) == 1 and mode not in next(iter(cr_modes)).split("|") \
                    and len(skeleton(crfit[0].command)) >= 2 \
                    and skeleton(crfit[0].command) == skeleton(fit[0].command) \
                    and any(d == doc_key or ReleaseResolver.family(self.platforms.get(d, "")) == fam
                            for d in self.cr_docs.get(crfit[0].command, ())):
                return next(iter(cr_modes))
        return mode

    def _normalize(self, cmd: str, doc_key: str | None = None) -> tuple[str, bool]:
        toks = cmd.split()
        low = [t.lower() for t in toks]
        fit = self.best_fit(cmd, doc_key)
        if fit is None:
            return join_tokens(type_values(toks)), False
        tpl, m, used = fit
        starts = {m.start(f"g{i}"): g for i, g in enumerate(tpl.groups) if m.group(f"g{i}")}
        out, pos = [], 0
        for tok in low[:used]:
            g = starts.get(pos)
            if g and IFNAME.fullmatch(tok) and not IF_ARG.search(g):
                g = "<interface>"                        # "interface Loopback0" fitted to <slot> of a doc typo
            out.append(g or tok)
            pos += len(tok) + 1
        return join_tokens(out + type_values(toks[used:])), True


TYPED_SYNTAX = re.compile(r"(^|\s)[\[{]\s*[a-z<]")               # "history [size number-of-lines]"
NOT_SYNTAX = re.compile(r"[$\"*\\^]|://|\{\}|\[\[")                # regex, shell, Tcl, URLs, quoted text


def balanced(text: str) -> bool:
    return text.count("[") == text.count("]") and text.count("{") == text.count("}")


def example_command(raw: str, templates: TemplateIndex, doc_key: str, styles: Styles) -> tuple[str | None, str]:
    """(command, how) for a command typed at a prompt: "syntax_normalized" when the doc typed syntax there
    ("history [size number-of-lines]", sdwan "action (accept | drop)"), "template_matched" when it fits a
    syntax/task template, "values_typed" otherwise; (None, why) for prose typed as syntax, or when the brackets
    cannot balance (a cut line, a shell macro)."""
    if TYPED_SYNTAX.search(raw) and not NOT_SYNTAX.search(raw) and raw.split()[0] not in TCL_WORDS:
        cmd = normalize_syntax(re.sub(r"\((\s*[\w-]+(?:\s*\|\s*[\w-]+)+\s*)\)", r"{\1}", raw), doc_key, styles)
        return cmd, "syntax_normalized" if cmd else "syntax_rejected"
    cmd, matched = templates.normalize(raw, doc_key)
    if balanced(cmd):
        return cmd, "template_matched" if matched else "values_typed"
    return (join_tokens(type_values(raw.split())), "values_typed") if balanced(raw) else (None, "unbalanced")


# --- prompts and CLI blocks ---------------------------------------------------------------------------------
PROMPT = re.compile(r"^\s*(?P<host>[A-Za-z][\w.\-]{0,40}?(?:/[A-Za-z][\w.\-]{0,31})?)?"
                    r"(?:\((?P<mode>[a-z][\w.\-/+]{0,60}|\d+)\))?(?P<sym>[#>])"
                    r"\s?(?P<cmd>.*)$")         # "(9)" in "R1-AGS(9)#" is an old history counter, not a mode
# host/user is the Cisco ISE application CLI prompt ("ise/admin#", "ise-1/pan(config)#"); the part after "/" must
# start with a letter, so "Gi1/0/1#" or "RP/0/RSP0/CPU0:router#" never parse as prompts
BARE_HOSTS = {"Device", "Router", "Switch", "device", "router", "switch", "vEdge", "vSmart", "vManage", "vBond"}
NOISE = re.compile(r"^(enable|disable|end|exit|exit-[\w-]+|conf(ig(ure)?)?( t\w*)?|no|default|"   # "configure teminal"
                   r"config-transaction|commit|do write|write memory|copy running-config startup-config)$", re.I)
# inline comments: "! ...", "<<<< checking", "<--- note", "-------to check", "--> note", "  //Receiver interface"
# ("tftp://x" and "tftp: //server1/file" keep their "//": a comment "//" follows whitespace that follows no ":")
COMMENT = re.compile(r"\s*(\s!.*|<{2,}.*|<-+.*|-{3,}.*|--+>.*)$|(?<![:\s])\s+//.*$")
# a second prompt on the same line (converter glue): "link-address 2001:1001::0/64(config-dhcpv6)# link-address ..."
EMBEDDED_PROMPT = re.compile(r"(?:(?:^|\s+)[A-Za-z][\w.\-]*(?:/[A-Za-z][\w.\-]*)?)?\(?[\w.\-/+]*\)#")   # host/user: ISE
PRINTS_CONFIG = re.compile(r"(show|sh)\s+(run|running-config|startup-config|full|conf|config|configuration|derived|"
                           r"parser)|more\s")


def output_follows(cmd: str) -> bool:
    """The line after this command is its output, not a wrap: the command has an output filter outside [], {} and
    () ("show run | sec username") or prints configuration, which looks like commands ("show full-configuration")."""
    depth = 0
    for i, ch in enumerate(cmd):
        depth += (ch in "[{(") - (ch in "]})")
        if ch == "|" and depth <= 0 and cmd[i - 1: i] == " ":
            return True
    return bool(PRINTS_CONFIG.match(cmd))


# --- rules v3 (--rules v3; corpus-v6): the CLI findings of the ISE audit (coverage report K2, K4-K7) ----------------
# Each rule is a name so that tests and measurements can switch them one at a time. Rules v2 (the default, empty) is
# the parser that built kb-v1..kb-v5, unchanged.
#   row-platform       a row's platform follows its own prompt: the ISE application CLI prompt (host/user#) gives
#                      "ise" in an IOS-family or ISE doc (not SD-WAN's "vManage/vSmart#"); in an ISE book a router or
#                      switch prompt gives "ios" (the NAD's IOS / IOS XE CLI), and so do, in an ISE book that is no
#                      command reference (the admin guides, not ise31-cli), configuration lines without a prompt and
#                      steps that no ISE CLI syntax entry fits; a Unix shell prompt ("sh-4.2#") is no Cisco CLI and is
#                      not indexed (K2)
#   topic-names        "com.cisco.ise.pubsub" (pxGrid topic names, and Cisco's typo "come.cisco.ise.sxp") is no
#                      command (K4)
#   mode-space         a prompt mode may hold spaces when it starts with "config": ISE "(config-Policy List)#", Cisco
#                      typos "(config router)#"; "config- flow-exporter" (a wrap space after "-") reads
#                      "config-flow-exporter" (K5)
#   cr-own-example     a command-reference entry's own example is kept although the command is a navigation
#                      command elsewhere ("copy running-config startup-config" in ISE's "copy", "exit" in "exit") (K6)
#   unprompted-config  a CLI block without any prompt whose every line starts with an IOS command word (the first
#                      keywords of the IOS-lineage syntax entries; "no"/"default" + one) and none reads as show output
#                      or prose (config_line) is configuration: each line is an example row (a numbered entry "10
#                      class ..." is none, as at a prompt), its mode that of the syntax or task template it fits when
#                      the template's rows agree on one (else null; "show"/"debug"... privileged-exec; an ISE row
#                      the ISE CLI's); these rows are dated but date no other row, and never displace a rules v2 row
#                      (K7)
#   digit-prompt       inside CLI blocks a hostname may start with a digit ("3850-1#", "6500(config)#", "9300-Switch#")
#                      when a (mode) follows, the host holds a letter or "-", "_" or "." (not an IPv4 address and not
#                      ending in "-": "10.1.1.1->" debug lines), or the host has 3+ digits and the command starts with
#                      an EXEC verb; "1#<md5>" and "546#feat:1" in c9300-1718-cr are no prompts
#
# --- rules v4 (--rules v4; corpus-v7.1 candidate): v3 plus narrow guards against one document's evidence rewriting
# another document's rows (kb-diff/REPORT.md: round add6's 41 texts re-normalized 965 rows of the corpus-v6 documents,
# 187 of them worse). Each guard changes a decision only where a single other document, a single task step or one
# document's typography decides it; broader rules (per-document style votes, template ties by document affinity,
# mode-filtered command-reference dating, per-family mode learning ...) were measured and left out because they also
# rewrite thousands of rows that corpus-v6's own inputs give (kb-diff/RULES-V4.md has the measurements).
#   strong-single      styles: a word set in bold by one other document only ("CS", "sshkeys", "XXXXX": bold user
#                      input in one command reference's examples) is no strong keyword of another document's examples
#   style-argshape     styles: an argument-shaped word ("login-string") is no keyword by the all-docs unigram table
#                      when only one other document sets it in bold
#   typed-keywords     templates: an address argument (<ip-address>, <peer-address>) takes no plain word that the
#                      styles set in bold more often than in italics ("master branch" is not "master <ip-address>")
#   pipe-values        templates: an output pipe "|" is never an argument value
#   task-numbers       templates: a number set as a keyword only by task steps is a value, not a matched keyword, and
#                      such a fit loses a tie ("option 150 ip ip-address" in a CUCME guide, "vlan 2", "line vty 0 98")
#   cr-modes           templates: an unprompted configuration line whose template is a single other document's task
#                      step takes the Command Mode of a command-reference template of its own document or platform
#                      family with the same keywords where the two disagree ("ip sla schedule", "type echo protocol")
#   lexicon-title      lexicon: an IOS command word comes from a command-reference entry whose own heading names it
#                      ("default interface-type interface-number" under "default interface" adds no word)
RULE_SETS = {
    "v2": frozenset(),
    "v3": frozenset({"row-platform", "topic-names", "mode-space", "cr-own-example", "unprompted-config",
                     "digit-prompt"}),
}
V4_STYLE_RULES = frozenset({"strong-single", "style-argshape"})
V4_TEMPLATE_RULES = frozenset({"typed-keywords", "pipe-values", "task-numbers", "cr-modes"})
RULE_SETS["v4"] = RULE_SETS["v3"] | V4_STYLE_RULES | V4_TEMPLATE_RULES | {"lexicon-title"}
ALL_RULES = frozenset().union(*RULE_SETS.values())
NO_RULES: frozenset = frozenset()
SPACED_PROMPT = re.compile(r"^\s*(?P<host>[A-Za-z][\w.\-]{0,40}?(?:/[A-Za-z][\w.\-]{0,31})?)?"
                           r"(?:\((?P<mode>config[\w.\-/+]*(?: +[\w.\-/+]+){1,3}|[a-z][\w.\-/+]{0,60}|\d+)\))?"
                           r"(?P<sym>[#>])\s?(?P<cmd>.*)$")     # PROMPT, plus "(config-Policy List)"
DIGIT_PROMPT = re.compile(r"^\s*(?P<host>\d[\w.\-]{0,40}?)(?:\((?P<mode>[a-z][\w.\-/+]{0,60})\))?(?P<sym>[#>])"
                          r"\s?(?P<cmd>.*)$")
DIGIT_HOST_VERBS = {"show", "sh", "cts", "debug", "undebug", "clear", "test", "request", "remote", "ping", "traceroute",
                    "copy", "dir", "more", "write", "reload", "terminal", "configure", "conf", "monitor", "switch",
                    "chassis", "set"}
SHELL_HOST = re.compile(r"(?:ba|z|k|c|tc)?sh-\d+(?:\.\d+)*")    # "sh-4.2#": the ISE appliance's Linux shell


def digit_host_ok(m: re.Match) -> bool:
    """A DIGIT_PROMPT match that is a prompt (see digit-prompt above)."""
    host, cmd = m.group("host"), m.group("cmd").strip()
    if m.group("mode"):
        return True
    if re.search(r"[A-Za-z_.\-]", host):
        return not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) and not host.endswith("-")
    return len(host) >= 3 and bool(cmd) and cmd.split()[0] in DIGIT_HOST_VERBS


def prompt_match(line: str, strict: bool, rules: frozenset = NO_RULES) -> re.Match | None:
    """The prompt match of a line with a host or a mode: PROMPT (rules v2), SPACED_PROMPT (mode-space) and, inside CLI
    blocks only (not strict), the DIGIT_PROMPT fallback (digit-prompt)."""
    m = (SPACED_PROMPT if "mode-space" in rules else PROMPT).match(line)
    if m and (m.group("host") or m.group("mode")):
        return m
    if "digit-prompt" in rules and not strict:
        d = DIGIT_PROMPT.match(line)
        if d and digit_host_ok(d):
            return d
    return None


EMBEDDED_VARIANTS: dict[tuple[bool, bool], re.Pattern] = {}


def embedded_prompt(rules: frozenset = NO_RULES) -> re.Pattern:
    """EMBEDDED_PROMPT (rules v2), or its rules v3 form: a spaced "(config-Policy List)#" (mode-space), and a
    digit-led host before a mode, "9500H(config-pmap-c)#" (digit-prompt), so neither is left glued to a command."""
    key = ("mode-space" in rules, "digit-prompt" in rules)
    if key == (False, False):
        return EMBEDDED_PROMPT
    if key not in EMBEDDED_VARIANTS:
        host = r"[A-Za-z][\w.\-]*(?:/[A-Za-z][\w.\-]*)?" + (r"|\d[\w.\-]*(?=\()" if key[1] else "")
        paren = r"(?:\(config[\w.\-/+]*(?: +[\w.\-/+]+){1,3}|\(?[\w.\-/+]*)" if key[0] else r"\(?[\w.\-/+]*"
        EMBEDDED_VARIANTS[key] = re.compile(rf"(?:(?:^|\s+)(?:{host}))?{paren}\)#")
    return EMBEDDED_VARIANTS[key]


def is_prompt(line: str, rules: frozenset = NO_RULES) -> bool:
    """A prompt, with or without a command after it ("device(config-router-lisp)#" alone counts)."""
    return prompt_match(line, False, rules) is not None


def clean_mode(mode: str) -> str:
    """ "config_if" (a doc typo) -> "config-if"; names inside a mode keep theirs ("config-vpn-list-vpn_1")."""
    return mode.replace("_", "-") if mode.startswith("config_") else mode


def parse_prompt(line: str, strict: bool, rules: frozenset = NO_RULES) -> tuple[str, str] | None:
    """(mode, command) of a prompt line, or None. strict (outside CLI blocks): only well-known hosts count."""
    p = parse_prompt_host(line, strict, rules)
    return p[:2] if p else None


def parse_prompt_host(line: str, strict: bool, rules: frozenset = NO_RULES) -> tuple[str, str, str | None] | None:
    """parse_prompt() plus the prompt's host ("ise/admin", "Switch"; None for a bare "(config-if)#")."""
    m = prompt_match(line, strict, rules)
    if not m:
        return None
    if strict and not m.group("mode") and m.group("host") not in BARE_HOSTS:
        return None
    if "row-platform" in rules and m.group("host") and SHELL_HOST.fullmatch(m.group("host")):
        return None                                      # a Unix shell, not a Cisco CLI
    cmd = COMMENT.sub("", embedded_prompt(rules).split(m.group("cmd"), 1)[0].strip())
    if not cmd or not re.match(r"[a-z]", cmd) or "?" in cmd or len(cmd) > 300:
        return None
    if strict and (len(cmd.split()) > 20 or sum(w in STOP for w in cmd.lower().split()) >= 2):
        return None                                      # prose that merely starts with "Device#"
    mode = m.group("mode")
    if not mode or mode.isdigit():
        mode = "privileged-exec" if m.group("sym") == "#" else "user-exec"
    else:
        mode = clean_mode(mode)
        if "mode-space" in rules:
            mode = re.sub(r"-\s+", "-", re.sub(r"\s+", " ", mode))   # "config- flow-exporter": a wrap space
    if cmd.startswith("do "):
        cmd, mode = cmd[3:].strip(), "privileged-exec"
    return mode, cmd, m.group("host")


@dataclass
class Raw:
    """A command occurrence. Syntax and task text is normalized when the Raw is made; examples later."""
    text: str
    mode: str | None
    line: int
    kind: str
    normalized: bool = True
    release: str | None = None   # from the entry's own Command History
    train: str | None = None     # the release's train (iosxe, ios, sdwan-manager, catalyst-center)
    group: int = 0               # task steps of one procedure share a group (mode inference)
    prompt: str | None = None    # rules v3, a row typed at a prompt: "ise" (host/user#) or "ios" (any other)
    unprompted: bool = False     # rules v3: a configuration line of a CLI block without prompts
    title: str | None = None     # a syntax row: its command-reference entry's heading (rules v4 lexicon-title)


def prompt_kind(host: str | None) -> str:
    """ "ise" for the ISE application CLI prompt (host/user), else "ios" (a router, switch or bare "(config)#")."""
    return "ise" if host and "/" in host else "ios"


def cli_blocks(doc: Doc, rules: frozenset = NO_RULES) -> list[Raw]:
    out: list[Raw] = []
    n = len(doc.lines)
    embedded = embedded_prompt(rules)
    i = 1
    while i <= n:
        text = doc.lines[i - 1]
        in_block = doc.in_fence[i - 1] and not text.startswith("```")
        p = parse_prompt_host(text, not in_block, rules)
        if p is None:
            i += 1
            continue
        mode, cmd, host = p
        j = i + 1
        # PDF CLI blocks wrap long config commands onto the next line, sometimes into a block of its own (after
        # an EXEC command the next line is its output; after an inline comment or a glued second prompt it
        # belongs to those)
        raw = prompt_match(text, not in_block, rules).group("cmd")
        if in_block and not mode.endswith("exec") and len(text) >= 35 and not output_follows(cmd) \
                and not COMMENT.search(raw) and not embedded.search(raw):
            k = j + 2 if doc.lines[j - 1: j + 1] == ["```", "```"] else j
            nxt = doc.lines[k - 1].strip() if k <= n and doc.in_fence[k - 1] else ""
            nxt = embedded.split(nxt, 1)[0].strip()
            if nxt and not nxt.startswith("```") and not is_prompt(nxt, rules) \
                    and re.match(r"[a-z0-9<]", nxt) and len(nxt.split()) <= 8:
                cmd = f"{cmd} {nxt}"
                j = k + 1
        kind = "example"
        if cmd.startswith("show ") and in_block:
            k = j
            while k <= n and doc.in_fence[k - 1] and not doc.lines[k - 1].startswith("```") \
                    and not doc.lines[k - 1].strip():
                k += 1
            if k <= n and doc.in_fence[k - 1] and not doc.lines[k - 1].startswith("```") \
                    and parse_prompt(doc.lines[k - 1], False, rules) is None:
                kind = "show-output"
        out.append(Raw(cmd, mode, i, kind, normalized=False,
                       prompt=prompt_kind(host) if "row-platform" in rules else None))
        i = j
    return out


class ModeModel:
    """Which mode a command enters, learned from consecutive prompts in the CLI blocks
    (Device(config)# router ospf 1 / Device(config-router)# ... => "router ospf" enters config-router)."""
    FIXED = {("configure", "terminal"): "config", ("enable",): "privileged-exec", ("end",): "privileged-exec"}

    def __init__(self):
        self.enters: dict[tuple, Counter] = defaultdict(Counter)

    @staticmethod
    def key(cmd: str) -> tuple:
        toks = [t.lower() for t in TOKEN.findall(cmd) if t not in "[]{}|"][:2]
        if len(toks) == 2 and not re.fullmatch(r"[a-z][a-z-]*", toks[1]):
            toks = toks[:1]                              # "interface Gi1/0/1", "interface <type>" -> (interface,)
        return tuple(toks)

    def learn(self, doc: Doc, rules: frozenset = NO_RULES):
        for start, end in doc.fences:
            prompts = [parse_prompt(doc.lines[k - 1], False, rules) for k in range(start + 1, end)]
            prompts = [p for p in prompts if p]
            for (m1, c1), (m2, _) in zip(prompts, prompts[1:]):
                if not NOISE.match(c1):
                    self.enters[(m1, *self.key(c1))][m2] += 1

    def next_mode(self, mode: str | None, cmd: str) -> str | None:
        k = self.key(cmd)
        if k in self.FIXED:
            return self.FIXED[k]
        if k[:1] == ("exit",) or mode is None:
            return None
        seen = self.enters.get((mode, *k))
        if seen:
            best, n = seen.most_common(1)[0]
            if n >= 2 and n >= 0.6 * sum(seen.values()):
                return best
        return mode


# --- command reference entries ------------------------------------------------------------------------------
GLUED_SUMMARY = re.compile(rf"{GLUE}[^\[\]{{}}|]*$")         # sdwan-cr "banner login-Configure banner text ..."
CR_FIELD = re.compile(r"^(Syntax Description|Command Default|Command Modes|Command History|Usage Guidelines|"
                      r"Examples?\b|Related Commands)")
MODE_NAMES = [(r"privileged exec", "privileged-exec"), (r"user exec", "user-exec"),
              (r"global configuration", "config"), (r"interface configuration", "config-if"),
              (r"router configuration", "config-router"), (r"line configuration", "config-line"),
              (r"boot ?loader|rommon", "rommon")]
MODE_STOP = {"the", "a", "an", "and", "or", "for", "in", "to", "of", "from", "any", "all", "this", "that", "on",
             "with", "into", "enter", "enters", "entering"}


def mode_from_name(name: str) -> list[str]:
    name = re.sub(r"\bmode\b", "", name.lower()).strip()
    if re.search(r"\bexec\b", name) and not re.search(r"(privileged|user) exec", name):
        return ["user-exec", "privileged-exec"]          # older references say just "EXEC"
    found = []
    for m in re.finditer(r"privileged exec|user exec|boot ?loader|rommon|(?:[a-z0-9-]+ ){0,3}configuration", name):
        words = m.group(0).split()                       # "the crypto transform configuration": no "the"
        phrase = " ".join(words[max((i + 1 for i, w in enumerate(words) if w in MODE_STOP), default=0):])
        mode = next((v for pat, v in MODE_NAMES if re.fullmatch(pat, phrase)), None)
        if mode is None and phrase.endswith("configuration"):
            stem = re.sub(r"[^a-z0-9]+", "-", phrase[: -len("configuration")]).strip("-")
            mode = f"config-{stem}" if stem else "config"
        if mode:
            found.append(mode)
    return found


def parse_modes(text: str) -> str | None:
    """ "Router configuration (config-router) Address family configuration (config-router-af)" ->
    "config-router|config-router-af"; "Privileged EXEC (#)" -> "privileged-exec"."""
    modes: list[str] = []
    for seg in re.split(r"(?<=[)\]])\s+", text):
        paren = re.search(r"[(\[]([^()\[\]]*(?:\([^()]*\)[^()\[\]]*)?)[)\]]", seg)
        inner = paren.group(1).strip() if paren else ""
        name = seg[: paren.start()] if paren else seg
        nested = re.search(r"\(([\w-]+)\)\s*#", inner)
        if re.fullmatch(r"[a-z][\w-]*", inner):
            modes.append(inner)
        elif nested:
            modes.append(nested.group(1))
        else:
            by_name = mode_from_name(name)
            if by_name:
                modes += by_name
            elif inner.endswith("#"):
                modes.append("privileged-exec")
            elif inner.endswith(">"):
                modes.append("user-exec")
    return "|".join(dict.fromkeys(clean_mode(m) for m in modes)) or None


def split_forms(text: str, name: str) -> list[str]:
    """The PDF joins a command's syntax forms into one paragraph line ("area area-id nssa ... no area area-id
    nssa ...", "show ip ospf database show ip ospf database [adv-router]"): a new form starts where the command
    name, or "no"/"default" + name, recurs outside [] and {}."""
    toks = text.split()
    forms, cur, depth = [], [], 0
    for i, t in enumerate(toks):
        new = t == name or (t in ("no", "default") and i + 1 < len(toks) and toks[i + 1] == name)
        if new and depth == 0 and cur and cur[-1] not in ("no", "default"):
            forms.append(" ".join(cur))
            cur = []
        cur.append(t)
        depth += t.count("[") + t.count("{") - t.count("]") - t.count("}")
    return forms + [" ".join(cur)]


def history_release(history: str) -> Release | None:
    """The release a Command History table introduces the command in: its earliest IOS XE release, unless IOS XE
    appears only from 16.x on and the table also lists classic IOS releases. Then the command predates IOS XE 16
    and the first classic IOS row is kept ("12.2(8)T ... Cisco IOS XE 17.11.1a: default mode changed" is not a
    17.11 command)."""
    rels = find_releases(history)
    best = preferred_release(rels)
    classic = [r for r in rels if r.train == "ios"]
    if best and best.train == "iosxe" and best.key[:1] >= (16,) and classic:
        return classic[0]
    return best


def cr_fields(doc: Doc, hl: int, level: int, end: int) -> list[tuple[int, str, bool]]:
    """(line, label, is_heading) of the command-reference fields of the entry at heading line `hl`: plain
    "Command Modes ..." lines (PDF books) or "### Command Modes" sub-headings (HTML books)."""
    out = []
    for ln in range(hl + 1, end + 1):
        if doc.in_fence[ln - 1]:
            continue
        text = doc.lines[ln - 1]
        head = HEADING.match(text)
        m = CR_FIELD.match(head.group(2).strip() if head else text)
        if m and (not head or len(head.group(1)) > level):
            out.append((ln, m.group(1), bool(head)))
    return out



def cr_entries(doc: Doc) -> list[tuple[int, int, int, str, list[tuple[int, str, bool]]]]:
    """(heading line, level, last line, title, fields) of each command-reference entry: a heading that names a
    command, with at least two of the Syntax Description, Command Modes and Command History fields."""
    out = []
    heads = doc.headings
    for idx, (hl, level, title) in enumerate(heads):
        name = title.split()[0] if title.split() else ""
        if not CLI_WORD.fullmatch(name):
            continue
        end = next((h[0] - 1 for h in heads[idx + 1:] if h[1] <= level), len(doc.lines))
        fields = cr_fields(doc, hl, level, end)
        if len({f for _, f, _ in fields} & {"Syntax Description", "Command Modes", "Command History"}) >= 2:
            out.append((hl, level, end, title, fields))
    return out


def in_own_entry(entries: list, line: int, cmd: str) -> bool:
    """rules v3 (cr-own-example): the line lies in the command-reference entry of this very command ("copy" for
    "copy running-config startup-config", "exit (EXEC)" for "exit")."""
    words = cmd.lower().split()
    for hl, _, end, title, _ in entries:
        name = re.sub(r"\s*\([^()]*\)$", "", title).lower().split()
        if hl < line <= end and name and words[:len(name)] == name:
            return True
    return False


def command_reference(doc: Doc, norm: Norm) -> list[Raw]:
    out: list[Raw] = []
    heads = doc.headings
    for hl, level, end, title, fields in cr_entries(doc):
        name = title.split()[0]

        def block(label: str) -> str:
            ln, is_head = next(((l, h) for l, f, h in fields if f == label), (None, False))
            if ln is None:
                return ""
            stop = next((l for l, _, _ in fields if l > ln), end + 1)
            if is_head:
                stop = min([stop] + [h[0] for h in heads if ln < h[0] < stop])
                return " ".join(doc.lines[k - 1] for k in range(ln + 1, stop) if not doc.in_fence[k - 1])
            return " ".join(doc.lines[k - 1] for k in range(ln, stop) if not doc.in_fence[k - 1])[len(label):]

        modes_text, history = block("Command Modes"), block("Command History")
        if not history:          # an HTML book sometimes drops the heading; its table then follows the modes
            m = re.search(r"Release \| Modification|Modification \| Release", modes_text)
            if m:
                modes_text, history = modes_text[: m.start()], modes_text[m.start():]
        modes = parse_modes(modes_text)
        rel = history_release(history)
        forms = []
        for ln in range(hl + 1, fields[0][0]):
            # "area nssa translate commandarea area-id ...": an HTML index term glued before the syntax
            text = re.sub(rf"^{re.escape(title)} command(?={re.escape(name)}\b)", "", doc.lines[ln - 1])
            if not doc.in_fence[ln - 1] and re.match(rf"(no |default )?{re.escape(name)}(?![\w-])", text):
                forms += [(ln, f) for f in split_forms(GLUED_SUMMARY.sub(r"\1", text), name)]
        entry = [(ln, cmd) for ln, text in forms if (cmd := norm(text, vetted=True))]
        if not entry:            # no syntax line, or none that parses: the heading names the command
            cmd = norm(re.sub(r"\s*\([^()]*\)$", "", title), vetted=True)     # "pool (isakmp-group)" -> "pool"
            entry = [(hl, cmd)] if cmd else []
        out += [Raw(cmd, modes, ln, "syntax", release=rel and rel.version, train=rel and rel.train, title=title)
                for ln, cmd in entry]
    return out


# --- configuration task steps -------------------------------------------------------------------------------
STEP_ITEM = re.compile(r"^(\d{1,2})\.\s+(.*)$")
PIPE_STEP = re.compile(r"^Step (\d+) \| (.*)$")
BULLET = re.compile('^[\u2022\u25cf\u25e6\u25aa]\\s*(.*)$')
STEP_MARK = re.compile(r"(^|\s)Step \d+\s*$")


def split_numbered(no: int, text: str) -> list[str]:
    """ "enable 2. configure terminal 3. router ospf process-id" (item `no` with the next items glued on)."""
    parts = []
    while True:
        m = re.search(rf"(?<=\s){no + 1}\.(?:\s+|$)", text)      # "... max-header-size 5." ends with item 5
        if not m:
            return [p for p in parts + [text.strip()] if p]
        parts.append(text[: m.start()].strip())
        text, no = text[m.end():], no + 1


def split_cells(row: str) -> list[str]:
    """Split an ePub table row on " | " outside [] and {} (syntax alternations use " | " too)."""
    cells, depth, cur = [], 0, []
    for part in re.split(r"( \| )", row):
        if part == " | " and depth == 0:
            cells.append("".join(cur).strip())
            cur = []
            continue
        depth += part.count("[") + part.count("{") - part.count("]") - part.count("}")
        cur.append(part)
    return cells + ["".join(cur).strip()]


def prompts_between(doc: Doc, a: int, b: int, rules: frozenset = NO_RULES) -> list[tuple[int, str, str]]:
    """(line, mode, command) of the prompt lines inside CLI blocks between lines a and b."""
    out = []
    for ln in range(max(a, 1), min(b, len(doc.lines)) + 1):
        if doc.in_fence[ln - 1] and not doc.lines[ln - 1].startswith("```"):
            p = parse_prompt(doc.lines[ln - 1], False, rules)
            if p:
                out.append((ln, p[0], p[1]))
    return out


def match_mode(cmd: str, examples: list[tuple[int, str, str]], start: int = 0) -> tuple[str | None, int]:
    """Mode of the first example (from index `start`) that agrees with the normalized command's leading
    keywords (a placeholder or group in the command accepts any example token)."""
    toks = TOKEN.findall(cmd)[:2]
    for i in range(start, len(examples)):
        ex = examples[i][2].lower().split()
        if ex[:1] == [toks[0].lower()] and (len(toks) < 2 or len(ex) < 2 or not literal(toks[1])
                                            or ex[1] == toks[1].lower()):
            return examples[i][1], i + 1
    return None, start


def summary_steps(doc: Doc, norm: Norm, group0: int, rules: frozenset = NO_RULES) -> tuple[list[Raw], set[int]]:
    """Router-book tasks: "SUMMARY STEPS" + numbered commands, modes from the DETAILED STEPS examples.
    Returns the steps and the lines of the DETAILED STEPS regions (their rows repeat the summary)."""
    out: list[Raw] = []
    covered: set[int] = set()
    n = len(doc.lines)
    group = group0
    for i in range(1, n + 1):
        if doc.in_fence[i - 1] or not doc.lines[i - 1].startswith("SUMMARY STEPS"):
            continue
        group += 1
        items, j = [], i + 1
        while j <= n and not doc.in_fence[j - 1]:
            text = doc.lines[j - 1].strip()
            m = STEP_ITEM.match(text)
            if m:
                items += [(j, t) for t in split_numbered(int(m.group(1)), m.group(2))]
            elif items and BULLET.match(text):
                items.append((j, BULLET.match(text).group(1)))
            elif items or j - i > 3:
                break
            j += 1
        detail = next((k for k in range(j, min(n, j + 400) + 1) if doc.lines[k - 1].startswith("DETAILED STEPS")),
                      None)
        examples = []
        if detail is not None:
            end = next((x[0] for x in doc.headings if x[0] > detail), n + 1) - 1
            covered.update(range(detail, end + 1))
            examples = prompts_between(doc, detail, end, rules)
        ptr = 0
        for ln, text in items:
            cmd = norm(text)
            if cmd:
                mode, ptr = match_mode(cmd, examples, ptr)
                out.append(Raw(cmd, mode, ln, "task", group=group))
    return out, covered


def pipe_steps(doc: Doc, norm: Norm, group0: int, rules: frozenset = NO_RULES) -> list[Raw]:
    """ePub tasks: "Step N | command | purpose" rows; the CLI block after the row gives the mode."""
    out: list[Raw] = []
    n = len(doc.lines)
    group = group0
    for i in range(1, n + 1):
        m = PIPE_STEP.match(doc.lines[i - 1]) if not doc.in_fence[i - 1] else None
        if not m:
            continue
        if m.group(1) == "1":
            group += 1
        nxt = next((k for k in range(i + 1, min(n, i + 60) + 1) if PIPE_STEP.match(doc.lines[k - 1])
                    or doc.lines[k - 1].startswith("#")), min(n, i + 60))
        examples = prompts_between(doc, i + 1, nxt, rules)
        for cmd in step_commands(join_output_filters(split_cells(m.group(2))), norm):
            out.append(Raw(cmd, match_mode(cmd, examples)[0], i, "task", group=group))
    return out


def step_commands(cells: list[str], norm: Norm) -> list[str]:
    """The commands of an ePub step row. "cmd | purpose | notes": the leading run of commands, also after an
    intro ("Use one of the following: | cmd1 | or | cmd2 | purpose"). "title | cmd | purpose | cmd | purpose":
    the commands followed by their purpose. Show commands and keyword lists in the notes ("aarp | amber | ...")
    are not steps."""
    cells = [c for c in cells if c and c.lower() != "or"]
    cmds = [norm(c) if re.match(r"[a-z]", c) and not re.search(r"(?<!\.\.)\.$", c) and len(c) < 300 else None
            for c in cells]
    if not cells:
        return []
    if cmds[0] or cells[0].endswith(":"):
        start = 0 if cmds[0] else 1
        end = next((k for k in range(start, len(cells)) if not cmds[k]), len(cells))
        return cmds[start:end]
    out, run = [], []
    for cell, cmd in zip(cells[1:] + [""], cmds[1:] + [None]):
        if cmd:
            run.append(cmd)
            continue
        if cell and 0 < len(run) <= 2:                   # one or two commands, then their purpose
            out += run
        run = []
    return out


def join_output_filters(cells: list[str]) -> list[str]:
    """ "show running-config | i autoQos" split into two cells at its " | " rejoined."""
    out: list[str] = []
    for cell in cells:
        if out and re.match(r"(show|sh) ", out[-1]) and re.match(r"(i|inc|include|e|exclude|b|begin|s|sec|section|"
                                                                 r"count)\s", cell):
            out[-1] += " | " + cell
        else:
            out.append(cell)
    return out


def table_steps(doc: Doc, norm: Norm, covered: set[int], group0: int, rules: frozenset = NO_RULES) -> list[Raw]:
    """PDF "Purpose Command or Action" rows ("Enables OSPF routing. router ospf process-id Step 3"): the
    command column is glued to the purpose text, so it is cut out where the step's example command starts.
    Only rows with a "Step N" marker between them and the example block count."""
    out: list[Raw] = []
    group = group0
    for start, end in doc.fences:
        if start in covered:
            continue
        prompts = prompts_between(doc, start + 1, end - 1, rules)
        if not prompts:
            continue
        _, mode, example = prompts[0]
        ex = example.split()
        stepped = False
        for back in range(start - 1, max(0, start - 5), -1):
            text = doc.lines[back - 1]
            if doc.in_fence[back - 1] or text.startswith("#") or PIPE_STEP.match(text):
                break                                    # ePub step rows are handled by pipe_steps()
            mark = STEP_MARK.search(text)
            stepped = stepped or bool(mark)
            if mark and mark.group(0).strip() == "Step 1":
                group += 1
            cand = STEP_MARK.sub("", text).strip()
            best = None
            for m in re.finditer(rf"(?<![\w-]){re.escape(ex[0])}(?![\w-])", cand):
                agree = sum(1 for a, b in zip(cand[m.start():].split(), ex) if a == b)
                if agree >= min(2, len(ex)) and (best is None or agree >= best[0]):
                    best = (agree, m.start())
            if best:
                cmd = norm(cand[best[1]:]) if stepped else None
                if cmd:
                    out.append(Raw(cmd, mode, back, "task", group=group))
                break
    return out


TCL_WORDS = {"if", "proc", "set", "foreach", "while", "puts", "return", "for", "namespace", "array", "expr",
             "fconfigure", "gets"}


def fenced_syntax(doc: Doc, norm: Norm, group0: int, rules: frozenset = NO_RULES) -> list[Raw]:
    """Syntax that the PDF set in a CLI block without a prompt, often wrapped over lines ("show ip ospf
    [process-id" / "[area-id]] database [nssa-external] [link-state-id]"): short blocks with [] or {} notation."""
    out: list[Raw] = []
    for n, (start, end) in enumerate(doc.fences):
        body = [doc.lines[k - 1].strip() for k in range(start + 1, end) if doc.lines[k - 1].strip()]
        if not body or len(body) > 4 or any(parse_prompt(b, False, rules) for b in body):
            continue
        text = " ".join(body)
        opened = body[0].count("[") + body[0].count("{") > body[0].count("]") + body[0].count("}")
        first = text.split()[0]
        if not re.search(r"[\[{]\s*[a-z<]", text) or re.search(r"[$=;#*%!()?]|::|\{\}", text) \
                or not CLI_WORD.fullmatch(first) or "_" in first or first in TCL_WORDS \
                or (len(body) > 1 and not opened):
            continue                                     # macros, Tcl, boot logs, arithmetic, config snippets
        cmd = norm(text)
        if cmd:
            mode = match_mode(cmd, prompts_between(doc, end + 1, end + 30, rules))[0]
            out.append(Raw(cmd, mode, start + 1, "task", group=group0 + n))
    return out


UNPROMPTED_SKIP = re.compile(r"^\s*(?:!.*|\.{3,}|<[^<>]*(?:omitted|snip|truncated)[^<>]*>)?\s*$", re.I)
DEBUG_STATE = re.compile(r"\bdebugging is (?:on|off)\b")   # "debug platform rep event debugging is on": output
TOPIC_NAME = re.compile(r"come?\.cisco\.")               # rules v3 (topic-names): "com.cisco.ise.pubsub"
# rules v3 (unprompted-config): a configuration line's own comment, beyond COMMENT: a "!" glued to the command
# ("match ip address kmd! Defines ..."), a trailing parenthesis of words ("ip route ... x.x.x.x (next hop to the
# internet)"; not a choice "(full | half)", a field "(PeerIP: 10.1.1.1)" or a count "(4 matches)": NOT_CONFIG)
UNPROMPTED_COMMENT = re.compile(r"\s*!\s.*$|\s*\((?=[A-Za-z])(?![^()]*[|:])[^()]*\s[^()]*\)\s*$")
SEQUENCE_NUMBER = re.compile(r"^\d+\s+(?=[a-z])")         # "10 class always do-until-failure": a numbered entry
FREE_TEXT = re.compile(r"(?:^|\s)(?:description|remark|banner|message|msg|contact|location)\s.*$")   # any words
FILESYSTEM = (r"(?:[a-z]+-)?(?:flash|bootflash|nvram|disk|harddisk|usbflash|slot|tftp|ftp|scp|sftp|https?|xmodem|"
              r"ymodem|rcp|crashinfo|system|null|archive)\d*")
# show output, prose or code in a CLI block, not configuration: a sentence ("is", "are", "was", "were"; a final "."),
# a field label ("remote binding: lsr: ...", "ping x.cisco.com: Temporary failure", but not "copy xmodem: flash:"),
# a parenthesis that starts with a number ("(4 matches)", "lifetime (12:00:00 UTC Oct 29 2014) - ..."), syntax
# notation "duplex (full | half)" (an SD-WAN command hierarchy), a shell assignment ("export CATALYST_PORT=443"; not
# "subject-name cn=R4,OU=Flex") or a function call ("execute('write memory')", Tcl "unset cat_msg_sev($index)")
NOT_CONFIG = re.compile(rf"(?<![\w-])(?:is|are|was|were)(?![\w-])|(?<![\w.:/-])(?!{FILESYSTEM}:)[A-Za-z][\w.-]*: |"
                        r"[a-z]\.$|\(\d[^()]*\s[^()]*\)|\([^()]*\|[^()]*\)|^(?:export\s+)?[A-Za-z_]\w*=\S|[A-Za-z]\(")


def command_word(text: str) -> str:
    """The command's first word, after "no" or "default" ("no shutdown" -> "shutdown")."""
    words = text.split()
    return words[1] if len(words) > 1 and words[0] in ("no", "default") else (words[0] if words else "")


def config_line(text: str, lexicon: set[str]) -> bool:
    """rules v3 (unprompted-config): a line that reads as configuration: its command word (a numbered entry's, after
    the number) is an IOS command word, and nothing in it reads as output or prose (NOT_CONFIG; quoted text and the
    free text of description, remark, banner ... are not looked at)."""
    if command_word(SEQUENCE_NUMBER.sub("", text)) not in lexicon or DEBUG_STATE.search(text):
        return False
    return not NOT_CONFIG.search(FREE_TEXT.sub("", re.sub(r'"[^"]*"', '""', text)))


def unprompted_config(doc: Doc, lexicon: set[str], taken: set[int], rules: frozenset) -> list[Raw]:
    """rules v3 (unprompted-config): configuration in a CLI block without any prompt (a config file, "show
    running-config" output in a block of its own, a pasted switch configuration), one example per line. A block
    counts only when every line reads as configuration (config_line: it starts with an IOS command word, `lexicon`),
    so show output, logs, prose, Tcl and shell sessions stay out; "!" comments and "<output omitted>" lines are
    skipped, and a block that fenced_syntax() read as syntax (its first line in `taken`) is left to it. A numbered
    entry ("10 class always ...") belongs to the block but is no row, as with a prompt (parse_prompt_host); build()
    drops a line written as syntax ("[optional]")."""
    out: list[Raw] = []
    for start, end in doc.fences:
        if start + 1 in taken:
            continue
        body = [(k, doc.lines[k - 1]) for k in range(start + 1, end)]
        if any(prompt_match(text, False, rules) for _, text in body):
            continue
        lines = [(k, UNPROMPTED_COMMENT.sub("", COMMENT.sub("", text)).strip()) for k, text in body
                 if not UNPROMPTED_SKIP.match(text)]
        lines = [(k, text) for k, text in lines if text]
        if lines and all(config_line(text, lexicon) for _, text in lines):
            out += [Raw(text, None, k, "example", normalized=False, unprompted=True) for k, text in lines
                    if not SEQUENCE_NUMBER.match(text)]
    return out


EXEC_VERBS = {"show", "debug", "undebug", "clear", "copy", "ping", "traceroute"}


def fill_task_modes(tasks: list[Raw], model: ModeModel):
    """Steps without an example take the mode the previous step of the same procedure enters. An EXEC step
    (show, debug, clear, copy...) is privileged EXEC and leaves the procedure's mode as it was."""
    prev = None
    for r in sorted(tasks, key=lambda r: (r.group, r.line)):
        if r.text.split()[0] in EXEC_VERBS:
            if not (r.mode or "").endswith("exec"):
                r.mode = "privileged-exec"
            continue
        if r.mode is None and prev is not None and prev.group == r.group:
            r.mode = model.next_mode(prev.mode, prev.text)
        prev = r


# --- release resolution -------------------------------------------------------------------------------------
# SD-WAN docs mix vEdge (Viptela OS) and cEdge commands, and Catalyst Center and ISE are not IOS: a classic IOS /
# IOS XE command reference does not date their commands, and their releases do not date IOS XE commands (ISE has

# release: its commands are neither dated by IOS XE nor date it.
NOT_IOS_LINEAGE = {'sdwan', 'catalyst-center', 'ise'}
OTHER_LINEAGE = NOT_IOS_LINEAGE - {"ise"}             # rules v3: docs where a host/user prompt is not ISE's

# ssh" after "line vty 0 1"): their commands are indexed and normalized with the Cisco templates and modes, but they
# are no evidence for Cisco rows, so they add no templates, mode transitions, feature tables or releases.
REQUIREMENT_PLATFORMS = set()


def entry_command_word(title: str) -> str:
    """rules v4 (lexicon-title): the command word a command-reference heading names ("pool (isakmp-group)" ->
    "pool", "default interface" -> "interface")."""
    return command_word(re.sub(r"\s*\([^()]*\)$", "", title).lower())


class ReleaseResolver:
    """The release that introduced a command, from the most specific evidence available (see resolve()). The
    book's own release (17.18 for c9300-1718-*) is never used: it only bounds the release from above, and a
    release_max filter would drop or misdate old commands such as "router lisp" with it."""

    def __init__(self):
        self.tables: dict[str, list[build_features.Table]] = {}
        self.line_table: dict[str, list[int]] = {}
        self.cr: dict[str, dict[str, str]] = defaultdict(dict)     # platform family -> skeleton -> release
        self.cr_any: dict[str, tuple[tuple, str]] = {}             # skeleton -> (rank, release): earliest, any CR

    @staticmethod
    def family(platform: str) -> str:
        return "cat9k" if platform.startswith("cat9") else platform

    def add_doc(self, doc: Doc):
        tables = build_features.parse_doc(doc)
        self.tables[doc.key] = tables
        paint = [-1] * (len(doc.lines) + 2)
        for ti in sorted(range(len(tables)), key=lambda t: -(tables[t].scope[1] - tables[t].scope[0])):
            a, b = tables[ti].scope
            paint[a:b + 1] = [ti] * (b - a + 1)                  # smaller scopes painted last win
        self.line_table[doc.key] = paint

    def add_cr(self, platform: str, command: str, release: str, train: str):
        key = " ".join(skeleton(command))
        if key and not key.startswith(("no ", "default ")):
            self.cr[self.family(platform)].setdefault(key, release)
            rank = (TRAIN_PREFERENCE.index(train), tuple(int(n) for n in re.findall(r"\d+", release)[:3]))
            if platform not in NOT_IOS_LINEAGE and (key not in self.cr_any or rank < self.cr_any[key][0]):
                self.cr_any[key] = (rank, release)

    @staticmethod
    def lookup(table: dict, sk: list[str]):
        """The entry for the longest leading run of the skeleton (at least two keywords unless it has one)."""
        for k in range(len(sk), 0, -1):
            if (k >= 2 or len(sk) == 1) and " ".join(sk[:k]) in table:
                return table[" ".join(sk[:k])]
        return None

    def resolve(self, doc_key: str, platform: str, line: int, command: str) -> tuple[str | None, str]:
        """In order: the same-family command reference, a feature row of the enclosing chapter that names the
        command, the earliest release in any family's command reference, the chapter's only feature release;
        else None (unknown, which a release_max filter lets through)."""
        sk = skeleton(command)
        if sk[:1] in (["no"], ["default"]):
            sk = sk[1:]
        rel = self.lookup(self.cr.get(self.family(platform), {}), sk)
        if rel:
            return rel, "command-reference"
        chapter = None
        paint = self.line_table.get(doc_key)
        ti = paint[line] if paint and line < len(paint) else -1
        if ti >= 0:
            rows = self.tables[doc_key][ti].rows
            best = None
            for row in rows:
                hay = f" {row.feature} {row.info} ".lower()
                for k in range(len(sk), 1, -1):
                    if f" {' '.join(sk[:k])}" in hay:
                        rel = preferred_release(row.releases)
                        cand = (k, tuple(-x for x in rel.key), rel.version)
                        best = max(best, cand) if best else cand
                        break
            if best:
                return best[2], "feature-row"
            versions = {preferred_release(r.releases).version for r in rows}
            if len(versions) == 1:
                chapter = versions.pop()
        other = self.lookup(self.cr_any, sk) if platform not in NOT_IOS_LINEAGE else None
        if other:
            return other[1], "command-reference-other-platform"
        if chapter:
            return chapter, "feature-chapter"
        return None, "none"


def refine_chapter_releases(records: list[dict], sources: list[str], evidence: list[bool] | None = None):
    """The chapter-release fallback dates every command of a chapter, also old ones ("ipv6 address" in a 17.13
    chapter). When the same command is dated earlier by other evidence elsewhere (IOS lineage), that release
    is kept instead. `evidence` (rules v3): records[i] may date others only when evidence[i] (default: all)."""
    def command_key(command: str) -> str:
        sk = skeleton(command)
        return " ".join(sk[1:] if sk[:1] in (["no"], ["default"]) else sk)

    def version(release: str) -> tuple:
        return tuple(int(n) for n in re.findall(r"\d+", release)[:3])   # every train before 17.x sorts lower

    earliest: dict[str, str] = {}
    for i, (rec, src) in enumerate(zip(records, sources)):
        if evidence is not None and not evidence[i]:
            continue
        if rec["release"] and src != "feature-chapter" and rec["platform"] not in NOT_IOS_LINEAGE:
            k = command_key(rec["command"])
            if k not in earliest or version(rec["release"]) < version(earliest[k]):
                earliest[k] = rec["release"]
    for i, (rec, src) in enumerate(zip(records, sources)):
        other = earliest.get(command_key(rec["command"])) if src == "feature-chapter" else None
        if other and rec["platform"] not in NOT_IOS_LINEAGE and version(other) < version(rec["release"]):
            rec["release"], sources[i] = other, "earlier-elsewhere"


# --- build --------------------------------------------------------------------------------------------------
CONVENTIONS = {
    "mode": "a prompt mode ('config-router', 'config', 'privileged-exec', 'user-exec', ...) or null; a syntax "
            "record may list several modes joined by mode_separator ('user-exec|privileged-exec')",
    "mode_separator": "|",
    "platforms": PLATFORMS,
    "generic_platforms": GENERIC_PLATFORMS,
    "generic_platforms_note": "platform-independent IOS / IOS XE guidance: a query for a Catalyst or router "
                              "platform should usually accept these too",
    "release": "the release that introduced the command (Command History, command references, feature tables) "
               "or null when unknown, which a release_max filter should let through; trains differ (IOS XE "
               "16.x/17.x and 2.x/3.xS, classic IOS 12.2(33)SRA, SD-WAN 20.x): compare numeric prefixes, every "
               "train before 17.x is older than 17.x; book_release is only an upper bound",
    "command": "<placeholder> arguments, [optional] groups, {a | b} choices; 'line' is 1-based in "
               "<doc_key>.txt of the Cisco corpus directory",
    "requirement_platforms": sorted(REQUIREMENT_PLATFORMS),
    "requirement_platforms_note": "No requirement platforms are supported in this public build",
}
CONVENTIONS_V3 = {        # added to CONVENTIONS by a rules v3 build (row-platform, unprompted-config)
    "platforms": {**PLATFORMS, "ise": "the Cisco ISE application CLI: the ISE books (ise31-*) and every row typed at "
                                      "an ISE prompt (host/user#), also in TechNotes (rules v3)"},
    "platform_from_prompt": "rules v3: a row typed at a prompt takes its platform from that prompt: the ISE "
                            "application CLI prompt (host/user#) gives 'ise' in an IOS-family or ISE doc (not in "
                            "SD-WAN or Catalyst Center texts); in an ISE book a router or switch prompt gives "
                            "'ios' (the network device's CLI, not ISE's), and so do, in an ISE book that is no command "
                            "reference, a configuration line without a prompt and a step that no ISE CLI syntax entry "
                            "fits",
    "unprompted_examples": "rules v3: 'example' rows also come from configuration blocks without prompts (every line "
                           "starts with an IOS command word and none reads as show output or prose); their mode is "
                           "that of the syntax or task template they fit when its rows agree on one (possibly several "
                           "modes joined by mode_separator, as on that template), else null",
}
CONVENTIONS_V4 = {        # added by a build with any rules v4 guard (same record format as v3)
    "rules_v4": "rules v4: a row of one document is no longer decided by a single other document's evidence where "
                "the v4 guards apply (a lone document's bold typography, an address argument taking a keyword, an "
                "output pipe taken as a value, a number only task steps set as a keyword, a single other document's "
                "task-step mode against a command reference of the same family, a command word from a syntax line "
                "its entry's heading does not name)",
}


def resolve_rules(rules: str | Iterable[str]) -> tuple[str, frozenset]:
    """(set name, rule names): a RULE_SETS name ("v2", "v3"), or rule names for a custom set (tests, measurements)."""
    if isinstance(rules, str):
        if rules not in RULE_SETS:
            raise ValueError(f"unknown rule set {rules!r} (known: {', '.join(RULE_SETS)})")
        return rules, RULE_SETS[rules]
    names = frozenset(rules)
    if names - ALL_RULES:
        raise ValueError(f"unknown rules: {', '.join(sorted(names - ALL_RULES))}")
    return "custom", names


def build(src_dirs: list[Path], styles_dir: Path | None, manifests: list[Path], out_dir: Path,
          exclude: Iterable[str] = (), evidence_only: Iterable[str] = (), rules: str | Iterable[str] = "v2",
          sources_out: Path | None = None) -> dict:
    """Build cli_index.json and its meta into out_dir; returns the meta's counts.
    exclude: doc_keys left out wherever they are (no rows, no evidence). evidence_only: doc_keys that are read and
    used as evidence exactly as before (syntax and task templates, mode transitions, command-reference releases,
    refine_chapter_releases) but write no rows and no sources: a retired book whose edition the corpus no longer
    serves, kept so that no row of the other books changes. rules: "v2" (default) or "v3" (RULE_SETS)."""
    rule_set, rules = resolve_rules(rules)
    manifest = load_manifest(manifests)
    exclude = sorted(set(exclude))
    found = discover(src_dirs, exclude)                 # CorpusError (bad exclude, unknown platform) before parsing
    evidence_only = sorted(set(evidence_only))
    if set(evidence_only) - set(found):
        raise CorpusError("--evidence-only names doc_keys that no source dir has (or that --exclude leaves out): "
                          + ", ".join(sorted(set(evidence_only) - set(found))))
    platforms = doc_platforms(found, manifest)
    styles = Styles(styles_dir, rules)
    templates = TemplateIndex(styles, rules)
    ise_templates = TemplateIndex(styles, rules)        # rules v3: the ISE CLI reference's syntax entries only
    templates.platforms = ise_templates.platforms = platforms
    resolver = ReleaseResolver()
    model = ModeModel()
    docs = [read_doc(k, p) for k, p in found.items()]
    hidden = set(evidence_only)                         # evidence-only docs: their counters are kept apart
    stats, hidden_stats = Counter(), Counter()
    lexicon: set[str] = set()                           # rules v3: IOS command words (unprompted-config)
    for doc in docs:
        if platforms[doc.key] not in REQUIREMENT_PLATFORMS:
            model.learn(doc, rules)
    # pass 1: syntax and task commands (they are the templates for pass 2) and raw example commands
    raws: dict[str, list[Raw]] = {}
    entries: dict[str, list] = {}                       # rules v3 (cr-own-example): command-reference entries
    syntax_blocks: dict[str, set[int]] = {}             # rules v3 (unprompted-config): blocks fenced_syntax() read
    reference: dict[str, bool] = {}                     # rules v3 (row-platform): the doc has command-reference entries
    for doc in docs:
        st = hidden_stats if doc.key in hidden else stats

        def norm(text: str, key=doc.key, vetted: bool = False, st=st) -> str | None:
            cmd = normalize_syntax(text, key, styles, vetted)
            st["syntax_text_rejected" if cmd is None else "syntax_text_normalized"] += 1
            return cmd

        platform = platforms[doc.key]
        evidence = platform not in REQUIREMENT_PLATFORMS
        if evidence:
            resolver.add_doc(doc)
        cr = command_reference(doc, norm)
        summary, covered = summary_steps(doc, norm, 0, rules)
        tasks = summary + pipe_steps(doc, norm, 10 ** 6, rules) + table_steps(doc, norm, covered, 2 * 10 ** 6, rules) \
            + fenced_syntax(doc, norm, 3 * 10 ** 6, rules)
        fill_task_modes(tasks, model)
        for r in (cr + tasks if evidence else []):
            templates.add(r.text, r.mode, doc.key, r.kind == "syntax")
            if r.kind == "syntax" and r.release:
                resolver.add_cr(platform, r.text, r.release, r.train)
        for r in (cr if evidence else []):
            if platform == "ise":
                ise_templates.add(r.text, r.mode, doc.key, True)
            elif platform not in NOT_IOS_LINEAGE:
                if "lexicon-title" in rules and r.title and command_word(r.text) != entry_command_word(r.title):
                    st["lexicon_word_not_the_entry_heading"] += 1     # rules v4: "default interface-type ..."
                    continue
                lexicon.add(command_word(r.text))
        raws[doc.key] = cr + tasks + cli_blocks(doc, rules)
        reference[doc.key] = bool(cr)
        if "cr-own-example" in rules:
            entries[doc.key] = cr_entries(doc)
        syntax_blocks[doc.key] = {r.line for r in tasks if r.group >= 3 * 10 ** 6}
    if "unprompted-config" in rules:                    # the lexicon is complete only now
        for doc in docs:
            if platforms[doc.key] not in REQUIREMENT_PLATFORMS:
                raws[doc.key] += unprompted_config(doc, lexicon, syntax_blocks[doc.key], rules)
    # pass 2: normalize examples, date everything, drop navigation steps and repeats within a section
    records, sources, per_doc = [], [], Counter()        # sources[i]: where records[i]["release"] came from
    repeats = []                                        # sources_out sidecar: lines dropped as repeats in a section
    unprompted = []                                     # unprompted[i]: records[i] is an unprompted config line
    for doc in docs:
        platform = platforms[doc.key]
        st = hidden_stats if doc.key in hidden else stats
        seen = set()
        # an unprompted configuration line comes last: it never takes the place of a row the rules v2 index has
        for r in sorted(raws[doc.key], key=lambda r: (r.unprompted, r.line)):
            cmd = r.text
            if r.kind != "syntax" and NOISE.match(cmd) \
                    and not ("cr-own-example" in rules and in_own_entry(entries[doc.key], r.line, cmd)):
                st[f"navigation_{r.kind}_dropped"] += 1
                continue
            if not r.normalized:
                cmd, how = example_command(cmd, templates, doc.key, styles)
                if cmd is None:
                    st[f"example_{how}_dropped"] += 1
                    continue
                if not CLI_WORD.fullmatch(TOKEN.findall(cmd)[0]):
                    st["example_without_keyword_dropped"] += 1      # "Device(config)# gigabitethernet0/0/0"
                    continue
                if r.unprompted and how not in ("template_matched", "values_typed"):
                    st[f"unprompted_{how}_dropped"] += 1         # a configuration line, not syntax ([optional])
                    continue
                st[f"example_{'unprompted' if r.unprompted else how}"] += 1
            if "topic-names" in rules and TOPIC_NAME.match(cmd):
                st["topic_name_dropped"] += 1
                continue
            row_platform = platform
            if "row-platform" in rules:
                if r.prompt == "ise" and platform not in OTHER_LINEAGE:
                    row_platform = "ise"                 # (an SD-WAN "vManage/vSmart#" is not ISE)
                elif platform == "ise" and r.kind != "syntax" and (
                        r.prompt == "ios" or (r.prompt is None and not reference[doc.key]
                                              and (r.unprompted or not ise_templates.fits(cmd, doc.key)))):
                    row_platform = "ios"                 # the NAD's CLI in an ISE admin guide
            if r.unprompted:     # the mode of the template it fits (an ISE row: the ISE CLI's own), EXEC for show ...
                r.mode = "privileged-exec" if cmd.split()[0] in EXEC_VERBS else \
                    (ise_templates if row_platform == "ise" else templates).mode_of(r.text, doc.key)
            if r.release and platform not in REQUIREMENT_PLATFORMS:
                release, src = r.release, "command-history"
            else:
                release, src = resolver.resolve(doc.key, row_platform, r.line, cmd)
            h = doc.heading_before(r.line)
            key = (cmd, r.mode, r.kind, h[0] if h else 0)
            if key in seen:
                st["repeat_in_section_dropped"] += 1
                if sources_out and doc.key not in hidden:
                    repeats.append([doc.key, r.line, r.kind, cmd, r.mode])
                continue
            seen.add(key)
            if row_platform != platform:
                st[f"platform_{platform}_to_{row_platform}"] += 1
            records.append({"command": cmd, "mode": r.mode, "platform": row_platform, "doc_key": doc.key,
                            "line": r.line, "kind": r.kind, "release": release})
            sources.append(src)
            unprompted.append(r.unprompted)
            per_doc[doc.key] += 1
    # evidence-only rows take part, so no other row changes; unprompted config lines are dated but date no other row
    refine_chapter_releases(records, sources, [not u for u in unprompted] if any(unprompted) else None)
    written = [i for i, rec in enumerate(records) if rec["doc_key"] not in hidden]
    records, sources = [records[i] for i in written], [sources[i] for i in written]
    stats.update(f"release_from_{src}" for src in sources)
    if sources_out:      # diagnosis sidecar (kb-diff classify_cli_diff.py): where each row's release came from
        flags = [unprompted[i] for i in written]
        Path(sources_out).parent.mkdir(parents=True, exist_ok=True)
        Path(sources_out).write_text(json.dumps([[r["doc_key"], r["line"], r["kind"], r["command"], s, u]
                                                 for r, s, u in zip(records, sources, flags)]), encoding="utf-8")
        Path(f"{sources_out}.repeats.json").write_text(json.dumps(repeats), encoding="utf-8")   # [doc, line, kind,
        # command, mode] of the lines whose row repeated an earlier row of their section (kb-diff: folded rows)
    records.sort(key=lambda r: (r["doc_key"], r["line"], r["kind"], r["command"]))
    out_docs = [d for d in docs if d.key not in hidden]
    counts = {"records": len(records), "unique_commands": len({r["command"] for r in records}),
              "by_kind": dict(Counter(r["kind"] for r in records).most_common()),
              "by_platform": dict(Counter(r["platform"] for r in records).most_common()),
              "by_platform_kind": dict(sorted(Counter(f"{r['platform']}/{r['kind']}" for r in records).items())),
              "with_mode": sum(1 for r in records if r["mode"]),
              "with_release": sum(1 for r in records if r["release"]),
              "pipeline": dict(sorted(stats.items())),
              "docs_without_style_lexicon": sorted({d.key for d in out_docs} - set(styles.docs)),
              # the release each book documents: an upper bound for its commands, never stored as their release
              "book_release": {d.key: doc_release(d.key) for d in out_docs if doc_release(d.key)},
              "by_doc": dict(sorted((k, n) for k, n in per_doc.items() if k not in hidden)),
              **({"excluded_doc_keys": exclude} if exclude else {}),
              # read as evidence, no rows written: the text each was read from, and the rows it would have had
              **({"evidence_only": {d.key: {"path": f"{d.path.parent.name}/{d.path.name}", "sha256": d.sha256,
                                            "rows_not_written": per_doc[d.key]} for d in docs if d.key in hidden},
                  "evidence_only_pipeline": dict(sorted(hidden_stats.items()))} if hidden else {}),
              **({"rules": {"set": rule_set, "rules": sorted(rules)}} if rules else {}),
              "conventions": ({**CONVENTIONS, **CONVENTIONS_V3,
                               **(CONVENTIONS_V4 if rules & (RULE_SETS["v4"] - RULE_SETS["v3"]) else {})}
                              if rules else CONVENTIONS)}
    write_output("cli_index", records, [source_record(d, manifest) for d in out_docs], GENERATOR, counts, out_dir)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build cli_index.json from the Cisco docs.")
    ap.add_argument("--src", action="append", type=Path, help="corpus dir(s); later dirs override earlier ones")
    ap.add_argument("--styles", type=Path, default=DEFAULT_STYLES, help="document_styles.py output dir")
    ap.add_argument("--manifest", action="append", type=Path)
    ap.add_argument("--out", type=Path, default=KB_DIR)
    ap.add_argument("--exclude", action="append", default=[], metavar="DOC_KEY",
                    help="leave this doc_key out wherever it is (a retired book; repeatable)")
    ap.add_argument("--evidence-only", action="append", default=[], metavar="DOC_KEY",
                    help="read this doc_key as evidence (templates, modes, releases) but write no rows or sources "
                         "for it (a retired book whose replacement is indexed instead; repeatable)")
    ap.add_argument("--rules", choices=sorted(RULE_SETS), default="v2",
                    help="parser rules: v2 (default; kb-v1..kb-v5), v3 (corpus-v6: row platform from the prompt, "
                         "digit-led hosts, spaced modes, pxGrid topic names, own-entry examples, unprompted config) or "
                         "v4 (measured for corpus-v7.1, not adopted: v3 plus narrow guards against one document's "
                         "evidence rewriting another document's rows; cisco-add6/kb-diff/RULES-V4.md)")
    ap.add_argument("--sources-out", type=Path, metavar="FILE",
                    help="also write where each row's release came from, [[doc_key, line, kind, command, source, "
                         "unprompted], ...] (a diagnosis sidecar for kb-diff/classify_cli_diff.py)")
    args = ap.parse_args(argv)
    try:
        counts = build(args.src or DEFAULT_SRC, args.styles, args.manifest or DEFAULT_MANIFESTS, args.out,
                       args.exclude, args.evidence_only, args.rules, args.sources_out)
    except CorpusError as e:
        sys.exit(f"build_cli: {e}")
    print(f"cli_index: {counts['records']} records, {counts['unique_commands']} unique commands; "
          f"by kind {counts['by_kind']}; by platform {counts['by_platform']}")
    if counts["docs_without_style_lexicon"]:
        print(f"no style lexicon (heuristic placeholders) for: {', '.join(counts['docs_without_style_lexicon'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
