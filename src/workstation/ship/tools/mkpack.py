"""mkpack.py: write one Deep32 pack (ship lane, First light PREVIEW) as an uncompressed ustar stream on stdout.

  python3.14 -I mkpack.py --name NAME --class CLASS --rights TEXT --release REL SRC=DEST [SRC=DEST ...] >pack.tar

Each SRC (a file or a directory, walked in sorted order) is stored under DEST (a path relative to /). Members are
files and symbolic links only (never directories, so unpacking at / never changes the mode of an existing system
directory), owner root:wheel (0:0), the source's mode without group/other write, mtime fixed. Every file is hashed
while it streams, and the pack's own MANIFEST.tsv and INFO are written LAST, under var/db/d32/packs/NAME/:
  MANIFEST.tsv  path  type(f|l)  mode  owner  group  bytes  sha256(or ->target)  rights
The install hooks check every file against it (sha256 -c). A summary line goes to stderr.
"""
import argparse
import hashlib
import io
import os
import stat
import sys
import tarfile
import time

MTIME = 1793750400  # 2026-11-05 00:00 UTC


class HashingReader:
    def __init__(self, f):
        self.f = f
        self.h = hashlib.sha256()

    def read(self, n=-1):
        b = self.f.read(n)
        self.h.update(b)
        return b


def walk(src):
    if os.path.isdir(src) and not os.path.islink(src):
        for dp, dns, fns in os.walk(src):
            dns.sort()
            for fn in sorted(fns):
                yield os.path.join(dp, fn)
            for dn in dns:
                p = os.path.join(dp, dn)
                if os.path.islink(p):
                    yield p
    else:
        yield src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--class", dest="klass", required=True)
    ap.add_argument("--rights", required=True)
    ap.add_argument("--release", required=True)
    ap.add_argument("maps", nargs="+")
    a = ap.parse_args()
    out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.USTAR_FORMAT)
    rows, nbytes, seen = [], 0, set()
    for m in a.maps:
        src, _, dest = m.partition("=")
        src = src.rstrip("/")
        dest = dest.strip("/")
        for p in walk(src):
            rel = os.path.relpath(p, src) if os.path.isdir(src) and not os.path.islink(src) else ""
            name = dest if not rel or rel == "." else dest + "/" + rel
            if name in seen:
                raise SystemExit("mkpack: duplicate member %s" % name)
            seen.add(name)
            st = os.lstat(p)
            ti = tarfile.TarInfo(name)
            ti.uid = ti.gid = 0
            ti.uname, ti.gname = "root", "wheel"
            ti.mtime = MTIME
            ti.mode = stat.S_IMODE(st.st_mode) & ~0o022
            if stat.S_ISLNK(st.st_mode):
                ti.type = tarfile.SYMTYPE
                ti.linkname = os.readlink(p)
                out.addfile(ti)
                rows.append((name, "l", "%04o" % ti.mode, "root", "wheel", "0", "->" + ti.linkname))
                continue
            if not stat.S_ISREG(st.st_mode):
                raise SystemExit("mkpack: not a file or link: %s" % p)
            ti.size = st.st_size
            with open(p, "rb") as f:
                hr = HashingReader(f)
                out.addfile(ti, hr)
            nbytes += st.st_size
            rows.append((name, "f", "%04o" % ti.mode, "root", "wheel", str(st.st_size), hr.h.hexdigest()))
    base = "var/db/d32/packs/%s" % a.name
    man = "# Deep32 pack %s (%s), release %s: path, type, mode, owner, group, bytes, sha256, rights\n" % (
        a.name, a.klass, a.release)
    man += "".join("\t".join(r + (a.rights,)) + "\n" for r in rows)
    info = ("name=%s\nclass=%s\nrelease=%s\nrights=%s\nfiles=%d\nbytes=%d\nmade=%s\n" % (
        a.name, a.klass, a.release, a.rights, len(rows), nbytes,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    for fn, data in (("MANIFEST.tsv", man), ("INFO", info)):
        b = data.encode()
        ti = tarfile.TarInfo(base + "/" + fn)
        ti.uid = ti.gid = 0
        ti.uname, ti.gname = "root", "wheel"
        ti.mtime = MTIME
        ti.mode = 0o444
        ti.size = len(b)
        out.addfile(ti, io.BytesIO(b))
    out.close()
    sys.stderr.write("mkpack: %s: %d members, %d bytes of files\n" % (a.name, len(rows), nbytes))


if __name__ == "__main__":
    main()
