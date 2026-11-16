"""deep32-engine node layers applied by serve_core.py before the app starts (launcher-side, no production code change,
like PYTHON-ON-OPENBSD 6.4's make_qdrant_client patch). Each is inert unless its switch is set, and each moves into
the C2 overlay or upstream later.

FIX-04, RAG_DB_IMMUTABLE=1 (HARDENING 5.8 item 1): the generation is read-only (slot mounted ro, files 0440,
  unveiled "r"), but production's rag_core.db.connect() opens rag.db read-write and sets journal_mode=WAL, and
  kb.py and status.py open `?mode=ro` URIs. rag.db is a WAL database (header bytes 18-19 = 2 in the p9er export),
  so even a read-only open needs to create -shm/-wal beside it, which the slot refuses. The layer opens every one
  of them as `file:<path>?mode=ro&immutable=1` and skips the two write pragmas (journal_mode, synchronous).
  immutable only turns off locking and change detection on a file that cannot change; the read pragmas
  (foreign_keys, temp_store=MEMORY, mmap_size, busy_timeout) stay, so results are identical by construction.
  tests/layer_identity.py compares the served answers with and without it.

FIX-05, RAG_TRACE=off: traces hold query text; the launcher refuses to start unless the login class set it.

GPU over UNIX sockets (HARDENING 5.8 item 5, the U3 shape): when D32_GPU_EMBED_UDS / D32_GPU_RERANK_UDS are set,
  the embedder's and reranker's httpx transports connect to the GPU_EXT forwarder's sockets instead of TCP, so
  deep32-engine needs no `inet` for them. Only the transport changes: URL path, body, headers and timeouts are
  production's (RAG_EMBEDDING_URL / RAG_RERANK_URL must then be plain http://; their host only fills Host:).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import types
from pathlib import Path

log = logging.getLogger("d32svc")

_state: dict[str, object] = {}


def _immutable_uri(path: Path) -> str:
    # abspath, not resolve(): realpath lstat()s every ancestor, and under unveil the ancestors of the generation
    # (/var, /d32/active, ...) answer ENOENT. The file opened is the same either way.
    return Path(os.path.abspath(path)).as_uri() + "?mode=ro&immutable=1"


def immutable_connect(db_path) -> sqlite3.Connection:
    """rag_core.db.connect for a read-only generation: production's read pragmas, no write pragmas."""
    path = Path(db_path)
    if not path.is_file():  # production would create an empty database here; a read-only node never does
        raise sqlite3.OperationalError("unable to open database file")
    conn = sqlite3.connect(_immutable_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def apply_immutable_db() -> list[str]:
    """Patch rag_core.db.connect (and every rag_core module that imported it by name), kb._read_only_uri and
    status._read_only. Returns the module names patched. Safe to call again after late imports."""
    import rag_core.db as db
    import rag_core.kb as kb
    import rag_core.status as status

    orig = _state.setdefault("connect", db.connect)
    patched = []
    for name, mod in list(sys.modules.items()):
        if (name == "rag_core" or name.startswith("rag_core.")) and getattr(mod, "connect", None) is orig:
            mod.connect = immutable_connect
            patched.append(name)
    if "kb_uri" not in _state:
        _state["kb_uri"] = kb._read_only_uri
        kb._read_only_uri = _immutable_uri  # production: path.resolve().as_uri() + "?mode=ro"
        patched.append("rag_core.kb._read_only_uri")
    if "status_ro" not in _state:
        _state["status_ro"] = status._read_only
        timeout = status.PROBE_TIMEOUT_SECONDS

        def read_only(path: Path) -> sqlite3.Connection:
            if not path.is_file():
                raise FileNotFoundError(f"{path.name} does not exist")
            return sqlite3.connect(_immutable_uri(path), uri=True, timeout=timeout)

        status._read_only = read_only
        patched.append("rag_core.status._read_only")
    return patched


def stale_connect_holders() -> list[str]:
    """rag_core modules imported after apply_immutable_db() that still hold the original connect."""
    orig = _state.get("connect")
    return [n for n, m in list(sys.modules.items())
            if orig is not None and (n == "rag_core" or n.startswith("rag_core.")) and getattr(m, "connect", None) is orig]


def apply_gpu_uds(embed_uds: str | None, rerank_uds: str | None) -> list[str]:
    """Give rag_core.dense (the query embedder) and rag_core.rerank (the reranker) an httpx whose HTTPTransport
    connects to a UNIX socket. Everything else in those modules sees the real httpx."""
    import httpx

    done = []
    for modname, uds in (("rag_core.dense", embed_uds), ("rag_core.rerank", rerank_uds)):
        if not uds:
            continue
        mod = sys.modules.get(modname) or __import__(modname, fromlist=["_"])

        class UdsTransport(httpx.HTTPTransport):
            _uds = uds

            def __init__(self, *args, **kwargs):
                kwargs.setdefault("uds", self._uds)
                super().__init__(*args, **kwargs)

        shim = types.ModuleType("httpx")
        shim.__getattr__ = lambda name, _real=httpx: getattr(_real, name)  # type: ignore[attr-defined]
        shim.HTTPTransport = UdsTransport
        mod.httpx = shim
        done.append(f"{modname} -> {uds}")
    return done
