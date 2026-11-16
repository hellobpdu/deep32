from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import uuid
from pathlib import Path

from rag_core.db import connect, initialize
from rag_core.identity import document_id, sha256_file
from rag_core.paths import RAGPaths


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


def _section_id(doc_id: str, ordinal: int, heading_path: list[str]) -> str:
    material = f"{doc_id}\0{ordinal}\0{' > '.join(heading_path)}"
    return "sec_" + hashlib.sha256(material.encode()).hexdigest()


def parse_markdown_sections(markdown: str, doc_id: str) -> list[dict]:
    raw: list[dict] = []

    current = {
        "level": 0,
        "heading": None,
        "heading_path": [],
        "lines": [],
    }
    heading_stack: list[str] = []

    def flush():
        nonlocal current

        text = "\n".join(current["lines"]).strip()

        if current["heading"] is not None or text:
            raw.append({
                "level": current["level"],
                "heading": current["heading"],
                "heading_path": list(current["heading_path"]),
                "text": text,
            })

    fence: str | None = None

    for line in markdown.splitlines():
        # Lines inside fenced code are never headings: CLI/config examples carry
        # "# comment" lines and "#" prompts that would otherwise split sections.
        # As in CommonMark, a backtick run followed by another backtick on the
        # same line (```show ip route```) is inline code, not an opening
        # fence, and a closing fence carries no info string.
        f = FENCE.match(line)
        if f:
            marker, info = f.group(1), f.group(2)
            if fence is None:
                if not (marker[0] == "`" and "`" in info):
                    fence = marker
            elif (
                marker[0] == fence[0]
                and len(marker) >= len(fence)
                and not info.strip()
            ):
                fence = None
            current["lines"].append(line)
            continue

        m = None if fence else HEADING.match(line)

        if not m:
            current["lines"].append(line)
            continue

        flush()

        level = len(m.group(1))
        heading = m.group(2).strip()

        heading_stack = heading_stack[: level - 1]
        heading_stack.append(heading)

        current = {
            "level": level,
            "heading": heading,
            "heading_path": list(heading_stack),
            "lines": [],
        }

    flush()

    result: list[dict] = []
    stack: list[tuple[int, str]] = []

    for ordinal, item in enumerate(raw):
        level = item["level"]

        while stack and stack[-1][0] >= level:
            stack.pop()

        parent = stack[-1][1] if stack and level > 0 else None
        sid = _section_id(doc_id, ordinal, item["heading_path"])

        result.append({
            "section_id": sid,
            "parent_section_id": parent,
            "ordinal": ordinal,
            **item,
        })

        if level > 0:
            stack.append((level, sid))

    return result


INGEST_ID_NAMESPACE = "deep32-ingest-id/1"


def deterministic_ingest_id(
    paths: RAGPaths,
    digest: str,
    original: Path,
    source_name: str,
    source_uri: str | None,
) -> str:
    """The ingest_runs id of one ingest, from its inputs only: "ing_" and 32 hex digits of SHA-256 over the
    namespace, the document's SHA-256, the source name and URI, the original's path relative to the root (its
    name when outside the root) and the first free ordinal. Same corpus, same order: same ids, at any root."""
    try:
        where = original.relative_to(paths.root).as_posix()
    except ValueError:
        where = original.name
    with connect(paths.sqlite / "rag.db") as conn:
        ordinal = 0
        while True:
            material = "\0".join((
                INGEST_ID_NAMESPACE, digest, source_name, source_uri or "", where, str(ordinal),
            ))
            candidate = "ing_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
            taken = conn.execute(
                "SELECT 1 FROM ingest_runs WHERE ingest_id = ?", (candidate,)
            ).fetchone()
            if taken is None:
                return candidate
            ordinal += 1


def ingest_mineru(
    original: Path,
    result_dir: Path,
    source_name: str = "mineru",
    source_uri: str | None = None,
) -> dict:
    paths = RAGPaths.from_env()
    initialize(paths)

    original = original.expanduser().resolve()
    result_dir = result_dir.expanduser().resolve()

    if not original.is_file():
        raise FileNotFoundError(original)

    required = [
        "markdown.md",
        "middle_json.json",
        "model_output.json",
        "structured_content.json",
        "worker_result.json",
    ]

    for name in required:
        if not (result_dir / name).is_file():
            raise FileNotFoundError(result_dir / name)

    digest = sha256_file(original)
    doc_id = document_id(digest)
    ingest_id = deterministic_ingest_id(paths, digest, original, source_name, source_uri)

    db = paths.sqlite / "rag.db"

    with connect(db) as conn:
        existing = conn.execute(
            "SELECT doc_id, status FROM documents WHERE sha256 = ?",
            (digest,),
        ).fetchone()

        if existing:
            conn.execute("""
                INSERT INTO ingest_runs (
                    ingest_id,
                    doc_id,
                    source_path,
                    status,
                    stage,
                    finished_at,
                    metadata_json
                )
                VALUES (?, ?, ?, 'DUPLICATE', 'DEDUPE',
                        CURRENT_TIMESTAMP, ?)
            """, (
                ingest_id,
                existing["doc_id"],
                str(original),
                json.dumps({"sha256": digest}),
            ))

            return {
                "duplicate": True,
                "doc_id": existing["doc_id"],
                "sha256": digest,
                "status": existing["status"],
            }

    worker = json.loads(
        (result_dir / "worker_result.json").read_text(encoding="utf-8")
    )

    markdown = (result_dir / "markdown.md").read_text(
        encoding="utf-8"
    )

    sections = parse_markdown_sections(markdown, doc_id)

    original_dir = paths.originals / doc_id
    normalized_dir = paths.normalized / doc_id
    metadata_dir = paths.metadata / doc_id

    original_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    original_dest = original_dir / original.name
    shutil.copy2(original, original_dest)

    for name in required:
        shutil.copy2(result_dir / name, normalized_dir / name)

    tier = worker.get("tier", "advanced")
    page_count = worker.get("pages")

    parse_backend = (
        "mineru-advanced"
        if tier == "advanced"
        else "mineru-standard"
    )

    document_manifest = {
        "doc_id": doc_id,
        "sha256": digest,
        "source_name": source_name,
        "source_uri": source_uri,
        "original_filename": original.name,
        "mime_type": (
            mimetypes.guess_type(original.name)[0]
            or "application/octet-stream"
        ),
        "parse_backend": parse_backend,
        "parser": "MinerU",
        "parser_version": "4.0.7",
        "page_count": page_count,
        "artifacts": required,
        "worker_result": worker,
        "sections": len(sections),
    }

    manifest_path = metadata_dir / "document.json"
    manifest_path.write_text(
        json.dumps(document_manifest, indent=2),
        encoding="utf-8",
    )

    try:
        with connect(db) as conn:
            conn.execute("""
                INSERT INTO ingest_runs (
                    ingest_id,
                    source_path,
                    status,
                    stage,
                    metadata_json
                )
                VALUES (?, ?, 'RUNNING', 'CANONICALIZE', ?)
            """, (
                ingest_id,
                str(original),
                json.dumps({"sha256": digest}),
            ))

            conn.execute("""
                INSERT INTO documents (
                    doc_id,
                    sha256,
                    source_name,
                    source_uri,
                    mime_type,
                    original_filename,
                    original_path,
                    canonical_path,
                    parse_backend,
                    parser_version,
                    parse_tier,
                    page_count,
                    status,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id,
                digest,
                source_name,
                source_uri,
                document_manifest["mime_type"],
                original.name,
                str(original_dest),
                str(normalized_dir / "markdown.md"),
                parse_backend,
                "4.0.7",
                tier,
                page_count,
                "CANONICALIZED",
                json.dumps(document_manifest),
            ))

            for sec in sections:
                conn.execute("""
                    INSERT INTO sections (
                        section_id,
                        doc_id,
                        parent_section_id,
                        ordinal,
                        level,
                        heading,
                        heading_path_json,
                        text,
                        provenance_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sec["section_id"],
                    doc_id,
                    sec["parent_section_id"],
                    sec["ordinal"],
                    sec["level"],
                    sec["heading"],
                    json.dumps(sec["heading_path"]),
                    sec["text"],
                    json.dumps({
                        "source": "markdown-structure",
                        "mineru_middle_json":
                            "<root>/" + (normalized_dir / "middle_json.json")
                            .relative_to(paths.root).as_posix(),
                    }),
                ))

            conn.execute("""
                UPDATE ingest_runs
                SET
                    doc_id = ?,
                    status = 'SUCCESS',
                    stage = 'CANONICALIZED',
                    finished_at = CURRENT_TIMESTAMP
                WHERE ingest_id = ?
            """, (doc_id, ingest_id))

    except Exception:
        shutil.rmtree(original_dir, ignore_errors=True)
        shutil.rmtree(normalized_dir, ignore_errors=True)
        shutil.rmtree(metadata_dir, ignore_errors=True)
        raise

    return {
        "duplicate": False,
        "doc_id": doc_id,
        "sha256": digest,
        "status": "CANONICALIZED",
        "sections": len(sections),
        "original": str(original_dest),
        "normalized": str(normalized_dir),
        "manifest": str(manifest_path),
    }
