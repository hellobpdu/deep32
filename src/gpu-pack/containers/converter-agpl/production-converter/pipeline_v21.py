# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
"""Verbatim copies of reconvert_all.py's sha, run and pipeline for the add8
v2.1 pipeline. configure() sets the module names used by these copies.
"""
import hashlib
import shutil
import subprocess
import time
from pathlib import Path

OUT = WORK = PY3 = TOOLS_PY = CONVERT = PREP_SOURCE = PREP_CLASSES = POSTCLEAN = PREP_DOC = None


def configure(chain, out, python):
    global OUT, WORK, PY3, TOOLS_PY, CONVERT, PREP_SOURCE, PREP_CLASSES, POSTCLEAN, PREP_DOC
    OUT = out
    WORK = out / "work"
    PY3 = TOOLS_PY = str(python)
    CONVERT = {"v2": chain / "tools" / "convert_docs.py", "v21": chain / "tools" / "convert_docs_v21.py"}
    PREP_SOURCE = chain / "pilot" / "vendor-add" / "prep_source.py"
    PREP_CLASSES = chain / "pilot" / "vendor-add2" / "prep_classes.py"
    POSTCLEAN = {"v2": chain / "pilot" / "vendor-add" / "postclean.py",
                 "v21": chain / "pilot" / "vendor-add" / "postclean_v21.py"}
    PREP_DOC = chain / "pilot" / "local-add5" / "prep_doc"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(cmd: list, log: list) -> str:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        name = next((Path(str(c)).name for c in cmd[1:] if str(c) != "-B"), "?")
        raise RuntimeError(f"{name} exit {r.returncode}: {r.stderr.strip()[-400:]}")
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    log.append(out)
    return out


def pipeline(doc: dict, variant: str) -> dict:
    key, src = doc["doc_key"], Path(doc["src"])
    work = WORK / "prep" / variant / key
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    log: list[str] = []
    t0 = time.time()
    cur = src
    if "prep_source" in doc["steps"] and cur.suffix in (".epub", ".html"):
        fixed = work / cur.name
        run([PY3, "-B", PREP_SOURCE, cur, fixed], log)
        cur = fixed if fixed.exists() else cur
    if variant == "v21" and cur.suffix == ".html":
        fixed = work / f"{key}.doc.html"
        run([PY3, "-B", PREP_DOC, key, cur, fixed], log)
        cur = fixed if fixed.exists() else cur
    if "prep_classes" in doc["steps"] and cur.suffix == ".html":
        cls = work / f"{key}.cls.html"
        run([PY3, "-B", PREP_CLASSES, cur, cls], log)
        cur = cls if cls.exists() else cur
    raw_dir = OUT / "raw" / variant
    raw_dir.mkdir(parents=True, exist_ok=True)
    run([TOOLS_PY, "-B", CONVERT[variant], cur, key, raw_dir], log)
    raw, final = raw_dir / f"{key}.txt", OUT / variant / f"{key}.txt"
    final.parent.mkdir(parents=True, exist_ok=True)
    if "postclean" in doc["steps"]:
        cmd = [PY3, "-B", POSTCLEAN[variant], raw, final] + (["--doc-key", key] if variant == "v21" else [])
        run(cmd, log)
    elif variant == "v21":
        run([PY3, "-B", POSTCLEAN["v21"], raw, final, "--doc-key", key, "--v21-only"], log)
    else:
        shutil.copyfile(raw, final)
    return {"notes": log, "seconds": round(time.time() - t0, 1), "sha256": sha(final)}
