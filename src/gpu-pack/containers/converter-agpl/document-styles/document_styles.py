# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
#!/usr/bin/env python3
"""Per-document CLI style lexicons: which command tokens Cisco sets in italics (arguments) or bold (keywords).

The converted corpus text (pilot/cisco-md-v2|v3/<doc_key>.txt) no longer carries font styles, so a syntax line such
as "area area-id nssa translate type7 always" cannot tell the argument "area-id" from the keywords around it.
The originals can: Cisco PDFs set keywords in bold and arguments in italics, and Cisco ePubs wrap arguments in
<var> and keywords in <b>/<kbd>/<span class="keyword ...">. This script reads the originals and counts, for each
token and for each (previous token, token) pair, how often it appears in an argument style and in a keyword style.
build_cli.py uses the counts to turn arguments into <placeholders>; it falls back to heuristics without them.

Output: <out>/<doc_key>.json  {"doc_key", "source", "source_sha256", "version", "unigram": {tok: [n_arg, n_kw]},
        "bigram": {"prev tok": [n_arg, n_kw]}}
A document is skipped when its cached lexicon has the same source sha256 and version (re-runs are cheap).

Needs PyMuPDF for PDFs. Run with explicit corpus and originals paths, e.g.:
  python3 -B document_styles.py --txt-dir /var/lib/deep32-forge/corpus/cisco \
      --orig-dir /var/lib/deep32-forge/originals/cisco
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor_common import ORIGINAL_DIRS

VERSION = 2
DEFAULT_OUT = Path("/var/lib/deep32-forge/kb-work/styles")
EXTS = (".epub", ".pdf", ".html", ".htm")
TOKEN = re.compile(r"[^\s\[\]{}|,;]+")
CLI_TOKEN = re.compile(r"[A-Za-z0-9<][\w.:/<>=*+#@-]{0,47}")    # shape of a CLI keyword or argument name
MONO = re.compile(r"courier|mono|consol|menlo", re.I)
ARG, KW, PLAIN = 0, 1, 2


class Lexicon:
    def __init__(self):
        self.uni: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self.bi: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def add_run(self, run: list[tuple[str, int]]):
        """run: (text, style) pieces of one line or block, in reading order."""
        toks: list[tuple[str, int]] = []
        for text, style in run:
            for t in TOKEN.findall(text):
                t = t.rstrip(".:")
                toks.append((t, style))
        prev = None
        for t, style in toks:
            ok = style != PLAIN and CLI_TOKEN.fullmatch(t)
            if ok:
                self.uni[t][style] += 1
                if prev is not None:
                    self.bi[f"{prev} {t}"][style] += 1
            prev = t if style != PLAIN or CLI_TOKEN.fullmatch(t) else None

    def to_json(self) -> dict:
        return {"unigram": dict(self.uni), "bigram": dict(self.bi)}


def pdf_lexicon(p: Path) -> Lexicon:
    import pymupdf as fitz
    lex = Lexicon()
    with fitz.open(p) as doc:
        for page in doc:
            for b in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
                for ln in b.get("lines", []):
                    run = []
                    for s in ln["spans"]:
                        font = s["font"].lower()
                        if MONO.search(font):
                            run.append((s["text"], PLAIN))
                        elif s["flags"] & 2 or "italic" in font or "oblique" in font:
                            run.append((s["text"], ARG))
                        elif s["flags"] & 16 or "bold" in font:
                            run.append((s["text"], KW))
                        else:
                            run.append((s["text"], PLAIN))
                    if any(st == ARG for _, st in run) or any(st == KW for _, st in run):
                        lex.add_run(run)
    return lex


class StyleParser(HTMLParser):
    """XHTML -> styled runs per block. <var> (and varname spans) = argument; <b>, <strong>, <kbd>, keyword spans =
    keyword. Text inside <pre> is CLI output, not syntax, and is ignored."""
    BLOCK = {"p", "div", "li", "dd", "dt", "tr", "td", "th", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section"}

    def __init__(self, lex: Lexicon):
        super().__init__(convert_charrefs=True)
        self.lex, self.run, self.stack, self.pre, self.skip = lex, [], [], 0, 0

    @staticmethod
    def style_of(tag: str, attrs) -> int | None:
        cls = dict(attrs).get("class") or ""
        if tag == "var" or "varname" in cls:
            return ARG
        if tag in ("b", "strong", "kbd") or (tag == "span" and re.search(r"\b(kwd|cmdname|keyword)\b", cls)):
            return KW
        return None

    def flush(self):
        if any(st != PLAIN for _, st in self.run):
            self.lex.add_run(self.run)
        self.run = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "pre":
            self.flush()
            self.pre += 1
        elif tag in self.BLOCK:
            self.flush()
        self.stack.append((tag, self.style_of(tag, attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):        # tolerate unclosed inline tags
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
        elif tag in self.BLOCK:
            self.flush()

    def handle_data(self, data):
        if self.skip or self.pre:
            return
        style = PLAIN
        for _, st in reversed(self.stack):
            if st is not None:
                style = st
                break
        self.run.append((data, style))


def epub_lexicon(p: Path) -> Lexicon:
    """Every XHTML member of the ePub (counts do not depend on spine order, and some OPFs are malformed)."""
    lex = Lexicon()
    with zipfile.ZipFile(p) as z:
        for name in sorted(z.namelist()):
            if name.lower().endswith((".xhtml", ".html", ".htm")):
                sp = StyleParser(lex)
                sp.feed(z.read(name).decode("utf-8", "replace"))
                sp.flush()
    return lex


def html_lexicon(p: Path) -> Lexicon:
    lex = Lexicon()
    sp = StyleParser(lex)
    sp.feed(p.read_text(encoding="utf-8", errors="replace"))
    sp.flush()
    return lex


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_original(doc_key: str, orig_dirs: list[Path]) -> Path | None:
    for d in orig_dirs:
        for ext in EXTS:
            p = d / f"{doc_key}{ext}"
            if p.is_file():
                return p
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--txt-dir", action="append", type=Path, required=True,
                    help="converted corpus dir(s); their *.txt stems are the doc_keys to process")
    ap.add_argument("--orig-dir", action="append", type=Path, help="dirs holding the original .pdf/.epub/.html")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="*", help="restrict to these doc_keys")
    args = ap.parse_args(argv)
    orig_dirs = args.orig_dir or ORIGINAL_DIRS
    args.out.mkdir(parents=True, exist_ok=True)
    keys = sorted({p.stem for d in args.txt_dir if d.is_dir() for p in d.glob("*.txt")})
    if args.only:
        keys = [k for k in keys if k in set(args.only)]
    done = skipped = missing = 0
    for key in keys:
        src = find_original(key, orig_dirs)
        if src is None:
            print(f"{key}: no original found, skipped", flush=True)
            missing += 1
            continue
        digest = sha256(src)
        out = args.out / f"{key}.json"
        if out.is_file():
            try:
                old = json.loads(out.read_text(encoding="utf-8"))
                if old.get("source_sha256") == digest and old.get("version") == VERSION:
                    skipped += 1
                    continue
            except (OSError, ValueError):
                pass
        ext = src.suffix.lower()
        lex = pdf_lexicon(src) if ext == ".pdf" else epub_lexicon(src) if ext == ".epub" else html_lexicon(src)
        rec = {"doc_key": key, "source": src.name, "source_sha256": digest, "version": VERSION, **lex.to_json()}
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        tmp.replace(out)
        done += 1
        print(f"{key}: {len(lex.uni)} tokens, {len(lex.bi)} pairs from {src.name}", flush=True)
    print(f"styles: built {done}, cached {skipped}, no original {missing}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
