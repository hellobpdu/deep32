"""Derive a service's SHIPPED distribution set from production's freeze (H0) and the service's roots.

Usage: python -I closure_lock.py CACHE_DIR PINFILE PYVER ROOT [ROOT ...] [--exclude NAME ...]
  ROOT is name or name[extra,...] (the distributions the service imports or runs directly).
  PYVER is the node interpreter's minor version (3.12 for deep32-engine, 3.14 for deep32-gateway).

Follows Requires-Dist (with markers evaluated for OpenBSD amd64, CPython PYVER) from the roots, checks each
requirement against production's pinned version, and prints one TSV row per shipped distribution:
  name  version  route  required-by
route: "pure" (a py3-none-any wheel exists on PyPI) or "sdist" (native: built on the VM).
Exit status 1 if a requirement is unpinned in H0 or production's pin does not satisfy it.
Uses pip's vendored `packaging` (the source workstation tool; no third-party install).
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.version import Version


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    args = sys.argv[1:]
    exclude = set()
    if "--exclude" in args:
        i = args.index("--exclude")
        exclude = {norm(a) for a in args[i + 1:]}
        args = args[:i]
    cache, pinfile, pyver, *roots = args
    cache = Path(cache)
    pins = {}
    for line in Path(pinfile).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            n, _, v = line.partition("==")
            pins.setdefault(norm(n), v)  # H0 lists packaging twice (26.0, 26.3): tooling, never shipped
    env = {
        "os_name": "posix", "sys_platform": "openbsd8", "platform_system": "OpenBSD",
        "platform_machine": "amd64", "platform_python_implementation": "CPython",
        "implementation_name": "cpython", "python_version": pyver,
        "python_full_version": pyver + (".14" if pyver == "3.12" else ".9"),
        "implementation_version": pyver + (".14" if pyver == "3.12" else ".9"),
        "platform_release": "8.0", "platform_version": "GENERIC.MP#4",
    }
    shipped = {}
    why = defaultdict(set)
    errors = []
    todo = [(Requirement(r), "ROOT") for r in roots]
    seen = set()
    while todo:
        req, parent = todo.pop()
        name = norm(req.name)
        if name in exclude:
            why[name].add(parent + " (EXCLUDED)")
            continue
        why[name].add(parent)
        version = pins.get(name)
        if version is None:
            errors.append(f"UNPINNED {req} (needed by {parent})")
            continue
        if req.specifier and not req.specifier.contains(Version(version), prereleases=True):
            errors.append(f"CONFLICT {name}=={version} does not satisfy {req} (needed by {parent})")
        key = (name, tuple(sorted(req.extras)))
        if key in seen:
            continue
        seen.add(key)
        meta = json.loads((cache / f"{name}-{version}.json").read_text(encoding="utf-8"))
        shipped[name] = (version, meta)
        for rd in meta["info"].get("requires_dist") or []:
            dep = Requirement(rd)
            ok = False
            for extra in [""] + sorted(req.extras):
                if dep.marker is None or dep.marker.evaluate({**env, "extra": extra}):
                    ok = True
                    break
            if ok:
                todo.append((dep, f"{name}{'[' + ','.join(sorted(req.extras)) + ']' if req.extras else ''}"))
    for name in sorted(shipped):
        version, meta = shipped[name]
        files = meta["urls"]
        pure = any(f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl") for f in files)
        route = "pure" if pure else "sdist"
        print(f"{name}\t{version}\t{route}\t{','.join(sorted(why[name]))}")
    for e in errors:
        print(e, file=sys.stderr)
    excluded = sorted(n for n in why if n in exclude)
    if excluded:
        print("excluded (declared but not shipped): " + ", ".join(excluded), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
