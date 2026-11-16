#!/usr/bin/env python3
"""vhd.py: the TEST medium between its two forms: a fixed VHD (Hyper-V; the hyperv adapter test in 930) and a raw image (Proxmox import).

What
  A fixed VHD is the raw disk followed by one 512-byte footer (cookie "conectix", disk type 2 = fixed, current
  size = the raw size). to-raw checks that footer and writes the raw disk without it; to-vhd appends the footer the
  tree's own release/tools/raw2vhd.py makes (its footer() and chs(), with the same fixed id rule as
  mkimage-ship.sh), so the build and the test lane agree byte for byte. Neither form is changed otherwise.
Usage
  python3 -I vhd.py to-raw IN.vhd OUT.raw
  python3 -I vhd.py to-vhd IN.raw OUT.vhd --raw2vhd <tree>/release/tools
Exit
  0 written; 1 not a fixed VHD, or sizes disagree; 2 bad usage or the output exists.
"""
import hashlib
import os
import struct
import sys

CHUNK = 1 << 20


def check_footer(foot, raw_size):
    if len(foot) != 512 or foot[:8] != b"conectix":
        return "no VHD footer (cookie)"
    disk_type = struct.unpack(">I", foot[60:64])[0]
    if disk_type != 2:
        return "not a fixed VHD (disk type %d)" % disk_type
    current = struct.unpack(">Q", foot[48:56])[0]
    if current != raw_size:
        return "footer says %d bytes, the file holds %d before it" % (current, raw_size)
    s = sum(foot[:64]) + sum(foot[68:])
    if (~s) & 0xFFFFFFFF != struct.unpack(">I", foot[64:68])[0]:
        return "footer checksum differs"
    return None


def copy_part(src, dst, n):
    with open(src, "rb") as i, open(dst, "xb") as o:
        left = n
        while left:
            b = i.read(min(CHUNK, left))
            if not b:
                raise OSError("short read")
            o.write(b)
            left -= len(b)


def main(argv):
    if len(argv) < 3 or argv[0] not in ("to-raw", "to-vhd"):
        print(__doc__, file=sys.stderr)
        return 2
    verb, src, dst = argv[:3]
    if os.path.exists(dst):
        print("vhd: %s exists" % dst, file=sys.stderr)
        return 2
    size = os.path.getsize(src)
    if verb == "to-raw":
        with open(src, "rb") as f:
            f.seek(size - 512)
            foot = f.read(512)
        why = check_footer(foot, size - 512)
        if why:
            print("vhd: %s: %s" % (src, why), file=sys.stderr)
            return 1
        copy_part(src, dst, size - 512)
        print("vhd: %s -> %s (%d bytes raw)" % (src, dst, size - 512))
        return 0
    if len(argv) != 5 or argv[3] != "--raw2vhd":
        print(__doc__, file=sys.stderr)
        return 2
    sys.path.insert(0, argv[4])
    import raw2vhd  # noqa: E402  (the tree's own module)
    if size % (1 << 20):
        print("vhd: %s is not a whole number of MiB" % src, file=sys.stderr)
        return 1
    copy_part(src, dst, size)
    with open(dst, "ab") as o:
        o.write(raw2vhd.footer(size, hashlib.sha256(b"deep32ws test medium %d" % size).digest()))
    print("vhd: %s -> %s (fixed VHD, %d MiB)" % (src, dst, size >> 20))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
