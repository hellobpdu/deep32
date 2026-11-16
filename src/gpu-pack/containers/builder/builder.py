#!/usr/bin/env python3
"""Run the production knowledge-base build chain and explicit C3 bundle steps."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_sets import EvalError, SKIPPED_MESSAGE, supplied_sets, doc_keys

SERVED_SETTINGS = (
    "retrieval.grep.regex_engine=true",
    "retrieval.reranker.candidates=15",
    "retrieval.reranker.max_chars=1600",
    "retrieval.reranker.window=centred",
    "retrieval.reranker.search_many_candidates=10",
    "retrieval.fold_identical=true",
)
SERVICE_ENV = {
    "RAG_EMBEDDING_URL": "http://embeddings:80/v1/embeddings",
    "RAG_QDRANT_URL": "http://qdrant:6333",
    "RAG_RERANK_URL": "http://reranker:8082/v1/rerank",
}
GENERATION_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}\Z")


class BuilderError(ValueError):
    pass


def child_env(source=None):
    env = dict(os.environ if source is None else source)
    model_pins = {key: env[key] for key in ("RAG_EMBEDDING_MODEL", "RAG_EMBEDDING_REVISION") if key in env}
    # A builder container must never inherit a production served root, dense
    # collection, config override or credential from its calling environment.
    for key in list(env):
        if key.startswith("RAG_"):
            del env[key]
    env.update(SERVICE_ENV)
    env.update(model_pins)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def command(args, script, *items):
    return [args.python, "-I", "-B", str(Path(args.engine_root) / "scripts" / script),
            *map(str, items)]


def run_step(name, argv, args, runner, env):
    # Capturing and discarding child output prevents error paths in upstream
    # eval code from leaking a query, expected answer or signing-hook output.
    try:
        result = runner(argv, cwd=str(Path(args.engine_root)), env=dict(env),
                        stdin=subprocess.DEVNULL, capture_output=True, check=False)
    except OSError:
        raise BuilderError(name + " could not start") from None
    if result.returncode:
        raise BuilderError(name + " failed with exit " + str(result.returncode))
    return name


def _generation(value):
    if not GENERATION_RE.fullmatch(value):
        raise BuilderError("invalid generation identifier")


def record_quality(root, quality):
    """Record the quality outcome in the generation's existing build report."""
    report = Path(root) / "BUILD_REPORT.json"
    temporary = None
    try:
        if report.is_symlink() or not report.is_file():
            raise BuilderError("served build report must be a regular file")
        data = json.loads(report.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("status") != "complete"
                or data.get("failed_steps") or (data.get("documents") or {}).get("failed")):
            raise BuilderError("only a complete build report can record quality validation")
        data["quality_validation"] = quality
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=".quality-", suffix=".tmp", dir=report.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, report)
    except BuilderError:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise BuilderError("could not record quality validation in the served build report") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build(args, *, runner=subprocess.run, environ=None):
    _generation(args.generation)
    _generation(args.served_generation)
    if Path(args.build_root).name != args.generation or Path(args.served_root).name != args.served_generation:
        raise BuilderError("root directory names must match generation identifiers")
    if args.generation == args.served_generation:
        raise BuilderError("build and served generations must be different")
    engine = Path(args.engine_root)
    embedded = engine / "eval"
    if embedded.exists() or embedded.is_symlink():
        raise BuilderError("builder engine must contain no eval inputs; use --eval-root")
    eval_root = getattr(args, "eval_root", None)
    selected, excluded = supplied_sets(Path(eval_root), Path(args.kept_manifest)) if eval_root is not None else ([], [])
    if eval_root is not None:
        # Preserve preflight path/link checks before resolving child inputs.
        eval_root = Path(eval_root).resolve()
        selected = [directory.resolve() for directory in selected]
    env = child_env(environ)
    completed = []
    def step(name, argv):
        completed.append(run_step(name, argv, args, runner, env))
    step("build_root", command(args, "build_root.py", "--corpus-repo", args.corpus_repo,
         "--corpus-ref", args.corpus_ref, "--root", args.build_root,
         "--generation", args.generation, "--opt-in-doc-types", "powershell,system",
         "--rerank-url", SERVICE_ENV["RAG_RERANK_URL"]))
    derive = command(args, "derive_root.py", "--from", args.build_root, "--to", args.served_root,
                     "--rerank-url", SERVICE_ENV["RAG_RERANK_URL"])
    for setting in SERVED_SETTINGS:
        derive.extend(["--set", setting])
    step("derive_root", derive)
    for directory in selected:
        validation = command(args, "validate_root.py", "--root", args.served_root, "--port", "18765",
                             "--eval-root", eval_root,
                             "--golden", directory, "--expect-generation", args.served_generation,
                             "--expect-fingerprint", args.expect_fingerprint,
                             "--expect-opt-in", "powershell,system", "--report-name",
                             "VALIDATION-" + directory.name + ".json")
        # Negative probes have no judged relevance rows, so hit metrics are
        # undefined. Use the validator's existing off switches for only those
        # two floors; its status, errors, degradation and scope gates remain.
        if directory.name.endswith("_probe") and not doc_keys(directory / "qrels.jsonl"):
            validation.extend(["--min", "hit@1=off", "--min", "hit@8=off"])
        step("validate_root:" + directory.name, validation)
    if not selected:
        step("validate_root:no-eval", command(args, "validate_root.py", "--root", args.served_root,
             "--port", "18765", "--no-eval", "--expect-generation", args.served_generation,
             "--expect-fingerprint", args.expect_fingerprint, "--expect-opt-in", "powershell,system",
             "--report-name", "VALIDATION-structural.json"))
    quality = {"status": "passed", "eval_sets": [p.name for p in selected]} if selected else {
        "status": "skipped", "message": SKIPPED_MESSAGE, "eval_sets": []}
    record_quality(args.served_root, quality)
    step("export_generation", command(args, "export_generation.py", "export", "--root", args.served_root,
         "--expect-generation", args.served_generation, "--expect-fingerprint", args.expect_fingerprint,
         "--out-dir", args.export_dir, "--recipient", args.recipient,
         "--qdrant-url", SERVICE_ENV["RAG_QDRANT_URL"], "--not-served-ok"))
    return {"steps": completed, "eval_sets": [p.name for p in selected], "excluded_names": excluded,
            "quality_validation": quality}


def json_argv(value):
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("expected a JSON command array") from None
    if not isinstance(parsed, list) or not parsed or any(not isinstance(p, str) or not p for p in parsed):
        raise argparse.ArgumentTypeError("expected a non-empty JSON array of non-empty strings")
    return json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))


def bundle(args, *, runner=subprocess.run, environ=None):
    _generation(args.served_generation)
    env = child_env(environ)
    completed = []
    def step(name, argv):
        completed.append(run_step(name, argv, args, runner, env))
    step("unpack", command(args, "export_generation.py", "unpack", "--input", args.export_input,
         "--into", args.unpack_dir, "--expect-generation", args.served_generation))
    generation = Path(args.unpack_dir) / args.served_generation
    step("verify_export", command(args, "export_generation.py", "verify", "--dir", generation,
         "--expect-fingerprint", args.expect_fingerprint))
    program = str(Path(args.kb_bundle_root) / "kb_bundle.py")
    prefix = [args.python, "-I", "-B", program]
    blocks = [item for path in args.block for item in ("--block", path)]
    step("c3_write", prefix + ["write", "--generation-dir", str(generation),
         "--out", args.bundle_dir, "--created", args.created, "--compat", args.compat,
         "--public", "--allow-path-prefix", "/var/lib/deep32-forge/",
         "--signer-type", args.signer_type, "--signer-id", args.signer_id,
         "--sign-cmd", args.sign_cmd, "--json"] + blocks)
    step("c3_check", prefix + ["check", "--dir", str(Path(args.bundle_dir) / args.served_generation),
         "--deep", "--public", "--allow-path-prefix", "/var/lib/deep32-forge/",
         "--verify-cmd", args.verify_cmd, "--json"] + blocks)
    return {"steps": completed}


def parser():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = ap.add_subparsers(dest="verb", required=True)
    for name in ("build", "bundle"):
        p = commands.add_parser(name, allow_abbrev=False)
        p.add_argument("--engine-root", default="/opt/deep32/engine")
        p.add_argument("--python", default=sys.executable)
        p.add_argument("--served-generation", required=True)
        p.add_argument("--expect-fingerprint", required=True)
        if name == "build":
            p.add_argument("--eval-root", type=Path,
                           help="operator-provided runtime mount containing eval_v2.py and golden/; omit to skip quality validation")
            for item in ("corpus-repo", "corpus-ref", "build-root", "served-root", "generation",
                         "kept-manifest", "export-dir", "recipient"):
                p.add_argument("--" + item, required=True)
        else:
            p.add_argument("--kb-bundle-root", default="/opt/deep32/builder/kb-bundle")
            for item in ("export-input", "unpack-dir", "bundle-dir", "compat", "created", "signer-id"):
                p.add_argument("--" + item, required=True)
            p.add_argument("--block", action="append", required=True)
            p.add_argument("--signer-type", choices=("openpgp", "external"), required=True)
            p.add_argument("--sign-cmd", type=json_argv, required=True)
            p.add_argument("--verify-cmd", type=json_argv, required=True)
    return ap


def main(argv=None, *, runner=subprocess.run, environ=None):
    args = parser().parse_args(argv)
    try:
        result = (build if args.verb == "build" else bundle)(args, runner=runner, environ=environ)
    except (BuilderError, EvalError) as exc:
        print("builder: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", **result}, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
