# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
"""Separate production converter.

python3 -I -B adapter.py convert --input FILE --output DIR --job FILE [--lexicon DIR] [--python PATH]
python3 -I -B adapter.py fetch --request FILE --output DIR [--python PATH]
python3 -I -B adapter.py chain-id

Exit codes:
0 completed; conversion text and reports written, or fetch succeeded.
2 usage.
3 bad job, request, input or existing output.
4 a file differs from CHAIN.json, or CHAIN.json is missing or invalid.
5 a conversion step or the quality report failed; result.json written.
6 fetch failed; fetch-result.json written.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
ADAPTER_DIR = Path(__file__).resolve().parent
CHAIN_DIR = ADAPTER_DIR / "chain"
sys.path.insert(0, str(ADAPTER_DIR))
sys.path.insert(0, str(CHAIN_DIR / "pilot" / "vendor-add8"))
HERE = CHAIN_DIR / "pilot" / "vendor-add8"
SRC = WORK = MD = TOOLS_PY = fin = prep8 = ra = None


def out_dir(c):
    return MD


STEPS = ("prep_source", "prep_classes", "convert", "postclean")


MIB_DEF = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s+(?:::=\s*TEXTUAL-CONVENTION|MODULE-IDENTITY|OBJECT-TYPE|"
                     r"NOTIFICATION-TYPE|OBJECT IDENTIFIER|OBJECT-GROUP|NOTIFICATION-GROUP|MODULE-COMPLIANCE)\b")


ADD8_KEYS = ("whole", "mib_excerpt", "steps")


FINISH_KEYS = ("dedupe_article", "keep_sections", "keep_rows", "section_lines", "keep_lines", "drop_sections",
               "drop_from", "drop_blocks",
               "replace_lines", "unfence_tail", "drop_figures", "drop_lines", "rejoin_ifnames", "dehyphenate",
               "context_heading", "promote_lines", "title_line", "title_text", "fix_skipped_levels", "expand_tabs",
               # add8 fix pass (finish_add8 docstring)
               "keep_lines_once", "relayout", "insert_headings", "dehyphenate_extra", "merge_fences",
               "line_separators")


PREP_KEYS = ("heading_levels", "pre_classes", "pre_absorb", "pre_tables", "p_headings", "drop_html", "table_rows",
             "pre_paras", "img_text", "br_pre",
             # add8 fix pass (prep_add8 docstring)
             "drop_childlinks", "glue_space", "unwrap_image_headings", "split_inline_captions", "colspan", "cell_pre",
             "cell_pre_classes", "cmd_tables", "nested_tables_out", "sup_caret", "soft_hyphen", "double_escaped")


INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff]")


PDF_KEYS = ("pdf_pages", "pdf_outline", "pdf_clip", "pdf_bands")


FAIL_TYPES = ("control chars", "unbalanced", "fence state", "no headings", "under 2 KB")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rules_for(c: dict, ov: dict) -> dict:
    """The conversion rules of catalog entry c: pdf_pages/pdf_outline/pdf_clip/pdf_bands for pdf_subset.py, the
    prep_add8 rules and the finish_add8 rules. The override's keep_sections/keep_rows replace the catalog's
    descriptive 'sections'."""
    o = ov.get(c["doc_key"]) or {}
    r: dict = {"title": c["title"]}
    pages = o.get("pdf_pages", c.get("pdf_pages"))
    if pages:
        r["pdf_pages"] = pages
        r["pdf_outline"] = o.get("pdf_outline", "keep")
    for k in ("pdf_clip", "pdf_bands"):
        if o.get(k):
            r[k] = o[k]
    for k in PREP_KEYS + FINISH_KEYS:
        if k in o:                                # a false value counts: 'fix_skipped_levels': false, 'context_heading': false
            r[k] = o[k]
    for k in ADD8_KEYS:                           # add8 keys (module docstring)
        if o.get(k):
            r[k] = o[k]
    if c.get("sections") and not (o.get("keep_sections") or o.get("keep_rows") or o.get("section_lines")
                                  or o.get("whole") or o.get("mib_excerpt")):
        r["keep_sections"] = list(c["sections"])
    return r


def mib_names(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        m = MIB_DEF.match(line)
        if m and m.group(1) not in out and m.group(1) not in ("SYNTAX", "MAX-ACCESS", "STATUS"):
            out.append(m.group(1))
    return out


def mib_excerpt(title: str, container: dict, spec: list[dict]) -> tuple[str, dict]:
    """The text of a MIB excerpt (module docstring) and its record {ranges: [...], lines_kept, problems}."""
    files = {f["name"]: f for f in container["files"]}
    out, rec = [f"# {title}", ""], {"ranges": [], "lines_kept": 0, "problems": []}
    for part in spec:
        f = files.get(part["file"])
        if f is None:
            rec["problems"].append(f"mib_excerpt: no file {part['file']} in the container")
            continue
        if hashlib.sha256(f["text"].encode("utf-8")).hexdigest() != f["sha256"]:
            rec["problems"].append(f"mib_excerpt: {part['file']} text differs from its recorded sha256")
        src = f["text"].split("\n")
        if src and src[-1] == "":
            src = src[:-1]
        titles = part.get("titles") or []
        for n, (a, b) in enumerate(part["ranges"]):
            if not (1 <= a <= b <= len(src)):
                rec["problems"].append(f"mib_excerpt: {part['file']} lines {a}-{b} outside 1-{len(src)}")
                continue
            chunk = [line.rstrip("\r") for line in src[a - 1:b]]
            while chunk and not chunk[0].strip():
                chunk, a = chunk[1:], a + 1
            while chunk and not chunk[-1].strip():
                chunk, b = chunk[:-1], b - 1
            names = titles[n] if n < len(titles) and titles[n] else ", ".join(mib_names(chunk)[:4]) or "header"
            stem = part["file"].rsplit(".", 1)[0]
            out += [f"## {stem}: {names} (lines {a}-{b})", "", "```"] + chunk + ["```", ""]
            rec["ranges"].append({"file": part["file"], "first": a, "last": b, "title": names})
            rec["lines_kept"] += len(chunk)
    return "\n".join(out).rstrip("\n") + "\n", rec


def convert_mib(c: dict, rules: dict, src: Path, rec: dict) -> dict:
    """A doc_type mib entry (module docstring): no chain, the excerpt and the always-on finish rules."""
    key = c["doc_key"]
    if not rules.get("mib_excerpt"):
        raise RuntimeError("doc_type mib needs the override's mib_excerpt (the catalog's line ranges)")
    container = json.loads(src.read_text(encoding="utf-8"))
    text, mrec = mib_excerpt(rules.get("title_text") or c["title"], container, rules["mib_excerpt"])
    out, frec = fin.finish(text, {k: v for k, v in rules.items()
                                  if k in ("fix_skipped_levels", "expand_tabs", "title")})
    frec["problems"] = mrec.pop("problems") + list(frec.get("problems") or [])
    frec["mib"] = mrec
    (WORK / "finish").mkdir(parents=True, exist_ok=True)
    (WORK / "finish" / f"{key}.json").write_text(json.dumps(frec, indent=1) + "\n", encoding="utf-8")
    dst = out_dir(c) / f"{key}.txt"
    dst.write_text(out, encoding="utf-8")
    rec.update(steps=["mib_excerpt"], notes=[f"mib: {len(mrec['ranges'])} ranges, {mrec['lines_kept']} lines"],
               finish=frec, sha256=sha(dst), lines=out.count("\n"), bytes=len(out.encode()), v21_sha256=None)
    return rec


def prep_epub(src: Path, rules: dict) -> tuple[bytes | None, dict]:
    """add8 fix pass: prep_add8.prep() on every XHTML/HTML member of an ePub. Returns the re-zipped bytes (None when no
    member changed) and the counts: numbers summed over the members; an 'unmatched' list keeps only what no member
    matched."""
    import io
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        data = {i.filename: z.read(i.filename) for i in infos}
    total: dict = {}
    lists: dict[str, list[set]] = {}
    members, changed = 0, False
    for name in list(data):
        if not name.lower().endswith((".html", ".xhtml", ".htm")):
            continue
        try:
            text = data[name].decode("utf-8")
        except UnicodeDecodeError:
            continue
        members += 1
        new, counts = prep8.prep(text, rules)
        for k, v in counts.items():
            if isinstance(v, list):
                lists.setdefault(k, []).append(set(v))
            elif isinstance(v, int):
                total[k] = total.get(k, 0) + v
        if new != text:
            data[name] = new.encode("utf-8")
            changed = True
    for k, sets in lists.items():
        common = set.intersection(*sets) if len(sets) == members else set()
        if common:
            total[k] = sorted(common)
    if not changed:
        return None, total
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for i in infos:                           # same order and compression; 'mimetype' stays first and stored
            out.writestr(i, data[i.filename])
    return buf.getvalue(), total


def convert_one(c: dict, rules: dict, f: dict, ra) -> dict:
    key = c["doc_key"]
    src = SRC / f["file"]
    if not src.is_file() or sha(src) != f["sha256"]:
        raise RuntimeError(f"{src} is missing or differs from fetch-log.json")
    steps = tuple(rules.get("steps") or STEPS)
    rec: dict = {"steps": list(steps), "source": {"file": f["file"], "sha256": f["sha256"], "url": f["url"],
                                                  "method": f.get("method"), "format": f.get("format")}}
    if c.get("doc_type") == "mib" or src.name.endswith(".mib.json"):
        return convert_mib(c, rules, src, rec)
    notes = []
    if any(rules.get(k) for k in ("pdf_pages", "pdf_clip", "pdf_bands", "dehyphenate")):
        if src.suffix.lower() != ".pdf":
            raise RuntimeError(f"pdf_pages/pdf_clip/pdf_bands/dehyphenate but the fetched source is {src.name}")
        cut = WORK / "pages" / f"{key}.pdf"
        cmd = [str(TOOLS_PY), "-I", "-B", str(HERE / "pdf_subset.py"), str(src), str(rules.get("pdf_pages") or "all"),
               str(cut), "--outline", rules.get("pdf_outline", "keep")]
        if rules.get("pdf_clip"):
            cmd += ["--clip", ",".join(str(x) for x in rules["pdf_clip"])]
        if rules.get("pdf_bands"):
            cmd += ["--bands", json.dumps(rules["pdf_bands"])]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"pdf_subset exit {r.returncode}: {r.stderr.strip()[-400:]}")
        notes.append(r.stdout.strip())
        prec = json.loads(cut.with_suffix(".json").read_text(encoding="utf-8"))
        if prec.get("context") and rules.get("pdf_pages") and "context_heading" not in rules:
            rules = {**rules, "context_heading": prec["context"]}
        if rules.get("dehyphenate"):
            rules = {**rules, "hyphen_breaks": prec.get("hyphen_breaks") or [],
                     "hyphen_kept": prec.get("hyphen_kept") or []}
        src = cut
    if any(rules.get(k) for k in PREP_KEYS):
        if src.suffix.lower() not in (".html", ".epub"):
            raise RuntimeError(f"{'/'.join(k for k in PREP_KEYS if rules.get(k))} needs an HTML or ePub source, "
                               f"not {src.name}")
        prules = {k: rules[k] for k in PREP_KEYS if rules.get(k)}
        new_epub = None
        if src.suffix.lower() == ".epub":                      # add8 fix pass: every XHTML member, re-zipped
            new_epub, counts = prep_epub(src, prules)
            html = new = None
        else:
            html = src.read_text(encoding="utf-8")
            new, counts = prep8.prep(html, prules)
        if counts.get("fix_unmatched"):
            raise RuntimeError(f"prep8: these rules changed nothing: {counts['fix_unmatched']}")
        if counts.get("drop_html_unmatched"):
            raise RuntimeError(f"prep8 drop_html: no match for {counts['drop_html_unmatched']}")
        if counts.get("table_rows_unmatched"):
            raise RuntimeError(f"prep8 table_rows: no table headed {counts['table_rows_unmatched']!r}")
        if counts.get("pre_paras_unmatched"):
            raise RuntimeError(f"prep8 pre_paras: not exactly one paragraph for {counts['pre_paras_unmatched']}")
        if counts.get("img_text_unmatched"):
            raise RuntimeError(f"prep8 img_text: no image for {counts['img_text_unmatched']}")
        if counts.get("br_pre_unmatched"):
            raise RuntimeError(f"prep8 br_pre: no paragraph for {counts['br_pre_unmatched']}")
        notes.append("prep8: " + (", ".join(f"{k} {v}" for k, v in counts.items() if v) or "clean"))
        if new_epub is not None:
            src = WORK / "prep8" / f"{key}.epub"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(new_epub)
        elif new != html:
            src = WORK / "prep8" / f"{key}.html"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(new, encoding="utf-8")
    res = ra.pipeline({"doc_key": key, "src": str(src), "steps": steps}, "v21")
    text = (WORK / "v21" / f"{key}.txt").read_text(encoding="utf-8")
    out, frec = fin.finish(text, {k: v for k, v in rules.items()
                                  if k not in PDF_KEYS + PREP_KEYS + ADD8_KEYS})
    (WORK / "finish").mkdir(parents=True, exist_ok=True)
    (WORK / "finish" / f"{key}.json").write_text(json.dumps(frec, indent=1) + "\n", encoding="utf-8")
    dst = out_dir(c) / f"{key}.txt"
    dst.write_text(out, encoding="utf-8")
    rec.update(notes=notes + res["notes"], finish=frec, sha256=sha(dst), lines=out.count("\n"),
               bytes=len(out.encode()), v21_sha256=res["sha256"])
    return rec


def isolated(run):
    """reconvert_all.run with -I added after the interpreter's -B (module docstring: isolated interpreters)."""
    def run_isolated(cmd: list, log: list) -> str:
        cmd = list(cmd)
        if len(cmd) > 1 and str(cmd[1]) == "-B":
            cmd[1:2] = ["-B", "-I"]
        return run(cmd, log)
    return run_isolated


def regrade(qa: dict, reviewed: dict, short: dict | None = None) -> list[str]:
    """Re-grade reviewed FAILs to WARN (see the module docstring): benign injection hits (reviewed: {doc_key: [{line,
    reason}]}) and short sources kept whole (short: {doc_key: reason}; only when 'under 2 KB' is the only reason, as
    an earlier round's re-grade did). Returns the doc_keys re-graded."""
    done = []
    for key, reason in (short or {}).items():
        r = qa.get(key)
        if r and r.get("verdict") == "FAIL" and r.get("reasons") == ["under 2 KB"]:
            r["reasons"] = [f"short source: {reason} (was: under 2 KB)"]
            r["verdict"] = "WARN"
            done.append(key)
    for key, items in reviewed.items():
        r = qa.get(key)
        if not r or r.get("verdict") != "FAIL" or not r.get("injection"):
            continue
        flagged = r.get("injection_examples") or []
        if len(flagged) != r["injection"]:
            continue                                         # more hits than examples: not all reviewable
        matched = [next((it for it in items if it["line"] in x), None) for x in flagged]
        others = [x for x in r["reasons"] if "instruction-like" not in x]
        if None in matched or any(w in x for x in others for w in FAIL_TYPES):
            continue
        r["reasons"] = [f"reviewed benign: {x.split(':', 1)[0]}: {it['reason']}" for x, it in zip(flagged, matched)] \
            + others
        r["verdict"] = "WARN"
        r["injection_review"] = [{"line": int(x.split(":", 1)[0]), "verdict": "benign", "reviewer": "operator",
                                  "reason": it["reason"]} for x, it in zip(flagged, matched)]
        done.append(key)
    return done


MESSAGES = {
    "chain_mismatch": "a converter file differs from CHAIN.json: {path}",
    "chain_unreadable": "CHAIN.json is missing or not valid",
    "job_invalid": "the job file is not valid: {problem}",
    "input_mismatch": "the input file does not match the job's source hash",
    "output_exists": "the output folder already exists",
    "step_failed": "a conversion step failed; see result.json",
    "qa_not_run": "the quality report did not run: {tool}",
    "fetch_failed": "the fetch failed: {status}",
    "request_invalid": "the fetch request is not valid: {problem}",
}


class Failure(Exception):
    def __init__(self, code, rule, **values):
        super().__init__(MESSAGES[rule].format(**values))
        self.code = code


def json_bytes(value):
    return (json.dumps(value, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def write_json(path, value):
    path.write_bytes(json_bytes(value))


def verify_chain():
    try:
        raw = (ADAPTER_DIR / "CHAIN.json").read_bytes()
        obj = json.loads(raw)
        if (not isinstance(obj, dict) or obj["format"] != "deep32-converter-chain" or
                type(obj["format_version"]) is not int or obj["format_version"] != 1 or
                obj["name"] != "pymupdf-chain" or obj["round"] != "cisco-add8" or
                not isinstance(obj["chain_id"], str) or not isinstance(obj["files"], list)):
            raise ValueError()
        rows = obj["files"]
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256", "origin"} or
                    not isinstance(row["path"], str) or type(row["bytes"]) is not int or row["bytes"] < 0 or
                    not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
                raise ValueError()
        names = [r["path"] for r in rows]
        if names != sorted(set(names)):
            raise ValueError()
        text = ""
        for row in rows:
            name = row["path"]
            if ("\\" in name or ":" in name or
                    any(ord(char) < 32 or ord(char) == 127 for char in name) or
                    any(part in ("", ".", "..") for part in name.split("/"))):
                raise ValueError()
            text += name + "\t" + row["sha256"] + "\n"
        chain_id = "add8-" + hashlib.sha256(text.encode("ascii")).hexdigest()[:16]
        if chain_id != obj["chain_id"]:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        raise Failure(4, "chain_unreadable")
    for row in rows:
        try:
            data = (ADAPTER_DIR / row["path"]).read_bytes()
        except (OSError, ValueError, UnicodeError):
            raise Failure(4, "chain_mismatch", path=row["path"])
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise Failure(4, "chain_mismatch", path=row["path"])
    return obj, hashlib.sha256(raw).hexdigest()


def _keys(value, required, allowed, prefix=""):
    if not isinstance(value, dict):
        return "wrong type " + prefix.rstrip(".")
    for key in sorted(required):
        if key not in value:
            return "missing key " + prefix + key
    for key in sorted(value):
        if key not in allowed:
            return "unknown key " + prefix + key
    return None


def _typed(value, fields, prefix=""):
    for key, kind in fields:
        if key not in value:
            continue
        v = value[key]
        valid = (type(v) is int if kind == "integer" else
                 isinstance(v, str) if kind == "string" else
                 v is None or isinstance(v, str) if kind == "nullable" else
                 isinstance(v, list) and all(isinstance(x, str) for x in v) if kind == "strings" else
                 isinstance(v, dict) if kind == "object" else
                 isinstance(v, list))
        if not valid:
            return "wrong type " + prefix + key
    return None


def read_checked(path, fetch=False):
    rule = "request_invalid" if fetch else "job_invalid"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
    except (OSError, ValueError, UnicodeError):
        raise Failure(3, rule, problem="not JSON")
    top = ["format", "format_version", "entry"] if fetch else [
        "format", "format_version", "doc_key", "entry", "overrides", "source", "review"]
    problem = _keys(value, top, top)
    if not problem:
        problem = _typed(value, [(k, "string" if k in ("format", "doc_key") else
                                  "integer" if k == "format_version" else "object") for k in top])
    if not problem and value["format"] != ("deep32-fetch-request" if fetch else "deep32-convert-job"):
        problem = "unknown format"
    if not problem and value["format_version"] != 1:
        problem = "unknown format_version"
    if not problem and not fetch and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,89}", value["doc_key"]):
        problem = "wrong type doc_key"
    er = ["doc_key", "title", "doc_type"] + (["fetch"] if fetch else [])
    eo = er + (["html_url", "pdf_url", "epub_url", "page_end", "chapter_urls"] if fetch else
               ["sections", "pdf_pages"])
    entry = value.get("entry")
    if not problem:
        problem = _keys(entry, er, eo, "entry.")
    if not problem:
        problem = _typed(entry, [(k, "strings" if k in ("sections", "chapter_urls") else "string") for k in eo],
                         "entry.")
    if not problem and not fetch and entry["doc_key"] != value["doc_key"]:
        problem = "doc_key mismatch"
    if not problem and not fetch:
        for key in sorted(value["overrides"]):
            if key not in PREP_KEYS + FINISH_KEYS + ADD8_KEYS + PDF_KEYS and not key.startswith("reason"):
                problem = "unknown key overrides." + key
                break
        source = value["source"]
        fields = ["file", "format", "sha256", "url", "method"]
        problem = problem or _keys(source, fields, fields, "source.")
        if not problem:
            problem = _typed(source, [(k, "nullable" if k in ("url", "method") else "string") for k in fields],
                             "source.")
        if not problem and source["format"] not in ("pdf", "html", "epub", "mib-json"):
            problem = "unknown format"
        if not problem and not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
            problem = "wrong type source.sha256"
        review = value["review"]
        problem = problem or _keys(review, ["reviewed_lines", "short_reason"],
                                  ["reviewed_lines", "short_reason"], "review.")
        if not problem:
            problem = _typed(review, [("reviewed_lines", "list"), ("short_reason", "nullable")], "review.")
        if not problem:
            for i, item in enumerate(review["reviewed_lines"]):
                prefix = "review.reviewed_lines."
                problem = _keys(item, ["line", "reason"], ["line", "reason"], prefix)
                if not problem:
                    problem = _typed(item, [("line", "string"), ("reason", "string")], prefix)
                if problem:
                    break
    if problem:
        raise Failure(3, rule, problem=problem)
    return value


def output_folder(path):
    if path.exists():
        raise Failure(3, "output_exists")
    path.mkdir(parents=True)


def configure(output, python):
    global SRC, WORK, MD, TOOLS_PY, fin, prep8, ra
    WORK = output / "work"
    SRC = WORK / "src"
    MD = WORK / "md"
    TOOLS_PY = python
    for path in (WORK, SRC, MD):
        path.mkdir(parents=True, exist_ok=True)
    import pipeline_v21
    pipeline_v21.configure(CHAIN_DIR, WORK, python)
    ra = pipeline_v21
    ra.run = isolated(ra.run)
    import finish_add8 as fin
    import prep_add8 as prep8


def normalise(path):
    data = path.read_bytes()
    if os.name == "nt":
        data = data.replace(b"\r\n", b"\n")
        path.write_bytes(data)
    return data


def pymupdf_version(python):
    code = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, " +
            repr(str(CHAIN_DIR / "tools")) + "); import pymupdf; " +
            "print(getattr(pymupdf, '__version__', '') or getattr(pymupdf, 'VersionBind', ''))")
    r = subprocess.run([str(python), "-I", "-B", "-c", code], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def convert_command(args, chain, chain_sha):
    output = Path(args.output)
    output_folder(output)
    job = read_checked(Path(args.job))
    src = Path(args.input)
    try:
        input_sha = sha(src)
    except OSError:
        input_sha = None
    if src.name != job["source"]["file"] or input_sha != job["source"]["sha256"]:
        raise Failure(3, "input_mismatch")
    key = job["doc_key"]
    result = {
        "format": "deep32-convert-result", "format_version": 1, "status": "failed", "doc_key": key,
        "chain": {"name": "pymupdf-chain", "chain_id": chain["chain_id"], "chain_sha256": chain_sha,
                  "pymupdf_version": None},
        "source": {"file": src.name, "format": job["source"]["format"], "sha256": input_sha,
                   "bytes": src.stat().st_size},
        "rules": None, "steps": [], "notes": [], "finish": None, "v21": None, "text": None,
        "qa": None, "injection": None, "invisible_check": None, "regraded": False, "error": None,
    }
    try:
        configure(output, args.python or sys.executable)
        ext = {"mib-json": "mib.json"}.get(job["source"]["format"], job["source"]["format"])
        name = key + "." + ext
        shutil.copyfile(src, SRC / name)
        rules = rules_for(job["entry"], {key: job["overrides"]})
        result["rules"] = rules
        f = {"file": name, "sha256": input_sha, "url": job["source"]["url"],
             "method": job["source"]["method"], "format": job["source"]["format"]}
        rec = convert_one(job["entry"], rules, f, ra)
        result.update(steps=rec["steps"], notes=rec["notes"], finish=rec["finish"])
        data = normalise(MD / (key + ".txt"))
        (output / "text.txt").write_bytes(data)
        result["text"] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                          "lines": data.count(b"\n")}
        if job["source"]["format"] != "mib-json":
            vdata = normalise(WORK / "v21" / (key + ".txt"))
            result["v21"] = {"sha256": hashlib.sha256(vdata).hexdigest()}
        for tool, report in (("qa_v21.py", "qa"), ("scan_injection.py", "injection")):
            report_path = WORK / (report + ".json")
            argv = [str(TOOLS_PY), "-I", "-B", str(CHAIN_DIR / "pilot" / "vendor-add" / tool),
                    str(report_path), str(MD / (key + ".txt"))]
            if report == "qa" and args.lexicon:
                argv += ["--lexicon", args.lexicon]
            with (WORK / (report + ".out")).open("wb") as out:
                subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT)
            try:
                value = json.loads(report_path.read_text(encoding="utf-8"))
                if key not in value:
                    raise ValueError()
            except (OSError, ValueError, UnicodeError):
                raise Failure(5, "qa_not_run", tool=tool)
            result[report] = value[key]
        reviews = job["review"]
        report = {key: result["qa"]}
        changed = regrade(report, {key: reviews["reviewed_lines"]} if reviews["reviewed_lines"] else {},
                          {key: reviews["short_reason"]} if reviews["short_reason"] else {})
        result["regraded"] = key in changed
        write_json(WORK / "qa.json", report)
        result["invisible_check"] = len(INVISIBLE.findall(data.decode("utf-8")))
        if job["source"]["format"] == "pdf":
            result["chain"]["pymupdf_version"] = pymupdf_version(TOOLS_PY)
        result["status"] = "ok"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        write_json(output / "result.json", result)
        if isinstance(error, Failure):
            raise error
        raise Failure(5, "step_failed")
    write_json(output / "result.json", result)
    return {"doc_key": key, "text_sha256": result["text"]["sha256"], "qa_verdict": result["qa"]["verdict"]}


def fetch_command(args):
    output = Path(args.output)
    output_folder(output)
    request = read_checked(Path(args.request), fetch=True)
    entry = request["entry"]
    key = entry["doc_key"]
    write_json(output / "catalog.json", {"docs": [entry]})
    (output / "rfcs.txt").write_bytes(b"")
    write_json(output / "overrides.json", {"docs": {}})
    argv = [args.python or sys.executable, "-I", "-B",
            str(CHAIN_DIR / "pilot" / "vendor-add8" / "fetch_add8.py"),
            str(output / "catalog.json"), str(output / "rfcs.txt"), str(output / "root"),
            "--overrides", str(output / "overrides.json")]
    with (output / "fetch.out").open("wb") as out, (output / "fetch.err").open("wb") as err:
        subprocess.run(argv, stdout=out, stderr=err)
    try:
        log = json.loads((output / "root" / "fetch-log.json").read_text(encoding="utf-8"))
        record = log["cisco"][key]
        record = {k: v for k, v in record.items() if k != "fetched_at"}
    except (OSError, ValueError, KeyError, TypeError):
        record = {"status": "failed", "error": "the fetch program wrote no record"}
    name = "root/cisco/" + record["file"] if record.get("file") else None
    value = {"format": "deep32-fetch-result", "format_version": 1, "status": record.get("status", "failed"),
             "doc_key": key, "record": record, "file": name}
    write_json(output / "fetch-result.json", value)
    try:
        good = value["status"] == "ok" and name is not None and sha(output / name) == record["sha256"]
    except (OSError, KeyError):
        good = False
    if not good:
        raise Failure(6, "fetch_failed", status=value["status"])
    return {"doc_key": key, "file": name}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    convert_parser = commands.add_parser("convert", add_help=False, allow_abbrev=False)
    for flag in ("input", "output", "job"):
        convert_parser.add_argument("--" + flag, required=True)
    convert_parser.add_argument("--lexicon")
    convert_parser.add_argument("--python")
    fetch_parser = commands.add_parser("fetch", add_help=False, allow_abbrev=False)
    fetch_parser.add_argument("--request", required=True)
    fetch_parser.add_argument("--output", required=True)
    fetch_parser.add_argument("--python")
    commands.add_parser("chain-id", add_help=False, allow_abbrev=False)
    args = parser.parse_args(argv)
    base = {"tool": "deep32-converter", "tool_version": "1", "command": args.command}
    try:
        chain, chain_sha = verify_chain()
        if args.command == "chain-id":
            details = {"chain_id": chain["chain_id"], "chain_sha256": chain_sha, "files": len(chain["files"])}
        elif args.command == "convert":
            details = convert_command(args, chain, chain_sha)
        else:
            details = fetch_command(args)
        print(json.dumps({**base, "status": "ok", **details}, ensure_ascii=True))
        return 0
    except Failure as error:
        print(str(error), file=sys.stderr)
        print(json.dumps({**base, "status": "error", "exit": error.code, "error": str(error)}, ensure_ascii=True))
        return error.code


if __name__ == "__main__":
    raise SystemExit(main())
