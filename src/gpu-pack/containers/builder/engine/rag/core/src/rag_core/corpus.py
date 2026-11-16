"""The corpus package and its manifest.json.

The corpus package (orphan branch corpus-v1) holds the source texts and one
manifest describing them:

  manifest.json  [{doc_key, title, doc_type, path, source_url, platform,
                   release, sha256, bytes}, ...]
  rfc/rfcNNNN.txt, cisco/<doc_key>.txt, kb/*.json

doc_key is the file stem of `path`, and sha256/bytes describe that file.
Every document row built from the package carries its manifest entry: the
title in documents.title (display_title), doc_key in documents.source_id,
source_url in documents.source_uri, and the whole entry under
metadata_json["corpus"].
"""
from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from rag_core.db import connect


REQUIRED_KEYS = (
    "doc_key",
    "title",
    "doc_type",
    "path",
    "sha256",
)

DOC_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RFC_KEY_RE = re.compile(r"^rfc(\d+)$")
RFC_TITLE_RE = re.compile(r"^RFC\s*\d+\b")


def load_manifest(
    path: Path,
) -> list[dict[str, Any]]:
    """Read and validate manifest.json; raise ValueError on any bad entry."""
    entries = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(entries, list):
        raise ValueError(
            "manifest.json must be a list of documents"
        )

    seen: set[str] = set()

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"manifest entry {index} is not an object"
            )

        missing = [
            key
            for key in REQUIRED_KEYS
            if not entry.get(key)
        ]
        if missing:
            raise ValueError(
                f"manifest entry {index} lacks {missing}"
            )

        doc_key = entry["doc_key"]
        rel = PurePosixPath(entry["path"])

        if not DOC_KEY_RE.match(doc_key):
            raise ValueError(
                f"manifest doc_key {doc_key!r} is not a safe file stem"
            )
        if doc_key in seen:
            raise ValueError(
                f"manifest doc_key {doc_key!r} appears twice"
            )
        if rel.is_absolute() or ".." in rel.parts or rel.stem != doc_key:
            raise ValueError(
                f"manifest path {entry['path']!r} must be a relative path "
                f"to {doc_key}.<ext>"
            )
        if not SHA256_RE.match(entry["sha256"]):
            raise ValueError(
                f"manifest sha256 of {doc_key!r} is not a sha256 hex digest"
            )

        seen.add(doc_key)

    return entries


def display_title(
    entry: dict[str, Any],
) -> str:
    """documents.title: the manifest title, with "RFC <n>: " in front for an
    RFC. The chunker opens every chunk's embedding text with this title, and
    "RFC <n>: <title>" is what the measured staging roots carried."""
    title = str(entry["title"]).strip()
    number = RFC_KEY_RE.match(entry["doc_key"])

    if (
        entry["doc_type"] == "rfc"
        and number
        and not RFC_TITLE_RE.match(title)
    ):
        return f"RFC {int(number.group(1))}: {title}"

    return title


def text_kind(
    entry: dict[str, Any],
) -> str:
    """rag_core.text_adapter kind: "rfc" plain text, else "cisco" (ATX Markdown)."""
    return (
        "rfc"
        if entry["doc_type"] == "rfc"
        else "cisco"
    )


def apply_manifest_entry(
    db_path: Path,
    doc_id: str,
    entry: dict[str, Any],
) -> None:
    """Fill documents.title (display_title), source_id (doc_key), source_uri
    and metadata_json["corpus"] (the entry as is) from a manifest entry.
    Idempotent."""
    with connect(db_path) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute(
            "SELECT metadata_json FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()

        if row is None:
            raise KeyError(
                f"unknown document: {doc_id}"
            )

        metadata = json.loads(
            row["metadata_json"] or "{}"
        )
        metadata["corpus"] = entry

        conn.execute(
            """
            UPDATE documents
            SET
                title = ?,
                source_id = ?,
                source_uri = ?,
                metadata_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE doc_id = ?
            """,
            (
                display_title(entry),
                entry["doc_key"],
                entry.get("source_url"),
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                ),
                doc_id,
            ),
        )

        conn.commit()


def corpus_fingerprint(
    sha256s: Iterable[str],
) -> str:
    """Order-independent id of a document set: the same for the manifest's
    sha256 list and for the documents.sha256 column built from it."""
    digest = hashlib.sha256(
        "\n".join(
            sorted(sha256s)
        ).encode("ascii")
    )
    return digest.hexdigest()[:16]
