"""deep32-engine launcher for Deep32 First light (PYTHON-ON-OPENBSD 7.2, HARDENING 5.8, the final design C6).

rc.d/d32core runs, as root:
  d32-sockexec -u _d32core -e /etc/d32/core.env -l core=unix:/var/run/d32/core.sock:root:_d32edge:0660 \
      -- /usr/local/d32/venv/deep32-engine/bin/python3.12 -I -B -X utf8 /usr/local/d32/node/shims/serve_core.py
so this file starts as _d32core with the socket on fd 3 and the environment file on fd 4. Order:
  1. boot: 8 MiB thread stacks, environment rebuilt (login class: RAG_TRACE=off, RAG_DB_IMMUTABLE=1, SQLITE_TMPDIR;
     the environment file: RAG_ROOT, RAG_CORE_API_TOKEN, RAG_QDRANT_URL, RAG_EMBEDDING_URL, RAG_RERANK_URL, ...)
  2. code tree first on sys.path, the torch/pylate stand-ins after the venv (deep32-engine only), cwd = code tree
  3. import rag_core.api and the serve path; node layers (immutable database, GPU over UNIX sockets)
  4. uvicorn with production's defaults (loop auto = uvloop, http auto = httptools, lifespan on) on the inherited
     socket; after the lifespan start-up: unveil, unveil_lock, pledge
Environment-file options (D32_*, never passed to the app):
  D32_CORE_CODE     code tree (default /usr/local/d32/deep32-engine/current; the realpath is used)
  D32_STANDINS      torch/pylate stand-ins (default /usr/local/d32/node/standins, the pyrag lane's)
  D32_GPU_EMBED_UDS, D32_GPU_RERANK_UDS   the GPU_EXT forwarder's sockets (default /var/run/d32/gpu-*.sock;
                    "-" leaves the transport as production's TCP)
"""
from __future__ import annotations

import logging
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import d32svc  # noqa: E402

CLASS_KEYS = ("RAG_TRACE", "RAG_DB_IMMUTABLE", "SQLITE_TMPDIR")
REQUIRED = ("RAG_ROOT", "RAG_CORE_API_TOKEN")
# the serve path rag_core.api loads; imported before confinement so no import happens later (FIX-06)
PRE_IMPORT = ("rag_core.api", "rag_core.api_v2", "rag_core.orchestrator", "rag_core.reader", "rag_core.structure",
              "rag_core.status", "rag_core.kb", "rag_core.db", "rag_core.dense", "rag_core.rerank",
              "rag_core.retrievers.lexical", "rag_core.retrievers.dense", "rag_core.pipeline", "rag_core.fusion",
              "rag_core.query_plan", "rag_core.sections", "rag_core.text_adapter", "rag_core.traces",
              "rag_core.breaker", "rag_core.identity", "rag_core.canonical", "rag_core.errors",
              "uvicorn.lifespan.on", "anyio._backends._asyncio", "encodings.idna", "json.decoder")
# (uvicorn's loop and protocol classes, uvloop and httptools as in production, come from Config.load(), which
# runs before the start-up and so before pledge)

log = logging.getLogger("d32svc")


def _is_file_url(url: str) -> bool:
    return url.startswith("file:")


def main() -> None:
    svc = d32svc.boot("deep32-engine", class_keys=CLASS_KEYS, required=REQUIRED)
    if os.environ["RAG_TRACE"].strip().lower() not in ("off", "false", "no", "none"):
        raise SystemExit("deep32-engine: RAG_TRACE must be off on the node (FIX-05: traces hold query text)")
    if "core" not in svc.listeners:
        raise SystemExit("deep32-engine: no 'core' listener inherited (start it through rc.d/d32core)")

    # the generation by its real path: every path deep32-engine derives from RAG_ROOT then lies under the unveiled tree,
    # with no symlink in between (a symlinked slot such as /d32/active would otherwise be looked up through /d32)
    root = d32svc.realpath_dir(os.environ["RAG_ROOT"])
    if root != os.environ["RAG_ROOT"]:
        log.warning("RAG_ROOT %s is %s", os.environ["RAG_ROOT"], root)
        os.environ["RAG_ROOT"] = root
    code = d32svc.realpath_dir(svc.opts.get("D32_CORE_CODE", "/usr/local/d32/deep32-engine/current"))
    standins = d32svc.realpath_dir(svc.opts.get("D32_STANDINS", "/usr/local/d32/node/standins"))
    src = os.path.join(code, "src")
    sys.path.insert(0, src)
    sys.path.append(standins)  # after the venv: only what is missing there (torch, pylate)
    os.chdir(code)             # production: WorkingDirectory=<the engine checkout>

    import importlib

    for name in PRE_IMPORT:
        importlib.import_module(name)
    import node_layers

    layers = []
    if os.environ["RAG_DB_IMMUTABLE"].strip() == "1":
        layers += node_layers.apply_immutable_db()
    embed_uds = svc.opts.get("D32_GPU_EMBED_UDS", "/var/run/d32/gpu-embed.sock")
    rerank_uds = svc.opts.get("D32_GPU_RERANK_UDS", "/var/run/d32/gpu-rerank.sock")
    embed_uds = None if embed_uds in ("", "-") else embed_uds
    rerank_uds = None if rerank_uds in ("", "-") else rerank_uds
    for key, uds in (("RAG_EMBEDDING_URL", embed_uds), ("RAG_RERANK_URL", rerank_uds)):
        if uds and not os.environ.get(key, "").startswith("http://"):
            raise SystemExit(f"deep32-engine: {key} must be an http:// URL when the GPU forwarder socket is used")
    layers += node_layers.apply_gpu_uds(embed_uds, rerank_uds)
    for line in layers:
        log.warning("node layer: %s", line)

    # inet only while something still speaks TCP: Qdrant until exact_local (file: URL, PYRAG C3) lands, or a GPU
    # endpoint left on TCP. deep32-engine never accepts on inet; pf (BASE) blocks _d32core's TCP besides.
    inet_for = []
    if not _is_file_url(os.environ.get("RAG_QDRANT_URL", "")):
        inet_for.append("qdrant client (until exact_local)")
    if not embed_uds:
        inet_for.append("embedder over TCP")
    if not rerank_uds:
        inet_for.append("reranker over TCP")
    promises = "stdio rpath wpath cpath unix flock prot_exec" + (" inet" if inet_for else "")

    unveils = d32svc.base_unveils() + [
        (code, "r"), (standins, "r"),
        (root, "r"),
        (d32svc.realpath_dir(os.environ["SQLITE_TMPDIR"]), "rwc"),  # SQLite temp files only (FIX-06)
        ("/etc/ssl/cert.pem", "r"),
    ]
    for key in ("RAG_RETRIEVAL_CONFIG", "RAG_SERVING_CONFIG", "RAG_LATE_INTERACTION_CONFIG", "RAG_CHUNKING_CONFIG"):
        if os.environ.get(key):
            unveils.append((d32svc.realpath_dir(os.environ[key]), "r"))
    for uds in (embed_uds, rerank_uds):
        if uds:
            unveils.append((uds, "rw"))  # connect(2) to a UNIX socket needs write permission on it

    def confine() -> None:
        late = node_layers.stale_connect_holders()
        if late and os.environ["RAG_DB_IMMUTABLE"].strip() == "1":
            log.warning("node layer: late imports held the read-write connect, patched: %s", ", ".join(late))
            node_layers.apply_immutable_db()
        if inet_for:
            log.warning("deep32-engine keeps inet for: %s", "; ".join(inet_for))
        d32svc.confine(svc, unveils, promises)

    import uvicorn

    # production: `uvicorn rag_core.api:app --host H --port P --workers 1`; Config's defaults are the CLI's
    config = uvicorn.Config("rag_core.api:app", workers=1)
    server = d32svc.make_server(config, svc, confine)
    server.run(sockets=[svc.listeners["core"]])


if __name__ == "__main__":
    main()
