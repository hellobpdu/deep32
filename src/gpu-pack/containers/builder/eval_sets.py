"""Positive-list neutral eval selection and doc_key-only kept-manifest checks.

No query or expected-answer value is returned, printed or logged here. Excluded
directory entries are inspected by name only, never traversed or opened.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

EXACT_FAMILIES = ("golden_sc_v1", "golden_ise_v1", "golden_iseapi_v1")
FORBIDDEN = ('smoke', 'secops', 'replay')
REQUIRED_MEMBERS = ("MANIFEST.json", "qrels.jsonl", "queries.jsonl")
KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class EvalError(ValueError):
    """An eval input cannot be used for a kept-collection build."""


def allowed_name(name: str) -> bool:
    if not name or "/" in name or "\\" in name or any(x in name.lower() for x in FORBIDDEN):
        return False
    return name in EXACT_FAMILIES or name.startswith("golden_pwsh_v1")


def select_sets(root: Path) -> tuple[list[Path], list[str]]:
    """Select only named neutral directories; excluded names are not opened."""
    root = Path(root)
    if root.is_symlink():
        raise EvalError("neutral eval root must not be a symbolic link")
    if not root.is_dir():
        raise EvalError("approved neutral eval directory is missing")
    selected, excluded = [], []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not allowed_name(entry.name):
            excluded.append(entry.name)
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise EvalError("approved neutral eval entry is not a regular directory")
        selected.append(entry)
    if not selected:
        raise EvalError("no approved neutral eval sets; quality validation cannot run")
    return selected, excluded


def release_check(root: Path) -> tuple[list[Path], list[str]]:
    """Require every shipping family by name, without opening any set member."""
    selected, excluded = select_sets(root)
    present = {path.name for path in selected}
    missing = [name for name in EXACT_FAMILIES if name not in present]
    if not any(name.startswith("golden_pwsh_v1") for name in present):
        missing.append("golden_pwsh_v1*")
    if missing:
        raise EvalError("release is missing approved neutral eval families: " + ", ".join(missing))
    return selected, excluded


def _project(pairs):
    """Discard non-doc_key scalar values while decoding; keep nested key sets."""
    found = set()
    for key, value in pairs:
        if key == "doc_key":
            if not isinstance(value, str) or not KEY_RE.fullmatch(value):
                raise EvalError("invalid doc_key in checked input")
            found.add(value)
        elif isinstance(value, set):
            found.update(value)
        elif isinstance(value, list):
            pending = list(value)
            while pending:
                item = pending.pop()
                if isinstance(item, set):
                    found.update(item)
                elif isinstance(item, list):
                    pending.extend(item)
    return found


def doc_keys(path: Path) -> set[str]:
    """Project only doc_key values, with errors that never include source text."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise EvalError("checked input is missing or is not a regular file")
    try:
        with path.open("r", encoding="utf-8") as stream:
            values = [json.loads(line, object_pairs_hook=_project)
                      for line in stream if line.strip()] if path.suffix == ".jsonl" else [
                          json.load(stream, object_pairs_hook=_project)]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EvalError("checked input is unreadable or has invalid JSON") from None
    found = set()
    pending = list(values)
    while pending:
        value = pending.pop()
        if isinstance(value, set):
            found.update(value)
        elif isinstance(value, list):
            pending.extend(value)
    return found


def check_sets(root: Path, kept_manifest: Path) -> tuple[list[Path], list[str]]:
    selected, excluded = select_sets(root)
    kept = doc_keys(kept_manifest)
    if not kept:
        raise EvalError("kept manifest has no doc_key fields")
    for directory in selected:
        # The engine requires these three files. Other regular metadata files
        # can be carried byte for byte; project all JSON/JSONL doc_key fields.
        # Never traverse a nested set or a symbolic link.
        members = sorted(directory.iterdir(), key=lambda p: p.name)
        if not set(REQUIRED_MEMBERS).issubset(p.name for p in members):
            raise EvalError("approved neutral eval set has missing required members")
        if any(path.is_symlink() or not path.is_file() for path in members):
            raise EvalError("approved neutral eval set members must be regular files")
        found = set()
        for path in members:
            if path.suffix.lower() in (".json", ".jsonl"):
                found.update(doc_keys(path))
        # A negative probe can contain no relevance doc_key fields. Its kept
        # check is vacuous; the production validator still runs its gates.
        missing = found - kept
        if missing:
            raise EvalError("approved neutral eval set " + directory.name + " names " + str(len(missing)) +
                            " doc_key fields absent from the kept manifest")
    return selected, excluded
