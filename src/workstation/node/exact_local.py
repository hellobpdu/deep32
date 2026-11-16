"""exact_local: exact dense search over a Deep32 generation's exported vectors, in place of Qdrant on the node.

PYTHON-ON-OPENBSD 4.2 and 6 (CORE-PORT 6.1, 6.3, 7). deep32-engine's serve path makes exactly one Qdrant call shape,

    client.query_points(collection_name, query, query_filter, limit, with_payload=["chunk_id"], with_vectors=False,
                        **search_params)

plus the /v2/status probe (limit=1, with_payload=False). ExactClient answers that shape from the generation's
`dense/` directory and nothing else:

    dense/vectors.f32   N x 1024 float32, little-endian, row = Qdrant scroll order (point id ascending)
    dense/points.bin    96-byte records: point UUID (16 B), chunk_id and doc_id as the 32 bytes their 64 hex digits
                        after chk_ / doc_ spell (32 B each), flags (1 B), zero (15 B)
    dense/EXPORT.json   format "deep32-dense/1", counts, sha256 of both files, the collection name

Rules (6.1):
- query: np.asarray(query, float64).astype(float32) (TEI JSON -> Python float -> qdrant_client JSON -> Qdrant f32);
- query normalisation: Qdrant's cosine_preprocess: unchanged when len2 < f32::EPSILON or |len2 - 1| <= 1e-6, else
  x / sqrt(len2) in f32, len2 summed in the scan's own order;
- scores: libd32vec (d32vec.c, Qdrant's dot_similarity_sse order) on `threads` row ranges, or the numpy emulation
  of the same order (kernel="numpy": the harness's independent reference, G-dense2);
- filter: `must` / `must_not` of FieldCondition(key="doc_id", match=MatchAny | MatchValue): a row mask;
- top-k: score descending, then row number ascending (deterministic inside groups of equal scores: class C2);
- score handed back: float(str(np.float32(s))), i.e. the f32's shortest decimal parsed as a Python float, which is
  what qdrant_client makes of Qdrant's serde output (a plain float(s) differs in nearly every score);
- every point is returned; deep32-engine drops points whose chunk is missing or not dense_indexed itself, after the cut.
`search_params` are accepted and ignored: the module is always exact (production's retrieval.dense.exact=true arm;
against production's HNSW search a difference is class C1). Anything outside this shape raises NotImplementedError,
so an unexpected call is loud, never approximately right.

The vectors are mapped read-only (PROT_READ, MAP_SHARED: file-backed, shared through the page cache, outside the
process's datasize) and touched once at start.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import numpy as np
from qdrant_client.http import models as rest

LIB_DEFAULT = "/usr/local/d32/lib/libd32vec.so"
ABI = 1
FORMAT = "deep32-dense/1"
RECORD = 96
F_CHUNK, F_DOC = 1, 2
PAYLOAD_KEYS = ("chunk_id", "doc_id")
F32_EPS = np.float32(np.finfo(np.float32).eps)
NORM_TOL = np.float32(1.0e-6)
ONE = np.float32(1.0)

__all__ = ["ExactClient", "CKernel", "emulate_scores", "qdrant_cosine_preprocess", "qdrant_score", "top_rows",
           "client_for", "install"]


# ---------------------------------------------------------------- kernels

class CKernel:
    """libd32vec through ctypes (a CDLL call releases the GIL, so row ranges run on threads in parallel)."""

    name = "c"

    def __init__(self, path: str = LIB_DEFAULT) -> None:
        lib = ctypes.CDLL(path)
        lib.d32vec_abi.restype = ctypes.c_int
        if lib.d32vec_abi() != ABI:
            raise RuntimeError(f"{path}: ABI {lib.d32vec_abi()}, exact_local needs {ABI}")
        lib.d32vec_sse.restype = ctypes.c_int
        vp, sz = ctypes.c_void_p, ctypes.c_size_t
        for fn in (lib.d32_dot, lib.d32_dot_lanes):
            fn.argtypes = [vp, vp, sz]
            fn.restype = ctypes.c_float
        for fn in (lib.d32_scan, lib.d32_scan_lanes):
            fn.argtypes = [vp, sz, sz, sz, vp, vp]
            fn.restype = None
        self.lib = lib
        self.path = path
        self.sse = bool(lib.d32vec_sse())

    def dot(self, x: np.ndarray, y: np.ndarray) -> np.float32:
        x = np.ascontiguousarray(x, np.float32)
        y = np.ascontiguousarray(y, np.float32)
        return np.float32(self.lib.d32_dot(x.ctypes.data, y.ctypes.data, x.shape[0]))

    def scan(self, M: np.ndarray, q: np.ndarray, out: np.ndarray, r0: int, r1: int, lanes: bool = False) -> None:
        fn = self.lib.d32_scan_lanes if lanes else self.lib.d32_scan
        fn(M.ctypes.data, M.shape[1], r0, r1, q.ctypes.data, out.ctypes.data)


def emulate_scores(M: np.ndarray, q: np.ndarray, chunk: int = 2048, out: np.ndarray | None = None) -> np.ndarray:
    """Qdrant's dot_similarity_sse order in numpy, written independently of d32vec.c (the harness's reference).

    For rows x and query y of dim d (m = d - d % 16): p[s, k, l] = x[16s+4k+l] * y[16s+4k+l]; acc[k, l] = p + acc
    over s = 0..m/16-1 (multiply, then add: numpy never fuses them); h[k] = (acc[k,0] + acc[k,2]) +
    (acc[k,1] + acc[k,3]); score = ((h0 + h1) + h2) + h3; then the tail r = r + x[i] * y[i] for i >= m."""
    n, dim = M.shape
    m = dim - dim % 16
    steps = m // 16
    q = np.ascontiguousarray(q, np.float32)
    if out is None:
        out = np.empty(n, np.float32)
    for r0 in range(0, n, chunk):
        blk = np.asarray(M[r0:r0 + chunk], np.float32)
        rows = blk.shape[0]
        p = np.multiply(blk[:, :m], q[:m]).reshape(rows, steps, 4, 4)
        acc = np.zeros((rows, 4, 4), np.float32)
        for s in range(steps):
            np.add(p[:, s], acc, out=acc)
        h = (acc[:, :, 0] + acc[:, :, 2]) + (acc[:, :, 1] + acc[:, :, 3])
        r = ((h[:, 0] + h[:, 1]) + h[:, 2]) + h[:, 3]
        for i in range(m, dim):
            r = r + blk[:, i] * q[i]
        out[r0:r0 + rows] = r
    return out


def numpy_dot(x: np.ndarray, y: np.ndarray) -> np.float32:
    return emulate_scores(np.asarray(x, np.float32)[None, :], y)[0]


def qdrant_cosine_preprocess(q: np.ndarray, dot) -> np.ndarray:
    """Qdrant's cosine_preprocess on an f32 query: unchanged when len2 < f32::EPSILON or |len2 - 1| <= 1e-6 (all in
    f32), else x / sqrt(len2) element by element in f32. `dot` sums len2 in the scan's order."""
    len2 = np.float32(dot(q, q))
    if len2 < F32_EPS or abs(len2 - ONE) <= NORM_TOL:
        return q
    length = np.sqrt(len2, dtype=np.float32)
    return np.divide(q, length, dtype=np.float32)


def qdrant_score(s: np.float32) -> float:
    """What qdrant_client hands deep32-engine for an f32 score: Qdrant writes the f32's shortest decimal (serde/ryu),
    qdrant_client parses it as a Python float. numpy's str() of a float32 is that shortest decimal (Dragon4)."""
    return float(str(np.float32(s)))


def top_rows(scores: np.ndarray, mask: np.ndarray | None, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The `limit` best rows by score descending, then row ascending; rows outside `mask` never appear."""
    if mask is None:
        cand, s = None, scores
    else:
        cand = np.flatnonzero(mask)
        s = scores[cand]
    n = s.shape[0]
    if limit <= 0 or n == 0:
        return np.empty(0, np.int64), np.empty(0, np.float32)
    if n > limit:
        kth = np.partition(s, n - limit)[n - limit]  # the limit-th largest score
        sel = np.flatnonzero(s >= kth)               # every row tied with it too, so the row rule decides
    else:
        sel = np.arange(n)
    rows = sel if cand is None else cand[sel]
    vals = s[sel]
    order = np.lexsort((rows, -vals))[:limit]
    return rows[order].astype(np.int64), vals[order]


# ---------------------------------------------------------------- the generation's dense directory

class DenseExport:
    """dense/: EXPORT.json, vectors.f32 (mapped read-only) and points.bin (decoded once)."""

    def __init__(self, dense_dir: str | os.PathLike, verify: bool = True, prefault: bool = True) -> None:
        d = Path(dense_dir)
        meta = json.loads((d / "EXPORT.json").read_text(encoding="utf-8"))
        if meta.get("format") != FORMAT or meta.get("dtype") != "float32" or meta.get("byte_order") != "little":
            raise ValueError(f"{d}/EXPORT.json: not a {FORMAT} float32 little-endian export")
        n, dim = int(meta["points_count"]), int(meta["dim"])
        vec_path = d / Path(meta["vectors"]["path"]).name
        pts_path = d / Path(meta["points"]["path"]).name
        if vec_path.stat().st_size != n * dim * 4 or pts_path.stat().st_size != n * RECORD:
            raise ValueError(f"{d}: file sizes do not match {n} x {dim}")
        if verify:
            for path, want in ((vec_path, meta["vectors"]["sha256"]), (pts_path, meta["points"]["sha256"])):
                got = _sha256(path)
                if got != want:
                    raise ValueError(f"{path}: sha256 {got} != EXPORT.json {want}")
        self.meta = meta
        self.dir = d
        self.n, self.dim = n, dim
        self.collection = meta["collection"]
        self.generation = meta.get("generation")
        self.vectors = np.memmap(vec_path, dtype="<f4", mode="r", shape=(n, dim))
        if prefault:  # one read per 4 KiB page, so the first search does not page the matrix in
            int(self.vectors.reshape(-1)[:: 1024].view(np.uint32).sum(dtype=np.uint64))
        self._decode_points(pts_path.read_bytes())

    def _decode_points(self, raw: bytes) -> None:
        n = self.n
        ids: list[str] = []
        chunk: list[str | None] = []
        doc: list[str | None] = []
        flags = np.empty(n, np.uint8)
        prev = b""
        zero32, zero15 = bytes(32), bytes(15)
        for r in range(n):
            rec = raw[r * RECORD:(r + 1) * RECORD]
            pid, ch, dc, fl, pad = rec[0:16], rec[16:48], rec[48:80], rec[80], rec[81:96]
            if pad != zero15 or fl & ~63:
                raise ValueError(f"points.bin row {r}: bad padding or flags {fl}")
            if pid <= prev:
                raise ValueError(f"points.bin row {r}: point ids are not ascending (scroll order)")
            prev = pid
            ids.append(str(uuid.UUID(bytes=pid)))
            chunk.append(_hex_field(ch, fl & F_CHUNK, "chk_", r, zero32))
            doc.append(_hex_field(dc, fl & F_DOC, "doc_", r, zero32))
            flags[r] = fl
        self.point_ids = ids
        self.chunk_ids = chunk
        self.doc_ids = doc
        self.flags = flags
        index: dict[str, int] = {}
        codes = np.full(n, -1, np.int32)
        for r, d in enumerate(doc):
            if d is not None:
                codes[r] = index.setdefault(d, len(index))
        self.doc_index = index
        self.doc_code = codes


def _hex_field(b: bytes, present: int, prefix: str, row: int, zero: bytes) -> str | None:
    """`prefix` + the 64 lowercase hex digits of the field's 32 bytes; None when the flag says the payload has none."""
    if not present:
        if b != zero:
            raise ValueError(f"points.bin row {row}: {prefix} field set without its flag")
        return None
    return prefix + b.hex()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- the client

class ExactClient:
    """The subset of QdrantClient that deep32-engine's serve path calls (query_points), answered exactly."""

    def __init__(self, dense_dir: str | os.PathLike, lib: str = LIB_DEFAULT, threads: int = 4,
                 kernel: str = "c", verify: bool = True, prefault: bool = True) -> None:
        self.export = DenseExport(dense_dir, verify=verify, prefault=prefault)
        self.n, self.dim = self.export.n, self.export.dim
        self.collection = self.export.collection
        self.threads = max(1, int(threads))
        if kernel == "c":
            self.kernel = CKernel(lib)
            self._dot = self.kernel.dot
        elif kernel == "numpy":
            self.kernel = None
            self._dot = numpy_dot
        else:
            raise ValueError(f"kernel must be 'c' or 'numpy', not {kernel!r}")
        self.kernel_name = kernel
        step = -(-self.n // self.threads)
        self._ranges = [(a, min(a + step, self.n)) for a in range(0, self.n, step)]
        self._pool: ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()
        self._mask_cache = lru_cache(maxsize=64)(self._match_mask)

    # -- scoring

    def prepare_query(self, query: Any) -> np.ndarray:
        if isinstance(query, (str, bytes, int, uuid.UUID)) or not hasattr(query, "__len__"):
            raise NotImplementedError(f"exact_local: query must be a dense vector, not {type(query).__name__}")
        q = np.asarray(query, np.float64)
        if q.ndim != 1 or q.shape[0] != self.dim:
            raise ValueError(f"Wrong input: Vector dimension error: expected dim: {self.dim}, got {q.shape}")
        q32 = np.ascontiguousarray(q.astype(np.float32))
        return np.ascontiguousarray(qdrant_cosine_preprocess(q32, self._dot), np.float32)

    def scores(self, q: np.ndarray) -> np.ndarray:
        """All N scores for a prepared query (prepare_query)."""
        M = self.export.vectors
        if self.kernel is None:
            return emulate_scores(M, q)
        out = np.empty(self.n, np.float32)
        if self.threads == 1:
            self.kernel.scan(M, q, out, 0, self.n)
            return out
        pool = self._executor()
        futures = [pool.submit(self.kernel.scan, M, q, out, a, b) for a, b in self._ranges]
        for f in futures:
            f.result()
        return out

    def _executor(self) -> ThreadPoolExecutor:
        # made on first use, so its threads exist only after the launcher set the thread stack size
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(self.threads, thread_name_prefix="d32vec")
            return self._pool

    # -- filters

    def _match_mask(self, ids: frozenset) -> np.ndarray:
        index = self.export.doc_index
        lut = np.zeros(len(index) + 1, bool)  # the extra last slot answers rows without a doc_id (code -1)
        hit = [index[d] for d in ids if d in index]
        lut[hit] = True
        mask = lut[self.export.doc_code]
        mask.setflags(write=False)
        return mask

    def mask(self, query_filter: Any) -> np.ndarray | None:
        if query_filter is None:
            return None
        if not isinstance(query_filter, rest.Filter):
            raise NotImplementedError(f"exact_local: filter type {type(query_filter).__name__}")
        if query_filter.should or query_filter.min_should:
            raise NotImplementedError("exact_local: only must / must_not filters")
        mask = np.ones(self.n, bool)
        for cond in _conditions(query_filter.must):
            mask &= self._mask_cache(_doc_set(cond))
        for cond in _conditions(query_filter.must_not):
            mask &= ~self._mask_cache(_doc_set(cond))
        return mask

    # -- Qdrant surface

    def query_points(self, collection_name: str, query: Any = None, *, using: Any = None, prefetch: Any = None,
                     query_filter: Any = None, search_params: Any = None, limit: int = 10, offset: Any = None,
                     with_payload: Any = True, with_vectors: Any = False, score_threshold: Any = None,
                     lookup_from: Any = None, consistency: Any = None, shard_key_selector: Any = None,
                     timeout: Any = None, **kwargs: Any) -> rest.QueryResponse:
        # consistency and timeout change nothing in a local exact scan; everything else that could change the
        # answer must be absent
        unsupported = [k for k, v in (("using", using), ("prefetch", prefetch), ("score_threshold", score_threshold),
                                      ("lookup_from", lookup_from), ("shard_key_selector", shard_key_selector),
                                      *kwargs.items()) if v is not None]
        if offset not in (None, 0):
            unsupported.append("offset")
        if unsupported:
            raise NotImplementedError(f"exact_local: unsupported query_points arguments {sorted(unsupported)}")
        if with_vectors not in (False, None):
            raise NotImplementedError("exact_local: with_vectors is not served")
        if collection_name != self.collection:
            raise ValueError(f"Not found: Collection `{collection_name}` doesn't exist!")
        keys = _payload_keys(with_payload)
        mask = self.mask(query_filter)
        q = self.prepare_query(query)
        rows, vals = top_rows(self.scores(q), mask, int(limit))
        return rest.QueryResponse(points=[
            rest.ScoredPoint(id=self.export.point_ids[r], version=0, score=qdrant_score(v),
                             payload=self._payload(int(r), keys))
            for r, v in zip(rows, vals)
        ])

    def _payload(self, r: int, keys: tuple[str, ...] | None) -> dict[str, Any] | None:
        if keys is None:
            return None
        values = {"chunk_id": self.export.chunk_ids[r], "doc_id": self.export.doc_ids[r]}
        return {k: values[k] for k in keys if values[k] is not None}

    def close(self, **_: Any) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.shutdown(wait=True)
                self._pool = None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(f"exact_local.ExactClient has no {name!r}: only query_points is served on the node "
                             "(collection management and upserts are build-time operations)")


def _conditions(x: Any) -> list[Any]:
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


def _doc_set(cond: Any) -> frozenset:
    if not isinstance(cond, rest.FieldCondition) or cond.key != "doc_id":
        raise NotImplementedError(f"exact_local: only doc_id field conditions, not {cond!r}")
    extra = set(cond.model_dump(exclude_none=True)) - {"key", "match"}
    if extra:
        raise NotImplementedError(f"exact_local: doc_id condition with {sorted(extra)}")
    m = cond.match
    if isinstance(m, rest.MatchAny):
        return frozenset(m.any)
    if isinstance(m, rest.MatchValue):
        return frozenset([m.value])
    raise NotImplementedError(f"exact_local: doc_id match {type(m).__name__}")


def _payload_keys(with_payload: Any) -> tuple[str, ...] | None:
    if with_payload is False or with_payload is None:
        return None
    if isinstance(with_payload, (list, tuple)) and all(isinstance(k, str) for k in with_payload):
        unknown = [k for k in with_payload if k not in PAYLOAD_KEYS]
        if unknown:
            raise NotImplementedError(f"exact_local: payload keys {unknown} are not in the export")
        return tuple(dict.fromkeys(with_payload))
    raise NotImplementedError("exact_local: with_payload must be False or a list of keys (the export carries "
                              "chunk_id and doc_id only, not the whole payload)")


# ---------------------------------------------------------------- plugging into deep32-engine (PYTHON-ON-OPENBSD 6.4)

_clients: dict[tuple, ExactClient] = {}
_clients_lock = threading.Lock()


def dense_dir_from_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost") or not parts.path:
        raise ValueError(f"exact_local: not a file: URL for a dense directory: {url!r}")
    return unquote(parts.path)


def client_for(url: str) -> ExactClient:
    """One shared ExactClient per dense directory (the mapping, the decoded points and the thread pool are made
    once). Settings from the environment: D32_EXACT_LIB, D32_EXACT_THREADS (4), D32_EXACT_KERNEL (c | numpy),
    D32_EXACT_VERIFY (1: sha256 of both files against EXPORT.json at start)."""
    path = os.path.realpath(dense_dir_from_url(url))
    key = (path, os.environ.get("D32_EXACT_LIB", LIB_DEFAULT), int(os.environ.get("D32_EXACT_THREADS", "4")),
           os.environ.get("D32_EXACT_KERNEL", "c"), os.environ.get("D32_EXACT_VERIFY", "1") != "0")
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = _clients[key] = ExactClient(path, lib=key[1], threads=key[2], kernel=key[3], verify=key[4])
        return client


def install(*modules: Any) -> None:
    """Replace make_qdrant_client in each module given (rag_core.dense, and rag_core.retrievers.dense, which binds
    the name at import) with one that returns the shared ExactClient for a `file:` RAG_QDRANT_URL and production's
    QdrantClient for anything else. Call before the app's lifespan builds the retrievers."""
    if not modules:
        raise TypeError("install() needs the modules to patch")
    original = modules[0].make_qdrant_client
    original = getattr(original, "__wrapped__", original)

    def make_qdrant_client(config: Any) -> Any:
        if str(config.qdrant_url).startswith("file:"):
            return client_for(config.qdrant_url)
        return original(config)

    make_qdrant_client.__wrapped__ = original  # type: ignore[attr-defined]
    make_qdrant_client.__doc__ = "exact_local.install: ExactClient for file: URLs, else " + (original.__doc__ or "")
    for module in modules:
        module.make_qdrant_client = make_qdrant_client
