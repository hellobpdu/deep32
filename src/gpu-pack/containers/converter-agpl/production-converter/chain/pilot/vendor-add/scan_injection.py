#!/usr/bin/env python3
"""Scan corpus text for prompt-injection-like content before it is published.

usage: scan_injection.py <out.json> <file.txt> [<file.txt> ...]

The corpus is retrieved into an LLM's context, so text that addresses the model ('ignore previous
instructions', 'you are now ...', chat role tags) is flagged, as are invisible format characters (zero-width,
bidi overrides, Unicode tag characters) that can hide such text. A pattern hit means a person reviews that line
before the document is published; qa_text.py FAILs a document with one. Network documentation has many
"prompts" (device CLI prompts), "system messages" (syslog) and <user>/<system> XML elements (YANG, NACM), so
those are not patterns.
Writes {doc_key: {hits, examples, invisible, invisible_examples}} and exits 1 when any file has a pattern hit.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

PATTERNS = [
    r"\b(?:ignore|disregard|forget|override)\b.{0,30}\b(?:previous|prior|above|earlier|preceding|all|your|any)\b"
    r".{0,20}\b(?:instructions?|prompts?|directions|guidelines)\b",
    r"\byou are (?:now )?(?:an? |the )?(?:ai|a\.i\.|assistant|chatbot|language model|llm|chatgpt|claude|gpt)\b",
    r"\bas an? (?:ai|large language model|language model)\b",
    r"\b(?:new|updated|real|actual|hidden|secret) (?:system )?instructions?\s*:",
    r"\b(?:reveal|print|repeat|show|output) (?:your|the) (?:system prompt|instructions|hidden prompt)\b",
    r"\bdo not (?:tell|inform|reveal to|mention (?:this )?to) the user\b",
    r"</?(?:assistant|human|instructions?|tool_call|function_calls|antml:[a-z_]+)>",   # not <user>/<system>: YANG XML
    r"\[/?INST\]|<<\/?SYS>>|<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>",
    r"^\s*(?:Human|Assistant)\s*:\s",
]
INJECTION = re.compile("|".join(f"(?:{p})" for p in PATTERNS), re.I | re.M)
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def scan_text(text: str) -> dict:
    lines = text.split("\n")
    hits = [f"{i + 1}: {l.strip()[:160]}" for i, l in enumerate(lines) if INJECTION.search(l)]
    inv = [(i + 1, c) for i, l in enumerate(lines) for c in INVISIBLE.findall(l)]
    return {"hits": len(hits), "examples": hits[:10], "invisible": len(inv),
            "invisible_examples": [f"{n}: U+{ord(c):04X} {unicodedata.name(c, '?')}" for n, c in inv[:10]]}


def main() -> int:
    out, files = Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]
    report = {p.stem: scan_text(p.read_text(encoding="utf-8", errors="replace")) for p in files}
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    for key, r in report.items():
        if r["hits"] or r["invisible"]:
            print(f"{key}: {r['hits']} instruction-like lines, {r['invisible']} invisible chars", *r["examples"][:3],
                  *r["invisible_examples"][:3], sep="\n  ")
    print(f"scanned {len(report)} files: {sum(r['hits'] for r in report.values())} instruction-like lines in "
          f"{sum(bool(r['hits']) for r in report.values())} files, "
          f"{sum(r['invisible'] for r in report.values())} invisible chars")
    return 1 if any(r["hits"] for r in report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
