"""Shared helpers for the KB builders (build_rfc.py, build_iana.py, build_glossary.py).

- fetch(): polite, cached download. Each URL is fetched once into the raw cache
  (default /var/lib/deep32-forge/kb-work/raw, override with $KB_RAW_DIR) together with a
  <name>.fetch.json sidecar; later runs reuse the cached copy (checked against the
  sidecar sha256) unless refresh=True.
- write_output(): writes <name>.json (one record per line, stable diffs) and the
  sibling <name>.meta.json {source_urls, fetched_at, generator, counts, sha256, sources},
  each via a .part file so an interrupted run never leaves a truncated output.
- default_dirs(): the assembled kept corpus dirs, the default for --corpus-dir/--cisco-dir.
Stdlib only.
"""
import datetime as _dt
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "deep32-kb-fetch/1.0"
KB_DIR = Path(__file__).resolve().parent
RAW_DIR = Path(os.environ.get("KB_RAW_DIR", "/var/lib/deep32-forge/kb-work/raw"))
POLITE_DELAY_S = 1.0  # pause after each real download
PILOT_DIR = Path("/var/lib/deep32-forge/corpus")
RFC_CORPUS_DIRS = ("rfc",)  # Kept corpus directory containing rfcNNNN.txt files
CISCO_CORPUS_DIRS = ("cisco",)  # Kept corpus directory containing <doc_key>.txt files


def default_dirs(names, base: Path = None) -> list:
    """Every existing <base>/<name> dir, so the default cannot silently miss a corpus dir that has been added."""
    base = Path(base or PILOT_DIR)
    return [str(base / n) for n in names if (base / n).is_dir()]


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, name: str, refresh: bool = False, raw_dir: Path = None, tries: int = 3) -> tuple:
    """Return (bytes, source_info) for url, downloading only if not cached (or refresh)."""
    raw_dir = Path(raw_dir or RAW_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest, side = raw_dir / name, raw_dir / f"{name}.fetch.json"
    if dest.exists() and side.exists() and not refresh:
        data, info = dest.read_bytes(), json.loads(side.read_text(encoding="utf-8"))
        if info.get("sha256") and sha256_bytes(data) != info["sha256"]:
            raise RuntimeError(f"cached {dest} does not match the sha256 in {side.name}; re-run with --refresh")
        return data, info
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
                headers = {k.lower(): v for k, v in r.headers.items()}
            if not data:
                raise ValueError("empty response")
            info = {"url": url, "fetched_at": utcnow(), "bytes": len(data), "sha256": sha256_bytes(data),
                    "last_modified": headers.get("last-modified"), "content_type": headers.get("content-type")}
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            side.write_text(json.dumps(info, indent=1), encoding="utf-8")
            time.sleep(POLITE_DELAY_S)
            return data, info
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code} for {url}"
            if e.code in (403, 404, 410):
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            err = f"{type(e).__name__}: {e} for {url}"
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed: {err}")


def dump_records(records: list) -> str:
    """JSON array with one record per line: readable, diff-friendly, still plain JSON."""
    if not records:
        return "[]\n"
    return "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=False) for r in records) + "\n]\n"


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_output(name: str, records: list, sources: list, generator: str, counts: dict, out_dir: Path = None) -> Path:
    """Write <out_dir>/<name>.json and <name>.meta.json (each atomically); return the JSON path."""
    out_dir = Path(out_dir or KB_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    body = dump_records(records).encode("utf-8")
    path = out_dir / f"{name}.json"
    _write_atomic(path, body)
    fetched = [s["fetched_at"] for s in sources if s.get("fetched_at")]
    meta = {
        "source_urls": [s["url"] for s in sources],
        "fetched_at": max(fetched) if fetched else None,
        "generator": generator,
        "generated_at": utcnow(),
        "counts": counts,
        "sha256": sha256_bytes(body),
        "sources": sources,
    }
    _write_atomic(out_dir / f"{name}.meta.json", (json.dumps(meta, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
