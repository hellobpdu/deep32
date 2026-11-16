#!/usr/bin/env python3
"""export-release-key.py: put the PUBLIC half of the release key into install.

Release operator tooling (src/release-tools). Python standard library only.

What it does
  1. Reads the release key's fingerprint from release-authorship.json
     (release_key_gpg_fingerprint), the only place it is recorded.
  2. Asks GnuPG for the key's public listing and its public key, with exactly these
     commands and no others (the secret half is never asked for):
       gpg --batch --list-keys --with-colons FPR
       gpg --batch --export FPR
       gpg --batch --armor --export FPR
  3. Refuses unless: the listing's primary fingerprint is FPR and the key is not revoked,
     expired or disabled; the binary export holds public packets only and exactly one
     primary key whose computed fingerprint is FPR; the armored export holds the same bytes.
  4. Writes, into the output folder (default install):
       release-key.gpg          the binary key: the keyring gpgv reads (--keyring)
       release-key.asc          the same key, ASCII-armored, LF line ends
       release-key.fingerprint  line 1 the 40 hex digits, line 2 the spaced form
     and prints each file's SHA-256. Run it again after the key's expiry is extended
     (SIGNING-STEPS.md), so the shipped key carries the new date.

Usage
  python -I export-release-key.py [--check-only] [--out-dir DIR]
      [--gpg PATH]        default C:/Program Files/GnuPG/bin/gpg.exe (Gpg4win, which holds his key)
      [--homedir DIR]     tests only: a throwaway GnuPG home; never given for the real key
      [--expected FPR]    tests only: the fingerprint to expect instead of authorship.json's
      --check-only        export and check, then compare with the files in the folder; writes nothing

Exit codes
  0  the three files written (with --check-only: the files in place are identical)
  1  usage error
  2  GnuPG missing or failed, or the key is not in its keyring
  3  the key is not the right one, or not usable (fingerprint, secret material, revoked,
     expired, disabled, armored and binary exports differ)
  4  a file could not be written
  5  --check-only: the files in place differ from a fresh export (or are missing)
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import openpgp_pub  # noqa: E402  (beside this file)

DIST = os.path.dirname(os.path.dirname(HERE))
DEFAULT_GPG = "C:/Program Files/GnuPG/bin/gpg.exe"
NAMES = ("release-key.gpg", "release-key.asc", "release-key.fingerprint")


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def gpg(cmd, homedir, *args):
    argv = [cmd, "--batch"] + (["--homedir", homedir] if homedir else []) + list(args)
    # The only operations allowed on his keyring: list and export the PUBLIC key.
    if any("secret" in a for a in argv) or argv[-2] not in ("--export", "--with-colons"):
        raise Fail(1, "internal: refusing an unexpected gpg command")
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except FileNotFoundError:
        raise Fail(2, "gpg not found: %s" % cmd)
    except subprocess.TimeoutExpired:
        raise Fail(2, "gpg did not answer within 60 s: %s" % " ".join(argv[1:]))
    if p.returncode != 0:
        raise Fail(2, "gpg %s failed (exit %d): %s" % (" ".join(argv[1:]), p.returncode,
                                                      p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout


def main(argv):
    ap = argparse.ArgumentParser(description="Export the release key's public half into install.")
    ap.add_argument("--gpg", default=DEFAULT_GPG)
    ap.add_argument("--homedir")
    ap.add_argument("--expected")
    ap.add_argument("--out-dir", default=os.path.join(DIST, "install"))
    ap.add_argument("--authorship", default=os.path.join(HERE, "release-authorship.json"))
    ap.add_argument("--check-only", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 1
    try:
        fpr = a.expected
        if not fpr:
            try:
                with open(a.authorship, "rb") as f:
                    fpr = json.loads(f.read().decode("utf-8-sig")).get("release_key_gpg_fingerprint")
            except (OSError, ValueError) as e:
                raise Fail(2, "authorship.json unreadable: %s" % e)
        if not isinstance(fpr, str) or not openpgp_pub.FPR_RE.match(fpr):
            raise Fail(1, "the expected fingerprint must be 40 upper-case hex digits, got %r" % fpr)

        listing = gpg(a.gpg, a.homedir, "--list-keys", "--with-colons", fpr).decode("utf-8", "replace")
        recs = [ln.split(":") for ln in listing.splitlines() if ln]
        pubs = [i for i, r in enumerate(recs) if r[0] == "pub"]
        if len(pubs) != 1:
            raise Fail(3, "the listing holds %d primary keys for %s; expected exactly one" % (len(pubs), fpr))
        nxt = [r for r in recs[pubs[0] + 1:] if r[0] in ("fpr", "sub", "uid", "pub")]
        if not nxt or nxt[0][0] != "fpr" or len(nxt[0]) < 10 or nxt[0][9] != fpr:
            raise Fail(3, "the listing's primary fingerprint is not %s" % fpr)
        pub = recs[pubs[0]]
        if pub[1] in ("r", "e", "d", "i"):
            raise Fail(3, "the key is %s in the keyring" % {"r": "revoked", "e": "expired", "d": "disabled",
                                                                "i": "invalid"}[pub[1]])
        expires = "never"
        if len(pub) > 6 and pub[6].isdigit():
            expires = datetime.datetime.fromtimestamp(int(pub[6]), datetime.timezone.utc).strftime("%Y-%m-%d")

        binary = gpg(a.gpg, a.homedir, "--export", fpr)
        armored = gpg(a.gpg, a.homedir, "--armor", "--export", fpr)
        try:
            got = openpgp_pub.primary_fingerprints(binary)
            unarmored = openpgp_pub.dearmor(armored.decode("ascii"))
        except (openpgp_pub.KeyError_, UnicodeDecodeError) as e:
            raise Fail(3, "the exported key is not usable: %s" % e)
        if got != [fpr]:
            raise Fail(3, "the export holds primary key(s) %s, not exactly %s; nothing written" % (got, fpr))
        if unarmored != binary:
            raise Fail(3, "the armored and binary exports differ; nothing written")

        asc = armored.decode("ascii").replace("\r\n", "\n").encode("ascii")
        files = {"release-key.gpg": binary, "release-key.asc": asc,
                 "release-key.fingerprint": ("%s\n%s\n" % (fpr, openpgp_pub.spaced(fpr))).encode("ascii")}
        print("key %s (%s), expires %s: public packets only, fingerprint checked"
              % (fpr, openpgp_pub.spaced(fpr), expires))

        if a.check_only:
            differ = []
            for n in NAMES:
                p = os.path.join(a.out_dir, n)
                try:
                    with open(p, "rb") as f:
                        same = f.read() == files[n]
                except OSError:
                    same = False
                print("  %-24s %s" % (n, "identical" if same else "DIFFERS or missing"))
                if not same:
                    differ.append(n)
            if differ:
                raise Fail(5, "the files in %s differ from a fresh export: %s" % (a.out_dir, ", ".join(differ)))
            print("RESULT: OK (the files in place are identical to a fresh export)")
            return 0

        try:
            os.makedirs(a.out_dir, exist_ok=True)
            for n in NAMES:
                p = os.path.join(a.out_dir, n)
                with open(p + ".tmp", "wb") as f:
                    f.write(files[n])
                os.replace(p + ".tmp", p)
                print("  wrote %s (sha256 %s)" % (p, hashlib.sha256(files[n]).hexdigest()))
        except OSError as e:
            raise Fail(4, "write failed: %s" % e)
        print("RESULT: OK")
        return 0
    except Fail as e:
        print("export-release-key: %s" % e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
