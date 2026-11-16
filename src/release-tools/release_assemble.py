#!/usr/bin/env python3
"""Assemble declared disc images into a deterministic, unsigned release folder.

Python 3.10 or later; standard library only. No signing, network or upload.
Input checksum files must declare exactly one image each. See RELEASE-LAYOUT.md
for the transport format and its relationship to the existing C2 publisher.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


MAX_PART = 1_900_000_000
BUFFER = 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
CHECKSUM = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,199})\Z")
USER_TOOLS = ("join_and_verify.sh", "join_and_verify.ps1",
              "verify-release.sh", "verify-release.ps1",
              "deep32-get.sh", "deep32-get.ps1", "deep32_get_manifest.py")
ROLES = (("disc1", "workstation"), ("disc2", "content"), ("disc3", "gpu-pack"))


class Refusal(Exception):
    """A refused input or failed read-back check."""


def safe_name(name):
    return bool(NAME.fullmatch(name)) and ".." not in name


def plain_path(path, *, directory=False):
    """Reject symlinks and Windows reparse points, including ancestors."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("Links and reparse points are refused")
    mode = path.stat().st_mode
    if directory and not stat.S_ISDIR(mode):
        raise Refusal("A required directory is not a directory")
    if not directory and not stat.S_ISREG(mode):
        raise Refusal("A declared input is not a regular file")
    return path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BUFFER), b""):
            digest.update(block)
    return digest.hexdigest()


def input_disc(image_arg, sums_arg, role, label, version, arch, part_size):
    image = plain_path(image_arg)
    sums = plain_path(sums_arg)
    if image == sums or not safe_name(image.name):
        raise Refusal("Each image needs a safe basename and a separate checksum file")
    if sums.stat().st_size > 8192:
        raise Refusal("An input checksum file is too large")
    try:
        raw = sums.read_bytes().decode("ascii")
    except UnicodeError as exc:
        raise Refusal("An input checksum file is not ASCII") from exc
    lines = raw.splitlines()
    if len(lines) != 1:
        raise Refusal("Each input checksum file must declare exactly one image")
    match = CHECKSUM.fullmatch(lines[0])
    if not match or not safe_name(match[2]):
        raise Refusal("An input checksum line is malformed")
    declared = plain_path(sums.parent / match[2])
    if declared != image:
        raise Refusal("An input image is outside its declared checksum output")
    suffix = image.suffix.lower()
    if suffix not in ((".img", ".iso") if role == "disc1" else (".iso",)):
        raise Refusal("Disc 1 requires .img or .iso; discs 2 and 3 require .iso")
    size = image.stat().st_size
    if size < 1:
        raise Refusal("An input image is empty")
    if sha256(image) != match[1]:
        raise Refusal("An input image does not match its own SHA256SUMS")
    prefix = "deep32-%s-%s-%s" % (version, role, label)
    if role == "disc1":
        prefix += "-" + arch
    name = prefix + suffix
    if not safe_name(name + ".part999"):
        raise Refusal("A generated image name is too long")
    count = (size + part_size - 1) // part_size if size > part_size else 1
    if count > 999:
        raise Refusal("An image needs more than 999 parts; increase --part-size-bytes")
    return {"source": image, "source_sums": sums, "role": role,
            "kind": label, "file": name, "prefix": prefix, "size": size,
            "sha256": match[1], "split": size > part_size, "count": count}


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    for role, _ in ROLES:
        parser.add_argument("--" + role, required=role == "disc1", metavar="IMAGE")
        parser.add_argument("--" + role + "-sums", required=role == "disc1", metavar="SHA256SUMS")
    parser.add_argument("--pending", action="append", choices=("disc2", "disc3"), default=[],
                        metavar="ROLE", help="Leave disc2 or disc3 pending; omit that disc image and sums")
    parser.add_argument("--output", required=True, metavar="NEW_DIRECTORY")
    parser.add_argument("--version", default="0.1.0-alpha.1")
    parser.add_argument("--arch", default="amd64")
    parser.add_argument("--part-size-bytes", type=int, default=MAX_PART)
    return parser


def assemble(args):
    for value in (args.version, args.arch):
        if not IDENTIFIER.fullmatch(value) or ".." in value:
            raise Refusal("Version and architecture must be safe identifiers")
    if not 1 <= args.part_size_bytes <= MAX_PART:
        raise Refusal("Part size must be between 1 and 1900000000 bytes")
    if len(args.pending) != len(set(args.pending)):
        raise Refusal("A pending role may be declared only once")
    pending = set(args.pending)
    if pending - {"disc2", "disc3"}:
        raise Refusal("Only disc2 and disc3 may be pending")
    for role, _ in ROLES:
        image, sums = getattr(args, role), getattr(args, role + "_sums")
        if role in pending:
            if image is not None or sums is not None:
                raise Refusal("A pending disc must not have image or checksum arguments")
        elif image is None or sums is None:
            raise Refusal("Every present disc requires its image and checksum arguments")
    # Preflight all inputs before creating even an empty release directory.
    discs = [input_disc(getattr(args, role), getattr(args, role + "_sums"),
                        role, label, args.version, args.arch, args.part_size_bytes)
             for role, label in ROLES if role not in pending]
    if len({os.path.normcase(str(d["source"])) for d in discs}) != len(discs):
        raise Refusal("Each disc must declare a distinct input image")
    planned_assets = sum(d["count"] + 2 + int(d["split"]) for d in discs) + len(USER_TOOLS) + 4 + len(discs)
    if planned_assets > 1000:
        raise Refusal("The assembly exceeds the 1000-assets-per-release budget")
    out = Path(os.path.abspath(args.output))
    plain_path(out.parent, directory=True)
    if os.path.lexists(out):
        raise Refusal("The release output directory already exists")
    for disc in discs:
        for source_parent in (disc["source"].parent, disc["source_sums"].parent):
            if out == source_parent or source_parent in out.parents:
                raise Refusal("The release directory must be outside all input directories")
    tool_sources = []
    for name in USER_TOOLS:
        source = plain_path(Path(__file__).absolute().parent / name)
        data = source.read_bytes()
        if not data or len(data) > 1024 * 1024 or b"\r" in data or not data.isascii():
            raise Refusal("A user tool must be non-empty ASCII with LF line endings")
        tool_sources.append((name, data))

    created = []
    out.mkdir()  # exclusive; never replace or clean an existing release

    def write_bytes(name, data):
        if not safe_name(name):
            raise Refusal("An output name is unsafe")
        path = out / name
        with path.open("xb") as stream:
            created.append(path)
            stream.write(data)
        return name

    def write_text(name, text):
        return write_bytes(name, text.encode("ascii"))

    def sums_text(names):
        return "".join("%s  %s\n" % (sha256(out / n), n) for n in sorted(names))

    try:
        public_discs = []
        for disc in discs:
            members = []
            parts = []
            whole = hashlib.sha256()
            total = 0
            # Recheck immediately before reading: no directory sweeps, no links.
            plain_path(disc["source"])
            with disc["source"].open("rb") as src:
                for index in range(1, disc["count"] + 1):
                    name = ("%s.part%03d" % (disc["file"], index)
                            if disc["split"] else disc["file"])
                    want = min(args.part_size_bytes, disc["size"] - total)
                    digest = hashlib.sha256()
                    size = 0
                    path = out / name
                    with path.open("xb") as dest:
                        created.append(path)
                        while size < want:
                            data = src.read(min(BUFFER, want - size))
                            if not data:
                                raise Refusal("An image shrank during assembly")
                            dest.write(data)
                            digest.update(data)
                            whole.update(data)
                            size += len(data)
                    total += size
                    if sha256(path) != digest.hexdigest() or path.stat().st_size != want:
                        raise Refusal("A written payload failed its read-back check")
                    members.append(name)
                    parts.append({"name": name, "size": size, "sha256": digest.hexdigest()})
                if src.read(1) or total != disc["size"] or whole.hexdigest() != disc["sha256"]:
                    raise Refusal("An image changed during assembly")
            manifest = None
            if disc["split"]:
                manifest = disc["file"] + ".parts.tsv"
                text = "deep32-parts-v1\nfile\t%s\t%d\t%s\n" % (
                    disc["file"], disc["size"], disc["sha256"])
                text += "".join("part\t%s\t%d\t%s\n" % (p["name"], p["size"], p["sha256"])
                                for p in parts)
                members.append(write_text(manifest, text))
            # Mechanical inventory only. Editorial release text is left to the lead.
            readme = disc["prefix"] + "-README.md"
            members.append(write_text(readme, "# %s\n\nImage: %s\n\nBytes: %d\n\nSHA256: %s\n\n"
                                      "%s\n" % (
                                          disc["kind"], disc["file"], disc["size"], disc["sha256"],
                                          {"disc1": "Disc 1 is the Deep32 Workstation image. ",
                                           "disc2": "Disc 2 is the Deep32 content image. ",
                                           "disc3": "Disc 3 is the Deep32 GPU pack image. "}[disc["role"]] +
                                          "Installation instructions are in INSTALL.md at the root of the source release; "
                                          "notices are in notices/NOTICE and notices/THIRD-PARTY-NOTICES.md there.")))
            sums = disc["prefix"] + "-SHA256SUMS"
            write_text(sums, sums_text(members))
            public_discs.append({"role": disc["role"], "kind": disc["kind"],
                                 "file": disc["file"], "size": disc["size"],
                                 "sha256": disc["sha256"], "split": disc["split"],
                                 "parts_manifest": manifest, "payloads": parts,
                                 "checksums": sums, "readme": readme})
        for name, data in tool_sources:
            write_bytes(name, data)
        metadata = {"format": "deep32-release-assembly", "format_version": 1,
                    "version": args.version, "architecture": args.arch,
                    "part_size_bytes": args.part_size_bytes, "discs": public_discs}
        if pending:
            metadata["pending"] = sorted(pending)
            metadata["pending_prefixes"] = {
                role: "deep32-%s-%s-%s" % (args.version, role, label)
                for role, label in ROLES if role in pending}
        write_text("ASSEMBLY.json", json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        static = [p.name for p in created] + ["ASSETS.txt", "SHA256SUMS"]
        signatures = ["SHA256SUMS.sig"] + [d["checksums"] + ".sig" for d in public_discs]
        assets = sorted(static + signatures)
        if len(assets) > 1000:
            raise Refusal("The assembly exceeds the 1000-assets-per-release budget")
        write_text("ASSETS.txt", "".join(name + "\n" for name in assets))
        write_text("SHA256SUMS", sums_text([p.name for p in created]))
        if pending:
            print("Assembled %d %s; %s pending; %d release assets including %d pending signatures." % (
                len(discs), "disc" if len(discs) == 1 else "discs",
                ", ".join(sorted(pending)), len(assets), len(signatures)))
            print("Unsigned assembly: review ASSETS.txt, sign the %d checksum files, then verify." % len(signatures))
        else:
            print("Assembled three discs; %d release assets including four pending signatures." % len(assets))
            print("Unsigned assembly: review ASSETS.txt, sign the four checksum files, then verify.")
        return 0
    except BaseException:
        # Remove only files created by this run. Never sweep a pre-existing tree.
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            out.rmdir()
        except OSError:
            pass
        raise


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        return assemble(args)
    except (Refusal, OSError, ValueError) as exc:
        print("Assembly refused: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
