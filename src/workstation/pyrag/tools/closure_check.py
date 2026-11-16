"""Import closure + import-origin audit for a Deep32 service venv (PYTHON-ON-OPENBSD 3.1, 3.4).

Usage (with the service venv's interpreter, -I -B):
  deep32-engine: python -I -B closure_check.py core <code tree> <standins dir>
  deep32-gateway: python -I -B closure_check.py edge <code tree> <syspick dir>
Imports the uvicorn target the way the launcher does (rag_core.api / deep32-gateway's app built from a dummy environment)
and lists every third-party top-level module loaded, then the origin of every loaded module file: interpreter
prefix (stdlib), venv, code tree, stand-ins or the syspick allow-list. Any module from elsewhere is printed as
OUTSIDE and makes the exit status 1.
"""
import os
import sys

kind, code, extra = sys.argv[1:4]
code = os.path.realpath(code)
extra = os.path.realpath(extra)
before = set(sys.modules)
if kind == "core":
    sys.path.insert(0, os.path.join(code, "src"))
    sys.path.append(extra)
    os.environ.setdefault("RAG_CORE_API_TOKEN", "x" * 40)
    import rag_core.api  # noqa: F401
    own = {"rag_core"}
else:
    sys.path.insert(0, code)
    os.environ.update({"RAG_CORE_URL": "http://127.0.0.1:9", "RAG_CORE_API_TOKEN": "x" * 40,
                       "TAILSCALE_DNS_NAME": "node.invalid"})
    import app  # noqa: F401  (builds the ASGI app from the environment, as `uvicorn app:app` does)
    own = {"app", "rag_edge"}
print(f"IMPORT {kind} OK: python {sys.version.split()[0]}, prefix {sys.prefix}")
third = sorted({m.split(".")[0] for m in set(sys.modules) - before} - set(sys.stdlib_module_names) - own
               - {"__main__", "__mp_main__", "cython_runtime"})
third = [m for m in third if not m.startswith(("_cython_", "_sysconfigdata_"))]  # Cython / sysconfig internals
print(f"third-party top-level modules ({len(third)}): {' '.join(third)}")
import sysconfig

stdlib = os.path.realpath(sysconfig.get_paths(vars={"base": sys.base_prefix, "platbase": sys.base_prefix})["stdlib"])
roots = {
    "stdlib": stdlib,
    "venv": os.path.realpath(sys.prefix),
    "code": code,
    "standins" if kind == "core" else "syspick": extra,
}
counts = {k: 0 for k in roots}
outside = []
for name, mod in sorted(sys.modules.items()):
    f = getattr(mod, "__file__", None)
    if not f or name in ("__main__", "__mp_main__"):  # this script
        continue
    path = os.path.abspath(f)  # the path Python used: a syspick symlink counts as syspick, not as its target
    label = next((k for k, root in sorted(roots.items(), key=lambda kv: -len(kv[1]))
                  if path.startswith(root + os.sep)), None)
    if label == "stdlib" and os.sep + "site-packages" + os.sep in path:
        label = None  # the interpreter's own site-packages (OpenBSD py3-* packages) must never leak in
    if label is None:
        outside.append(f"{name} {f}")
    else:
        counts[label] += 1
print("module origins:", ", ".join(f"{k} {v}" for k, v in counts.items()), f", OUTSIDE {len(outside)}")
for o in outside:
    print("OUTSIDE", o)
sys.exit(1 if outside else 0)
