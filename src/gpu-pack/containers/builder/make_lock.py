#!/usr/bin/env python3
"""Write a --require-hashes lock from a pin list and the wheels pip downloaded for it (on 901, lock-wheels.sh).

usage: make_lock.py --pins FILE --wheels DIR --out FILE

Every "name==version" line of FILE must have exactly one wheel in DIR (names compared in normalised form, local
versions such as "+cpu" kept), and every wheel in DIR must belong to a pinned line (pip pulled nothing the pins
did not name). Writes "name==version --hash=sha256:<hex>" lines in the pins' order. Exit 0 written, 3 refused.
"""
import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import re
from pathlib import Path


def norm(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="make_lock.py", allow_abbrev=False)
    ap.add_argument("--pins", required=True)
    ap.add_argument("--wheels", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    wheels = {}
    for w in sorted(Path(a.wheels).glob("*.whl")):
        name, version = w.name.split("-")[:2]
        wheels.setdefault((norm(name), version), []).append(w)
    lines, used, problems = [], set(), []
    for raw in Path(a.pins).read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        name, _, version = raw.partition("==")
        key = (norm(name.split("[")[0]), version)
        found = wheels.get(key, [])
        if len(found) != 1:
            problems.append(f"{raw}: {len(found)} wheels")
            continue
        used.add(key)
        digest = hashlib.sha256(found[0].read_bytes()).hexdigest()
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    extra = sorted(f"{k[0]}=={k[1]}" for k in wheels if k not in used)
    if extra:
        problems.append("wheels nobody pinned: " + ", ".join(extra[:10]))
    if problems:
        print("make_lock: REFUSED\n  " + "\n  ".join(problems), file=sys.stderr)
        return 3
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"{len(lines)} pins locked into {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
