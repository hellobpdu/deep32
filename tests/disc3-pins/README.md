# disc 3 pin resolver tests

Offline tests for `src/build/disc3-pins/resolve-pins.py` and `lockgen.py`, which fill in the GPU pack's pins on the
build host. `fixtures.py` gives the resolver a fake web on loopback (registry, Hugging Face, apt repositories, PyPI and
the torch index), a fake host for docker and dpkg, and throwaway OpenPGP keys made by the real gpg in a temporary
folder. `fakepip.py` stands in for pip. Nothing reaches the network.

```
python -B -m unittest tests/disc3-pins/test_resolver.py
```

They need only the Python standard library and gpg and gpgv (GnuPG, or the copies in Git for Windows'
`usr/bin`). `D32_RESOLVER`, `D32_LOCKGEN_SOURCE` and `D32_KIT` point them at other copies of the resolver, lockgen and
the kit (default: `src/build/disc3-pins` and `src/gpu-pack`).

Two tests in `test_resolver.py` expect the pairing-helper pin of an earlier kit and are skipped.
