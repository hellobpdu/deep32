#!/usr/bin/env python3
"""apply-transforms.py: the disc 1 build tree, made on build-controller from the consolidated Workstation tree.

What
  1. Copies the consolidated tree (work/workstation) to <out>/tree, a folder that must not exist yet. Left out of
     the copy: .git and __pycache__ everywhere; at the top level every plain file (the groundwork's reports:
     CUTS.tsv, SOURCE-MAP.tsv, TRANSFORMS.md and the like, which are inputs of their own) and the folders tools/
     and transforms/ (the groundwork's machinery: the build never runs them).
  2. Applies the transforms slot by slot in number order (build sequence 5.0, build-guide section 5):
       base                    100-899  work/workstation/transforms (the groundwork's 101-806)
       t0113                   901      Codex T-0113 (install, settings disc, first start)
       t0120                   902      Codex T-0120 (importers)
       rc1-fixes               903-909  release-candidate corrections
       library-marks-pass      910-939  the Microsoft and Encarta marks pass on the Library
       workstation-ui-screens  940-959  new Workstation screens
       art-desktop-gaps        960-979  desktop gap art
       ui-fixes-after-look     980-989  fixes after his look
       lead                    990-999  the lead
       laptop-look             1000-1009 laptop look parity, after the lead
       name                    1010-1019 visible program name, after laptop look
     Each slot folder holds NNN-name.py files (the export tool's contract: the first docstring line names
     "release specification section N", apply(tree_dir) returns the changed paths, it raises when a target is missing). A file
     whose number lies outside its slot, a number used twice, an unknown slot or a malformed file stops the run
     before anything is copied. Each transform applies once: the files it reports must equal the files that
     really changed (created, changed or deleted) and it must change something; the first one that raises,
     changes nothing or misreports stops the run (exit 1) and no pin is written.
     --prove also re-runs every transform on a scratch copy of the state right after it and on an empty folder:
     both must raise (the groundwork's proof, tools/transforms/run_transforms.py).
  3. --generate runs the Windows-side generators inside the copy (node 24): the art, the Library category art,
     the public desktop theme (theme/out/all, which ship/tools/stage.sh takes) and the sounds.
  4. --ledger copies the asset store by ledger v2 into <out>/tree/assets/root/<installed path>: every copied
     file's sha256 is checked against its row; a store file without a ledger row, a row without its file, a hash
     that differs, an unknown origin class or a missing permission row (13 for SHIP-HP, 14 for SHIP-MS and
     MS-MARKS) stops the run. Rows of class HELD stay in the store and are not copied, nor are rows whose copy
     column says no (the tree, a package or a later transform provides them: the CDE package's icons, the public
     theme, PENDING drafts, the files the marks pass reletters, merged configuration). The ledger's own bytes are
     kept as assets/LEDGER-v2.tsv and the copied set as assets/SHA256SUMS.
  5. Pins the result as a folder source (source-map.json pin rule): <out>/PIN.sha256 holds
     "<sha256>  <relpath>" for every file of <out>/tree, and <out>/TREE-HASH the export tool's tree hash of it.
     The transformed tree's hash before steps 3 and 4 is recorded too (transformed_tree_hash in RUN.json).

Ledger v2 (TSV with a header; '#' lines are comments). Columns, by name, aliases in brackets:
  installed_path [path]            absolute installed path, for example /usr/local/share/d32/x.pm
  sha256                           64 hex (rows not copied may say 'unknown')
  origin [class, origin_class]     SHIP-HP | SHIP-MS | SHIP-OWN | CDE-STOCK | NEW-DRAWING | MS-MARKS | FONT-MAP |
                                   PENDING | HELD | STAYS-CUT | MS-FONT-ASK (the last three never copied); a
                                   parenthesised note after the class is ignored ('PENDING (art-desktop-gaps)')
  permission_row [permission, row] 13 for SHIP-HP, 14 for SHIP-MS and MS-MARKS; other classes empty, '-', 'none',
                                   13 or 14 (asset-rerule marks his own files in the HP look trees with 13)
  copy (optional)                  yes or no; default yes, except HELD, STAYS-CUT and MS-FONT-ASK
  store_path (optional)            path in the store; with named stores its first part is the store's name
                                   ('asset-store/SHIP-HP/...'); default: installed_path without its leading '/'
  golden_path (optional)           the laptop path whose golden hash a SHIP-HP/SHIP-MS/MS-MARKS row is checked
                                   against in asset-compare.py (default installed_path)
  install (optional)               copy | merge | after-marks-pass (golden-compare reads it)
  any other column is kept and ignored here.
  asset-rerule's own LEDGER-v2.tsv (one row per pack row, laptop paths) is refused here: ledger-from-rerule.py
  makes this form from its SHIP-ASSETS-v2.tsv and LEDGER-v2.tsv, with the T-0103 rows LEDGER-v2 does not hold.

Usage
  python -I -B apply-transforms.py --tree DIR --out DIR [--slot NAME=DIR ...] [--prove] [--generate]
      [--ledger LEDGER.tsv --store [NAME=]DIR ... [--cuts CUTS.tsv]] [--expect-transformed-hash HEX]
      [--expect-tree-hash HEX]
  --tree     the consolidated tree (../../workstation)
  --out      a new folder (/build/inputs/... : the copy is about 40 MB); refused if it exists and is not empty,
             or if it lies inside --tree
  --slot     NAME=DIR, repeatable; the base slot defaults to <tree>/transforms
  --store    one unnamed store DIR, or named stores NAME=DIR (repeatable), for example
             asset-store=/build/inputs/asset-store t0103-store=<T-0103>/out/store; asset-rerule's store is
             walked in its class folders only and refused while its _hold/ holds files
  --cuts     CUTS.tsv or asset-rerule's CUTS-v2.tsv: a shipped ledger row on a path it cuts, renames or moves to
             disc 2 stops the run before anything is copied
  --record   where RUN.json, RESULTS.tsv, CHANGES.tsv, TREE-RENAMES.tsv, PIN.sha256 and TREE-HASH go (default <out>)

Exit codes
  0 every step done and pinned; 1 a transform, the generators, the asset copy or an expected hash failed (no pin);
  2 bad arguments, a refused folder or a malformed slot.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

SLOTS = [
    ("base", 100, 899),
    ("t0113", 901, 901),
    ("t0120", 902, 902),
    ("rc1-fixes", 903, 909),
    ("library-marks-pass", 910, 939),
    ("workstation-ui-screens", 940, 959),
    ("art-desktop-gaps", 960, 979),
    ("ui-fixes-after-look", 980, 989),
    ("lead", 990, 999),
    ("laptop-look", 1000, 1009),
    ("name", 1010, 1019),
]
TRANSFORM_RE = re.compile(r"^([0-9]{3,4})-[a-z0-9]+(?:-[a-z0-9]+)*\.py$")
PLAN_REF_RE = re.compile(r"release specification section [0-9]+")
TOP_DIRS_LEFT_OUT = ("tools", "transforms")
ORIGINS = {"SHIP-HP": "13", "SHIP-MS": "14", "MS-MARKS": "14", "SHIP-OWN": None, "CDE-STOCK": None,
           "NEW-DRAWING": None, "FONT-MAP": None, "PENDING": None, "HELD": None, "STAYS-CUT": None,
           "MS-FONT-ASK": None}
NEVER_SHIPPED = ("HELD", "STAYS-CUT", "MS-FONT-ASK")
ALIASES = {"path": "installed_path", "class": "origin", "origin_class": "origin", "permission": "permission_row",
           "row": "permission_row"}
# asset-rerule's store (pull.py): <store>/<class>/<installed path>, STORE-HASHES.tsv at the top, the lead's
# checks/ and sheets/ beside the class folders, and _hold/ (files waiting for his ruling, never shipped)
RERULE_STORE_MARK = "STORE-HASHES.tsv"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GENERATORS = [
    # (folder inside the tree, command): the commands of TRANSFORMS.md section 4 and tools/consolidate/regen.sh
    ("art", ["node", "tools/build.js"]),
    ("art/library-categories", ["node", "tools/build.js"]),
    ("theme", ["node", "tools/gen.js", "--release", "all", "--out", "out/all"]),
    ("sound", ["node", "render.js"]),
]


class Stop(Exception):
    """A run failure: exit 1."""


class Refuse(Exception):
    """Bad arguments or inputs: exit 2."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root):
    """{relpath: sha256} of every file under root; .git and __pycache__ left out."""
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in (".git", "__pycache__"))
        for n in fn:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                raise Stop("a link in the tree: %s" % p)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = sha256_file(p)
    return out


def tree_hash(hashes):
    """The export tool's tree hash (source-export.py tree_hash)."""
    h = hashlib.sha256()
    for rel in sorted(hashes, key=lambda s: s.encode("utf-8", "surrogateescape")):
        h.update(("%s  %s\n" % (hashes[rel], rel)).encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def build_tree_rule(root):
    """{relpath: sha256} of a tree under this tool's copy rule (top-level files, tools/, transforms/ left out)."""
    hashes = hash_tree(root)
    return {k: v for k, v in hashes.items() if "/" in k and k.split("/", 1)[0] not in TOP_DIRS_LEFT_OUT}


def copy_tree(src, dst):
    src = os.path.abspath(src)

    def ignore(d, names):
        left = [n for n in names if n in (".git", "__pycache__")]
        if os.path.abspath(d) == src:
            left += [n for n in names if os.path.isfile(os.path.join(d, n)) or n in TOP_DIRS_LEFT_OUT]
        return set(left)

    shutil.copytree(src, dst, ignore=ignore, symlinks=False)


def slot_range(name):
    for n, lo, hi in SLOTS:
        if n == name:
            return lo, hi
    raise Refuse("unknown slot %r (known: %s)" % (name, ", ".join(s[0] for s in SLOTS)))


def load_slot(name, folder):
    lo, hi = slot_range(name)
    if not os.path.isdir(folder):
        raise Refuse("slot %s: %s is not a folder" % (name, folder))
    found = []
    for fname in sorted(os.listdir(folder)):
        path = os.path.join(folder, fname)
        if fname == "__pycache__":
            continue
        if fname.casefold().endswith(".py") and not fname.endswith(".py") and os.path.isfile(path):
            # 950-x.PY would load on Windows but sort and match outside the contract: refuse, never skip (as
            # source-export.py load_transforms does)
            raise Refuse("slot %s: %s: a transform's name ends in a lower-case .py" % (name, fname))
        if not fname.endswith(".py") or not os.path.isfile(path):
            continue
        m = TRANSFORM_RE.match(fname)
        if not m:
            raise Refuse("slot %s: %s is not named NNN-name.py" % (name, fname))
        num = int(m.group(1))
        if not lo <= num <= hi:
            raise Refuse("slot %s: %s lies outside the slot's numbers %d-%d" % (name, fname, lo, hi))
        spec = importlib.util.spec_from_file_location("s5_transform_%s_%03d" % (name.replace("-", "_"), num), path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            raise Refuse("slot %s: %s does not load: %s" % (name, fname, exc))
        doc = (mod.__doc__ or "").strip().splitlines()
        first = doc[0].strip() if doc else ""
        if not PLAN_REF_RE.search(first):
            raise Refuse("slot %s: %s: first docstring line names no 'release specification section N'" % (name, fname))
        if not callable(getattr(mod, "apply", None)):
            raise Refuse("slot %s: %s has no apply()" % (name, fname))
        if getattr(mod, "EXAMPLE_ONLY", False):
            raise Refuse("slot %s: %s sets EXAMPLE_ONLY" % (name, fname))
        renames = getattr(mod, "RENAMES", None)
        pairs = []
        if isinstance(renames, (list, tuple)):
            pairs = [(a, b) for a, b in renames if isinstance(a, str) and isinstance(b, str)]
        found.append({"num": num, "slot": name, "file": fname, "path": path, "purpose": first,
                      "sha256": sha256_file(path), "apply": mod.apply, "renames": pairs})
    return found


def load_ledger(path):
    """Rows of a ledger v2 file as dicts; raises Refuse on a malformed file."""
    rows, header = [], None
    with open(path, encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            cells = line.split("\t")
            if header is None:
                header = [ALIASES.get(c.strip().lower(), c.strip().lower()) for c in cells]
                if "dist_path" in header and "install" in header:
                    raise Refuse("ledger %s is asset-rerule's LEDGER-v2.tsv itself (one row per pack row, laptop "
                                 "paths): make the copy ledger with ledger-from-rerule.py from SHIP-ASSETS-v2.tsv "
                                 "and LEDGER-v2.tsv (it also holds T-0103's rows, which LEDGER-v2 does not)" % path)
                for need in ("installed_path", "sha256", "origin"):
                    if need not in header:
                        raise Refuse("ledger %s: no column %s (header: %s)" % (path, need, header))
                continue
            if len(cells) != len(header):
                raise Refuse("ledger %s line %d: %d cells, header has %d" % (path, lineno, len(cells), len(header)))
            r = dict(zip(header, cells))
            r["_line"] = lineno
            rows.append(r)
    if header is None:
        raise Refuse("ledger %s: empty" % path)
    return rows


def check_ledger(rows):
    """Row checks that do not need the store. Returns the rows with normalised fields; raises Stop."""
    seen, errors = {}, []
    for r in rows:
        ip, sha = r["installed_path"].strip(), r["sha256"].strip().lower()
        origin = r["origin"].strip().split(" ", 1)[0].upper()
        perm = (r.get("permission_row") or "").strip()
        perm = "" if perm in ("-", "", "none") else perm
        copy = (r.get("copy") or "").strip().lower() or ("no" if origin in NEVER_SHIPPED else "yes")
        where = "line %d (%s)" % (r["_line"], ip)
        if not ip.startswith("/") or "/../" in ip + "/" or "//" in ip or "\\" in ip:
            errors.append("%s: installed_path must be absolute and plain" % where)
        if copy not in ("yes", "no"):
            errors.append("%s: copy must be yes or no, has %r" % (where, copy))
        if not HEX64.match(sha) and (copy == "yes" or origin == "HELD" or (r.get("store_path") or "").strip()):
            errors.append("%s: sha256 is not 64 hex" % where)
        if origin not in ORIGINS:
            errors.append("%s: unknown origin %r" % (where, origin))
        else:
            need = ORIGINS[origin]
            if need and perm != need:
                errors.append("%s: origin %s needs permission row %s, has %r" % (where, origin, need, perm))
            # other classes carry no row, or 13/14 when they sit in the HP look trees or the Library (asset-rerule)
            if not need and perm not in ("", "13", "14"):
                errors.append("%s: origin %s: permission row %r is neither 13 nor 14" % (where, origin, perm))
            if origin in NEVER_SHIPPED[1:] and copy == "yes":
                errors.append("%s: origin %s is never shipped, but the row says copy" % (where, origin))
        if origin not in NEVER_SHIPPED:
            if ip in seen:
                errors.append("%s: installed_path shipped twice (also line %d)" % (where, seen[ip]))
            seen[ip] = r["_line"]
        sp = (r.get("store_path") or "").strip()
        if not sp and (copy == "yes" or origin == "HELD"):
            sp = ip.lstrip("/")
        if sp.startswith("/") or ".." in sp.replace("\\", "/").split("/"):
            errors.append("%s: store_path %r leaves the store" % (where, sp))
        r.update(installed_path=ip, sha256=sha, origin=origin, permission_row=perm, store_path=sp.replace("\\", "/"),
                 copy=copy)
    if errors:
        raise Stop("ledger: %d problem(s):\n  %s" % (len(errors), "\n  ".join(errors[:40])))
    return rows


def walk_store(name, store):
    """{store path: file} of one store. A named store's paths start with its name ('asset-store/SHIP-HP/...').

    asset-rerule's store (STORE-HASHES.tsv at its top) is walked in its class folders only: its top-level files and
    the lead's checks/ and sheets/ are bookkeeping, not assets; files left in _hold/ (waiting for his ruling) stop
    the run, as LEAD-RUN step 9 empties it before anything leaves build-controller."""
    store = os.path.abspath(store)
    if not os.path.isdir(store):
        raise Refuse("store %s is not a folder" % store)
    tops = None
    if os.path.isfile(os.path.join(store, RERULE_STORE_MARK)):
        hold = os.path.join(store, "_hold")
        if os.path.isdir(hold) and any(fn for _, _, fn in os.walk(hold)):
            raise Stop("store %s: _hold/ still holds files (asset-rerule LEAD-RUN step 9 empties it first)" % store)
        tops = sorted(n for n in os.listdir(store) if os.path.isdir(os.path.join(store, n)) and n in ORIGINS)
    files = {}
    for top in (tops if tops is not None else [""]):
        base = os.path.join(store, top) if top else store
        for dp, dn, fn in os.walk(base):
            dn.sort()
            for n in fn:
                p = os.path.join(dp, n)
                if os.path.islink(p):
                    raise Stop("store: a link: %s" % p)
                rel = os.path.relpath(p, store).replace(os.sep, "/")
                files[(name + "/" + rel) if name else rel] = p
    return files


def check_against_cuts(rows, cuts_path):
    """Stops when a shipped ledger row lands on a path CUTS (v1 or v2) takes off disc 1: a cut feature's art (the
    air-gapped editor's icons, for one) must not come back through the asset store."""
    cut = {}
    with open(cuts_path, encoding="utf-8-sig") as f:
        header = None
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            c = line.split("\t")
            if header is None:
                header = c
                if "golden_path" not in header or "what" not in header:
                    raise Refuse("cuts %s: no golden_path or what column" % cuts_path)
                continue
            r = dict(zip(header, c))
            if r["what"].strip() in ("cut", "renamed", "moved-to-disc-2"):
                cut[r["golden_path"].strip()] = r["what"].strip()
    bad = ["%s (ledger line %d, %s): CUTS says %s" % (r["installed_path"], r["_line"], r["origin"],
                                                     cut[r["installed_path"]])
           for r in rows if r["origin"] not in NEVER_SHIPPED and r["installed_path"] in cut]
    if bad:
        raise Stop("ledger ships %d path(s) CUTS takes off disc 1:\n  %s" % (len(bad), "\n  ".join(bad[:20])))


PRODUCED_ASSETS = [
    ("library/name/strings.tsv", "/d32/a/share/deep32-library/assets/ui/strings.tsv",
     "name/1012-encyclopedia-sid-strings.py",
     "abef0c929f8df52e937a79eda25dfb1484fb0c3756c104f9d3210f471594fe10", "MS-MARKS", "14"),  # sha256 or public signing fingerprint
    ("library/name/late/etc/dt/appconfig/types/C/deep32.dt", "/etc/dt/appconfig/types/C/deep32.dt",
     "name/1015-late-installed-names.py:/etc/dt/appconfig/types/C/deep32.dt",
     "5ac894e9e4ac5c51495f51d2a0cde68309c449f2516a9c19a02eb35b799abc97", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/etc/dt/appconfig/types/C/deep32.fp", "/etc/dt/appconfig/types/C/deep32.fp",
     "name/1015-late-installed-names.py:/etc/dt/appconfig/types/C/deep32.fp",
     "bdf82069f15f797c93df51239202f27b29e82c00d21328664d5223be626c8a1d", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/etc/dt/appconfig/types/C/library.dt", "/etc/dt/appconfig/types/C/library.dt",
     "name/1015-late-installed-names.py:/etc/dt/appconfig/types/C/library.dt",
     "ab4351680359a018ec82e016d69099690b80cdddae77413e723d23a6ee4416a2", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/library/index.html", "/usr/local/share/d32/library/index.html",
     "name/1015-late-installed-names.py:/usr/local/share/d32/library/index.html",
     "e8d6fd63bfc8abe9132fbbcd88aeab8e7dd74709f154e20af445e7fd81b03489", "SHIP-OWN", ""),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/hp/user/.dt/types/deep32.dt", "/usr/local/share/d32/look/hp/user/.dt/types/deep32.dt",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/hp/user/.dt/types/deep32.dt",
     "c23b210f41f744f0c73e086c733e529a1f65ce460537543a9d64a6a9be1a3b03", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/hp/user/.dt/types/deep32.fp", "/usr/local/share/d32/look/hp/user/.dt/types/deep32.fp",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/hp/user/.dt/types/deep32.fp",
     "bdf82069f15f797c93df51239202f27b29e82c00d21328664d5223be626c8a1d", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/hp/user/.dt/types/library.dt", "/usr/local/share/d32/look/hp/user/.dt/types/library.dt",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/hp/user/.dt/types/library.dt",
     "ab4351680359a018ec82e016d69099690b80cdddae77413e723d23a6ee4416a2", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/vue/user/.dt/types/deep32.dt", "/usr/local/share/d32/look/vue/user/.dt/types/deep32.dt",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/vue/user/.dt/types/deep32.dt",
     "c23b210f41f744f0c73e086c733e529a1f65ce460537543a9d64a6a9be1a3b03", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/vue/user/.dt/types/deep32.fp", "/usr/local/share/d32/look/vue/user/.dt/types/deep32.fp",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/vue/user/.dt/types/deep32.fp",
     "bdf82069f15f797c93df51239202f27b29e82c00d21328664d5223be626c8a1d", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
    ("library/name/late/usr/local/share/d32/look/vue/user/.dt/types/library.dt", "/usr/local/share/d32/look/vue/user/.dt/types/library.dt",
     "name/1015-late-installed-names.py:/usr/local/share/d32/look/vue/user/.dt/types/library.dt",
     "ab4351680359a018ec82e016d69099690b80cdddae77413e723d23a6ee4416a2", "SHIP-OWN", "13"),  # sha256 or public signing fingerprint
]


LEAD_BITMAP_ASSETS = [
    {'target': 'library/name/lead-bitmaps/title1-26.dib.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/e26/title1-26.dib.ppm', 'producer': 'lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf', 'sha256': '408a939b02eafa2c74737af1e6fac0ce968d0f78635a3110c3a04710bda10fc3', 'store_path': 'lead-name-store/MS-MARKS/408a939b02eafa2c74737af1e6fac0ce968d0f78635a3110c3a04710bda10fc3', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/e26/title1-26.dib.ppm","producer":"lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf","sha256":"408a939b02eafa2c74737af1e6fac0ce968d0f78635a3110c3a04710bda10fc3","store_path":"lead-name-store/MS-MARKS/408a939b02eafa2c74737af1e6fac0ce968d0f78635a3110c3a04710bda10fc3"}\n', 'reference_sha256': '84cd787e89c533b6e23487e09529806fd3262b7a6207ebb4a74ab8e84cb9f3ad', 'bytes': 921615, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/title26.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/e26/title26.ppm', 'producer': 'lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf', 'sha256': '7c303ebf50c5e0da278939e8b590bba39aa607004f8d8c3dd3db54f5001ddd67', 'store_path': 'lead-name-store/MS-MARKS/7c303ebf50c5e0da278939e8b590bba39aa607004f8d8c3dd3db54f5001ddd67', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/e26/title26.ppm","producer":"lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf","sha256":"7c303ebf50c5e0da278939e8b590bba39aa607004f8d8c3dd3db54f5001ddd67","store_path":"lead-name-store/MS-MARKS/7c303ebf50c5e0da278939e8b590bba39aa607004f8d8c3dd3db54f5001ddd67"}\n', 'reference_sha256': '98484fa0435c1fb82e150d10727c874720550c10a5a9ad15cc9a82b03c2eeedc', 'bytes': 8845, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/SPLASH16.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/e96/ui/bitmap/SPLASH16.ppm', 'producer': 'lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3', 'sha256': 'cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069', 'store_path': 'lead-name-store/MS-MARKS/cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/e96/ui/bitmap/SPLASH16.ppm","producer":"lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3","sha256":"cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069","store_path":"lead-name-store/MS-MARKS/cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069"}\n', 'reference_sha256': 'bce6f8b0bc12bdd06f4f364e19472542be1ee596fbe13e82fe0a0def6df150ad', 'bytes': 215055, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/title1.dib.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/hilite/title1.dib.ppm', 'producer': 'lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf', 'sha256': '2bd68dac1f7d7e2603de85089de7b4692f65c1405ad84f911a17061a83787eb5', 'store_path': 'lead-name-store/MS-MARKS/2bd68dac1f7d7e2603de85089de7b4692f65c1405ad84f911a17061a83787eb5', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/hilite/title1.dib.ppm","producer":"lead/name-bitmaps/titlename.py@sha256:0b1ff0b91dc91f62e1166addf52317046bfaed69a95c88cf75f0106952468331+lead/name-bitmaps/emblem.py@sha256:bd702b2d0ec257434e10930a4bc66971d5e1fd26bec1315dd240c8382a4b3fdf","sha256":"2bd68dac1f7d7e2603de85089de7b4692f65c1405ad84f911a17061a83787eb5","store_path":"lead-name-store/MS-MARKS/2bd68dac1f7d7e2603de85089de7b4692f65c1405ad84f911a17061a83787eb5"}\n', 'reference_sha256': '36b6dfc8fa04666f8c5e025dbd168f18d90450d9596cc1fd46b0ba468bda87c3', 'bytes': 921615, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/SPLASH.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/ui/bitmap/SPLASH.ppm', 'producer': 'lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3', 'sha256': 'cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069', 'store_path': 'lead-name-store/MS-MARKS/cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/ui/bitmap/SPLASH.ppm","producer":"lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3","sha256":"cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069","store_path":"lead-name-store/MS-MARKS/cc0ad33f3856be66777420e6938cd17b46bd132e10fc468994159ee18f8a4069"}\n', 'reference_sha256': 'a86d07080fa2679f517530b00d7bc4b968eeec79f91ccbc1d11f862cf0108d75', 'bytes': 215055, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/SPLASHVGA.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/ui/bitmap/SPLASHVGA.ppm', 'producer': 'lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3', 'sha256': '631206ce0d528c5674591d59de76b532b700da4f9e2dbc9f10c359de08dc3d56', 'store_path': 'lead-name-store/MS-MARKS/631206ce0d528c5674591d59de76b532b700da4f9e2dbc9f10c359de08dc3d56', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/ui/bitmap/SPLASHVGA.ppm","producer":"lead/name-bitmaps/splash.py@sha256:9a2674b093d0abae6d0eef274f79a3100b62cb5c2dca1e6ea6042092eedc94e3","sha256":"631206ce0d528c5674591d59de76b532b700da4f9e2dbc9f10c359de08dc3d56","store_path":"lead-name-store/MS-MARKS/631206ce0d528c5674591d59de76b532b700da4f9e2dbc9f10c359de08dc3d56"}\n', 'reference_sha256': 'e50855e50c18a496196856f7d637955037a3f06433514157e62bffe4a37651f7', 'bytes': 215055, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
    {'target': 'library/name/lead-bitmaps/WORLDOF.ppm.ref', 'installed_path': '/d32/a/share/deep32-library/assets/ui/bitmap/WORLDOF.ppm', 'producer': 'lead/name-bitmaps/worldof.py@sha256:876833933863dded7455869839f04930b12b8ae88fe8268accfed65243284ffb', 'sha256': '288ff16f97ca083c89157c0b60fb6beacab2bfea2251f0df66fbe5d1fd3b5682', 'store_path': 'lead-name-store/MS-MARKS/288ff16f97ca083c89157c0b60fb6beacab2bfea2251f0df66fbe5d1fd3b5682', 'reference_ascii': 'D32-NAME-BITMAP/1\n{"installed_path":"/d32/a/share/deep32-library/assets/ui/bitmap/WORLDOF.ppm","producer":"lead/name-bitmaps/worldof.py@sha256:876833933863dded7455869839f04930b12b8ae88fe8268accfed65243284ffb","sha256":"288ff16f97ca083c89157c0b60fb6beacab2bfea2251f0df66fbe5d1fd3b5682","store_path":"lead-name-store/MS-MARKS/288ff16f97ca083c89157c0b60fb6beacab2bfea2251f0df66fbe5d1fd3b5682"}\n', 'reference_sha256': 'b92b7dc65b067b927fd978fc6ad05abb302ccf0ded81ca3f4d3d0118ebcbe802', 'bytes': 50666, 'origin_detail': 'lead re-lettering 2026-11-15'},  # sha256 or public signing fingerprint
]


def copy_produced_assets(rows, tree, root, shipped, store_files=None):
    """Install registered transform outputs after the ordinary ledger copies."""
    prepared = []
    seen = set(shipped)
    for rel, ip, producer, want, origin, permission in PRODUCED_ASSETS:
        source = os.path.join(tree, *rel.split("/"))
        matches = [r for r in rows if r.get("producer") == producer]
        if not matches and not os.path.lexists(source):
            continue
        if len(matches) != 1:
            raise Stop("produced asset: expected exactly one producer row: " + producer)
        row = matches[0]
        live = [r for r in rows if r["installed_path"] == ip and r["origin"] != "HELD"]
        fields = {"installed_path": ip, "sha256": want, "origin": origin,
                  "permission_row": permission, "copy": "no", "store_path": "",
                  "install": "after-marks-pass", "producer": producer}
        if len(live) != 1 or live[0] is not row or any(row.get(k) != v for k, v in fields.items()):
            raise Stop("produced asset: ledger row differs: " + producer)
        for base, subpath in ((tree, rel), (root, ip.lstrip("/"))):
            parts = subpath.split("/")
            paths = [base] + [os.path.join(base, *parts[:n]) for n in range(1, len(parts) + 1)]
            if any(os.path.islink(p) or os.path.isjunction(p) for p in paths):
                raise Stop("produced asset: linked source or destination: " + producer)
        if not os.path.isfile(source):
            raise Stop("produced asset: source absent: " + rel)
        if sha256_file(source) != want:
            raise Stop("produced asset: source hash differs: " + rel)
        if ip in seen:
            raise Stop("produced asset: destination already copied: " + ip)
        seen.add(ip)
        prepared.append((source, ip, want))
    lead_producers = {entry["producer"] for entry in LEAD_BITMAP_ASSETS}
    lead_active = any(os.path.lexists(os.path.join(tree, *entry["target"].split("/")))
                      for entry in LEAD_BITMAP_ASSETS) or any(
        row.get("producer") in lead_producers or
        row.get("store_path", "").startswith("lead-name-store/")
        for row in rows)
    if lead_active:
        for entry in LEAD_BITMAP_ASSETS:
            rel, ip = entry["target"], entry["installed_path"]
            producer, want = entry["producer"], entry["sha256"]
            reference = os.path.join(tree, *rel.split("/"))
            matches = [r for r in rows if r.get("producer") == producer and r["installed_path"] == ip]
            live = [r for r in rows if r["installed_path"] == ip and r["origin"] != "HELD"]
            fields = {"installed_path": ip, "sha256": want, "origin": "MS-MARKS",
                      "origin_detail": entry["origin_detail"], "permission_row": "14",
                      "copy": "no", "store_path": entry["store_path"],
                      "install": "after-marks-pass", "producer": producer}
            if (len(matches) != 1 or len(live) != 1 or live[0] is not matches[0] or
                    any(matches[0].get(k) != v for k, v in fields.items())):
                raise Stop("lead bitmap: ledger row differs: " + ip)
            for base, subpath in ((tree, rel), (root, ip.lstrip("/"))):
                parts = subpath.split("/")
                paths = [base] + [os.path.join(base, *parts[:n]) for n in range(1, len(parts) + 1)]
                if any(os.path.islink(p) or os.path.isjunction(p) for p in paths):
                    raise Stop("lead bitmap: linked reference or destination: " + ip)
            if not os.path.isfile(reference):
                raise Stop("lead bitmap: reference absent: " + rel)
            with open(reference, "rb") as handle:
                encoded = handle.read()
            if (encoded != entry["reference_ascii"].encode("ascii") or
                    hashlib.sha256(encoded).hexdigest() != entry["reference_sha256"]):
                raise Stop("lead bitmap: reference differs: " + rel)
            if store_files is None or entry["store_path"] not in store_files:
                raise Stop("lead bitmap: validated store source absent: " + ip)
            source = os.path.abspath(store_files[entry["store_path"]])
            ancestor = source
            while True:
                if os.path.islink(ancestor) or os.path.isjunction(ancestor):
                    raise Stop("lead bitmap: linked store source: " + ip)
                parent = os.path.dirname(ancestor)
                if parent == ancestor:
                    break
                ancestor = parent
            if (not os.path.isfile(source) or os.path.getsize(source) != entry["bytes"] or
                    sha256_file(source) != want):
                raise Stop("lead bitmap: store source differs: " + ip)
            if ip in seen:
                raise Stop("lead bitmap: destination already copied: " + ip)
            seen.add(ip)
            prepared.append((source, ip, want))
    # Validate every registered output before copying any of them.
    for source, ip, want in prepared:
        destination = os.path.join(root, *ip.lstrip("/").split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(destination) != want:
            raise Stop("produced asset: installed hash differs: " + ip)
        shipped[ip] = want


def copy_assets(rows, stores, tree, ledger_path):
    """Copies the rows marked copy from their store into tree/assets/root; returns {installed_path: sha256}.

    stores: {name: folder}; one unnamed store ({"": folder}) takes store paths as they are, named stores take
    store paths whose first part is the store's name. Every file of every store must be named by a row (HELD rows
    included); every row to copy must find its file; every hash is checked before and after the copy."""
    store_files = {}
    for name, folder in sorted(stores.items()):
        store_files.update(walk_store(name, folder))
    by_store_path = {}
    for r in rows:
        if r["store_path"]:     # rows to copy, HELD rows and rows that name a store file they do not copy
            by_store_path.setdefault(r["store_path"], []).append(r)
    stray = sorted(set(store_files) - set(by_store_path))
    if stray:
        raise Stop("store: %d file(s) without a ledger row (first: %s); the build stops" % (len(stray), stray[:5]))
    if all(stores):
        unnamed = sorted(sp for sp in by_store_path if sp.split("/", 1)[0] not in stores)
        if unnamed:
            raise Stop("store: %d row(s) whose store path names no given store (stores: %s; first: %s)" % (
                len(unnamed), sorted(stores), unnamed[:3]))
    missing = sorted(sp for sp in by_store_path if sp not in store_files)
    if missing:
        raise Stop("store: %d ledger row(s) whose file is not in the store (first: %s)" % (len(missing), missing[:5]))
    root = os.path.join(tree, "assets", "root")
    if os.path.exists(os.path.join(tree, "assets")):
        raise Stop("the tree already has an assets/ folder: %s" % os.path.join(tree, "assets"))
    shipped, bad = {}, []
    for sp in sorted(by_store_path):
        actual = sha256_file(store_files[sp])
        for r in by_store_path[sp]:
            if actual != r["sha256"]:
                bad.append("%s (ledger line %d): store file %s has %s, ledger says %s" % (
                    r["installed_path"], r["_line"], sp, actual[:12], r["sha256"][:12]))
    if bad:
        raise Stop("store: %d hash mismatch(es):\n  %s" % (len(bad), "\n  ".join(bad[:20])))
    for r in sorted(rows, key=lambda x: x["installed_path"]):
        if r["origin"] in NEVER_SHIPPED or r["copy"] != "yes":
            continue
        dst = os.path.join(root, *r["installed_path"].lstrip("/").split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(store_files[r["store_path"]], dst)
        if sha256_file(dst) != r["sha256"]:
            raise Stop("copy of %s does not hash as its row" % r["installed_path"])
        shipped[r["installed_path"]] = r["sha256"]
    copy_produced_assets(rows, tree, root, shipped, store_files)
    shutil.copyfile(ledger_path, os.path.join(tree, "assets", "LEDGER-v2.tsv"))
    with open(os.path.join(tree, "assets", "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as w:
        for ip in sorted(shipped):
            w.write("%s  %s\n" % (shipped[ip], ip.lstrip("/")))
    return shipped


def run_generators(tree, logdir):
    for folder, cmd in GENERATORS:
        cwd = os.path.join(tree, *folder.split("/"))
        if not os.path.isdir(cwd):
            raise Stop("generator folder missing: %s" % folder)
        exe = shutil.which(cmd[0])
        if not exe:
            raise Stop("generator needs %s on PATH" % cmd[0])
        log = os.path.join(logdir, "gen-%s.log" % folder.replace("/", "-"))
        with open(log, "wb") as lf:
            rc = subprocess.call([exe] + cmd[1:], cwd=cwd, stdout=lf, stderr=subprocess.STDOUT)
        print("generate %-24s %s: rc %d (log %s)" % (folder, " ".join(cmd), rc, log))
        if rc != 0:
            raise Stop("generator %s in %s failed (rc %d), see %s" % (" ".join(cmd), folder, rc, log))


def apply_all(transforms, tree, out, prove):
    rows, changes, renames = [], [], []
    state = hash_tree(tree)
    for t in transforms:
        row = {"num": "%03d" % t["num"], "slot": t["slot"], "transform": t["file"], "sha256": t["sha256"],
               "purpose": t["purpose"]}
        rows.append(row)
        try:
            result = t["apply"](tree)
        except Exception as exc:
            row["first"] = "RAISED: " + traceback.format_exception_only(type(exc), exc)[-1].strip()
            raise Stop("%s (%s) does not apply: %s" % (t["file"], t["slot"], row["first"])) from None
        if not isinstance(result, (list, tuple)) or not all(isinstance(x, str) for x in result):
            row["first"] = "BAD RESULT"
            raise Stop("%s returned %r, not a list of paths" % (t["file"], type(result).__name__))
        reported = set(x.replace("\\", "/").lstrip("./") for x in result)
        after = hash_tree(tree)
        actual = set(k for k in set(state) | set(after) if state.get(k) != after.get(k))
        if not actual:
            row["first"] = "CHANGED NOTHING"
            raise Stop("%s changed nothing" % t["file"])
        if actual != reported:
            row["first"] = "MISMATCH"
            raise Stop("%s: reported files differ from the changed files: unreported %s; not changed %s" % (
                t["file"], sorted(actual - reported)[:5], sorted(reported - actual)[:5]))
        row["first"] = "ok"
        row["changed"] = str(len(actual))
        row["created"] = str(sum(1 for k in actual if k not in state))
        row["deleted"] = str(sum(1 for k in actual if k not in after))
        for k in sorted(actual):
            changes.append((k, row["num"], t["file"],
                            "created" if k not in state else ("deleted" if k not in after else "changed")))
        for a, b in t["renames"]:
            if a in actual and b in actual and a not in after and b in after:
                renames.append((a, b, row["num"]))
        if prove:
            scratch = tempfile.mkdtemp(prefix="second-", dir=out)
            try:
                shutil.copytree(tree, os.path.join(scratch, "t"))
                try:
                    again = t["apply"](os.path.join(scratch, "t"))
                    row["second"] = "DID NOT FAIL (%d paths)" % len(again or [])
                except Exception as exc:
                    row["second"] = "raised: " + traceback.format_exception_only(type(exc), exc)[-1].strip()[:160]
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            empty = tempfile.mkdtemp(prefix="empty-", dir=out)
            try:
                try:
                    t["apply"](empty)
                    row["empty"] = "DID NOT FAIL"
                except Exception as exc:
                    row["empty"] = "raised: " + traceback.format_exception_only(type(exc), exc)[-1].strip()[:160]
            finally:
                shutil.rmtree(empty, ignore_errors=True)
            if not (row["second"].startswith("raised") and row["empty"].startswith("raised")):
                raise Stop("%s: second run %s; empty folder %s" % (t["file"], row["second"], row["empty"]))
        print("ok   %s %s (%s): %s changed (%s created, %s deleted)%s" % (
            row["num"], t["file"], t["slot"], row["changed"], row["created"], row["deleted"],
            "; second run and empty folder raised" if prove else ""))
        state = after
    return rows, changes, renames


def write_tsv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as w:
        w.write("\t".join(cols) + "\n")
        for r in rows:
            if isinstance(r, dict):
                r = [str(r.get(c, "")) for c in cols]
            w.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in r) + "\n")


def main(argv):
    ap = argparse.ArgumentParser(prog="apply-transforms.py", description=__doc__.split("\n")[0])
    ap.add_argument("--tree", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--slot", action="append", default=[], metavar="NAME=DIR")
    ap.add_argument("--prove", action="store_true")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--ledger")
    ap.add_argument("--store", action="append", default=[], metavar="[NAME=]DIR")
    ap.add_argument("--record")
    ap.add_argument("--cuts")
    ap.add_argument("--expect-transformed-hash")
    ap.add_argument("--expect-tree-hash")
    a = ap.parse_args(argv)
    src = os.path.abspath(a.tree)
    out = os.path.abspath(a.out)
    record = os.path.abspath(a.record or out)
    try:
        if not os.path.isdir(src):
            raise Refuse("--tree %s is not a folder" % src)
        if (out + os.sep).startswith(src + os.sep):
            raise Refuse("--out %s lies inside the tree" % out)
        if os.path.exists(out) and os.listdir(out):
            raise Refuse("--out %s exists and is not empty: a fresh folder is needed" % out)
        if bool(a.ledger) != bool(a.store):
            raise Refuse("--ledger and --store go together")
        stores = {}
        for s in a.store:
            name, folder = s.split("=", 1) if ("=" in s and not re.match(r"^[A-Za-z]:[\\/]", s)) else ("", s)
            if name in stores or (stores and ("" in stores or not name)):
                raise Refuse("--store: one unnamed store, or named stores (NAME=DIR) each given once")
            if name and not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
                raise Refuse("--store: store name %r is not lower-case words with dashes" % name)
            stores[name] = os.path.abspath(folder)
        slots = {}
        for s in a.slot:
            if "=" not in s:
                raise Refuse("--slot %r is not NAME=DIR" % s)
            name, folder = s.split("=", 1)
            slot_range(name)
            if name in slots:
                raise Refuse("slot %s given twice" % name)
            slots[name] = os.path.abspath(folder)
        slots.setdefault("base", os.path.join(src, "transforms"))
        transforms = []
        for name, _, _ in SLOTS:
            if name in slots:
                transforms += load_slot(name, slots[name])
        nums = [t["num"] for t in transforms]
        dup = sorted(set(n for n in nums if nums.count(n) > 1))
        if dup:
            raise Refuse("transform number(s) used twice: %s" % dup)
        transforms.sort(key=lambda t: t["num"])
        if not transforms:
            raise Refuse("no transforms found")
        ledger_rows = None
        if a.ledger:
            ledger_rows = check_ledger(load_ledger(a.ledger))
            if a.cuts:
                check_against_cuts(ledger_rows, a.cuts)
        elif a.cuts:
            raise Refuse("--cuts checks the ledger: give --ledger too")
    except Refuse as e:
        print("apply-transforms: REFUSED: %s" % e, file=sys.stderr)
        return 2
    except Stop as e:
        print("apply-transforms: FAILED: %s" % e, file=sys.stderr)
        return 1
    os.makedirs(out, exist_ok=True)
    os.makedirs(record, exist_ok=True)
    tree = os.path.join(out, "tree")
    run = {"tool": "apply-transforms.py", "tree_source": src.replace(os.sep, "/"),
           "slots": {k: v.replace(os.sep, "/") for k, v in slots.items()},
           "transforms": [{"num": t["num"], "slot": t["slot"], "file": t["file"], "sha256": t["sha256"]}
                          for t in transforms],
           "python": sys.version.split()[0], "status": "running"}
    rows = changes = renames = []
    rc = 0
    try:
        print("copy %s -> %s (top-level files, %s, .git and __pycache__ left out)" % (
            src, tree, "/, ".join(TOP_DIRS_LEFT_OUT) + "/"))
        copy_tree(src, tree)
        rows, changes, renames = apply_all(transforms, tree, out, a.prove)
        transformed = tree_hash(hash_tree(tree))
        run["transformed_tree_hash"] = transformed
        print("transformed tree hash %s" % transformed)
        if a.expect_transformed_hash and a.expect_transformed_hash.lower() != transformed:
            raise Stop("transformed tree hash %s is not the expected %s" % (transformed, a.expect_transformed_hash))
        if a.generate:
            run_generators(tree, record)
        if ledger_rows is not None:
            shipped = copy_assets(ledger_rows, stores, tree, a.ledger)
            run["ledger"] = {"path": os.path.abspath(a.ledger).replace(os.sep, "/"), "sha256": sha256_file(a.ledger),
                             "rows": len(ledger_rows), "shipped": len(shipped),
                             "held": sum(1 for r in ledger_rows if r["origin"] == "HELD"),
                             "not_copied": sum(1 for r in ledger_rows if r["origin"] not in NEVER_SHIPPED
                                               and r["copy"] != "yes"),
                             "stores": {k or "-": v.replace(os.sep, "/") for k, v in sorted(stores.items())}}
            print("assets: %d shipped files copied by ledger v2 (%d held in the store)%s" % (
                len(shipped), run["ledger"]["held"],
                "; %d shipped rows not copied (the tree, a package or a later transform provides them)" %
                run["ledger"]["not_copied"] if run["ledger"]["not_copied"] else ""))
        final = hash_tree(tree)
        th = tree_hash(final)
        run["tree_hash"] = th
        run["files"] = len(final)
        if a.expect_tree_hash and a.expect_tree_hash.lower() != th:
            raise Stop("tree hash %s is not the expected %s" % (th, a.expect_tree_hash))
        with open(os.path.join(record, "PIN.sha256"), "w", encoding="utf-8", newline="\n") as w:
            for rel in sorted(final, key=lambda s: s.encode("utf-8", "surrogateescape")):
                w.write("%s  %s\n" % (final[rel], rel))
        with open(os.path.join(record, "TREE-HASH"), "w", encoding="ascii", newline="\n") as w:
            w.write(th + "\n")
        run["status"] = "pinned"
        print("pinned: %d files, tree hash %s (PIN.sha256, TREE-HASH in %s)" % (len(final), th, record))
    except Stop as e:
        run["status"] = "failed"
        run["error"] = str(e)
        print("apply-transforms: FAILED: %s" % e, file=sys.stderr)
        rc = 1
    except Refuse as e:
        run["status"] = "refused"
        run["error"] = str(e)
        print("apply-transforms: REFUSED: %s" % e, file=sys.stderr)
        rc = 2
    finally:
        write_tsv(os.path.join(record, "RESULTS.tsv"),
                  ["num", "slot", "transform", "sha256", "first", "changed", "created", "deleted", "second", "empty",
                   "purpose"], rows)
        write_tsv(os.path.join(record, "CHANGES.tsv"), ["path", "num", "transform", "what"], changes)
        write_tsv(os.path.join(record, "TREE-RENAMES.tsv"), ["old", "new", "num"], renames)
        with open(os.path.join(record, "RUN.json"), "w", encoding="utf-8", newline="\n") as w:
            json.dump(run, w, indent=1, sort_keys=True)
            w.write("\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
