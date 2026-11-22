"""Which copy of deep32-gateway the tests in this folder run against. Not a test module.

The repository carries the gateway twice: src/gateway (rag_edge.py and app.py) and the Forge builder's copy under
src/gpu-pack/containers/builder/engine/rag/edge. The tests run against either one in place:

    DEEP32_GATEWAY_TARGET=gateway  src/gateway (the default)
    DEEP32_GATEWAY_TARGET=builder  the builder's copy

Importing this module puts this folder (support.py and the two earlier releases the tests compare against,
edge_241_render.py and edge_242.py) and the target folder (rag_edge) on sys.path.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGETS = {
    "gateway": ROOT / "src" / "gateway",
    "builder": ROOT / "src" / "gpu-pack" / "containers" / "builder" / "engine" / "rag" / "edge",
}
NAME = os.environ.get("DEEP32_GATEWAY_TARGET", "gateway")
if NAME not in TARGETS:
    raise SystemExit(f"DEEP32_GATEWAY_TARGET={NAME!r}: expected one of {', '.join(TARGETS)}")
REPO = TARGETS[NAME]
# The builder's copy is the gateway of the lab compose line only (src/kb-tools/linux-line/compose/gateway.Dockerfile
# copies engine/rag/edge; the Forge builder image copies only engine/rag/core). It keeps 0.1.0-alpha.2's behaviour, so
# a test of what alpha.3 changed in src/gateway (WP-C) skips there with this reason.
BUILDER = NAME == "builder"
ALPHA3_ONLY = "the Forge builder's lab copy keeps the 0.1.0-alpha.2 gateway; WP-C changed src/gateway only"

for _path in (str(REPO), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
# no bytecode next to the code under test
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
