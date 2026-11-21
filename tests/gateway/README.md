# deep32-gateway tests

These are the gateway's own unit tests, adapted to test the two copies in this repository in place: `src/gateway` and
the Forge builder's copy under `src/gpu-pack/containers/builder/engine/rag/edge`. A fake deep32-engine answers on
`httpx.MockTransport` and the MCP server runs in process, so nothing reaches the network.

`edge_241_render.py` and `edge_242.py` are verbatim copies of earlier gateway releases (2.4.1's rendering and all of
2.4.2). The tests feed the same answers to them and to the gateway as it is and require the same bytes where the
behaviour is meant to be unchanged.

## Running them

From the root of the repository, with Python 3.12 or newer (tested with 3.14):

```
python -m pip install -r tests/gateway/requirements-dev.txt
python -B tests/gateway/run_tests.py                     # both copies
python -B tests/gateway/run_tests.py --target gateway    # src/gateway only
DEEP32_GATEWAY_TARGET=builder python -B -m unittest tests/gateway/test_tools.py
```

`gateway_target.py` chooses the copy. With `--skip-missing`, a Python without the packages prints one line and exits
0. The tests read the version the gateway reports from its own `VERSION`.
