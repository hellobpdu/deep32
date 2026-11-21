# GPU pack kit tests

Unit tests for the GPU pack (`src/gpu-pack`), from the pack's earlier kit suite: the builder's staging, image and
runtime bindings, base pins, converter options, knowledge-base bundle URLs, pack profiles, signed kernel modules and
the provisioning forms. They need only the Python standard library and Git for Windows' bash on Windows (never WSL).
Docker, the network and the GPU are replaced by synthetic stand-ins.

```
python -B -m unittest tests/gpu-pack/test_builder_stage.py
```

`kit_support.py` points the tests at `src/gpu-pack` and loads its scripts by path. The pack's newer tests live in
`src/gpu-pack/tests`.
