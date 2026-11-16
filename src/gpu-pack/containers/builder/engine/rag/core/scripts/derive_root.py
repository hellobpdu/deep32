#!/usr/bin/env python3
"""Derive a new root (generation) from a built one: the same index, another serving configuration.

usage: derive_root.py --from <generation or root path> --to <new generation or root path>
                      [--set CONFIG.KEY[.KEY...]=VALUE ...] [--rerank-url URL] [--resume]
                      [--dense-clone copy|snapshot [--qdrant-snapshots-dir DIR]]

For an A/B of a configuration (reranker on vs off, admission N, BM25 options) on the index a generation already
has, without rebuilding it. A generation id names a sibling directory (rag-data/<generation>); --to defaults to
a sibling of --from.

Steps, each safe to repeat with --resume; progress lines go to stdout:
  1. clone      <from>/sqlite/rag.db and kb/kb.sqlite copied with SQLite's backup API (a consistent copy even of
                the served root's database), sources/ hard-linked (copied across file systems); the copy's dense
                marks are reset, because its vectors will live in a collection of its own. The copy is the whole
                database: a root built with --opt-in-doc-types keeps both BM25 tables, chunk_fts and chunk_fts_optin,
                and its routed chunk triggers (rag_core.db.split_optin_fts); the report's bm25.tables is <from>'s.
                A --set retrieval.opt_in_doc_types other than the build's still serves correctly
                (rag_core.retrievers.lexical: a default search then also ranks chunk_fts_optin, or leaves documents
                out of chunk_fts), but only the build's doc types get p1.4's statements over chunk_fts alone
  2. structure  documents whose structure an older section parser indexed (rag_core.structure.STRUCTURE_VERSION)
                are indexed again, as build_root's step 5 does; the others are skipped. Only the clone's rag.db is
                written (the sources are read); the KB keeps the section ids it was built with
  3. config     <from>'s overlay (config/*.json) with the collection rag_dense_<to> and generation <to>, then every
                --set: CONFIG is dense, retrieval, serving or late_interaction, KEY a dotted path (objects are made
                as needed), VALUE JSON (true, 10, null, "x") or else a string. --rerank-url URL is short for
                retrieval.reranker = {enabled: true, url: URL, candidates: 10, timeout_seconds: 15, breaker 3/30}
                (e.g. https://<forge>.<tailnet>.ts.net:8443/v1/rerank, the GPU reranker behind Tailscale Serve),
                applied before the --set items. Serving arms, e.g.: retrieval.reranker.max_chars=1200,
                retrieval.reranker.window=centred, retrieval.reranker.candidates=15 with
                retrieval.reranker.search_many_candidates=10 (rag_core.rerank), retrieval.search_many_parallel=true,
                retrieval.search_many_rerank=best_query (rag_core.api_v2.SearchManySettings); opt-in
                collections, e.g. retrieval.opt_in_doc_types='["powershell","system"]'
                (rag_core.api_v2.opt_in_doc_types); grep on the regex engine, retrieval.grep.regex_engine=true
                (rag_core.api_v2.GrepSettings); the rerank gate, serving.max_concurrent_searches=4
                serving.max_queued_searches=12 serving.pipeline.rerank_gate=1 (rag_core.pipeline); the dense leg's
                Qdrant search, exact (a full scan, no HNSW) retrieval.dense.exact=true or a wider HNSW beam
                retrieval.dense.hnsw_ef=256 (rag_core.retrievers.dense.DenseSearchSettings; unset: no search params,
                /v2/status settings unchanged; scripts/ann_audit.py measures ANN against exact); identical-text
                copies folded out of the hits into also_in, retrieval.fold_identical=true
                (rag_core.api_v2.fold_identical; unset: the hits and /v2/status settings unchanged)
  4. dense      a root that serves dense gets its OWN collection: every vector copied from <from>'s collection
                through build_root's reuse path (fetched by point id per document, read-only; batched upserts), nothing
                embedded. So pruning one generation's collection never breaks the other. The step then waits
                (up to 20 min) until Qdrant has indexed the copy (status green; report dense.index), so that a
                validate_root started next does not measure latency against a background HNSW build.
                --dense-clone snapshot (default copy, the above): the collection is made by Qdrant from a snapshot of
                <from>'s instead (its segments, HNSW graphs included: an upserted copy gets a fresh graph, which
                answers a little differently), checked (config, payload indexes, exact point count, the same ids
                and scores in the top 50 of 64 seeded stored vectors, unfiltered and with the derived root's default
                filter), the snapshot deleted; every chunk's point is checked as the copy path takes it (a stray
                point is deleted), and the chunks are marked as the copy path marks them. A failure before that is
                done deletes the collection and the snapshot, so --resume starts it again. The report's dense.clone
                says so; the "dense: N points in <collection>, N copied from <source>, 0 embedded" line is the copy
                path's. A --resume run must use the mode of the run that started the step
  5. report     <to>/BUILD_REPORT.json: <from>'s build facts (corpus, documents, chunks, structure, kb) with
                "derived": true, "derived_from" and the overrides, rewritten after every step; the structure facts
                are the derived root's own (and "structure_reindexed": true) once step 2 has indexed anything.
                validate_root reads it like a build's

A derived root is for serving and measuring, never for building on:
  - its sources/ are hard links to <from>'s files, which may be the served root's. Never edit a file there in
    place: an append or an in-place write changes <from>'s file too. Replacing a file (a new file renamed over
    it) or deleting one is safe, and so is deleting the whole derived root.
  - its rag.db keeps <from>'s documents.original_path and canonical_path, absolute paths into <from>. Serving
    never reads them (ingest writes them; nothing reads them back), and originals/ and the canonical artifacts
    are not copied, so do not ingest into a derived root.
  - build_root refuses it (its report is not build_root's). For new or changed documents build a new generation
    with build_root --reuse-dense-from <from>, which re-embeds only what changed.
Deleting a derived root means its directory and its collection rag_dense_<to>; <from> keeps its own files and
collection. <from>'s collection is only read; with --dense-clone snapshot Qdrant also writes a snapshot file of it
(about the size of the collection's vectors; for a moment, the recovery's unpacked copy too), which step dense
deletes.

Guards (all before anything is written): <to> is never $RAG_ROOT or inside or above it, never a non-empty
directory without a report of this script (or of another derivation from another root), never an existing
collection it did not create; <from> must be a complete build or derivation, and a copy of its vectors must fit
the derived dense config (vector size; one embedding model and revision, the revision this process writes);
dense.collection and serving.generation are not settable; a reranker that is set must be configured well enough
to start, and the search_many, grep, dense search, fold and pipeline settings and opt_in_doc_types must be ones
deep32-engine starts with.
Exit status: 0 complete, 1 incomplete (see "failed_steps" in the report), 2 refused.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_root as br  # noqa: E402  (scripts/build_root.py: guards, reuse path, report helpers)
from rag_core.api_v2 import fold_identical, grep_settings, opt_in_doc_types, search_many_settings  # noqa: E402
from rag_core.config import OVERLAY_NAMES, load_config  # noqa: E402
from rag_core.db import connect  # noqa: E402
from rag_core.dense import (  # noqa: E402
    DenseConfig,
    ensure_collection,
    load_dense_config,
    make_qdrant_client,
    mark_dense_indexed,
    reset_dense_state,
)
from rag_core.paths import RAGPaths  # noqa: E402
from rag_core.pipeline import pipeline_settings  # noqa: E402
from rag_core.rerank import Reranker  # noqa: E402
from rag_core.retrievers.dense import dense_search_settings  # noqa: E402

DERIVER = "scripts/derive_root.py"
REPORT_NAME = br.REPORT_NAME
OWNED_KEYS = {("dense", "collection"), ("serving", "generation")}
# what a derived root copies from its source's report, as the build wrote it
BUILD_FACTS = ("corpus", "documents", "chunks", "chunker", "bm25", "structure", "kb", "code_commit")

log = br.log
now = br.now
write_json = br.write_json
Refused = br.Refused


# ---------------------------------------------------------------- arguments


def parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def parse_sets(items: list[str]) -> list[tuple[str, list[str], Any]]:
    """--set CONFIG.KEY[.KEY...]=VALUE -> (config, key path, value)."""
    out = []
    for item in items:
        target, sep, value = item.partition("=")
        parts = target.split(".")
        if not sep or len(parts) < 2 or not all(parts):
            raise Refused(f"--set {item!r}: expected CONFIG.KEY[.KEY...]=VALUE")
        if parts[0] not in OVERLAY_NAMES:
            raise Refused(f"--set {item!r}: CONFIG must be one of {sorted(OVERLAY_NAMES)}")
        if (parts[0], parts[1]) in OWNED_KEYS and len(parts) == 2:
            raise Refused(f"--set {item!r}: {parts[0]}.{parts[1]} belongs to the derived root and is set by "
                          "this script")
        out.append((parts[0], parts[1:], parse_value(value)))
    return out


def apply_set(config: dict[str, Any], path: list[str], value: Any) -> None:
    node = config
    for key in path[:-1]:
        child = node.get(key)
        if child is None:
            child = node[key] = {}
        if not isinstance(child, dict):
            raise Refused(f"--set: {'.'.join(path)} goes through {key!r}, which is not an object")
        node = child
    node[path[-1]] = value


def resolve_root(value: str, beside: Path | None) -> Path:
    """A generation id is a sibling of `beside` (rag-data/<generation>); anything with a slash is a path."""
    if "/" in value or os.sep in value:
        return Path(value).expanduser().resolve()
    if not br.GENERATION_RE.fullmatch(value):
        raise Refused(f"{value!r} is neither a generation id ({br.GENERATION_RE.pattern}) nor a path")
    if beside is None:
        raise Refused(f"--from {value!r}: give the source root as a path (a generation id needs a place)")
    return (beside / value).resolve()


def default_rag_data() -> Path:
    """Where a bare generation id lives: rag-data/ next to the served root when RAG_ROOT says where that is,
    else this checkout's rag-data/ (a deploy job may run without RAG_ROOT)."""
    served = os.environ.get("RAG_ROOT")
    return Path(served).expanduser().resolve().parent if served else br.REPO / "rag-data"


# ---------------------------------------------------------------- preflight


def read_report(root: Path) -> dict[str, Any] | None:
    path = root / REPORT_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Refused(f"{path} is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Refused(f"{path} is not a JSON object")
    return data


def source_facts(source: Path) -> dict[str, Any]:
    """The source's report: a complete build (or derivation) with a database."""
    if not (source / "sqlite" / "rag.db").is_file():
        raise Refused(f"--from {source}: no sqlite/rag.db, not a built root")
    report = read_report(source)
    if report is None:
        raise Refused(f"--from {source}: no {REPORT_NAME}, not a root built by build_root or derive_root")
    failed = report.get("failed_steps") or []
    bad_docs = (report.get("documents") or {}).get("failed") or []
    if report.get("status") != "complete" or failed or bad_docs:
        raise Refused(f"--from {source}: its report says {report.get('status')!r} with {len(failed)} failed steps "
                      f"and {len(bad_docs)} failed documents; only a complete root is derived from")
    return report


def source_config(source: Path) -> dict[str, dict[str, Any]]:
    """The source root's overlay files, as they are (a missing one is empty)."""
    out = {}
    for name in sorted(OVERLAY_NAMES):
        path = source / "config" / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError as exc:
            raise Refused(f"--from {source}: config/{name}.json is not JSON: {exc}") from None
        if not isinstance(data, dict):
            raise Refused(f"--from {source}: config/{name}.json is not an object")
        out[name] = data
    return out


def has_dense_index(report: dict[str, Any]) -> bool:
    dense = report.get("dense") or {}
    return bool(dense.get("collection")) and not dense.get("skipped")


def serves_dense(config: dict[str, dict[str, Any]]) -> bool:
    retrievers = config["retrieval"].get("retrievers")
    return retrievers is None or "dense" in retrievers


def derived_config(source: dict[str, dict[str, Any]], generation: str, collection: str,
                   sets: list[tuple[str, list[str], Any]], rerank_url: str | None) -> dict[str, dict[str, Any]]:
    config = json.loads(json.dumps(source))                 # a deep copy
    # always its own collection name, also when it serves no dense: never the source's
    config["dense"]["collection"] = collection
    config["serving"]["generation"] = generation
    if rerank_url is not None:
        config["retrieval"]["reranker"] = {
            **(config["retrieval"].get("reranker") or {}),
            "enabled": True, "url": rerank_url, "candidates": br.RERANK_CANDIDATES,
            "timeout_seconds": br.RERANK_TIMEOUT_SECONDS, "breaker": br.ROOT_BREAKER,
        }
    for name, path, value in sets:
        apply_set(config[name], path, value)
    try:
        search_many_settings(config["retrieval"])           # rag_core.api checks them at start
        opt_in_doc_types(config["retrieval"])               # and this
        grep_settings(config["retrieval"])                  # and this
        fold_identical(config["retrieval"])                 # and this
        dense_search_settings(config["retrieval"].get("dense"))     # and the orchestrator this
        serving = config["serving"]
        if "pipeline" in serving:                           # and the rerank gate, with the admission it runs with
            # rag_core.api imports the serving stack (orchestrator, colbert: torch, pylate, transformers), ~9 s
            # and ~430 MiB: only for a root that sets the gate, never on every derive
            from rag_core.api import admission_settings, queue_limit
            pipeline_settings(serving, slots=admission_settings(serving)[0], max_queued=queue_limit(serving))
    except ValueError as exc:
        raise Refused(f"{exc}: deep32-engine would not start with it") from None
    reranker = config["retrieval"].get("reranker") or {}
    if not isinstance(reranker, dict):
        raise Refused("retrieval.reranker must be an object")
    if reranker.get("enabled"):
        try:
            Reranker(reranker).close()          # makes a client, sends nothing
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(f"retrieval.reranker is enabled but would not start: {exc}") from None
    return {name: data for name, data in config.items() if data or name != "late_interaction"}


def derived_dense_config(overlay: dict[str, Any], collection: str) -> DenseConfig:
    """The DenseConfig that step dense will load from the derived root (the repo's dense.json, this overlay, the
    process environment), made before anything is written: the overlay goes to a temporary directory."""
    with tempfile.TemporaryDirectory(prefix="derive-root-") as tmp:
        (Path(tmp) / "config").mkdir()
        write_json(Path(tmp) / "config" / "dense.json", overlay)
        try:
            with env_var("RAG_DENSE_COLLECTION", collection):
                return load_dense_config(root=tmp)
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(f"the derived dense config would not load: {type(exc).__name__}: {exc}") from None


def check_source_vectors(client, source: Path, source_collection: str, derived: DenseConfig,
                         generation: str) -> None:
    """The source's vectors fit the derived root: the vector size it is configured for, one embedding model and
    revision, and the revision this process would mark the copies with (RAG_EMBEDDING_REVISION). Step dense
    checks the revision again; this is so that a mismatch is refused before the clone writes ~0.6 GB."""
    vectors = client.get_collection(collection_name=source_collection).config.params.vectors
    size = getattr(vectors, "size", None)
    if size is None or int(size) != derived.vector_size:
        raise Refused(f"--from {source}: {source_collection} holds {size if size is not None else 'named'}-d "
                      f"vectors, the derived root is configured for {derived.vector_size}-d")
    try:
        reuse = br.DenseSource(source, source_collection, client, derived.vector_size, generation)
    except ValueError as exc:
        raise Refused(f"--from {source}: {exc}") from None
    try:
        if reuse.revision != derived.embedding_revision:
            raise Refused(f"--from {source}: embedding revision {reuse.revision!r}, but this process would mark "
                          f"the copies {derived.embedding_revision!r} (RAG_EMBEDDING_REVISION)")
    finally:
        reuse.close()


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Every refusal happens here, before anything is written."""
    rag_data = default_rag_data()
    source = resolve_root(args.source, rag_data)
    target = resolve_root(args.target, source.parent)
    generation = target.name
    if not br.GENERATION_RE.fullmatch(generation):
        raise Refused(f"--to directory name {generation!r} must match {br.GENERATION_RE.pattern}")
    if target == source or source in target.parents or target in source.parents:
        raise Refused(f"--to {target} overlaps --from {source}")
    br.check_not_served(target)
    if args.dense_clone == "snapshot" and not br.PurePosixPath(args.qdrant_snapshots_dir).is_absolute():
        raise Refused(f"--qdrant-snapshots-dir {args.qdrant_snapshots_dir!r} is not an absolute path (the Qdrant "
                      "server's snapshots_path)")
    sets = parse_sets(args.set)
    if args.rerank_url is not None:
        from urllib.parse import urlsplit
        parts = urlsplit(args.rerank_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Refused(f"--rerank-url {args.rerank_url!r} is not an http(s) URL")

    report = source_facts(source)
    src_config = source_config(source)
    collection = br.collection_for(generation)
    config = derived_config(src_config, generation, collection, sets, args.rerank_url)
    dense = serves_dense(config)
    if dense and not has_dense_index(report):
        raise Refused(f"the derived root would serve dense, but {source} has no dense index to copy")
    if dense:
        br.check_collection(collection)

    mine = read_report(target)
    if mine is not None:
        if mine.get("builder") != DERIVER:
            raise Refused(f"{target} holds a root this script did not make ({mine.get('builder')})")
        if (mine.get("derived_from") or {}).get("root") != str(source):
            raise Refused(f"{target} was derived from {(mine.get('derived_from') or {}).get('root')}, not {source}")
        if not args.resume:
            raise Refused(f"{target} already holds a derivation ({mine.get('status')}); pass --resume to continue it")
        # the collection a started dense step made is the mode's: copied, or recovered from a snapshot
        started = mine.get("dense") or {}
        cloned_by = (started.get("clone") or {}).get("mode", "copy")
        if started.get("collection_owned") and cloned_by != args.dense_clone:
            raise Refused(f"{target}'s dense step started with --dense-clone {cloned_by}; resume it the same way or "
                          "derive another root")
    elif target.exists() and any(target.iterdir()):
        raise Refused(f"{target} is not empty and holds no {REPORT_NAME}: not a root this script started")

    source_collection = None
    if dense:
        source_collection = str(report["dense"]["collection"])
        derived = derived_dense_config(config["dense"], collection)
        client = make_qdrant_client(derived)
        try:
            if not client.collection_exists(collection_name=source_collection):
                raise Refused(f"--from {source}: Qdrant has no collection {source_collection}")
            if not (mine or {}).get("dense", {}).get("collection_owned") and \
                    client.collection_exists(collection_name=collection):
                raise Refused(f"Qdrant collection {collection} already exists and this derivation did not create it")
            check_source_vectors(client, source, source_collection, derived,
                                 str(report.get("generation") or source.name))
            if args.dense_clone == "snapshot":
                status = br.collection_status(client, source_collection)
                if status != "green":
                    raise Refused(f"--dense-clone snapshot: {source_collection} is {status}, not green (Qdrant is "
                                  "still optimizing it, and a snapshot of it would not search as it will)")
        finally:
            client.close()
    return {
        "source": source, "target": target, "generation": generation, "report": report, "config": config,
        "sets": [f"{name}.{'.'.join(path)}={json.dumps(value)}" for name, path, value in sets],
        "dense": dense, "collection": collection, "source_collection": source_collection, "mine": mine,
    }


# ---------------------------------------------------------------- steps


def backup_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a SQLite database (also of one in use, WAL included), atomically in place."""
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.unlink(missing_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    os.replace(tmp, dst)


def link_tree(src: Path, dst: Path) -> dict[str, int]:
    """dst as a copy of src made of hard links (copies where linking fails, e.g. across file systems)."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    counts = {"linked": 0, "copied": 0}

    def place(s: str, d: str) -> str:
        try:
            os.link(s, d)
            counts["linked"] += 1
        except OSError:
            shutil.copy2(s, d)
            counts["copied"] += 1
        return d

    shutil.copytree(src, tmp, copy_function=place)
    shutil.rmtree(dst, ignore_errors=True)
    os.replace(tmp, dst)
    return counts


def step_clone(state: dict[str, Any], report: dict[str, Any]) -> None:
    source, target = state["source"], state["target"]
    clone = report.setdefault("clone", {})
    if not clone.get("sqlite"):
        backup_copy(source / "sqlite" / "rag.db", target / "sqlite" / "rag.db")
        with connect(target / "sqlite" / "rag.db") as conn:
            # the vectors go into this root's own collection (step dense), which marks them again
            reset = conn.execute(
                "UPDATE index_state SET dense_indexed = 0, embedding_model = NULL, embedding_revision = NULL, "
                "indexed_at = NULL WHERE dense_indexed != 0"
            ).rowcount
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        clone["sqlite"] = {"bytes": (target / "sqlite" / "rag.db").stat().st_size, "dense_marks_reset": reset}
        log(f"clone: sqlite/rag.db copied ({clone['sqlite']['bytes']} bytes; {reset} dense marks reset)")
    kb = source / "kb" / "kb.sqlite"
    if kb.is_file() and not clone.get("kb"):
        backup_copy(kb, target / "kb" / "kb.sqlite")
        clone["kb"] = {"bytes": (target / "kb" / "kb.sqlite").stat().st_size}
        log("clone: kb/kb.sqlite copied")
    if (source / "sources").is_dir() and not clone.get("sources"):
        clone["sources"] = link_tree(source / "sources", target / "sources")
        log(f"clone: sources/ {clone['sources']['linked']} files linked, {clone['sources']['copied']} copied")
    RAGPaths(target).ensure()


def step_structure(state: dict[str, Any], report: dict[str, Any]) -> None:
    """The clone's documents whose structure an older section parser indexed (another STRUCTURE_VERSION) are
    indexed again by this checkout's (build_root's step, rag_core.structure.index_root; the others are skipped),
    so the derived root serves the sections its deep32-engine parses. The report keeps <from>'s structure facts while the
    clone's structure is <from>'s, and has the derived root's own once a run has re-indexed anything."""
    target = state["target"]
    copied = report.get("structure")
    br.step_structure(report, target, target / "sqlite" / "rag.db")
    if report["structure"]["indexed_this_run"]:
        report["structure_reindexed"] = True
    elif copied is not None and not report.get("structure_reindexed"):
        report["structure"] = copied


def step_config(state: dict[str, Any], report: dict[str, Any]) -> None:
    config_dir = state["target"] / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, data in state["config"].items():
        write_json(config_dir / f"{name}.json", data)
    retrieval = state["config"]["retrieval"]
    report["config"] = {
        "collection": state["collection"],
        "retrievers": retrieval.get("retrievers"),
        "reranker": bool((retrieval.get("reranker") or {}).get("enabled")),
        "files": sorted(f"config/{name}.json" for name in state["config"]),
        "overrides": state["sets"] + ([f"--rerank-url {state['rerank_url']}"] if state["rerank_url"] else []),
    }
    log(f"config: overlay written (generation {state['generation']}, collection {state['collection']}, "
        f"reranker {'on' if report['config']['reranker'] else 'off'}; {len(report['config']['overrides'])} "
        "overrides)")


@contextlib.contextmanager
def env_var(name: str, value: str):
    """os.environ[name] = value for the block, then as it was."""
    saved = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved


def step_dense(state: dict[str, Any], report: dict[str, Any], write: Callable[[], None]) -> None:
    if state.get("dense_clone") == "snapshot":
        step_dense_snapshot(state, report, write)
        return
    target = state["target"]
    # the derived root's collection, whatever RAG_DENSE_COLLECTION the process came with
    with env_var("RAG_DENSE_COLLECTION", state["collection"]):
        config = load_dense_config(root=target)
    if config.collection != state["collection"]:
        raise RuntimeError(f"dense config resolves to {config.collection}, expected {state['collection']}")
    dense = report.setdefault("dense", {})
    dense.update({"collection": state["collection"], "collection_owned": True})
    write()                                         # ownership is on disk before the collection exists
    client = make_qdrant_client(config)
    reuse = None
    try:
        reuse = br.DenseSource(state["source"], state["source_collection"], client, config.vector_size,
                               str(state["report"].get("generation") or state["source"].name))
        if reuse.revision != config.embedding_revision:
            raise RuntimeError(f"{reuse.generation} has embedding revision {reuse.revision!r}, "
                               f"this process {config.embedding_revision!r}")
        result = br.index_dense_resumable(target / "sqlite" / "rag.db", config, None, client, reuse=reuse)
        # validate_root measures latency next: not while Qdrant is still building the copy's index
        result["index"] = br.wait_until_indexed(client, state["collection"])
    finally:
        if reuse is not None:
            reuse.close()
        client.close()
    dense.update(result)
    counts = dense.get("reuse") or {}
    dense["reuse"] = {"from_generation": reuse.generation, "from_root": str(reuse.root),
                      "collection": reuse.collection,
                      "reused": counts.get("reused", 0) + result["reused_this_run"],
                      "embedded": counts.get("embedded", 0) + result["embedded_this_run"]}
    log(f"dense: {dense['points']} points in {state['collection']}, {dense['reuse']['reused']} copied from "
        f"{reuse.collection}, 0 embedded")


def unmarked_chunks(db_path: Path) -> list[str]:
    with connect(db_path) as conn:
        return [r[0] for r in conn.execute(
            "SELECT c.chunk_id FROM chunks AS c LEFT JOIN index_state AS s ON s.chunk_id = c.chunk_id "
            "WHERE COALESCE(s.dense_indexed, 0) = 0 ORDER BY c.chunk_id")]


def step_dense_snapshot(state: dict[str, Any], report: dict[str, Any], write: Callable[[], None]) -> None:
    """Step dense with --dense-clone snapshot (module docstring, step 4): the collection Qdrant recovers from a snapshot
    of <from>'s, checked (build_root.restore_from_snapshot), every chunk's point checked as the copy path takes it
    (build_root.clone_points), the chunks marked as the copy path marks them. Until that is done ("complete" in
    dense.clone) a failure deletes the collection and resets the marks, so a --resume run starts it again; a resume
    of a complete clone checks the counts and waits for green, as the copy path's resume does."""
    target, collection = state["target"], state["collection"]
    with env_var("RAG_DENSE_COLLECTION", collection):
        config = load_dense_config(root=target)
    if config.collection != collection:
        raise RuntimeError(f"dense config resolves to {config.collection}, expected {collection}")
    if collection == state["source_collection"]:
        raise RuntimeError(f"{collection} is <from>'s own collection: it is never deleted or recovered into")
    db_path = target / "sqlite" / "rag.db"
    dense = report.setdefault("dense", {})
    dense.update({"collection": collection, "collection_owned": True})
    clone = dense.setdefault("clone", {"mode": "snapshot"})
    write()                                     # ownership and the mode are on disk before the collection exists
    with connect(db_path) as conn:
        chunks = [(r[0], r[1]) for r in conn.execute("SELECT chunk_id, doc_id FROM chunks ORDER BY chunk_id")]
    chunk_ids = [chunk_id for chunk_id, _ in chunks]
    client = make_qdrant_client(config)
    snapper = br.snapshot_client(config)
    reuse = None
    marked = 0
    restored_now = False
    try:
        reuse = br.DenseSource(state["source"], state["source_collection"], client, config.vector_size,
                               str(state["report"].get("generation") or state["source"].name))
        if reuse.revision != config.embedding_revision:
            raise RuntimeError(f"{reuse.generation} has embedding revision {reuse.revision!r}, "
                               f"this process {config.embedding_revision!r}")
        if not clone.get("complete"):
            try:
                if not clone.get("restored"):
                    if client.collection_exists(collection_name=collection):
                        client.delete_collection(collection_name=collection)    # an earlier run's, never checked
                    reset_dense_state(db_path, chunk_ids)
                    with connect(db_path) as conn:
                        filters = br.search_filters(conn, opt_in_doc_types(load_config("retrieval", root=target)))
                    clone["restored"] = br.restore_from_snapshot(
                        client, snapper, reuse.collection, collection, clone, write,
                        snapshots_dir=state["snapshots_dir"], chunk_ids=chunk_ids, filters=filters)
                    restored_now = True
                    write()
                clone["points"] = br.clone_points(client, collection, chunks)
                pending = unmarked_chunks(db_path)
                mark_dense_indexed(db_path, pending, reuse.model, config.embedding_revision)
                marked = len(pending)
                clone["complete"] = True
                write()
            except BaseException:
                # no unchecked copy is left behind: --resume makes it again
                for key in ("restored", "points"):
                    clone.pop(key, None)
                with contextlib.suppress(Exception):
                    if client.collection_exists(collection_name=collection):
                        client.delete_collection(collection_name=collection)
                with contextlib.suppress(Exception):
                    reset_dense_state(db_path, chunk_ids)
                with contextlib.suppress(Exception):
                    write()
                raise
        with connect(db_path) as conn:
            indexed = conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]
        points_total = client.count(collection_name=collection, exact=True).count
        if not points_total == indexed == len(chunks):
            raise RuntimeError(f"dense index incomplete: {len(chunks)} chunks, {indexed} marked, "
                               f"{points_total} points in {collection}")
        result = {
            "collection": collection,
            "collection_created": restored_now,
            "embedding_model": reuse.model,
            "embedding_revision": config.embedding_revision,
            "chunks_indexed_this_run": marked,
            "reused_this_run": marked,
            "embedded_this_run": 0,
            "points": points_total,
        }
        result["index"] = br.wait_until_indexed(client, collection)
    finally:
        if reuse is not None:
            reuse.close()
        snapper.close()
        client.close()
    dense.update(result)
    # every marked chunk's vector is <from>'s (nothing is embedded), however many runs it took
    dense["reuse"] = {"from_generation": reuse.generation, "from_root": str(reuse.root),
                      "collection": reuse.collection, "reused": indexed, "embedded": 0}
    log(f"dense: {dense['points']} points in {collection}, {dense['reuse']['reused']} copied from "
        f"{reuse.collection}, 0 embedded")


# ---------------------------------------------------------------- main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="source", required=True, help="the built root: a generation id or a path")
    ap.add_argument("--to", dest="target", required=True, help="the new root: a generation id or a path")
    ap.add_argument("--set", action="append", default=[], metavar="CONFIG.KEY=VALUE",
                    help="config overlay change, repeatable (e.g. retrieval.reranker.candidates=20)")
    ap.add_argument("--rerank-url", help="turn the reranker on at this URL (measured defaults: top 10, 15 s)")
    ap.add_argument("--resume", action="store_true", help="continue a derivation this script started")
    ap.add_argument("--dense-clone", choices=("copy", "snapshot"), default="copy",
                    help="how the derived root's collection is made: copy (default) upserts every vector (a fresh "
                         "HNSW graph); snapshot has Qdrant recover a snapshot of <from>'s collection (its graph: the "
                         "same dense searches), checked")
    ap.add_argument("--qdrant-snapshots-dir", default=br.DEFAULT_SNAPSHOTS_DIR, metavar="DIR",
                    help="--dense-clone snapshot: the Qdrant server's snapshots_path, where it writes "
                         f"<collection>/<snapshot> (default: {br.DEFAULT_SNAPSHOTS_DIR}, the qdrant/qdrant image's)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        state = preflight(args)
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - e.g. Qdrant unreachable while checking the collections
        log(f"FAILED before deriving: {type(exc).__name__}: {exc}")
        return 1
    state["rerank_url"] = args.rerank_url
    state["dense_clone"], state["snapshots_dir"] = args.dense_clone, args.qdrant_snapshots_dir
    source, target = state["source"], state["target"]
    target.mkdir(parents=True, exist_ok=True)

    built = state["report"]
    report = state["mine"] or {}
    report.update({
        "builder": DERIVER,
        "derived": True,
        "generation": state["generation"],
        "root": str(target),
        "status": "running",
        "started_at": report.get("started_at") or now(),
        "updated_at": now(),
        "runs": report.get("runs", 0) + 1,
        "derived_from": {
            "generation": built.get("generation") or source.name,
            "root": str(source),
            "builder": built.get("builder"),
            "collection": (built.get("dense") or {}).get("collection"),
            "code_commit": built.get("code_commit"),
        },
        **{key: built[key] for key in BUILD_FACTS if key in built},
    })
    with contextlib.suppress(Exception):
        report["derive_code_commit"] = br.git(br.REPO, "rev-parse", "HEAD")
    if not state["dense"]:
        report["dense"] = {"skipped": "the source root serves no dense index"}
    report_path = target / REPORT_NAME
    steps = report.setdefault("steps", {})
    failures: list[dict[str, str]] = []
    log(f"derive_root: {state['generation']} from {source}")

    def write() -> None:
        report["updated_at"] = now()
        write_json(report_path, report)

    def run(name: str, fn: Callable[[], Any]) -> None:
        started = time.monotonic()
        try:
            fn()
        finally:
            steps[name] = {"seconds": round(time.monotonic() - started, 1), "at": now()}
            write()

    try:
        write()
        run("clone", lambda: step_clone(state, report))
        run("structure", lambda: step_structure(state, report))
        run("config", lambda: step_config(state, report))
        if state["dense"]:
            run("dense", lambda: step_dense(state, report, write))
    except Exception as exc:  # noqa: BLE001 - reported; a rerun with --resume continues
        failures.append({"step": "derive", "error": f"{type(exc).__name__}: {exc}"})
        log(f"FAILED: {type(exc).__name__}: {exc}")

    report["failed_steps"] = failures
    report["status"] = "incomplete" if failures else "complete"
    report["finished_at"] = now()
    write()
    log(f"derive_root: {report['status']}; report {report_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
