#!/usr/bin/env python3
"""Export the served root (one index generation) and its Qdrant vectors for the Deep32 node, read-only, as one
age-encrypted tar (ISO-DESIGN 4.2, CORE-PORT 6, PYTHON-ON-OPENBSD 6.3; REPLAN C8).

usage on deep32-engine, as a deploy job (deployctl passes arguments that match its SAFE_ARG, hence --key=value):
  export_generation.py check  --root=DIR --expect-generation=ID --expect-fingerprint=FP [--expect-docs=N]
                              [--expect-chunks=N] [--expect-points=N] [--recipient=age1...] [--out-dir=DIR]
  export_generation.py export (the same, --recipient and --out-dir required)
  export_generation.py clean  --out-dir=DIR
usage on the Deep32 build VM (standard library only; nothing here imports rag_core):
  age -d -i KEY export-FP.tar.age | python3 -I export_generation.py unpack --into=DIR [--expect-tar-sha256=HEX]
  python3 -I export_generation.py verify --dir=DIR/GENERATION [--parser=auto|strtof|python] [--no-fts-check]

check   Everything export checks, and writes nothing: the root is the served one ($RAG_ROOT) and is GENERATION
        (config/serving.json, BUILD_REPORT.json, the directory name); the corpus fingerprint of rag.db's documents
        (rag_core.corpus.corpus_fingerprint), of BUILD_REPORT.json and of sources/manifest.json is FP; the counts
        match; no SQLite file of the root has a WAL holding frames or a hot journal; every entry is a regular file,
        a directory or a symlink inside the root; the Qdrant collection the root serves (the root's dense.json, with
        RAG_DENSE_COLLECTION and RAG_QDRANT_URL on top as rag_core.dense.load_dense_config reads them) is one unnamed
        float32 vector of the configured size; one point scrolled; age found (and the recipient accepted by it); the
        free space at --out-dir. Prints READY or NOT READY with every reason.
export  check, then one stream: tar (PAX) -> `age -r RECIPIENT` -> OUT-DIR/.export-FP.tar.age.partial, renamed to
        export-FP.tar.age when age has finished; a sidecar export-FP.tar.age.json and the job log carry the sizes and
        SHA-256 of the plaintext tar, the ciphertext and MANIFEST.json. No plaintext touches a disk: files are read
        from the root into the tar, the vectors are held in memory (N x DIM x 4 bytes; N from the collection), the
        scroll pages one at a time. A failure, --max-minutes (default 90) or a SIGTERM (systemctl stop) deletes
        the partial file and stops age.
clean   Deletes export-*.tar.age, its .json and .partial files from --out-dir, and the directory when empty.
unpack  (VM) Reads the decrypted tar on stdin into DIR/GENERATION/ (directories 0700, files 0600, no path outside
        it, no member type but directory, file and relative symlink), then checks every file against MANIFEST.json
        and the stream's SHA-256 against --expect-tar-sha256.
verify  (VM) Every file against MANIFEST.json again (and nothing extra on disk); the vectors re-parsed from the raw
        scroll pages by a second parser (libc strtof by default) and compared bit for bit with vectors.f32;
        points.bin rebuilt from the pages and the exported rag.db and compared byte for byte; PRAGMA quick_check of
        rag.db and kb.sqlite; the counts and the fingerprint; every doc_sources.sha256 against its source file; FTS5
        integrity-check of chunk_fts (and chunk_fts_optin) on a scratch copy, deleted afterwards.

The tar, top directory GENERATION/:
  root/...                       every entry of the root byte for byte, except (listed in MANIFEST.json "excluded"):
                                 logs/ (the serving process's retrieval traces: runtime output, not the generation;
                                 --include-logs ships it) and SQLite's -wal/-shm/-journal sidecars (refused unless
                                 empty: an empty WAL holds no change; -shm is lock state)
  dense/scroll/page-NNNNN.json.gz  each Qdrant scroll response body as received (gzip, mtime 0)
  dense/vectors.f32              N x DIM float32, little-endian, in scroll order (point id ascending)
  dense/points.bin               N records of 96 bytes, same order (POINT_LAYOUT)
  dense/EXPORT.json              collection config and counts, the pages, the parse, checks and stats
  repo/config/*.json             the repository's config/ at the commit the job runs (load_config's lowest layer)
  MANIFEST.json                  last: every member above with size and SHA-256, the counts, versions (this
                                 interpreter, its SQLite and compile options, packages, Qdrant), exclusions

Vectors: Qdrant serialises each stored float32 with serde_json's shortest representation. CPython's float() of
that text is the correctly rounded double, and the double cast to float32 is the stored value except when the
double is exactly halfway between two float32 (rounding twice can land on such a midpoint, never cross it); those
values are found by their bit pattern and rounded again from the exact decimal (fractions.Fraction). EXPORT.json
counts them. verify parses the same raw pages with libc strtof, an independent correctly rounded parser.

Read-only: SQLite files are opened with mode=ro&immutable=1 (no -wal, -shm, lock or write, ever); files are read
with O_NOFOLLOW and their (size, mtime, inode) checked before, during and after the copy, and the whole tree again
at the end, so a change anywhere fails the export. Qdrant is reached only for GET /, GET /collections/NAME and
POST /collections/NAME/points/scroll (anything else raises before a request). --out-dir must lie outside the root,
$RAG_ROOT and this repository (a file there would dirty the deployed tree and block the next release).
Exit status: 0 done (check: READY), 1 failed, 2 refused (check: NOT READY).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import platform
import posixpath
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from array import array
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable


EXPORTER_VERSION = "1"
FORMAT = "deep32-generation-export/1"
DENSE_FORMAT = "deep32-dense/1"
REPO = Path(__file__).resolve().parents[1]

GENERATION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")                  # build_root.GENERATION_RE
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{58}$")   # X25519, bech32
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_ID_RE = re.compile(r"^chk_([0-9a-f]{64})$")                            # rag_core.identity.chunk_id
DOC_ID_RE = re.compile(r"^doc_([0-9a-f]{64})$")                              # rag_core.identity.document_id
SIDECAR_RE = re.compile(r"^(?P<db>.+)-(?P<kind>wal|shm|journal)$")
OUT_NAME_RE = re.compile(r"^(?:export-[0-9a-f]{16}\.tar\.age(?:\.json)?|\.export-[0-9a-f]{16}\.tar\.age\.partial)$")
POINT_NAME_PREFIX = "rag-core:dense:v1:"                                     # rag_core.dense.point_id_for_chunk
INDEX_CHECK_SQL = "INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 0)"   # rag_core.db
DEFAULT_FTS, OPTIN_FTS, OPTIN_DOCS = "chunk_fts", "chunk_fts_optin", "chunk_fts_optin_docs"
PAYLOAD_FIELDS = ["chunk_id", "doc_id"]

# points.bin: one record per row of vectors.f32
POINT_RECORD = struct.Struct("<16s32s32sB15x")
F_CHUNK_PAYLOAD, F_DOC_PAYLOAD, F_CHUNK_IN_DB, F_DENSE_INDEXED, F_ID_IS_UUID5, F_DOC_MATCHES = 1, 2, 4, 8, 16, 32
POINT_LAYOUT = {
    "record_bytes": POINT_RECORD.size,
    "fields": [
        {"offset": 0, "bytes": 16, "what": "point id: the UUID's 16 bytes (RFC 4122 order, uuid.UUID(bytes=...))"},
        {"offset": 16, "bytes": 32, "what": "payload chunk_id: the 64 hex digits after chk_, as bytes (zero if none)"},
        {"offset": 48, "bytes": 32, "what": "payload doc_id: the 64 hex digits after doc_, as bytes (zero if none)"},
        {"offset": 80, "bytes": 1, "what": "flags"},
        {"offset": 81, "bytes": 15, "what": "zero"},
    ],
    "flags": {
        "1": "payload has chunk_id",
        "2": "payload has doc_id",
        "4": "rag.db has the chunk",
        "8": "rag.db index_state.dense_indexed = 1 for the chunk",
        "16": "point id = rag_core.dense.point_id_for_chunk(chunk_id)",
        "32": "payload doc_id = rag.db chunks.doc_id",
    },
}

LOW28 = (1 << 28) - 1      # a float32 midpoint has at most 25 significant bits: a double whose low 28 bits are 0
MiB = 1 << 20
T0 = time.monotonic()


class Refused(Exception):
    """The export must not or cannot start; nothing was written."""


class Failed(Exception):
    """Something went wrong on the way; the partial output is removed."""


def log(message: str) -> None:
    print(f"[{time.monotonic() - T0:7.1f}s] {message}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def canonical_json(data: Any) -> bytes:
    return (json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while block := f.read(4 * MiB):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def lower_priority() -> str:
    """Niceness 19 and, if psutil is there, the idle I/O class; a job already runs at Nice=10, CPUWeight and
    IOWeight 20 (deployctl)."""
    done = []
    if hasattr(os, "nice"):
        try:
            done.append(f"nice {os.nice(max(0, 19 - os.nice(0)))}")
        except OSError:
            pass
    try:
        import psutil  # noqa: PLC0415  (production's venv has it; optional)
        psutil.Process().ionice(psutil.IOPRIO_CLASS_IDLE)
        done.append("ionice idle")
    except Exception:  # noqa: BLE001  (absent, or not supported here)
        pass
    return ", ".join(done) or "unchanged"


# ---------------------------------------------------------------- SQLite, read-only


def sqlite_ro(path: Path) -> sqlite3.Connection:
    """mode=ro&immutable=1: SQLite opens the file read-only and never locks it, never looks for or makes a -wal or
    -shm, and never writes. Right for a served root, whose files never change, once scan_root has refused a WAL
    that holds frames (immutable ignores the WAL)."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def corpus_fingerprint(sha256s) -> str:
    """rag_core.corpus.corpus_fingerprint (kept stdlib-only here: the VM runs this file without rag_core)."""
    return hashlib.sha256("\n".join(sorted(sha256s)).encode("ascii")).hexdigest()[:16]


def bm25_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    split = has_table(conn, OPTIN_FTS) and has_table(conn, OPTIN_DOCS)          # rag_core.db.is_split
    return (DEFAULT_FTS, OPTIN_FTS) if split else (DEFAULT_FTS,)


def rag_db_stats(db_path: Path) -> dict[str, Any]:
    """The counts of rag_core.status.corpus_stats (/v2/status), plus dense-indexed chunks."""
    conn = sqlite_ro(db_path)
    try:
        shas = [row[0] for row in conn.execute("SELECT sha256 FROM documents")]
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        bm25 = {
            table: conn.execute(f"SELECT count(*) FROM {table}_docsize").fetchone()[0]
            for table in bm25_tables(conn)
            if has_table(conn, f"{table}_docsize")
        }
        structure = None
        n_struct = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('doc_sources', 'doc_sections', 'chunk_spans')").fetchone()[0]
        if n_struct == 3 and conn.execute("SELECT 1 FROM doc_sources LIMIT 1").fetchone() is not None:
            structure = {
                "documents": conn.execute("SELECT COUNT(*) FROM doc_sources").fetchone()[0],
                "sections": conn.execute("SELECT COUNT(*) FROM doc_sections").fetchone()[0],
            }
        dense_indexed = (conn.execute("SELECT COUNT(*) FROM index_state WHERE dense_indexed = 1").fetchone()[0]
                         if has_table(conn, "index_state") else None)
    finally:
        conn.close()
    return {
        "n_docs": len(shas),
        "n_chunks": int(n_chunks),
        "corpus_fingerprint": corpus_fingerprint(shas) if shas else None,
        "bm25_tables": bm25,
        "structure": structure,
        "dense_indexed": dense_indexed,
    }


def kb_stats(kb_path: Path) -> dict[str, int]:
    """Row count per table (rag_core.status.kb_stats)."""
    conn = sqlite_ro(kb_path)
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}
    finally:
        conn.close()


def chunk_map(db_path: Path) -> dict[str, tuple[str, int]]:
    """chunk_id -> (doc_id, dense_indexed) for every chunk of rag.db."""
    conn = sqlite_ro(db_path)
    try:
        join = has_table(conn, "index_state")
        sql = ("SELECT c.chunk_id, c.doc_id, COALESCE(s.dense_indexed, 0) FROM chunks AS c "
               "LEFT JOIN index_state AS s ON s.chunk_id = c.chunk_id") if join else \
              "SELECT chunk_id, doc_id, 0 FROM chunks"
        return {row[0]: (row[1], int(row[2])) for row in conn.execute(sql)}
    finally:
        conn.close()


def quick_check(path: Path) -> str:
    conn = sqlite_ro(path)
    try:
        return "; ".join(str(row[0]) for row in conn.execute("PRAGMA quick_check"))
    finally:
        conn.close()


def versions() -> dict[str, Any]:
    """This interpreter as the export ran it: the facts H0 could not read through the dispatcher."""
    import importlib.metadata as md  # noqa: PLC0415
    conn = sqlite3.connect(":memory:")
    try:
        options = [row[0] for row in conn.execute("PRAGMA compile_options")]
    finally:
        conn.close()
    packages = sorted({f"{d.metadata['Name']}=={d.version}" for d in md.distributions() if d.metadata["Name"]},
                      key=str.lower)
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "unicodedata": unicodedata.unidata_version,
        "sqlite": sqlite3.sqlite_version,
        "sqlite_compile_options": options,
        "packages": packages,
    }


def repo_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", head) else None


# ---------------------------------------------------------------- the root's entries


class Entry:
    __slots__ = ("rel", "kind", "size", "mode", "mtime_ns", "ino", "target")

    def __init__(self, rel: str, kind: str, st: os.stat_result, target: str | None = None) -> None:
        self.rel, self.kind, self.target = rel, kind, target
        self.size = st.st_size if kind == "file" else 0
        self.mode = stat.S_IMODE(st.st_mode)
        self.mtime_ns = st.st_mtime_ns
        self.ino = st.st_ino

    def signature(self) -> tuple:
        """What must not change while the export runs. A directory's mtime is left out: SQLite deletes a database's
        -wal and -shm when its last connection closes and the next connection makes them again, which touches
        sqlite/ without changing any data (the files themselves are compared, and the sidecars checked)."""
        return (self.rel, self.kind, self.size, self.mtime_ns if self.kind == "file" else None, self.ino,
                self.target)


def file_signature(st: os.stat_result) -> tuple[int, int, int]:
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _tree_size(path: str) -> tuple[int, int]:
    """Files and bytes under path; entries that vanish meanwhile (trace pruning) are skipped."""
    files = size = 0
    try:
        with os.scandir(path) as it:
            items = list(it)
    except OSError:
        return 0, 0
    for item in items:
        try:
            if item.is_dir(follow_symlinks=False):
                f, s = _tree_size(item.path)
                files, size = files + f, size + s
            else:
                files += 1
                size += item.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return files, size


def scan_root(root: Path, include_logs: bool) -> tuple[list[Entry], list[dict[str, Any]], list[str]]:
    """Every entry under root in a fixed order (names sorted, depth first), what is left out, and the problems
    that refuse the export."""
    entries: list[Entry] = []
    excluded: list[dict[str, Any]] = []
    problems: list[str] = []

    def walk(directory: Path, parts: list[str]) -> None:
        with os.scandir(directory) as it:
            items = sorted(it, key=lambda e: e.name)
        for item in items:
            rel = "/".join(parts + [item.name])
            path = directory / item.name
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                if not SIDECAR_RE.match(item.name):        # a -wal or -shm comes and goes with connections
                    problems.append(f"{rel}: vanished while the root was read")
                continue
            if not parts and item.name == "logs" and stat.S_ISDIR(st.st_mode) and not include_logs:
                files, size = _tree_size(str(path))
                excluded.append({"path": "logs/", "kind": "directory", "files": files, "bytes": size,
                                 "why": "retrieval traces the serving process writes (rag_core.traces), not the "
                                        "generation; --include-logs ships them"})
                continue
            if stat.S_ISDIR(st.st_mode):
                entries.append(Entry(rel, "dir", st))
                walk(path, parts + [item.name])
            elif stat.S_ISLNK(st.st_mode):
                target = os.readlink(path)
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
                if posixpath.isabs(target) or resolved == ".." or resolved.startswith("../"):
                    problems.append(f"{rel}: a symlink that leaves the root ({target})")
                else:
                    entries.append(Entry(rel, "symlink", st, target))
            elif stat.S_ISREG(st.st_mode):
                side = SIDECAR_RE.match(item.name)
                if side and (directory / side.group("db")).is_file():
                    kind = side.group("kind")
                    if kind == "wal" and st.st_size:
                        problems.append(f"{rel}: the WAL holds {st.st_size} bytes, so the database file alone is "
                                        "not the database (a served root is never written: what writes it?)")
                    if kind == "journal" and st.st_size:
                        problems.append(f"{rel}: a rollback journal of {st.st_size} bytes (a writer, or a hot "
                                        "journal)")
                    excluded.append({"path": rel, "kind": f"sqlite-{kind}", "files": 1, "bytes": st.st_size,
                                     "why": {"wal": "an empty WAL holds no change",
                                             "shm": "SQLite's shared-memory lock index, not data",
                                             "journal": "an empty journal holds no change"}[kind]})
                    continue
                if not os.access(path, os.R_OK):
                    problems.append(f"{rel}: not readable")
                entries.append(Entry(rel, "file", st))
            else:
                problems.append(f"{rel}: not a regular file, directory or symlink")

    walk(root, [])
    return entries, excluded, problems


# ---------------------------------------------------------------- Qdrant, read-only


class QdrantReader:
    """Qdrant's REST API, reads only: GET /, GET /collections/NAME, POST /collections/NAME/points/scroll. Any other
    request raises PermissionError before it is sent. Raw response bodies are returned (the scroll pages ship as
    received)."""

    def __init__(self, url: str, collection: str, timeout: float = 120.0) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise Refused(f"Qdrant URL {url!r} is not http(s)://host:port")
        self.base = url.rstrip("/")
        self.collection = collection
        self.timeout = timeout
        coll = "/collections/" + urllib.parse.quote(collection, safe="")
        self.allowed = {("GET", "/"), ("GET", coll), ("POST", coll + "/points/scroll")}
        self._coll = coll
        self.sent: list[tuple[str, str]] = []
        # loopback in production: never through an environment proxy
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, body: Any = None) -> bytes:
        if (method, path) not in self.allowed:
            raise PermissionError(f"export_generation reads Qdrant only: {method} {path} is not one of its reads")
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        self.sent.append((method, path))
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise Failed(f"Qdrant {method} {path}: HTTP {exc.code}: {exc.read()[:300]!r}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise Failed(f"Qdrant {method} {path}: {exc}") from None

    def server(self) -> dict[str, Any]:
        return json.loads(self.request("GET", "/"))

    def collection_info(self) -> dict[str, Any]:
        doc = json.loads(self.request("GET", self._coll))
        if doc.get("status") != "ok" or not isinstance(doc.get("result"), dict):
            raise Failed(f"Qdrant collection {self.collection}: unexpected answer {str(doc)[:300]}")
        return doc["result"]

    def scroll(self, offset: Any, limit: int) -> bytes:
        body: dict[str, Any] = {"limit": limit, "with_payload": PAYLOAD_FIELDS, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        return self.request("POST", self._coll + "/points/scroll", body)


def collection_problems(info: dict[str, Any], dim: int) -> list[str]:
    problems = []
    params = ((info.get("config") or {}).get("params") or {})
    vectors = params.get("vectors")
    if not isinstance(vectors, dict) or "size" not in vectors:
        problems.append(f"the collection's vectors are not one unnamed vector: {str(vectors)[:200]}")
    else:
        if int(vectors["size"]) != dim:
            problems.append(f"vector size {vectors['size']}, the root's dense.json says {dim}")
        if str(vectors.get("distance", "")).lower() != "cosine":
            problems.append(f"distance {vectors.get('distance')}, not Cosine")
        if str(vectors.get("datatype") or "float32").lower() != "float32":
            problems.append(f"vector datatype {vectors.get('datatype')}: the export ships float32 rows")
    if params.get("sparse_vectors"):
        problems.append("the collection has sparse vectors")
    if not isinstance(info.get("points_count"), int) or info["points_count"] <= 0:
        problems.append(f"points_count {info.get('points_count')!r}")
    return problems


# ---------------------------------------------------------------- vectors: exact float32 from Qdrant's text


def f32_step(x: float, up: bool) -> float:
    """The float32 next to the float32 x, upwards or downwards."""
    if x == 0:
        bits = 0x00000001 if up else 0x80000001
    else:
        bits = struct.unpack("<I", struct.pack("<f", x))[0]
        bits = bits + 1 if (x > 0) == up else bits - 1
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def exact_f32(doubles: array, texts: Callable[[], list[str]]) -> tuple[array, int, int]:
    """float32 values nearest (ties to even) to the decimal texts whose correctly rounded doubles are `doubles`.
    The C cast (array 'f') is right except where the double is exactly halfway between two float32: such a
    midpoint is itself a double, so rounding the text to a double can land on it, never cross it. Those doubles
    have their low 28 mantissa bits zero; each is decided again from the exact decimal value. texts() is called
    only when there is one. Returns (values, midpoints met, values corrected)."""
    values = array("f", doubles)
    if not len(doubles):
        return values, 0, 0
    bits = memoryview(doubles).cast("B").cast("Q")
    candidates = [i for i, b in enumerate(bits) if not b & LOW28]
    midpoints = corrected = 0
    decimal: list[str] | None = None
    for i in candidates:
        x, cast = doubles[i], values[i]
        if x == cast or math.isinf(cast) or math.isnan(x):
            continue
        other = f32_step(cast, up=x > cast)
        mid = (cast + other) / 2
        if x != mid:
            continue
        midpoints += 1
        if decimal is None:
            decimal = texts()
        exact, half = Fraction(decimal[i]), Fraction(mid)
        if exact == half:
            continue                                  # a true tie: the cast's half-to-even is the rule
        want = max(cast, other) if exact > half else min(cast, other)
        if want != cast:
            values[i] = want
            corrected += 1
    return values, midpoints, corrected


def page_texts(raw: bytes) -> list[str]:
    """The vector components of a scroll page as the decimal text Qdrant wrote, in row order."""
    doc = json.loads(raw, parse_float=str, parse_int=str)
    out: list[str] = []
    for point in doc["result"]["points"]:
        out.extend(point["vector"])
    return out


class Page:
    __slots__ = ("ids", "payloads", "values", "next_offset", "midpoints", "corrected")


def parse_page(raw: bytes, dim: int) -> Page:
    doc = json.loads(raw)
    if doc.get("status") != "ok" or not isinstance(doc.get("result"), dict):
        raise Failed(f"scroll page: unexpected answer {raw[:200]!r}")
    points = doc["result"].get("points")
    if not isinstance(points, list):
        raise Failed("scroll page without points")
    doubles = array("d")
    page = Page()
    page.ids, page.payloads = [], []
    for point in points:
        vector = point.get("vector")
        if not isinstance(vector, list):
            raise Refused(f"point {point.get('id')!r}: the vector is not one unnamed list ({type(vector).__name__})")
        if len(vector) != dim:
            raise Failed(f"point {point.get('id')!r}: {len(vector)} components, not {dim}")
        doubles.extend(vector)
        page.ids.append(point.get("id"))
        page.payloads.append(point.get("payload"))
    page.values, page.midpoints, page.corrected = exact_f32(doubles, lambda: page_texts(raw))
    page.next_offset = doc["result"].get("next_page_offset")
    return page


def point_record(pid: Any, payload: Any, chunks: dict[str, tuple[str, int]]) -> tuple[bytes, int, int]:
    """(the 96-byte points.bin record, its flags, the id as an integer for the order check)."""
    if not isinstance(pid, str):
        raise Refused(f"point id {pid!r} is not a UUID string: points.bin holds UUIDs only")
    try:
        uid = uuid.UUID(pid)
    except ValueError:
        raise Refused(f"point id {pid!r} is not a UUID") from None
    if str(uid) != pid:
        raise Refused(f"point id {pid!r} is not in canonical form")
    payload = payload if isinstance(payload, dict) else {}
    flags = 0
    chunk_bytes = doc_bytes = bytes(32)
    row = None
    cid, did = payload.get("chunk_id"), payload.get("doc_id")
    if cid is not None:
        m = CHUNK_ID_RE.match(cid) if isinstance(cid, str) else None
        if not m:
            raise Refused(f"point {pid}: payload chunk_id {cid!r} is not chk_<64 hex>")
        chunk_bytes = bytes.fromhex(m.group(1))
        flags |= F_CHUNK_PAYLOAD
        if str(uuid.uuid5(uuid.NAMESPACE_URL, POINT_NAME_PREFIX + cid)) == pid:
            flags |= F_ID_IS_UUID5
        row = chunks.get(cid)
        if row is not None:
            flags |= F_CHUNK_IN_DB
            if row[1] == 1:
                flags |= F_DENSE_INDEXED
    if did is not None:
        m = DOC_ID_RE.match(did) if isinstance(did, str) else None
        if not m:
            raise Refused(f"point {pid}: payload doc_id {did!r} is not doc_<64 hex>")
        doc_bytes = bytes.fromhex(m.group(1))
        flags |= F_DOC_PAYLOAD
        if row is not None and row[0] == did:
            flags |= F_DOC_MATCHES
    return POINT_RECORD.pack(uid.bytes, chunk_bytes, doc_bytes, flags), flags, uid.int


# ---------------------------------------------------------------- tar plumbing


class HashingWriter:
    def __init__(self, raw: BinaryIO) -> None:
        self.raw, self.sha, self.bytes = raw, hashlib.sha256(), 0

    def write(self, data) -> int:
        self.raw.write(data)
        self.sha.update(data)
        self.bytes += len(data)
        return len(data)

    def flush(self) -> None:
        self.raw.flush()


class HashingReader:
    def __init__(self, raw: BinaryIO) -> None:
        self.raw, self.sha, self.bytes = raw, hashlib.sha256(), 0

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.sha.update(data)
        self.bytes += len(data)
        return data


class BufferReader:
    """read() over a buffer without copying it whole (vectors.f32 is N x DIM x 4 bytes)."""

    def __init__(self, buf) -> None:
        self.view, self.pos = memoryview(buf).cast("B"), 0

    def read(self, n: int = -1) -> bytes:
        end = len(self.view) if n is None or n < 0 else min(len(self.view), self.pos + n)
        data = bytes(self.view[self.pos:end])
        self.pos = end
        return data


def tar_info(name: str, kind: str, *, size: int = 0, mode: int = 0o640, mtime: int = 0,
             target: str | None = None) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = {"dir": tarfile.DIRTYPE, "file": tarfile.REGTYPE, "symlink": tarfile.SYMTYPE}[kind]
    info.size = size if kind == "file" else 0
    info.mode = mode & 0o7777
    info.mtime = int(mtime)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if target is not None:
        info.linkname = target
    return info


class Archive:
    """The tar stream with the manifest's file list kept beside it (paths relative to GENERATION/)."""

    def __init__(self, tar: tarfile.TarFile, generation: str, mtime: int) -> None:
        self.tar, self.prefix, self.mtime = tar, generation + "/", mtime
        self.files: list[dict[str, Any]] = []
        self.bytes = 0

    def add_dir(self, rel: str, mode: int = 0o750, mtime: int | None = None) -> None:
        name = self.prefix + rel if rel else self.prefix.rstrip("/")
        self.tar.addfile(tar_info(name, "dir", mode=mode, mtime=self.mtime if mtime is None else mtime))
        if rel:
            self.files.append({"path": rel, "kind": "dir", "mode": f"{mode & 0o7777:04o}"})

    def add_symlink(self, rel: str, target: str, mtime: int) -> None:
        self.tar.addfile(tar_info(self.prefix + rel, "symlink", mode=0o777, mtime=mtime, target=target))
        self.files.append({"path": rel, "kind": "symlink", "target": target})

    def add_stream(self, rel: str, reader, size: int, *, mode: int = 0o640, mtime: int | None = None,
                   extra: dict[str, Any] | None = None) -> str:
        hashing = HashingReader(reader)
        self.tar.addfile(tar_info(self.prefix + rel, "file", size=size, mode=mode,
                                  mtime=self.mtime if mtime is None else mtime), hashing)
        if hashing.bytes != size:
            raise Failed(f"{rel}: {hashing.bytes} bytes read, {size} expected")
        digest = hashing.sha.hexdigest()
        self.files.append({"path": rel, "kind": "file", "size": size, "sha256": digest,
                           "mode": f"{mode & 0o7777:04o}", **(extra or {})})
        self.bytes += size
        return digest

    def add_bytes(self, rel: str, data: bytes, **kw) -> str:
        return self.add_stream(rel, BufferReader(data), len(data), **kw)

    def add_root_file(self, rel: str, path: Path, entry: Entry) -> str:
        """The file as it is, checked unchanged (size, mtime, inode) before, during and after the copy."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            want = (entry.size, entry.mtime_ns, entry.ino)
            if file_signature(os.fstat(f.fileno())) != want:
                raise Failed(f"root/{entry.rel} changed after the root was scanned")
            digest = self.add_stream(rel, f, entry.size, mode=entry.mode, mtime=entry.mtime_ns // 1_000_000_000,
                                     extra={"mtime_ns": entry.mtime_ns})
            if f.read(1):
                raise Failed(f"root/{entry.rel} grew while it was copied")
            if file_signature(os.fstat(f.fileno())) != want:
                raise Failed(f"root/{entry.rel} changed while it was copied")
        if file_signature(os.lstat(path)) != want:
            raise Failed(f"root/{entry.rel} was replaced while it was copied")
        return digest


# ---------------------------------------------------------------- preflight (check and export)


class Plan:
    """What preflight found; export streams from it."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.notes: list[str] = []


def _out_dir_problems(out_dir: Path, forbidden: list[tuple[str, Path]], *, strict: bool = True) -> list[str]:
    """--out-dir must be absolute, outside the root, $RAG_ROOT and this repository, and (strict) a directory of
    this user with mode 0700 when it exists."""
    problems = []
    if not out_dir.is_absolute():
        problems.append(f"--out-dir {out_dir} is not an absolute path")
        return problems
    resolved = Path(os.path.realpath(out_dir))
    for what, base in forbidden:
        base = Path(os.path.realpath(base))
        if resolved == base or base in resolved.parents:
            problems.append(f"--out-dir {out_dir} lies in {what} ({base})")
    if out_dir.exists():
        if not out_dir.is_dir() or out_dir.is_symlink():
            problems.append(f"--out-dir {out_dir} is not a plain directory")
        elif strict and hasattr(os, "getuid"):
            st = os.stat(out_dir)
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                problems.append(f"--out-dir {out_dir} must be owned by this user and mode 0700 "
                                f"(is uid {st.st_uid}, {stat.S_IMODE(st.st_mode):04o})")
    return problems


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def preflight(args: argparse.Namespace, env: dict[str, str], *, exporting: bool) -> Plan:
    plan = Plan()
    p = plan.problems
    root = Path(args.root)
    if not root.is_absolute():
        raise Refused(f"--root {root} is not an absolute path")
    if not root.is_dir():
        raise Refused(f"--root {root} is not a directory")
    plan.root = Path(os.path.realpath(root))
    plan.repo = REPO
    plan.started = now_iso()
    plan.mtime = int(time.time())
    plan.deadline = time.monotonic() + 60 * args.max_minutes

    # the served root
    rag_root = env.get("RAG_ROOT")
    plan.rag_root = rag_root
    plan.served = bool(rag_root) and Path(os.path.realpath(rag_root)) == plan.root
    if not plan.served and not args.not_served_ok:
        p.append(f"--root is not the served root (RAG_ROOT={rag_root or 'unset'}); --not-served-ok exports it anyway")

    # generation
    if not GENERATION_RE.fullmatch(args.expect_generation or ""):
        raise Refused(f"--expect-generation {args.expect_generation!r} is not a generation id")
    if not FINGERPRINT_RE.fullmatch(args.expect_fingerprint or ""):
        raise Refused(f"--expect-fingerprint {args.expect_fingerprint!r} is not 16 hex digits")
    plan.generation = args.expect_generation
    seen = {"directory name": plan.root.name}
    serving_path = plan.root / "config" / "serving.json"
    report_path = plan.root / "BUILD_REPORT.json"
    report = read_json(report_path) if report_path.is_file() else {}
    if serving_path.is_file():
        seen["config/serving.json"] = read_json(serving_path).get("generation")
    else:
        p.append("config/serving.json is missing: the root names no generation")
    if report:
        seen["BUILD_REPORT.json"] = report.get("generation")
    for where, value in seen.items():
        if value != plan.generation:
            p.append(f"generation: {where} says {value!r}, not {plan.generation}")

    # fingerprint and counts
    db_path = plan.root / "sqlite" / "rag.db"
    kb_path = plan.root / "kb" / "kb.sqlite"
    if not db_path.is_file():
        raise Refused(f"{db_path} does not exist")
    plan.db_stats = rag_db_stats(db_path)
    plan.kb_stats = kb_stats(kb_path) if kb_path.is_file() else None
    fps = {"rag.db documents": plan.db_stats["corpus_fingerprint"]}
    if report:
        fps["BUILD_REPORT.json"] = (report.get("corpus") or {}).get("fingerprint")
    manifest_path = plan.root / "sources" / "manifest.json"
    if manifest_path.is_file():
        entries = read_json(manifest_path)
        fps["sources/manifest.json"] = corpus_fingerprint(e["sha256"] for e in entries) if entries else None
    plan.fingerprints = fps
    for where, value in fps.items():
        if value != args.expect_fingerprint:
            p.append(f"fingerprint: {where} gives {value}, not {args.expect_fingerprint}")
    plan.fingerprint = args.expect_fingerprint
    for flag, have in (("docs", plan.db_stats["n_docs"]), ("chunks", plan.db_stats["n_chunks"])):
        want = getattr(args, f"expect_{flag}")
        if want is not None and want != have:
            p.append(f"rag.db holds {have} {flag}, not {want}")

    # the entries
    plan.entries, plan.excluded, problems = scan_root(plan.root, args.include_logs)
    p.extend(problems)
    plan.root_bytes = sum(e.size for e in plan.entries)

    # the collection
    repo_dense = REPO / "config" / "dense.json"
    merged = read_json(repo_dense) if repo_dense.is_file() else {}
    overlay_path = plan.root / "config" / "dense.json"
    overlay = read_json(overlay_path) if overlay_path.is_file() else {}
    if not overlay.get("collection"):
        p.append("config/dense.json names no collection of the root's own")
    merged.update(overlay)
    env_collection = env.get("RAG_DENSE_COLLECTION")
    collection = env_collection or merged.get("collection")
    if env_collection and overlay.get("collection") and env_collection != overlay["collection"] \
            and args.collection != env_collection:
        p.append(f"RAG_DENSE_COLLECTION={env_collection} overrides the root's {overlay['collection']}: say which "
                 "one with --collection")
    if args.collection and args.collection != collection:
        p.append(f"--collection {args.collection}, but the root serves {collection}")
    plan.collection = collection
    plan.qdrant_url = args.qdrant_url or env.get("RAG_QDRANT_URL") or merged.get("qdrant_url")
    plan.dim = int(merged.get("vector_size") or 0)
    if not collection or not plan.qdrant_url or plan.dim <= 0:
        raise Refused(f"dense config incomplete: collection {collection!r}, qdrant_url {plan.qdrant_url!r}, "
                      f"vector_size {plan.dim}")
    plan.reader = QdrantReader(plan.qdrant_url, collection)
    try:
        plan.qdrant_server = plan.reader.server()
        plan.collection_info = plan.reader.collection_info()
    except Failed as exc:
        raise Refused(str(exc)) from None
    p.extend(collection_problems(plan.collection_info, plan.dim))
    plan.points = plan.collection_info.get("points_count") or 0
    if args.expect_points is not None and plan.points != args.expect_points:
        p.append(f"the collection holds {plan.points} points, not {args.expect_points}")
    plan.vector_bytes = plan.points * plan.dim * 4
    if plan.vector_bytes > args.max_vector_mib * MiB:
        p.append(f"the vectors need {human(plan.vector_bytes)} in memory, over --max-vector-mib {args.max_vector_mib}")
    try:
        probe = parse_page(plan.reader.scroll(None, 1), plan.dim)
        if probe.ids:
            point_record(probe.ids[0], probe.payloads[0], {})
            payload = probe.payloads[0] or {}
            plan.notes.append(f"probe: 1 point scrolled, payload {sorted(payload)}")
    except (Refused, Failed) as exc:
        p.append(f"probe scroll: {exc}")

    # age, the recipient and the out-dir
    plan.age = args.age or shutil.which("age")
    plan.age_version = None
    if plan.age:
        try:
            out = subprocess.run([plan.age, "--version"], capture_output=True, text=True, timeout=30, check=False)
            plan.age_version = (out.stdout or out.stderr).strip() or None
        except OSError as exc:
            p.append(f"age at {plan.age} does not run: {exc}")
    elif not getattr(args, "_encrypt_argv", None):
        p.append("age is not installed on this host (Ubuntu: sudo apt-get install age)")
    if args.recipient is not None:
        if not AGE_RECIPIENT_RE.fullmatch(args.recipient):
            p.append("--recipient is not an age X25519 recipient (age1 and 58 bech32 characters)")
        elif plan.age and not getattr(args, "_encrypt_argv", None):
            out = subprocess.run([plan.age, "-r", args.recipient], input=b"", capture_output=True, timeout=30,
                                 check=False)
            if out.returncode != 0:
                p.append(f"age refuses the recipient: {out.stderr.decode(errors='replace').strip()[:200]}")
    elif exporting:
        p.append("--recipient is required")
    plan.out_dir = Path(args.out_dir) if args.out_dir else None
    plan.estimate = int((plan.root_bytes + plan.vector_bytes + plan.points * (POINT_RECORD.size + plan.dim * 6)
                         + 1024 * (len(plan.entries) + 200)) * 1.05)
    if plan.out_dir is not None:
        forbidden = [("the root", plan.root), ("this repository", REPO)]
        if rag_root:
            forbidden.append(("$RAG_ROOT", Path(rag_root)))
        p.extend(_out_dir_problems(plan.out_dir, forbidden))
        if plan.out_dir.is_absolute():
            plan.free = _free_bytes(plan.out_dir)
            if plan.free < plan.estimate + (1 << 30):
                p.append(f"--out-dir has {human(plan.free)} free; the export needs about {human(plan.estimate)} "
                         "and 1 GiB to spare")
            name = f"export-{plan.fingerprint}.tar.age"
            for existing in (name, name + ".json", f".{name}.partial"):
                if (plan.out_dir / existing).exists():
                    p.append(f"{plan.out_dir / existing} exists: run clean first")
    elif exporting:
        p.append("--out-dir is required")
    return plan


def report_plan(plan: Plan, args: argparse.Namespace) -> None:
    s = plan.db_stats
    log(f"root: {plan.root} ({'served: RAG_ROOT names it' if plan.served else 'NOT the served root'})")
    log(f"generation: {plan.generation}; fingerprint {plan.fingerprint} by "
        + ", ".join(f"{k} {v}" for k, v in plan.fingerprints.items()))
    log(f"rag.db: {s['n_docs']} documents, {s['n_chunks']} chunks, bm25 {s['bm25_tables']}, structure "
        f"{s['structure']}, {s['dense_indexed']} dense-indexed")
    if plan.kb_stats is not None:
        log(f"kb.sqlite: {len(plan.kb_stats)} tables, {sum(plan.kb_stats.values())} rows")
    kinds = {k: sum(1 for e in plan.entries if e.kind == k) for k in ("file", "dir", "symlink")}
    log(f"entries: {kinds['file']} files ({human(plan.root_bytes)}), {kinds['dir']} directories, "
        f"{kinds['symlink']} symlinks")
    for item in plan.excluded:
        log(f"  left out: {item['path']} ({item['files']} files, {human(item['bytes'])}): {item['why']}")
    info = plan.collection_info
    vectors = ((info.get("config") or {}).get("params") or {}).get("vectors") or {}
    log(f"qdrant {plan.qdrant_server.get('version')} at {plan.qdrant_url}: {plan.collection}, {plan.points} points, "
        f"{vectors.get('size')}-d {vectors.get('distance')} {vectors.get('datatype') or 'float32'}, quantization "
        f"{(info.get('config') or {}).get('quantization_config')}, status {info.get('status')}")
    log(f"dense: vectors {human(plan.vector_bytes)} ({plan.points} x {plan.dim} float32), "
        f"{math.ceil(plan.points / args.page_size)} pages of {args.page_size}")
    for note in plan.notes:
        log(note)
    log(f"age: {plan.age or 'NOT FOUND'}{' (' + plan.age_version + ')' if plan.age_version else ''}; recipient "
        f"{'given' if args.recipient else 'not given'}")
    if plan.out_dir is not None:
        log(f"out-dir: {plan.out_dir} ({'exists' if plan.out_dir.exists() else 'absent: made 0700 by export'}), "
            f"{human(getattr(plan, 'free', 0))} free; the export needs about {human(plan.estimate)}")


# ---------------------------------------------------------------- export


def past_deadline(plan: Plan, where: str) -> None:
    if time.monotonic() > plan.deadline:
        raise Failed(f"--max-minutes passed during {where}")


def export_dense(archive: Archive, plan: Plan, args: argparse.Namespace) -> dict[str, Any]:
    dim, n = plan.dim, plan.points
    chunks = chunk_map(plan.root / "sqlite" / "rag.db")
    values = array("f", [0.0]) * (n * dim)
    rows_view = memoryview(values)
    records = bytearray(n * POINT_RECORD.size)
    pages: list[dict[str, Any]] = []
    flag_counts = {bit: 0 for bit in (1, 2, 4, 8, 16, 32)}
    row, offset, last_id, midpoints, corrected = 0, None, -1, 0, 0
    total_pages = math.ceil(n / args.page_size)
    started = time.monotonic()
    while True:
        past_deadline(plan, "the scroll")
        raw = plan.reader.scroll(offset, args.page_size)
        page = parse_page(raw, dim)
        k = len(page.ids)
        if row + k > n:
            raise Failed(f"the scroll returned more than the collection's {n} points")
        for j in range(k):
            record, flags, id_int = point_record(page.ids[j], page.payloads[j], chunks)
            if id_int <= last_id:
                raise Failed(f"scroll order: point {page.ids[j]} does not follow the previous id")
            last_id = id_int
            records[(row + j) * POINT_RECORD.size:(row + j + 1) * POINT_RECORD.size] = record
            for bit in flag_counts:
                flag_counts[bit] += bool(flags & bit)
        rows_view[row * dim:(row + k) * dim] = page.values
        midpoints += page.midpoints
        corrected += page.corrected
        index = len(pages)
        packed = gzip.compress(raw, compresslevel=args.gzip_level, mtime=0)
        rel = f"dense/scroll/page-{index:05d}.json.gz"
        gz_sha = archive.add_bytes(rel, packed, extra={"raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest()})
        pages.append({"path": rel, "points": k, "first_id": page.ids[0] if k else None,
                      "last_id": page.ids[-1] if k else None, "raw_bytes": len(raw),
                      "raw_sha256": hashlib.sha256(raw).hexdigest(), "gz_bytes": len(packed), "gz_sha256": gz_sha})
        row += k
        if index % 10 == 9 or page.next_offset is None:
            rate = row / max(time.monotonic() - started, 1e-6)
            log(f"dense: page {index + 1}/{total_pages}, {row}/{n} points, {rate:.0f} points/s")
        if page.next_offset is None:
            break
        if not k:
            raise Failed("an empty scroll page with a next offset")
        offset = page.next_offset
        if args.pause_ms:
            time.sleep(args.pause_ms / 1000)
    if row != n:
        raise Failed(f"the scroll returned {row} points, the collection holds {n}")
    after = plan.reader.collection_info().get("points_count")
    if after != n:
        raise Failed(f"the collection changed during the export: {n} points before, {after} after")

    # sanity statistics (not gates): norms, exact duplicate rows
    norm_min, norm_max, groups = math.inf, -math.inf, {}
    sumprod = getattr(math, "sumprod", None)
    for r in range(n):
        vec = values[r * dim:(r + 1) * dim]
        s = sumprod(vec, vec) if sumprod else math.fsum(v * v for v in vec)
        if not math.isfinite(s):
            raise Failed(f"row {r}: a value that is not finite")
        norm = math.sqrt(s)
        norm_min, norm_max = min(norm_min, norm), max(norm_max, norm)
        key = hashlib.sha256(vec.tobytes()).digest()
        groups[key] = groups.get(key, 0) + 1
    dup_groups = [c for c in groups.values() if c > 1]
    if sys.byteorder != "little":
        values.byteswap()
    vec_sha = archive.add_stream("dense/vectors.f32", BufferReader(values), n * dim * 4)
    del rows_view
    pts_sha = archive.add_bytes("dense/points.bin", bytes(records))
    flags_named = {POINT_LAYOUT["flags"][str(bit)]: count for bit, count in flag_counts.items()}
    export_doc = {
        "format": DENSE_FORMAT,
        "generation": plan.generation,
        "collection": plan.collection,
        "qdrant": {"url": plan.qdrant_url, "server": plan.qdrant_server},
        "collection_info": plan.collection_info,
        "points_count": n,
        "dim": dim,
        "dtype": "float32",
        "byte_order": "little",
        "row_order": "Qdrant scroll order: point id ascending (the UUID as a 128-bit big-endian integer), checked",
        "vectors": {"path": "dense/vectors.f32", "bytes": n * dim * 4, "sha256": vec_sha},
        "points": {"path": "dense/points.bin", "bytes": len(records), "sha256": pts_sha, "layout": POINT_LAYOUT},
        "scroll": {"limit": args.page_size, "with_payload": PAYLOAD_FIELDS, "with_vector": True,
                   "compression": f"gzip level {args.gzip_level}, mtime 0", "pages": pages},
        "parse": {"method": "CPython float() (correctly rounded double), C cast to float32, and the doubles that "
                            "fall exactly on a float32 midpoint rounded again from the decimal text (Fraction)",
                  "values": n * dim, "midpoints": midpoints, "corrected": corrected},
        "checks": {"points_count_before": n, "points_count_after": after, "ids_ascending": True,
                   "rows_with": flags_named, "rows": n},
        "stats": {"norm_min": norm_min, "norm_max": norm_max, "duplicate_vector_groups": len(dup_groups),
                  "rows_in_duplicate_groups": sum(dup_groups)},
    }
    blob = canonical_json(export_doc)
    archive.add_bytes("dense/EXPORT.json", blob)
    log(f"dense: {n} rows, vectors.f32 sha256 {vec_sha[:16]}..., {midpoints} midpoint doubles, {corrected} "
        f"corrected; norms {norm_min:.7f}..{norm_max:.7f}; {len(dup_groups)} duplicate-vector groups; "
        f"rows: " + ", ".join(f"{count} {what}" for what, count in flags_named.items()))
    return export_doc


def export_root(archive: Archive, plan: Plan) -> None:
    archive.add_dir("root", mode=stat.S_IMODE(os.stat(plan.root).st_mode),
                    mtime=os.stat(plan.root).st_mtime_ns // 1_000_000_000)
    total = max(plan.root_bytes, 1)
    done, next_mark, started = 0, 0.1, time.monotonic()
    for entry in plan.entries:
        past_deadline(plan, "the root's files")
        rel = "root/" + entry.rel
        path = plan.root.joinpath(*entry.rel.split("/"))
        if entry.kind == "dir":
            archive.add_dir(rel, mode=entry.mode, mtime=entry.mtime_ns // 1_000_000_000)
        elif entry.kind == "symlink":
            if os.readlink(path) != entry.target:
                raise Failed(f"{rel}: the symlink changed")
            archive.add_symlink(rel, entry.target, entry.mtime_ns // 1_000_000_000)
        else:
            archive.add_root_file(rel, path, entry)
            done += entry.size
            if done / total >= next_mark:
                rate = done / max(time.monotonic() - started, 1e-6)
                log(f"root: {done * 100 // total}% ({human(done)} of {human(total)}, {human(rate)}/s)")
                while next_mark <= done / total:
                    next_mark += 0.1


def export_repo_config(archive: Archive) -> list[str]:
    archive.add_dir("repo")
    archive.add_dir("repo/config")
    names = []
    config = REPO / "config"
    for path in sorted(config.glob("*.json")):
        archive.add_bytes(f"repo/config/{path.name}", path.read_bytes(), mode=0o644)
        names.append(path.name)
    return names


def run_export(args: argparse.Namespace, env: dict[str, str], encrypt_argv: list[str] | None) -> int:
    plan = preflight(args, env, exporting=True)
    report_plan(plan, args)
    if plan.problems:
        raise Refused("; ".join(plan.problems))
    out_dir = plan.out_dir
    name = f"export-{plan.fingerprint}.tar.age"
    final, partial, sidecar = out_dir / name, out_dir / f".{name}.partial", out_dir / f"{name}.json"
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    argv = encrypt_argv or [plan.age, "-r", args.recipient]
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    errors = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=fd, stderr=errors)
    except BaseException:
        os.close(fd)
        os.unlink(partial)
        errors.close()
        raise
    os.close(fd)
    log(f"export: {' '.join(argv[:2])} ... -> {partial}")
    tar = None
    try:
        sink = HashingWriter(proc.stdin)
        tar = tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT, copybufsize=MiB)
        archive = Archive(tar, plan.generation, plan.mtime)
        archive.add_dir("", mode=0o750)
        archive.add_dir("dense")
        archive.add_dir("dense/scroll")
        dense = export_dense(archive, plan, args)
        export_root(archive, plan)
        repo_files = export_repo_config(archive)

        # the whole tree once more: nothing may have changed while it was read
        entries2, excluded2, problems2 = scan_root(plan.root, args.include_logs)
        if problems2 or [e.signature() for e in entries2] != [e.signature() for e in plan.entries]:
            raise Failed("the root changed during the export: " + ("; ".join(problems2) or "entries differ"))
        if rag_db_stats(plan.root / "sqlite" / "rag.db") != plan.db_stats:
            raise Failed("rag.db's counts or fingerprint changed during the export")

        manifest = {
            "format": FORMAT,
            "exporter": {"script": "scripts/export_generation.py", "version": EXPORTER_VERSION,
                         "sha256": sha256_file(Path(__file__))[0], "repo_head": repo_head(REPO),
                         "repo_config": repo_files},
            "created_at": plan.started,
            "finished_at": now_iso(),
            "host": platform.node(),
            "generation": plan.generation,
            "corpus_fingerprint": plan.fingerprint,
            "fingerprint_sources": plan.fingerprints,
            "root": str(plan.root),
            "served": plan.served,
            "rag_root": plan.rag_root,
            "expected": {"generation": args.expect_generation, "fingerprint": args.expect_fingerprint,
                         "docs": args.expect_docs, "chunks": args.expect_chunks, "points": args.expect_points},
            "counts": {"rag_db": plan.db_stats, "kb": plan.kb_stats, "points": plan.points,
                       "root_files": sum(1 for e in plan.entries if e.kind == "file"),
                       "root_bytes": plan.root_bytes},
            "dense": {"path": "dense/EXPORT.json", "collection": plan.collection, "points": plan.points,
                      "dim": plan.dim, "midpoints": dense["parse"]["midpoints"],
                      "corrected": dense["parse"]["corrected"]},
            "versions": {**versions(), "qdrant": plan.qdrant_server},
            "excluded": plan.excluded,
            "files": archive.files,
        }
        manifest_bytes = canonical_json(manifest)
        tar.addfile(tar_info(archive.prefix + "MANIFEST.json", "file", size=len(manifest_bytes), mode=0o640,
                             mtime=plan.mtime), BufferReader(manifest_bytes))
        tar.close()
        proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            errors.seek(0)
            raise Failed(f"{argv[0]} exited {rc}: {errors.read()[-500:].decode(errors='replace').strip()}")
        with open(partial, "rb+") as f:
            os.fsync(f.fileno())
        cipher_sha, cipher_bytes = sha256_file(partial)
        os.replace(partial, final)
        summary = {
            "generation": plan.generation, "corpus_fingerprint": plan.fingerprint, "created_at": plan.started,
            "finished_at": now_iso(), "file": str(final), "recipient": args.recipient,
            "tar_bytes": sink.bytes, "tar_sha256": sink.sha.hexdigest(),
            "age_bytes": cipher_bytes, "age_sha256": cipher_sha,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "members": len(archive.files) + 2,              # + the top directory and MANIFEST.json
        }
        side_fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(side_fd, "wb") as f:
            f.write(canonical_json(summary))
            f.flush()
            os.fsync(f.fileno())
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(out_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        if tar is not None:                 # a half-written stream must not flush itself into a dead pipe later
            tar.closed = True
            tar.fileobj.closed = True
        if proc.poll() is None:
            proc.kill()
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait()
        for leftover in (partial, final, sidecar):      # preflight refused if any existed: all three are ours
            try:
                os.unlink(leftover)
            except FileNotFoundError:
                pass
        raise
    finally:
        errors.close()
    log(f"export: DONE {final}")
    log(f"export: generation {plan.generation} fingerprint {plan.fingerprint} members {summary['members']}")
    log(f"export: tar  bytes {summary['tar_bytes']} sha256 {summary['tar_sha256']}")
    log(f"export: age  bytes {summary['age_bytes']} sha256 {summary['age_sha256']}")
    log(f"export: MANIFEST.json sha256 {summary['manifest_sha256']}")
    return 0


def run_check(args: argparse.Namespace, env: dict[str, str]) -> int:
    plan = preflight(args, env, exporting=False)
    report_plan(plan, args)
    if plan.problems:
        for problem in plan.problems:
            log(f"NOT READY: {problem}")
        log(f"check: NOT READY ({len(plan.problems)} problems)")
        return 2
    log("check: READY")
    return 0


def run_clean(args: argparse.Namespace, env: dict[str, str]) -> int:
    out_dir = Path(args.out_dir)
    forbidden = [("this repository", REPO)]
    if env.get("RAG_ROOT"):
        forbidden.append(("$RAG_ROOT", Path(env["RAG_ROOT"])))
    problems = _out_dir_problems(out_dir, forbidden, strict=False)
    if problems:
        raise Refused("; ".join(problems))
    if not out_dir.exists():
        log(f"clean: {out_dir} does not exist; nothing to do")
        return 0
    names = sorted(os.listdir(out_dir))
    others = [n for n in names if not OUT_NAME_RE.match(n)]
    for n in names:
        if OUT_NAME_RE.match(n):
            path = out_dir / n
            size = path.stat().st_size
            path.unlink()
            log(f"clean: deleted {path} ({human(size)})")
    if others:
        log(f"clean: {len(others)} other entries left alone in {out_dir}: {others[:5]}")
    else:
        out_dir.rmdir()
        log(f"clean: removed {out_dir}")
    return 0


# ---------------------------------------------------------------- the build VM: unpack and verify


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if name.startswith("/") or path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name:
        raise Failed(f"unsafe member name {name!r}")
    return path


def compare_files(manifest: dict[str, Any], have: dict[str, tuple[str, Any]]) -> list[str]:
    """have: rel -> (kind, (size, sha256) for a file | target for a symlink | None for a directory)."""
    fails = []
    listed = {e["path"]: e for e in manifest["files"]}
    for rel, entry in listed.items():
        got = have.get(rel)
        if got is None:
            fails.append(f"{rel}: missing")
        elif got[0] != entry["kind"]:
            fails.append(f"{rel}: a {got[0]}, the manifest says {entry['kind']}")
        elif entry["kind"] == "file" and got[1] != (entry["size"], entry["sha256"]):
            fails.append(f"{rel}: size/sha256 {got[1][0]}/{got[1][1][:16]}..., the manifest says "
                         f"{entry['size']}/{entry['sha256'][:16]}...")
        elif entry["kind"] == "symlink" and got[1] != entry["target"]:
            fails.append(f"{rel}: symlink to {got[1]}, the manifest says {entry['target']}")
    for rel in sorted(set(have) - set(listed)):
        fails.append(f"{rel}: not in the manifest")
    return fails


def run_unpack(args: argparse.Namespace, stream: BinaryIO) -> int:
    into = Path(args.into)
    into.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = HashingReader(stream)
    tar = tarfile.open(fileobj=source, mode="r|")
    top: Path | None = None
    generation = None
    have: dict[str, tuple[str, Any]] = {}
    total = 0
    for member in tar:
        path = _safe_member(member.name)
        if generation is None:
            generation = path.parts[0]
            if not GENERATION_RE.fullmatch(generation):
                raise Failed(f"top directory {generation!r} is not a generation id")
            if args.expect_generation and generation != args.expect_generation:
                raise Failed(f"the tar holds {generation}, not {args.expect_generation}")
            top = into / generation
            if top.exists():
                raise Refused(f"{top} exists: remove it first")
            top.mkdir(mode=0o700)
            log(f"unpack: {generation} -> {top}")
        elif path.parts[0] != generation:
            raise Failed(f"member {member.name!r} outside {generation}/")
        rel = "/".join(path.parts[1:])
        target = into.joinpath(*path.parts)
        if member.isdir():
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            if rel:
                have[rel] = ("dir", None)
        elif member.isreg():
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            reader = tar.extractfile(member)
            digest, size = hashlib.sha256(), 0
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as out:
                while block := reader.read(4 * MiB):
                    digest.update(block)
                    out.write(block)
                    size += len(block)
            if size != member.size:
                raise Failed(f"{rel}: {size} bytes, the header says {member.size}")
            have[rel] = ("file", (size, digest.hexdigest()))
            total += size
        elif member.issym():
            link = member.linkname
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), link))
            if posixpath.isabs(link) or resolved == ".." or resolved.startswith("../"):
                raise Failed(f"{rel}: symlink {link!r} leaves the generation")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.symlink(link, target)
            have[rel] = ("symlink", link)
        else:
            raise Failed(f"{member.name}: member type {member.type!r} is not unpacked")
    tar.close()
    while source.read(4 * MiB):
        pass
    if top is None:
        raise Failed("empty tar")
    tar_sha = source.sha.hexdigest()
    fails = []
    if args.expect_tar_sha256 and tar_sha != args.expect_tar_sha256:
        fails.append(f"tar sha256 {tar_sha}, expected {args.expect_tar_sha256}")
    if "MANIFEST.json" not in have:
        fails.append("MANIFEST.json missing")
    else:
        have.pop("MANIFEST.json")
        manifest = read_json(top / "MANIFEST.json")
        fails.extend(compare_files(manifest, have))
    log(f"unpack: {len(have) + 1} members, {human(total)}, tar {source.bytes} bytes sha256 {tar_sha}")
    for fail in fails:
        log(f"FAIL: {fail}")
    log("unpack: PASS" if not fails else f"unpack: FAIL ({len(fails)})")
    return 0 if not fails else 1


def strtof_parser() -> Callable[[list[str]], bytes] | None:
    """libc strtof (correctly rounded on glibc, OpenBSD's gdtoa and the UCRT), via ctypes; None without one."""
    try:
        import ctypes  # noqa: PLC0415
        libc = ctypes.CDLL("ucrtbase") if sys.platform == "win32" else ctypes.CDLL(None)
        fn = libc.strtof
    except (OSError, AttributeError, ImportError):
        return None
    fn.restype = ctypes.c_float
    fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p]

    def parse(texts: list[str]) -> bytes:
        buf = (ctypes.c_float * len(texts))()
        for i, text in enumerate(texts):
            buf[i] = fn(text.encode("ascii"), None)
        out = array("f", bytes(buf))
        if sys.byteorder != "little":
            out.byteswap()
        return out.tobytes()

    return parse


def python_parser(texts: list[str]) -> bytes:
    values, _, _ = exact_f32(array("d", map(float, texts)), lambda: texts)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def run_verify(args: argparse.Namespace) -> int:
    gdir = Path(args.dir)
    manifest = read_json(gdir / "MANIFEST.json")
    fails: list[str] = []
    exporter = (manifest.get("exporter") or {}).get("sha256")
    mine = sha256_file(Path(__file__))[0]
    log(f"verify: this script sha256 {mine}: "
        + ("the same file that exported" if exporter == mine else f"NOT the exporter ({exporter})"))

    # 1. every file, and nothing else
    have: dict[str, tuple[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(gdir):
        base = Path(dirpath)
        for name in sorted(dirnames + filenames):
            path = base / name
            rel = path.relative_to(gdir).as_posix()
            if rel == "MANIFEST.json":
                continue
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                have[rel] = ("symlink", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                have[rel] = ("dir", None)
            else:
                have[rel] = ("file", sha256_file(path)[::-1])
    fails.extend(compare_files(manifest, have))
    log(f"verify: {len(have)} entries against MANIFEST.json: {'ok' if not fails else f'{len(fails)} problems'}")

    # 2. the vectors from the raw pages, by an independent parser; points.bin rebuilt
    export = read_json(gdir / "dense" / "EXPORT.json")
    dim, n = export["dim"], export["points_count"]
    choice = args.parser
    parser = strtof_parser() if choice in ("auto", "strtof") else None
    if parser is None:
        if choice == "strtof":
            raise Refused("no libc strtof here")
        parser, choice = python_parser, "python"
    else:
        choice = "strtof"
    db_path = gdir / "root" / "sqlite" / "rag.db"
    chunks = chunk_map(db_path)
    records = bytearray()
    row = mismatched = 0
    last_id = -1
    with open(gdir / "dense" / "vectors.f32", "rb") as vectors:
        if os.fstat(vectors.fileno()).st_size != n * dim * 4:
            fails.append(f"vectors.f32 is not {n} x {dim} x 4 bytes")
        for index, page in enumerate(export["scroll"]["pages"]):
            raw = gzip.decompress((gdir / page["path"]).read_bytes())
            if hashlib.sha256(raw).hexdigest() != page["raw_sha256"]:
                fails.append(f"{page['path']}: raw sha256 differs")
            doc = json.loads(raw, parse_float=str, parse_int=str)
            texts: list[str] = []
            for point in doc["result"]["points"]:
                if len(point["vector"]) != dim:
                    fails.append(f"{page['path']}: a vector of {len(point['vector'])} components")
                texts.extend(point["vector"])
                record, _, id_int = point_record(point["id"], point.get("payload"), chunks)
                if id_int <= last_id:
                    fails.append(f"{page['path']}: point {point['id']} out of order")
                last_id = id_int
                records += record
            got = parser(texts)
            want = vectors.read(len(got))
            if len(want) != len(got):
                fails.append(f"{page['path']}: vectors.f32 ends before this page's rows")
            elif got != want:
                a, b = array("I", got), array("I", want)
                bad = [i for i in range(len(a)) if a[i] != b[i]]
                mismatched += len(bad)
                fails.append(f"{page['path']}: {len(bad)} values differ from vectors.f32 (first at row "
                             f"{row + bad[0] // dim}, component {bad[0] % dim})")
            row += len(doc["result"]["points"])
            if index % 10 == 9:
                log(f"verify: dense page {index + 1}/{len(export['scroll']['pages'])}, {row}/{n} rows")
        if vectors.read(1):
            fails.append("vectors.f32 has bytes after the last page's rows")
    if row != n:
        fails.append(f"the pages hold {row} points, EXPORT.json says {n}")
    if bytes(records) != (gdir / "dense" / "points.bin").read_bytes():
        fails.append("points.bin differs from the records rebuilt from the pages and rag.db")
    log(f"verify: dense {row} rows re-parsed by {choice}: {mismatched} values differ; points.bin "
        f"{'rebuilt equal' if bytes(records) == (gdir / 'dense' / 'points.bin').read_bytes() else 'DIFFERS'}")

    # 3. SQLite: integrity, counts, fingerprint, sources
    counts = manifest["counts"]
    stats = rag_db_stats(db_path)
    if stats != counts["rag_db"]:
        fails.append(f"rag.db counts {stats}, the manifest says {counts['rag_db']}")
    if stats["corpus_fingerprint"] != manifest["corpus_fingerprint"]:
        fails.append(f"rag.db fingerprint {stats['corpus_fingerprint']}")
    for flag, key in (("docs", "n_docs"), ("chunks", "n_chunks")):
        want = getattr(args, f"expect_{flag}")
        if want is not None and stats[key] != want:
            fails.append(f"rag.db holds {stats[key]} {flag}, not {want}")
    if args.expect_fingerprint and stats["corpus_fingerprint"] != args.expect_fingerprint:
        fails.append(f"fingerprint {stats['corpus_fingerprint']}, not {args.expect_fingerprint}")
    if args.expect_points is not None and n != args.expect_points:
        fails.append(f"{n} points, not {args.expect_points}")
    checks = {"rag.db": quick_check(db_path)}
    kb_path = gdir / "root" / "kb" / "kb.sqlite"
    if kb_path.is_file():
        checks["kb.sqlite"] = quick_check(kb_path)
        kb = kb_stats(kb_path)
        if kb != counts["kb"]:
            fails.append(f"kb.sqlite counts {kb}, the manifest says {counts['kb']}")
    for name, result in checks.items():
        if result != "ok":
            fails.append(f"{name} quick_check: {result[:300]}")
    listed = {e["path"]: e for e in manifest["files"]}
    conn = sqlite_ro(db_path)
    try:
        sources = conn.execute("SELECT rel_path, sha256 FROM doc_sources").fetchall() \
            if has_table(conn, "doc_sources") else []
    finally:
        conn.close()
    bad_sources = [rel for rel, sha in sources if (listed.get(f"root/sources/{rel}") or {}).get("sha256") != sha]
    if bad_sources:
        fails.append(f"{len(bad_sources)} doc_sources rows whose file differs, e.g. {bad_sources[:3]}")
    manifest_path = gdir / "root" / "sources" / "manifest.json"
    if manifest_path.is_file():
        fp = corpus_fingerprint(e["sha256"] for e in read_json(manifest_path))
        if fp != manifest["corpus_fingerprint"]:
            fails.append(f"sources/manifest.json fingerprint {fp}")
    log(f"verify: rag.db {stats['n_docs']} documents, {stats['n_chunks']} chunks, fingerprint "
        f"{stats['corpus_fingerprint']}, quick_check {checks}; {len(sources)} doc_sources rows checked")

    # 4. FTS5 integrity-check on a scratch copy (it needs a write transaction)
    if args.fts_check:
        scratch = Path(tempfile.mkdtemp(prefix=".fts-check-", dir=gdir.parent))
        try:
            copy = scratch / "rag.db"
            shutil.copyfile(db_path, copy)
            conn = sqlite3.connect(copy)
            try:
                for table in bm25_tables(conn):
                    try:
                        conn.execute(INDEX_CHECK_SQL.format(table=table))
                        log(f"verify: FTS5 integrity-check {table}: ok")
                    except sqlite3.DatabaseError as exc:
                        fails.append(f"FTS5 integrity-check {table}: {exc}")
                conn.rollback()
            finally:
                conn.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    for fail in fails:
        log(f"FAIL: {fail}")
    log(f"verify: {'PASS' if not fails else f'FAIL ({len(fails)})'} ({manifest['generation']}, fingerprint "
        f"{manifest['corpus_fingerprint']}, {n} x {dim} vectors)")
    return 0 if not fails else 1


# ---------------------------------------------------------------- command line


def parser_for() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="export_generation.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for mode in ("check", "export"):
        p = sub.add_parser(mode)
        p.add_argument("--root", required=True)
        p.add_argument("--expect-generation", required=True)
        p.add_argument("--expect-fingerprint", required=True)
        p.add_argument("--expect-docs", type=int)
        p.add_argument("--expect-chunks", type=int)
        p.add_argument("--expect-points", type=int)
        p.add_argument("--recipient")
        p.add_argument("--out-dir")
        p.add_argument("--age", help="the age binary (default: age on PATH)")
        p.add_argument("--collection", help="the collection the root serves, stated (it must agree)")
        p.add_argument("--qdrant-url", help="default: RAG_QDRANT_URL, else the root's and the repo's dense.json")
        p.add_argument("--page-size", type=int, default=1024)
        p.add_argument("--pause-ms", type=int, default=100)
        p.add_argument("--gzip-level", type=int, default=1, choices=range(1, 10),
                       help="for the scroll pages (1: 0.25 s and 5.6 MB per 1,024-point page, 6: 1.2 s and 4.9 MB)")
        p.add_argument("--max-minutes", type=int, default=90, help="export fails (and cleans up) after this long")
        p.add_argument("--max-vector-mib", type=int, default=4096)
        p.add_argument("--include-logs", action="store_true")
        p.add_argument("--not-served-ok", action="store_true")
    p = sub.add_parser("clean")
    p.add_argument("--out-dir", required=True)
    p = sub.add_parser("unpack")
    p.add_argument("--into", required=True)
    p.add_argument("--input", default="-")
    p.add_argument("--expect-generation")
    p.add_argument("--expect-tar-sha256")
    p = sub.add_parser("verify")
    p.add_argument("--dir", required=True)
    p.add_argument("--parser", choices=("auto", "strtof", "python"), default="auto")
    p.add_argument("--no-fts-check", dest="fts_check", action="store_false")
    p.add_argument("--expect-fingerprint")
    p.add_argument("--expect-docs", type=int)
    p.add_argument("--expect-chunks", type=int)
    p.add_argument("--expect-points", type=int)
    return ap


def main(argv: list[str] | None = None, *, env: dict[str, str] | None = None,
         encrypt_argv: list[str] | None = None, stdin: BinaryIO | None = None) -> int:
    """encrypt_argv replaces `age -r RECIPIENT` (a command that reads the tar on stdin and writes to stdout): the
    tests' stand-in. Nothing else changes with it."""
    args = parser_for().parse_args(argv)
    env = dict(os.environ) if env is None else env
    log(f"export_generation {EXPORTER_VERSION} {args.mode} on {platform.node()}: Python "
        f"{platform.python_version()}, SQLite {sqlite3.sqlite_version}")
    try:
        if args.mode in ("check", "export"):
            if args.page_size < 1 or args.page_size > 4096:
                raise Refused("--page-size must be 1..4096")
            args._encrypt_argv = encrypt_argv
            if args.mode == "check":
                return run_check(args, env)
            return run_export(args, env, encrypt_argv)
        if args.mode == "clean":
            return run_clean(args, env)
        if args.mode == "unpack":
            if args.input == "-":
                return run_unpack(args, stdin or sys.stdin.buffer)
            with open(args.input, "rb") as f:
                return run_unpack(args, f)
        return run_verify(args)
    except Refused as exc:
        log(f"REFUSED: {exc}")
        return 2
    except (Failed, OSError, sqlite3.Error, ValueError, KeyError, tarfile.TarError) as exc:
        log(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


def _terminate(signum, frame):
    # systemctl stop (SIGTERM): unwind like any failure, so export deletes its partial file and stops age
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, _terminate)
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "export", "clean"):
        print(f"priority: {lower_priority()}", flush=True)
    sys.exit(main())
