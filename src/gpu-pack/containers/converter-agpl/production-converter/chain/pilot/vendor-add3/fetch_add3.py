#!/usr/bin/env python3
"""Download the corpus-v3 additions: ../vendor-add/fetch_add.py (unchanged conventions: honest UA, 3 tries,
403/404/410 recorded without retry, cache, fetch-log.json) plus three fetch modes the gap review needed.

usage: fetch_add3.py <catalog-add3.json> <rfcs-add3.txt> <pilot-add3-dir> [--only key1,key2] [--force]

Catalog "fetch" values:
- book | technote | devnet   exactly as fetch_add.py (book: ePub, else HTML chapters, else PDF; technote: ePub,
                             else the eot-doc-wrapper article body, else PDF; devnet: DevNet doc project)
- chapters                   only the chapters listed in "chapter_urls" (td/docs chapter pages, joined in that
                             order, headings normalized by fetch_add.chapter_html). For books where only a few
                             chapters are in scope (the review measured the rest as out of scope or verbatim in
                             served documents): LAN/WAN guide ERSPAN chapter, ISE 3.1 admin guide chapters, ...
- page                       one HTML page whose content is not in a Cisco support template (the Security Center
                             white papers on sec.cloudapps.cisco.com, like the served iosxe-hardening-guide): the
                             page from its first <h1 to the footer, without script/style/header/footer/nav blocks
- pdf                        the PDF at "pdf_url" (html_url may be the same PDF) - solution-guide PDFs under
                             cisco.com/c/dam with no HTML version
Held catalog entries ("held": reason) are not fetched.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "vendor-add"))
import fetch_add as fa  # noqa: E402

DROP_BLOCKS = re.compile(r"(?is)<(script|style|noscript|header|footer|nav)\b.*?</\1\s*>")


def page_html(page: bytes, title: str) -> bytes:
    t = fa.page_text(page)
    t = DROP_BLOCKS.sub("", t)
    i = t.find("<h1")
    if i < 0:
        raise ValueError("no <h1> in page")
    ends = [j for j in (t.find('id="footer"', i), t.find('class="footer', i), t.find("</body>", i)) if j > 0]
    body = t[i:min(ends) if ends else len(t)]
    if len(re.sub(r"<[^>]+>", " ", body).split()) < 300:
        raise ValueError("page body under 300 words")
    return f"<html><body><!-- {title} -->\n{body}\n</body></html>\n".encode()


def fetch_chapters(c: dict, out: Path) -> dict:
    urls = c.get("chapter_urls") or []
    if not urls:
        return {"status": "failed", "url": c.get("html_url"), "error": "fetch=chapters needs chapter_urls"}
    parts, html = [], ["<html><body>\n"]
    for u in urls:
        st, data = fa.get(u)
        parts.append({"url": u, "status": st, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest() if data else None})
        if st == "ok":
            try:
                html.append(fa.chapter_html(data) + "\n")
            except ValueError as e:
                parts[-1]["status"] = f"parse: {e}"
        time.sleep(0.3)
    bad = [p["url"] for p in parts if p["status"] != "ok"]
    if bad:
        return {"status": "failed", "url": urls[0], "parts": parts, "error": f"{len(bad)}/{len(parts)} chapters failed"}
    html.append("</body></html>\n")
    return fa.record(out / f"{c['doc_key']}.html", "".join(html).encode(), urls[0], "html", method="chapters",
                     parts=parts, tried=["chapters: ok"])


def fetch_page(c: dict, out: Path) -> dict:
    url = c["html_url"]
    st, data = fa.get(url)
    if st != "ok":
        return {"status": "failed", "url": url, "tried": [f"page: {st}"]}
    try:
        body = page_html(data, c["title"])
    except ValueError as e:
        return {"status": "failed", "url": url, "tried": [f"page: {e}"]}
    return fa.record(out / f"{c['doc_key']}.html", body, url, "html", method="page", tried=["page: ok"])


def fetch_pdf(c: dict, out: Path) -> dict:
    url = c.get("pdf_url") or c["html_url"]
    st, data = fa.get(url)
    if st != "ok" or not fa.valid("pdf", data):
        return {"status": "failed", "url": url, "tried": [f"pdf: {st if st != 'ok' else 'invalid pdf'}"]}
    return fa.record(out / f"{c['doc_key']}.pdf", data, url, "pdf", method="pdf", tried=["pdf: ok"])


def main() -> int:
    catalog = [c for c in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["docs"] if not c.get("held")]
    rfcs = [l.split()[0] for l in Path(sys.argv[2]).read_text().splitlines() if l.strip() and not l.startswith("#")]
    root = Path(sys.argv[3]).expanduser()
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    force = "--force" in sys.argv and only is not None
    (root / "rfc").mkdir(parents=True, exist_ok=True)
    (root / "cisco").mkdir(parents=True, exist_ok=True)
    log_path = root / "fetch-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"rfc": {}, "cisco": {}}
    for sec, keys in (("rfc", set(rfcs)), ("cisco", {c["doc_key"] for c in catalog})):
        for gone in set(log[sec]) - keys:
            if log[sec][gone].get("file"):
                (root / sec / log[sec][gone]["file"]).unlink(missing_ok=True)
            del log[sec][gone]
    for n in rfcs:
        if only and n not in only:
            continue
        if not force and fa.cached(root / "rfc", log["rfc"].get(n)):
            continue
        url = f"https://www.rfc-editor.org/rfc/{n}.txt"
        st, data = fa.get(url)
        log["rfc"][n] = fa.record(root / "rfc" / f"{n}.txt", data, url, "txt") if st == "ok" else {"status": st, "url": url}
        print(f"{n}: {log['rfc'][n]['status']}", flush=True)
        time.sleep(0.2)
    modes = {"chapters": fetch_chapters, "page": fetch_page, "pdf": fetch_pdf, "devnet": fa.fetch_devnet}
    for c in catalog:
        key = c["doc_key"]
        if (only and key not in only) or (not force and fa.cached(root / "cisco", log["cisco"].get(key))):
            continue
        try:
            log["cisco"][key] = modes.get(c["fetch"], fa.fetch_book)(c, root / "cisco")
        except (ValueError, KeyError) as e:
            log["cisco"][key] = {"status": "failed", "url": c.get("html_url"), "error": f"{type(e).__name__}: {e}"}
        r = log["cisco"][key]
        if r["status"] == "ok":
            for stale in (root / "cisco").glob(f"{key}.*"):
                if stale.name != r["file"]:
                    stale.unlink()
        print(f"{key}: {r['status']} {r.get('format', '')} {r.get('bytes', '')} {r.get('error', '')}", flush=True)
        log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
        time.sleep(0.5)
    log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
    bad = [k for sec in log.values() for k, v in sec.items() if v["status"] != "ok"]
    print(f"RFCs ok: {sum(v['status'] == 'ok' for v in log['rfc'].values())}/{len(log['rfc'])}; "
          f"Cisco ok: {sum(v['status'] == 'ok' for v in log['cisco'].values())}/{len(log['cisco'])}; not ok: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
