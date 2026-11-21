"""Which copy of deep32-engine the tests in this folder run against. Not a test module.

The repository carries the engine twice: src/engine (rag_core and its config) and the Forge builder's copy under
src/gpu-pack/containers/builder/engine/rag/core (rag_core, its config and the build scripts). The tests run against
either one in place, without copying or changing it:

    DEEP32_ENGINE_TARGET=builder  the builder's copy (the default: every test has what it needs there)
    DEEP32_ENGINE_TARGET=engine   src/engine

src/engine has no scripts/ folder: run_tests.py runs the test modules that drive the build scripts (BUILDER_ONLY
there) on the builder's copy only, and single tests that need a script skip on src/engine (SCRIPTS_SKIP).

Importing this module puts on sys.path, in order: this folder (the shared fixtures v2_root.py and fold_root.py), the
target's src/ (rag_core), and, last, the torch and pylate stand-ins from src/workstation/pyrag/node/standins. The
stand-ins answer the engine's module-level imports on a host without torch or pylate, as on the Workstation; a real
torch or pylate in the environment comes first on the path and wins.

It also gives the tests a clean git: the throwaway repositories they make must not pick up the developer's settings
(commit signing, line-end conversion), so the system and global git configuration are left out (posix_like below
covers the rest).
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGETS = {
    "engine": ROOT / "src" / "engine",
    "builder": ROOT / "src" / "gpu-pack" / "containers" / "builder" / "engine" / "rag" / "core",
}
NAME = os.environ.get("DEEP32_ENGINE_TARGET", "builder")
if NAME not in TARGETS:
    raise SystemExit(f"DEEP32_ENGINE_TARGET={NAME!r}: expected one of {', '.join(TARGETS)}")
REPO = TARGETS[NAME]
STANDINS = ROOT / "src" / "workstation" / "pyrag" / "node" / "standins"
HAS_SCRIPTS = (REPO / "scripts").is_dir()
SCRIPTS_SKIP = unittest.skipUnless(HAS_SCRIPTS, f"the {NAME} target has no scripts/ folder "
                                                f"(DEEP32_ENGINE_TARGET=builder runs these tests)")

for _path in (str(REPO / "src"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
if str(STANDINS) not in sys.path:
    sys.path.append(str(STANDINS))
# the same path for the Python processes the tests start (the build scripts, small probes); no bytecode is
# written next to the code under test, by this process or by those
os.environ["PYTHONPATH"] = os.pathsep.join([str(REPO / "src"), str(HERE), str(STANDINS)])
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
WINDOWS = os.name == "nt"
POSIX_ONLY = unittest.skipIf(WINDOWS, "relies on POSIX file semantics (an open file can be replaced or removed, any "
                                      "character but / in a file name); the engine runs on OpenBSD and Linux")


def _standin(name):
    try:
        module = importlib.import_module(name)
    except ImportError:
        return True
    return bool(getattr(module, "__deep32_standin__", False))


NEEDS_PYLATE = unittest.skipIf(_standin("pylate"), "needs pylate itself; the Deep32 stand-in is on the path")


def minimal_env(**extra):
    """The small environment some tests give the build scripts: PATH=/usr/bin:/bin plus their own variables. On
    Windows a Python process does not start without SYSTEMROOT and git is not in /usr/bin, so both come from the
    calling environment there."""
    env = {"PATH": "/usr/bin:/bin"}
    if WINDOWS:
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "PATH") if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
    env.update(extra)
    return env


def posix_like():
    """On Windows, the two POSIX behaviours these tests take for granted (the engine runs on OpenBSD and Linux):
    Path.write_text writes "\\n" line ends, not "\\r\\n"; and a temporary folder can be removed while the code under
    test still holds a SQLite database in it open (Windows refuses with WinError 32, so the removal of what is left
    is not an error). Neither changes what a test checks. Nothing to do on other systems."""
    if os.name != "nt" or getattr(Path.write_text, "deep32_lf", False):
        return
    write_text = Path.write_text

    def write_text_lf(self, data, encoding=None, errors=None, newline="\n"):
        return write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    write_text_lf.deep32_lf = True
    Path.write_text = write_text_lf

    class TemporaryDirectory(tempfile.TemporaryDirectory):
        def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=True, **kwargs):
            super().__init__(suffix, prefix, dir, ignore_cleanup_errors=ignore_cleanup_errors, **kwargs)

    tempfile.TemporaryDirectory = TemporaryDirectory


posix_like()
