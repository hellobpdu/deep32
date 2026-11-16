#!/usr/bin/env python3
"""Turn kept corpus blocks (contract C4) into the corpus package that build_root.py builds from, and optionally
commit it as a one-commit git repository with a fixed identity and time, so the same blocks give the same commit
id on 901 and in the forge (build_root --corpus-repo/--corpus-ref; a --resume must use the same commit).

usage: corpus_from_blocks.py --out DIR [--kb DIR] [--repo --created YYYY-MM-DDTHH:MM:SSZ] [--json] BLOCK [BLOCK ...]

BLOCK   a block folder: block.json and its archive (one file, or the parts the record lists)
--out   a new folder: manifest.json (one entry per document, sorted by doc_key, the corpus manifest's sixteen
        fields plus family for cisco), <block>/<doc_key>.txt, pages/<doc_key>.tsv where a page map exists, and
        kb/ (a copy of --kb: the pruned derived layers) when given
--repo  git init (branch "corpus"), add everything, one commit authored and committed by
        "Deep32 corpus builder <corpus@deep32.invalid>" at --created, with the user's and the system's git
        configuration switched off (no signing, no hooks): an unsigned scratch commit of build data, never pushed.
        Prints the commit id; the ref for build_root is refs/heads/corpus.

Checks (exit 3, the partial output removed): format deep32-corpus-block version 1; block name one of rfc, cisco,
powershell, system (the five cut collections are refused by name); the archive's size and sha256; every member a
regular file named docs/<key>.txt or pages/<key>.tsv of a listed document; every document's bytes and sha256;
doc_key unique across blocks. Exit 0 done, 2 usage. Standard library and the git program (only with --repo).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

KEPT = ("rfc", "cisco", "powershell", "system")
FIELDS = ("doc_key", "title", "doc_type", "path", "source_url", "platform", "release", "sha256", "bytes", "doc_kind",
          "page_url", "release_note", "license", "terms_url", "redistribute", "near_duplicate_of")
IDENT = ("Deep32 corpus builder", "corpus@deep32.invalid")


class Bad(Exception):
    pass


def archive_bytes(block: Path, rec: dict) -> bytes:
    arc = rec["archive"]
    parts = arc.get("parts") or []
    if parts:
        data = b"".join((block / (p["file"] if isinstance(p, dict) else p)).read_bytes() for p in parts)
    else:
        data = (block / arc["file"]).read_bytes()
    if len(data) != arc["size"] or hashlib.sha256(data).hexdigest() != arc["sha256"]:
        raise Bad(f"{block}: archive size or sha256 differs from block.json")
    return data


def read_block(block: Path) -> tuple[str, list[dict], dict[str, bytes]]:
    rec = json.loads((block / "block.json").read_bytes())
    if rec.get("format") != "deep32-corpus-block" or rec.get("format_version") != 1:
        raise Bad(f"{block}: not a deep32-corpus-block version 1 record")
    name = rec.get("block")
    if name not in KEPT:
        raise Bad(f"{block}: block {name!r} is not one of the kept collections")
    docs = {d["doc_key"]: d for d in rec["docs"]}
    if len(docs) != len(rec["docs"]):
        raise Bad(f"{block}: a doc_key appears twice")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes(block, rec)), mode="r:") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                raise Bad(f"{block}: archive member {m.name!r} is not a regular file")
            files[m.name] = tar.extractfile(m).read()
    for key, d in docs.items():
        data = files.get(d["path"])
        if data is None or len(data) != d["bytes"] or hashlib.sha256(data).hexdigest() != d["sha256"]:
            raise Bad(f"{block}: document {key} is missing or differs from its record")
    allowed = {d["path"] for d in docs.values()} | {f"pages/{k}.tsv" for k in docs}
    extra = sorted(set(files) - allowed)
    if extra:
        raise Bad(f"{block}: archive members not in the record: {extra[:3]}")
    return name, list(docs.values()), files


def entry(block: str, d: dict) -> dict:
    e = {f: d.get(f) for f in FIELDS}
    e["doc_type"] = block
    e["path"] = f"{block}/{d['doc_key']}.txt"
    if block == "cisco" and "family" in d:
        e["family"] = d["family"]
    return e


def git(repo: Path, *args: str, env: dict) -> str:
    exe = shutil.which("git") or "git"
    r = subprocess.run([exe, "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                        "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false", "-c", "core.fileMode=false",
                        *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise Bad(f"git {args[0]} failed: {r.stderr.decode(errors='replace')[-400:]}")
    return r.stdout.decode().strip()


def make(blocks: list[Path], out: Path, kb: Path | None, repo: bool, created: str | None) -> dict:
    if out.exists():
        raise Bad(f"{out} exists")
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        entries, seen, counts = [], set(), {}
        tmp.mkdir(parents=True)
        for b in blocks:
            name, docs, files = read_block(b)
            if name in counts:
                raise Bad(f"block {name} given twice")
            counts[name] = len(docs)
            (tmp / name).mkdir()
            for d in docs:
                if d["doc_key"] in seen:
                    raise Bad(f"doc_key {d['doc_key']} in two blocks")
                seen.add(d["doc_key"])
                (tmp / name / f"{d['doc_key']}.txt").write_bytes(files[d["path"]])
                page = files.get(f"pages/{d['doc_key']}.tsv")
                if page is not None:
                    (tmp / "pages").mkdir(exist_ok=True)
                    (tmp / "pages" / f"{d['doc_key']}.tsv").write_bytes(page)
                entries.append(entry(name, d))
        entries.sort(key=lambda e: e["doc_key"])
        (tmp / "manifest.json").write_bytes((json.dumps(entries, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        if kb is not None:
            shutil.copytree(kb, tmp / "kb", ignore=shutil.ignore_patterns("PRUNE.json"))
        commit = None
        if repo:
            if not created:
                raise Bad("--repo needs --created")
            date = created.replace("T", " ").replace("Z", " +0000")
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": IDENT[0],
                        "GIT_AUTHOR_EMAIL": IDENT[1], "GIT_COMMITTER_NAME": IDENT[0], "GIT_COMMITTER_EMAIL": IDENT[1],
                        "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "HOME": str(tmp)})
            git(tmp, "init", "-q", "-b", "corpus", env=env)
            git(tmp, "add", "-A", env=env)
            git(tmp, "commit", "-q", "--no-verify", "-m", "corpus package from kept blocks", env=env)
            commit = git(tmp, "rev-parse", "HEAD", env=env)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest_sha = hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()
    return {"out": str(out), "documents": len(entries), "by_block": counts, "manifest_sha256": manifest_sha,
            "commit": commit, "ref": "refs/heads/corpus" if commit else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="corpus_from_blocks.py", allow_abbrev=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kb")
    ap.add_argument("--repo", action="store_true")
    ap.add_argument("--created")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("blocks", nargs="+")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        rec = make([Path(b) for b in a.blocks], Path(a.out), Path(a.kb) if a.kb else None, a.repo, a.created)
    except (Bad, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f"corpus_from_blocks: REFUSED {exc}", file=sys.stderr)
        return 3
    print(json.dumps(rec, sort_keys=True) if a.json else
          f"{rec['documents']} documents into {rec['out']}" + (f", commit {rec['commit']}" if rec["commit"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
