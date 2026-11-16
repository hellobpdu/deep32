"""deep32-engine launcher of the First light PREVIEW image (ship lane).

The shims lane's serve_core.py, unchanged, plus the nodegen lane's exact_local installed at the point where the
shims launcher applies its node layers: after rag_core's serve path is imported and before the app's lifespan builds
the retrievers. With RAG_QDRANT_URL=file:<generation>/dense, make_qdrant_client then returns exact_local's
ExactClient (libd32vec, the generation's own exported vectors, Qdrant's order of operations), so dense search needs
no Qdrant and deep32-engine keeps no "inet" promise. Any other URL still gets production's QdrantClient.

rc.d/d32core (the ship copy) runs, as root:
  d32-sockexec -u _d32core -e /etc/d32/core.env -l core=unix:/var/run/d32/core.sock:root:_d32edge:0660 \
      -- /usr/local/d32/venv/deep32-engine/bin/python3.12 -I -B -X utf8 /usr/local/d32/node/ship/serve_core_ship.py

Update 1 (U32): no access log. uvicorn's access log put every GET's query string (the search terms) into
/var/log/daemon through rc.d's logger, and the lamp's poll every minute rotated the rest out in about two days.
serve_core builds uvicorn.Config itself, so the Config it gets is one whose access_log defaults to False; errors and
start-up lines are still logged.

Update 15: a longer limit for the live probes of GET /v2/status. rag_core.status gives the embedder and the reranker
2 s each (StatusService's probe_timeout default; no setting reads it). Over a relayed tailnet one query embedding
takes 2.0 to 2.8 s (measured 2026-11-13), so the status and the lamp said "degraded: dense" while searches, which
have their own limit (RAG_EMBED_QUERY_TIMEOUT), used the dense leg. The node's StatusService now defaults to
STATUS_PROBE_TIMEOUT. It is a ceiling, not a delay: a probe returns when the hub answers, so a healthy status takes
as long as before (about 0.1 s on the home LAN); only a hub that does not answer at all makes a status wait longer
(6.5 s, was 2.5 s). rag_core.status.PROBE_TIMEOUT_SECONDS, the SQLite probes' busy limit, is not touched. deep32-gateway
asks for a status too, for its opt-in note: serve_edge.py raises that limit to match (OPT_IN_STATUS_TIMEOUT).
"""
import importlib.util
import os
import sys

SHIMS = "/usr/local/d32/node/shims"
NODE = "/usr/local/d32/node"
sys.path.insert(0, SHIMS)

import node_layers  # noqa: E402  (the shims lane's module; serve_core imports this same object)

_apply_gpu_uds = node_layers.apply_gpu_uds

# Update 15: the limit of one live status probe (embedder, reranker), in seconds; rag_core.status's default is 2.0.
# serve_edge.py's OPT_IN_STATUS_TIMEOUT is this + rag_core.status.PROBE_GRACE_SECONDS (0.5) + 0.5: change both together.
STATUS_PROBE_TIMEOUT = 6.0


def _longer_status_probe():
    """Make rag_core.api build its StatusService with probe_timeout=STATUS_PROBE_TIMEOUT unless a caller names one.
    Returns the line for the start-up log. A deep32-engine whose StatusService has no such parameter, or a default that
    is not shorter, is left as it is, and the line says so."""
    import inspect

    import rag_core.api

    base = getattr(rag_core.api, "StatusService", None)
    known = inspect.signature(base.__init__).parameters if inspect.isclass(base) else {}
    if "probe_timeout" not in known:
        return "status probe limit: UNCHANGED (rag_core.api.StatusService takes no probe_timeout)"
    default = known["probe_timeout"].default
    if isinstance(default, (int, float)) and default >= STATUS_PROBE_TIMEOUT:
        return f"status probe limit: UNCHANGED (rag_core.status default {default:g} s is not shorter)"

    class ShipStatusService(base):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("probe_timeout", STATUS_PROBE_TIMEOUT)
            super().__init__(*args, **kwargs)

    rag_core.api.StatusService = ShipStatusService
    return (f"status probe limit: {STATUS_PROBE_TIMEOUT:g} s for the embedder and the reranker "
            f"(rag_core.status default: {default!r} s)")


def _with_exact_local(*args, **kwargs):
    lines = list(_apply_gpu_uds(*args, **kwargs))
    spec = importlib.util.spec_from_file_location("exact_local", os.path.join(NODE, "exact_local.py"))
    exact_local = importlib.util.module_from_spec(spec)
    sys.modules["exact_local"] = exact_local
    spec.loader.exec_module(exact_local)
    import rag_core.dense
    import rag_core.retrievers.dense

    exact_local.install(rag_core.dense, rag_core.retrievers.dense)
    lines.append("exact_local: file: Qdrant URLs answered by ExactClient (libd32vec)")
    lines.append(_longer_status_probe())
    return lines


node_layers.apply_gpu_uds = _with_exact_local

import uvicorn  # noqa: E402  (serve_core imports it later, inside main(); the same module object)

_UvicornConfig = uvicorn.Config


class _NoAccessLogConfig(_UvicornConfig):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("access_log", False)
        super().__init__(*args, **kwargs)


uvicorn.Config = _NoAccessLogConfig

_spec = importlib.util.spec_from_file_location("serve_core", os.path.join(SHIMS, "serve_core.py"))
serve_core = importlib.util.module_from_spec(_spec)
sys.modules["serve_core"] = serve_core
_spec.loader.exec_module(serve_core)

if __name__ == "__main__":
    serve_core.main()
