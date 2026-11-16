#!/usr/bin/env python3
"""openpgp_pub.py: read OpenPGP PUBLIC key files just far enough to name their keys.

Release operator tooling (src/release-tools). Imported by make-manifest.py and
export-release-key.py (both put this folder on sys.path, because python -I does not).
Python standard library only, 3.10+.

What it does
  read_key_file(path)      the key data of a binary (.gpg) or ASCII-armored (.asc) public key
                           file, as bytes; armor is checked (header, footer, CRC-24)
  dearmor(text)            the bytes inside one "PGP PUBLIC KEY BLOCK" armor
  packets(data)            the (tag, body) list of an OpenPGP packet stream (old and new headers)
  primary_fingerprints(d)  the fingerprints (40 upper-case hex digits, version 4 keys) of every
                           primary key in d, in order; refuses secret-key packets, unknown key
                           versions and anything that is not a public key block
  spaced(fpr)              the fingerprint as GnuPG prints it: ten groups of four, two spaces in
                           the middle
  It never reads a secret key: a secret-key packet (tag 5 or 7) is an error, never parsed.

Usage (as a command, for a quick look at a key file)
  python -I openpgp_pub.py KEYFILE      prints each primary key's fingerprint, both forms

Exit codes (as a command)
  0  every primary key printed
  1  usage error
  2  the file is missing, unreadable, or not an OpenPGP public key
"""

import base64
import hashlib
import re
import sys

FPR_RE = re.compile(r"^[0-9A-F]{40}$")
ALLOWED_TAGS = {2, 6, 12, 13, 14, 17}       # signature, public key, trust, user id, public subkey, user attribute
SECRET_TAGS = {5, 7}


class KeyError_(Exception):
    """A key file that is not a usable OpenPGP public key."""


def crc24(data):
    crc = 0xB704CE
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def dearmor(text):
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    try:
        start = lines.index("-----BEGIN PGP PUBLIC KEY BLOCK-----")
        end = lines.index("-----END PGP PUBLIC KEY BLOCK-----", start + 1)
    except ValueError:
        if any("PRIVATE KEY BLOCK" in ln for ln in lines):
            raise KeyError_("this is a SECRET key block; only public keys are read")
        raise KeyError_("no PGP PUBLIC KEY BLOCK armor found")
    body = lines[start + 1:end]
    # Armor headers ("Comment: ...") end at the first empty line, when there are any.
    if "" in body and all(":" in ln for ln in body[:body.index("")]):
        body = body[body.index("") + 1:]
    body = [ln for ln in body if ln]
    crc = None
    if body and body[-1].startswith("=") and len(body[-1]) == 5:
        crc = body.pop()[1:]
    try:
        data = base64.b64decode("".join(body), validate=True)
    except ValueError:
        raise KeyError_("the armor's base64 is invalid")
    if crc is not None:
        try:
            want = int.from_bytes(base64.b64decode(crc, validate=True), "big")
        except ValueError:
            raise KeyError_("the armor's checksum line is invalid")
        if crc24(data) != want:
            raise KeyError_("the armor's CRC-24 does not match its data")
    return data


def packets(data):
    out = []
    i = 0
    n = len(data)
    while i < n:
        h = data[i]
        if not h & 0x80:
            raise KeyError_("byte %d is not an OpenPGP packet header" % i)
        if h & 0x40:                                  # new format
            tag = h & 0x3F
            i += 1
            if i >= n:
                raise KeyError_("truncated packet header")
            o = data[i]
            if o < 192:
                ln, i = o, i + 1
            elif o < 224:
                if i + 1 >= n:
                    raise KeyError_("truncated packet header")
                ln, i = ((o - 192) << 8) + data[i + 1] + 192, i + 2
            elif o == 255:
                if i + 4 >= n:
                    raise KeyError_("truncated packet header")
                ln, i = int.from_bytes(data[i + 1:i + 5], "big"), i + 5
            else:
                raise KeyError_("partial body lengths do not occur in key blocks")
        else:                                         # old format
            tag = (h >> 2) & 0x0F
            lt = h & 0x03
            i += 1
            if lt == 3:
                raise KeyError_("indeterminate packet lengths do not occur in key blocks")
            size = (1, 2, 4)[lt]
            if i + size > n:
                raise KeyError_("truncated packet header")
            ln, i = int.from_bytes(data[i:i + size], "big"), i + size
        if i + ln > n:
            raise KeyError_("truncated packet body (tag %d)" % tag)
        out.append((tag, data[i:i + ln]))
        i += ln
    return out


def v4_fingerprint(body):
    if not body or body[0] != 4:
        raise KeyError_("key version %s is not supported (version 4 keys only)" % (body[0] if body else "?"))
    return hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()


def primary_fingerprints(data):
    pk = packets(data)
    if not pk:
        raise KeyError_("no packets")
    tags = {t for t, _ in pk}
    if tags & SECRET_TAGS:
        raise KeyError_("the data holds SECRET key material; only public keys are read")
    bad = sorted(tags - ALLOWED_TAGS)
    if bad:
        raise KeyError_("unexpected packet tag(s) %s in a public key block" % bad)
    if pk[0][0] != 6:
        raise KeyError_("a public key block starts with a public key packet")
    return [v4_fingerprint(b) for t, b in pk if t == 6]


def read_key_file(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise KeyError_("cannot read %s (%s)" % (path, e.__class__.__name__))
    if not raw:
        raise KeyError_("empty key file: %s" % path)
    if raw[0] & 0x80:
        return raw
    try:
        return dearmor(raw.decode("ascii"))
    except UnicodeDecodeError:
        raise KeyError_("neither a binary key nor ASCII armor: %s" % path)


def spaced(fpr):
    g = [fpr[i:i + 4] for i in range(0, 40, 4)]
    return " ".join(g[:5]) + "  " + " ".join(g[5:])


def main(argv):
    if len(argv) != 1:
        print("usage: python -I openpgp_pub.py KEYFILE", file=sys.stderr)
        return 1
    try:
        for fpr in primary_fingerprints(read_key_file(argv[0])):
            print(fpr)
            print(spaced(fpr))
    except KeyError_ as e:
        print("openpgp_pub: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
