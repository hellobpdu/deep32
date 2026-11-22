"""Check a finished Deep32 answer against the tool calls that produced it. A script; no model.

What it does: reads the final answer text and the turn's tool events (tool name, arguments, result text) and
reports, per check, counts and findings: quotations against their verify_quote results, RFC numbers against
lookup_rfc calls, "not covered" statements against get_outline calls, degraded search against the answer's first
lines, the number of calls per tool, numbers, releases and commands that occur in no tool result, and sentences of
the cited sections that carry a specific and no citation.

What it never does: it never edits, deletes or blocks an answer, and it calls no model. It flags. The reason is
arithmetic: in the graded answers about 2% of the claims were unsupported, and at a base rate that low most flags
of any checker are false alarms. So every flag is worded "to check": the script could not confirm the item, which
does not mean the item is wrong.

It has no side effects (check(), footer(), redact(), index() and summarize() touch no file, socket or process),
uses the standard library only, imports nothing from the relay package (so this file also runs as a plain script
under python -I), and never raises to its caller: check() returns {"version": 1, "status": "failed", ...} when
something inside it breaks, and footer() then returns "".

The tool events are one object per call, {"tool", "arguments", "result", "is_error"}: the tool's bare name, the
arguments the agent sent, the text of the result it read, and whether the call failed.

The result object (check()): {"version": 1, "status": "ok" | "no-events" | "failed", "to_check": the number of
flags, "index": {"generation", "corpus_fingerprint"} or None, "checks": {name: its counts, "status", "flagged"},
"findings": [{"check", "kind", "line", "excerpt", ...}]}. A finding's line is the 1-based line of the answer where
the flagged text starts, and its excerpt is that text. footer() turns the result into the text shown under an answer.

Command line, from the root of the clone (it reads the two files and prints the footer, or the result with --json):
    python -I -B adapters/relay/deep32_relay/answer_check.py --events FILE --answer FILE [--question FILE] [--json]
"""
import json
import re
import sys
import time

VERSION = 1
TOOLS = ("rag_status", "search_rag", "search_many", "get_section", "get_outline", "lookup_id", "lookup_rfc",
         "find_command", "grep_corpus", "list_documents", "verify_quote")
CHECKS = ("quotations", "rfcs", "gaps", "degraded", "counts", "specifics", "citations")
MAX_FINDINGS = 20       # findings kept per check in the result
MAX_SHOWN = 3           # findings shown per check in the footer
MAX_EXCERPT = 160       # characters of answer text kept per finding
MAX_TOKENS = 400        # distinct specifics examined per answer
CITED_SECTIONS = ("answer", "evidence", "rfc-vs-cisco")

# --- the engine's quote comparison form (src/engine/src/rag_core/reader.py: QUOTE_CHARS, ELLIPSIS, CANON_DROP) ---
QUOTE_CHARS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ",
                             "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-"})
ELLIPSIS = re.compile(r"\s*(?:\[\.\.\.\]|\.\.\.|\u2026)\s*")
CANON_DROP = re.compile(r"[\s-]+")
EDGE = ".,;:!?'\"()[]"


def canonical(text):
    return CANON_DROP.sub("", text.translate(QUOTE_CHARS))


def quote_key(text):
    """The parts of a quotation between its omission marks, each in the engine's comparison form."""
    parts = [canonical(part).strip(EDGE) for part in ELLIPSIS.split(text.translate(QUOTE_CHARS))]
    return tuple(part for part in parts if part)


# --- the answer's grammar (citation-style.md, expanded-answer.md, outline-check.md) ---
QUOTED = re.compile(r'"([^"\n]{1,2000})"|\u201c([^\u201d\n]{1,2000})\u201d')
SEPARATOR = r"[\u00b7\u2022|,:/\u2013\u2014-]"
MARKER = re.compile(r"\s*[.,;:]?\s*\(\s*[*_]{0,2}(verified|normalized|unverified)[*_]{0,2}"
                    r"(?:\s*" + SEPARATOR + r"\s*([^()\n]{0,200}))?\)", re.I)
ANY_MARKER = re.compile(r"\(\s*[*_]{0,2}(?:verified|normalized|unverified)[*_]{0,2}\s*"
                        r"(?:" + SEPARATOR + r"[^()\n]{0,200})?\)", re.I)
LOCATOR = re.compile(r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z]{1,5}):(\d{1,7})(?:\s*[-\u2013]\s*(\d{1,7}))?")
BRACKET_CITE = re.compile(r"\[[^\[\]\n]*(?:\u00a7|\u203a|RFC\s*\d|App\.|IANA)[^\[\]\n]*\]")
FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]{1,40})\](?!:)")
FOOTNOTE_DEF = re.compile(r"^\s*\[\^([^\]\s]{1,40})\]:\s*(.*)$")
SECTION_ID = re.compile(r"(?<![\w/.-])[a-z0-9][a-z0-9._-]*#[A-Za-z0-9][A-Za-z0-9._-]*")
INLINE_CODE = re.compile(r"`([^`\n]{1,200})`")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
BOLD_LABEL = re.compile(r"^\s*(?:#{1,6}\s*)?\*\*([^*\n]{2,50}?)\*\*")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
SECTIONS = (("answer", "answer"), ("how it works", "how"), ("configuration and verification", "config"),
            ("worked example", "example"), ("in production", "production"), ("edge cases", "edge"),
            ("evidence", "evidence"), ("analysis", "analysis"), ("rfc vs cisco", "rfc-vs-cisco"),
            ("rfc vs. cisco", "rfc-vs-cisco"), ("further reading", "further"), ("confidence and gaps", "gaps"),
            ("confidence", "gaps"), ("gaps", "gaps"), ("read", "read"))
LABELLED = re.compile(r"\b(?:unsourced|inference|inferred)\b|not evidence", re.I)
UNSOURCED_END = re.compile(r"\(\s*unsourced\s*\)\s*[.*_]*\s*$", re.I)
# a sentence ends at . ! or ? (and any closing quote or bracket after it) when white space and a capital letter,
# a digit, an opening quote, bracket, parenthesis, asterisk or backtick follow; group 1 is that white space
SENTENCE_END = re.compile(r"[.!?][\"'\u201d)\]]*(\s+)(?=[A-Z0-9\"\u201c\[(*`])")
ABBREVIATION = re.compile(r"\b(e\.g|i\.e|vs|cf|etc|App|Sec|Fig|No|approx)\.")
LEADING_CITES = re.compile(r"^\s*((?:\[\^[^\]\s]{1,40}\]|" + BRACKET_CITE.pattern + r")\s*)+")
OWN_LINE = re.compile(r"^\s*(?:\*\*)?(?:RAG|Read)(?:\*\*)?\s*[.:]", re.I)
RAG_COUNT = re.compile(r"^[ \t]*(?:\*\*)?RAG(?:\*\*)?[ \t]*:[ \t]*(\d{1,4})\b[^\n]{0,30}?\bcalls?\b", re.I | re.M)
GAP = re.compile(r"not in (?:the )?corpus|\bnot covered (?:in|by) (?:the )?(?:corpus|documents|docs|sources|"
                 r"knowledge base)|\blab-only\b|\brelease drift \(only in\b|outline not checked|"
                 r"\b(?:docs|documents|corpus|sources) (?:do not|don't|does not|doesn't) "
                 r"(?:say|state|cover|mention|document)\b|\bI (?:could not|couldn't|did not|didn't) find\b|"
                 r"\bno (?:served )?document (?:states|covers|says)\b", re.I)
# outline-check.md's label `inference (from [cite] + [cite])` marks an open point only in Confidence and gaps;
# elsewhere the same words label the agent's own reasoning
GAP_LABEL = re.compile(r"\binference \(from\b", re.I)
OUTLINE_CHECKED = re.compile(r"outline checked:\s*([^)\n]{1,400})", re.I)
OUTLINE_NOT_CHECKED = re.compile(r"outline not checked", re.I)
# the upper-case word followed by a number: not a file name (rfc5004.txt) and not a section id (rfc5004#3)
RFC_NAMED = re.compile(r"\bRFCs?\s?(\d{3,5})(?![\w#]|\.txt)((?:\s*(?:,|and|&|/|or)\s*\d{3,5}(?![\w#]|\.txt))*)")

# --- specifics: commands, RFC sections, numbers with units, releases, ports, addresses, timers ---
UNITS = (("time-ms", r"ms|msec|msecs|milliseconds?"), ("time-s", r"s|sec|secs|seconds?"),
         ("time-min", r"min|mins|minutes?"), ("time-h", r"h|hr|hrs|hours?"), ("time-d", r"days?"),
         ("bytes", r"bytes?|octets?"), ("bits", r"bits?"), ("rate", r"[kmg]?bps|pps"),
         ("size", r"[kmg]b|[kmg]ib"), ("hops", r"hops?"), ("packets", r"packets?"), ("percent", r"percent|%"))
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
QUANTITY = re.compile(r"(?<![\w.,])(" + NUMBER + r")(?:\s|-)?(" + "|".join(u for _, u in UNITS) + r")(?![\w%])",
                      re.I)
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?)(?!\w|\.\d|/\d)")
PORT = re.compile(r"\b(?:(?:TCP|UDP|SCTP)(?:\s+ports?)?|ports?)\s+(\d{1,5})\b", re.I)
RELEASE = re.compile(r"\b(?:IOS[ -]XE|IOS|NX-OS|SD-WAN|ISE|Catalyst Center|DNA Center|release|version|"
                     r"Cupertino|Dublin|Bengaluru|Amsterdam|Gibraltar|Fuji|Everest)\s+(?:Release\s+)?"
                     r"(\d{1,2}\.(?:\d{1,2}|x)(?:\.(?:\d{1,3}[a-z]?|x))?)(?![\w])", re.I)
RFC_SECTION = re.compile(r"\bRFC\s?(\d{3,5})\s*(?:,\s*)?(?:\u00a7+\s*|[Ss]ection\s+|[Ss]ec\.\s*|App(?:endix)?\.?\s+)"
                         r"([A-Z]?\d*(?:\.\d+)*)", re.I)
TIMER = re.compile(r"\b(?:timers?|intervals?|hold ?time|holdtime|keepalives?|hello|dead|delay|timeout|lifetime)\b"
                   r"[^.\n\d]{0,40}?(?<![\w.])(\d{1,6})(?!\w|\.\d)", re.I)
PLACEHOLDER = re.compile(r"[<>{}\[\]|\u2026]|\.\.\.")
# a backticked span that is no command: an arrow chain, or only digits and separators (an address, a number)
NOT_COMMAND = re.compile(r"\u2192|->|=>|\A[\d./:,\s-]+\Z")
PROMPT = re.compile(r"^(?:[A-Za-z0-9_.()-]{0,40}[#>]\s*)")


def _clip(text):
    """An excerpt: the answer's own words with white space squashed, cut (never marked) at MAX_EXCERPT characters,
    so that it stays a substring of the squashed answer text."""
    return " ".join(str(text).split())[:MAX_EXCERPT]


def _finding(check, kind, line, excerpt, **extra):
    value = {"check": check, "kind": kind, "line": int(line), "excerpt": _clip(excerpt)}
    value.update(extra)
    return value


def _events(events):
    """Keep well-formed events only: {"tool", "arguments", "result", "is_error"} with a known tool name."""
    result = []
    for event in events if isinstance(events, (list, tuple)) else ():
        if not isinstance(event, dict) or not isinstance(event.get("tool"), str):
            continue
        tool = event["tool"].rsplit("__", 1)[-1].rsplit(".", 1)[-1]
        if tool not in TOOLS:
            continue
        arguments = event.get("arguments")
        text = event.get("result")
        result.append({"tool": tool, "arguments": arguments if isinstance(arguments, dict) else {},
                       "result": text if isinstance(text, str) else "", "is_error": event.get("is_error") is True,
                       "truncated": event.get("truncated") is True})
    return result


class Answer:
    """The answer cut into lines, sections and units (paragraphs, bullets, code, tables), with quotations found."""

    def __init__(self, text):
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.text.split("\n")
        self.masked = []            # each line with its quotations and their markers blanked, same length
        self.footnotes = {}         # footnote id -> (line index, definition text)
        self.quotations = []        # dicts: text, label, locator, line (index)
        self.units = []             # dicts: section, kind (p, li, code, table, own), lines (indexes), text (masked)
        self._scan()

    def _scan(self):
        section, fence, block, kind = "answer", None, [], None

        def flush():
            nonlocal block, kind
            if block:
                self.units.append({"section": section, "kind": kind, "lines": block,
                                   "text": "\n".join(self.masked[i] for i in block)})
            block, kind = [], None

        for index, raw in enumerate(self.lines):
            fenced = FENCE.match(raw)
            if fence:
                self.masked.append(raw)
                if fenced and fenced.group(1)[0] == fence[0] and len(fenced.group(1)) >= len(fence):
                    fence = None
                    flush()
                else:
                    block.append(index)
                continue
            if fenced:
                flush()
                fence, kind = fenced.group(1), "code"
                self.masked.append(raw)
                continue
            definition = FOOTNOTE_DEF.match(raw)
            if definition:
                flush()
                self.footnotes[definition.group(1)] = (index, definition.group(2))
                self.masked.append(" " * len(raw))
                continue
            self.masked.append(self._quotes(raw, index))
            if not raw.strip():
                flush()
                continue
            name = self.heading(raw)
            if name:
                flush()
                section = name
            if OWN_LINE.match(raw):
                flush()
                self.units.append({"section": section, "kind": "own", "lines": [index], "text": self.masked[index]})
            elif raw.lstrip().startswith("|"):
                if kind != "table":
                    flush()
                kind = "table"
                block.append(index)
            elif BULLET.match(raw):
                flush()
                kind = "li"
                block.append(index)
            else:
                if kind in (None, "table"):
                    flush()
                    kind = "p"
                block.append(index)
        flush()

    @staticmethod
    def heading(raw):
        """The section a line opens: a known name, "other" for any other short bold label, or None."""
        match = BOLD_LABEL.match(raw) or HEADING.match(raw)
        if not match:
            return None
        label = match.group(1).strip().strip("*").strip().rstrip(".:").strip().lower()
        for name, key in SECTIONS:
            if label == name or label.startswith((name + " ", name + ".", name + ":", name + "/")):
                return key
        return "other" if BOLD_LABEL.match(raw) else None

    def _quotes(self, raw, index):
        """Record this line's quotations and return the line with them (and their markers) blanked."""
        probe = list(raw)
        for match in INLINE_CODE.finditer(raw):      # quotes inside inline code are not quotations
            for i in range(match.start() + 1, match.end() - 1):
                if probe[i] in '"\u201c\u201d':
                    probe[i] = " "
        probe = "".join(probe)
        marked = ANY_MARKER.search(probe) is not None
        out = list(raw)
        for match in QUOTED.finditer(probe):
            text = match.group(1) if match.group(1) is not None else match.group(2)
            marker = MARKER.match(probe, match.end())
            words = len(text.split())
            if marker is None and not marked and words < 4 and len(text) < 20:
                continue                    # a short quoted term on a line with no marker
            if marker is None and marked and words < 2 and len(text) < 12:
                continue                    # one quoted word beside another quotation's marker
            locator = LOCATOR.search(marker.group(2) or "") if marker else None
            self.quotations.append({"text": text, "label": marker.group(1).lower() if marker else None,
                                    "locator": locator.group(0) if locator else None, "line": index})
            for i in range(match.start(), marker.end() if marker else match.end()):
                out[i] = " "
        return "".join(out)

    def cited(self, text):
        """True when the text carries a citation marker."""
        if BRACKET_CITE.search(text) or LOCATOR.search(text) or ANY_MARKER.search(text) or SECTION_ID.search(text):
            return True
        return any(ref in self.footnotes for ref in FOOTNOTE_REF.findall(text))

    def line_of(self, unit, text, offset):
        """The 1-based answer line of a position in a unit's text (the unit's lines joined by line feeds)."""
        return unit["lines"][min(text.count("\n", 0, max(0, offset)), len(unit["lines"]) - 1)] + 1


def _sentences(text):
    """(sentence, offset) pairs. A run of citation markers that opens a sentence belongs to the one before."""
    guarded = ABBREVIATION.sub(lambda m: m.group(0).replace(".", "\x00"), text)
    spans, start = [], 0
    for match in SENTENCE_END.finditer(guarded):
        if ANY_MARKER.match(guarded, match.end()):
            continue                        # "quotation." (verified - file:line) stays one sentence
        spans.append((start, match.start(1)))
        start = match.end()
    spans.append((start, len(guarded)))
    result = []
    for a, b in spans:
        part = text[a:b]
        if not part.strip():
            continue
        lead = LEADING_CITES.match(part)
        if lead and result:
            result[-1] = (result[-1][0] + " " + lead.group(0).strip(), result[-1][1])
            a, part = a + lead.end(), part[lead.end():]
            if not part.strip():
                continue
        result.append((part, a))
    return result


# ------------------------------------------------------------------------------------------- tool results

VERIFY_RESULT = re.compile(r'<verify\b[^>]*\bresult="([a-z_]+)"')
VERIFY_LOCAL = re.compile(r'<verify\b[^>]*\blocal="([^"]*)"')
VERIFY_ITEM = re.compile(r"<verify n=(\d{1,3}) ([^>]*)>")       # one line of a quotes=[...] reply
VERIFY_ITEM_RESULT = re.compile(r'(?:^| )result="([a-z_]+)"')
ATTR_SECTION = re.compile(r'\bsection="([^"]+)"')
ATTR_ID = re.compile(r'\bid="([^"]+)"')
HIT = re.compile(r"<hit\b([^>]*)>")
DEGRADED_FIRST = re.compile(r"\Adegraded:[ \t]*([^\n]*)")
STATUS_BACKEND = re.compile(r"^([a-z][a-z0-9_-]*): (ok|off|DOWN)\b", re.M)
STATUS_SKIP = ("ok", "degraded", "edge", "opt_in_doc_types")
OBSOLETED = re.compile(r"OBSOLETED by ((?:RFC \d+(?:, )?)+)")


def _json_result(text):
    """The object of a result asked for with format="json" (after a degraded: first line, if any), else None."""
    text = text.lstrip()
    if text.startswith("degraded:"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.startswith("{"):
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except ValueError:
            return None
    return None


def _verify_result(event):
    if event["is_error"]:
        return "error"
    value = _json_result(event["result"])
    if value is not None:
        result = value.get("result")
    else:
        match = VERIFY_RESULT.search(event["result"])
        result = match.group(1) if match else None
    return result if result in ("exact", "normalized", "not_found") else "error"


def _verified(event):
    """Each quotation one verify_quote call checked, as (quote text, result). A call with ref and quote gives one.
    A call with quotes=[{ref, quote}, ...] (a gateway after 0.1.0-alpha.2 takes a list) gives one per item, read
    from the reply's line <verify n=K ... result="..."/> or from results[K-1] of the JSON form; an item whose line
    is missing or carries error="..." counts as a failed check."""
    arguments = event["arguments"]
    quotes = arguments.get("quotes")
    if not isinstance(quotes, list):
        quote = arguments.get("quote")
        return [(quote, _verify_result(event))] if isinstance(quote, str) else []
    results = {}
    if not event["is_error"]:
        value = _json_result(event["result"])
        if value is not None:
            listed = value.get("results")
            for position, item in enumerate(listed if isinstance(listed, list) else (), 1):
                if isinstance(item, dict):
                    results[item["n"] if type(item.get("n")) is int else position] = item.get("result")
        else:
            for number, attrs in VERIFY_ITEM.findall(event["result"]):
                found = VERIFY_ITEM_RESULT.search(attrs)
                results[int(number)] = found.group(1) if found else None
    out = []
    for position, item in enumerate(quotes, 1):
        quote = item.get("quote") if isinstance(item, dict) else None
        if isinstance(quote, str):
            result = results.get(position)
            out.append((quote, result if result in ("exact", "normalized", "not_found") else "error"))
    return out


def _rfc_number(value):
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits and len(digits) <= 5 else None


def _degraded(event):
    """The backends a result reports as below full strength."""
    found = []
    text = event["result"]
    first = DEGRADED_FIRST.match(text)
    if first and first.group(1).strip().lower() not in ("", "none"):
        found += [part.strip() for part in first.group(1).split(";") if part.strip()]
    if event["tool"] == "rag_status" and not event["is_error"]:
        value = _json_result(text)
        if value is not None:
            found += [str(item) for item in value.get("degraded") or []]
            backends = value.get("backends")
            for name, state in (backends.items() if isinstance(backends, dict) else ()):
                if (state.get("ok") if isinstance(state, dict) else state) is not True:
                    found.append(str(name))
        else:
            for line in text.split("\n"):
                if line.startswith("degraded:") and line[9:].strip().lower() not in ("", "none"):
                    found += [part.strip() for part in line[9:].split(";") if part.strip()]
            found += [name for name, state in STATUS_BACKEND.findall(text)
                      if name not in STATUS_SKIP and state != "ok"]
    return list(dict.fromkeys(found))


def index(events):
    """The index generation and corpus fingerprint as rag_status printed them in this turn, or None when the turn
    did not call it: {"generation": ..., "corpus_fingerprint": ...}."""
    for event in _events(events):
        if event["tool"] != "rag_status" or event["is_error"]:
            continue
        value = _json_result(event["result"])
        if value is not None:
            generation, fingerprint = value.get("generation"), value.get("corpus_fingerprint")
        else:
            first = event["result"].split("\n", 1)[0]
            generation = re.search(r"\bgeneration: ([^;\n]+)", first)
            fingerprint = re.search(r"\bcorpus_fingerprint: ([^;\n]+)", first)
            generation = generation.group(1).strip() if generation else None
            fingerprint = fingerprint.group(1).strip() if fingerprint else None
        if generation or fingerprint:
            return {"generation": str(generation) if generation else None,
                    "corpus_fingerprint": str(fingerprint) if fingerprint else None}
    return None


def summarize(events):
    """One entry per tool call: the tool, the ids it returned and its outcome. Never passage or query text."""
    out = []
    for event in _events(events):
        tool, text, arguments = event["tool"], event["result"], event["arguments"]
        item = {"tool": tool, "ids": []}
        if event["is_error"]:
            item["is_error"] = True
        degraded = _degraded(event) if tool != "rag_status" else []
        if degraded:
            item["degraded"] = degraded
        if tool in ("search_rag", "search_many"):
            for attrs in HIT.findall(text):
                ident = ATTR_ID.search(attrs) or ATTR_SECTION.search(attrs)
                if ident:
                    item["ids"].append(ident.group(1))
        elif tool == "get_section":
            match = re.search(r"<section\b([^>]*)>", text)
            ident = ATTR_SECTION.search(match.group(1)) if match else None
            item["ids"] = [ident.group(1)] if ident else []
        elif tool == "get_outline":
            if isinstance(arguments.get("doc"), str):
                item["ids"] = [arguments["doc"][:200]]
        elif tool == "lookup_rfc":
            number = _rfc_number(arguments.get("rfc"))
            item["ids"] = ["rfc%d" % number] if number else []
        elif tool == "verify_quote" and isinstance(arguments.get("quotes"), list):
            # the list form: one result per quotation, and the section each was found in
            item["results"] = [result for _, result in _verified(event)]
            for _, attrs in VERIFY_ITEM.findall(text):
                ident = ATTR_SECTION.search(attrs)
                if ident:
                    item["ids"].append(ident.group(1))
        elif tool == "verify_quote":
            item["result"] = _verify_result(event)
            match = re.search(r"<verify\b([^>]*)>", text)
            ident = ATTR_SECTION.search(match.group(1)) if match else None
            item["ids"] = [ident.group(1)] if ident else []
            local = VERIFY_LOCAL.search(text)
            if local:
                item["local"] = local.group(1)
        out.append(item)
    return out


# ------------------------------------------------------------------------------------------------- checks
# Each check returns (summary, findings): the summary holds counts, the findings are the items to check.

def check_quotations(answer, events, question=""):
    verified = []
    for event in events:
        if event["tool"] == "verify_quote":
            verified += [(quote_key(quote), result) for quote, result in _verified(event)]
    rank = {"exact": 3, "normalized": 2, "not_found": 1, "error": 0}
    out, findings = {"total": 0, "exact": 0, "normalized": 0, "labelled_unverified": 0}, []
    asked = canonical(question or "")
    for quotation in answer.quotations:
        key = quote_key(quotation["text"])
        label, line = quotation["label"], quotation["line"] + 1
        if label is None and key and all(part in asked for part in key):
            continue                        # the user's own words, quoted back
        out["total"] += 1
        if label == "unverified":
            out["labelled_unverified"] += 1
            continue
        same = [result for call, result in verified if key and call == key]
        within = [result for call, result in verified
                  if len(key) == 1 and len(call) == 1 and len(key[0]) >= 12 and key[0] in call[0]
                  and result in ("exact", "normalized")]
        result = max(same, key=rank.get) if same else (max(within, key=rank.get) if within else None)
        if result in ("exact", "normalized"):
            out[result] += 1
            if label is None:
                findings.append(_finding("quotations", "no-label", line, quotation["text"], result=result))
            elif label == "verified" and result == "normalized" and same:
                findings.append(_finding("quotations", "label-overstates", line, quotation["text"], result=result))
        else:
            kind = {"not_found": "not-found", "error": "check-failed"}.get(result, "no-check")
            findings.append(_finding("quotations", kind, line, quotation["text"], label=label))
    return out, findings


def check_rfcs(answer, events):
    named = {}                              # RFC number -> (1-based line of its first mention, the mention as written)
    places = [(index, line) for index, line in enumerate(answer.masked)]
    places += [(index, text) for index, text in answer.footnotes.values()]
    for index, line in sorted(places):
        for match in RFC_NAMED.finditer(line):
            for number in [int(match.group(1))] + [int(n) for n in re.findall(r"\d{3,5}", match.group(2))]:
                named.setdefault(number, (index + 1, match.group(0)))
    looked, successors = set(), {}
    for event in events:
        number = _rfc_number(event["arguments"].get("rfc")) if event["tool"] == "lookup_rfc" else None
        if number is None or event["is_error"]:
            continue
        looked.add(number)
        value = _json_result(event["result"])
        if value is not None:
            later = [int(n) for n in value.get("obsoleted_by") or [] if str(n).isdigit()]
        else:
            match = OBSOLETED.search(event["result"])
            later = [int(n) for n in re.findall(r"\d+", match.group(1))] if match else []
        if later:
            successors[number] = later
    findings = []
    for number, (line, mention) in named.items():
        if number not in looked:
            findings.append(_finding("rfcs", "no-lookup", line, mention, rfc=number))
        elif number in successors and not any(later in named for later in successors[number]):
            findings.append(_finding("rfcs", "obsolete-without-successor", line, mention, rfc=number,
                                     successors=successors[number]))
    return {"total": len(named), "looked_up": sum(1 for number in named if number in looked)}, findings


def check_gaps(answer, events):
    """Every statement that the documents do not cover a point needs an outline read in this turn. The one label
    written without one, `not in corpus (outline not checked: budget)`, says so itself: it is counted, not flagged."""
    outlined = {str(event["arguments"].get("doc", "")).lower() for event in events
                if event["tool"] == "get_outline" and not event["is_error"]}
    out, findings = {"total": 0, "with_outline": 0, "budget": 0}, []
    for unit in answer.units:
        if unit["kind"] == "code":
            continue
        raw = "\n".join(answer.lines[i] for i in unit["lines"])     # same length as the masked text
        for sentence, offset in _sentences(unit["text"]):
            if not (GAP.search(sentence) or (unit["section"] == "gaps" and GAP_LABEL.search(sentence))):
                continue
            out["total"] += 1
            line = answer.line_of(unit, unit["text"], offset)
            shown = raw[offset:offset + len(sentence)]
            named = OUTLINE_CHECKED.search(sentence)
            if OUTLINE_NOT_CHECKED.search(sentence):
                out["budget"] += 1
            elif named:
                docs = [part.split(">")[0].strip().strip("`").lower() for part in named.group(1).split(";")]
                missing = [doc for doc in docs if doc and doc not in outlined]
                if missing:
                    findings.append(_finding("gaps", "outline-not-read", line, shown, docs=missing))
                else:
                    out["with_outline"] += 1
            elif outlined:
                out["with_outline"] += 1
            else:
                findings.append(_finding("gaps", "gap-without-outline", line, shown))
    return out, findings


def check_degraded(answer, events):
    backends = []
    for event in events:
        backends += _degraded(event)
    backends = list(dict.fromkeys(backends))
    status_seen = any(event["tool"] == "rag_status" and not event["is_error"] for event in events)
    head = [(index, line) for index, line in enumerate(answer.lines) if line.strip()][:5]
    stated = any(re.search(r"\bdegraded\b", line, re.I) for _, line in head)
    if not backends:
        return {"state": "none" if status_seen else "unknown", "backends": []}, []
    if stated:
        return {"state": "stated", "backends": backends}, []
    first = head[0] if head else (0, "")
    return ({"state": "not-stated", "backends": backends},
            [_finding("degraded", "degraded-not-stated", first[0] + 1, first[1], backends=backends)])


def check_counts(answer, events):
    by_tool = {}
    for event in events:
        by_tool[event["tool"]] = by_tool.get(event["tool"], 0) + 1
    findings = []
    said = RAG_COUNT.search(answer.text)
    if said and int(said.group(1)) != len(events):
        line = answer.text.count("\n", 0, said.start()) + 1
        findings.append(_finding("counts", "count-differs", line, said.group(0), said=int(said.group(1)),
                                 counted=len(events)))
    return {"total": len(events), "by_tool": by_tool}, findings


def _tokens(text):
    """Specifics in a piece of answer text: (class, surface text, test, start) with test(evidence) -> bool.
    The classes are tried in a fixed order and a later class skips text an earlier class already took."""
    found, taken = [], []

    def free(a, b):
        if any(a < d and c < b for c, d in taken):
            return False
        taken.append((a, b))
        return True

    for match in INLINE_CODE.finditer(text):
        literal = _literal(match.group(1))
        if literal and free(*match.span()):
            found.append(("command", match.group(1), lambda ev, v=literal: v in ev["squashed"], match.start()))
    for match in RFC_SECTION.finditer(text):
        number, section = match.group(1), match.group(2).lower().strip(".")
        if section and free(*match.span()):
            found.append(("rfc-section", match.group(0),
                          lambda ev, n=number, s=section: _rfc_section(n, s, ev), match.start()))
    for match in QUANTITY.finditer(text):
        if not free(*match.span()):
            continue
        number, unit = match.group(1).replace(",", ""), match.group(2).lower()
        family = next((u for _, u in UNITS if re.fullmatch(u, unit)), re.escape(unit))
        pattern = re.compile(r"(?<![\d.])" + re.escape(number) + r"(?:\s|-)?(?:" + family + r")(?![a-z%])")
        found.append(("quantity", match.group(0), lambda ev, p=pattern: p.search(ev["nocomma"]) is not None,
                      match.start()))
    for match in RELEASE.finditer(text):
        if not free(*match.span(1)):
            continue
        pattern = re.compile(r"(?<![\d.])" + re.escape(match.group(1).lower()) + r"(?!\d)")
        found.append(("release", match.group(0), lambda ev, p=pattern: p.search(ev["lower"]) is not None,
                      match.start()))
    for match in PORT.finditer(text):
        if free(*match.span(1)):
            number = match.group(1)
            found.append(("port", match.group(0), lambda ev, n=number: _standalone(n, ev), match.start()))
    for match in IPV4.finditer(text):
        value = match.group(1)
        if all(int(part) <= 255 for part in value.split("/")[0].split(".")) and free(*match.span(1)):
            found.append(("address", value, lambda ev, v=value: v in ev["lower"], match.start(1)))
    for match in TIMER.finditer(text):
        if free(*match.span(1)):
            number = match.group(1)
            found.append(("timer", match.group(0), lambda ev, n=number: _standalone(n, ev), match.start()))
    return sorted(found, key=lambda item: item[3])


def _standalone(number, evidence):
    return re.search(r"(?<![\d.])" + re.escape(number) + r"(?!\d)", evidence["nocomma"]) is not None


def _rfc_section(number, section, evidence):
    text = evidence["lower"]
    return ("rfc%s#%s" % (number, section) in text or "rfc%s#section-%s" % (number, section) in text
            or "rfc%s#appendix-%s" % (number, section) in text
            or re.search(r"rfc %s (?:\u00a7|app\. )%s(?![\w.]*\d)" % (number, re.escape(section)), text) is not None)


def _literal(code):
    """The checkable part of a backticked span or a code line: lower case, single spaces, no prompt, cut at the
    first placeholder. None when nothing checkable is left."""
    text = " ".join(code.split()).lower()
    if NOT_COMMAND.search(text) or text.startswith("!"):
        return None
    text = PROMPT.sub("", text)
    cut = PLACEHOLDER.search(text)
    if cut:
        text = text[:cut.start()].strip()
    if text in TOOLS or text.startswith("mcp__") or len(text) < 4:
        return None
    return text


def _forms(text):
    lower = text.translate(QUOTE_CHARS).lower()
    return {"lower": lower, "squashed": " ".join(lower.split()), "nocomma": re.sub(r"(?<=\d),(?=\d{3})", "", lower)}


def check_specifics(answer, events, question=""):
    if any(event["truncated"] for event in events):
        return {"status": "unavailable", "reason": "capture-limit"}, []
    evidence, asked = _forms("\n".join(event["result"] for event in events)), _forms(question or "")
    out, findings, seen = {"total": 0, "sourced": 0, "in_question": 0, "labelled": 0}, [], set()
    for unit in answer.units:
        if unit["kind"] == "own":
            continue                        # the answer's own RAG: and Read lines
        if unit["kind"] in ("code", "table"):
            pieces, position = [], 0        # one piece per line of a code block or a table
            for row in unit["text"].split("\n"):
                pieces.append((row, position))
                position += len(row) + 1
        else:
            pieces = _sentences(unit["text"])
        whole = unit["kind"] in ("p", "li") and UNSOURCED_END.search(unit["text"]) is not None
        missing = []                        # (line, text) of the lines of one code block found in no tool result
        for piece, offset in pieces:
            if unit["kind"] == "code":
                literal = _literal(piece)
                tokens = [("code", piece.strip(), lambda ev, v=literal: v in ev["squashed"], 0)] if literal else []
            else:
                tokens = _tokens(piece)
            labelled = whole or (unit["kind"] in ("p", "li") and LABELLED.search(piece) is not None)
            for kind, surface, test, start in tokens:
                key = (kind, " ".join(surface.lower().split()))
                if key in seen or len(seen) >= MAX_TOKENS:
                    continue
                seen.add(key)
                out["total"] += 1
                line = answer.line_of(unit, unit["text"], offset + start)
                if test(evidence):
                    out["sourced"] += 1
                elif test(asked):
                    out["in_question"] += 1
                elif labelled:
                    out["labelled"] += 1
                elif kind == "code":
                    missing.append((line, surface))
                else:
                    findings.append(_finding("specifics", kind, line, surface))
        if missing:                         # one finding per code block, however many of its lines are missing
            findings.append(_finding("specifics", "code", missing[0][0], missing[0][1], lines=len(missing)))
    return out, findings


def check_citations(answer):
    out, findings = {"sentences": 0, "own": 0, "later": 0}, []
    for unit in answer.units:
        if unit["section"] not in CITED_SECTIONS or unit["kind"] not in ("p", "li"):
            continue
        raw = "\n".join(answer.lines[i] for i in unit["lines"])
        blank = BULLET.match(raw)
        if blank:                           # blank the bullet mark and the section's own label, keeping offsets
            raw = " " * blank.end() + raw[blank.end():]
        label = BOLD_LABEL.match(raw) if answer.heading(raw.split("\n", 1)[0]) else None
        if label:
            raw = " " * label.end() + raw[label.end():]
        sentences = _sentences(raw)
        marks = [answer.cited(sentence) for sentence, _ in sentences]
        for index, (sentence, offset) in enumerate(sentences):
            plain = sentence                # the specifics of the sentence itself: no quotation, no citation
            for pattern in (QUOTED, ANY_MARKER, BRACKET_CITE, FOOTNOTE_REF, LOCATOR, SECTION_ID):
                plain = pattern.sub(" ", plain)
            if not _tokens(plain) or LABELLED.search(sentence):
                continue
            out["sentences"] += 1
            if marks[index]:
                out["own"] += 1
            elif any(marks[index + 1:]):
                out["later"] += 1
            else:
                start = offset + len(sentence) - len(sentence.lstrip())
                findings.append(_finding("citations", "no-citation", answer.line_of(unit, raw, start), sentence))
    return out, findings


def check(answer, events, *, question="", budget_seconds=2.0):
    """Run every check. Returns the result object; never raises."""
    try:
        started = time.monotonic()
        if not isinstance(answer, str):
            raise TypeError("answer")
        events = _events(events)
        asked = question if isinstance(question, str) else ""
        parsed = Answer(answer)
        result = {"version": VERSION, "status": "ok" if events else "no-events", "to_check": 0,
                  "index": index(events), "checks": {}, "findings": []}
        steps = {"quotations": lambda: check_quotations(parsed, events, asked),
                 "rfcs": lambda: check_rfcs(parsed, events),
                 "gaps": lambda: check_gaps(parsed, events),
                 "degraded": lambda: check_degraded(parsed, events),
                 "counts": lambda: check_counts(parsed, events),
                 "specifics": lambda: check_specifics(parsed, events, asked),
                 "citations": lambda: check_citations(parsed)}
        for name in CHECKS:
            if time.monotonic() - started > budget_seconds:
                result["checks"][name] = {"status": "unavailable", "reason": "time", "flagged": 0}
                continue
            try:
                summary, findings = steps[name]()
            except Exception as error:      # one check failing does not lose the others
                summary, findings = {"status": "unavailable", "reason": "error:" + type(error).__name__}, []
            summary.setdefault("status", "ok")
            summary["flagged"] = len(findings)
            result["checks"][name] = summary
            result["findings"] += findings[:MAX_FINDINGS]
            result["to_check"] += len(findings)
        return result
    except Exception as error:
        return {"version": VERSION, "status": "failed", "error": type(error).__name__}


def redact(result):
    """The result without any text taken from the answer: counts, and for each finding its check, kind and line."""
    try:
        value = json.loads(json.dumps(result))
        value["findings"] = [{"check": item.get("check"), "kind": item.get("kind"), "line": item.get("line")}
                             for item in value.get("findings") or []]
        return value
    except Exception:
        return {"version": VERSION, "status": "failed", "error": "redact"}


# ------------------------------------------------------------------------------------------------- footer

HEAD = "---\nChecks (by script, no model):"
WHY = {"no-check": "no verify_quote call found for it", "not-found": "verify_quote did not find it",
       "check-failed": "its verify_quote call failed", "no-label": "it carries no label",
       "label-overstates": "labeled verified; the check said normalized",
       "no-lookup": "no lookup_rfc call in this answer",
       "obsolete-without-successor": "obsoleted; the answer does not name its successor",
       "outline-not-read": "no get_outline call for the document it names",
       "gap-without-outline": "no get_outline call in this answer",
       "no-citation": "no citation in or after it"}
SKIPPED = {"time": "the script ran out of time",
           "capture-limit": "the tool results of this answer were too large to keep"}
QUOTED_KINDS = ("quotations", "gaps", "citations")


def _show(text):
    """An excerpt as the footer prints it: no backtick or asterisk, at most 100 characters."""
    text = " ".join(str(text).replace("`", "").replace("*", "").split())
    return text if len(text) <= 100 else text[:97] + "..."


def _flags(result, name):
    """The "to check" lines of one check: at most MAX_SHOWN findings, then how many more."""
    lines = []
    findings = [item for item in result.get("findings") or [] if item.get("check") == name]
    for finding in findings[:MAX_SHOWN]:
        if finding.get("kind") == "count-differs":
            text = "the answer says %s calls; the script counted %s" % (finding.get("said"), finding.get("counted"))
        elif name == "rfcs" and "rfc" in finding:
            text = "RFC %s" % finding["rfc"]
        else:
            text = _show(finding.get("excerpt", ""))
            text = '"%s"' % text if name in QUOTED_KINDS else text
        why = WHY.get(finding.get("kind"))
        lines.append("  - to check: " + text + (" (" + why + ")" if why else ""))
    flagged = result["checks"][name].get("flagged", 0)
    if flagged > MAX_SHOWN:
        lines.append("  - and %d more" % (flagged - MAX_SHOWN))
    return lines


def _skipped(title, item):
    return "- %s: not checked (%s)." % (title, SKIPPED.get(item.get("reason", ""), "the check did not run"))


def footer(result):
    """The plain-text footer for a result; "" when the check itself failed. Never raises."""
    try:
        if not isinstance(result, dict) or result.get("status") not in ("ok", "no-events"):
            return ""
        checks = result["checks"]
        q, r, g, d, c, s, t = (checks[name] for name in CHECKS)
        lines = [HEAD]
        if result["status"] == "no-events":
            stated = (q.get("total", 0), r.get("total", 0), s.get("total", 0))
            if not any(stated):
                return "\n".join(lines + [
                    "- No tool call was recorded for this answer.",
                    "- It contains no quotation, RFC number or other specific, so there was nothing to check."])
            return "\n".join(lines + [
                "- No tool call was recorded for this answer, so none of it was retrieved or checked in this turn.",
                "- In the answer: quotations %d, RFC numbers %d, other specifics (numbers, releases, commands) %d."
                % stated,
                "To check: the whole answer."])

        def tail(item):
            return " To check: %d." % item["flagged"] if item["flagged"] else ""

        if q["status"] != "ok":
            lines.append(_skipped("Quotations", q))
        else:
            line = "- Quotations: %d. Checked exact: %d. Checked normalized: %d." % (q["total"], q["exact"],
                                                                                    q["normalized"])
            if q["labelled_unverified"]:
                line += " Labeled unverified: %d." % q["labelled_unverified"]
            lines += [line + tail(q)] + _flags(result, "quotations")
        if r["status"] != "ok":
            lines.append(_skipped("RFCs", r))
        else:
            lines += ["- RFCs named: %d. Status looked up: %d." % (r["total"], r["looked_up"]) + tail(r)]
            lines += _flags(result, "rfcs")
        if g["status"] != "ok":
            lines.append(_skipped('"Not covered" statements', g))
        elif not g["total"]:
            lines.append('- "Not covered" statements: none.')
        else:
            line = '- "Not covered" statements: %d. With an outline read: %d.' % (g["total"], g["with_outline"])
            if g["budget"]:
                line += ' Labeled "outline not checked": %d.' % g["budget"]
            lines += [line + tail(g)] + _flags(result, "gaps")
        if d["status"] != "ok":
            lines.append(_skipped("Search", d))
        elif d["state"] == "none":
            lines.append("- Search: full strength (rag_status showed every backend ok).")
        elif d["state"] == "unknown":
            lines.append("- Search: no degraded reply seen; rag_status was not called in this answer, so a "
                         "backend that is switched off would not show.")
        elif d["state"] == "stated":
            lines.append("- Search: degraded (%s), and the answer says so." % "; ".join(d["backends"])[:120])
        else:
            lines.append("- Search: degraded (%s), and the answer's first lines do not say so. To check: 1."
                         % "; ".join(d["backends"])[:120])
        if c["status"] != "ok":
            lines.append(_skipped("Tool calls", c))
        else:
            order = sorted(c["by_tool"].items(), key=lambda item: (-item[1], item[0]))
            lines += ["- Tool calls: %d (%s)." % (c["total"], ", ".join("%s %d" % item for item in order)) + tail(c)]
            lines += _flags(result, "counts")
        if s["status"] != "ok":
            lines.append(_skipped("Numbers, releases and commands", s))
        else:
            line = ("- Numbers, releases and commands outside quotations: %d. Found in the retrieved text: %d."
                    % (s["total"], s["sourced"]))
            if s["in_question"]:
                line += " From your question: %d." % s["in_question"]
            if s["labelled"]:
                line += " In text the answer labels unsourced or inference: %d." % s["labelled"]
            lines += [line + tail(s)] + _flags(result, "specifics")
        if t["status"] != "ok":
            lines.append(_skipped("Citations", t))
        else:
            line = ("- Sentences with a specific in Answer, Evidence and RFC vs Cisco: %d. With their own "
                    "citation: %d." % (t["sentences"], t["own"]))
            if t["later"]:
                line += " Citation later in the paragraph: %d." % t["later"]
            lines += [line + tail(t)] + _flags(result, "citations")
        total = result.get("to_check", 0)
        last = ("To check: %d. A flag means the script could not confirm the item, not that it is wrong." % total
                if total else "To check: nothing.")
        missed = sum(1 for name in CHECKS if checks[name]["status"] != "ok")
        if missed:
            # A check that did not run confirms nothing: its silence must not read as a clean result.
            last = (last if total else "To check: nothing was flagged.") + " Not run: %d of the %d checks." % (
                missed, len(CHECKS))
        lines.append(last)
        return "\n".join(lines)
    except Exception:
        return ""


# ------------------------------------------------------------------------------------------- command line

def load_events(raw):
    """Events from JSON Lines text: one object per line; blank lines are skipped."""
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="answer_check.py",
                                     description="Check a finished Deep32 answer against its tool calls.")
    parser.add_argument("--events", required=True, help="the tool events, JSON Lines, UTF-8")
    parser.add_argument("--answer", required=True, help="the answer text, UTF-8")
    parser.add_argument("--question", help="the question text, UTF-8 (its own values are then not flagged)")
    parser.add_argument("--json", action="store_true", help="print the result object instead of the footer")
    args = parser.parse_args(argv)
    try:
        with open(args.answer, "rb") as stream:
            answer = stream.read().decode("utf-8-sig")
        with open(args.events, "rb") as stream:
            events = load_events(stream.read().decode("utf-8-sig"))
        question = ""
        if args.question:
            with open(args.question, "rb") as stream:
                question = stream.read().decode("utf-8-sig")
    except (OSError, ValueError) as error:
        sys.stderr.write("answer_check: cannot read the input (%s)\n" % type(error).__name__)
        return 2
    result = check(answer, events, question=question, budget_seconds=60.0)
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) if args.json else footer(result)
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0 if result.get("status") in ("ok", "no-events") else 1


if __name__ == "__main__":
    sys.exit(main())
