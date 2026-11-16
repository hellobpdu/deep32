"""G-libm through SQLite (identity lane): production's libm proof recomputed with SQLite's own ln() -- the same
log() call FTS5's bm25 makes for its IDF (sqlite3.c fts5Bm25Function; logFunc for ln) -- in this interpreter's
_sqlite3. d32_ref_record.py libm checks the math module's path; this checks the SQLite path.

  PYTHON -I -B sqlite_libm_proof.py REF/proof        prints counts and differing value indexes only
Exit 0 when every value is bit-equal."""
import json
import sqlite3
import struct
import sys
from pathlib import Path

proof = Path(sys.argv[1])
idx = json.loads((proof / "libm-log.idx").read_text(encoding="utf-8"))
ref = (proof / "libm-log.bin").read_bytes()
con = sqlite3.connect(":memory:")
args = []
for t in idx["domain"]["bm25"]:
    big_n = t["N"]
    args += [(big_n - n + 0.5) / (n + 0.5) for n in range(big_n + 1)]
p_used = idx["domain"]["best_line"]["p_used"]
args += [1 + p / d for p in range(1, p_used + 1) for d in range(1, p + 1)]
if len(args) != idx["values"] or len(ref) != 8 * len(args):
    print(f"sqlite-libm: domain {len(args)} values, proof {idx['values']} ({len(ref) // 8} in the .bin)")
    sys.exit(2)
diffs = []
step = 500
for lo in range(0, len(args), step):
    chunk = args[lo:lo + step]
    sql = "SELECT " + ", ".join("ln(?)" for _ in chunk)
    row = con.execute(sql, chunk).fetchone()
    for j, v in enumerate(row):
        i = lo + j
        if struct.pack("<d", v) != ref[8 * i:8 * i + 8]:
            diffs.append(i)
print(f"sqlite-libm: {len(args)} values through SQLite {sqlite3.sqlite_version} ln() in {sys.executable}: "
      f"{len(diffs)} differ{' (indexes ' + str(diffs[:12]) + (' ...)' if len(diffs) > 12 else ')') if diffs else ''}"
      f": {'PASS' if not diffs else 'FAIL'}")
sys.exit(1 if diffs else 0)
