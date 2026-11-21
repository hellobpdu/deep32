# deep32-engine tests

These are the engine's own unit tests, the suite I ran while building the engine, adapted to test the two copies in
this repository in place: `src/engine` and the Forge builder's copy under
`src/gpu-pack/containers/builder/engine/rag/core`. All fixtures are synthetic: small RFC-like and Cisco-like texts,
stand-in Qdrant, embedding and reranker services on loopback, and throwaway git repositories. Nothing reaches the
network.

## Running them

From the root of the repository, with Python 3.12 or newer:

```
python -m pip install -r tests/engine/requirements-dev.txt
python -B tests/engine/run_tests.py                    # both copies
python -B tests/engine/run_tests.py --target engine    # src/engine only
DEEP32_ENGINE_TARGET=engine python -B -m unittest tests/engine/test_kb.py
```

`run_tests.py` runs each module in its own process. The modules listed in its `BUILDER_ONLY` drive the build scripts
(`scripts/`), which only the builder's copy has; a single test that needs a script skips on `src/engine`. With
`--skip-missing`, a Python without the packages prints one line and exits 0.

`engine_target.py` chooses the copy and sets up the import path, including the torch and pylate stand-ins from
`src/workstation/pyrag/node/standins`, as the Workstation does. On Windows it also gives the tests two POSIX behaviours
they assume: text files written with `\n`, and temporary folders that can be removed while a SQLite database in them is
still open.

## Skipped here

- Three tests need POSIX file semantics (a `?` in a file name, replacing or removing an open file) and skip on Windows.
- Two tests in `test_plaid_search_batch.py` need pylate itself and skip while the stand-in is on the path.
- `test_bench_pipeline.MainTests` needs the quality evaluator (`eval_v2.py`), which this repository does not carry.

## Left out of this folder

- `test_validate_root.py` and `test_ann_audit.py`: they need the quality evaluator.
- `test_bench_query_embed.py`, `test_colbert_build_knobs.py`: they need torch, transformers, tokenizers or fast-plaid.
- `test_dense_snapshot_live.py`: it needs a live Qdrant server.
