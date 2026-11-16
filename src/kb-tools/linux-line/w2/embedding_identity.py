#!/usr/bin/env python3
"""Check repository provenance and the served embedding name separately."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_COMPAT = Path("/srv/d32/s3/compat.json")


def from_inputs(inputs: dict, compat: Path = DEFAULT_COMPAT) -> dict:
    """Refuse missing identities or a compat/input repository pin mismatch."""
    if not isinstance(inputs, dict):
        raise ValueError("inputs manifest must be an object")
    document = json.loads(Path(compat).read_bytes())
    embedding = document.get("embedding") if isinstance(document, dict) else None
    if not isinstance(embedding, dict):
        raise ValueError("compat embedding must be an object")
    for field in ("model_id", "served_name", "revision"):
        value = embedding.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"compat embedding {field} missing")
    for field, source in (("model_id", "model_id"), ("revision", "model_revision")):
        value = inputs.get(source)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"inputs manifest {source} missing")
        if embedding[field] != value:
            raise ValueError(f"compat embedding {field} differs from inputs manifest {source}")
    return embedding


def check_merge(record: dict, inputs: dict, compat: Path = DEFAULT_COMPAT) -> dict:
    embedding = from_inputs(inputs, compat)
    for field, value in (("model_id", embedding["model_id"]),
                         ("embedding_model", embedding["served_name"]),
                         ("embedding_revision", embedding["revision"])):
        if record.get(field) != value:
            raise ValueError(f"flat manifest {field} differs from compat or inputs")
    return embedding
