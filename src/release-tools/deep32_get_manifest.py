#!/usr/bin/env python3
"""Local-only release helpers. Python 3.10+, standard library.

Exit 3 means the requested disc is pending; other refusals exit 1.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import unquote, urlsplit


BUFFER = 1024 * 1024
MAX_META = 5 * 1024 * 1024
MAX_PART = 1_900_000_000
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
FINGERPRINT = re.compile(r"(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})\Z")
TAG = re.compile(r"v[A-Za-z0-9][A-Za-z0-9._-]*\Z")
ROLES = {"disc1": "workstation", "disc2": "content", "disc3": "gpu-pack"}


class Refusal(Exception):
    pass


class PendingDisc(Refusal):
    pass


def safe_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or ".." in value or value.endswith("."):
        return False
    stem = value.split(".", 1)[0].lower()
    return stem not in {"con", "prn", "aux", "nul", *("com%d" % n for n in range(1, 10)), *("lpt%d" % n for n in range(1, 10))}


def path(value, kind="new"):
    """Inspect every existing component without resolving links."""
    result = Path(os.path.abspath(value))
    for item in (*reversed(result.parents), result):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("Links and reparse points are refused: %s" % item)
        if item != result and not stat.S_ISDIR(info.st_mode):
            raise Refusal("A path component is not a directory")
    if kind == "file" and not result.is_file():
        raise Refusal("A required file is missing: %s" % result)
    if kind == "dir" and not result.is_dir():
        raise Refusal("A required directory is missing: %s" % result)
    if result.exists() and not (result.is_file() or result.is_dir()):
        raise Refusal("Special files are refused")
    return result


def mkdir(value):
    result = path(value)
    result.mkdir(parents=True, exist_ok=True)
    return path(result, "dir")


def sha256(value):
    result = hashlib.sha256()
    with path(value, "file").open("rb") as stream:
        for block in iter(lambda: stream.read(BUFFER), b""):
            result.update(block)
    return result.hexdigest()


def check_file(value, size, digest):
    item = path(value, "file")
    if item.stat().st_size != int(size) or sha256(item) != digest:
        raise Refusal("Checksum or size failed: %s; remove this local file to retry" % item)


def integer(value, minimum=1, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise Refusal("Invalid manifest size or count")
    return value


def read_sums(value):
    source = path(value, "file")
    if source.stat().st_size > MAX_META:
        raise Refusal("SHA256SUMS is too large")
    raw = source.read_bytes()
    if not raw.endswith(b"\n") or any(byte != 10 and not 32 <= byte <= 126 for byte in raw):
        raise Refusal("Root checksums must be ASCII with LF line endings")
    result = {}
    folded = set()
    for line in raw.decode("ascii").split("\n")[:-1]:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or not safe_name(match[2]) or match[2].lower() in folded:
            raise Refusal("Malformed or duplicate root checksum entry")
        folded.add(match[2].lower())
        result[match[2]] = match[1]
    if not result or len(result) > 1000:
        raise Refusal("Invalid root checksum entry count")
    return result


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("Duplicate JSON property")
        result[key] = value
    return result


def read_assembly(manifest, sums_path):
    # The caller MUST have authenticated sums_path before using this helper.
    sums = read_sums(sums_path)
    source = path(manifest, "file")
    if source.stat().st_size > MAX_META or sums.get("ASSEMBLY.json") != sha256(source):
        raise Refusal("ASSEMBLY.json does not match authenticated SHA256SUMS")
    data = json.loads(source.read_text(encoding="ascii"), object_pairs_hook=unique_json)
    if not isinstance(data, dict) or data.get("format") != "deep32-release-assembly" or type(data.get("format_version")) is not int or data["format_version"] != 1:
        raise Refusal("Unsupported assembly manifest")
    return data, sums


def pending_layout(data, sums):
    pending = data.get("pending", [])
    if (not isinstance(pending, list)
            or any(not isinstance(role, str) or role not in ("disc2", "disc3") for role in pending)
            or len(pending) != len(set(pending))):
        raise Refusal("Only disc2 and disc3 may be pending, each declared once")
    prefixes = data.get("pending_prefixes", {})
    if not isinstance(prefixes, dict):
        raise Refusal("Invalid pending asset prefixes")
    if pending:
        version = data.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", version) or ".." in version:
            raise Refusal("Invalid pending release version")
        expected = {role: "deep32-%s-%s-%s" % (version, role, ROLES[role])
                    for role in sorted(pending)}
        if prefixes != expected:
            raise Refusal("Each pending disc must declare its intended asset prefix")
        if any(name.lower().startswith(prefix.lower())
               for name in sums for prefix in expected.values()):
            raise Refusal("A pending disc must not have release assets")
    elif prefixes:
        raise Refusal("Pending prefixes require a pending disc")
    discs = data.get("discs")
    if not isinstance(discs, list) or len(discs) != len(ROLES) - len(pending):
        raise Refusal("Manifest must contain every present disc exactly once")
    seen = set()
    for disc in discs:
        if not isinstance(disc, dict):
            raise Refusal("Malformed disc entry")
        role = disc.get("role")
        if not isinstance(role, str) or role not in ROLES or role in seen or disc.get("kind") != ROLES[role]:
            raise Refusal("Invalid or duplicate disc role")
        seen.add(role)
    if seen != set(ROLES) - set(pending):
        raise Refusal("Present and pending disc roles disagree")
    return set(pending)


def pending(manifest, sums_path):
    data, sums = read_assembly(manifest, sums_path)
    for role in sorted(pending_layout(data, sums)):
        print(role + "\t" + data["pending_prefixes"][role])


def selection(manifest, sums_path, role, release):
    if not TAG.fullmatch(release) or ".." in release:
        raise Refusal("Invalid release tag")
    data, sums = read_assembly(manifest, sums_path)
    if data.get("version") != release[1:]:
        raise Refusal("Manifest version does not match requested release")
    pending_roles = pending_layout(data, sums)
    cap = integer(data.get("part_size_bytes"), maximum=MAX_PART)
    discs = data.get("discs")
    seen_roles = set()
    seen_names = {"assembly.json", "sha256sums", "sha256sums.sig", "assets.txt"}
    selected = None

    def add_name(name):
        if not safe_name(name) or name.lower() in seen_names:
            raise Refusal("Unsafe or duplicate manifest name")
        seen_names.add(name.lower())

    for disc in discs:
        if not isinstance(disc, dict):
            raise Refusal("Malformed disc entry")
        current = disc.get("role")
        if current not in ROLES or current in seen_roles or disc.get("kind") != ROLES[current]:
            raise Refusal("Invalid or duplicate disc role")
        seen_roles.add(current)
        name, digest = disc.get("file"), disc.get("sha256")
        add_name(name)
        suffix = Path(name).suffix
        if suffix not in ((".img", ".iso") if current == "disc1" else (".iso",)):
            raise Refusal("Unsupported disc image extension")
        size = integer(disc.get("size"))
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or type(disc.get("split")) is not bool:
            raise Refusal("Invalid disc hash or split flag")
        split = disc["split"]
        payloads = disc.get("payloads")
        if not isinstance(payloads, list) or not 1 <= len(payloads) <= 999:
            raise Refusal("Invalid part count")
        expected_count = (size + cap - 1) // cap if size > cap else 1
        if split != (size > cap) or len(payloads) != expected_count:
            raise Refusal("Part count disagrees with image size")
        total = 0
        for index, payload in enumerate(payloads, 1):
            if not isinstance(payload, dict):
                raise Refusal("Invalid payload entry")
            part_name, part_hash = payload.get("name"), payload.get("sha256")
            expected_name = "%s.part%03d" % (name, index) if split else name
            if part_name != expected_name or not safe_name(part_name):
                raise Refusal("Invalid payload name or order")
            if split:
                add_name(part_name)
            part_size = integer(payload.get("size"), maximum=cap)
            if part_size != min(cap, size - total):
                raise Refusal("Invalid payload size")
            if not isinstance(part_hash, str) or not HASH.fullmatch(part_hash):
                raise Refusal("Invalid payload SHA256")
            if sums.get(part_name) != part_hash:
                raise Refusal("Payload does not agree with authenticated root checksums")
            total += part_size
        if total != size or (not split and payloads[0]["sha256"] != digest):
            raise Refusal("Payload total or image SHA256 disagreement")
        parts_manifest = disc.get("parts_manifest")
        if parts_manifest != (name + ".parts.tsv" if split else None):
            raise Refusal("Invalid parts manifest name")
        if split:
            add_name(parts_manifest)
        for field in ("checksums", "readme"):
            add_name(disc.get(field))
        if current == role:
            selected = disc
    if seen_roles != set(ROLES) - pending_roles:
        raise Refusal("Missing present disc")
    if role in pending_roles:
        raise PendingDisc("%s is not in this release yet; it will be added to the same release. Check the release page." % role)
    if selected is None:
        raise Refusal("Missing requested disc")
    print("image\t%s\t%d\t%s" % (selected["file"], selected["size"], selected["sha256"]))
    for item in selected["payloads"]:
        print("asset\t%s\t%d\t%s" % (item["name"], item["size"], item["sha256"]))


def local_source(source):
    if source.startswith("file:"):
        parsed = urlsplit(source)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise Refusal("The local source must be a local file URL or directory")
        source = unquote(parsed.path)
        if os.name == "nt" and re.match(r"/[A-Za-z]:/", source):
            source = source[1:]
    elif "://" in source:
        raise Refusal("Only local fixture sources are accepted by --source")
    if source.startswith(("//", "\\\\")):
        raise Refusal("Network paths are refused for --source")
    return path(source, "dir")


def local_copy(source, name, target, maximum):
    if not safe_name(name):
        raise Refusal("Unsafe asset name")
    source_file = path(local_source(source) / name, "file")
    target = path(target)
    path(target.parent, "dir")
    size = source_file.stat().st_size
    maximum = int(maximum)
    if size > maximum:
        raise Refusal("Source asset exceeds its allowed size")
    offset = target.stat().st_size if target.exists() else 0
    if offset > size:
        raise Refusal("Partial download exceeds source size; remove it to retry")
    with source_file.open("rb") as src, target.open("ab") as dest:
        src.seek(offset)
        remaining = size - offset
        while remaining:
            data = src.read(min(BUFFER, remaining))
            if not data:
                raise Refusal("Source asset changed during transfer")
            dest.write(data)
            remaining -= len(data)


def publish(source, target, replace=False):
    source, target = path(source, "file"), path(target)
    path(target.parent, "dir")
    if replace:
        os.replace(source, target)
    else:
        # Hard-link publication is atomic and refuses a pre-existing destination.
        os.link(source, target)
        source.unlink()


def join(target, size, digest, parts):
    target = path(target)
    if target.exists():
        raise Refusal("The joined destination already exists")
    parent = path(target.parent, "dir")
    fd, name = tempfile.mkstemp(prefix=".deep32-join-", dir=parent)
    temporary = Path(name)
    try:
        whole = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as dest:
            for part in parts:
                with path(part, "file").open("rb") as src:
                    for block in iter(lambda: src.read(BUFFER), b""):
                        total += len(block)
                        if total > int(size):
                            raise Refusal("Joined image exceeds declared size")
                        whole.update(block)
                        dest.write(block)
        if total != int(size) or whole.hexdigest() != digest:
            raise Refusal("Joined image failed its SHA256 or size check")
        publish(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def primary_fingerprint(value):
    """Read one primary fingerprint from GPG's colon-delimited public listing."""
    # User IDs need not be ASCII; inspect only the ASCII record tags and fpr.
    text = path(value, "file").read_bytes()
    primary_count = 0
    fingerprints = []
    in_primary = False
    for line in text.splitlines():
        fields = line.split(b":")
        if fields[0] == b"pub":
            primary_count += 1
            in_primary = True
        elif fields[0] == b"sub":
            in_primary = False
        elif fields[0] == b"fpr" and in_primary:
            fingerprint = fields[9].decode("ascii") if len(fields) > 9 else ""
            if not FINGERPRINT.fullmatch(fingerprint):
                raise Refusal("Malformed primary fingerprint in supplied public key")
            fingerprints.append(fingerprint.upper())
    if primary_count != 1 or len(fingerprints) != 1:
        raise Refusal("The supplied public key file must contain exactly one primary key and fingerprint")
    return fingerprints[0]


def validsig(status, key_status):
    expected = primary_fingerprint(key_status)
    text = path(status, "file").read_text(encoding="ascii")
    records = [line.split()[2:] for line in text.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
    if len(records) != 1:
        raise Refusal("Expected exactly one valid GPG signature")
    fields = records[0]
    if len(fields) not in (9, 10) or not FINGERPRINT.fullmatch(fields[0]):
        raise Refusal("Malformed GPG VALIDSIG status")
    primary = fields[9] if len(fields) == 10 else fields[0]
    if not FINGERPRINT.fullmatch(primary):
        raise Refusal("Malformed primary fingerprint in GPG status")
    if primary.upper() != expected:
        raise Refusal("The signature primary fingerprint does not match the supplied public key")


def main(argv):
    command, *args = argv
    if command == "selection":
        selection(*args)
    elif command == "pending":
        pending(*args)
    elif command == "path":
        print(path(*args).as_posix())
    elif command == "mkdir":
        print(mkdir(*args).as_posix())
    elif command == "check-file":
        check_file(*args)
    elif command == "local-copy":
        local_copy(*args)
    elif command == "publish":
        publish(*args)
    elif command == "refresh":
        publish(*args, replace=True)
    elif command == "join":
        join(*args[:3], args[3:])
    elif command == "partial":
        item = path(args[0])
        if item.exists() and (not item.is_file() or item.stat().st_size > int(args[1])):
            raise Refusal("Invalid partial download; remove it to retry")
    elif command == "length":
        item = path(args[0])
        print(path(item, "file").stat().st_size if item.exists() else 0)
    elif command == "validsig":
        validsig(*args)
    else:
        raise Refusal("Unknown helper command")


if __name__ == "__main__":
    try:
        # TSV is consumed by Bash; keep LF even when fixtures run on Windows.
        sys.stdout.reconfigure(newline="\n")
        sys.stderr.reconfigure(newline="\n")
        main(sys.argv[1:])
    except PendingDisc as error:
        print(str(error), file=sys.stderr)
        sys.exit(3)
    except (Refusal, OSError, ValueError, TypeError, UnicodeError, KeyError, RecursionError) as error:
        print("Download refused: %s" % error, file=sys.stderr)
        sys.exit(1)
