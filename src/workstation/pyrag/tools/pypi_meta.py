"""Fetch PyPI JSON for every name==version in the given pin files (production's freezes, H0) into a cache dir.

Usage: python -I pypi_meta.py CACHE_DIR PINFILE [PINFILE ...]
Writes CACHE_DIR/<normalised-name>-<version>.json (PyPI's /pypi/<name>/<version>/json), skips files already there.
Runs on the source workstation (the build VM is offline).
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, version = line.partition("==")
        yield norm(name), version.strip()


def main() -> int:
    cache = Path(sys.argv[1])
    cache.mkdir(parents=True, exist_ok=True)
    wanted = sorted({p for f in sys.argv[2:] for p in pins(Path(f))})
    for name, version in wanted:
        out = cache / f"{name}-{version}.json"
        if out.exists():
            continue
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.load(r)
                break
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                err = exc
                time.sleep(2)
        else:
            print(f"FAIL {name}=={version}: {err}")
            continue
        out.write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"ok {name}=={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
