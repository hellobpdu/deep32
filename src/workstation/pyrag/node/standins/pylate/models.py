"""Deep32 stand-in for pylate.models (PYTHON-ON-OPENBSD 4.1): any use raises RuntimeError."""

__deep32_standin__ = True
_WHY = "not available on Deep32: ColBERT is not served"


def __getattr__(name):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    raise RuntimeError(f"pylate.models.{name}: {_WHY}")
