#!/usr/bin/env python3
"""The xfer pack for the forge's s3-dense window job (the processor comparison procedure 2.1, SCHEDULE section 2, lever 10): the engine
and its venv inputs, the KEPT blocks of this pack only, the corpus repository built from them, and the --no-dense
root built on 901 at the neutral path the forge job resumes (/var/lib/deep32/rag-data/<generation>). Never the
corpus mirror, never a cut collection.

usage:
  xfer_pack.py make   --generation GEN --root ROOT --corpus REPO --engine DIR --wheels DIR --lock FILE
                      --out DIR [--blocks-dir DIR] [--blocks rfc,powershell,system|cisco|all] [--opt-in TYPES]
                      [--embedding-revision HEX40]
  xfer_pack.py verify --pack DIR

make     writes DIR/<GEN>/ (refuses an existing one):
           engine/            the transformed engine (rag/core of the export)
           wheels/, engine.lock  the venv inputs (the forge makes .venv with --no-index --require-hashes)
           blocks/<name>/     block.json and archive of each block in --blocks (kept names only)
           corpus.bundle      git bundle of REPO's refs/heads/corpus (one commit, the corpus package)
           root.tar           ROOT as "rag-data/<GEN>/..." (regular files and folders only; SQLite sidecars and
                              logs/ left out), so it unpacks at /var/lib/deep32/rag-data/<GEN> in the forge
           job.env            ENGINE_DIR, CORPUS_REPO, CORPUS_REF, ROOT and OPT_IN for forge/jobs/s3-dense.sh, and
                              with --embedding-revision "export RAG_EMBEDDING_REVISION=<rev>": s3-dense.sh
                              sources job.env and names no revision itself; without one build_root records
                              index_state.embedding_revision NULL, which kb_bundle refuses (compat-embedding)
                              and the two-card merge cannot match
           PACK.json, SHA256SUMS  what is in it, every file's sha256
         The root must be a --no-dense build of exactly this pack's documents: BUILD_REPORT.json status complete
         or dense skipped, generation GEN, and its sources/manifest.json doc types equal to --blocks.
verify   re-hashes every file against SHA256SUMS and re-checks PACK.json (run in the forge before import).
Exit 0 done, 2 usage, 3 refused. Standard library and the git program (make only).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

KEPT = ("rfc", "cisco", "powershell", "system")
SPLITS = {"all": KEPT, "a": ("rfc", "powershell", "system"), "b": ("cisco",)}
GEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
FORGE_ENGINE = "/opt/d32f/s3/engine"
FORGE_CORPUS = "/opt/d32f/s3/corpus.git"
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class Refused(Exception):
    pass


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def blocks_wanted(value: str) -> tuple[str, ...]:
    if value in SPLITS:
        return SPLITS[value]
    names = tuple(v.strip() for v in value.split(",") if v.strip())
    if not names or any(n not in KEPT for n in names) or len(set(names)) != len(names):
        raise Refused(f"--blocks {value!r}: kept block names only (rfc, cisco, powershell, system)")
    return names


def root_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = info.name
    parts = name.split("/")
    if name.endswith(("-wal", "-shm", "-journal")) or (len(parts) >= 3 and parts[2] in ("logs", "work")):
        return None
    if not (info.isfile() or info.isdir()):
        raise Refused(f"root holds {name}, which is not a regular file or folder")
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def make(a) -> dict:
    gen = a.generation
    if not GEN_RE.fullmatch(gen):
        raise Refused(f"generation {gen!r}")
    names = blocks_wanted(a.blocks)
    if a.embedding_revision is not None and not REVISION_RE.fullmatch(a.embedding_revision):
        raise Refused(f"--embedding-revision {a.embedding_revision!r}: 40 lower-case hex digits (the model commit)")
    root, out = Path(a.root), Path(a.out) / gen
    if out.exists():
        raise Refused(f"{out} exists")
    report = json.loads((root / "BUILD_REPORT.json").read_bytes())
    if report.get("generation") != gen:
        raise Refused(f"the root's generation is {report.get('generation')!r}, not {gen}")
    if (report.get("dense") or {}).get("points"):
        raise Refused("the root already holds dense vectors: a pack carries a --no-dense root")
    man = json.loads((root / "sources/manifest.json").read_bytes())
    types = sorted({e["doc_type"] for e in man})
    if types != sorted(names):
        raise Refused(f"the root's documents are of {types}, the pack's blocks are {sorted(names)}")
    tmp = out.with_name(gen + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        tmp.mkdir(parents=True)
        shutil.copytree(Path(a.engine), tmp / "engine", ignore=shutil.ignore_patterns("__pycache__", ".venv"))
        shutil.copytree(Path(a.wheels), tmp / "wheels")
        shutil.copyfile(a.lock, tmp / "engine.lock")
        if a.blocks_dir:
            for n in names:
                src = Path(a.blocks_dir) / n
                rec = json.loads((src / "block.json").read_bytes())
                if rec.get("block") != n:
                    raise Refused(f"{src}: block.json says {rec.get('block')!r}")
                shutil.copytree(src, tmp / "blocks" / n)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        r = subprocess.run([shutil.which("git") or "git", "-C", str(a.corpus), "bundle", "create",
                            str(tmp / "corpus.bundle"), "refs/heads/corpus"], env=env, capture_output=True)
        if r.returncode:
            raise Refused(f"git bundle: {r.stderr.decode(errors='replace')[-300:]}")
        commit = subprocess.run([shutil.which("git") or "git", "-C", str(a.corpus), "rev-parse", "refs/heads/corpus"],
                                env=env, capture_output=True, text=True).stdout.strip()
        if report.get("corpus", {}).get("commit") not in (None, commit):
            raise Refused(f"the root was built from commit {report['corpus']['commit'][:12]}, the repo holds {commit[:12]}")
        with tarfile.open(tmp / "root.tar", "w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(root, arcname=f"rag-data/{gen}", filter=root_filter)
        opt_in = a.opt_in or "powershell,system"
        (tmp / "job.env").write_text(
            f"ENGINE_DIR={FORGE_ENGINE}\nCORPUS_REPO={FORGE_CORPUS}\nCORPUS_REF=refs/heads/corpus\n"
            f"ROOT=/var/lib/deep32/rag-data/{gen}\nOPT_IN={opt_in}\n"
            + (f"export RAG_EMBEDDING_REVISION={a.embedding_revision}\n" if a.embedding_revision else ""),
            encoding="ascii", newline="\n")
        files = sorted(p for p in tmp.rglob("*") if p.is_file())
        sums = {p.relative_to(tmp).as_posix(): sha_file(p) for p in files}
        pack = {"format": "deep32-xfer-pack", "format_version": 1, "generation": gen, "blocks": list(names),
                "documents": len(man), "corpus_commit": commit, "root_report_status": report.get("status"),
                "files": len(sums) + 1, "bytes": sum(p.stat().st_size for p in files),
                "embedding_revision": a.embedding_revision,
                "job_env_sha256": sums["job.env"]}
        (tmp / "PACK.json").write_text(json.dumps(pack, indent=1, sort_keys=True) + "\n", encoding="ascii")
        sums["PACK.json"] = sha_file(tmp / "PACK.json")
        (tmp / "SHA256SUMS").write_text("".join(f"{h}  {p}\n" for p, h in sorted(sums.items())), encoding="ascii")
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return pack


def verify(pack_dir: Path) -> dict:
    sums = {}
    for line in (pack_dir / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        h, _, p = line.partition("  ")
        sums[p] = h
    present = {p.relative_to(pack_dir).as_posix() for p in pack_dir.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    if present != set(sums):
        raise Refused(f"files differ from SHA256SUMS: {sorted(present ^ set(sums))[:5]}")
    bad = [p for p, h in sums.items() if sha_file(pack_dir / p) != h]
    if bad:
        raise Refused(f"{len(bad)} files changed: {bad[:5]}")
    pack = json.loads((pack_dir / "PACK.json").read_bytes())
    if any(b not in KEPT for b in pack["blocks"]):
        raise Refused("PACK.json names a block that is not kept")
    return {"status": "pass", "files": len(sums), "generation": pack["generation"], "blocks": pack["blocks"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xfer_pack.py", allow_abbrev=False)
    sub = ap.add_subparsers(dest="verb", required=True)
    m = sub.add_parser("make", allow_abbrev=False)
    for f in ("generation", "root", "corpus", "engine", "wheels", "lock", "out"):
        m.add_argument("--" + f, required=True)
    m.add_argument("--blocks-dir")
    m.add_argument("--blocks", default="all")
    m.add_argument("--opt-in")
    m.add_argument("--embedding-revision")
    v = sub.add_parser("verify", allow_abbrev=False)
    v.add_argument("--pack", required=True)
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        rec = make(a) if a.verb == "make" else verify(Path(a.pack))
    except (Refused, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f"xfer_pack: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
