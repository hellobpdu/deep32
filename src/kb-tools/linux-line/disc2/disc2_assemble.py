"""Join the existing content-disc tools; leave signing to the key holder.

prepare writes a fresh stage. finish consumes that exact stage after signing.
All paths in the configuration are operator-local. Nothing invokes a shell.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

FORMAT = "deep32-disc2-assembly/1"
CAP = 1992294400
XORRISO_WRAPPER_SHA256 = "acc95b4fc6b1d8b719ce8813f770f74fb690aec0f22aec856cadc373ea531508"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def write(path, value):
    Path(path).write_bytes((json.dumps(value, indent=2, sort_keys=True,
                                      ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))


def pairs(items):
    obj = {}
    for key, value in items:
        if key in obj:
            raise ValueError("Duplicate configuration key: " + key)
        obj[key] = value
    return obj


def read(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def no_links(path):
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Links are refused: " + item.name)
    return path


def argv(value, name):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(name + " must be a non-empty string array")
    return value


def load(config):
    c = read(no_links(config))
    required = {"format", "fixture", "tools", "bundle", "blocks", "main_packs", "shelves", "code",
                "ietf_manifest", "cisco_types", "release", "disc_version", "accepts_system",
                "created", "signer_fingerprint", "verify_argv", "xorriso_argv", "time_form",
                "corpus_manifest", "corpus_manifest_sha256", "cut_keys", "cut_keys_sha256",
                "markings", "cut_count", "scan_args", "root_files", "library_approval"}
    if set(c) != required or c["format"] != FORMAT or type(c["fixture"]) is not bool:
        raise ValueError("Configuration keys or format differ from disc2-config.example.json")
    if set(c["tools"]) != {"disc", "libdata", "gates"}:
        raise ValueError("tools must name disc, libdata and gates directories")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?", c["release"]):
        raise ValueError("release must have three numeric components (optionally -prerelease)")
    if not isinstance(c["blocks"], list) or len(c["blocks"]) != 4:
        raise ValueError("Supply exactly the four kept corpus blocks")
    if not isinstance(c["accepts_system"], list) or not c["accepts_system"]:
        raise ValueError("Supply the accepted system-disc versions")
    if not isinstance(c["root_files"], dict):
        raise ValueError("root_files must map root names to source files")
    if not isinstance(c["shelves"], dict) or set(c["shelves"]) != {"cisco", "ietf", "encyclopaedia"}:
        raise ValueError("Supply Cisco, IETF and cleared encyclopaedia C5 shelf folders")
    moment = dt.datetime.strptime(c["created"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    c["epoch"] = int(moment.timestamp())
    if c["time_form"] not in ("epoch", "digits"):
        raise ValueError("time_form must be epoch or digits")
    argv(c["xorriso_argv"], "xorriso_argv")
    argv(c["scan_args"], "scan_args")
    # These arguments belong to this wrapper; scan configuration cannot override them.
    forbidden = {"--tree", "--tree-b", "--gates", "--report", "--tmp", "--blind",
                 "--blocks", "--bundle", "--corpus-manifest", "--markings", "--cut-count",
                 "--block-tool", "--kb-tool", "--tool-python", "--kb-allow-unsigned",
                 "--kb-verify-cmd", "--g5-exceptions", "--history", "--repo", "--manifest"}
    if any(x.split("=", 1)[0] in forbidden for x in c["scan_args"]):
        raise ValueError("scan_args overrides an assembly-owned gate argument")
    if not c["fixture"]:
        if not re.fullmatch(r"[A-F0-9]{40}", c["signer_fingerprint"] or ""):
            raise ValueError("Release assembly requires the pinned primary fingerprint")
        argv(c["verify_argv"], "verify_argv")
        if not all(any(token in x for x in c["verify_argv"]) for token in ("{manifest}", "{sig}", "{fingerprint}")):
            raise ValueError("Verifier must bind manifest, signature and fingerprint")
        if c["xorriso_argv"] != ["xorriso"]:
            wrapper = Path(__file__).with_name("xorriso-win.py")
            if not (len(c["xorriso_argv"]) == 4 and c["xorriso_argv"][1:3] == ["-I", "-B"]
                    and Path(c["xorriso_argv"][3]).resolve() == wrapper.resolve()
                    and digest(wrapper) == XORRISO_WRAPPER_SHA256):
                raise ValueError("Release assembly uses the installed xorriso")
        if any(key.startswith("T0117_STUB_") for key in os.environ):
            raise ValueError("Test controls are present in a release environment")
        if "--g15-profile" not in c["scan_args"] or "--marks-scope" not in c["scan_args"]:
            raise ValueError("Release scans require full G15 disc profile and marks scope")
        index = c["scan_args"].index("--g15-profile")
        if c["scan_args"][index + 1:index + 2] != ["disc"]:
            raise ValueError("Release G15 profile must be disc")
        if not c["code"] or not c["root_files"]:
            raise ValueError("Supply the public code export and final notices/credits/licences/fetch list")
        for stem in ("THIRD-PARTY-NOTICES", "THIRD-PARTY-LICENSES", "LIBRARY-CREDITS", "SYSTEM-FETCH-LIST"):
            if not any(name == stem or name.startswith(stem + ".") for name in c["root_files"]):
                raise ValueError("Required release root file missing: " + stem)
    elif c["signer_fingerprint"] is not None or c["verify_argv"]:
        raise ValueError("Fixture assembly must be explicitly unsigned")
    for key in ("TMP", "TEMP", "TMPDIR"):
        if not os.environ.get(key):
            raise ValueError("Set TMP, TEMP and TMPDIR first")
    if len({os.environ[key] for key in ("TMP", "TEMP", "TMPDIR")}) != 1:
        raise ValueError("Temporary directory variables disagree")
    temp = no_links(os.environ["TMP"])
    if not temp.is_dir():
        raise ValueError("Temporary directory is absent")
    for key in ("corpus_manifest", "cut_keys"):
        if digest(no_links(c[key])) != c[key + "_sha256"]:
            raise ValueError(key + " hash differs")
    # This is an explicit lead census/approval artifact, not an automatic content ruling.
    approval = read(no_links(c["library_approval"]))
    if (approval.get("format") != "deep32-library-assembly-approval/1" or
            approval.get("fixture") is not c["fixture"] or
            approval.get("encyclopaedia") != "ready" or
            approval.get("cuts_reviewed") is not True or
            approval.get("main_program_name") != "lib-e26"):
        raise ValueError("Library readiness/cuts approval is incomplete")
    for key in ("main_packs", "ietf_manifest", "cisco_types"):
        if c[key]:
            no_links(c[key])
    for role, folder in c["shelves"].items():
        if approval.get("shelves", {}).get(role) != digest(no_links(Path(folder) / "PARTS.tsv")):
            raise ValueError("Library approval does not pin the " + role + " shelf")
    if approval.get("main_parts_sha256") != digest(no_links(Path(c["main_packs"]) / "PARTS.tsv")):
        raise ValueError("Library approval does not pin the main streams")
    if not c["fixture"]:
        for role in ("main_census", "native_zim_validation", "final_encyclopaedia_g1_g2"):
            row = approval.get("evidence", {}).get(role, {})
            if not row.get("path") or not re.fullmatch(r"[a-f0-9]{64}", row.get("sha256", "")):
                raise ValueError("Library approval evidence missing: " + role)
            if digest(no_links(row["path"])) != row["sha256"]:
                raise ValueError("Library approval evidence hash differs: " + role)
    identities = {Path(b).name: digest(no_links(Path(b) / "block.json")) for b in c["blocks"]}
    if set(identities) != {"rfc", "cisco", "powershell", "system"}:
        raise ValueError("Block directory names must be the four kept collections")
    bundle = read(no_links(Path(c["bundle"]) / "BUNDLE.json"))
    if {b["name"]: b["sha256"] for b in bundle["blocks"]} != identities:
        raise ValueError("Bundle and block identities differ")
    docs = {k: v["documents"] for k, v in bundle["counts"]["by_collection"].items()}
    if set(docs) != {"rfc", "cisco", "powershell", "system"} or any(v <= 0 for v in docs.values()):
        raise ValueError("Standard content disc needs four non-empty kept collections")
    if not c["fixture"] and docs != {"rfc": 365, "cisco": 489, "powershell": 241, "system": 85}:
        raise ValueError("Generation-2 document census differs; ask the lead to review the input")
    return c


def inputs(c):
    files = [Path(__file__), Path(__file__).with_name("disc2_scan.py"),
             Path(c["bundle"]) / "BUNDLE.json", Path(c["bundle"]) / "MANIFEST.json",
             Path(c["cisco_types"]), Path(c["library_approval"]),
             Path(c["corpus_manifest"]), Path(c["cut_keys"]), Path(c["markings"])]
    files += [Path(b) / "block.json" for b in c["blocks"]]
    files += [Path(p) for p in c["root_files"].values()]
    for row in read(c["library_approval"]).get("evidence", {}).values():
        if isinstance(row, dict) and row.get("path"):
            files.append(Path(row["path"]))
    path_options = {"--terms", "--excludes", "--allow", "--attribution", "--assets", "--collisions",
                    "--marks-scope", "--library-scope", "--marks", "--marks-allow", "--marks-decisions",
                    "--marks-context", "--notice-lines", "--licence-map", "--ledger", "--surface-file"}
    for i, token in enumerate(c["scan_args"]):
        option, sep, inline = token.partition("=")
        if option in path_options:
            value = inline if sep else c["scan_args"][i + 1]
            if option in ("--ledger", "--surface-file"):
                value = value.split("=", 1)[1]
            source = no_links(value)
            files += [p for p in source.rglob("*") if p.is_file()] if source.is_dir() else [source]
            if option == "--attribution":
                files.append(source.parent / read(source)["authorship_file"])
    if c["ietf_manifest"]:
        files.append(Path(c["ietf_manifest"]))
    # PARTS.tsv and every part are frozen; reject unexpected links before reading.
    files += [p for p in Path(c["main_packs"]).iterdir() if p.is_file()]
    for folder in c["shelves"].values():
        files += [p for p in Path(folder).iterdir() if p.is_file()]
    if c["code"]:
        files += [p for p in Path(c["code"]).rglob("*") if p.is_file()]
    for role, directory in c["tools"].items():
        files += [p for p in Path(directory).rglob("*.py") if p.is_file()]
        files += [p for p in Path(directory).rglob("*.json") if p.is_file()]
    rows = []
    for p in sorted(set(files), key=lambda p: p.as_posix()):
        rows.append({"path": str(no_links(p).resolve()), "sha256": digest(p)})
    return rows


class Runner:
    def __init__(self, c, work):
        self.c, self.work = c, Path(work)
        self.py = [sys.executable, "-I", "-B"]
        self.disc = Path(c["tools"]["disc"]) / "d32_disc2.py"
        self.lib = Path(c["tools"]["libdata"]) / "d32_libdata.py"
        self.gates = Path(c["tools"]["gates"]) / "run_gates.py"

    def run(self, name, script, args):
        args = [str(x) for x in args]
        record = self.work / "logs" / (name + ".json")
        if record.exists():
            raise ValueError("Step already has a result: " + name)
        print(name, file=sys.stderr, flush=True)
        result = subprocess.run([*self.py, str(script), *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=86400)
        # Keep these machine-local logs outside the disc and release set.
        write(record, {"exit": result.returncode, "stdout": result.stdout.decode("utf-8", "replace"),
                       "stderr": result.stderr.decode("utf-8", "replace")})
        if result.returncode:
            raise ValueError("Step failed: " + name + "; inspect logs/" + name + ".json")
        return result

    def d(self, name, args):
        return self.run(name, self.disc, [*args, "--python-argv", json.dumps(self.py)])

    def auth(self):
        # The release C11 signature supplies bundle trust, as documented by the recorded build.
        return ["--bundle", self.c["bundle"], "--kb-allow-unsigned"]

    def common(self):
        c, w = self.c, self.work
        args = self.auth()
        for b in c["blocks"]:
            args += ["--block", b]
        args += ["--packs", w / "rebound/packs", "--packs", w / "lib-corpus/packs",
                 "--cisco-public", w / "cisco/cisco-public.sqlite", "--disc-version", c["disc_version"],
                 "--created", c["created"], "--cut-keys", c["cut_keys"], "--main-program-name", "lib-e26"]
        for role in ("cisco", "ietf", "encyclopaedia"):
            args += ["--packs", c["shelves"][role]]
        if c["code"]:
            args += ["--code", c["code"]]
        for v in c["accepts_system"]:
            args += ["--accepts-system", v]
        for name, path in sorted(c["root_files"].items()):
            args += ["--root-file", name + "=" + path]
        return args + (["--unsigned", "--allow-fixture-catalogue"] if c["fixture"] else
                       ["--signer-fingerprint", c["signer_fingerprint"]])

    def scan(self, name, tree, gates, extra=(), proposed=()):
        report = self.work / "reports" / (name + ".json")
        scan_args = self.c["scan_args"]
        if gates in ("G3", "G5"):
            # The gate runner rejects options belonging to gates outside this run.
            scan_args = []
            for i, token in enumerate(self.c["scan_args"]):
                option, sep, value = token.partition("=")
                if option in ("--terms", "--excludes", "--allow"):
                    scan_args += [token] if sep else [token, self.c["scan_args"][i + 1]]
        if proposed:
            merged = self.work / "reports" / (name + "-allow.tsv")
            retained, base = [], None
            i = 0
            while i < len(scan_args):
                option, sep, value = scan_args[i].partition("=")
                if option == "--allow":
                    base = Path(value if sep else scan_args[i + 1])
                    i += 1 if sep else 2
                else:
                    retained.append(scan_args[i])
                    i += 1
            if base is None:
                raise ValueError("An explicit gate allowlist is required")
            raw = base.read_bytes()
            if not raw.endswith(b"\n"):
                raw += b"\n"
            for row in proposed:
                raw += ("\t".join(str(row[k]) for k in ("path", "line", "text", "reason", "date")) + "\n").encode("ascii")
            merged.write_bytes(raw)
            scan_args = [*retained, "--allow", str(merged)]
        self.run(name, self.gates, ["--tree", tree, "--archives", "--gates", gates,
                                    "--report", report, "--tmp", os.environ["TMP"], "--blind", "fail",
                                    *scan_args, *extra])
        obj = read(report)
        if obj.get("result") != "pass" or {g["id"] for g in obj["gates"]} != set(gates.split(",")):
            raise ValueError("Gate report did not pass all requested gates: " + name)


def scan_module():
    path = Path(__file__).with_name("disc2_scan.py")
    spec = importlib.util.spec_from_file_location("disc2_scan_glue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare(config, work):
    c = load(config)
    work = no_links(work)
    protected = [Path(c["bundle"]), Path(c["main_packs"]), *(Path(p) for p in c["shelves"].values()),
                 *(Path(b) for b in c["blocks"]),
                 *(Path(p) for p in c["tools"].values())]
    if c["code"]:
        protected.append(Path(c["code"]))
    for p in protected:
        p = no_links(p).resolve()
        if work.resolve().is_relative_to(p) or p.is_relative_to(work.resolve()):
            raise ValueError("Output overlaps an input")
    work.mkdir(parents=True, exist_ok=False)
    (work / "logs").mkdir()
    (work / "reports").mkdir()
    frozen = inputs(c)
    r = Runner(c, work)
    r.run("lib-corpus", r.lib, ["lib-corpus", *r.auth(), "--release", c["release"],
                              "--made", c["epoch"], "--python-argv", json.dumps(r.py),
                              "--out", work / "lib-corpus"])
    mapping_args = ["citemap-main", *r.auth(), "--main-packs", c["main_packs"], "--out", work / "mapping"]
    if c["ietf_manifest"]:
        mapping_args += ["--ietf-manifest", c["ietf_manifest"]]
    r.d("citemap", mapping_args)
    r.d("rebind", ["rebind", *r.auth(), "--packs", c["main_packs"],
                   "--cite-map", work / "mapping/lib-e26/cite-map.tsv",
                   "--binding", work / "mapping/lib-e26/cite-map.binding.json", "--out", work / "rebound"])
    r.run("cisco-public", r.lib, ["cisco-public", *r.auth(), "--lib-corpus", work / "lib-corpus",
                                "--main-packs", work / "rebound/packs", "--cisco-types", c["cisco_types"],
                                "--scope", "verified",
                                "--main-program-name", "lib-e26", "--out", work / "cisco"])
    shelf_args = [x for role in ("cisco", "ietf", "encyclopaedia") for x in ("--packs", c["shelves"][role])]
    r.d("packs", ["check-pack", *r.auth(), "--packs", work / "rebound/packs",
                  "--packs", work / "lib-corpus/packs", *shelf_args, "--program-checks", "--count-sources"])
    r.d("govcheck", ["govcheck", *r.auth(), "--lib-corpus", work / "lib-corpus", "--from-packs",
                     "--corpus-manifest", c["corpus_manifest"], "--corpus-manifest-sha256", c["corpus_manifest_sha256"],
                     "--cut-keys", c["cut_keys"], "--cut-keys-sha256", c["cut_keys_sha256"],
                     "--out", work / "reports/GOVCHECK.json"])
    r.d("plan", ["plan", *r.common(), "--out", work / "reports/plan.json"])
    planned = read(work / "reports/plan.json")
    if shutil.disk_usage(work).free < 7 * planned["bytes"]:
        raise ValueError("Free space is below seven times planned payload bytes")
    r.d("stage", ["stage", *r.common(), "--out", work / "stage"])
    staged = read(work / "stage/disc/MANIFEST.json")
    expected_streams = {"lib-main", "lib-catalogue", "lib-corpus", "lib-cisco", "lib-ietf", "lib-encyclopedia"}
    actual_streams = [s["pack"] for s in staged["library"]]
    if set(actual_streams) != expected_streams or len(actual_streams) != len(expected_streams):
        raise ValueError("Standard Library stream roster differs")
    # The stock scanner takes complete archives; C5/C4 joins preserve every byte.
    scanner = scan_module()
    coverage = scanner.build_view(work / "stage/disc", work / "scan-view-prepared", Path(c["tools"]["disc"]))
    write(work / "reports/coverage-prepared.json", coverage)
    r.scan("stage-scan", work / "scan-view-prepared", "G1,G2,G13,G14,G15",
           proposed=scanner.allowances(coverage))
    if inputs(c) != frozen:
        raise ValueError("An input changed during preparation")
    write(work / "prepared.json", {"format": FORMAT, "fixture": c["fixture"],
          "config_sha256": digest(config), "inputs": frozen,
          "manifest_sha256": digest(work / "stage/disc/MANIFEST.json"),
          "stage_sha256": digest(work / "stage/stage.json"),
          "next": "finish" if c["fixture"] else "key holder signs stage/disc/MANIFEST.json; then finish"})
    return {"status": "prepared", "fixture": c["fixture"], "manifest_sha256": digest(work / "stage/disc/MANIFEST.json")}


def finish(config, work):
    c = load(config)
    work = no_links(work)
    p = read(work / "prepared.json")
    if p["config_sha256"] != digest(config) or p["inputs"] != inputs(c):
        raise ValueError("Configuration or inputs changed since prepare")
    for path, key in (("stage/disc/MANIFEST.json", "manifest_sha256"), ("stage/stage.json", "stage_sha256")):
        if digest(work / path) != p[key]:
            raise ValueError("Prepared stage changed")
    r = Runner(c, work)
    verification = (["--unsigned-ok"] if c["fixture"] else ["--verify-cmd", json.dumps(c["verify_argv"])])
    r.d("check-disc", ["check-disc", "--disc", work / "stage/disc", *verification,
                       "--bundle-check", "--kb-allow-unsigned", "--out", work / "reports/check-disc.json"])
    image_auth = (["--allow-unsigned-image"] if c["fixture"] else ["--verify-cmd", json.dumps(c["verify_argv"])])
    for number in (1, 2):
        r.d("image-" + str(number), ["image", "--stage", work / "stage", *image_auth,
             "--xorriso-argv", json.dumps(c["xorriso_argv"]), "--xorriso-time-form", c["time_form"],
             "--out", work / ("image-" + str(number))])
    name = "deep32-content-" + c["disc_version"] + ".iso"
    image = work / "image-1" / name
    if digest(image) != digest(work / "image-2" / name):
        raise ValueError("Repeated image hashes differ")
    r.d("verify-image", ["verify-image", "--image", image, "--stage", work / "stage",
                         "--out", work / "reports/verify-image.json"])
    scanner = scan_module()
    coverage = scanner.build_view(work / "stage/disc", work / "scan-view-final", Path(c["tools"]["disc"]))
    write(work / "reports/coverage-final.json", coverage)
    r.scan("final-view-scan", work / "scan-view-final", "G1,G2,G13,G14,G15",
           proposed=scanner.allowances(coverage))
    # Raw ISO fragment allowances are used only after complete joined content passes.
    r.scan("image-scan", image, "G1,G2,G13,G14,G15",
           proposed=scanner.allowances(coverage, image_basename=name))
    r.scan("G3", image, "G3", ["--blocks", work / "stage/disc/corpus", "--bundle",
           work / "stage/disc" / read(work / "stage/disc/MANIFEST.json")["kb"]["path"],
           "--corpus-manifest", c["corpus_manifest"], "--markings", c["markings"],
           "--cut-count", c["cut_count"], "--tool-python", sys.executable,
           "--kb-tool", Path(c["tools"]["disc"]) / "vendor/t0114/kb-bundle/kb_bundle.py",
           "--block-tool", Path(c["tools"]["disc"]) / "vendor/t0106/validate_block.py", "--kb-allow-unsigned"])
    # Stock G5 also needs complete archives. Image equality covers raw part layout.
    second = scanner.build_view(work / "stage/disc", work / "scan-view-repeat", Path(c["tools"]["disc"]))
    write(work / "reports/coverage-repeat.json", second)
    r.scan("G5", work / "scan-view-final", "G5", ["--tree-b", work / "scan-view-repeat"])
    r.d("parts", ["parts", "--image", image, "--version", c["disc_version"], "--out", work / "parts"])
    # Independently prove the C2 joined hash without allocating a second image.
    split = read(work / "parts/content-disc.json")
    joined = hashlib.sha256()
    total = 0
    for part in split["parts"]:
        path = work / "parts" / part["name"]
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                joined.update(data)
                total += len(data)
    if total != image.stat().st_size or joined.hexdigest() != digest(image):
        raise ValueError("Joined release parts differ from the image")
    # The reader reports the observed size for a sole C5 part. Preserve the
    # original build record and make this contract conversion explicit.
    corpus_record = read(work / "lib-corpus/lib-corpus.json")
    corpus_stream = next(s for s in read(work / "stage/disc/MANIFEST.json")["library"] if s["pack"] == "lib-corpus")
    if corpus_record["stream"]["part_size"] != corpus_stream["part_size"]:
        stream = corpus_record["stream"]
        if (len(stream["parts"]) != 1 or stream["part_size"] < stream["bytes"] or
                corpus_stream["part_size"] != stream["bytes"] or
                corpus_stream["stream_sha256"] != stream["sha256"]):
            raise ValueError("Corpus part-size records cannot be reconciled")
        corpus_record["assembly_normalisation"] = {
            "source_sha256": digest(work / "lib-corpus/lib-corpus.json"),
            "configured_part_size": stream["part_size"], "effective_part_size": corpus_stream["part_size"],
            "reason": "Single-part C5 parser records its observed part size"}
        stream["part_size"] = corpus_stream["part_size"]
    write(work / "reports/lib-corpus-effective-record.json", corpus_record)
    r.d("provenance", ["provenance", "--stage-record", work / "stage/stage.json",
         "--image-record", work / "image-1/image.json", "--parts-record", work / "parts/content-disc.json",
         "--lib-corpus-record", work / "reports/lib-corpus-effective-record.json", "--cisco-public-record", work / "cisco/cisco-public.json",
         "--rebind-record", work / "rebound/rebind.json", "--out", work / "provenance.json"])
    files = [image, work / "stage/disc/MANIFEST.json", work / "provenance.json", work / "parts/content-disc.json"]
    sig = work / "stage/disc/MANIFEST.json.asc"
    if sig.exists():
        files.append(sig)
    files += [work / "parts" / part["name"] for part in split["parts"]]
    # Store names relative to the work directory; this checksum file is beside the ISO.
    (work / "SHA256SUMS").write_bytes("".join(digest(f) + "  " + f.relative_to(work).as_posix() + "\n"
                  for f in sorted(files)).encode("ascii"))
    if inputs(c) != p["inputs"]:
        raise ValueError("An input changed during image construction")
    result = {"format": FORMAT, "status": "assembled", "fixture": c["fixture"],
              "image": {"file": name, "bytes": image.stat().st_size, "sha256": digest(image)},
              "manifest_sha256": digest(work / "stage/disc/MANIFEST.json"),
              "repeat_equal": True, "joined_parts_equal": True,
              "gates": ["G1", "G2", "G3", "G5", "G13", "G14", "G15"],
              "gate_scope": "Complete joined archive view plus exact raw ISO checks and equal repeated image hashes",
              "release_gates_pending": ["G4", "G5-independent-host", "G6", "G7", "G8", "G9", "G10", "G11", "G12", "G14-hidden-mark"]}
    write(work / "assembly.json", result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("verb", choices=("prepare", "finish"))
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    a = ap.parse_args()
    try:
        result = prepare(a.config, a.work) if a.verb == "prepare" else finish(a.config, a.work)
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "failed", "detail": str(error)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
