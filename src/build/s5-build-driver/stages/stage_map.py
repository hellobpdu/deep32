#!/usr/bin/env python3
"""stage_map.py: stage 65 on deep32-builder: every program row disc 1 should carry, looked for in the staged packs.

What
  ship/tools/stage.sh stages the packs as the First light PREVIEW had them; updates 1 to 15 then added and changed
  files with their own set tooling, which disc 1 does not replay. This reads SOURCE-MAP.tsv (how each golden
  program row is made), CUTS.tsv and ADDITIONS.tsv (what disc 1 drops and adds) and BASELINE-MODES.tsv (modes), and for
  every expected row of the packs d32-node and d32-apps:
    staged             already in the staged pack tree (stages 20 to 60 made it)
    made-later         a pack inventory row (R-PACKINV) stage 70 makes after this stage
    replaced-from-tree a copy-recipe row that stage.sh had staged as the First light PREVIEW's file, but whose tree
                       source (the row's SOURCE-MAP source, as copied-from-tree takes it) differs: a file a later
                       update changed, so the tree's copy replaces the staged one (lab 2026-11-15: /d32/a/bin/e26 was
                       the PREVIEW launcher, not u12's); the report names the staged file's old sha256
    made-folder       a golden folder row, made with its golden mode
    made-link          a golden link row, made with its golden target
    copied-from-tree   a copy-recipe row (R-COPY and the other "copied" recipes) taken from the pinned tree, through
                       the tree renames of the transforms (TREE-RENAMES.tsv), with its golden mode; a row whose
                       status is equal-eol is written in the one line-ending form (as is, LF or CRLF) whose sha256 is
                       the golden hash (the laptop keeps a deliberate mix: CONSOLIDATION.md 8), or is MISSING
    copied-from-build  a built row taken from stage 55's output, the recorded producer path below or the one
                       file there with the same name
    site-set           a row with no pack delivered at install by the site set (checked after install by golden-compare)
    site-overlay       (--site-src) a row a later update delivered outside any pack, that the site set also carries in
                       an older form (the BASE lane's): the site set's copy becomes the row's final tree version and
                       d32-apps does not carry the row (hook 68 would replace what hooks 30 to 66 made of it)
    asset-store        a row the asset store holds (--assets, the tree's assets/SHA256SUMS): stage 68 lays it in
    MISSING            none of these
  Rows a later update delivered without a pack go into d32-apps (as stage.sh already puts /etc files there).
  Folders and files are made with owner root:wheel. Nothing outside --stage is written.
Usage
  python3.14 -I stage_map.py --tree DIR --stage DIR --builds DIR --lists DIR --report FILE [--allow-missing]
      [--assets SHA256SUMS] [--site-src DIR]
  --lists holds SOURCE-MAP.tsv, CUTS.tsv, ADDITIONS.tsv, BASELINE-MODES.tsv and TREE-RENAMES.tsv
Exit
  0 nothing missing (or --allow-missing); 1 rows missing; 2 bad usage or inputs.
"""
import argparse
import collections
import hashlib
import os
import shutil
import sys

COPY_RECIPES = {"R-COPY", "R-SCREENS", "R-ARTICLE", "R-REGDATA", "R-GEO", "R-DTCALC", "R-ASKWAV", "D-25FSTAB",
                "D-68APPS"}
PACKS = ("d32-node", "d32-apps")
# stage 55 compiles in writable source copies at the top of its output: never build outputs
BUILD_SOURCE_COPIES = ("u10", "ship", "u14src")
# Exact stage 55 producer paths disambiguate shared data names and the u10/u14 native builds.
BUILD_OUTPUTS = {
    # SOURCE-MAP's R-U14APPS row selects u14's stripped calculator payload.
    ("R-U14APPS", "/d32/a/bin/d32calc"): "u14apps/payload/d32/a/bin/d32calc",
    # The capture dialogue's R-U14APPS row also requires u14's stripped payload binary.
    ("R-U14APPS", "/d32/a/bin/d32capdlg"): "u14apps/payload/d32/a/bin/d32capdlg",
    # SOURCE-MAP's R-NATIVE row selects build-native.sh's output; u14apps/tools holds a test copy.
    ("R-NATIVE", "/d32/a/bin/d32ouiq"): "native/d32ouiq",
    # regdata/build.py publish() writes the geo generation's MANIFEST below its --out folder.
    ("R-GEO", "/usr/local/d32/share/data/geo/20261101-e5c8459d638a272e/MANIFEST"):
        "geo/20261101-e5c8459d638a272e/MANIFEST",
    # The same publish() call writes the generation's registry image beside its MANIFEST.
    ("R-GEO", "/usr/local/d32/share/data/geo/20261101-e5c8459d638a272e/registry.d32r"):
        "geo/20261101-e5c8459d638a272e/registry.d32r",
    # u14 apps/build.sh places the registry selector in its installed-path payload tree.
    ("R-OUIDATA", "/usr/local/d32/share/data/oui/CURRENT"):
        "u14apps/payload/usr/local/d32/share/data/oui/CURRENT",
    # build-system-registry.sh creates this generation's MANIFEST in the same payload tree.
    ("R-OUIDATA", "/usr/local/d32/share/data/oui/openbsd-mac-vendor-20260913/MANIFEST"):
        "u14apps/payload/usr/local/d32/share/data/oui/openbsd-mac-vendor-20260913/MANIFEST",
    # The same registry build writes the image beside that generation's MANIFEST.
    ("R-OUIDATA", "/usr/local/d32/share/data/oui/openbsd-mac-vendor-20260913/oui.d32i"):
        "u14apps/payload/usr/local/d32/share/data/oui/openbsd-mac-vendor-20260913/oui.d32i",
}
# rows the site set makes at install (mksite-ship.sh, the site.d hooks): no file to stage
INSTALL_MADE = ("R-RELEASE", "R-FSTAB", "R-SETUPCONF")
# rows stage 70 makes after this stage (the packs' own inventory)
LATER_STAGE = ("R-PACKINV",)
# generated rows taken from the generator's output in the tree: recipe -> (installed prefix, tree folder)
GENERATED_IN_TREE = {"R-SOUNDS": ("/usr/local/share/d32/sounds/", "sound/out/")}


def read_tsv(path, need):
    rows, header = [], None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            c = line.split("\t")
            if header is None:
                header = c
                missing = [n for n in need if n not in header]
                if missing:
                    raise SystemExit("stage_map: %s has no column %s" % (path, missing))
                continue
            rows.append(dict(zip(header, c)))
    return rows


def pack_by_prefix(p):
    if p.startswith("/usr/local/d32/"):
        return "d32-node"
    return "d32-apps"


def chown_root(p):
    if hasattr(os, "lchown") and os.geteuid() == 0:
        os.lchown(p, 0, 0)



def laptop_look_removals(tree, stage, packs):
    """Preflight exact removals before deleting stage60 leftovers or mapping additions."""
    import json
    policy = os.path.join(tree, "theme", "laptop-look", "removed-paths.json")
    if not os.path.isfile(policy):
        return {}
    with open(policy, encoding="ascii") as f:
        data = json.load(f)
    removed = {}
    prepared = []
    for row in data:
        path = row["installed_path"]
        digest = row["before_sha256"]
        if (not path.startswith("/") or path == "/" or path.endswith("/") or "//" in path or "\\" in path or
                any(part in (".", "..") for part in path.split("/")) or path in removed or
                (digest != "absent" and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)))):
            raise ValueError("bad laptop-look removal metadata")
        removed[path] = row
        for pack in sorted(packs):
            target = os.path.join(stage, pack, path.lstrip("/"))
            ancestor = os.path.dirname(target)
            while ancestor != os.path.abspath(stage):
                if os.path.islink(ancestor):
                    raise ValueError("linked removal ancestor: " + path)
                parent = os.path.dirname(ancestor)
                if parent == ancestor:
                    raise ValueError("removal leaves stage")
                ancestor = parent
            if not os.path.lexists(target):
                continue
            if os.path.islink(target) or not os.path.isfile(target):
                raise ValueError("removal target is not a plain file: " + path)
            with open(target, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            if actual != digest:
                raise ValueError("laptop-look removal target drift: " + path)
            prepared.append(target)
    for target in prepared:
        os.remove(target)
    return removed

def main(argv):
    ap = argparse.ArgumentParser()
    for k in ("--tree", "--stage", "--lists", "--report"):
        ap.add_argument(k, required=True)
    # repeatable: stage 55's output, and stage 50's Library objects (e26-lib, e26-render, d32library-ask: stage.sh
    # installs only d32library, lab 2026-11-15); the writable source copies in BUILD_SOURCE_COPIES are skipped
    ap.add_argument("--builds", action="append", required=True)
    ap.add_argument("--allow-missing", action="store_true")
    # CLI help for the additive runtime gate.
    ap.add_argument("--alpha2", action="store_true", help="verify alpha.2 runtime additions")
    ap.add_argument("--assets", help="the tree's assets/SHA256SUMS: rows stage 68 lays in from the asset store")
    ap.add_argument("--site-src", help="the lane's writable source copy (stage.sh's S): rows delivered outside a pack "
                                       "that the site set carries get their final version in its ship/site or distrib/site")
    a = ap.parse_args(argv)
    L = a.lists
    smap = read_tsv(os.path.join(L, "SOURCE-MAP.tsv"), ("golden_path", "delivered_by", "pack", "type",
                                                       "golden_sha256", "tree_path_or_inputs", "recipe", "status"))
    cuts = {r["golden_path"]: r["what"] for r in read_tsv(os.path.join(L, "CUTS.tsv"), ("golden_path", "what"))}
    adds = read_tsv(os.path.join(L, "ADDITIONS.tsv"), ("path", "what"))
    golden = {r["path"]: r for r in read_tsv(os.path.join(L, "BASELINE-MODES.tsv"), ("path", "mode", "sha256", "type"))}
    renames = {r["old"]: r["new"] for r in read_tsv(os.path.join(L, "TREE-RENAMES.tsv"), ("old", "new"))}
    by_path = {r["golden_path"]: r for r in smap}
    assets = set()
    if a.assets:
        with open(a.assets, encoding="utf-8") as f:
            assets = set("/" + line.rstrip("\r\n").split("  ", 1)[1] for line in f if "  " in line)
    built = collections.defaultdict(list)
    for broot in a.builds:
        for dp, dn, fn in os.walk(broot):
            if os.path.normpath(dp) == os.path.normpath(broot):
                dn[:] = [d for d in dn if d not in BUILD_SOURCE_COPIES]
            for n in fn:
                built[n].append(os.path.join(dp, n))

    rows = []

    def tree_source(r):
        src = r["tree_path_or_inputs"].strip()
        if not src or src == "-" or ";" in src or " " in src:
            return None
        src = renames.get(src, src)
        p = os.path.join(a.tree, *src.split("/"))
        return p if os.path.isfile(p) else None

    def source_bytes(src, r):
        # a copy-recipe row's bytes as disc 1 carries them: as in the tree, or for an equal-eol row the one
        # line-ending form whose sha256 is the golden hash (None when no form has it)
        with open(src, "rb") as f:
            data = f.read()
        if r["status"] != "equal-eol":
            return data
        lf = data.replace(b"\r\n", b"\n")
        for b in (data, lf, lf.replace(b"\n", b"\r\n")):
            if hashlib.sha256(b).hexdigest() == r["golden_sha256"]:
                return b
        return None

    def place(path, pack, r, mode_from):
        dst = os.path.join(a.stage, pack, path.lstrip("/"))
        g = golden.get(mode_from) or {}
        gtype = (r or {}).get("type") or g.get("type") or "f"
        mode = int(g["mode"], 8) if g.get("mode", "-") not in ("-", "") else 0o444
        if os.path.lexists(dst):
            src = None
            if r and r["recipe"] in COPY_RECIPES and os.path.isfile(dst) and not os.path.islink(dst):
                src = tree_source(r)
            if src is None:
                return "staged", ""
            want = source_bytes(src, r)
            if want is None:
                return "staged", "the tree source has no line-ending form with the golden hash: staged file kept"
            with open(dst, "rb") as f:
                have = f.read()
            if have == want:
                return "staged", ""
            with open(dst, "wb") as f:
                f.write(want)
            os.chmod(dst, mode)
            chown_root(dst)
            return "replaced-from-tree", "%s (staged %s)" % (
                os.path.relpath(src, a.tree).replace(os.sep, "/"), hashlib.sha256(have).hexdigest()[:16])
        if path in assets:
            return "asset-store", "laid in by stage 68"
        if gtype == "d":
            os.makedirs(dst, exist_ok=True)
            os.chmod(dst, mode)
            chown_root(dst)
            return "made-folder", ""
        if gtype == "l":
            tgt = (g.get("sha256") or "")
            if tgt.startswith("->"):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.symlink(tgt[2:], dst)
                chown_root(dst)
                return "made-link", tgt
            return "MISSING", "link target unknown"
        src = None
        how = ""
        if r and r["recipe"] in LATER_STAGE:
            return "made-later", "stage 70 makes it"
        if r and r["recipe"] in GENERATED_IN_TREE:
            pre, folder = GENERATED_IN_TREE[r["recipe"]]
            if path.startswith(pre):
                cand = os.path.join(a.tree, *(folder + path[len(pre):]).split("/"))
                if os.path.isfile(cand):
                    src, how = cand, "copied-from-tree"
        if src is None and r and r["recipe"] in COPY_RECIPES:
            src = tree_source(r)
            how = "copied-from-tree"
        if src is None and r and (r["recipe"], path) in BUILD_OUTPUTS:
            rel = BUILD_OUTPUTS[r["recipe"], path]
            cands = [os.path.join(b, *rel.split("/")) for b in a.builds]
            cands = [p for p in cands if os.path.isfile(p)]
            if len(cands) != 1:
                return "MISSING", "%d build outputs at %s" % (len(cands), rel)
            src, how = cands[0], "copied-from-build"
        if src is None and r and r["status"] in ("built", "generated"):
            cands = built.get(os.path.basename(path), [])
            if len(cands) == 1:
                src, how = cands[0], "copied-from-build"
            elif len(cands) > 1:
                return "MISSING", "%d build outputs named %s" % (len(cands), os.path.basename(path))
        if src is None:
            return "MISSING", "recipe %s" % (r["recipe"] if r else "-")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if r and r["status"] == "equal-eol" and how == "copied-from-tree":
            data = source_bytes(src, r)
            if data is None:
                return "MISSING", "no line-ending form of %s has the golden hash" % r["tree_path_or_inputs"]
            with open(dst, "wb") as f:
                f.write(data)
        else:
            shutil.copyfile(src, dst)
        os.chmod(dst, mode)
        chown_root(dst)
        if how == "copied-from-tree":
            base = a.tree
        else:
            base = next((b for b in a.builds if os.path.abspath(src).startswith(os.path.abspath(b) + os.sep)), a.builds[0])
        return how, os.path.relpath(src, base).replace(os.sep, "/")

    def site_place(path, r):
        # a row u15 delivered outside any pack that the site set also carries (ship/site wins over distrib/site, as
        # mksite-ship.sh copies them): the site set gets the row's final tree version and d32-apps does not carry it,
        # so hook 68's tar over / cannot undo what the earlier hooks did (lab 2026-11-15: hook 30 rendered
        # @D32_USER@ in /etc/doas.conf, then d32-apps' unrendered copy replaced it; and the medium's site set kept
        # the BASE lane's doas.conf and pf.conf with his user and host names). None: the site set does not carry it.
        sp = root = None
        for root in ("ship/site", "distrib/site"):
            cand = os.path.join(a.site_src, *root.split("/"), *path.lstrip("/").split("/"))
            if os.path.isfile(cand) and not os.path.islink(cand):
                sp = cand
                break
        if sp is None:
            return None
        removed = ""
        dst = os.path.join(a.stage, "d32-apps", path.lstrip("/"))
        if os.path.lexists(dst):
            os.remove(dst)
            removed = "; removed from d32-apps"
        src = tree_source(r) if r["recipe"] in COPY_RECIPES else None
        if src is None:
            return "site-set", root + removed
        want = source_bytes(src, r)
        if want is None:
            return "MISSING", "no line-ending form of %s has the golden hash" % r["tree_path_or_inputs"]
        with open(sp, "rb") as f:
            have = f.read()
        if have == want:
            return "site-set", root + removed
        with open(sp, "wb") as f:
            f.write(want)
        return "site-overlay", "%s -> %s (site had %s)%s" % (
            os.path.relpath(src, a.tree).replace(os.sep, "/"), root, hashlib.sha256(have).hexdigest()[:16], removed)

    removed = laptop_look_removals(os.path.abspath(a.tree), os.path.abspath(a.stage), PACKS)
    for r in smap:
        p = r["golden_path"]
        if p in removed:
            rows.append((p, r["pack"], r["recipe"], "laptop-look-removed", "round3 M5 measured removal"))
            continue
        if p in cuts:
            continue
        pack = r["pack"]
        if pack == "-":
            if r["delivered_by"] == "base":
                rows.append((p, "-", r["recipe"], "site-set", ""))
                continue
            got = site_place(p, r) if a.site_src else None
            if got is not None:
                rows.append((p, "-", r["recipe"], got[0], got[1]))
                continue
            if r["recipe"] in INSTALL_MADE:
                rows.append((p, "-", r["recipe"], "site-set", "made at install by the site set"))
                continue
            pack = "d32-apps"
        if pack not in PACKS:
            rows.append((p, pack, r["recipe"], "MISSING", "unknown pack"))
            continue
        st, src = place(p, pack, r, p)
        rows.append((p, pack, r["recipe"], st, src))
    for ad in adds:
        p = ad["path"]
        if p in removed:
            rows.append((p, "-", "-", "laptop-look-removed", "round3 M5 superseded addition"))
            continue
        old = ad["what"][len("renamed-from "):].strip() if ad["what"].startswith("renamed-from ") else None
        r = by_path.get(old) if old else None
        if p.startswith("/etc/") and not r:
            rows.append((p, "-", "-", "site-set", ad["what"]))
            continue
        # a file a transform added to the site set (901, 902: distrib/site/usr/local/d32/...): mksite-ship.sh ships it
        if not r and a.site_src and any(os.path.isfile(os.path.join(a.site_src, *root.split("/"), *p.lstrip("/").split("/")))
                                        for root in ("ship/site", "distrib/site")):
            rows.append((p, "-", "-", "site-set", "added to the site set: " + ad["what"]))
            continue
        pack = (r["pack"] if r and r["pack"] in PACKS else pack_by_prefix(p))
        st, src = place(p, pack, r, old or p)
        rows.append((p, pack, r["recipe"] if r else "-", st, src or ad["what"]))

    if a.alpha2:
        if not a.site_src:
            ap.error("--alpha2 requires --site-src")
        import runpy
        helper = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alpha2_stage.py"))
        rows.extend(helper["audit_rows"](a.tree, a.stage, a.site_src, a.assets))

    counts = collections.Counter(x[3] for x in rows)
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8", newline="\n") as w:
        w.write("path\tpack\trecipe\tstatus\tsource\n")
        for x in sorted(rows):
            w.write("\t".join(x) + "\n")
    print("stage_map: " + ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    for x in sorted(rows):
        if x[3] == "MISSING":
            print("  MISSING %s (%s, %s) %s" % (x[0], x[1], x[2], x[4]))
    return 1 if counts.get("MISSING") and not a.allow_missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
