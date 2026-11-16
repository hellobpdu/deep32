from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as f:
        while block := f.read(block_size):
            digest.update(block)

    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def document_id(sha256: str) -> str:
    return f"doc_{sha256}"


def chunk_id(doc_sha256: str, ordinal: int, text: str) -> str:
    material = (
        f"{doc_sha256}\0{ordinal}\0{text}"
    )

    digest = hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()

    return f"chk_{digest}"
