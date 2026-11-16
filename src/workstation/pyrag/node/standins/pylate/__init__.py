"""Deep32 stand-in for pylate (PYTHON-ON-OPENBSD 4.1); see torch/__init__.py in this directory. The three
submodules deep32-engine imports (`from pylate import retrieve`, `from pylate import indexes, models`) are real
attributes, so those imports work; anything used from them raises RuntimeError."""

from . import indexes, models, retrieve  # noqa: F401  (attributes for `from pylate import ...`)

__deep32_standin__ = True
_WHY = "not available on Deep32: ColBERT is not served"


def __getattr__(name):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    raise RuntimeError(f"pylate.{name}: {_WHY}")
