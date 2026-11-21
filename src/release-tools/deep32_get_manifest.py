#!/usr/bin/env python3
"""Local-only release helpers. Python 3.10+, standard library.

Exit 3 means the requested disc is pending; other refusals exit 1.
"""

import hashlib
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
import sys
import tempfile
import time
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


class NeedsAction(Refusal):
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


def read_metadata(value, *, limit=MAX_META):
    """Bound bytes at the open stream, including growth after any earlier stat."""
    with path(value, "file").open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise Refusal("Metadata exceeds the size limit")
    return raw


def read_sums(value):
    source = path(value, "file")
    if source.stat().st_size > MAX_META:
        raise Refusal("SHA256SUMS is too large")
    raw = read_metadata(source)
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
    # Release checks require a binary signature over the exact bytes.
    if fields[8] != "00":
        raise Refusal("Only binary detached signatures (class 00) are accepted")
    primary = fields[9] if len(fields) == 10 else fields[0]
    if not FINGERPRINT.fullmatch(primary):
        raise Refusal("Malformed primary fingerprint in GPG status")
    if primary.upper() != expected:
        raise Refusal("The signature primary fingerprint does not match the supplied public key")


def gpg_executable(explicit=""):
    """Resolve a file, never shell text. Windows discovery uses native GnuPG."""
    candidates = [explicit] if explicit else []
    if not explicit and os.name == "nt":
        for base in (os.environ.get("ProgramFiles", "C:/Program Files"),
                     os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")):
            candidates.extend(str(Path(base) / suffix) for suffix in (
                "GnuPG/bin/gpg.exe", "Gpg4win/bin/gpg.exe"))
    if not explicit:
        candidates += [shutil.which("gpg") or ""]
    for candidate in candidates:
        if not explicit and os.name == "nt" and str(candidate).replace("\\", "/").lower().endswith("/usr/bin/gpg.exe"):
            # Git/MSYS may be installed under any directory name or be on PATH.
            continue
        if candidate and Path(candidate).is_file():
            selected = path(candidate, "file")
            if os.name == 'nt' and selected.suffix.lower() != '.exe':
                if explicit:
                    raise Refusal('--gpg must name a native .exe file on Windows')
                continue
            result = subprocess.run([str(selected), "--no-options", "--version"], capture_output=True,
                                    timeout=10, check=False)
            if result.returncode == 0 and b"gpg (GnuPG)" in result.stdout:
                return selected
            if explicit:
                raise Refusal("The --gpg file is not a working GnuPG executable")
    # GnuPG fixes use the host's own package manager or vendor download.
    requirement = ("GnuPG was not found; install GnuPG using this host's package manager "
                   "or the vendor download; ")
    raise NeedsAction(requirement + ("reopen PowerShell and run this check again." if os.name == "nt" else
                                    "reopen the terminal and run this check again."))


def authenticate(cache, key, fingerprint, executable="", checksum="SHA256SUMS"):
    """Import one public key into a fresh temporary home and pin its primary."""
    if not FINGERPRINT.fullmatch(fingerprint):
        raise Refusal("Expected primary fingerprint must be 40 or 64 hexadecimal digits")
    cache, key = path(cache, "dir"), path(key, "file")
    if not safe_name(checksum):
        raise Refusal("Invalid checksum file name")
    for name in (checksum, checksum + ".sig"):
        if path(cache / name, "file").stat().st_size > MAX_META:
            raise Refusal("Checksum or signature exceeds the metadata size limit")
    if key.stat().st_size > MAX_META:
        raise Refusal("Public key exceeds the metadata size limit")
    gpg = gpg_executable(executable)
    with tempfile.TemporaryDirectory(prefix=".deep32-public-key-", dir=cache) as name:
        os.chmod(name, 0o700)
        base = [str(gpg), "--no-options", "--homedir", str(name), "--batch",
                "--no-autostart", "--no-auto-key-retrieve", "--auto-key-locate", "clear"]
        environment = os.environ.copy()
        environment["GNUPGHOME"] = str(name)

        def run(arguments, public_import=False):
            start = time.monotonic()
            with subprocess.Popen(base + [str(a) for a in arguments], env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                while True:
                    try:
                        out, _ = child.communicate(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        print("Waiting for local public-key verification; stop and retry if GnuPG is blocked.", file=sys.stderr)
                        if time.monotonic() - start >= 60:
                            child.kill()
                            child.communicate()
                            raise Refusal("Local GnuPG verification timed out; retry the same command")
                # Public-key import can exit 2 when --no-autostart prevents an
                # optional agent notification (including Windows long paths).
                # Only an explicit IMPORT_OK for our pinned public key is
                # usable; the subsequent VALIDSIG is still mandatory.
                imported = any(line.startswith(b"[GNUPG:] IMPORT_OK ") and
                               line.split()[-1].decode("ascii", "replace").upper() == fingerprint.upper()
                               for line in out.splitlines())
                if child.returncode and not (public_import and child.returncode == 2 and imported):
                    raise Refusal("Public key or checksum signature failed; check the expected release fingerprint")
                return out

        listing = run(["--with-colons", "--show-keys", "--fingerprint", str(key)])
        if any(line.split(b":", 1)[0] in (b"sec", b"ssb") for line in listing.splitlines()):
            raise Refusal("Secret-key input is refused; supply a public .asc or binary keyring")
        listing_path = Path(name) / "public-status"
        listing_path.write_bytes(listing)
        if primary_fingerprint(listing_path) != fingerprint.upper():
            raise Refusal("Supplied public key does not match the expected primary fingerprint")
        run(["--status-fd", "1", "--import-options", "import-minimal", "--import", str(key)], public_import=True)
        status = run(["--status-fd", "1", "--verify", str(cache / (checksum + ".sig")), str(cache / checksum)])
        status_path = Path(name) / "signature-status"
        status_path.write_bytes(status)
        validsig(status_path, listing_path)


def contract_module(name='contract_validate'):
    """Load the bundled validator by its fixed sibling path under isolated Python."""
    spec = importlib.util.spec_from_file_location("deep32_" + name, path(Path(__file__).with_name(name + ".py"), "file"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_mirrors(manifest, sums_path, release=None):
    """Read MIRRORS only after the caller authenticated sums_path.

    Raw bytes are hashed before JSON parsing or inspection of any index field.
    This low-level reader deliberately performs no retrieval or key discovery.
    """
    sums = read_sums(sums_path)
    source = path(manifest, "file")
    if source.name != "MIRRORS.json" or source.stat().st_size > MAX_META:
        raise Refusal("Invalid MIRRORS.json file or size")
    raw = read_metadata(source)
    if sums.get("MIRRORS.json") != hashlib.sha256(raw).hexdigest():
        raise Refusal("MIRRORS.json does not match authenticated SHA256SUMS")
    validator = contract_module()
    data = validator.validate_contract("mirrors", validator.strict_loads(raw, max_bytes=MAX_META))
    if release is not None and data["release"] != release:
        raise Refusal("MIRRORS release does not match the requested release")
    for disc in validator.mirror_transports(data):
        if sums.get(disc["file"]) != disc["sha256"]:
            raise Refusal("Whole image does not agree with authenticated root checksums")
    if data["format_version"] == 2:
        binding = data["capability_manifest"]
        if sums.get(binding["file"]) != binding["sha256"]:
            raise Refusal("Capability manifest does not agree with authenticated root checksums")
    return data


def read_mirror_capabilities(data, sums_path, capability_path=None):
    """Read only capability bytes bound by the authenticated root and index."""
    if data["format_version"] != 2:
        raise Refusal("Bundle selection requires mirrors version 2")
    binding = data["capability_manifest"]
    sums = read_sums(sums_path)
    source = path(capability_path or Path(sums_path).parent / binding["file"], "file")
    if source.name != binding["file"]:
        raise Refusal("Capability manifest basename differs from the mirror binding")
    raw = read_metadata(source, limit=8 * 1024 * 1024)
    if sums.get(binding["file"]) != binding["sha256"] or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise Refusal("Capability manifest bytes differ from authenticated release metadata")
    validator = contract_module()
    manifest = validator.strict_loads(raw, max_bytes=8 * 1024 * 1024)
    validator.validate_mirror_capabilities(data, manifest, capability_bytes=raw)
    return manifest


def select_whole(manifest, sums_path, role, release):
    """Return one whole-image descriptor from an already authenticated root."""
    if role not in ROLES:
        raise Refusal("Invalid disc role")
    data = read_mirrors(manifest, sums_path, release)
    if role == "disc3" and data["format_version"] == 2:
        raise Refusal("Disc3 requires an explicit hardware class and bundle selection")
    return next(disc for disc in data["discs"] if disc["role"] == role)


def validate_whole_release(folder, *, release=None, images=None, scope="metadata", require_signatures=True):
    """Check exact whole-release coverage after root authentication.

    Signing preflight calls this with require_signatures=False, before any GPG
    signing command. It validates bytes/closure, never claims authentication.
    Images are absent only at the three precisely signed MIRRORS names.
    """
    if scope not in ("metadata", "whole"):
        raise Refusal("Choose metadata or whole verification scope")
    folder = path(folder, "dir")
    sums = read_sums(folder / "SHA256SUMS")
    data = read_mirrors(folder / "MIRRORS.json", folder / "SHA256SUMS", release)
    validator = contract_module()
    for name in sums:
        validator.safe_path(name, basename=True)
    transports = validator.mirror_transports(data)
    image_names = {disc["file"] for disc in transports}
    if any(name == "SHA256SUMS" or name.lower().endswith(".sig") or name == "ASSEMBLY.json"
           or re.search(r"\.part(?:[0-9]{3}|s\.tsv)$", name)
           or (name.lower().endswith((".img", ".iso")) and name not in image_names) for name in sums):
        raise Refusal("Whole root contains an excluded or legacy payload entry")
    files = {}
    folded = set()
    for item in sorted(folder.iterdir()):
        path(item, "file")
        if not safe_name(item.name) or item.name.lower() in folded:
            raise Refusal("Release contains an unsafe or colliding file name")
        validator.safe_path(item.name, basename=True)
        files[item.name] = item
        folded.add(item.name.lower())
    child_names = validator.mirror_child_names(data)
    children = {name: [d for d in transports if d.get("role", "disc3") == role]
                for role, name in child_names.items()}
    readmes = {name.removesuffix("-SHA256SUMS") + "-README.md" for name in children}
    if any(name.endswith("-SHA256SUMS") and name not in children for name in sums):
        raise Refusal("Unexpected per-disc checksum file")
    small = set(sums) - image_names
    if not ({"MIRRORS.json", "ASSETS.txt", "ASSET-MAP.json"} | set(children) | readmes) <= small:
        raise Refusal("Required whole-release metadata is absent from root checksums")
    signatures = {"SHA256SUMS.sig", *(name + ".sig" for name in children)}
    expected = small | {"SHA256SUMS"} | (signatures if require_signatures else set())
    if set(files) != expected:
        raise Refusal("Release metadata has missing or unexpected files: " + ", ".join(sorted(set(files) ^ expected)))
    capabilities = contract_module('capabilities') if data['format_version'] == 2 else None
    storage_name = capabilities.STORAGE_ASSET if capabilities is not None else None
    for name in sorted(small):
        if name == storage_name:
            continue  # The detached verifier reads and hashes this file with its own bound.
        limit = (8 * 1024 * 1024 if data["format_version"] == 2 and
                 name == data["capability_manifest"]["file"] else MAX_META)
        if files[name].stat().st_size > limit or sha256(files[name]) != sums[name]:
            raise Refusal("Metadata size or SHA256 failed: " + name)
    if capabilities is not None:
        capability = read_mirror_capabilities(data, folder / 'SHA256SUMS')
        storage = read_metadata(files[storage_name], limit=capabilities.STORAGE_LIMIT) if storage_name in files else None
        capabilities.verify_detached_storage(
            capability, storage, sums.get(storage_name),
            observed={row['bundle_id']: {'iso_bytes': row['size']} for row in data['bundles']})
        if storage is not None and 'FORGE-PREREQUISITES.json' in files:
            requirements = contract_module('forge_requirements')
            requirements.validate(read_metadata(files[requirements.ASSET], limit=requirements.LIMIT),
                                  capability_sha256=data['capability_manifest']['sha256'],
                                  pins_sha256=capability['pins_sha256'], release=capability['release'],
                                  storage_evidence_sha256=sums[storage_name])
    for name in sorted(signatures if require_signatures else ()):
        if files[name].stat().st_size > MAX_META:
            raise Refusal("Signature exceeds metadata size limit")
    raw_assets = read_metadata(files["ASSETS.txt"])
    if not raw_assets.endswith(b"\n"):
        raise Refusal("ASSETS.txt must end with LF")
    asset_names = raw_assets.decode("ascii").split("\n")[:-1]
    uploads = small | {"SHA256SUMS"} | signatures
    if (any(not safe_name(name) for name in asset_names) or len(asset_names) != len(set(asset_names))
            or asset_names != sorted(uploads)):
        raise Refusal("ASSETS.txt must list exactly the sorted small uploads and four signatures")
    validator = contract_module()
    asset_map = validator.load_contract("asset-map", files["ASSET-MAP.json"])
    if any(entry["asset"] not in small for entry in asset_map["entries"]):
        raise Refusal("Asset map names metadata absent from the authenticated root")
    mapped = {entry["asset"] for entry in asset_map["entries"]}
    for child_name, descriptors in sorted(children.items()):
        child = read_sums(files[child_name])
        if storage_name in small and (storage_name in child) != (child_name == child_names['disc3']):
            raise Refusal('Physical storage measurements must belong to the Disc 3 checksum list only.')
        mandatory = {*(d["file"] for d in descriptors), child_name.removesuffix("-SHA256SUMS") + "-README.md"}
        if not mandatory <= set(child):
            raise Refusal("Per-disc checksums must cover its whole image and README")
        forbidden = image_names | readmes | set(children) | {"SHA256SUMS", "MIRRORS.json", "ASSETS.txt", "ASSET-MAP.json"}
        for name, digest in child.items():
            if (name not in sums or sums[name] != digest
                    or (name not in mandatory and (name in forbidden or name not in mapped))):
                raise Refusal("Per-disc checksum differs from its explicitly named release files")
    if scope == "whole":
        images = ({disc.get("role", disc.get("bundle_id")): folder.parent / "images" / disc["file"] for disc in transports}
                  if not images else images)
        if set(images) != {disc.get("role", disc.get("bundle_id")) for disc in transports}:
            raise Refusal("Whole scope requires one explicit path for every declared transport")
        seen_paths = set()
        for disc in transports:
            source = path(images[disc.get("role", disc.get("bundle_id"))], "file")
            identity = (source.stat().st_dev, source.stat().st_ino)
            if identity in seen_paths:
                raise Refusal("Duplicate image input")
            seen_paths.add(identity)
            check_file(source, disc["size"], disc["sha256"])
    return data


def verify_whole(argv, *, signing=False):
    parser = argparse.ArgumentParser(description="Verify explicit whole-image or metadata-only scope")
    if signing:
        parser.add_argument("--release-dir", dest="directory", required=True)
    else:
        parser.add_argument("directory")
        parser.add_argument("fingerprint")
        parser.add_argument("--key", default=str(Path(__file__).parents[2] / "install/release-key.gpg"))
        parser.add_argument("--gpg", default="")
    parser.add_argument("--scope", choices=("metadata", "whole"), required=not signing, default="metadata" if signing else None)
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--release")
    args = parser.parse_args(argv)
    images = {}
    for item in args.image:
        role, separator, source = item.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9-]{0,95}", role) or role in images or not source:
            raise Refusal("Each --image must name a unique disc role or bundle ID and explicit path")
        images[role] = source
    if args.scope != "whole" and images:
        raise Refusal("Explicit image inputs require whole verification scope")
    folder = path(args.directory, "dir")
    if signing and read_mirrors(folder / "MIRRORS.json", folder / "SHA256SUMS", args.release)["format_version"] != 2:
        raise Refusal("New alpha.2 signing preflight requires mirrors version 2")
    if not signing:
        authenticate(folder, args.key, args.fingerprint, args.gpg)
    data = validate_whole_release(folder, release=args.release, images=images, scope=args.scope, require_signatures=not signing)
    if not signing:
        for child in contract_module().mirror_child_names(data).values():
            authenticate(folder, args.key, args.fingerprint, args.gpg, child)
        print("Release signatures and metadata verified; scope: %s%s." %
              (args.scope, " (image bytes were not checked)" if args.scope == "metadata" else " (all declared whole objects checked)"))
    else:
        print("Whole-image signing preflight passed; exact metadata coverage checked.")


def selected_records(manifest, sums, role, release):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        selection(manifest, sums, role, release)
    rows = [line.split("\t") for line in output.getvalue().splitlines()]
    return rows[0], rows[1:]


def storage_plan(manifest, sums, role, release, destination, cache, style="sh", vm=""):
    """Peak *additional* allocation, grouped by the backing filesystem once."""
    image, assets = selected_records(manifest, sums, role, release)
    destination, cache = path(destination, "dir"), path(cache, "dir")
    target = destination / image[1]
    allocations = []
    if target.exists():
        check_file(target, image[2], image[3])
    else:
        for _, name, size, digest in assets:
            where = destination if style == "sh" and name == image[1] else cache
            asset = path(where / name)
            if asset.exists():
                check_file(asset, size, digest)
                needed = 0
            else:
                partial = path(str(asset) + ".download")
                retained = partial.stat().st_size if partial.exists() else 0
                if retained > int(size):
                    raise Refusal("Partial download exceeds its authenticated size")
                needed = int(size) - retained
            allocations.append((where, "retained-payload", needed))
        if style == "ps1" or assets[0][1] != image[1]:
            allocations.append((destination, "joined-image", int(image[2])))
    if vm:
        if role != "disc1" or vm not in ("hyper-v", "kvm"):
            raise Refusal("VM preparation requires disc1 and hyper-v or kvm")
        allocations.append((destination, "installer-copy", int(image[2]) + (512 if vm == "hyper-v" else 0)))
    groups = {}
    for folder, label, amount in allocations:
        identity = str(folder.stat().st_dev)
        row = groups.setdefault(identity, {"filesystem": identity, "path": str(folder), "needed": 0,
                                           "available": shutil.disk_usage(folder).free, "allocations": []})
        row["needed"] += amount
        row["allocations"].append({"stage": label, "bytes": amount})
    for row in groups.values():
        row["remaining"] = row["available"] - row["needed"]
        row["ready"] = row["remaining"] >= 0
    return {"schema": "deep32.storage.v1", "scope": "download-and-preparation", "disc": role,
            "filesystems": [groups[key] for key in sorted(groups)],
            "installed_storage": "unknown: payload, Docker data-root, WSL backing volume and rollback need measured Phase B media"}


def print_storage(*args):
    plan = storage_plan(*args)
    print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
    if any(not row["ready"] for row in plan["filesystems"]):
        raise NeedsAction("Insufficient storage; free the printed needed bytes on that filesystem and rerun the same command")


def publication_check(folder):
    """Fail before payload transfer if atomic no-overwrite publication is absent."""
    folder = path(folder, 'dir')
    with tempfile.TemporaryDirectory(prefix='.deep32-filesystem-', dir=folder) as name:
        source, target = Path(name) / 'synthetic-source', Path(name) / 'synthetic-link'
        source.write_bytes(b'Deep32 filesystem capability probe\n')
        try:
            os.link(source, target)
        except OSError:
            raise NeedsAction('This filesystem cannot publish files without overwrite; choose a local hard-link-capable --to filesystem and rerun the same command') from None


def prepare_vm(image, size, digest, hypervisor, destination):
    """Write installer artefacts and commands only. Never run a hypervisor."""
    image, destination = path(image, "file"), path(destination, "dir")
    check_file(image, size, digest)
    if hypervisor not in ("hyper-v", "kvm") or image.suffix != ".img":
        raise Refusal("VM preparation requires a verified disc1 raw .img and hyper-v or kvm")
    target = path(destination / (image.stem + "-" + hypervisor))
    if target.exists():
        raise Refusal("VM preparation destination exists; choose an empty --to directory")
    publication_check(destination)
    need = int(size) + (512 if hypervisor == "hyper-v" else 0)
    if shutil.disk_usage(destination).free < need:
        raise NeedsAction("Insufficient storage for the VM installer artefact; choose a larger --to filesystem")
    with tempfile.TemporaryDirectory(prefix=".deep32-vm-", dir=destination) as temporary:
        staging = Path(temporary)
        installer = staging / ("installer.vhd" if hypervisor == "hyper-v" else "installer.img")
        if hypervisor == "hyper-v":
            if int(size) % (1 << 20):
                raise Refusal("Hyper-V raw installer size must be a whole number of MiB")
            converter = Path(__file__).resolve().parents[1] / "workstation/release/tools/raw2vhd.py"
            spec = importlib.util.spec_from_file_location("deep32_raw2vhd", path(converter, "file"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            shutil.copyfile(image, installer)
            with installer.open("ab") as stream:
                stream.write(module.footer(int(size), bytes.fromhex(digest)))
        else:
            shutil.copyfile(image, installer)
        expected_size = int(size) + (512 if hypervisor == 'hyper-v' else 0)
        if installer.stat().st_size != expected_size:
            raise Refusal('VM installer copy has the wrong size')
        copied = hashlib.sha256()
        with installer.open('rb') as stream:
            remaining = int(size)
            while remaining:
                block = stream.read(min(BUFFER, remaining))
                if not block:
                    raise Refusal('VM installer copy is truncated')
                copied.update(block)
                remaining -= len(block)
            if hypervisor == 'hyper-v' and stream.read() != module.footer(int(size), bytes.fromhex(digest)):
                raise Refusal('VM installer VHD footer failed validation')
        if copied.hexdigest() != digest:
            raise Refusal('VM installer copy changed during preparation')
        # Never interpolate a path into executable syntax: commands locate their
        # own directory. Existing switch/network choices require human review.
        if hypervisor == "hyper-v":
            commands = """# Review this unqualified recipe on a disposable host before use.
# Run from this directory in an elevated PowerShell session after prerequisite checks.
$root = (Get-Location).Path
$switch = Read-Host 'Existing external or NAT switch with DHCP'
New-VHD -Path (Join-Path $root 'target.vhdx') -Dynamic -SizeBytes 160GB
New-VM -Name Deep32Workstation -Generation 1 -MemoryStartupBytes 12GB -NoVHD -SwitchName $switch
Set-VMProcessor -VMName Deep32Workstation -Count 6
Add-VMHardDiskDrive -VMName Deep32Workstation -ControllerType IDE -ControllerNumber 0 -ControllerLocation 0 -Path (Join-Path $root 'installer.vhd')
Add-VMHardDiskDrive -VMName Deep32Workstation -ControllerType IDE -ControllerNumber 0 -ControllerLocation 1 -Path (Join-Path $root 'target.vhdx')
# Review disk identities before installation: installer is IDE 0:0; empty target is IDE 0:1.
# After shutdown, detach installer.vhd and boot target.vhdx. Attach signed disc2 as DVD.
# No VM is created or started by deep32-get.
"""
        else:
            commands = """# Review this unqualified recipe on a disposable host before use.
# Run from this directory after prerequisite checks; the existing default NAT network needs DHCP.
qemu-img create -f qcow2 target.qcow2 160G
virt-install --name Deep32Workstation --memory 12288 --vcpus 6 --os-variant generic --import --boot hd --disk path=installer.img,format=raw,bus=ide --disk path=target.qcow2,format=qcow2,bus=ide --network network=default,model=e1000 --graphics vnc,listen=127.0.0.1 --video vga --noautoconsole --print-xml > workstation.xml
# Review workstation.xml before virsh define workstation.xml and virsh start Deep32Workstation.
# Install to the empty second disk only. Shut down, detach installer.img, put target first, attach signed disc2 as a read-only CD-ROM.
# No VM is created or started by deep32-get.
"""
        (staging / "commands.txt").write_text(commands, encoding="ascii", newline="\n")
        (staging / "recipe.json").write_text(json.dumps({"schema": "deep32.vm-recipe.v1", "hypervisor": hypervisor,
            "maturity": "awaiting-clean-host-boot-test", "installer_sha256": digest,
            "installer_bytes": int(size), "target_minimum_bytes": 160_000_000_000,
            "firmware": "legacy-bios", "network": "wired-virtual-nic-with-dhcp",
            "content": "signed-disc2-read-only-optical-after-install", "starts_vm": False}, sort_keys=True, indent=2) + "\n", encoding="ascii")
        # mkdir without exist_ok prevents overwriting another completed recipe.
        target.mkdir()
        for item in sorted(staging.iterdir()):
            publish(item, target / item.name)
    print("VM artefacts written: %s; review commands.txt. Host boot qualification is pending." % target)


def verify_selected(argv):
    parser = argparse.ArgumentParser(description="Verify one selected disc; complete-release mode is unchanged")
    parser.add_argument("directory")
    parser.add_argument("fingerprint")
    parser.add_argument("--disc", required=True, choices=sorted(ROLES))
    parser.add_argument("--key", default=str(Path(__file__).parents[2] / "install/release-key.gpg"))
    parser.add_argument("--gpg", default="")
    parser.add_argument("--release", default="v0.1.0-alpha.1")
    args = parser.parse_args(argv)
    folder = path(args.directory, "dir")
    authenticate(folder, args.key, args.fingerprint, args.gpg)
    image, assets = selected_records(folder / "ASSEMBLY.json", folder / "SHA256SUMS", args.disc, args.release)
    # A downloader's cache may have its joined image beside it. One complete
    # selected payload set is mandatory; a joined image alone cannot hide parts.
    allowed = set(read_sums(folder / 'SHA256SUMS')) | {"SHA256SUMS", "SHA256SUMS.sig", image[1]}
    for _, name, size, digest in assets:
        check_file(folder / name, size, digest)
        allowed.add(name)
    if (folder / image[1]).exists():
        check_file(folder / image[1], image[2], image[3])
    for child in folder.iterdir():
        path(child, "file")
        if child.name not in allowed:
            raise Refusal("Selected-disc directory contains an unexpected entry")
    print("Selected %s signatures, manifest and payloads verified; scope is not the complete release." % args.disc)


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
    elif command == "authenticate":
        authenticate(*args)
    elif command == "storage":
        print_storage(*args)
    elif command == "prepare-vm":
        prepare_vm(*args)
    elif command == 'publication-check':
        publication_check(*args)
    elif command == "verify-selected":
        verify_selected(args)
    elif command == "verify-whole":
        verify_whole(args)
    elif command == "sign-preflight":
        verify_whole(args, signing=True)
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
    except NeedsAction as error:
        print(str(error), file=sys.stderr)
        sys.exit(10)
    except (Refusal, OSError, ValueError, TypeError, UnicodeError, KeyError, RecursionError) as error:
        print("Download refused: %s" % error, file=sys.stderr)
        sys.exit(1)
