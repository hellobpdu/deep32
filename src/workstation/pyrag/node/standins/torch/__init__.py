"""Deep32 stand-in for torch (PYTHON-ON-OPENBSD 4.1). OpenBSD has no torch; deep32-engine imports it at module load
only for the ColBERT retriever, which is not served. Only deep32-engine's launcher (serve_core.py) puts this directory
on sys.path, after the venv, so a real torch would win. Any use is loud: RuntimeError. Dunder lookups answer
AttributeError so hasattr()/getattr(..., default) probes behave as for a module that lacks the name.
Goes away with upstream layer U1 (lazy ColBERT import)."""

__deep32_standin__ = True
_WHY = "not available on Deep32: ColBERT is not served"


def __getattr__(name):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    raise RuntimeError(f"torch.{name}: {_WHY}")
