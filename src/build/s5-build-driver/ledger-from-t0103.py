#!/usr/bin/env python3
"""ledger-from-t0103.py: writes today's asset ledger (Codex T-0103/T-0115) in the ledger v2 form apply-transforms.py reads.

What
  T-0103 keeps its ledger in three files: SHIP-ASSETS.tsv (installed_path, fate: the files to install),
  ASSETS.tsv (every store file with source, licence, notes) and SHA256SUMS (sha256 and store path of every file in
  out/store). This writes one ledger v2 row per store file: a SHIP-ASSETS row becomes a shipped row (fate ORIGINAL
  -> SHIP-OWN, CDE-STOCK -> CDE-STOCK, FONT-MAP -> FONT-MAP), every other store file a HELD row (kept in the store,
  never installed: the PENDING-DESIGN placeholders). asset-rerule's LEDGER-v2.tsv replaces this file when it lands;
  this adapter only lets the build run on today's ledger shape.
Usage
  python -I -B ledger-from-t0103.py <T-0103 folder> <out.tsv>
Exit
  0 written; 1 the inputs disagree (a SHIP-ASSETS path with no store file, an unknown fate); 2 bad usage.
"""
import os
import sys

FATES = {"ORIGINAL": "SHIP-OWN", "CDE-STOCK": "CDE-STOCK", "FONT-MAP": "FONT-MAP"}


def read_tsv(path):
    rows, header = [], None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            cells = line.split("\t")
            if header is None:
                header = cells
                continue
            rows.append(dict(zip(header, cells)))
    return rows


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src, out = argv
    sums = {}
    with open(os.path.join(src, "SHA256SUMS"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                h, rel = line.split("  ", 1)
                sums[rel] = h.lower()
    meta = {r["path"]: r for r in read_tsv(os.path.join(src, "ASSETS.tsv"))}
    ship = {r["installed_path"]: r["fate"] for r in read_tsv(os.path.join(src, "SHIP-ASSETS.tsv"))}
    errors = []
    rows = []
    for ip, fate in sorted(ship.items()):
        rel = ip.lstrip("/")
        if rel not in sums:
            errors.append("shipped %s has no store file" % ip)
            continue
        if fate not in FATES:
            errors.append("shipped %s has fate %r" % (ip, fate))
            continue
        m = meta.get(ip, {})
        rows.append((ip, sums[rel], FATES[fate], "-", rel, ip, m.get("licence", ""), "T-0103 fate " + fate))
    for rel in sorted(sums):
        ip = "/" + rel
        if ip in ship:
            continue
        m = meta.get(ip, {})
        rows.append((ip, sums[rel], "HELD", "-", rel, ip, m.get("licence", ""), "T-0103: in the store, not shipped"))
    if errors:
        print("ledger-from-t0103: %d problem(s): %s" % (len(errors), "; ".join(errors[:10])), file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8", newline="\n") as w:
        # the folder's own name only: this file is copied into the pinned build tree (assets/LEDGER-v2.tsv)
        w.write("# ledger v2 written by ledger-from-t0103.py from %s (today's T-0103/T-0115 shape)\n" %
                os.path.basename(os.path.abspath(src)))
        w.write("installed_path\tsha256\torigin\tpermission_row\tstore_path\tgolden_path\tlicence\tnotes\n")
        for r in rows:
            w.write("\t".join(x.replace("\t", " ") for x in r) + "\n")
    n_ship = sum(1 for r in rows if r[2] != "HELD")
    print("ledger-from-t0103: %d rows (%d shipped, %d held) -> %s" % (len(rows), n_ship, len(rows) - n_ship, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
