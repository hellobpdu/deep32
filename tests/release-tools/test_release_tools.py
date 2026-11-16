#!/usr/bin/env python3
"""Offline release-tool regression tests; Python 3.10+, standard library only.

Run with an isolated Python interpreter, --tools-dir, and an empty task-owned
--scratch-root. Windows tests use Git for Windows Bash and PowerShell 5.1.
Synthetic signing keys exist only in the scratch run and are removed on exit.
--baseline-only exercises the existing three-disc contract without pending cases.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


PENDING = "{role} is not in this release yet; it will be added to the same release. Check the release page."
PENDING_VERIFY = "{role}: pending (not in this release yet)"
VERSION = "0.1.0-alpha.1"
TOOLS = ("release_assemble.py", "deep32_get_manifest.py", "deep32-get.sh",
         "deep32-get.ps1", "verify-release.sh", "verify-release.ps1",
         "join_and_verify.sh", "join_and_verify.ps1")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def msys(path):
    value = str(path).replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if re.match(r"^[A-Za-z]:/", value) else value


def text(path, value):
    path.write_bytes(value.encode("ascii"))


class Suite:
    def __init__(self, args):
        self.args = args
        self.tools = args.tools_dir.resolve(strict=True)
        self.scratch = args.scratch_root.resolve()
        self.scratch.mkdir(parents=True, exist_ok=True)
        if any(self.scratch.iterdir()):
            raise ValueError("The scratch root must be empty and owned by this test run")
        # Native GnuPG adds its own socket subdirectory. Use the explicit empty
        # root directly to keep its socket paths within the Windows limit.
        self.root = self.scratch
        self.checks = []
        self.commands = []
        self.env = os.environ.copy()
        self.home = self.root / "k"
        self.home.mkdir()
        self.key = self.root / "synthetic-public.gpg"
        self.env.update(GNUPGHOME=str(self.home), TEMP=str(self.root), TMP=str(self.root),
                        RELEASE_TEST_ROOT=str(self.root), RELEASE_TEST_GPG=str(args.gpg),
                        RELEASE_TEST_PYTHON=sys.executable, RELEASE_TEST_NATIVE_GPG=str(args.keygen_gpg))
        self.shim = self.root / "shim"
        self.shim.mkdir()
        self.env["PATH"] = str(self.shim) + os.pathsep + self.env.get("PATH", "")
        self.env["RELEASE_TEST_SHIM"] = msys(self.shim)
        self.env["RELEASE_TEST_GUARD"] = str(self.root / "gpg_guard.py")
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.setup_shims()

    def setup_shims(self):
        # Guard both .sh and .ps1 calls, including old verifiers that rely on
        # GNUPGHOME. No invocation can inspect the user's default GnuPG home.
        text(self.root / "gpg_guard.py", r'''import json, os, pathlib, re, subprocess, sys
root = pathlib.Path(os.environ["RELEASE_TEST_ROOT"]).resolve(strict=True)
args = sys.argv[1:]
native = bool(args and args[0] == "--test-native")
if native:
    args.pop(0)
home = os.environ["GNUPGHOME"]
for index, value in enumerate(args):
    if value == "--homedir":
        home = args[index + 1]
    elif value.startswith("--homedir="):
        home = value.split("=", 1)[1]
if re.match(r"^/[A-Za-z]/", home):
    home = home[1].upper() + ":" + home[2:]
resolved = pathlib.Path(home).resolve()
if not resolved.is_relative_to(root):
    raise SystemExit("Refused GPG home outside synthetic scratch")
def posix(value):
    value = value.replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if re.match(r"^[A-Za-z]:/", value) else value
convert = str if native else posix
binary = os.environ["RELEASE_TEST_NATIVE_GPG"] if native else os.environ["RELEASE_TEST_GPG"]
command = [binary, "--no-options", "--homedir", convert(str(resolved))] + [convert(a) for a in args]
with (root / "gpg-audit.jsonl").open("a", encoding="ascii") as stream:
    stream.write(json.dumps(command) + "\n")
environment = os.environ.copy()
environment["GNUPGHOME"] = convert(str(resolved))
raise SystemExit(subprocess.call(command, env=environment))
''')
        guard = str(self.root / "gpg_guard.py")
        text(self.shim / "gpg.cmd", '@echo off\r\n"%RELEASE_TEST_PYTHON%" -I -B "' + guard + '" %*\r\n')
        text(self.shim / "gpg", '#!/usr/bin/env bash\nexec "$RELEASE_TEST_PYTHON" -I -B "$RELEASE_TEST_GUARD" "$@"\n')
        text(self.shim / "python3", '#!/usr/bin/env bash\nexec "$RELEASE_TEST_PYTHON" "$@"\n')

    def record(self):
        for name, value in (("commands.json", self.commands), ("checks.json", self.checks)):
            text(self.root / name, json.dumps(value, sort_keys=True, indent=2) + "\n")

    def check(self, name, condition, detail=""):
        self.checks.append({"id": name, "pass": bool(condition), "detail": detail})
        self.record()
        if not condition:
            raise AssertionError(name + ": " + detail)

    def run(self, name, argv, expected=0):
        print(name, flush=True)
        completed = subprocess.run([str(a) for a in argv], cwd=self.root, env=self.env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        record = {"id": name, "argv": [str(a) for a in argv], "exit": completed.returncode,
                  "stdout": completed.stdout, "stderr": completed.stderr}
        self.commands.append(record)
        ok = completed.returncode == expected if expected is not None else completed.returncode != 0
        self.check(name, ok, "exit %d, expected %s" % (completed.returncode, expected) +
                   ("\n" + completed.stdout + completed.stderr if not ok else ""))
        return completed

    def py(self, name, script, args=(), expected=0):
        return self.run(name, [sys.executable, "-I", "-B", script, *args], expected)

    def gpg(self, name, args):
        return self.py(name, self.root / "gpg_guard.py", args)

    def shell(self, name, script, args=(), expected=0):
        converted = [msys(a) if isinstance(a, Path) else str(a) for a in args]
        return self.run(name, [self.args.bash, "--noprofile", "--norc", "-c",
                              'export PATH="$RELEASE_TEST_SHIM:$PATH"; test "$(command -v gpg)" = "$RELEASE_TEST_SHIM/gpg" || exit 97; source "$1" "${@:2}"',
                              "--", msys(script), *converted], expected)

    def ps(self, name, script, args=(), expected=0):
        return self.run(name, [self.args.powershell, "-NoProfile", "-NonInteractive",
                              "-ExecutionPolicy", "Bypass", "-File", script, *args], expected)

    def setup(self):
        self.gpg("generate-synthetic-key", ["--test-native", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                                        "--quick-generate-key", "Release tools TEST ONLY", "ed25519", "sign", "0"])
        result = self.gpg("list-synthetic-fingerprint", ["--test-native", "--batch", "--with-colons", "--list-keys"])
        self.fingerprint = next(line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:"))
        self.gpg("export-synthetic-public-key", ["--test-native", "--batch", "--output", self.key, "--export", self.fingerprint])
        for number, size, extension in ((1, 2001, ".img"), (2, 4096, ".iso"), (3, 10003, ".iso")):
            image = self.inputs / ("disc%d" % number + extension)
            image.write_bytes(bytes((index * (number * 2 + 1) + number) % 256 for index in range(size)))
            text(self.inputs / ("disc%d.sums" % number), "%s  %s\n" % (digest(image), image.name))
        self.source_hashes = {name: digest(self.tools / name) for name in TOOLS}

    def assemble_args(self, out, pending=False, version="1.0"):
        pending = ("disc2",) if pending is True else tuple(pending or ())
        result = ["--output", out, "--part-size-bytes", "4096", "--version", version]
        for number, extension in ((1, ".img"), (2, ".iso"), (3, ".iso")):
            if "disc%d" % number in pending:
                continue
            result += ["--disc%d" % number, self.inputs / ("disc%d" % number + extension),
                       "--disc%d-sums" % number, self.inputs / ("disc%d.sums" % number)]
        for role in pending:
            result += ["--pending", role]
        return result

    def assemble(self, name, pending=False, version="1.0"):
        out = self.root / name
        self.py(name + "-assemble", self.tools / "release_assemble.py", self.assemble_args(out, pending, version))
        metadata = json.loads((out / "ASSEMBLY.json").read_text(encoding="ascii"))
        descriptions = {"disc1": "Disc 1 is the Deep32 Workstation image. ",
                        "disc2": "Disc 2 is the Deep32 content image. ",
                        "disc3": "Disc 3 is the Deep32 GPU pack image. "}
        for disc in metadata["discs"]:
            expected = ("# %s\n\nImage: %s\n\nBytes: %d\n\nSHA256: %s\n\n" % (
                disc["kind"], disc["file"], disc["size"], disc["sha256"]) +
                descriptions[disc["role"]] +
                "Installation instructions are in INSTALL.md at the root of the source release; "
                "notices are in notices/NOTICE and notices/THIRD-PARTY-NOTICES.md there.\n")
            self.check(name + "-" + disc["role"] + "-readme-bytes",
                       (out / disc["readme"]).read_bytes() == expected.encode("ascii"))
        return out

    def sign(self, release, name):
        for index, sums in enumerate(sorted(release.glob("*SHA256SUMS"))):
            self.gpg(name + "-sign-%d" % index, ["--test-native", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
                      "--local-user", self.fingerprint, "--no-textmode", "--armor", "--detach-sign", "--output", str(sums) + ".sig", sums])

    def verify(self, name, release, expected=0, fingerprint=None):
        result = []
        fingerprint = fingerprint or self.fingerprint
        result.append(self.shell(name + "-sh", self.tools / "verify-release.sh", [release, fingerprint], expected))
        result.append(self.ps(name + "-ps1", self.tools / "verify-release.ps1", ["-ReleaseDirectory", release, "-Fingerprint", fingerprint], expected))
        return result

    def download(self, name, release, role, version="1.0", expected=0):
        results = []
        for kind in ("sh", "ps1"):
            out = self.root / (name + "-" + kind + "-download")
            args = [role, "--to", out, "--source", release, "--release", "v" + version, "--key", self.key]
            # Some previously shipped helpers also require the explicit test pin.
            script_text = (self.tools / ("deep32-get." + kind)).read_text(encoding="ascii")
            if re.search(r"--fingerprint(?:\)|'\s*\{)", script_text):
                args += ["--fingerprint", self.fingerprint]
            method = self.shell if kind == "sh" else self.ps
            completed = method(name + "-" + kind, self.tools / ("deep32-get." + kind), args, expected)
            results.append(completed)
            if expected == 0:
                metadata = json.loads((release / "ASSEMBLY.json").read_text(encoding="ascii"))
                disc = next(item for item in metadata["discs"] if item["role"] == role)
                image = out / disc["file"]
                self.check(name + "-" + kind + "-image", image.is_file() and digest(image) == disc["sha256"])
            elif expected == 3:
                combined = (completed.stdout + completed.stderr).strip()
                self.check(name + "-" + kind + "-pending-message", combined == PENDING.format(role=role), combined)
                self.check(name + "-" + kind + "-no-image", not any(out.glob("*.img")) and not any(out.glob("*.iso")))
        return results

    def rewrite_sums(self, release):
        names = [line.split("  ", 1)[1] for line in (release / "SHA256SUMS").read_text(encoding="ascii").splitlines()]
        text(release / "SHA256SUMS", "".join("%s  %s\n" % (digest(release / name), name) for name in names))
        self.sign(release, release.name)

    def pending_install(self, name, release):
        # Exercise the actual entry points while replacing every installation
        # gateway with a harmless guard. A regression can only create a marker,
        # never invoke a platform installer, mount operation or WSL command.
        for kind in ("sh", "ps1"):
            out = self.root / (name + "-" + kind + "-download")
            marker = self.root / (name + "-" + kind + "-install-called")
            args = ["disc3", "--install", "--to", out, "--source", release,
                    "--release", "v" + VERSION, "--key", self.key]
            if kind == "sh":
                guard = self.root / (name + "-install-guard.sh")
                text(guard, '''#!/usr/bin/env bash
guard_called() { printf '%s\\n' "$1" >> "$RELEASE_TEST_INSTALL_MARKER"; return 97; }
id() { guard_called id; printf '%s\\n' 65534; }
mount() { guard_called mount; }
umount() { guard_called umount; }
bash() { guard_called bash; }
wsl.exe() { guard_called wsl.exe; }
export PATH="$RELEASE_TEST_SHIM:$PATH"
test "$(command -v gpg)" = "$RELEASE_TEST_SHIM/gpg" || exit 97
source "$1" "${@:2}"
''')
                self.env["RELEASE_TEST_INSTALL_MARKER"] = msys(marker)
                completed = self.run(name + "-sh", [self.args.bash, "--noprofile", "--norc", msys(guard),
                    msys(self.tools / "deep32-get.sh"), *[msys(a) if isinstance(a, Path) else a for a in args]], 3)
            else:
                guard = self.root / (name + "-install-guard.ps1")
                text(guard, '''param([string]$Tool, [string]$Marker, [string[]]$Remaining)
. $Tool
function Set-InstallGuard { param([string]$Name); [IO.File]::AppendAllText($Marker, $Name + "`n"); throw 'Synthetic installation guard reached.' }
function Invoke-Deep32Install { Set-InstallGuard 'Invoke-Deep32Install' }
function Invoke-Deep32Wsl { Set-InstallGuard 'Invoke-Deep32Wsl' }
function Mount-DiskImage { Set-InstallGuard 'Mount-DiskImage' }
function Get-DiskImage { Set-InstallGuard 'Get-DiskImage' }
function Dismount-DiskImage { Set-InstallGuard 'Dismount-DiskImage' }
function wsl.exe { Set-InstallGuard 'wsl.exe' }
function Get-Command {
    param([string]$Name, [string]$ErrorAction = 'Stop')
    if ($Name -in @('Mount-DiskImage', 'Get-DiskImage', 'Dismount-DiskImage', 'wsl.exe')) { Set-InstallGuard ('Get-Command ' + $Name) }
    Microsoft.PowerShell.Core\\Get-Command -Name $Name -ErrorAction $ErrorAction
}
try { Invoke-Deep32Main -Arguments $Remaining }
catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
''')
                # -File binds a string[] named parameter differently from a
                # trailing argv array. Pass the data through a generated JSON
                # file, then use a small wrapper to keep every path literal.
                invocation = self.root / (name + "-install-args.json")
                text(invocation, json.dumps([str(a) for a in args]) + "\n")
                wrapper = self.root / (name + "-install-run.ps1")
                text(wrapper, '''$values = Get-Content -LiteralPath $env:RELEASE_TEST_INSTALL_ARGS -Raw | ConvertFrom-Json
& $env:RELEASE_TEST_INSTALL_GUARD -Tool $env:RELEASE_TEST_INSTALL_TOOL -Marker $env:RELEASE_TEST_INSTALL_MARKER -Remaining $values
exit $LASTEXITCODE
''')
                self.env.update(RELEASE_TEST_INSTALL_ARGS=str(invocation), RELEASE_TEST_INSTALL_GUARD=str(guard),
                                RELEASE_TEST_INSTALL_TOOL=str(self.tools / "deep32-get.ps1"), RELEASE_TEST_INSTALL_MARKER=str(marker))
                completed = self.ps(name + "-ps1", wrapper, expected=3)
            combined = (completed.stdout + completed.stderr).strip()
            self.check(name + "-" + kind + "-sole-pending-line", combined == PENDING.format(role="disc3"), combined)
            self.check(name + "-" + kind + "-no-install-or-prerequisite", not marker.exists())
            self.check(name + "-" + kind + "-no-image", not any(out.glob("*.img")) and not any(out.glob("*.iso")))

    def mutate(self, release, name, change):
        out = self.root / name
        shutil.copytree(release, out)
        change(out)
        self.rewrite_sums(out)
        return out

    def baseline(self):
        release = self.assemble("three-disc")
        data = json.loads((release / "ASSEMBLY.json").read_text(encoding="ascii"))
        self.check("three-disc-roles", [item["role"] for item in data["discs"]] == ["disc1", "disc2", "disc3"])
        self.check("three-disc-metadata-unchanged", "pending" not in data and "pending_prefixes" not in data)
        self.check("three-disc-split-boundary", [item["split"] for item in data["discs"]] == [False, False, True])
        self.sign(release, "three-disc")
        self.verify("three-disc-verify", release)
        for role in ("disc1", "disc2", "disc3"):
            self.download("three-disc-get-" + role, release, role)
        disc3 = data["discs"][2]
        for kind in ("sh", "ps1"):
            out = self.root / ("join-" + kind)
            out.mkdir()
            args = [release / disc3["parts_manifest"], out] if kind == "sh" else ["-Manifest", release / disc3["parts_manifest"], "-OutputDirectory", out]
            method = self.shell if kind == "sh" else self.ps
            method("three-disc-join-" + kind, self.tools / ("join_and_verify." + kind), args)
            self.check("three-disc-join-" + kind + "-image", digest(out / disc3["file"]) == disc3["sha256"])
        corrupt = self.root / "corrupt-present"
        shutil.copytree(release, corrupt)
        image = corrupt / data["discs"][0]["file"]
        image.write_bytes(b"X" + image.read_bytes()[1:])
        self.verify("corrupt-present-refused", corrupt, None)
        self.download("corrupt-present-get-refused", corrupt, "disc1", expected=None)
        bad = self.root / "malformed-assembly"
        shutil.copytree(release, bad)
        text(bad / "ASSEMBLY.json", "{malformed\n")
        self.rewrite_sums(bad)
        self.download("malformed-assembly-refused", bad, "disc1", expected=None)
        invalid = self.assemble_args(self.root / "bad-input-must-not-exist")
        sums = self.inputs / "disc1.sums"
        saved = sums.read_bytes()
        text(sums, "0" * 64 + "  disc1.img\n")
        try:
            self.py("present-input-checksum-refused", self.tools / "release_assemble.py", invalid, None)
            self.check("present-input-refusal-no-output", not (self.root / "bad-input-must-not-exist").exists())
        finally:
            sums.write_bytes(saved)
        return release

    def pending(self):
        release = self.assemble("partial-alpha", pending=True, version=VERSION)
        data = json.loads((release / "ASSEMBLY.json").read_text(encoding="ascii"))
        prefix = "deep32-" + VERSION + "-disc2-content"
        self.check("partial-roles", [item["role"] for item in data["discs"]] == ["disc1", "disc3"])
        self.check("partial-pending-metadata", data.get("pending") == ["disc2"] and data.get("pending_prefixes") == {"disc2": prefix})
        self.check("partial-no-disc2-assets", not any("disc2" in path.name for path in release.iterdir()))
        self.check("partial-alpha-disc1-name", data["discs"][0]["file"] == "deep32-" + VERSION + "-disc1-workstation-amd64.img")
        self.sign(release, "partial")
        for result in self.verify("partial-verify", release):
            self.check("partial-pending-verifier-message", PENDING_VERIFY.format(role="disc2") in result.stdout.splitlines(), result.stdout)
        self.download("partial-get-pending", release, "disc2", VERSION, 3)
        for role in ("disc1", "disc3"):
            self.download("partial-get-" + role, release, role, VERSION)
        added = self.assemble("later-all-three-alpha", version=VERSION)
        self.sign(added, "later-all-three")
        self.verify("later-all-three-verify", added)
        self.download("later-all-three-get-disc2", added, "disc2", VERSION)
        self.check("later-new-folder-keeps-partial", json.loads((release / "ASSEMBLY.json").read_text(encoding="ascii")) == data)
        for disc in data["discs"]:
            self.check("later-unchanged-" + disc["role"] + "-sums", (added / disc["checksums"]).read_bytes() == (release / disc["checksums"]).read_bytes())
        # Malformed pending metadata is re-signed, so validation must refuse it
        # after authenticating a genuinely valid synthetic signature.
        cases = {
            "pending-wrong-role": lambda value: value.update(pending=["disc1"]),
            "pending-duplicate": lambda value: value.update(pending=["disc2", "disc2"]),
            "pending-wrong-prefix": lambda value: value.update(pending_prefixes={"disc2": "wrong-content"}),
            "pending-no-prefix": lambda value: value.pop("pending_prefixes"),
            "pending-wrong-type": lambda value: value.update(pending="disc2"),
            "pending-missing-present": lambda value: value["discs"].pop(),
            "pending-overlap": lambda value: value["discs"].append(json.loads((added / "ASSEMBLY.json").read_text(encoding="ascii"))["discs"][1]),
        }
        for name, change in cases.items():
            def modify(out, change=change):
                metadata = json.loads((out / "ASSEMBLY.json").read_text(encoding="ascii"))
                change(metadata)
                text(out / "ASSEMBLY.json", json.dumps(metadata, sort_keys=True, indent=2) + "\n")
            bad = self.mutate(release, name, modify)
            self.verify(name + "-verify-refused", bad, None)
            self.download(name + "-get-refused", bad, "disc1", VERSION, None)
        for case, asset in (("pending-root-asset", prefix + "-README.md"),
                            ("pending-root-asset-uppercase", prefix.upper() + "-README.md")):
            def unexpected(out, asset=asset):
                text(out / asset, "Synthetic pending asset must be refused.\n")
                with (out / "SHA256SUMS").open("ab") as stream:
                    stream.write(("%s  %s\n" % (digest(out / asset), asset)).encode("ascii"))
            bad = self.mutate(release, case, unexpected)
            self.verify(case + "-verify-refused", bad, None)
            self.download(case + "-get-refused", bad, "disc1", VERSION, None)
        self.verify("partial-wrong-fingerprint-refused", release, None, "0" * 40)
        invalid_signature = self.root / "partial-invalid-signature"
        shutil.copytree(release, invalid_signature)
        text(invalid_signature / "SHA256SUMS.sig", "This is not a valid synthetic signature.\n")
        self.verify("partial-invalid-signature-refused", invalid_signature, None)
        self.download("partial-invalid-signature-get-refused", invalid_signature, "disc1", VERSION, None)
        for role in ("disc1", "unknown", "disc2"):
            args = self.assemble_args(self.root / ("pending-" + role + "-must-not-exist"), True, VERSION)
            args += ["--pending", role]
            self.py("pending-" + role + "-assembler-refused", self.tools / "release_assemble.py", args, None)
        for case, extra in (("image-only", ["--disc2", self.inputs / "disc2.iso"]),
                            ("sums-only", ["--disc2-sums", self.inputs / "disc2.sums"]),
                            ("both", ["--disc2", self.inputs / "disc2.iso", "--disc2-sums", self.inputs / "disc2.sums"])):
            out = self.root / ("pending-with-" + case + "-must-not-exist")
            args = self.assemble_args(out, True, VERSION) + extra
            self.py("pending-with-" + case + "-refused", self.tools / "release_assemble.py", args, None)
            self.check("pending-with-" + case + "-no-output", not out.exists())
        args = self.assemble_args(self.root / "missing-disc2-must-not-exist", True, VERSION)[:-2]
        self.py("missing-disc2-without-pending-refused", self.tools / "release_assemble.py", args, None)
        # Present inputs retain their preflight refusals with disc2 pending.
        sums = self.inputs / "disc1.sums"
        saved = sums.read_bytes()
        text(sums, "0" * 64 + "  disc1.img\n")
        try:
            self.py("partial-bad-present-input-refused", self.tools / "release_assemble.py",
                    self.assemble_args(self.root / "partial-bad-input-must-not-exist", True, VERSION), None)
        finally:
            sums.write_bytes(saved)
        duplicate = self.assemble_args(self.root / "duplicate-source-must-not-exist", True, VERSION)
        duplicate[duplicate.index("--disc1") + 1] = self.inputs / "disc3.iso"
        duplicate[duplicate.index("--disc1-sums") + 1] = self.inputs / "disc3.sums"
        self.py("partial-duplicate-source-refused", self.tools / "release_assemble.py", duplicate, None)
        unsupported = self.inputs / "unsupported.bin"
        unsupported.write_bytes(b"Synthetic unsupported suffix")
        text(self.inputs / "unsupported.sums", digest(unsupported) + "  unsupported.bin\n")
        bad_suffix = self.assemble_args(self.root / "unsupported-must-not-exist", True, VERSION)
        bad_suffix[bad_suffix.index("--disc1") + 1] = unsupported
        bad_suffix[bad_suffix.index("--disc1-sums") + 1] = self.inputs / "unsupported.sums"
        self.py("partial-unsupported-suffix-refused", self.tools / "release_assemble.py", bad_suffix, None)
        existing = self.root / "existing-output"
        existing.mkdir()
        text(existing / "sentinel.txt", "Keep this exact file.\n")
        self.py("partial-existing-output-refused", self.tools / "release_assemble.py", self.assemble_args(existing, True, VERSION), None)
        self.check("partial-existing-output-preserved", sorted(path.name for path in existing.iterdir()) == ["sentinel.txt"] and
                   (existing / "sentinel.txt").read_bytes() == b"Keep this exact file.\n")
        nested = self.inputs / "nested-output-must-not-exist"
        self.py("partial-nested-output-refused", self.tools / "release_assemble.py", self.assemble_args(nested, True, VERSION), None)
        self.check("partial-nested-output-not-created", not nested.exists())
        corrupt = self.root / "partial-corrupt-present"
        shutil.copytree(release, corrupt)
        image = corrupt / data["discs"][0]["file"]
        image.write_bytes(b"X" + image.read_bytes()[1:])
        self.verify("partial-corrupt-present-refused", corrupt, None)
        self.download("partial-corrupt-present-get-refused", corrupt, "disc1", VERSION, None)
        missing = self.root / "partial-missing-present"
        shutil.copytree(release, missing)
        (missing / data["discs"][1]["payloads"][0]["name"]).unlink()
        self.verify("partial-missing-present-refused", missing, None)
        self.download("partial-missing-present-get-refused", missing, "disc3", VERSION, None)

    def more_pending(self):
        releases = {}
        for name, roles, present in (("disc3-pending", ("disc3",), ["disc1", "disc2"]),
                                     ("one-disc", ("disc2", "disc3"), ["disc1"])):
            release = self.assemble(name, roles, VERSION)
            releases[name] = release
            data = json.loads((release / "ASSEMBLY.json").read_text(encoding="ascii"))
            prefixes = {role: "deep32-" + VERSION + "-" + role + suffix
                        for role, suffix in (("disc2", "-content"), ("disc3", "-gpu-pack")) if role in roles}
            self.check(name + "-roles", [item["role"] for item in data["discs"]] == present)
            self.check(name + "-pending-metadata", data.get("pending") == list(roles) and data.get("pending_prefixes") == prefixes,
                       json.dumps(data.get("pending_prefixes"), sort_keys=True))
            self.check(name + "-no-pending-assets", not any(any(role in path.name.lower() for role in roles) for path in release.iterdir()))
            # Before signatures introduce random creation times, both repeat
            # assemblies and reversed flag order must produce identical bytes.
            again = self.assemble(name + "-repeat", roles, VERSION)
            reverse = self.assemble(name + "-reverse", tuple(reversed(roles)), VERSION)
            expected = {path.name: digest(path) for path in release.iterdir()}
            for variant, candidate in (("repeat", again), ("reverse", reverse)):
                self.check(name + "-" + variant + "-canonical-bytes", expected == {path.name: digest(path) for path in candidate.iterdir()})
            self.sign(release, name)
            for index, result in enumerate(self.verify(name + "-verify", release)):
                notices = [line for line in result.stdout.splitlines() if ": pending " in line]
                self.check(name + "-verify-pending-notices-" + str(index), notices == [PENDING_VERIFY.format(role=role) for role in roles], result.stdout)
            for role in roles:
                self.download(name + "-get-" + role, release, role, VERSION, 3)
            self.pending_install(name + "-get-disc3-install", release)
            for role in present:
                self.download(name + "-get-present-" + role, release, role, VERSION)

        # Disc3 pending is now a positive contract, while supplied disc3 input
        # or checksums remain a conflict with the pending declaration.
        for case, extra in (("image-only", ["--disc3", self.inputs / "disc3.iso"]),
                            ("sums-only", ["--disc3-sums", self.inputs / "disc3.sums"]),
                            ("both", ["--disc3", self.inputs / "disc3.iso", "--disc3-sums", self.inputs / "disc3.sums"])):
            out = self.root / ("disc3-pending-with-" + case + "-must-not-exist")
            self.py("disc3-pending-with-" + case + "-refused", self.tools / "release_assemble.py",
                    self.assemble_args(out, ("disc3",), VERSION) + extra, None)
            self.check("disc3-pending-with-" + case + "-no-output", not out.exists())

        release = releases["one-disc"]
        full = json.loads((self.root / "later-all-three-alpha" / "ASSEMBLY.json").read_text(encoding="ascii"))
        cases = {
            "one-disc-pending-duplicate-disc3": lambda value: value.update(pending=["disc2", "disc3", "disc3"]),
            "one-disc-pending-unsupported": lambda value: value.update(pending=["disc2", "disc4"]),
            "one-disc-pending-disc1": lambda value: value.update(pending=["disc1", "disc2", "disc3"]),
            "one-disc-pending-disc3-wrong-prefix": lambda value: value["pending_prefixes"].update(disc3="wrong-gpu-pack"),
            "one-disc-pending-disc3-no-prefix": lambda value: value["pending_prefixes"].pop("disc3"),
            "one-disc-pending-disc2-overlap": lambda value: value["discs"].append(full["discs"][1]),
            "one-disc-pending-disc3-overlap": lambda value: value["discs"].append(full["discs"][2]),
            "one-disc-required-disc1": lambda value: value.update(discs=[]),
        }
        for name, change in cases.items():
            def modify(out, change=change):
                metadata = json.loads((out / "ASSEMBLY.json").read_text(encoding="ascii"))
                change(metadata)
                text(out / "ASSEMBLY.json", json.dumps(metadata, sort_keys=True, indent=2) + "\n")
            bad = self.mutate(release, name, modify)
            self.verify(name + "-verify-refused", bad, None)
            self.download(name + "-get-refused", bad, "disc1", VERSION, None)
        for role, suffix in (("disc2", "-content"), ("disc3", "-gpu-pack")):
            for upper in (False, True):
                name = "one-disc-pending-asset-" + role + ("-uppercase" if upper else "")
                asset = "deep32-" + VERSION + "-" + role + suffix + "-README.md"
                asset = asset.upper() if upper else asset
                def unexpected(out, asset=asset):
                    text(out / asset, "Synthetic pending asset must be refused.\n")
                    with (out / "SHA256SUMS").open("ab") as stream:
                        stream.write(("%s  %s\n" % (digest(out / asset), asset)).encode("ascii"))
                bad = self.mutate(release, name, unexpected)
                self.verify(name + "-verify-refused", bad, None)
                self.download(name + "-get-refused", bad, "disc1", VERSION, None)
        for role in ("disc1", "disc2", "disc3", "unknown"):
            out = self.root / ("one-disc-extra-pending-" + role + "-must-not-exist")
            self.py("one-disc-extra-pending-" + role + "-refused", self.tools / "release_assemble.py",
                    self.assemble_args(out, ("disc2", "disc3"), VERSION) + ["--pending", role], None)
            self.check("one-disc-extra-pending-" + role + "-no-output", not out.exists())
        for role in ("disc1", "disc3"):
            for option in ("--" + role, "--" + role + "-sums"):
                out = self.root / ("one-disc-missing-" + option[2:] + "-must-not-exist")
                args = self.assemble_args(out, ("disc2", "disc3"), VERSION)
                if role == "disc1":
                    index = args.index(option)
                    del args[index:index + 2]
                else:
                    # Missing disc3 arguments need an explicit pending role.
                    args = [item for item in args]
                    del args[-2:]
                self.py("one-disc-missing-" + option[2:] + "-refused", self.tools / "release_assemble.py", args, None)
                self.check("one-disc-missing-" + option[2:] + "-no-output", not out.exists())
        sums = self.inputs / "disc1.sums"
        saved = sums.read_bytes()
        text(sums, "0" * 64 + "  disc1.img\n")
        try:
            out = self.root / "one-disc-bad-input-must-not-exist"
            self.py("one-disc-bad-present-input-refused", self.tools / "release_assemble.py",
                    self.assemble_args(out, ("disc2", "disc3"), VERSION), None)
            self.check("one-disc-bad-present-input-no-output", not out.exists())
        finally:
            sums.write_bytes(saved)
        bad_suffix = self.assemble_args(self.root / "one-disc-unsupported-must-not-exist", ("disc2", "disc3"), VERSION)
        bad_suffix[bad_suffix.index("--disc1") + 1] = self.inputs / "unsupported.bin"
        bad_suffix[bad_suffix.index("--disc1-sums") + 1] = self.inputs / "unsupported.sums"
        self.py("one-disc-unsupported-suffix-refused", self.tools / "release_assemble.py", bad_suffix, None)
        existing = self.root / "one-disc-existing-output"
        existing.mkdir()
        text(existing / "sentinel.txt", "Keep this exact file.\n")
        self.py("one-disc-existing-output-refused", self.tools / "release_assemble.py",
                self.assemble_args(existing, ("disc2", "disc3"), VERSION), None)
        self.check("one-disc-existing-output-preserved", sorted(path.name for path in existing.iterdir()) == ["sentinel.txt"] and
                   (existing / "sentinel.txt").read_bytes() == b"Keep this exact file.\n")
        nested = self.inputs / "one-disc-nested-must-not-exist"
        self.py("one-disc-nested-output-refused", self.tools / "release_assemble.py",
                self.assemble_args(nested, ("disc2", "disc3"), VERSION), None)
        self.check("one-disc-nested-output-not-created", not nested.exists())
        for name, remove in (("one-disc-corrupt-present", False), ("one-disc-missing-present", True)):
            bad = self.root / name
            shutil.copytree(release, bad)
            metadata = json.loads((bad / "ASSEMBLY.json").read_text(encoding="ascii"))
            image = bad / metadata["discs"][0]["file"]
            if remove:
                image.unlink()
            else:
                image.write_bytes(b"X" + image.read_bytes()[1:])
            self.verify(name + "-verify-refused", bad, None)
            self.download(name + "-get-refused", bad, "disc1", VERSION, None)

    def cleanup(self):
        # This run owns root and every home underneath it. Keep evidence and
        # harmless images, but stop agents and remove all generated key homes.
        homes = [self.home] + sorted(path for path in self.root.rglob("gpg") if path.is_dir())
        for home in homes:
            if not home.resolve().is_relative_to(self.root):
                raise RuntimeError("Unsafe cleanup target")
            subprocess.run([str(self.args.gpgconf), "--homedir", msys(home), "--kill", "all"], env=self.env,
                           capture_output=True, timeout=30)
            subprocess.run([str(self.args.keygen_gpg.with_name("gpgconf.exe")), "--homedir", str(home), "--kill", "all"],
                           env=self.env, capture_output=True, timeout=30)
            shutil.rmtree(home)
        if self.key.exists():
            self.key.unlink()
        self.record()
        text(self.root / "summary.json", json.dumps({"assertions": len(self.checks), "commands": len(self.commands),
             "pass": bool(self.checks) and all(item["pass"] for item in self.checks), "baseline_only": self.args.baseline_only,
             "tool_sha256": getattr(self, "source_hashes", {})}, sort_keys=True, indent=2) + "\n")
        print("Evidence: " + str(self.root), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-dir", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--bash", type=Path, default=Path("C:/Program Files/Git/bin/bash.exe"))
    parser.add_argument("--powershell", type=Path, default=Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    parser.add_argument("--gpg", type=Path, default=Path("C:/Program Files/Git/usr/bin/gpg.exe"))
    parser.add_argument("--gpgconf", type=Path, default=Path("C:/Program Files/Git/usr/bin/gpgconf.exe"))
    parser.add_argument("--keygen-gpg", type=Path, default=Path("C:/Program Files/GnuPG/bin/gpg.exe"))
    args = parser.parse_args()
    suite = Suite(args)
    try:
        suite.setup()
        suite.baseline()
        if not args.baseline_only:
            suite.pending()
            suite.more_pending()
        suite.check("tools-unchanged-during-tests", suite.source_hashes == {name: digest(suite.tools / name) for name in TOOLS})
        print("PASS: %d assertions; %d commands" % (len(suite.checks), len(suite.commands)), flush=True)
    finally:
        suite.cleanup()


if __name__ == "__main__":
    main()
