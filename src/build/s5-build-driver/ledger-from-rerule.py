#!/usr/bin/env python3
"""ledger-from-rerule.py: asset-rerule's copy list and ledger as the one ledger apply-transforms.py and asset-compare.py read.

What
  asset-rerule (work/opus/asset-rerule) landed two files the build needs together:
    SHIP-ASSETS-v2.tsv  the copy list for disc 1: installed_path (the disc 1 path), fate, source, sha256,
                        permission_row, licence_key, origin. Sources: asset-store/<class>/<laptop path> (pulled by
                        the lead into /build/inputs/asset-store), t0103-store (T-0103's out/store, path = the
                        installed path), cde-package:..., public:theme:..., reproduced ..., art-desktop-gaps (...)
                        and library-marks-pass (...).
    LEDGER-v2.tsv       one row per pack row: installed_path (the laptop's path), dist_path (the disc 1 path),
                        class, install (copy, merge, after-marks-pass, none), pull, store_path and the rest; use
                        the lead's checks/LEDGER-after-check.tsv instead once content-check has run (its LEAD-RUN 7).
  This writes one ledger in apply-transforms.py's form:
    every SHIP-ASSETS-v2 row: copy yes when its source is a store (asset-store/..., t0103-store), else copy no
      (the CDE package, the public theme, a reproduced file, art-desktop-gaps' PENDING drafts and the marks pass's
      relettered files reach the system another way); golden_path and install from the LEDGER-v2 row of the same
      dist_path; fate ORIGINAL (T-0103) becomes SHIP-OWN;
    every LEDGER-v2 row with install 'merge' (configuration merged into the Workstation's files, never copied);
    a HELD row for every store file no other row names: from the asset store's STORE-HASHES.tsv (--store-hashes)
      and T-0103's SHA256SUMS, so a file that appears in a store later still stops the build.
  It refuses (exit 1) an MS-MARKS row whose source is a store (the store holds the laptop's file WITH the mark),
  a STAYS-CUT or MS-FONT-ASK row in the copy list, a t0103-store row whose file or hash is not in T-0103's
  SHA256SUMS, an asset-store row whose hash differs from STORE-HASHES.tsv, and a disc 1 path listed twice.
Usage
  python -I -B ledger-from-rerule.py --ship SHIP-ASSETS-v2.tsv --ledger LEDGER-v2.tsv --t0103 <T-0103 folder>
      [--store-hashes /build/inputs/asset-store/STORE-HASHES.tsv] --out LEDGER-copy.tsv
  then: apply-transforms.py ... --ledger LEDGER-copy.tsv --store asset-store=/build/inputs/asset-store
        --store t0103-store=<T-0103>/out/store;   asset-compare.py ... --ledger LEDGER-copy.tsv
Exit
  0 written; 1 the inputs disagree; 2 bad usage or an unreadable input.
"""
import argparse
import collections
import os
import re
import sys

HEX64 = re.compile(r"^[0-9a-f]{64}$")
FATE = {"ORIGINAL": "SHIP-OWN"}
COLS = ("installed_path", "sha256", "origin", "permission_row", "copy", "store_path", "golden_path", "install",
        "licence_key", "source")


def read_tsv(path, need):
    rows, header = [], None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            cells = line.split("\t")
            if header is None:
                header = cells
                missing = [n for n in need if n not in header]
                if missing:
                    raise ValueError("%s: no column %s" % (path, missing))
                continue
            rows.append(dict(zip(header, cells)))
    if header is None:
        raise ValueError("%s: no header" % path)
    return rows


def origin_of(fate):
    o = fate.strip().split(" ", 1)[0].upper()
    return FATE.get(o, o)


def main(argv):
    ap = argparse.ArgumentParser(prog="ledger-from-rerule.py")
    ap.add_argument("--ship", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--t0103", required=True)
    ap.add_argument("--store-hashes")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        ship = read_tsv(a.ship, ("installed_path", "fate", "source", "sha256", "permission_row"))
        ledger = read_tsv(a.ledger, ("installed_path", "dist_path", "class", "install", "sha256", "store_path"))
        sums = {}
        with open(os.path.join(a.t0103, "SHA256SUMS"), encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if line:
                    h, rel = line.split("  ", 1)
                    sums[rel] = h.lower()
        store = {}
        if a.store_hashes:
            for r in read_tsv(a.store_hashes, ("installed_path", "store_path", "sha256")):
                store[r["store_path"]] = r
    except (OSError, ValueError) as e:
        print("ledger-from-rerule: %s" % e, file=sys.stderr)
        return 2
    by_dist = {r["dist_path"]: r for r in ledger}
    errors, rows, used = [], [], set()
    seen = collections.Counter()
    for r in ship:
        ip, src = r["installed_path"].strip(), r["source"].strip()
        origin = origin_of(r["fate"])
        led = by_dist.get(ip)
        if led and led["install"].strip() == "none":
            # the laptop's file at this path stays cut (for example the title music) and a file of our own ships
            # under the same name (T-0103's ORIGINAL rows): no golden file to compare it with
            led = None
        copy, sp = "no", ""
        sha = r["sha256"].strip().lower()
        if src.startswith("asset-store/"):
            copy, sp = "yes", src
            rel = src[len("asset-store/"):]
            if store and rel not in store:
                errors.append("%s: %s is not in STORE-HASHES.tsv" % (ip, src))
            elif not HEX64.match(sha):
                # 'pull': no laptop hash on build-controller before the pull (priv-fonts has no golden rows); pull.py
                # hashed the file at both ends, so the store's hash is the laptop's
                if not store:
                    errors.append("%s: sha256 %r until the pull: run again with --store-hashes" % (ip, sha))
                else:
                    sha = store[rel]["sha256"].strip().lower()
            elif store and store[rel]["sha256"].lower() != sha:
                errors.append("%s: the store holds %s, the copy list says %s" % (
                    ip, store[rel]["sha256"][:12], sha[:12]))
        elif src == "t0103-store" or src.startswith("t0103-store/"):
            rel = src[len("t0103-store/"):] if src.startswith("t0103-store/") else ip.lstrip("/")
            copy, sp = "yes", "t0103-store/" + rel
            if sums.get(rel) != sha:
                errors.append("%s: T-0103's SHA256SUMS has %s for %s, the copy list says %s" % (
                    ip, (sums.get(rel) or "nothing")[:12], rel, sha[:12]))
        if origin == "MS-MARKS" and copy == "yes":
            errors.append("%s: MS-MARKS from a store would ship the laptop's file with the mark" % ip)
        if origin in ("STAYS-CUT", "MS-FONT-ASK", "HELD"):
            errors.append("%s: %s in the copy list" % (ip, origin))
        seen[ip] += 1
        if sp:
            used.add(sp)
        rows.append({"installed_path": ip, "sha256": sha, "origin": origin,
                     "permission_row": r["permission_row"].strip(), "copy": copy, "store_path": sp,
                     "golden_path": led["installed_path"] if led else ip,
                     "install": led["install"] if led else "copy", "licence_key": r.get("licence_key", ""),
                     "source": src})
    merged = 0
    for led in ledger:
        if led["install"].strip() != "merge":
            continue
        ip = led["dist_path"].strip()
        if seen[ip]:
            continue
        sp = ("asset-store/" + led["store_path"]) if led["store_path"].strip() else ""
        seen[ip] += 1
        merged += 1
        if sp:
            used.add(sp)
        lsha = led["sha256"].strip().lower()
        if not HEX64.match(lsha) and sp and store.get(led["store_path"]):
            lsha = store[led["store_path"]]["sha256"].strip().lower()
        rows.append({"installed_path": ip, "sha256": lsha, "origin": origin_of(led["class"]),
                     "permission_row": led.get("permission_row", "").strip(), "copy": "no", "store_path": sp,
                     "golden_path": led["installed_path"], "install": "merge",
                     "licence_key": led.get("licence_key", ""), "source": "merge into the Workstation's own file"})
    # every other store file must still be named, or apply-transforms stops on it: the MS-MARKS originals the
    # marks pass reads stay in the store as HELD
    for rel, r in sorted(store.items()):
        sp = "asset-store/" + rel
        if sp not in used:
            rows.append({"installed_path": r["installed_path"], "sha256": r["sha256"].lower(), "origin": "HELD",
                         "permission_row": "", "copy": "no", "store_path": sp, "golden_path": r["installed_path"],
                         "install": "none", "licence_key": "", "source": "asset store, not on disc 1 by copy"})
    for rel, h in sorted(sums.items()):
        sp = "t0103-store/" + rel
        if sp not in used:
            rows.append({"installed_path": "/" + rel, "sha256": h, "origin": "HELD", "permission_row": "",
                         "copy": "no", "store_path": sp, "golden_path": "/" + rel, "install": "none",
                         "licence_key": "", "source": "T-0103 store, superseded or a placeholder"})
    dup = sorted(p for p, n in seen.items() if n > 1)
    if dup:
        errors.append("disc 1 path listed twice: %s" % dup[:5])
    if errors:
        print("ledger-from-rerule: %d problem(s):\n  %s" % (len(errors), "\n  ".join(errors[:20])), file=sys.stderr)
        return 1
    with open(a.out, "w", encoding="utf-8", newline="\n") as w:
        w.write("# copy ledger written by ledger-from-rerule.py from %s and %s\n" % (
            os.path.basename(a.ship), os.path.basename(a.ledger)))
        w.write("\t".join(COLS) + "\n")
        for r in rows:
            w.write("\t".join(str(r[c]).replace("\t", " ") for c in COLS) + "\n")
    c = collections.Counter((r["origin"], r["copy"]) for r in rows)
    print("ledger-from-rerule: %d rows: %d to copy (%d asset store, %d T-0103), %d shipped another way, %d merged, "
          "%d held -> %s" % (len(rows), sum(1 for r in rows if r["copy"] == "yes"),
                             sum(1 for r in rows if r["store_path"].startswith("asset-store/") and r["copy"] == "yes"),
                             sum(1 for r in rows if r["store_path"].startswith("t0103-store/") and r["copy"] == "yes"),
                             sum(1 for r in rows if r["copy"] == "no" and r["origin"] != "HELD"
                                 and r["install"] != "merge"),
                             merged, c[("HELD", "no")], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
