#!/usr/bin/env python3
"""raw2vhd.py <raw.img> <out.vhd>: copy a raw disk image into a FIXED VHD (the raw bytes plus the 512-byte footer of
Microsoft's VHD format, v1.0), which Hyper-V Generation 1 attaches as an IDE disk. Deterministic: the timestamp is a
fixed date and the unique id is derived from the image's SHA-256, so the same image always gives the same VHD.
Runs with any Python 3 (the build VM's python3.14, or the source workstation's python3.15). No third-party modules."""
import hashlib, shutil, struct, sys, uuid

def chs(total_sectors):
    # Appendix "CHS calculation" of the VHD specification
    if total_sectors > 65535 * 16 * 255:
        total_sectors = 65535 * 16 * 255
    if total_sectors >= 65535 * 16 * 63:
        spt, heads = 255, 16
        cth = total_sectors // spt
    else:
        spt = 17
        cth = total_sectors // spt
        heads = max((cth + 1023) // 1024, 4)
        if cth >= heads * 1024 or heads > 16:
            spt, heads = 31, 16
            cth = total_sectors // spt
        if cth >= heads * 1024:
            spt, heads = 63, 16
            cth = total_sectors // spt
    return cth // heads, heads, spt

def footer(size, digest):
    c, h, s = chs(size // 512)
    stamp = 847152000  # 2026-11-05 00:00:00 UTC, in seconds since 2000-01-01 00:00:00 UTC
    f = bytearray(512)
    struct.pack_into(">8sIIQI4sI4sQQHBBII16sB", f, 0,
                     b"conectix", 2, 0x00010000, 0xFFFFFFFFFFFFFFFF, stamp, b"d32 ", 0x00010000, b"Wi2k",
                     size, size, c, h, s, 2, 0, uuid.UUID(bytes=digest[:16]).bytes, 0)
    struct.pack_into(">I", f, 64, (~sum(f)) & 0xFFFFFFFF)
    return bytes(f)

def main():
    src, dst = sys.argv[1], sys.argv[2]
    h = hashlib.sha256()
    size = 0
    with open(src, "rb") as i:
        for blk in iter(lambda: i.read(1 << 20), b""):
            h.update(blk)
            size += len(blk)
    if size % (1 << 20):
        sys.exit("raw2vhd: image size %d is not a whole number of MiB" % size)
    shutil.copyfile(src, dst)
    with open(dst, "ab") as o:
        o.write(footer(size, h.digest()))
    print("raw2vhd: %s -> %s (%d MiB, fixed VHD, geometry %d/%d/%d)" % ((src, dst, size >> 20) + chs(size // 512)))

if __name__ == "__main__":
    main()
