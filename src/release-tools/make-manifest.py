#!/usr/bin/env python3
r"""make-manifest.py: split Deep32 disc images into release parts and write MANIFEST.json.

Release operator tooling (src/release-tools). It implements contract C2,
src/docs/contracts/C2-release-manifest-draft.md.

What it does
  1. Reads the bill of materials: which three disc versions go together.
  2. For each disc given as an image (--disc ROLE=PATH), copies it into numbered parts
     of at most --part-size bytes (default and maximum 1900 MiB, well under GitHub's
     2 GiB per-file limit), hashing every part and the whole disc in the same pass,
     then reads every part back from disk and checks its hash again.
  3. For each disc reused from an earlier release (--reuse ROLE=OLD_MANIFEST.json),
     copies that disc's entry (parts and hashes) from the earlier manifest. Verify the
     earlier release with verify-release first: this tool does not check signatures.
  4. Writes OUTDIR/MANIFEST.json: format and version, release version, tag and created
     time (passed in, so the same inputs always give the same bytes), the author block
     from release-authorship.json, the signer (the OpenPGP fingerprint of the release
     key, read from install/release-key.fingerprint and checked against the
     shipped key install/release-key.gpg), the bill of materials, and every disc
     with its parts in order.
  5. Prints the one command Jeffrey runs to sign it, which writes the detached,
     armored signature MANIFEST.json.asc beside it:
       "C:\Program Files\GnuPG\bin\gpg.exe" --local-user <FPR> --armor --detach-sign
           --output MANIFEST.json.asc MANIFEST.json
  It never signs, and never touches a GnuPG keyring. Jeffrey signs (SIGNING-STEPS.md).

Usage
  python -I make-manifest.py --release 1.0.0 --created 2026-11-14T00:00:00Z
      --bom BOM.json --out OUTDIR
      --disc system=PATH.iso --disc content=PATH.iso --disc gpu=PATH.iso
  Options
    --reuse ROLE=FILE         take the ROLE disc from an earlier manifest instead of an image
    --release-tag TAG         tag of the code release (default v<release>)
    --key-dir DIR             folder holding release-key.fingerprint (the pin) and
                              release-key.gpg (default: install of this workspace);
                              the key's computed fingerprint must equal the pin
    --introduce-key FILE      a successor release key (.gpg or .asc public key) that this
                              release, signed with the current key, names in "introduces"
    --part-size N             bytes per part; suffix KiB, MiB or GiB allowed; max 1900MiB
    --authorship FILE         default: release-authorship.json (the folder above this one)
    --allow-incomplete        allow fewer than three discs (development only; the manifest
                              then says "complete": false)
    --force                   write into a non-empty OUTDIR: same-named files are replaced and
                              earlier parts of the discs being split are removed first
  BOM.json is either {"system": "1.0.0", "content": "1.0.0", "gpu": "1.0.0"} or
  {"system": {"version": "1.0.0", "tag": "system-1.0.0"}, ...}. The tag defaults to
  ROLE-VERSION. A disc version is part of the file names, so it holds only letters,
  digits, ".", "_" and "-". JSON files may start with a UTF-8 byte-order mark.
  Every disc image is checked (exists, regular file, not empty, part count) before the
  first part is written; a refused run leaves no part and no .tmp file behind.

Exit codes
  0  parts and MANIFEST.json written
  1  usage error (bad or missing argument)
  2  input error (missing or unreadable file, bad bill of materials, bad key or pin, bad reused manifest)
  3  output error (OUTDIR not empty without --force, a write failed, a part read back with another hash)
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True     # no __pycache__ beside the release tooling
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)       # python -I leaves the script's folder off sys.path
import openpgp_pub  # noqa: E402  (beside this file)

FORMAT = "deep32-release-manifest"
FORMAT_VERSION = 1
SIG_NAME = "MANIFEST.json.asc"
SIGN_GPG = r"C:\Program Files\GnuPG\bin\gpg.exe"
ROLES = ("system", "content", "gpu")
MIB = 1024 * 1024
MAX_PART = 1900 * MIB
MAX_ASSETS_PER_RELEASE = 1000
CHUNK = 8 * MIB

VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")
# A disc version becomes part of file names (deep32-ROLE-VERSION.EXT.partNNN), which a
# verifier accepts only as [A-Za-z0-9._-]: no "+" there.
DISC_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
TAG_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,99}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
CREATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STALE_RE = re.compile(r"^deep32-(system|content|gpu)-.+\.part\d{3}(\.tmp)?$")


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def say(msg):
    print(msg, flush=True)


def parse_size(text):
    m = re.fullmatch(r"(\d+)\s*(KiB|MiB|GiB)?", text.strip())
    if not m:
        raise Fail(1, "--part-size must be a whole number of bytes, optionally with KiB, MiB or GiB")
    n = int(m.group(1)) * {None: 1, "KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB}[m.group(2)]
    if n < 1 or n > MAX_PART:
        raise Fail(1, "--part-size must be between 1 byte and 1900 MiB (%d bytes); got %d" % (MAX_PART, n))
    return n


def role_pairs(items, what):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise Fail(1, "%s takes ROLE=PATH, got %r" % (what, item))
        role, path = item.split("=", 1)
        if role not in ROLES:
            raise Fail(1, "%s: role must be one of %s, got %r" % (what, ", ".join(ROLES), role))
        if role in out:
            raise Fail(1, "%s: role %s given twice" % (what, role))
        out[role] = path
    return out


def load_json(path, what):
    try:
        with open(path, "rb") as f:
            # utf-8-sig: a file saved by Windows PowerShell 5.1 starts with a byte-order mark
            return json.loads(f.read().decode("utf-8-sig"))
    except FileNotFoundError:
        raise Fail(2, "%s not found: %s" % (what, path))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise Fail(2, "%s unreadable (%s): %s" % (what, e.__class__.__name__, path))


def read_pin(path):
    """release-key.fingerprint: line 1 the 40 hex digits, line 2 (optional) the spaced form."""
    try:
        with open(path, "r", encoding="ascii") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        raise Fail(2, "release key fingerprint file not found: %s" % path)
    except (OSError, UnicodeDecodeError):
        raise Fail(2, "release key fingerprint file unreadable: %s" % path)
    if not lines or not openpgp_pub.FPR_RE.match(lines[0]):
        raise Fail(2, "first line of %s must be the 40 upper-case hex digits of the fingerprint" % path)
    if len(lines) > 2 or (len(lines) == 2 and lines[1].replace(" ", "") != lines[0]):
        raise Fail(2, "%s: the second line must be the same fingerprint, spaced, and nothing more" % path)
    return lines[0]


def key_fingerprints(path, what):
    try:
        return openpgp_pub.primary_fingerprints(openpgp_pub.read_key_file(path))
    except openpgp_pub.KeyError_ as e:
        raise Fail(2, "%s is not a usable OpenPGP public key (%s): %s" % (what, e, path))


def read_bom(path, allow_incomplete):
    raw = load_json(path, "bill of materials")
    if not isinstance(raw, dict):
        raise Fail(2, "bill of materials must be a JSON object keyed by role")
    bom = {}
    for role, val in raw.items():
        if role not in ROLES:
            raise Fail(2, "bill of materials: unknown role %r" % role)
        if isinstance(val, str):
            val = {"version": val}
        if not isinstance(val, dict) or not isinstance(val.get("version"), str):
            raise Fail(2, "bill of materials: %s needs a version" % role)
        version = val["version"]
        if not DISC_VERSION_RE.match(version):
            raise Fail(2, "bill of materials: bad version %r for %s (letters, digits, '.', '_' and '-' only: "
                          "it is part of the file names)" % (version, role))
        tag = val.get("tag", "%s-%s" % (role, version))
        if not isinstance(tag, str) or not TAG_RE.match(tag):
            raise Fail(2, "bill of materials: bad tag %r for %s" % (tag, role))
        bom[role] = {"version": version, "tag": tag}
    missing = [r for r in ROLES if r not in bom]
    if missing and not allow_incomplete:
        raise Fail(2, "bill of materials must name all three discs; missing: %s" % ", ".join(missing))
    if not bom:
        raise Fail(2, "bill of materials is empty")
    return bom


def plan_disc(role, image, version, part_size):
    """Every check that can refuse a disc image, done for all discs before any part is
    written, so a refusal never leaves parts behind."""
    if not os.path.exists(image):
        raise Fail(2, "%s image not found: %s" % (role, image))
    if not os.path.isfile(image):
        raise Fail(2, "%s image is not a regular file: %s" % (role, image))
    try:
        total = os.path.getsize(image)
    except OSError:
        raise Fail(2, "%s image unreadable: %s" % (role, image))
    if total == 0:
        raise Fail(2, "%s image is empty: %s" % (role, image))
    ext = os.path.splitext(image)[1].lower() or ".img"
    if not EXT_RE.match(ext):
        raise Fail(2, "%s image has an unusable extension %r" % (role, ext))
    fname = "deep32-%s-%s%s" % (role, version, ext)
    if not NAME_RE.match(fname + ".part001"):
        raise Fail(2, "%s: file name %r would not be safe" % (role, fname))
    nparts = (total + part_size - 1) // part_size
    if nparts > 999:
        raise Fail(2, "%s would need %d parts; the limit is 999 (raise --part-size)" % (role, nparts))
    return total, fname, nparts


def write_parts(role, image, version, tag, part_size, outdir, force):
    total, fname, nparts = plan_disc(role, image, version, part_size)
    whole = hashlib.sha256()
    parts = []
    say("split %s: %s (%d bytes) into %d part(s) of at most %d bytes" % (role, image, total, nparts, part_size))
    try:
        src = open(image, "rb")
    except OSError:
        raise Fail(2, "%s image unreadable: %s" % (role, image))
    with src:
        for idx in range(1, nparts + 1):
            name = "%s.part%03d" % (fname, idx)
            final = os.path.join(outdir, name)
            if os.path.exists(final) and not force:
                raise Fail(3, "refusing to overwrite %s (use --force)" % final)
            tmp = final + ".tmp"
            want = min(part_size, total - (idx - 1) * part_size)
            h = hashlib.sha256()
            got = 0
            try:
                with open(tmp, "wb") as dst:
                    while got < want:
                        buf = src.read(min(CHUNK, want - got))
                        if not buf:
                            raise Fail(2, "%s image shrank while being read: %s" % (role, image))
                        dst.write(buf)
                        h.update(buf)
                        whole.update(buf)
                        got += len(buf)
                os.replace(tmp, final)
            except (OSError, Fail) as e:
                try:
                    os.remove(tmp)  # never leave a half-written part behind
                except OSError:
                    pass
                if isinstance(e, Fail):
                    raise
                raise Fail(3, "write failed for %s: %s" % (final, e))
            parts.append({"index": idx, "name": name, "size": want, "sha256": h.hexdigest()})
        if src.read(1):
            raise Fail(2, "%s image grew while being read: %s" % (role, image))
    for p in parts:
        if sha256_file(os.path.join(outdir, p["name"])) != p["sha256"]:
            raise Fail(3, "part %s read back with a different SHA-256" % p["name"])
    say("  %s sha256 %s" % (fname, whole.hexdigest()))
    return {
        "role": role,
        "version": version,
        "tag": tag,
        "file": fname,
        "size": total,
        "sha256": whole.hexdigest(),
        "part_size": part_size,
        "parts": parts,
    }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                return h.hexdigest()
            h.update(buf)


def reuse_disc(role, path, version, tag):
    old = load_json(path, "reused manifest")
    if not isinstance(old, dict) or old.get("format") != FORMAT or old.get("format_version") != FORMAT_VERSION:
        raise Fail(2, "reused manifest is not a %s version %d: %s" % (FORMAT, FORMAT_VERSION, path))
    found = [d for d in old.get("discs", []) if isinstance(d, dict) and d.get("role") == role]
    if len(found) != 1:
        raise Fail(2, "reused manifest has no single %s disc: %s" % (role, path))
    d = found[0]
    if d.get("version") != version or d.get("tag") != tag:
        raise Fail(2, "reused %s disc is %s/%s, the bill of materials wants %s/%s"
                   % (role, d.get("version"), d.get("tag"), version, tag))
    check_disc(d)
    say("reuse %s %s from %s" % (role, version, path))
    return {k: d[k] for k in ("role", "version", "tag", "file", "size", "sha256", "part_size", "parts")}


def check_disc(d):
    """Self-check of a disc entry: names safe, hashes well formed, every part but the
    last exactly part_size bytes, part sizes add up to the disc size."""
    try:
        ok = (NAME_RE.match(d["file"]) and SHA_RE.match(d["sha256"]) and isinstance(d["size"], int)
              and isinstance(d["part_size"], int) and 1 <= d["part_size"] <= MAX_PART and d["parts"])
        total = 0
        last = len(d["parts"])
        for i, p in enumerate(d["parts"], 1):
            ok = ok and p["index"] == i and NAME_RE.match(p["name"]) and SHA_RE.match(p["sha256"])
            ok = ok and isinstance(p["size"], int) and 1 <= p["size"] <= d["part_size"]
            ok = ok and (i == last or p["size"] == d["part_size"])
            ok = ok and p["name"] == "%s.part%03d" % (d["file"], i)
            total += p["size"]
        ok = ok and total == d["size"]
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise Fail(2, "disc entry for %s is malformed" % d.get("role"))


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description="Split Deep32 discs into parts and write MANIFEST.json (contract C2).")
    ap.add_argument("--release", required=True)
    ap.add_argument("--release-tag")
    ap.add_argument("--created", required=True)
    ap.add_argument("--bom", required=True)
    ap.add_argument("--disc", action="append")
    ap.add_argument("--reuse", action="append")
    ap.add_argument("--key-dir")
    ap.add_argument("--introduce-key", action="append")
    ap.add_argument("--part-size", default="1900MiB")
    ap.add_argument("--authorship")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-incomplete", action="store_true")
    ap.add_argument("--force", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 1

    try:
        if not VERSION_RE.match(a.release):
            raise Fail(1, "--release must look like 1.0.0")
        tag = a.release_tag or "v" + a.release
        if not TAG_RE.match(tag):
            raise Fail(1, "--release-tag is not a usable tag: %r" % tag)
        if not CREATED_RE.match(a.created):
            raise Fail(1, "--created must be UTC like 2026-11-14T00:00:00Z")
        try:
            datetime.datetime.strptime(a.created, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise Fail(1, "--created is not a real date and time: %s" % a.created)
        part_size = parse_size(a.part_size)
        discs_in = role_pairs(a.disc, "--disc")
        reuse_in = role_pairs(a.reuse, "--reuse")
        both = set(discs_in) & set(reuse_in)
        if both:
            raise Fail(1, "role given as both --disc and --reuse: %s" % ", ".join(sorted(both)))
        apath = a.authorship or os.path.join(HERE, "release-authorship.json")
        auth = load_json(apath, "authorship.json")
        for k in ("name", "contact_email", "github", "required_notice"):
            if not isinstance(auth.get(k), str) or not auth[k]:
                raise Fail(2, "authorship.json lacks %s" % k)

        key_dir = a.key_dir or os.path.join(os.path.dirname(os.path.dirname(HERE)), "install")
        fpr = read_pin(os.path.join(key_dir, "release-key.fingerprint"))
        shipped = key_fingerprints(os.path.join(key_dir, "release-key.gpg"), "the shipped release key")
        if shipped != [fpr]:
            raise Fail(2, "the shipped key %s holds %s, not exactly the pinned key %s"
                       % (os.path.join(key_dir, "release-key.gpg"), ", ".join(shipped) or "no key", fpr))
        introduces = []
        for p in a.introduce_key or []:
            got = key_fingerprints(p, "--introduce-key")
            if len(got) != 1:
                raise Fail(2, "--introduce-key must hold exactly one primary key: %s" % p)
            if got[0] == fpr:
                raise Fail(2, "--introduce-key names the signing key itself")
            if any(k["fingerprint"] == got[0] for k in introduces):
                raise Fail(2, "--introduce-key names the same key twice")
            introduces.append({"type": "openpgp", "fingerprint": got[0]})

        bom = read_bom(a.bom, a.allow_incomplete)
        for role in list(discs_in) + list(reuse_in):
            if role not in bom:
                raise Fail(2, "%s is given but not in the bill of materials" % role)
        for role in bom:
            if role not in discs_in and role not in reuse_in:
                raise Fail(2, "bill of materials names %s but no --disc or --reuse gives it" % role)

        for role in ROLES:
            if role in discs_in:
                plan_disc(role, discs_in[role], bom[role]["version"], part_size)

        out = a.out
        if os.path.exists(out) and not os.path.isdir(out):
            raise Fail(3, "--out names a file, not a folder: %s" % out)
        try:
            os.makedirs(out, exist_ok=True)
            not_empty = bool(os.listdir(out))
        except OSError as e:
            raise Fail(3, "cannot use the output folder %s: %s" % (out, e))
        if not_empty and not a.force:
            raise Fail(3, "output folder is not empty (use --force): %s" % out)
        if a.force:
            # Parts of an earlier run (perhaps with more parts) must not linger beside the new ones.
            for old in sorted(os.listdir(out)):
                sm = STALE_RE.match(old)
                if sm and sm.group(1) in discs_in:
                    try:
                        os.remove(os.path.join(out, old))
                    except OSError as e:
                        raise Fail(3, "could not remove the earlier part %s: %s" % (old, e))
                    say("removed earlier part %s" % old)

        discs = []
        for role in ROLES:
            if role not in bom:
                continue
            v, t = bom[role]["version"], bom[role]["tag"]
            if role in discs_in:
                d = write_parts(role, discs_in[role], v, t, part_size, out, a.force)
            else:
                d = reuse_disc(role, reuse_in[role], v, t)
            check_disc(d)
            discs.append(d)

        names = {}
        assets = {tag: 2}
        for d in discs:
            for p in d["parts"]:
                if p["name"] in names or p["name"] in ("MANIFEST.json", SIG_NAME, "MANIFEST.json.sig"):
                    raise Fail(2, "part name used twice: %s" % p["name"])
                names[p["name"]] = 1
            assets[d["tag"]] = assets.get(d["tag"], 0) + len(d["parts"])
        for t, n in sorted(assets.items()):
            if n > MAX_ASSETS_PER_RELEASE:
                raise Fail(2, "release %s would hold %d files; GitHub allows %d" % (t, n, MAX_ASSETS_PER_RELEASE))

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "release": {"version": a.release, "tag": tag, "created": a.created, "complete": len(discs) == len(ROLES)},
            "author": {
                "name": auth["name"],
                "contact_email": auth["contact_email"],
                "github": auth["github"],
                "notice": auth["required_notice"],
            },
            "signer": {"type": "openpgp", "fingerprint": fpr},
            "introduces": introduces,
            "bill_of_materials": [
                {"role": d["role"], "version": d["version"], "tag": d["tag"], "file": d["file"],
                 "size": d["size"], "sha256": d["sha256"]}
                for d in discs
            ],
            "discs": discs,
        }
        text = json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
        mpath = os.path.join(out, "MANIFEST.json")
        for stale in (SIG_NAME, "MANIFEST.json.sig"):
            sp = os.path.join(out, stale)
            if os.path.exists(sp):
                try:
                    os.remove(sp)
                except OSError as e:
                    raise Fail(3, "could not remove the stale %s: %s" % (stale, e))
                say("removed a stale %s (it signed an older manifest)" % stale)
        try:
            with open(mpath + ".tmp", "wb") as f:
                f.write(text.encode("ascii"))
            os.replace(mpath + ".tmp", mpath)
        except OSError as e:
            raise Fail(3, "could not write %s: %s" % (mpath, e))
        say("wrote %s (sha256 %s)" % (mpath, hashlib.sha256(text.encode("ascii")).hexdigest()))
        say("signer: openpgp %s (%s)" % (fpr, openpgp_pub.spaced(fpr)))
        say("next: Jeffrey signs it, in a terminal of his own, in the folder %s:" % os.path.abspath(out))
        say('  "%s" --local-user %s --armor --detach-sign --output %s MANIFEST.json' % (SIGN_GPG, fpr, SIG_NAME))
        say("  (in PowerShell, type & and a space in front of it)")
        return 0
    except Fail as e:
        print("make-manifest: " + str(e), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
