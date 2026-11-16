from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PARSED = "PARSED"
    CANONICALIZED = "CANONICALIZED"
    CHUNKED = "CHUNKED"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class ParseBackend(StrEnum):
    NATIVE = "native"
    PYMUPDF = "pymupdf"
    MINERU_STANDARD = "mineru-standard"
    MINERU_ADVANCED = "mineru-advanced"


class BoundingBox(BaseModel):
    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float


class ProvenanceSpan(BaseModel):
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    bboxes: list[BoundingBox] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    doc_id: str
    sha256: str
    source_name: str
    source_uri: str | None = None
    mime_type: str | None = None
    original_filename: str
    parse_backend: ParseBackend
    parser_version: str | None = None
    page_count: int | None = Field(default=None, ge=1)
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalSection(BaseModel):
    section_id: str
    doc_id: str
    parent_section_id: str | None = None
    ordinal: int = Field(ge=0)
    level: int = Field(ge=0)
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str = ""
    provenance: ProvenanceSpan = Field(default_factory=ProvenanceSpan)


class CanonicalChunk(BaseModel):
    chunk_id: str
    doc_id: str
    section_id: str | None = None
    ordinal: int = Field(ge=0)

    heading_path: list[str] = Field(default_factory=list)

    text: str
    embedding_text: str

    token_count: int = Field(ge=0)
    char_count: int = Field(ge=0)

    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None

    provenance: ProvenanceSpan = Field(default_factory=ProvenanceSpan)
    metadata: dict[str, Any] = Field(default_factory=dict)
