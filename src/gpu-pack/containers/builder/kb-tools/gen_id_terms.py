#!/usr/bin/env python3
"""Generate glossary identifier entries from a Cisco ISE message catalogue."""
from __future__ import annotations
import argparse
import hashlib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
ISE_CODE, ISE_SEVERITY = re.compile(r"\d{4,6}"), re.compile(r"[A-Za-z]+")
HEADING = re.compile(r"^(#{1,6}) ")


class FormatError(ValueError):
    """An input text does not have the layout this generator reads."""

@dataclass
class Term:
    term: str
    expansion: str
    aliases: list[str]
    refs: list[str]
    category: str

    def line(self) -> str:
        return " | ".join([self.term, self.expansion, "; ".join(self.aliases), "; ".join(self.refs), self.category])

def clean(text: str) -> str:
    """One glossary field: whitespace collapsed; "|" (the field separator) cannot occur, so it becomes "/"."""
    return re.sub(r"\s+", " ", text).strip().replace("|", "/")

def ise_message_terms(text: str, doc_key: str) -> tuple[list[Term], Counter]:
    """ise-message entries for the messages of a compact ISE syslog message catalog, and counts (messages, classes,
    with_description, merged: a code listed in a second class with the same text, variants: a code listed again with
    another severity or text)."""
    found: dict[str, list[tuple[str, str, str, list[str]]]] = {}   # code -> [(severity, text, description, classes)]
    counts, cls = Counter(), ""
    for i, ln in enumerate(text.split("\n"), 1):
        head = HEADING.match(ln)
        if head:
            cls = clean(ln[len(head.group(0)):]) if len(head.group(1)) >= 2 else cls
            continue
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) < 2 or not ISE_CODE.fullmatch(cells[0]) or not ISE_SEVERITY.fullmatch(cells[1]):
            continue                                         # the title, header rows, notes
        code, severity = cells[0], cells[1]
        message, desc = clean(cells[2] if len(cells) > 2 else ""), clean(" / ".join(c for c in cells[3:] if c))
        if not message:
            raise FormatError(f"{doc_key} line {i}: ISE message {code} has no text")
        desc = "" if desc.casefold() in ("", "-", message.casefold()) else desc
        variants = found.setdefault(code, [])
        same = next((v for v in variants if (v[0], v[1]) == (severity, message)), None)
        if same is None:
            if variants:
                counts["variants"] += 1                      # listed again, worded differently: a second variant
            variants.append((severity, message, desc, [cls] if cls else []))
        elif cls and cls not in same[3]:
            same[3].append(cls)
            counts["merged"] += 1
    if not found:
        raise FormatError(f"{doc_key}: no message lines ('22056 | NOTICE | <message text> | <description>')")
    terms = []
    for code, variants in found.items():
        expansion = " / ".join(f"{message} [{'; '.join([severity] + classes)}]" + (f" {desc}" if desc else "")
                               for severity, message, desc, classes in variants)
        terms.append(Term(f"ISE {code}", expansion, [code], [f"doc:{doc_key}"], "ise-message"))
        counts["with_description"] += any(desc for _, _, desc, _ in variants)
    counts["messages"] = len(terms)
    counts["classes"] = len({c for variants in found.values() for *_, classes in variants for c in classes})
    return terms, counts

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(ise_messages: Path | None = None) -> tuple[str, list[str]]:
    header = ["# Identifier entries for build_glossary.py --terms; generated from the source below."]
    report, terms = [], []
    if ise_messages is not None:
        data = ise_messages.read_bytes()
        terms, counts = ise_message_terms(data.decode("utf-8"), ise_messages.stem)
        header.append(f"# {ise_messages.name} sha256 {sha256(data)}: {len(terms)} ise-message entries")
        report.append(f"{ise_messages.name}: {len(terms)} entries; {counts['classes']} message classes")
    seen = Counter(t.term.casefold() for t in terms)
    duplicate = sorted({t.term for t in terms if seen[t.term.casefold()] > 1})
    if duplicate:
        raise FormatError(f"identifiers repeat: {', '.join(duplicate[:10])}")
    return "\n".join(header + [t.line() for t in terms]) + "\n", report

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ise-messages', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.ise_messages is None:
        parser.error('give --ise-messages')
    try:
        text, report = generate(args.ise_messages)
    except FormatError as exc:
        print(f'gen_id_terms: {exc}', file=sys.stderr)
        return 1
    for line in report:
        print(line, file=sys.stderr)
    if args.out is None:
        sys.stdout.write(text)
    else:
        part = args.out.with_name(args.out.name + '.part')
        part.write_text(text, encoding='utf-8', newline='\n')
        part.replace(args.out)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
