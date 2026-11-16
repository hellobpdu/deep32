"""Native-text ingest adapter: RFC plaintext and converted Cisco Markdown -> a MinerU-shaped result directory.

rag_core ingests only MinerU results (markdown.md, structured_content.json, middle_json.json, model_output.json,
worker_result.json). This adapter synthesizes those artifacts from text sources so the unchanged rag_core
ingest -> harden -> chunk -> index path runs on RFCs and Cisco docs (DESIGN.md P5 "RFC .txt and Cisco adapters").

Ported unchanged from the staging tool (staging/tools/text_adapter.py, sha256 6b99301f...) that built the
measured staging roots root-v3 and root-v4, minus its command-line wrapper; scripts/build_root.py calls adapt().
Kinds: "rfc" (RFC plaintext) and "cisco" (converter Markdown: ATX headings, ``` fences, optional
"<!-- page N -->" markers, which give chunks real PDF pages / ePub spine items).

structured_content.json:
  {"metadata": {"document": {"title", "page_count", "doc_key", "source_kind"}}, "is_full_document": true,
   "pages": [{"page_idx": i, "blocks": [{"type": "title", "level": n, "content": {"text": ...}},
                                        {"type": "text" | "code" | "table", "content": {"text": ...}}]}]}

RFC rules (kept in step with eval/validate_golden.js so gold sections line up with chunk heading paths):
  - pages split on form feed; "RFC nnnn ... Month Year" headers and "[Page n]" footers dropped
  - paragraph = run of non-blank lines; body text is joined with single spaces (what the validator normalizes to)
  - headings: numbered ("9.1.2.  Title", "A.4.  Title") at column 0, or indented <= 4 when the body indent >= 6
    (RFC 2328-era layout); "Appendix X.  Title"; unnumbered Title Case lines at column 0 standing alone
  - table-of-contents lines (dot leaders) are dropped; ASCII figures/tables become code blocks with layout kept
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

RFC_HEADER = re.compile(r"^\f?RFC \d{3,5}\s{2,}.*\s{2,}\S.*\d{4}\s*$")
RFC_FOOTER = re.compile(r"\[Page \d+\]\s*$")
TOC_LINE = re.compile(r"(\.\s?){4,}\s*\d+\s*$")
APPENDIX = re.compile(r"^Appendix ([A-Z0-9]+)\.?\s+(\S.*)$")
UNNUMBERED = re.compile(r"^[A-Z][A-Za-z0-9 ,'()/-]{3,60}$")
FRONT_MATTER = re.compile(r"^(RFC|Request|Category|Updates|Obsoletes|ISSN|STD|BCP|FYI|Network Working Group|"
                          r"Internet Engineering Task Force|Internet Architecture Board|Independent Submission|"
                          r"Internet Research Task Force|Status of This Memo|Copyright Notice|Table of Contents)\b", re.I)
FIGURE = re.compile(r"[+|]-{2,}|-{3,}\+|^\s*\|.*\|\s*$|\S {3,}\S.* {3,}\S")
PAGE = re.compile(r"^<!-- page (\d+) -->$")
MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def block(kind: str, text: str, level: int | None = None) -> dict:
    b = {"type": kind, "content": {"text": text}}
    if level is not None:
        b["level"] = level
    return b


def rfc_pages(text: str) -> list[list[dict]]:
    text = text.replace("\r", "")
    indents = Counter(len(m.group(1)) for l in text.split("\n") if (m := re.match(r"^( +)\S", l)))
    body_indent = indents.most_common(1)[0][0] if indents else 3
    max_head_indent = 4 if body_indent >= 6 else 0
    head_re = re.compile(rf"^ {{0,{max_head_indent}}}((?:\d+|[A-Z])(?:\.\d+)*)\.?\s{{1,6}}(\S.{{0,110}})$")
    raw_pages = text.split("\f")
    while raw_pages and not raw_pages[-1].strip():
        raw_pages.pop()

    def heading(line: str, first_page_top: bool) -> tuple[int, str] | None:
        if TOC_LINE.search(line):
            return None
        m = APPENDIX.match(line)
        if m:
            return 1, line.strip()
        m = head_re.match(line)
        if m and not (re.search(r"[.,;]$", m.group(2)) and len(m.group(2).split()) > 8):
            sec = m.group(1)
            if not re.fullmatch(r"[A-Z]", sec) or re.match(r"^\S", line):
                return min(6, sec.count(".") + 1), line.strip()
        if (not first_page_top and UNNUMBERED.match(line) and not FRONT_MATTER.match(line)):
            return 1, line.strip()
        return None

    pages: list[list[dict]] = []
    for pi, page in enumerate(raw_pages):
        lines = [l.rstrip() for l in page.split("\n")]
        lines = [l for l in lines if not RFC_HEADER.match(l) and not RFC_FOOTER.search(l)]
        blocks: list[dict] = []
        para: list[str] = []
        first_para = pi == 0

        def flush(para: list[str]) -> None:
            nonlocal first_para
            if not para:
                return
            top = first_para
            first_para = False
            if all(TOC_LINE.search(l) or not l.strip() for l in para):
                return                                      # table of contents
            h = heading(para[0], top) if (len(para) == 1 or re.match(r"^\S", para[0]) or max_head_indent) else None
            if h and (len(para) == 1 or re.match(r"^\s", para[1]) or h[1][0].isdigit() or h[1].startswith("Appendix")):
                blocks.append(block("title", h[1], h[0]))
                para = para[1:]
                if not para:
                    return
            if top or sum(1 for l in para if FIGURE.search(l)) >= max(1, len(para) // 2):
                common = min(len(l) - len(l.lstrip()) for l in para if l.strip())
                blocks.append(block("code", "\n".join(l[common:] for l in para)))
                return
            blocks.append(block("text", " ".join(l.strip() for l in para)))

        for l in lines + [""]:
            if l.strip():
                para.append(l)
            else:
                flush(para)
                para = []
        pages.append(blocks)
    return pages


def cisco_pages(text: str) -> list[list[dict]]:
    pages: list[list[dict]] = [[]]
    started = False
    fence: list[str] | None = None
    table: list[str] = []

    def flush_table() -> None:
        if table:
            pages[-1].append(block("table", "\n".join(table)))
            table.clear()

    for line in text.replace("\r", "").split("\n"):
        if fence is not None:
            if line.startswith("```"):
                code = "\n".join(fence).strip("\n")
                if code.strip():
                    pages[-1].append(block("code", code))
                fence = None
            else:
                fence.append(line)
            continue
        m = PAGE.match(line)
        if m:
            flush_table()
            if started:
                pages.append([])
            started = True
            continue
        if line.startswith("```"):
            flush_table()
            fence = []
            continue
        if not line.strip():
            flush_table()
            continue
        m = MD_HEAD.match(line)
        if m:
            flush_table()
            pages[-1].append(block("title", m.group(2), len(m.group(1))))
            continue
        if " | " in line:
            table.append(line)
            continue
        flush_table()
        pages[-1].append(block("text", line.strip()))
    flush_table()
    if fence:
        pages[-1].append(block("code", "\n".join(fence).strip("\n")))
    return pages


def to_markdown(pages: list[list[dict]]) -> str:
    out: list[str] = []
    for blocks in pages:
        for b in blocks:
            t = b["content"]["text"]
            if b["type"] == "title":
                out.append("#" * b["level"] + " " + t)
            elif b["type"] == "code":
                out.append("```\n" + t + "\n```")
            else:
                out.append("\\" + t if t.startswith("#") else t)   # RFC 2328 "# links": text, not a heading
    return "\n\n".join(out) + "\n"


def adapt(kind: str, text: str, doc_key: str, out: Path, title: str | None = None) -> dict:
    """Write the five MinerU-shaped artifacts for one document into `out`; return a summary."""
    pages = rfc_pages(text) if kind == "rfc" else cisco_pages(text)
    title = title or next((b["content"]["text"] for p in pages for b in p if b["type"] == "title"), doc_key)
    n = len(pages)
    meta = {"document": {"title": title, "page_count": n, "doc_key": doc_key, "source_kind": kind}}
    structured = {"metadata": meta, "is_full_document": True,
                  "pages": [{"page_idx": i, "blocks": b} for i, b in enumerate(pages)]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "structured_content.json").write_text(json.dumps(structured, ensure_ascii=False), encoding="utf-8")
    (out / "markdown.md").write_text(to_markdown(pages), encoding="utf-8")
    (out / "middle_json.json").write_text(json.dumps({"metadata": meta, "is_full_document": True}), encoding="utf-8")
    (out / "model_output.json").write_text("{}", encoding="utf-8")
    (out / "worker_result.json").write_text(json.dumps({
        "tier": "standard", "pages": n, "parser": "claude-text-adapter", "parser_version": "1.0",
        "source_kind": kind, "doc_key": doc_key}), encoding="utf-8")
    kinds = Counter(b["type"] for p in pages for b in p)
    return {"doc_key": doc_key, "pages": n, "blocks": dict(kinds), "title": title}
