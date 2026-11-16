#!/usr/bin/env python3
"""Download the corpus-deepen additions (RFCs + Cisco documents) into the staging pilot-add area.

usage: fetch_add.py <catalog-add.json> <rfcs-add.txt> <pilot-add-dir> [--only key1,key2] [--force]

Conventions follow staging/tools/fetch_sources.py: honest UA, 3 tries with backoff, 403/404/410 recorded
without retry, cached files are not downloaded again (--force re-fetches the --only keys).
- RFCs: https://www.rfc-editor.org/rfc/rfcNNNN.txt -> <pilot-add>/rfc/rfcNNNN.txt
- fetch=book: complete-book ePub, else the book's HTML chapters joined into one file, else the PDF
- fetch=technote: ePub, else the HTML article body, else the PDF (ePub/PDF URLs derived from html_url)
  -> <pilot-add>/cisco/<doc_key>.<epub|html|pdf>  (a superseded file of another format is removed)
- fetch=devnet: a developer.cisco.com doc project. Its guide pages (TOC items of type 'markdown', in TOC order)
  are joined into one HTML file, headings shifted by TOC depth -> <pilot-add>/cisco/<doc_key>.html
Writes <pilot-add>/fetch-log.json: {"rfc": {key: rec}, "cisco": {key: rec}} with
rec = {status, url, file, format, bytes, sha256, fetched_at, tried?, parts?}.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from prep_source import to_utf8

UA = "deep32-forge-fetch/1.0 (fetches one document for a local Deep32 knowledge base)"
# DevNet pages left out: support/marketing pages, and the API changelogs (long endpoint-diff lists that would
# crowd retrieval; the release is already fixed by the doc itself)
DEVNET_SKIP = {"Developer Support", "Postman Collections", "Videos", "Sandboxes", "API Changelog",
               "SD-WAN vManage v20.9 API Change Log"}


def page_text(data: bytes) -> str:
    """A fetched page as text; stray cp1252 bytes (Cisco pages have a few) are kept, not turned into U+FFFD."""
    return to_utf8(data)[0]


def get(url: str, tries: int = 3) -> tuple[str, bytes]:
    """('ok', body) or (error status, b'')."""
    err = "not tried"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if len(data) < 512:
                raise ValueError(f"suspiciously small response ({len(data)} bytes)")
            return "ok", data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                return f"http {e.code}", b""
            err = f"http {e.code}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        time.sleep(2 * (i + 1))
    return err, b""


def valid(fmt: str, data: bytes) -> bool:
    if fmt == "epub":
        try:
            return "META-INF/container.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return False
    if fmt == "pdf":
        return data.startswith(b"%PDF")
    return b"<" in data[:4096]


def record(dest: Path, data: bytes, url: str, fmt: str, **extra) -> dict:
    dest.write_bytes(data)
    return {"status": "ok", "url": url, "file": dest.name, "format": fmt, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


def cached(out: Path, prev: dict | None) -> dict | None:
    """A previous ok record whose file is still on disk unchanged."""
    if not prev or prev.get("status") != "ok":
        return None
    f = out / prev["file"]
    if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == prev["sha256"]:
        return prev
    return None


def article_html(page: bytes, title: str) -> bytes:
    """The TechNote/book article body of a cisco.com page (drops site chrome and the in-page TOC)."""
    t = page_text(page)
    i = t.find('id="eot-doc-wrapper"')
    if i < 0:
        raise ValueError("no eot-doc-wrapper article in page")
    end = min((j for j in (t.find("Was this Document Helpful", i), t.find('class="col narrow-v2"', i)) if j > 0),
              default=len(t))
    body = t[t.rfind("<div", 0, i):end]
    body = re.sub(r'(?s)<div id="support-toc".*?</div>\s*(?=<h2)', "", body, count=1)
    return f"<html><body><h1>{title}</h1>\n{body}\n</body></html>\n".encode()


class FetchError(Exception):
    pass


def get_ok(url: str) -> bytes:
    st, data = get(url)
    if st != "ok":
        raise FetchError(st)
    return data


def book_chapters(book_url: str, page: bytes) -> list[str]:
    """Chapter page URLs of a cisco.com book (<book>/<chapter>.html), in the order the book page lists them."""
    prefix = book_url[:-len(".html")] + "/"
    out: list[str] = []
    for href in re.findall(r'href="([^"#?]+\.html)"', page_text(page)):
        u = urljoin(book_url, href)
        if u.startswith(prefix) and u not in out:
            out.append(u)
    return out


def chapter_html(page: bytes) -> str:
    """A book chapter's content with DITA headings normalized: topictitleN -> hN, and a sectiontitle
    ('Syntax Description', 'Command History', ...; Cisco tags these h2 or h3 inconsistently) one level below
    its topic. The result has the same shape as Cisco's ePub books: chapter h1, command/topic h2, labels h3."""
    t = page_text(page)
    i = t.find('id="chapterContent"')
    if i < 0:
        raise ValueError("no chapterContent in chapter page")
    j = t.find('<div class="col narrow-v2"', i)
    body = t[t.index(">", i) + 1:j if j > 0 else len(t)]
    level = 1

    def fix(m):
        nonlocal level
        attrs = m.group(2)
        topic = re.search(r"topictitle(\d)", attrs)
        if topic:
            level = n = min(6, int(topic.group(1)))
        elif "sectiontitle" in attrs:
            n = min(6, level + 1)
        else:
            n = int(m.group(1))
        return f"<h{n}{attrs}>{m.group(3)}</h{n}>"
    return re.sub(r"(?s)<h([1-6])(\b[^>]*)>(.*?)</h\1>", fix, body)


def assemble_chapters(book_url: str) -> tuple[bytes, list[dict]]:
    """All chapters of a book joined into one HTML file; FetchError unless every chapter was fetched."""
    urls = book_chapters(book_url, get_ok(book_url))
    if not urls:
        raise FetchError("no chapter links on book page")
    parts, html = [], ["<html><body>\n"]
    for u in urls:
        st, data = get(u)
        parts.append({"url": u, "status": st, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest() if data else None})
        if st == "ok":
            html.append(chapter_html(data) + "\n")
        time.sleep(0.3)
    bad = [p["url"] for p in parts if p["status"] != "ok"]
    if bad:
        raise FetchError(f"{len(bad)}/{len(parts)} chapters failed, e.g. {bad[0]}")
    html.append("</body></html>\n")
    return "".join(html).encode(), parts


def fetch_plan(c: dict) -> list[tuple[str, str]]:
    """(method, url) in the order tried. Books: complete ePub, else the HTML chapters (tables and CLI blocks keep
    their structure; the PDF path flattens them), else the PDF. TechNotes: ePub, else the HTML article, else the
    PDF. ePub/PDF URLs the catalog does not give are derived from html_url (<book>.html -> <book>.epub), so every
    attempt, including a 404, lands in the fetch log's 'tried'."""
    html_url = c.get("html_url", "")
    epub = c.get("epub_url") or html_url[:-5] + ".epub"
    pdf = c.get("pdf_url") or html_url[:-5] + ".pdf"
    return [("epub", epub), ("article" if c["fetch"] == "technote" else "chapters", html_url), ("pdf", pdf)]


def fetch_book(c: dict, out: Path) -> dict:
    html_url = c.get("html_url", "")
    tried, parts = [], None
    for how, url in fetch_plan(c):
        if not url:
            continue
        try:
            if how == "chapters":
                data, parts = assemble_chapters(url)
            elif how == "article":
                data = article_html(get_ok(url), c["title"])
            else:
                data = get_ok(url)
                if not valid(how, data):
                    raise FetchError(f"invalid {how}")
                if how == "pdf" and len(data) < 16384:
                    raise FetchError(f"pdf too small ({len(data)} bytes)")  # stub PDFs of HTML-only notes
        except (FetchError, ValueError) as e:
            tried.append(f"{how}: {e}")
            time.sleep(0.5)
            continue
        fmt = "html" if how in ("chapters", "article") else how
        extra = {"method": how, "tried": tried + [f"{how}: ok"]}
        if parts:
            extra["parts"] = parts
        return record(out / f"{c['doc_key']}.{fmt}", data, url, fmt, **extra)
    return {"status": "failed", "url": html_url, "tried": tried}


class JSLiteral:
    """Parser for the JavaScript object-literal subset DevNet embeds its doc TOC in
    ({key:"v",k2:'v',n:1,b:!0,a:[...],o:{...}}); raises ValueError on anything else."""

    TOKEN = re.compile(r'\s*(?:(?P<p>[{}\[\]:,])|(?P<s>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
                       r'|(?P<n>-?\d+(?:\.\d+)?)|(?P<b>![01])|(?P<i>[A-Za-z_$][\w$]*))', re.S)

    ESC = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(.))", re.S)
    SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def __init__(self, text: str, pos: int):
        self.t, self.pos = text, pos

    @classmethod
    def unescape(cls, s: str) -> str:
        def rep(m):
            code = m.group(1) or m.group(2) or m.group(3)
            return chr(int(code, 16)) if code else cls.SIMPLE.get(m.group(4), m.group(4))
        return cls.ESC.sub(rep, s)

    def _next(self):
        m = self.TOKEN.match(self.t, self.pos)
        if not m:
            raise ValueError(f"unexpected input at {self.pos}: {self.t[self.pos:self.pos + 40]!r}")
        self.pos = m.end()
        kind = m.lastgroup
        v = m.group(kind)
        if kind == "s":
            return "v", self.unescape(v[1:-1])
        if kind == "n":
            return "v", float(v) if "." in v else int(v)
        if kind == "b":
            return "v", v == "!0"
        if kind == "i":
            return ("v", {"true": True, "false": False, "null": None}[v]) if v in ("true", "false", "null") else ("i", v)
        return "p", v

    def value(self):
        kind, v = self._next()
        if kind == "v":
            return v
        if v == "{":
            obj = {}
            while True:
                kind, k = self._next()
                if (kind, k) == ("p", "}"):
                    return obj
                if kind not in ("i", "v"):
                    raise ValueError(f"bad key {k!r}")
                if self._next() != ("p", ":"):
                    raise ValueError("expected ':'")
                obj[str(k)] = self.value()
                kind, sep = self._next()
                if sep == "}":
                    return obj
                if sep != ",":
                    raise ValueError("expected ',' or '}'")
        if v == "[":
            arr = []
            while True:
                save = self.pos
                if self._next() == ("p", "]"):
                    return arr
                self.pos = save
                arr.append(self.value())
                kind, sep = self._next()
                if sep == "]":
                    return arr
                if sep != ",":
                    raise ValueError("expected ',' or ']'")
        raise ValueError(f"unexpected token {v!r}")


def devnet_toc(page: str) -> tuple[str, list[tuple[int, dict]]]:
    """(content base URL, plan) from a DevNet doc page. The plan lists (depth, item) in TOC order for the guide
    pages we keep ('markdown' items not in DEVNET_SKIP) and for the groups that contain at least one of them;
    groups are the items without 'content'."""
    i = page.find("{webJson:")
    if i < 0:
        raise ValueError("no embedded webJson TOC")
    web = JSLiteral(page, i + len("{webJson:")).value()

    def walk(items, d):
        out = []
        for it in items:
            if "content" in it:
                if it.get("type") == "markdown" and it.get("title") not in DEVNET_SKIP:
                    out.append((d, it))
            else:
                sub = walk(it.get("items", []), d + 1)
                if sub:
                    out += [(d, it)] + sub
        return out
    return web["meta"]["location"], walk(web["items"], 1)


def shift_headings(html: str, by: int) -> str:
    return re.sub(r"<(/?)h([1-6])\b", lambda m: f"<{m.group(1)}h{min(6, int(m.group(2)) + by)}", html)


def fetch_devnet(c: dict, out: Path) -> dict:
    st, page = get(c["html_url"])
    if st != "ok":
        return {"status": "failed", "url": c["html_url"], "tried": [f"toc: {st}"]}
    base, plan = devnet_toc(page_text(page))
    parts, html = [], [f"<html><body>\n<!-- {c['title']}; assembled from {c['html_url']} -->\n"]
    for depth, it in plan:
        title = it.get("title", "")
        if "content" not in it:                               # a TOC group
            html.append(f"<h{min(6, depth)}>{title}</h{min(6, depth)}>\n")
            continue
        url = base + re.sub(r"^(\./)+", "", it["content"])
        st, data = get(url)
        parts.append({"title": title, "url": url, "status": st, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest() if data else None})
        if st == "ok":
            html.append(shift_headings(page_text(data), depth - 1) + "\n")
        time.sleep(0.3)
    html.append("</body></html>\n")
    ok = sum(p["status"] == "ok" for p in parts)
    if not ok:
        return {"status": "failed", "url": c["html_url"], "parts": parts}
    rec = record(out / f"{c['doc_key']}.html", "".join(html).encode(), c["html_url"], "html", parts=parts)
    rec["content_base"] = base
    return rec


def main() -> int:
    catalog = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["docs"]
    rfcs = [l.split()[0] for l in Path(sys.argv[2]).read_text().splitlines() if l.strip() and not l.startswith("#")]
    root = Path(sys.argv[3]).expanduser()
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    force = "--force" in sys.argv and only is not None
    (root / "rfc").mkdir(parents=True, exist_ok=True)
    (root / "cisco").mkdir(parents=True, exist_ok=True)
    log_path = root / "fetch-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"rfc": {}, "cisco": {}}
    for sec, keys in (("rfc", set(rfcs)), ("cisco", {c["doc_key"] for c in catalog})):
        for gone in set(log[sec]) - keys:                   # dropped from the catalog: forget it and its file
            if log[sec][gone].get("file"):
                (root / sec / log[sec][gone]["file"]).unlink(missing_ok=True)
            del log[sec][gone]
    for n in rfcs:
        if only and n not in only:
            continue
        if not force and cached(root / "rfc", log["rfc"].get(n)):
            continue
        url = f"https://www.rfc-editor.org/rfc/{n}.txt"
        st, data = get(url)
        log["rfc"][n] = record(root / "rfc" / f"{n}.txt", data, url, "txt") if st == "ok" else {"status": st, "url": url}
        time.sleep(0.2)
    for c in catalog:
        key = c["doc_key"]
        if (only and key not in only) or (not force and cached(root / "cisco", log["cisco"].get(key))):
            continue
        try:
            log["cisco"][key] = fetch_devnet(c, root / "cisco") if c["fetch"] == "devnet" else fetch_book(c, root / "cisco")
        except (ValueError, KeyError) as e:
            log["cisco"][key] = {"status": "failed", "url": c.get("html_url"), "error": f"{type(e).__name__}: {e}"}
        r = log["cisco"][key]
        if r["status"] == "ok":
            for stale in (root / "cisco").glob(f"{key}.*"):     # a superseded download in another format
                if stale.name != r["file"]:
                    stale.unlink()
        print(f"{key}: {r['status']} {r.get('format', '')} {r.get('bytes', '')}", flush=True)
        log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
        time.sleep(0.5)
    log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
    bad = [k for sec in log.values() for k, v in sec.items() if v["status"] != "ok"]
    print(f"RFCs ok: {sum(v['status'] == 'ok' for v in log['rfc'].values())}/{len(log['rfc'])}; "
          f"Cisco ok: {sum(v['status'] == 'ok' for v in log['cisco'].values())}/{len(log['cisco'])}; not ok: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
