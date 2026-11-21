#!/usr/bin/env python3
# build CLI help, fixed runtime checks and failure messages below.
"""Install and audit alpha.2 runtime additions without relaxing baseline gates."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys


def plain_files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("missing or linked source directory: " + str(root))
    result = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError("linked runtime source: " + str(p))
        if p.is_file():
            result.append(p)
    return result


def diagnostics(tree):
    root = Path(tree) / "diagnostics"
    files = []
    entry = root / "deep32_doctor.py"
    if entry.is_symlink() or not entry.is_file():
        raise ValueError("missing diagnostics/deep32_doctor.py in merged tree")
    files.append(entry)
    package = plain_files(root / "deep32_diag")
    schemas = plain_files(root / "schemas")
    files += [p for p in package if p.suffix == ".py" and
              not any(part in ("tests", "__pycache__") for part in p.relative_to(root).parts)]
    files += [p for p in schemas if p.suffix == ".json" and p.parent == root / "schemas"]
    required = ("deep32_diag/__init__.py", "deep32_diag/__main__.py", "deep32_diag/cli.py",
                "deep32_diag/doctor.py", "schemas/doctor-output.schema.json")
    for rel in required:
        if not (root / rel).is_file():
            raise ValueError("diagnostics runtime missing: " + rel)
    selftest = ("deep32_diag/selftest.py", "deep32_diag/selftest_cli.py",
                "deep32_diag/selftest_contract.py", "deep32_diag/selftest_trust.py",
                "schemas/selftest-questions.schema.json", "schemas/selftest-reference.schema.json",
                "schemas/selftest-result.schema.json")
    if any((root / rel).exists() for rel in selftest) and not all((root / rel).is_file() for rel in selftest):
        raise ValueError("incomplete diagnostics selftest runtime")
    return [(p, "/usr/local/d32/diagnostics/" + p.relative_to(root).as_posix(), "d32-node")
            for p in sorted(files)]


def site_files(tree):
    root = Path(tree) / "distrib/site"
    package = root / "usr/local/d32/deep32net"
    files = [p for p in plain_files(package) if p.suffix == ".py"]
    if not (package / "__init__.py").is_file():
        raise ValueError("missing deep32net package")
    names = ["usr/local/bin/deep32", "usr/local/d32/publication.py",
             "usr/local/d32/sbin/d32-publish", "usr/local/d32/libexec/d32-forge-enrol",
             "usr/local/d32/libexec/d32-gateway-enrol", "usr/local/d32/libexec/deep32-pair",
             "usr/local/d32/libexec/pairing-helper"]
    for name in names:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise ValueError("missing alpha.2 site source: " + name)
        files.append(p)
    return [(p, "/" + p.relative_to(root).as_posix(), "-") for p in sorted(files)]


def safe_destination(root, path):
    root = Path(root).absolute()
    result = root / path.lstrip("/")
    for p in (result, *result.parents):
        if p == root.parent:
            break
        if p.is_symlink():
            raise ValueError("linked destination: " + str(p))
    if result.exists() and not result.is_file():
        raise ValueError("destination is not a file: " + str(result))
    return result


def install(tree, stage):
    stage = Path(stage).absolute()
    entries = diagnostics(tree)
    # Preflight every destination before writing any file.
    targets = [(src, safe_destination(Path(stage) / pack, path)) for src, path, pack in entries]
    for src, dst in targets:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o444)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(dst, 0, 0)
        p = dst.parent
        while p != Path(stage) / "d32-node":
            os.chmod(p, 0o755)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(p, 0, 0)
            p = p.parent
    return len(entries)


def audit_rows(tree, stage, site_src, assets=None):
    entries = diagnostics(tree) + site_files(tree)
    asset_paths = set()
    if assets:
        for line in Path(assets).read_text(encoding="utf-8").splitlines():
            if line:
                digest, sep, path = line.partition("  ")
                if not sep or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("malformed asset hash row")
                asset_paths.add("/" + path)
    rows = []
    for src, path, pack in entries:
        if path in asset_paths:
            raise ValueError("asset would overwrite alpha.2 runtime: " + path)
        # Site sources are installed by mksite-ship, never duplicate them in packs.
        if pack == "-":
            for name in ("d32-node", "d32-apps"):
                if (Path(stage) / name / path.lstrip("/")).exists():
                    raise ValueError("site runtime also in pack: " + path)
            for extra in ("ship/site",):
                if (Path(site_src) / extra / path.lstrip("/")).exists():
                    raise ValueError("ship site shadows alpha.2 runtime: " + path)
            dst = safe_destination(Path(site_src) / "distrib/site", path)
        else:
            dst = safe_destination(Path(stage) / pack, path)
        data = src.read_bytes()
        if not dst.is_file() or dst.read_bytes() != data:
            raise ValueError("alpha.2 runtime missing or changed after staging: " + path)
        rows.append((path, pack, "ALPHA2-RUNTIME", "alpha2-verified",
                     src.relative_to(Path(tree)).as_posix() + " sha256=" + hashlib.sha256(data).hexdigest()))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("install", "check"))
    ap.add_argument("--tree", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site-src")
    ap.add_argument("--assets")
    a = ap.parse_args(argv)
    try:
        if a.action == "install":
            count = install(a.tree, a.stage)
        else:
            if not a.site_src:
                ap.error("check requires --site-src")
            count = len(audit_rows(a.tree, a.stage, a.site_src, a.assets))
        print("alpha2 runtime files: %d" % count)
        return 0
    except (OSError, ValueError) as exc:
        print("alpha2 runtime: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
