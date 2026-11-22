# Shared model bridge

`d32_model_bridge.py` is the portable request/authentication and model-attestation
core extracted from the Workstation forwarder. The Workstation wrapper retains
its fixed credential path, tailnet TLS/loopback rules, inherited listeners and
confinement. `node/shims/install.sh` and the ship node staging recipe copy the
canonical module beside the wrapper; a partial build tree supplies its exact
source using `D32_MODEL_BRIDGE_SOURCE`.

The Linux `linux_bridge.py` adapter fixes its only upstream to the authenticated
`door:8081` and `door:8082` services. Internal listeners are `9081` (embeddings)
and `9082` (reranking). Only the exact model POST route and `GET /d32/status`
are accepted. No host port is published. `--check` succeeds only when both
models are authenticated and their expected model/revision or golden probe
matches. With `GPU_EXT_PROBE_ALWAYS` set to `yes`, a matching revision is not
enough: the short known-input probes run on every check, and the probe file's
long ones (a 15-passage rerank and a long embedding input) also run at the
first check after a start, whenever a model reports another identity, and
until they pass. The image carries the probe file at
`/opt/deep32/model-bridge/gpu-probes.json`, a copy of the Workstation's
`/etc/d32/gpu-probes/rerank.json`; `record_gpu_probes.py` records its long
probes on a Forge. Configuration is non-secret JSON at
`/etc/deep32/model-bridge.json`, schema `deep32.model-bridge.v1`, with flat
`GPU_EXT_*` identity/probe fields.
There is no transport, endpoint or credential override in that configuration.
The enclosing release verifier authenticates this file's signed pin metadata.

The Forge bearer is read only from `/run/secrets/forge-token`. Every outgoing
request strips any caller authorisation and attaches this enrolled credential.
The file must be regular, non-linked, bounded, root-owned and mode 0400 or
0440 (the latter with the service group); its parent cannot be group/world
writable. Credential changes or removal immediately refuse model use, including
an existing connection pool. Restart the door and bridge after an enrol/revoke
transaction; the restarted bridge must attest both models again.

`linux_runtime.py engine|gateway` launches the existing application source using
the existing offline dependency closure. The engine uses only
`http://model-bridge:9081/v1/embeddings` and
`http://model-bridge:9082/v1/rerank`. Its API token is loaded from
`/run/secrets/core-token` into the existing in-memory token getter. The gateway
reads that same file and `/run/secrets/mcp-tokens` into its Config object, reaches
only `http://engine:8765`, and exposes the Workstation's eleven read tools at
the exact `/mcp` path on internal port 8766. Missing, changed or removed
credentials fail closed until enrolment and restart. No secret enters argv,
settings, environment or logs.

The engine root is the imported bundle's `kb/root`, with its signed model and
dimension matched against the expected bridge model and revision. The launcher
requires `RAG_TRACE=off`, uses the canonical Workstation `node_layers.py`
immutable SQLite adapter, and refuses a different raw-model or Qdrant URL.
`linux_runtime.py --check gateway` checks authenticated MCP `tools/list` and
requires the exact eleven read tools, so no public gateway health route is needed.

These changes need rebuilt discs and images before a host can use them.
