"""Download exact PyPI artefacts and verify each against PyPI's published SHA-256 (the source workstation; the VM is offline).

Usage: python -I fetch_pypi.py CACHE_DIR OUT_DIR LISTFILE
LISTFILE lines: "<name>==<version> <kind>  [# comment]", kind = any (the py3-none-any wheel) | sdist.
Writes OUT_DIR/<file> and appends "<sha256>  <file>" to OUT_DIR/SHA256SUMS (sorted, rewritten each run).

Why not `pip download`: for an sdist pip prepares metadata by running the package's build backend, and numpy's
mesonpy and grpcio's setup.py cannot run without a C compiler (the source workstation has none). The files fetched here are
the exact files pip would fetch (same PyPI URL), checked against the digest PyPI publishes for that file.
"""
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def meta(cache: Path, name: str, version: str) -> dict:
    path = cache / f"{name}-{version}.json"
    if not path.exists():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
            path.write_bytes(r.read())
    return json.loads(path.read_text(encoding="utf-8"))


def pick(files: list, kind: str) -> dict:
    if kind == "sdist":
        c = [f for f in files if f["packagetype"] == "sdist"]
    elif kind == "any":
        c = [f for f in files if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl")]
    else:
        raise SystemExit(f"bad kind {kind}")
    if len(c) != 1:
        raise SystemExit(f"expected one {kind} file, found {[f['filename'] for f in c]}")
    return c[0]


def main() -> int:
    cache, out, listfile = (Path(a) for a in sys.argv[1:4])
    out.mkdir(parents=True, exist_ok=True)
    sums = {}
    sumfile = out / "SHA256SUMS"
    if sumfile.exists():
        for line in sumfile.read_text().splitlines():
            h, f = line.split("  ", 1)
            sums[f] = h
    bad = 0
    for line in listfile.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        spec, kind = line.split()
        name, version = spec.split("==")
        f = pick(meta(cache, norm(name), version)["urls"], kind)
        dest = out / f["filename"]
        if not dest.exists():
            with urllib.request.urlopen(f["url"], timeout=120) as r:
                dest.write_bytes(r.read())
        got = hashlib.sha256(dest.read_bytes()).hexdigest()
        if got != f["digests"]["sha256"]:
            print(f"HASH MISMATCH {dest.name}: got {got}, PyPI says {f['digests']['sha256']}")
            dest.unlink()
            bad += 1
            continue
        sums[dest.name] = got
        print(f"ok {got[:16]} {dest.name}")
    sumfile.write_text("".join(f"{h}  {f}\n" for f, h in sorted(sums.items())), encoding="ascii", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
