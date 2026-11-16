#!/usr/bin/env python3
"""Apply this lane's engine transforms (190-193, in number order) to a COPY of the engine export.

usage: apply_all.py TREE [--only 190,191,192] [--json]

TREE holds rag/core (and rag/edge for 193). Each transform raises when its target is missing or already changed,
so a second run fails instead of applying twice. --only limits the set (the vendored fixture engine of the bundle tool
has no rag/edge: it takes 190-192). Exit 0 applied, 1 a transform refused, 2 usage. Standard library only.
"""
import sys

sys.dont_write_bytecode = True

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = set(argv[i + 1].split(","))
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    done = []
    for t in sorted(HERE.glob("1[0-9][0-9]-*.py")):
        if only and t.name[:3] not in only:
            continue
        spec = importlib.util.spec_from_file_location("xf_" + t.stem.replace("-", "_"), t)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        try:
            changed = mod.apply(argv[0])
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            print(f"{t.name}: REFUSED {exc}", file=sys.stderr)
            return 1
        done.append({"transform": t.name, "changed": changed})
    print(json.dumps(done) if as_json else "\n".join(f"{d['transform']}: {len(d['changed'])} files" for d in done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
