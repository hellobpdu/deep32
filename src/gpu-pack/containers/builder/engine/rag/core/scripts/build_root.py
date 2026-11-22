#!/usr/bin/env python3
"""Build a new, self-contained RAG root (one index generation) from the corpus package.

usage: build_root.py --corpus-ref refs/heads/corpus-v1
                     --root /var/lib/deep32/rag-data/<generation>
                     [--corpus-repo PATH] [--generation ID] [--no-dense] [--resume] [--rerank-url URL]
                     [--reuse-dense-from <generation or root path> [--reuse-key id|text]] [--table-header-repeat]
                     [--opt-in-doc-types TYPE[,TYPE...]] [--dense-base upsert|snapshot [--qdrant-snapshots-dir DIR]]

Steps, each safe to repeat; progress lines go to stdout:
  1. sources    git archive the corpus commit into <root>/sources
  2. config     the root's overlay <root>/config/{dense,retrieval,serving}.json (rag_core.config): its own
                Qdrant collection rag_dense_<generation>, retrievers bm25 + dense (bm25 only with --no-dense),
                the technical query variant on bm25 only, rerank settings, admission 2, the generation id;
                with --opt-in-doc-types, retrieval.opt_in_doc_types (rag_core.api_v2: doc types that
                searches and grep leave out unless doc_type or docs names them; unset otherwise). A --resume
                run must give the doc types of the run it resumes, in any order (none if it gave none)
  3. documents  per manifest entry: text adapter -> ingest -> title and metadata from manifest.json ->
                harden -> chunk (policy v3). A document already chunked is skipped, so a rerun resumes.
                The report's toc_dropped counts the table-of-contents units the chunker left out (v3.2).
                --table-header-repeat turns on chunking.table_header_repeat (rag_core.chunker: a table
                split across chunks repeats its caption line and header row at the start of each
                continuation chunk); the report's chunker says so. The build's own setting is used, never
                the chunking config, and a --resume run must use the setting of the run it resumes
  4. bm25       the FTS5 index is filled by the chunk triggers; integrity-check and optimize it. With
                --opt-in-doc-types the chunks of those doc types are indexed in a table of their own,
                chunk_fts_optin, and chunk_fts holds exactly the other chunks (rag_core.db.split_optin_fts; the
                chunk triggers then route each chunk to its table): a default search ranks over the rows, and with
                the BM25 statistics, of a root built without those documents. Redone on every run; the report's
                bm25.tables counts the rows of each table and bm25.sqlite names the SQLite. Each table is checked
                with FTS5's check of the index alone (an SQLite without one: bm25.fts_integrity "skipped", rows
                checked)
  5. structure  rag_core.structure.index_root: each document's sections with their line ranges in
                sources/, and each chunk's span (the /v2 reading endpoints and the KB need them);
                unchanged documents are skipped
  6. dense      embed and upsert per document, in batches with retry and backoff; documents already
                indexed are skipped (not with --no-dense; a later --resume run adds dense).
                --reuse-dense-from <generation>: a chunk whose id and embedding text are the same in that
                generation (a sibling directory of --root, or a root path) takes its vector from that
                generation's Qdrant collection (fetched by point id per document, read-only; the point's payload
                must name the same chunk and document) instead of TEI; only new or changed chunks are
                embedded. Progress lines and the report count reused and embedded chunks. The step ends when
                Qdrant has indexed the collection (status green, up to 20 min; report dense.index).
                --reuse-key text (with --reuse-dense-from; default id, the above): a chunk whose id changed but
                whose embedding text that generation embedded also takes that vector (DenseSource.by_text), under
                its new id; the report's dense.reuse says key "text" and counts reused_by_text, and a --resume run
                must give the same key
                --dense-base snapshot (with --reuse-dense-from; default upsert, the above): the collection starts as
                Qdrant's recovery of a snapshot of that generation's collection (its segments, HNSW graphs included,
                checked against it: restore_from_snapshot), its points that no chunk of this build can reuse are
                deleted (prune_base), and only new or changed chunks are written (index_dense_on_base): a reused
                point is left untouched in the recovered graph, so the new root searches as that generation does
                except where the corpus changed. The final points are the upsert path's, id for id and vector for
                vector (checked). The report's dense.base says so; a --resume run must use the base of the run that
                started the step
  7. kb         rag_core.kb.build_kb(<root>/sources/kb, <root>/kb/kb.sqlite); CLI and feature rows
                resolve (doc_key, line) to section ids through step 5
  8. report     <root>/BUILD_REPORT.json, rewritten after every step

Guards: it never writes to $RAG_ROOT (the served root) or anything inside or above it, never to a
non-empty directory that holds no BUILD_REPORT.json of its own, never to a shared or pre-existing Qdrant
collection, and a --resume run must use the same corpus commit and generation as the run it resumes. The
--reuse-dense-from generation is only read (its rag.db opened read-only, its points fetched; with --dense-base
snapshot Qdrant also writes a snapshot file of its collection, which the step deletes); it may be the served one,
and it must be a complete build with a dense index of the same embedding revision (and, for a snapshot, green).
The generation id and the root directory name must both match GENERATION_RE, the name part of the
managed RAG_ROOT pattern, so that a complete root can be promoted.
RAG_DENSE_COLLECTION is forced to the root's collection for this process.
Exit status: 0 complete, 1 incomplete (see "failed" in the report), 2 refused.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import random
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlsplit

from qdrant_client import QdrantClient, models

from rag_core.canonical import harden_document
from rag_core.config import load_config
from rag_core.chunker import CHUNKER_VERSIONS, chunk_document
from rag_core.corpus import (
    apply_manifest_entry,
    corpus_fingerprint,
    display_title,
    load_manifest,
    text_kind,
)
from rag_core.db import (
    DEFAULT_FTS,
    INDEX_CHECK_SQL,
    OPTIN_FTS,
    check_optin_split,
    connect,
    fts5_checks_index_alone,
    initialize,
    split_optin_fts,
)
from rag_core.dense import (
    UPSERT_BATCH_POINTS,
    DenseConfig,
    JinaEmbeddingClient,
    batched,
    delete_doc_points,
    ensure_collection,
    load_chunks,
    load_dense_config,
    make_qdrant_client,
    mark_dense_indexed,
    payload_for_chunk,
    point_id_for_chunk,
    reset_dense_state,
)
from rag_core.errors import error_kind
from rag_core.identity import sha256_file
from rag_core.ingest import ingest_pdf
from rag_core.kb import build_kb
from rag_core.paths import RAGPaths
from rag_core.retrievers.dense import qdrant_filter
from rag_core.structure import has_structure, index_root
from rag_core.text_adapter import adapt


REPO = Path(__file__).resolve().parents[1]
BUILDER = "scripts/build_root.py"
REPORT_NAME = "BUILD_REPORT.json"

# the name part of the managed RAG_ROOT pattern (deploy.json, runbook a1): a root built
# under another name could never be promoted with env-set
GENERATION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
SHARED_COLLECTIONS = frozenset({"rag_dense_v1"})
CHUNKER_POLICY = "v3"
DONE_STATUSES = ("CHUNKED", "INDEXING", "READY")

ROOT_SERVING = {
    "max_concurrent_searches": 2,
    "search_queue_timeout_seconds": 30,
}
ROOT_DENSE_QUERY_TIMEOUT_SECONDS = 1.5
ROOT_BREAKER = {"failures": 3, "open_seconds": 30}
RERANK_CANDIDATES = 10
RERANK_TIMEOUT_SECONDS = 15
DOC_TYPE_MAX_CHARS = 32         # rag_core.api_v2.Filters.doc_type

DENSE_ATTEMPTS = 5
BACKOFF_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

# after the dense step: how long to wait for Qdrant to finish indexing the collection, polled this often
INDEXED_WAIT_SECONDS = 1200.0
INDEXED_POLL_SECONDS = 2.0

# --dense-base (this script) and derive_root's --dense-clone: a collection made by Qdrant from a snapshot of another
# (restore_from_snapshot). Qdrant 1.19 (vendor documentation: qdrant.tech/documentation/snapshots/ and
# api.qdrant.tech/v-1-19-x/api-reference/snapshots): POST /collections/{c}/snapshots?wait=true writes a .snapshot file
# into the server's snapshots_path, "/qdrant/snapshots when using our Docker image"; PUT
# /collections/{target}/snapshots/recover?wait=true {"location": "file:///...", "priority": "snapshot", "checksum":
# sha256} makes the target ("if the target collection does not exist, it will be created"; priority snapshot: "the
# snapshot will be used as a source of truth"); DELETE /collections/{c}/snapshots/{name}?wait=true deletes it. Observed
# on 1.19.1: the file is <snapshots_path>/<collection>/<name> with a .checksum beside it, and a
# file:// location outside snapshots_path is refused (403 "must be inside the snapshots directory")
DENSE_BASES = ("upsert", "snapshot")
DEFAULT_SNAPSHOTS_DIR = "/qdrant/snapshots"
SNAPSHOT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.snapshot$")
SNAPSHOT_TIMEOUT_SECONDS = 3600         # a wait=true snapshot or recovery request lasts as long as the work
CLONE_CHECK_SAMPLE = 64                 # seeded stored vectors searched in both collections after a recovery
CLONE_CHECK_LIMIT = 50
CLONE_CHECK_SEED = 20261102
SCROLL_PAGE = 2048
DELETE_BATCH_POINTS = 1024

T0 = time.monotonic()


class Refused(Exception):
    """The build would touch something it must not. Raised only by preflight(), before any write."""


def log(message: str) -> None:
    print(f"[{time.monotonic() - T0:8.1f}s] {message}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    """Atomic: a reader (or a crash) never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def collection_for(generation: str) -> str:
    return "rag_dense_" + re.sub(r"[^A-Za-z0-9_-]", "_", generation)


# ---------------------------------------------------------------- guards


def check_not_served(root: Path) -> None:
    """Never the served root, nothing inside or above it. The journal names the RAG_ROOT compared against, or
    warns that the guard is inactive: deployctl passes a job only its job_env_keys, so a job may run without it."""
    served = os.environ.get("RAG_ROOT")
    if not served:
        log("WARNING: RAG_ROOT is not set here, so the served-root guard is inactive; "
            "make sure --root is not the root deep32-engine serves")
        return
    served_path = Path(served).expanduser().resolve()
    log(f"served-root guard: RAG_ROOT is {served_path}")
    if root == served_path or served_path in root.parents or root in served_path.parents:
        raise Refused(f"{root} overlaps RAG_ROOT {served_path}, the served root")


def open_report(root: Path, resume: bool) -> dict[str, Any]:
    path = root / REPORT_NAME
    if path.is_file():
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("builder") != BUILDER:
            raise Refused(f"{path} was not written by {BUILDER}")
        if not resume:
            raise Refused(f"{root} already holds a build ({report.get('status')}); pass --resume to continue it")
        return report
    if root.exists() and any(root.iterdir()):
        raise Refused(f"{root} is not empty and holds no {REPORT_NAME}: not a root this script started")
    return {}


class DenseSource:
    """An earlier generation's dense index, read-only: its rag.db (opened read-only) says which chunks were
    embedded from which text by which model, and its Qdrant collection holds the vectors. A chunk of the new
    build reuses a vector only when the chunk id and the embedding text are the same (the text carries the
    document title from the manifest, which the chunk id does not), the source is not mixing models, and the
    vector has the configured size and is finite.

    With --reuse-key text (vectors(key="text")), a chunk that does not qualify by its id is looked up by the hash of
    its embedding text among all the source's dense-indexed chunks: a chunk id hashes its ordinal, so one passage
    inserted early in a document changes every later id although the texts are unchanged. The match is exact for
    the vector because the source holds one embedding model and the revision this build writes (checked before
    the build starts); the point fetched must still name that source chunk and its document."""

    PAGE = 256

    def __init__(self, root: Path, collection: str, client, vector_size: int, generation: str | None = None):
        self.root = root
        self.generation = generation or root.name
        self.collection = collection
        self.client = client
        self.vector_size = vector_size
        self._by_text: dict[bytes, tuple[str, str]] | None = None
        self.reused_by_text = 0     # vectors handed out by the text key, over this source's life
        db_path = root / "sqlite" / "rag.db"
        self.db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30,
                                  check_same_thread=False)
        seen = self.db.execute(
            "SELECT DISTINCT embedding_model, embedding_revision FROM index_state WHERE dense_indexed = 1"
        ).fetchall()
        if len(seen) > 1:
            self.db.close()
            raise ValueError(f"{self.generation} mixes embedding models or revisions: {sorted(map(str, seen))}")
        self.model, self.revision = seen[0] if seen else (None, None)

    def close(self) -> None:
        self.db.close()

    def texts(self, doc_id: str) -> dict[str, str]:
        """chunk id -> embedding text of this document's chunks that are dense-indexed in the source."""
        return dict(self.db.execute(
            """
            SELECT c.chunk_id, c.embedding_text
            FROM chunks AS c JOIN index_state AS s ON s.chunk_id = c.chunk_id
            WHERE c.doc_id = ? AND s.dense_indexed = 1
            """,
            (doc_id,),
        ).fetchall())

    def by_text(self) -> dict[bytes, tuple[str, str]]:
        """sha256 of the stripped embedding text -> (chunk id, doc id) of the source's dense-indexed chunks, the first
        of equal texts (read once, on first use)."""
        if self._by_text is None:
            table: dict[bytes, tuple[str, str]] = {}
            for chunk_id, doc_id, text in self.db.execute(
                    "SELECT c.chunk_id, c.doc_id, c.embedding_text FROM chunks AS c "
                    "JOIN index_state AS s ON s.chunk_id = c.chunk_id WHERE s.dense_indexed = 1"):
                table.setdefault(hashlib.sha256(str(text or "").strip().encode("utf-8", "surrogatepass")).digest(),
                                 (chunk_id, doc_id))
            self._by_text = table
        return self._by_text

    def vectors(self, doc_id: str, chunks: list[dict[str, Any]], key: str = "id") -> dict[str, list[float]]:
        """Vectors for the chunks of `chunks` that can reuse one, by chunk id: the source collection's points
        fetched by their ids (point_id_for_chunk, as every build writes them), read-only. A point counts only when
        its payload names the same chunk and document, so a stray point that carries the chunk id under another
        id, or a point whose payload names another chunk, is never copied. With key "text", a chunk the id rule
        leaves out takes the vector of the source chunk with the same stripped embedding text (by_text), under the
        same payload check against that source chunk."""
        known = self.texts(doc_id)
        wanted = {point_id_for_chunk(c["chunk_id"]): (c["chunk_id"], c["chunk_id"], doc_id)
                  for c in chunks if known.get(c["chunk_id"]) == c["embedding_text"]}
        if key == "text":
            taken = {chunk_id for chunk_id, _, _ in wanted.values()}
            table = self.by_text()
            for c in chunks:
                if c["chunk_id"] in taken:
                    continue
                text = str(c["embedding_text"] or "").strip().encode("utf-8", "surrogatepass")
                match = table.get(hashlib.sha256(text).digest())
                if match is not None:
                    source_id, source_doc = match
                    wanted.setdefault(point_id_for_chunk(source_id), (c["chunk_id"], source_id, source_doc))
        if not wanted:
            return {}
        found: dict[str, list[float]] = {}
        ids = list(wanted)
        for start in range(0, len(ids), self.PAGE):
            points = self.client.retrieve(
                collection_name=self.collection, ids=ids[start:start + self.PAGE],
                with_payload=["chunk_id", "doc_id"], with_vectors=True,
            )
            for point in points:
                chunk_id, source_id, source_doc = wanted.get(canonical_point_id(point.id), (None, None, None))
                payload = point.payload or {}
                vector = point.vector
                if (chunk_id is not None and payload.get("chunk_id") == source_id
                        and payload.get("doc_id") == source_doc
                        and isinstance(vector, list) and len(vector) == self.vector_size
                        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)):
                    found[chunk_id] = [float(v) for v in vector]
                    if source_id != chunk_id:
                        self.reused_by_text += 1
        return found


def canonical_point_id(point_id: Any) -> str:
    """A Qdrant point id as point_id_for_chunk spells it (a UUID in lower-case hyphenated form)."""
    try:
        return str(uuid.UUID(str(point_id)))
    except ValueError:
        return str(point_id)


def resolve_dense_source(value: str, root: Path) -> Path:
    """A generation id names a sibling of the new root (rag-data/<generation>); anything with a slash is a path."""
    if "/" in value or os.sep in value:
        return Path(value).expanduser().resolve()
    if not GENERATION_RE.fullmatch(value):
        raise Refused(f"--reuse-dense-from {value!r}: not a generation id ({GENERATION_RE.pattern}) or a path")
    return (root.parent / value).resolve()


def dense_source_settings(source: Path, root: Path) -> tuple[str, str]:
    """(generation, collection) of a root whose dense index may be reused; Refused unless it is a complete
    build (or derivation) with a dense index, other than the new root."""
    if source == root:
        raise Refused("--reuse-dense-from names the root being built")
    if not (source / "sqlite" / "rag.db").is_file():
        raise Refused(f"--reuse-dense-from {source}: no sqlite/rag.db there")
    try:
        report = json.loads((source / REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"--reuse-dense-from {source}: no readable {REPORT_NAME} ({type(exc).__name__})") from None
    dense = report.get("dense") or {}
    if report.get("status") != "complete" or report.get("failed_steps"):
        raise Refused(f"--reuse-dense-from {source}: its build is {report.get('status')!r}, not complete")
    if dense.get("skipped") or not dense.get("collection"):
        raise Refused(f"--reuse-dense-from {source}: it has no dense index")
    try:
        overlay = json.loads((source / "config" / "dense.json").read_text(encoding="utf-8")).get("collection")
    except (OSError, ValueError):
        overlay = None
    if overlay not in (None, dense["collection"]):
        raise Refused(f"--reuse-dense-from {source}: its config names {overlay}, its report {dense['collection']}")
    return str(report.get("generation") or source.name), str(dense["collection"])


def check_collection(collection: str) -> None:
    served = {
        load_dense_config(REPO / "config" / "dense.json").collection,
        os.environ.get("RAG_DENSE_COLLECTION"),
        *SHARED_COLLECTIONS,
    }
    if collection in served:
        raise Refused(f"collection {collection} is a served or shared collection; choose another generation id")


def opt_in_doc_types(value: str | None) -> list[str] | None:
    """--opt-in-doc-types TYPE[,TYPE...] -> the doc types, in the order given, once each; None when not given."""
    if value is None:
        return None
    types = [t.strip() for t in value.split(",")]
    if not types or not all(types) or any(len(t) > DOC_TYPE_MAX_CHARS for t in types):
        raise Refused(f"--opt-in-doc-types {value!r}: expected doc types separated by commas, e.g. powershell,system")
    return list(dict.fromkeys(types))


def preflight(args: argparse.Namespace, root: Path, generation: str, collection: str) -> tuple[dict, str]:
    """Every refusal happens here, before anything is written. Returns (report so far, corpus commit)."""
    if args.reuse_dense_from and args.no_dense:
        raise Refused("--reuse-dense-from needs dense indexing; it cannot go with --no-dense")
    reuse_key = getattr(args, "reuse_key", "id")
    if reuse_key == "text" and not args.reuse_dense_from:
        raise Refused("--reuse-key text reuses the vectors of the --reuse-dense-from generation; give one")
    if args.dense_base == "snapshot":
        if not args.reuse_dense_from:
            raise Refused("--dense-base snapshot starts the collection from the --reuse-dense-from generation's; "
                          "give one")
        if not PurePosixPath(args.qdrant_snapshots_dir).is_absolute():
            raise Refused(f"--qdrant-snapshots-dir {args.qdrant_snapshots_dir!r} is not an absolute path (the Qdrant "
                          "server's snapshots_path)")
    opt_in_doc_types(args.opt_in_doc_types)
    if not GENERATION_RE.fullmatch(generation):
        raise Refused(f"generation id {generation!r} must match {GENERATION_RE.pattern}")
    if not GENERATION_RE.fullmatch(root.name):
        raise Refused(f"root directory name {root.name!r} must match {GENERATION_RE.pattern}")
    check_not_served(root)
    check_collection(collection)
    if args.rerank_url is not None:
        parts = urlsplit(args.rerank_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Refused(f"--rerank-url {args.rerank_url!r} is not an http(s) URL")
    report = open_report(root, args.resume)
    if report.get("generation") not in (None, generation):
        raise Refused(f"{root} holds generation {report['generation']}, not {generation}")
    chunked_with = bool((report.get("chunker") or {}).get("table_header_repeat"))
    if report and chunked_with != args.table_header_repeat:
        raise Refused(f"this root is chunked {'with' if chunked_with else 'without'} --table-header-repeat; "
                      "resume it the same way or build a new generation")
    # the config step rewrites the overlay on every run: a resume that left the flag out would drop the key
    configured = report.get("config")
    built_opt_in = (configured or {}).get("opt_in_doc_types")
    if configured is not None and set(built_opt_in or ()) != set(opt_in_doc_types(args.opt_in_doc_types) or ()):
        raise Refused(f"this root is configured with "
                      f"{'--opt-in-doc-types ' + ','.join(built_opt_in) if built_opt_in else 'no --opt-in-doc-types'}"
                      "; resume it the same way or build a new generation")
    # the collection a started dense step made is the base's: upserted, or recovered from a snapshot
    dense_started = report.get("dense") or {}
    based_on = (dense_started.get("base") or {}).get("mode", "upsert")
    if dense_started.get("collection_owned") and based_on != args.dense_base:
        raise Refused(f"this root's dense step started with --dense-base {based_on}; resume it the same way or build "
                      "a new generation")
    keyed_on = (dense_started.get("reuse") or {}).get("key", "id")
    if dense_started.get("collection_owned") and keyed_on != reuse_key:
        raise Refused(f"this root's dense step started with --reuse-key {keyed_on}; resume it the same way or build "
                      "a new generation")
    try:
        commit = git(Path(args.corpus_repo), "rev-parse", "--verify", f"{args.corpus_ref}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise Refused(f"cannot resolve {args.corpus_ref} in {args.corpus_repo}: {exc.stderr.strip()}") from None
    built_from = report.get("corpus", {}).get("commit")
    if built_from not in (None, commit):
        raise Refused(f"{args.corpus_ref} is now {commit[:12]}, but this root was built from "
                      f"{built_from[:12]}; build a new generation instead")
    if not args.no_dense and not report.get("dense", {}).get("collection_owned"):
        # the overlay is not written yet; ask Qdrant under the name the build will use
        config = load_dense_config(REPO / "config" / "dense.json")
        client = make_qdrant_client(DenseConfig(**{**config.__dict__, "collection": collection}))
        try:
            if client.collection_exists(collection_name=collection):
                raise Refused(f"Qdrant collection {collection} already exists and this build did not create it")
        finally:
            client.close()
    if args.reuse_dense_from:
        check_dense_source(args, root, collection)
    return report, commit


def check_dense_source(args: argparse.Namespace, root: Path, collection: str) -> None:
    """--reuse-dense-from: a complete generation with a dense index, another collection, one that exists with
    the configured vector size, and the embedding revision this build will write."""
    if args.no_dense:
        raise Refused("--reuse-dense-from needs dense indexing; it cannot go with --no-dense")
    source = resolve_dense_source(args.reuse_dense_from, root)
    generation, source_collection = dense_source_settings(source, root)
    if source_collection == collection:
        raise Refused(f"--reuse-dense-from {generation} uses collection {collection}, the one this build writes")
    config = load_dense_config(REPO / "config" / "dense.json")
    client = make_qdrant_client(DenseConfig(**{**config.__dict__, "collection": source_collection}))
    try:
        if not client.collection_exists(collection_name=source_collection):
            raise Refused(f"--reuse-dense-from {generation}: Qdrant has no collection {source_collection}")
        size = client.get_collection(collection_name=source_collection).config.params.vectors.size
        if int(size) != config.vector_size:
            raise Refused(f"--reuse-dense-from {generation}: {source_collection} holds {size}-d vectors, "
                          f"this build embeds {config.vector_size}-d")
        dense = DenseSource(source, source_collection, client, config.vector_size, generation)
        try:
            if dense.revision != config.embedding_revision:
                raise Refused(f"--reuse-dense-from {generation}: embedding revision {dense.revision!r}, "
                              f"this build writes {config.embedding_revision!r}")
        finally:
            dense.close()
        if getattr(args, "dense_base", "upsert") == "snapshot":
            status = collection_status(client, source_collection)
            if status != "green":
                raise Refused(f"--dense-base snapshot: {source_collection} is {status}, not green (Qdrant is still "
                              "optimizing it, and a snapshot of it would not search as it will)")
    except ValueError as exc:
        raise Refused(f"--reuse-dense-from {generation}: {exc}") from None
    finally:
        client.close()


# ---------------------------------------------------------------- steps


def step_sources(report: dict, root: Path, repo: Path, ref: str, commit: str) -> list[dict[str, Any]]:
    corpus = report.setdefault("corpus", {})
    sources = root / "sources"
    if corpus.get("commit") == commit and sources.is_dir():
        log(f"sources: {commit[:12]} already archived")
    else:
        tmp = root / "sources.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sources, ignore_errors=True)
        tmp.mkdir(parents=True)
        proc = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", commit], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(tmp, filter="data")
        if proc.wait() != 0:
            raise RuntimeError(f"git archive {commit[:12]} failed with status {proc.returncode}")
        os.replace(tmp, sources)
        log(f"sources: archived {ref} at {commit[:12]}")

    manifest_path = sources / "manifest.json"
    entries = load_manifest(manifest_path)
    corpus.update({
        "ref": ref,
        "commit": commit,
        "manifest_sha256": sha256_file(manifest_path),
        "documents": len(entries),
        "manifest_fingerprint": corpus_fingerprint(e["sha256"] for e in entries),
    })
    return entries


def step_config(report: dict, root: Path, generation: str, collection: str,
                with_dense: bool, rerank_url: str | None, opt_in: list[str] | None = None) -> None:
    """`opt_in`: retrieval.opt_in_doc_types (rag_core.api_v2), written only when given."""
    retrievers = ["bm25", "dense"] if with_dense else ["bm25"]
    overlay = {
        "dense": {
            "collection": collection,
            "query_timeout_seconds": ROOT_DENSE_QUERY_TIMEOUT_SECONDS,
        },
        "retrieval": {
            "retrievers": retrievers,
            "retriever_weights": {name: 1.0 for name in retrievers},
            # a bag of identifiers is a poor semantic query (measured arm bd-tb)
            "technical_variant_retrievers": ["bm25"],
            "dense_breaker": ROOT_BREAKER,
            "reranker": {
                "enabled": rerank_url is not None,
                "url": rerank_url,
                "candidates": RERANK_CANDIDATES,
                "timeout_seconds": RERANK_TIMEOUT_SECONDS,
                "breaker": ROOT_BREAKER,
            },
        },
        "serving": {"generation": generation, **ROOT_SERVING},
    }
    if opt_in is not None:
        overlay["retrieval"]["opt_in_doc_types"] = opt_in
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, data in overlay.items():
        write_json(config_dir / f"{name}.json", data)
    report["config"] = {
        "collection": collection,
        "retrievers": retrievers,
        "reranker": rerank_url is not None,
        **({"opt_in_doc_types": opt_in} if opt_in is not None else {}),
        "files": sorted(f"config/{name}.json" for name in overlay),
    }
    log(f"config: overlay written (collection {collection}, retrievers {'+'.join(retrievers)}, "
        f"rerank {'on' if rerank_url else 'off'}"
        f"{', opt-in doc types ' + ','.join(opt_in) if opt_in else ''})")


def prepare_schema(paths: RAGPaths) -> Path:
    db_path = initialize(paths)
    # harden_document needs the 17D document columns, which initialize() does not create
    with contextlib.redirect_stdout(io.StringIO()):
        runpy.run_path(str(REPO / "scripts" / "migrate_17d.py"), run_name="__main__")
    return db_path


def chunker_facts(table_header_repeat: bool) -> dict[str, Any]:
    """The report's "chunker": the policy and its version, and table_header_repeat when it is on."""
    facts: dict[str, Any] = {"policy": CHUNKER_POLICY, "version": CHUNKER_VERSIONS[CHUNKER_POLICY]}
    if table_header_repeat:
        facts["table_header_repeat"] = True
    return facts


def toc_dropped(root: Path, doc_keys: list[Any]) -> dict[str, Any]:
    """The report's "toc_dropped": the table-of-contents units the chunker left out (chunker v3.2,
    rag_core.chunker.drop_toc_sections), from each document's chunk manifest, so a resumed build
    counts the documents an earlier run chunked: {"documents", "units", "by_doc": {doc_key: units}}."""
    by_doc: dict[str, int] = {}
    for doc_id, doc_key in doc_keys:
        try:
            manifest = json.loads((root / "metadata" / doc_id / "chunks.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        units = manifest.get("toc_units_dropped") or 0
        if units:
            by_doc[doc_key or doc_id] = units
    return {"documents": len(by_doc), "units": sum(by_doc.values()), "by_doc": by_doc}


def step_documents(report: dict, root: Path, db_path: Path, entries: list[dict[str, Any]],
                   table_header_repeat: bool = False) -> None:
    sources = root / "sources"
    with connect(db_path) as conn:
        known = {
            row["sha256"]: dict(row)
            for row in conn.execute("SELECT doc_id, sha256, source_id, status FROM documents")
        }
    counts = {"total": len(entries), "built_this_run": 0, "already_done": 0}
    failed: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    claimed: dict[str, str] = {}

    for index, entry in enumerate(entries, 1):
        key = entry["doc_key"]
        try:
            source = sources / entry["path"]
            digest = sha256_file(source)
            if digest != entry["sha256"]:
                raise ValueError(f"sha256 of {entry['path']} is {digest[:12]}, manifest says {entry['sha256'][:12]}")
            row = known.get(digest)
            owner = claimed.get(digest) or (row or {}).get("source_id")
            if owner not in (None, key):
                duplicates.append({"doc_key": key, "same_content_as": owner})
                log(f"doc {index}/{len(entries)} {key}: same content as {owner}, skipped")
                continue
            claimed[digest] = key

            if row is not None and row["status"] in DONE_STATUSES:
                apply_manifest_entry(db_path, row["doc_id"], entry)
                counts["already_done"] += 1
                continue

            if row is None:
                work = root / "work" / key
                shutil.rmtree(work, ignore_errors=True)
                text = source.read_text(encoding="utf-8", errors="replace")
                adapt(text_kind(entry), text, key, work, display_title(entry))
                parts = PurePosixPath(entry["path"]).parts
                result = ingest_pdf(
                    source, work,
                    source_name=parts[0] if len(parts) > 1 else entry["doc_type"],
                    source_uri=entry.get("source_url"),
                )
                shutil.rmtree(work, ignore_errors=True)
                doc_id = result["doc_id"]
            else:
                doc_id = row["doc_id"]          # ingested by an earlier run that stopped before chunking

            apply_manifest_entry(db_path, doc_id, entry)
            harden_document(doc_id)
            chunked = chunk_document(doc_id, policy=CHUNKER_POLICY, table_header_repeat=table_header_repeat)
            known[digest] = {"doc_id": doc_id, "sha256": digest, "source_id": key, "status": "CHUNKED"}
            counts["built_this_run"] += 1
            log(f"doc {index}/{len(entries)} {key}: {chunked['chunks']} chunks")
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the others
            failed.append({"doc_key": key, "error": f"{type(exc).__name__}: {exc}"})
            log(f"doc {index}/{len(entries)} {key}: FAILED {type(exc).__name__}: {exc}")

    with contextlib.suppress(OSError):
        (root / "work").rmdir()             # left only if a document failed after its adapter ran
    with connect(db_path) as conn:
        shas = [row[0] for row in conn.execute("SELECT sha256 FROM documents")]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        doc_keys = list(conn.execute("SELECT doc_id, source_id FROM documents ORDER BY source_id"))
    report["documents"] = {**counts, "failed": failed, "duplicates": duplicates}
    report["chunks"] = chunks
    report["chunker"] = chunker_facts(table_header_repeat)
    report["toc_dropped"] = toc_dropped(root, doc_keys)
    report["corpus"]["fingerprint"] = corpus_fingerprint(shas) if shas else None
    log(f"documents: {counts['built_this_run']} built, {counts['already_done']} already done, {len(failed)} failed, "
        f"{len(duplicates)} duplicates; {chunks} chunks")


# the documents of these doc types (as rag_core.structure names a document's doc type: its manifest entry's,
# else its source name)
OPT_IN_DOCS_SQL = """
SELECT doc_id FROM documents
WHERE COALESCE(NULLIF(json_extract(metadata_json, '$.corpus.doc_type'), ''), source_name) IN ({marks})
"""


def step_bm25(report: dict, db_path: Path, opt_in: list[str] | None = None) -> None:
    """`opt_in` (--opt-in-doc-types): the chunks of those doc types go to a BM25 table of their own,
    chunk_fts_optin, and chunk_fts holds exactly the others (rag_core.db.split_optin_fts), redone on every run so
    that a resume which added or re-chunked documents is split too; the report counts each table's rows.

    Each table of the split indexes part of chunks, so it is checked with FTS5's check of the index alone
    (rag_core.db.INDEX_CHECK_SQL, never one that compares it with chunks) and check_optin_split proves its rows; an
    SQLite that compares every check with the content table (fts5_checks_index_alone) gets the row proof alone, and
    the report says so. The report names the SQLite, so a gate on the split can be reproduced."""
    with connect(db_path) as conn:
        tables = None
        if opt_in is None:
            # raises SQLITE_CORRUPT_VTAB if the FTS index is corrupt
            conn.execute(f"INSERT INTO {DEFAULT_FTS}({DEFAULT_FTS}) VALUES ('integrity-check')")
            conn.execute(f"INSERT INTO {DEFAULT_FTS}({DEFAULT_FTS}) VALUES ('optimize')")
        else:
            doc_ids = [row[0] for row in conn.execute(
                OPT_IN_DOCS_SQL.format(marks=",".join("?" for _ in opt_in)), opt_in)]
            split_optin_fts(conn, doc_ids)
            index_check = fts5_checks_index_alone()
            for table in (DEFAULT_FTS, OPTIN_FTS):
                if index_check:
                    conn.execute(INDEX_CHECK_SQL.format(table=table))       # SQLITE_CORRUPT_VTAB if corrupt
                conn.execute(f"INSERT INTO {table}({table}) VALUES ('optimize')")
        conn.commit()
        if opt_in is not None:
            tables = check_optin_split(conn)        # each table exactly the chunks of its documents
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    report["bm25"] = {"fts_integrity": "ok", "chunks": chunks}
    if tables is None:
        log(f"bm25: FTS5 index checked and optimized ({chunks} chunks)")
        return
    report["bm25"].update({"tables": tables, "opt_in_documents": len(doc_ids), "sqlite": sqlite3.sqlite_version})
    if not index_check:
        report["bm25"]["fts_integrity"] = "skipped"
        report["bm25"]["fts_integrity_skipped"] = (
            f"SQLite {sqlite3.sqlite_version} compares an FTS5 index with all of chunks, of which each table holds "
            "a part; check_optin_split proved each table's rows")
        log(f"bm25: WARNING {report['bm25']['fts_integrity_skipped']}")
    log(f"bm25: FTS5 indexes {'checked and ' if index_check else ''}optimized ({chunks} chunks: "
        f"{tables[DEFAULT_FTS]} in {DEFAULT_FTS}, {tables[OPTIN_FTS]} of {len(doc_ids)} opt-in documents in "
        f"{OPTIN_FTS})")


def step_structure(report: dict, root: Path, db_path: Path) -> None:
    counts = index_root(RAGPaths(root), log=log)
    with connect(db_path) as conn:
        documents = conn.execute("SELECT COUNT(*) FROM doc_sources").fetchone()[0]
        sections = conn.execute("SELECT COUNT(*) FROM doc_sections").fetchone()[0]
        spans, located = conn.execute("SELECT COUNT(*), COUNT(line_start) FROM chunk_spans").fetchone()
    report["structure"] = {
        "documents": documents,
        "sections": sections,
        "chunks": spans,
        "chunks_located": located,
        "indexed_this_run": counts["indexed"],
        "unchanged": counts["unchanged"],
        "no_source": counts["no_source"],
        "duplicate_key": counts["duplicate_key"],
    }
    log(f"structure: {documents} documents, {sections} sections, {located}/{spans} chunks located "
        f"({counts['indexed']} indexed, {counts['unchanged']} unchanged)")
    if counts["no_source"] or counts["duplicate_key"]:
        raise RuntimeError(f"documents without a source file {counts['no_source']} or with a duplicate "
                           f"doc_key {counts['duplicate_key']}")


def retryable(exc: BaseException) -> bool:
    kind = error_kind(exc)
    return kind in ("timeout", "connect", "transport", "http_429") or kind.startswith("http_5")


def with_retry(what: str, fn: Callable[[], Any], *, attempts: int = DENSE_ATTEMPTS,
               backoff: float = BACKOFF_SECONDS, sleep: Callable[[float], None] = time.sleep) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not retryable(exc):
                raise
            delay = min(backoff * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)
            log(f"dense: {what} failed ({error_kind(exc)}); retry {attempt}/{attempts - 1} in {delay:.0f} s")
            sleep(delay)
    raise AssertionError("unreachable")


def index_dense_resumable(db_path: Path, config: DenseConfig, embedder, client, *,
                          sleep: Callable[[float], None] = time.sleep,
                          reuse: DenseSource | None = None, reuse_key: str = "id") -> dict[str, Any]:
    """Embed and upsert every document that has chunks not yet dense-indexed.

    Per document: the vectors `reuse` (an earlier generation) holds for chunks with the same id and embedding
    text (reuse_key "text": or with the same embedding text alone, DenseSource.vectors), then the other chunks
    embedded in batches (config.batch_size) with retry and backoff, then
    rag_core.dense's own steps (reset state, delete the document's points, upsert in bounded batches, mark
    indexed). A document is either fully marked or not, so a rerun resumes. With embedder None nothing is
    embedded: a chunk without a reusable vector fails the step (derive_root copies, it never embeds)."""
    created = ensure_collection(client, config)
    with connect(db_path) as conn:
        pending = conn.execute(
            """
            SELECT c.doc_id, COUNT(*) AS n
            FROM chunks AS c
            LEFT JOIN index_state AS s ON s.chunk_id = c.chunk_id
            GROUP BY c.doc_id
            HAVING SUM(COALESCE(s.dense_indexed, 0) = 0) > 0
            ORDER BY c.doc_id
            """
        ).fetchall()
        models_seen = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT embedding_model FROM index_state WHERE dense_indexed = 1")
        }
    if len(models_seen) > 1:
        raise RuntimeError(f"root already mixes embedding models: {sorted(models_seen)}")
    model = next(iter(models_seen), None)
    total = sum(row["n"] for row in pending)
    done = reused = embedded = 0
    by_text_before = reuse.reused_by_text if reuse is not None else 0
    started = time.monotonic()
    source = f", reusing {reuse.generation} ({reuse.collection}, read-only)" if reuse is not None else ""
    log(f"dense: {len(pending)} documents, {total} chunks to index into {config.collection}{source}")

    for index, row in enumerate(pending, 1):
        doc_id = row["doc_id"]
        chunks = load_chunks(db_path, doc_id=doc_id)
        vectors: dict[str, list[float]] = {}
        if reuse is not None:
            vectors = with_retry(f"reading vectors of {doc_id[:16]} from {reuse.collection}",
                                 lambda: reuse.vectors(doc_id, chunks, key=reuse_key), sleep=sleep)
            if vectors:
                if model is None:
                    model = reuse.model
                elif reuse.model != model:
                    raise RuntimeError(f"{reuse.generation} was embedded by {reuse.model!r}, this root by {model!r}")
        todo = [chunk for chunk in chunks if chunk["chunk_id"] not in vectors]
        if todo and embedder is None:
            raise RuntimeError(f"{len(todo)} chunks of {doc_id} have no reusable vector, and this run does not embed")
        for batch in batched(todo, config.batch_size):
            texts = [str(chunk["embedding_text"]).strip() for chunk in batch]
            if not all(texts):
                raise RuntimeError(f"empty embedding_text in {doc_id}")
            batch_vectors, batch_model = with_retry(
                f"embedding {doc_id[:16]}", lambda: embedder.embed(texts), sleep=sleep)
            if model is None:
                model = batch_model
            elif batch_model != model:
                raise RuntimeError(f"embedding model changed during the build: {model!r} -> {batch_model!r}")
            vectors.update(zip((chunk["chunk_id"] for chunk in batch), batch_vectors, strict=True))

        chunk_ids = [chunk["chunk_id"] for chunk in chunks]
        points = [
            models.PointStruct(id=point_id_for_chunk(chunk["chunk_id"]), vector=vectors[chunk["chunk_id"]],
                               payload=payload_for_chunk(chunk))
            for chunk in chunks
        ]
        reset_dense_state(db_path, chunk_ids)
        with_retry(f"deleting points of {doc_id[:16]}",
                   lambda: delete_doc_points(client, config, doc_id), sleep=sleep)
        for start in range(0, len(points), UPSERT_BATCH_POINTS):
            part = points[start:start + UPSERT_BATCH_POINTS]
            with_retry(f"upserting {doc_id[:16]}",
                       lambda: client.upsert(collection_name=config.collection, points=part, wait=True),
                       sleep=sleep)
        mark_dense_indexed(db_path, chunk_ids, model, config.embedding_revision)
        done += len(chunks)
        reused += len(chunks) - len(todo)
        embedded += len(todo)
        rate = done / max(time.monotonic() - started, 1e-9)
        split = f"; reused {reused}, embedded {embedded}" if reuse is not None else ""
        log(f"dense {index}/{len(pending)} docs, {done}/{total} chunks ({rate:.1f}/s{split})")

    with connect(db_path) as conn:
        chunks_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        indexed = conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]
    points_total = client.count(collection_name=config.collection, exact=True).count
    if not points_total == indexed == chunks_total:
        raise RuntimeError(f"dense index incomplete: {chunks_total} chunks, {indexed} marked, "
                           f"{points_total} points in {config.collection}")
    result = {
        "collection": config.collection,
        "collection_created": created,
        "embedding_model": model,
        "embedding_revision": config.embedding_revision,
        "chunks_indexed_this_run": done,
        "reused_this_run": reused,
        "embedded_this_run": embedded,
        "points": points_total,
    }
    if reuse is not None and reuse_key == "text":
        result["reused_by_text_this_run"] = reuse.reused_by_text - by_text_before
    return result


def wait_until_indexed(client, collection: str, *, timeout: float | None = None,
                       sleep: Callable[[float], None] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Wait until Qdrant has finished optimizing `collection` (status green). Upserted points are searchable at
    once, but Qdrant builds their HNSW index in the background; on deep32-engine's CPU that takes cores from the
    served searches and skews a latency measurement (validate_root) started right away. Returns the last status,
    the seconds waited and Qdrant's counts. A collection still yellow at the timeout, or grey (optimization
    pending, not running) or red, is logged, not an error: the root is complete either way."""
    timeout = INDEXED_WAIT_SECONDS if timeout is None else timeout
    started = clock()
    while True:
        info = client.get_collection(collection_name=collection)
        status = str(getattr(info.status, "value", info.status)).lower()
        waited = round(clock() - started, 1)
        if status == "green" or status != "yellow" or waited >= timeout:
            break
        sleep(INDEXED_POLL_SECONDS)
    result = {"status": status, "seconds": waited, "points": info.points_count,
              "indexed_vectors": info.indexed_vectors_count}
    if status == "green":
        log(f"dense: {collection} indexed (green) after {waited:.0f} s")
    else:
        log(f"WARNING: dense: {collection} is {status} after {waited:.0f} s: "
            + ("Qdrant is still indexing it" if status == "yellow" else "Qdrant is not optimizing it")
            + "; wait for green before measuring latency on this root")
    return result


# ---------------------------------------------------------------- dense by Qdrant snapshot
# A collection of upserted vectors gets an HNSW graph of its own, and HNSW search is approximate: a fresh graph of the
# same vectors answers a little differently (two fresh copies of one generation's vectors: net -1 and -2 hit@1 on a
# 696-query set). A snapshot carries a collection's segments, graphs included, so a collection Qdrant recovers from a
# snapshot of another searches as that one does (restore_from_snapshot checks it).


def snapshot_client(config: DenseConfig) -> QdrantClient:
    """The client for Qdrant's snapshot calls (tests replace it): a wait=true snapshot or recovery request lasts as
    long as the work, longer than make_qdrant_client's 30 s, and a request the client gives up on goes on in Qdrant."""
    return QdrantClient(url=config.qdrant_url, timeout=SNAPSHOT_TIMEOUT_SECONDS)


def snapshot_location(snapshots_dir: str, collection: str, name: str) -> str:
    """The file:// location of a collection's snapshot on the Qdrant server: <snapshots_dir>/<collection>/<name>."""
    if not SNAPSHOT_NAME_RE.fullmatch(name or ""):
        raise RuntimeError(f"Qdrant named the snapshot {name!r}, not a file name this script recovers from")
    return "file://" + str(PurePosixPath(snapshots_dir) / collection / name)


def collection_status(client, collection: str) -> str:
    info = client.get_collection(collection_name=collection)
    return str(getattr(info.status, "value", info.status)).lower()


def collection_shape(client, collection: str) -> dict[str, Any]:
    """What a recovered copy shares with its source: Qdrant's whole config of the collection (vectors, HNSW,
    optimizers, WAL, quantization), its payload indexes (type, params, points) and its exact point count."""
    dump = client.get_collection(collection_name=collection).model_dump(mode="json")
    return {"config": dump.get("config"), "payload_schema": dump.get("payload_schema") or {},
            "points": client.count(collection_name=collection, exact=True).count}


def point_ids(client, collection: str) -> list[str]:
    """Every point id of a collection (canonical_point_id), scrolled without payloads or vectors."""
    out: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(collection_name=collection, limit=SCROLL_PAGE, offset=offset,
                                       with_payload=False, with_vectors=False)
        out += [canonical_point_id(p.id) for p in points]
        if offset is None:
            return out


def delete_points(client, collection: str, ids: list[str], *, sleep: Callable[[float], None] = time.sleep) -> None:
    for start in range(0, len(ids), DELETE_BATCH_POINTS):
        part = ids[start:start + DELETE_BATCH_POINTS]
        with_retry(f"deleting {len(part)} points of {collection}",
                   lambda: client.delete(collection_name=collection, points_selector=models.PointIdsList(points=part),
                                         wait=True), sleep=sleep)


def search_filters(db: sqlite3.Connection, opt_in) -> dict[str, Any]:
    """The searches a recovered copy is checked with: unfiltered and, when the root has documents of its opt-in doc
    types, a search without filters as the root serves it (must_not those documents, as rag_core.api_v2.filter_docs
    and retrievers.dense.qdrant_filter build it; RootReader.doc_ids_of reads them from doc_sources)."""
    filters: dict[str, Any] = {"unfiltered": None}
    types = sorted(set(opt_in or ()))
    if types and has_structure(db):
        excluded = frozenset(row[0] for row in db.execute(
            f"SELECT doc_id FROM doc_sources WHERE doc_type IN ({','.join('?' for _ in types)})", types))
        if excluded:
            filters["default_filter"] = qdrant_filter(None, excluded)
    return filters


def sample_vectors(client, collection: str, chunk_ids: list[str]) -> list[list[float]]:
    """The stored vectors of CLONE_CHECK_SAMPLE chunks drawn by random.Random(CLONE_CHECK_SEED) from the sorted ids."""
    ids = sorted(chunk_ids)
    picked = [point_id_for_chunk(c) for c in random.Random(CLONE_CHECK_SEED).sample(ids, min(CLONE_CHECK_SAMPLE,
                                                                                              len(ids)))]
    found = {canonical_point_id(p.id): p.vector
             for p in client.retrieve(collection_name=collection, ids=picked, with_payload=False, with_vectors=True)}
    return [found[pid] for pid in picked if isinstance(found.get(pid), list)]


def top_hits(client, collection: str, vector: list[float], flt) -> list[tuple[str, float]]:
    """Qdrant's default HNSW search (no search params, as a root without retrieval.json "dense" settings sends it)."""
    response = client.query_points(collection_name=collection, query=vector, query_filter=flt, limit=CLONE_CHECK_LIMIT,
                                   with_payload=False, with_vectors=False)
    return [(canonical_point_id(p.id), p.score) for p in response.points]


def same_searches(client, source: str, target: str, vectors: list[list[float]],
                  filters: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per filter: each vector's top CLONE_CHECK_LIMIT in both collections, {"queries", "differ"}: differ counts the
    searches whose ids or scores, in order, are not the same."""
    out = {}
    for name, flt in filters.items():
        differ = sum(top_hits(client, source, v, flt) != top_hits(client, target, v, flt) for v in vectors)
        out[name] = {"queries": len(vectors), "differ": differ}
    return out


def check_restore(client, source: str, target: str, before: dict[str, Any], chunk_ids: list[str],
                  filters: dict[str, Any]) -> dict[str, Any]:
    """The recovered `target` against `source`: the same config, payload indexes and exact point count, and the same
    ids with the same scores in the top CLONE_CHECK_LIMIT of each sampled stored vector, per filter."""
    if collection_shape(client, source) != before:
        raise RuntimeError(f"{source} changed while Qdrant made its snapshot")
    shape = collection_shape(client, target)
    for key in ("points", "config", "payload_schema"):
        if shape[key] != before[key]:
            raise RuntimeError(f"the recovered {target} differs from {source} in its {key.replace('_', ' ')}: "
                               f"{json.dumps(shape[key], sort_keys=True)[:300]} vs "
                               f"{json.dumps(before[key], sort_keys=True)[:300]}")
    vectors = sample_vectors(client, source, chunk_ids)
    if before["points"] and not vectors:
        raise RuntimeError(f"none of the sampled chunks has a point in {source} to check the recovery with")
    searches = same_searches(client, source, target, vectors, filters)
    differ = {name: s["differ"] for name, s in searches.items() if s["differ"]}
    if differ:
        raise RuntimeError(f"the recovered {target} does not search as {source} does: "
                           + ", ".join(f"{n} of {len(vectors)} {name} searches differ" for name, n in differ.items()))
    return {"points": before["points"], "limit": CLONE_CHECK_LIMIT, "seed": CLONE_CHECK_SEED, "searches": searches}


def snapshot_made_at(value: Any) -> datetime | None:
    """A SnapshotDescription's creation_time (Qdrant 1.19: "2026-11-02T14:09:14", UTC) as an aware datetime."""
    try:
        made = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return made if made.tzinfo else made.replace(tzinfo=timezone.utc)


def unnamed_snapshots(snapper, pending: dict[str, Any]) -> list[str]:
    """What a create_snapshot that failed on this side (a client timeout, a dropped connection: Qdrant goes on with the
    request) may have made: `pending["collection"]`'s snapshots not listed before the call and made from its start
    ("since") to SNAPSHOT_TIMEOUT_SECONDS + 10 min after (one without a readable creation_time counts)."""
    since = snapshot_made_at(pending.get("since"))
    if since is None:
        raise RuntimeError(f"the pending snapshot record has no start time: {pending!r}")
    until = since + timedelta(seconds=SNAPSHOT_TIMEOUT_SECONDS + 600)
    before = set(pending.get("before") or ())
    names = []
    for snap in snapper.list_snapshots(collection_name=pending["collection"]):
        made = snapshot_made_at(snap.creation_time)
        if snap.name not in before and (made is None or since <= made <= until):
            names.append(snap.name)
    return names


def drop_pending_snapshot(snapper, record: dict[str, Any], write: Callable[[], None], *,
                          forget: bool = True) -> str | None:
    """Deletes the snapshot record["pending_snapshot"] names (an earlier step's, or a crashed run's) and forgets it; one
    Qdrant no longer lists is just forgotten. A record without a name is a create_snapshot that failed on this side:
    what it may have made is deleted (unnamed_snapshots). forget=False keeps the record (Qdrant may still be making
    it: the next run looks again). Returns why it could not be deleted (it stays recorded), else None."""
    pending = record.get("pending_snapshot")
    if not pending:
        return None
    try:
        if pending.get("name") is None:
            names = unnamed_snapshots(snapper, pending)
        else:
            listed = {s.name for s in snapper.list_snapshots(collection_name=pending["collection"])}
            names = [pending["name"]] if pending["name"] in listed else []
        for name in names:
            snapper.delete_snapshot(collection_name=pending["collection"], snapshot_name=name, wait=True)
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        return f"{type(exc).__name__}: {exc}"
    if forget:
        record.pop("pending_snapshot", None)
        write()
    return None


def restore_from_snapshot(client, snapper, source: str, target: str, record: dict[str, Any],
                          write: Callable[[], None], *, snapshots_dir: str, chunk_ids: list[str],
                          filters: dict[str, Any]) -> dict[str, Any]:
    """`target`, which must not exist, made by Qdrant from a snapshot of `source` and checked against it; returns the
    facts for the report.

    `source` must be green (a snapshot of a collection Qdrant is still optimizing would not search as it will). Its
    snapshot is created (wait), recovered into `target` (priority snapshot, its sha256 checksum, wait) and deleted
    (wait) whatever happened; record["pending_snapshot"] names it on disk meanwhile, so that a run after a crash
    deletes it (it is recorded before the request, without a name until Qdrant answers: a request this side gives up
    on goes on in Qdrant, and the next run deletes what it made). Once Qdrant reports `target` green, check_restore.
    Any failure, a snapshot not deleted included, deletes `target` (only this call can have made it) and raises: no
    unchecked copy is left behind. `target` is never `source` (a recovery overwrites an existing collection).
    `source`'s points are only read; Qdrant writes and this function deletes the snapshot file."""
    if source == target:
        # a recovery with priority snapshot overwrites an existing collection, and a failure deletes the target
        raise RuntimeError(f"{target} cannot be recovered from a snapshot of itself")
    problem = drop_pending_snapshot(snapper, record, write)
    if problem:
        pending = record["pending_snapshot"]
        name = pending.get("name") or f"(made by a failed request since {pending.get('since')})"
        raise RuntimeError(f"an earlier run's snapshot {name} of {pending['collection']} could not be deleted "
                           f"({problem}); delete it in Qdrant")
    if client.collection_exists(collection_name=target):
        raise RuntimeError(f"Qdrant collection {target} exists before its recovery")
    status = collection_status(client, source)
    if status != "green":
        raise RuntimeError(f"{source} is {status}, not green: Qdrant is still optimizing it, and a snapshot of it "
                           "would not search as it will")
    before = collection_shape(client, source)
    # recorded before the request: one this side gives up on (a timeout, a dropped connection) goes on in Qdrant
    record["pending_snapshot"] = {
        "collection": source, "name": None,
        "since": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(timespec="seconds"),
        "before": sorted(s.name for s in snapper.list_snapshots(collection_name=source))}
    write()
    started = time.monotonic()
    try:
        snap = snapper.create_snapshot(collection_name=source, wait=True)
        if snap is None:
            raise RuntimeError(f"Qdrant made no snapshot of {source}")
    except Exception:
        # deleted if Qdrant has made it by now; the record stays, so the next run deletes one it makes later
        drop_pending_snapshot(snapper, record, write, forget=False)
        log(f"dense: the snapshot request for {source} failed; Qdrant may still make it, and the next run deletes it")
        raise
    record["pending_snapshot"] = {"collection": source, "name": snap.name}
    write()
    facts: dict[str, Any] = {"from_collection": source, "points": before["points"],
                             "snapshot": {"name": snap.name, "bytes": snap.size,
                                          "seconds": round(time.monotonic() - started, 1)}}
    log(f"dense: snapshot {snap.name} of {source} ({snap.size} bytes, {before['points']} points) made in "
        f"{facts['snapshot']['seconds']:.0f} s")
    error: Exception | None = None
    try:
        location = snapshot_location(snapshots_dir, source, snap.name)
        started = time.monotonic()
        snapper.recover_snapshot(collection_name=target, location=location, priority=models.SnapshotPriority.SNAPSHOT,
                                 checksum=snap.checksum, wait=True)
        facts["recover_seconds"] = round(time.monotonic() - started, 1)
        facts["index"] = wait_until_indexed(client, target)
        facts["check"] = check_restore(client, source, target, before, chunk_ids, filters)
    except Exception as exc:  # noqa: BLE001 - raised below, after the cleanup
        error = exc
    problem = drop_pending_snapshot(snapper, record, write)
    if error is None and problem:
        error = RuntimeError(f"the snapshot {snap.name} of {source} could not be deleted: {problem}")
    if error is not None:
        with contextlib.suppress(Exception):
            if client.collection_exists(collection_name=target):
                client.delete_collection(collection_name=target)
        raise error
    log(f"dense: {target} recovered from it in {facts['recover_seconds']:.0f} s and checked: "
        + ", ".join(f"{s['queries']} {name} top-{CLONE_CHECK_LIMIT} searches the same"
                    for name, s in facts["check"]["searches"].items())
        + "; snapshot deleted")
    return facts


def clone_points(client, collection: str, chunks: list[tuple[str, str]], *,
                 sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """derive_root --dense-clone snapshot: the recovered collection holds every chunk's point as the copy path takes
    it (point_id_for_chunk, the payload naming the same chunk and document: DenseSource.vectors' rule) and no other:
    a point under an id no chunk has (one the copy path would never fetch) is deleted. Raises naming the chunks
    without such a point (the copy path would find no reusable vector for them). `chunks`: (chunk_id, doc_id)."""
    expected = {point_id_for_chunk(chunk_id): (chunk_id, doc_id) for chunk_id, doc_id in chunks}
    seen: set[str] = set()
    strays: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(collection_name=collection, limit=SCROLL_PAGE, offset=offset,
                                       with_payload=["chunk_id", "doc_id"], with_vectors=False)
        for point in points:
            pid = canonical_point_id(point.id)
            want = expected.get(pid)
            payload = point.payload or {}
            if want is None:
                strays.append(pid)
            elif (payload.get("chunk_id"), payload.get("doc_id")) == want:
                seen.add(pid)
        if offset is None:
            break
    missing = sorted(chunk_id for pid, (chunk_id, _) in expected.items() if pid not in seen)
    if missing:
        raise RuntimeError(f"{len(missing)} chunks have no reusable vector in the recovered {collection} (e.g. "
                           f"{missing[0]}), and this run does not embed")
    delete_points(client, collection, strays, sleep=sleep)
    return {"points": len(seen), "strays_deleted": len(strays)}


def text_key(text: str | None) -> bytes | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


def prune_base(client, collection: str, db_path: Path, reuse: DenseSource, *,
               sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """--dense-base snapshot, once after the recovery: the base's points that no chunk of this build can reuse are
    deleted (by id, wait), so that only new or changed chunks are written after. A point stays when its id is that of
    a chunk of this build whose document and embedding text are the ones `reuse` embedded (DenseSource's rule; the
    vector and payload are checked per document after): the points of chunks this build no longer has, or whose text
    or document changed, go, and so does a point under an id no chunk has."""
    embedded = {chunk_id: (doc_id, text_key(text)) for chunk_id, doc_id, text in reuse.db.execute(
        "SELECT c.chunk_id, c.doc_id, c.embedding_text FROM chunks AS c "
        "JOIN index_state AS s ON s.chunk_id = c.chunk_id WHERE s.dense_indexed = 1")}
    with connect(db_path) as conn:
        keep = {point_id_for_chunk(row["chunk_id"]) for row in conn.execute(
            "SELECT chunk_id, doc_id, embedding_text FROM chunks")
            if embedded.get(row["chunk_id"]) == (row["doc_id"], text_key(row["embedding_text"]))}
    found = with_retry(f"listing the points of {collection}", lambda: point_ids(client, collection), sleep=sleep)
    stale = [pid for pid in found if pid not in keep]
    delete_points(client, collection, stale, sleep=sleep)
    return {"points": len(found), "deleted": len(stale), "kept": len(found) - len(stale)}


def stored_points(client, collection: str, ids: list[str]) -> dict[str, Any]:
    """The points of these ids, payloads and vectors, by canonical id (fetched in DenseSource.PAGE pages)."""
    out = {}
    for start in range(0, len(ids), DenseSource.PAGE):
        for point in client.retrieve(collection_name=collection, ids=ids[start:start + DenseSource.PAGE],
                                     with_payload=True, with_vectors=True):
            out[canonical_point_id(point.id)] = point
    return out


def index_dense_on_base(db_path: Path, config: DenseConfig, embedder, client, reuse: DenseSource, *,
                        sleep: Callable[[float], None] = time.sleep, reuse_key: str = "id") -> dict[str, Any]:
    """--dense-base snapshot, after the base (recovered, pruned): index_dense_resumable with `reuse`, except that the
    collection already holds the vectors this build reuses, so a reused point is checked in place instead of written
    again. Per document with chunks not yet dense-indexed: reuse.vectors (the same rule as the upsert path) and the
    collection's points of the document's chunks; a point whose vector and payload are those the upsert path would
    write is left untouched (kept), any other chunk with a reusable vector is written with it as the upsert path writes
    it (rewritten: e.g. a payload that changed), the rest is embedded and written. Nothing is deleted here (prune_base
    did), so every kept point keeps its place in the recovered graph. Then the upsert path's checks, and the
    collection's ids are exactly the chunks'. With reuse_key "text", a vector taken by its embedding text is under a
    new id, so it is written (rewritten), never kept; prune_base has deleted the point of its old id."""
    if not client.collection_exists(collection_name=config.collection):
        raise RuntimeError(f"{config.collection} does not exist: the dense base step recovers it first")
    ensure_collection(client, config)                       # checks its vector size and distance
    with connect(db_path) as conn:
        pending = conn.execute(
            """
            SELECT c.doc_id, COUNT(*) AS n
            FROM chunks AS c
            LEFT JOIN index_state AS s ON s.chunk_id = c.chunk_id
            GROUP BY c.doc_id
            HAVING SUM(COALESCE(s.dense_indexed, 0) = 0) > 0
            ORDER BY c.doc_id
            """
        ).fetchall()
        models_seen = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT embedding_model FROM index_state WHERE dense_indexed = 1")
        }
    if len(models_seen) > 1:
        raise RuntimeError(f"root already mixes embedding models: {sorted(models_seen)}")
    model = next(iter(models_seen), None)
    total = sum(row["n"] for row in pending)
    done = kept = rewritten = embedded = 0
    by_text_before = reuse.reused_by_text
    started = time.monotonic()
    log(f"dense: {len(pending)} documents, {total} chunks to index into {config.collection} on its base (reusing "
        f"{reuse.generation}'s vectors in place)")

    for index, row in enumerate(pending, 1):
        doc_id = row["doc_id"]
        chunks = load_chunks(db_path, doc_id=doc_id)
        vectors = with_retry(f"reading vectors of {doc_id[:16]} from {reuse.collection}",
                             lambda: reuse.vectors(doc_id, chunks, key=reuse_key), sleep=sleep)
        if vectors:
            if model is None:
                model = reuse.model
            elif reuse.model != model:
                raise RuntimeError(f"{reuse.generation} was embedded by {reuse.model!r}, this root by {model!r}")
        ids = {chunk["chunk_id"]: point_id_for_chunk(chunk["chunk_id"]) for chunk in chunks}
        stored = with_retry(f"reading points of {doc_id[:16]} from {config.collection}",
                            lambda: stored_points(client, config.collection, list(ids.values())), sleep=sleep)
        in_place = set()
        for chunk in chunks:
            vector, point = vectors.get(chunk["chunk_id"]), stored.get(ids[chunk["chunk_id"]])
            if (vector is not None and point is not None and point.vector == vector
                    and (point.payload or {}) == payload_for_chunk(chunk)):
                in_place.add(chunk["chunk_id"])
        todo = [chunk for chunk in chunks if chunk["chunk_id"] not in vectors]
        if todo and embedder is None:
            raise RuntimeError(f"{len(todo)} chunks of {doc_id} have no reusable vector, and this run does not embed")
        for batch in batched(todo, config.batch_size):
            texts = [str(chunk["embedding_text"]).strip() for chunk in batch]
            if not all(texts):
                raise RuntimeError(f"empty embedding_text in {doc_id}")
            batch_vectors, batch_model = with_retry(
                f"embedding {doc_id[:16]}", lambda: embedder.embed(texts), sleep=sleep)
            if model is None:
                model = batch_model
            elif batch_model != model:
                raise RuntimeError(f"embedding model changed during the build: {model!r} -> {batch_model!r}")
            vectors.update(zip((chunk["chunk_id"] for chunk in batch), batch_vectors, strict=True))

        chunk_ids = [chunk["chunk_id"] for chunk in chunks]
        points = [
            models.PointStruct(id=ids[chunk["chunk_id"]], vector=vectors[chunk["chunk_id"]],
                               payload=payload_for_chunk(chunk))
            for chunk in chunks if chunk["chunk_id"] not in in_place
        ]
        reset_dense_state(db_path, chunk_ids)
        for start in range(0, len(points), UPSERT_BATCH_POINTS):
            part = points[start:start + UPSERT_BATCH_POINTS]
            with_retry(f"upserting {doc_id[:16]}",
                       lambda: client.upsert(collection_name=config.collection, points=part, wait=True),
                       sleep=sleep)
        mark_dense_indexed(db_path, chunk_ids, model, config.embedding_revision)
        done += len(chunks)
        kept += len(in_place)
        embedded += len(todo)
        rewritten += len(chunks) - len(in_place) - len(todo)
        rate = done / max(time.monotonic() - started, 1e-9)
        log(f"dense {index}/{len(pending)} docs, {done}/{total} chunks ({rate:.1f}/s; kept {kept}, "
            f"rewritten {rewritten}, embedded {embedded})")

    with connect(db_path) as conn:
        chunk_ids = [r[0] for r in conn.execute("SELECT chunk_id FROM chunks")]
        indexed = conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]
    points_total = client.count(collection_name=config.collection, exact=True).count
    if not points_total == indexed == len(chunk_ids):
        raise RuntimeError(f"dense index incomplete: {len(chunk_ids)} chunks, {indexed} marked, "
                           f"{points_total} points in {config.collection}")
    expected = {point_id_for_chunk(c) for c in chunk_ids}
    found = set(with_retry(f"listing the points of {config.collection}",
                           lambda: point_ids(client, config.collection), sleep=sleep))
    if found != expected:
        raise RuntimeError(f"{config.collection} holds {len(found - expected)} points no chunk has and lacks "
                           f"{len(expected - found)} chunks' points")
    result = {
        "collection": config.collection,
        "collection_created": False,
        "embedding_model": model,
        "embedding_revision": config.embedding_revision,
        "chunks_indexed_this_run": done,
        "reused_this_run": kept + rewritten,
        "embedded_this_run": embedded,
        "points": points_total,
        "kept_this_run": kept,
        "rewritten_this_run": rewritten,
    }
    if reuse_key == "text":
        result["reused_by_text_this_run"] = reuse.reused_by_text - by_text_before
    return result


def reuse_counts(dense: dict, reuse: DenseSource, result: dict, reuse_key: str) -> dict[str, Any]:
    """The report's dense.reuse after a run: the counts of the runs so far plus this one's; with --reuse-key text
    also the key (a --resume run must give the same) and the vectors taken by their embedding text."""
    counts = dense.get("reuse") or {}
    out = {
        "from_generation": reuse.generation, "from_root": str(reuse.root), "collection": reuse.collection,
        "reused": counts.get("reused", 0) + result["reused_this_run"],
        "embedded": counts.get("embedded", 0) + result["embedded_this_run"],
    }
    if reuse_key == "text":
        out["key"] = "text"
        out["reused_by_text"] = counts.get("reused_by_text", 0) + result["reused_by_text_this_run"]
    return out


def step_dense_snapshot(report: dict, root: Path, db_path: Path, collection: str, reuse_from: str,
                        snapshots_dir: str = DEFAULT_SNAPSHOTS_DIR, reuse_key: str = "id") -> None:
    """Step dense with --dense-base snapshot: the base (once: restore_from_snapshot of the reuse generation's
    collection, then prune_base), index_dense_on_base, the wait for green. A base that fails is deleted (the
    collection and the snapshot), so a --resume run makes it again; once made, a resume continues on it."""
    config = load_dense_config(root=root)
    if config.collection != collection:
        raise RuntimeError(f"dense config resolves to {config.collection}, expected {collection}")
    dense = report.setdefault("dense", {})
    dense.update({"collection": collection, "collection_owned": True})
    if reuse_key == "text":                     # on disk before the step starts: a --resume run is checked against it
        dense.setdefault("reuse", {})["key"] = "text"
    base = dense.setdefault("base", {"mode": "snapshot"})

    def write() -> None:
        write_json(root / REPORT_NAME, report)

    write()                                     # ownership and the base are on disk before the collection exists
    client = make_qdrant_client(config)
    snapper = snapshot_client(config)
    embedder = JinaEmbeddingClient(config)
    reuse = None
    try:
        source = resolve_dense_source(reuse_from, root)
        generation, source_collection = dense_source_settings(source, root)
        if source_collection == collection:
            raise RuntimeError(f"{collection} is the reuse generation's own collection: it is never deleted or "
                               "recovered into")
        reuse = DenseSource(source, source_collection, client, config.vector_size, generation)
        if base.get("from_collection") not in (None, source_collection):
            raise RuntimeError(f"this root's base is {base['from_collection']}, not {source_collection}")
        if not base.get("restored"):
            if client.collection_exists(collection_name=collection):
                client.delete_collection(collection_name=collection)        # an earlier run's, never checked
            embedded_ids = [r[0] for r in reuse.db.execute(
                "SELECT chunk_id FROM index_state WHERE dense_indexed = 1")]
            retrieval = load_config("retrieval", root=source)
            opt_in = retrieval.get("opt_in_doc_types") if isinstance(retrieval.get("opt_in_doc_types"), list) else []
            base["restored"] = restore_from_snapshot(
                client, snapper, source_collection, collection, base, write, snapshots_dir=snapshots_dir,
                chunk_ids=embedded_ids, filters=search_filters(reuse.db, opt_in))
            base["from_collection"] = source_collection
            write()
        if not base.get("pruned"):
            try:
                base["pruned"] = prune_base(client, collection, db_path, reuse)
            except Exception:
                base.pop("restored", None)              # a base half pruned is made again
                with contextlib.suppress(Exception):
                    client.delete_collection(collection_name=collection)
                write()
                raise
            write()
            log(f"dense: base {collection}: {base['pruned']['deleted']} points deleted (chunks gone or changed), "
                f"{base['pruned']['kept']} kept")
        result = index_dense_on_base(db_path, config, embedder, client, reuse, reuse_key=reuse_key)
        dense.update(result)
        dense["reuse"] = reuse_counts(dense, reuse, result, reuse_key)
        base["kept"] = base.get("kept", 0) + result["kept_this_run"]
        base["rewritten"] = base.get("rewritten", 0) + result["rewritten_this_run"]
        base["verified"] = {"points": result["points"], "ids": "the chunks'", "at": now()}
        dense["index"] = wait_until_indexed(client, collection)
    finally:
        if reuse is not None:
            reuse.close()
        embedder.close()
        snapper.close()
        client.close()
    log(f"dense: base kept {base['kept']} points untouched, rewrote {base['rewritten']}, deleted "
        f"{base['pruned']['deleted']}; final points checked against the upsert path's")
    log(f"dense: {dense['points']} points in {collection} ({dense['embedding_model']}; {dense['reuse']['reused']} "
        f"reused from {dense['reuse']['from_generation']}, {dense['reuse']['embedded']} embedded)")


def step_dense(report: dict, root: Path, db_path: Path, collection: str, reuse_from: str | None = None,
               dense_base: str = "upsert", snapshots_dir: str = DEFAULT_SNAPSHOTS_DIR, reuse_key: str = "id") -> None:
    if reuse_key == "text" and not reuse_from:
        raise RuntimeError("--reuse-key text needs --reuse-dense-from")
    if dense_base == "snapshot":
        if not reuse_from:
            raise RuntimeError("--dense-base snapshot needs --reuse-dense-from")
        step_dense_snapshot(report, root, db_path, collection, reuse_from, snapshots_dir, reuse_key)
        return
    config = load_dense_config(root=root)
    if config.collection != collection:
        raise RuntimeError(f"dense config resolves to {config.collection}, expected {collection}")
    client = make_qdrant_client(config)
    dense = report.setdefault("dense", {})
    dense.update({"collection": collection, "collection_owned": True})
    if reuse_key == "text":                     # on disk before the step starts: a --resume run is checked against it
        dense.setdefault("reuse", {})["key"] = "text"
    write_json(root / REPORT_NAME, report)       # ownership is on disk before the collection exists
    embedder = JinaEmbeddingClient(config)
    reuse = None
    try:
        if reuse_from:
            source = resolve_dense_source(reuse_from, root)
            generation, source_collection = dense_source_settings(source, root)
            reuse = DenseSource(source, source_collection, client, config.vector_size, generation)
        result = index_dense_resumable(db_path, config, embedder, client, reuse=reuse, reuse_key=reuse_key)
        dense.update(result)
        if reuse is not None:
            dense["reuse"] = reuse_counts(dense, reuse, result, reuse_key)
        dense["index"] = wait_until_indexed(client, collection)
    finally:
        if reuse is not None:
            reuse.close()
        embedder.close()
        client.close()
    split = (f"; {dense['reuse']['reused']} reused from {dense['reuse']['from_generation']}, "
             f"{dense['reuse']['embedded']} embedded") if reuse is not None else ""
    log(f"dense: {dense['points']} points in {collection} ({dense['embedding_model']}{split})")


def step_kb(report: dict, root: Path) -> None:
    source = root / "sources" / "kb"
    if not source.is_dir():
        report["kb"] = {"status": "skipped", "reason": "the corpus has no kb/ directory"}
    else:
        target = root / "kb" / "kb.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        for stale in (target, target.with_name("kb.sqlite-wal"), target.with_name("kb.sqlite-shm")):
            stale.unlink(missing_ok=True)
        report["kb"] = {"status": "built", "counts": build_kb(source, target)}
    log(f"kb: {report['kb']['status']} {report['kb'].get('reason', '')}".rstrip())


# ---------------------------------------------------------------- main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus-ref", required=True, help="git ref of the corpus package, e.g. refs/heads/corpus-v1")
    ap.add_argument("--root", required=True, help="new root directory, e.g. rag-data/<generation>")
    ap.add_argument("--corpus-repo", default=str(REPO), help="git repository holding the ref (default: this checkout)")
    ap.add_argument("--generation", help="generation id (default: the root directory name)")
    ap.add_argument("--no-dense", action="store_true", help="skip dense indexing; the root serves bm25 only")
    ap.add_argument("--resume", action="store_true", help="continue a build this script started in --root")
    ap.add_argument("--rerank-url", help="enable the reranker at this URL in the root's retrieval.json, e.g. "
                                         "https://<forge>.<tailnet>.ts.net:8443/v1/rerank (Tailscale Serve)")
    ap.add_argument("--reuse-dense-from", metavar="GENERATION",
                    help="copy the vectors of unchanged chunks from this generation's collection (read-only); "
                         "embed only new or changed chunks")
    ap.add_argument("--reuse-key", choices=("id", "text"), default="id",
                    help="with --reuse-dense-from: id (default) reuses a vector when the chunk id and its embedding "
                         "text are unchanged; text also when only the text is (a passage inserted earlier in a "
                         "document changes the later chunk ids)")
    ap.add_argument("--table-header-repeat", action="store_true",
                    help="chunking.table_header_repeat: each continuation chunk of a split table starts with "
                         "the table's caption line and header row (default: off)")
    ap.add_argument("--opt-in-doc-types", metavar="TYPE[,TYPE...]",
                    help="retrieval.opt_in_doc_types in the root's overlay: doc types that searches and grep leave "
                         "out unless doc_type or docs names them, e.g. powershell,system (default: unset)")
    ap.add_argument("--dense-base", choices=DENSE_BASES, default="upsert",
                    help="with --reuse-dense-from: upsert (default) writes every vector into a new collection (a "
                         "fresh HNSW graph); snapshot starts it as Qdrant's recovery of a snapshot of that "
                         "generation's collection (its graph) and writes only new or changed chunks")
    ap.add_argument("--qdrant-snapshots-dir", default=DEFAULT_SNAPSHOTS_DIR, metavar="DIR",
                    help="--dense-base snapshot: the Qdrant server's snapshots_path, where it writes "
                         f"<collection>/<snapshot> (default: {DEFAULT_SNAPSHOTS_DIR}, the qdrant/qdrant image's)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    generation = args.generation or root.name
    collection = collection_for(generation)
    try:
        report, commit = preflight(args, root, generation, collection)
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - e.g. Qdrant unreachable while checking the collection
        log(f"FAILED before building: {type(exc).__name__}: {exc}")
        return 1

    root.mkdir(parents=True, exist_ok=True)
    os.environ["RAG_ROOT"] = str(root)
    os.environ["RAG_DENSE_COLLECTION"] = collection
    report.update({
        "builder": BUILDER,
        "generation": generation,
        "root": str(root),
        "status": "running",
        "started_at": report.get("started_at") or now(),
        "updated_at": now(),
        "runs": report.get("runs", 0) + 1,
    })
    with contextlib.suppress(subprocess.CalledProcessError, OSError):
        report["code_commit"] = git(REPO, "rev-parse", "HEAD")
    if args.table_header_repeat:
        report["chunker"] = chunker_facts(True)     # on disk before any document is chunked
    report_path = root / REPORT_NAME
    steps = report.setdefault("steps", {})
    step_failures: list[dict[str, str]] = []
    log(f"build_root: generation {generation} into {root}")

    def run(name: str, fn: Callable[[], Any]) -> Any:
        started = time.monotonic()
        try:
            return fn()
        finally:
            steps[name] = {"seconds": round(time.monotonic() - started, 1), "at": now()}
            report["updated_at"] = now()
            write_json(report_path, report)

    def attempt(name: str, fn: Callable[[], Any]) -> None:
        # a failed dense or kb step is reported; the steps after it still run
        try:
            run(name, fn)
        except Exception as exc:  # noqa: BLE001
            step_failures.append({"step": name, "error": f"{type(exc).__name__}: {exc}"})
            log(f"{name}: FAILED {type(exc).__name__}: {exc}")

    try:
        write_json(report_path, report)
        entries = run("sources", lambda: step_sources(report, root, Path(args.corpus_repo), args.corpus_ref, commit))
        run("config", lambda: step_config(report, root, generation, collection, not args.no_dense, args.rerank_url,
                                          opt_in_doc_types(args.opt_in_doc_types)))
        db_path = run("schema", lambda: prepare_schema(RAGPaths(root)))
        run("documents", lambda: step_documents(report, root, db_path, entries, args.table_header_repeat))
        run("bm25", lambda: step_bm25(report, db_path, opt_in_doc_types(args.opt_in_doc_types)))
        attempt("structure", lambda: step_structure(report, root, db_path))
        if args.no_dense:
            report.setdefault("dense", {})["skipped"] = "--no-dense"
            log("dense: skipped (--no-dense)")
        else:
            report.get("dense", {}).pop("skipped", None)
            attempt("dense", lambda: step_dense(report, root, db_path, collection, args.reuse_dense_from,
                                                args.dense_base, args.qdrant_snapshots_dir, args.reuse_key))
        attempt("kb", lambda: step_kb(report, root))
        with connect(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # noqa: BLE001 - reported below; a rerun with --resume continues
        step_failures.append({"step": "build", "error": f"{type(exc).__name__}: {exc}"})
        log(f"FAILED: {type(exc).__name__}: {exc}")

    incomplete = step_failures or report.get("documents", {}).get("failed")
    report["failed_steps"] = step_failures
    report["status"] = "incomplete" if incomplete else "complete"
    report["finished_at"] = report["updated_at"] = now()
    write_json(report_path, report)
    log(f"build_root: {report['status']}; report {report_path}")
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
