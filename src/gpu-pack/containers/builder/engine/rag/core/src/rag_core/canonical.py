from __future__ import annotations

import hashlib
import json
import os

from pathlib import Path
from typing import Any

from rag_core.db import connect
from rag_core.paths import RAGPaths


CANONICAL_SCHEMA_VERSION = "17d-v1"


def sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            block = f.read(
                chunk_size
            )

            if not block:
                break

            h.update(block)

    return h.hexdigest()


def _positive_int(
    value: Any,
) -> int | None:
    if isinstance(
        value,
        bool,
    ):
        return None

    if isinstance(
        value,
        int,
    ) and value > 0:
        return value

    return None


def _nested_get(
    obj: Any,
    *keys: str,
) -> Any:
    current = obj

    for key in keys:
        if not isinstance(
            current,
            dict,
        ):
            return None

        current = current.get(key)

    return current


def _deep_find_first(
    obj: Any,
    names: set[str],
) -> Any:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key in names
                and value not in (
                    None,
                    "",
                    [],
                    {},
                )
            ):
                return value

        for value in obj.values():
            found = _deep_find_first(
                value,
                names,
            )

            if found not in (
                None,
                "",
                [],
                {},
            ):
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = _deep_find_first(
                value,
                names,
            )

            if found not in (
                None,
                "",
                [],
                {},
            ):
                return found

    return None


def _collect_page_map_values(
    obj: Any,
) -> list[int]:
    values: list[int] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "page_index_map":
                candidates = (
                    value
                    if isinstance(
                        value,
                        list,
                    )
                    else [value]
                )

                for candidate in candidates:
                    if (
                        isinstance(
                            candidate,
                            int,
                        )
                        and not isinstance(
                            candidate,
                            bool,
                        )
                        and candidate >= 0
                    ):
                        values.append(
                            candidate
                        )

            else:
                values.extend(
                    _collect_page_map_values(
                        value
                    )
                )

    elif isinstance(obj, list):
        for value in obj:
            values.extend(
                _collect_page_map_values(
                    value
                )
            )

    return values


def derive_source_page_map(
    structured: dict[str, Any],
    model_output:
        dict[str, Any] | None,
) -> dict[int, int]:
    """
    Return:

        local MinerU page_idx
            ->
        original source page number

    Page numbers returned to users are 1-based.
    """

    structured_pages = (
        structured.get(
            "pages",
            [],
        )
    )

    mapping: dict[int, int] = {}

    if isinstance(
        model_output,
        dict,
    ):
        model_pages = model_output.get(
            "pages",
            [],
        )

        if isinstance(
            model_pages,
            list,
        ):
            for local_index, page in enumerate(
                model_pages
            ):
                values = (
                    _collect_page_map_values(
                        page
                    )
                )

                if values:
                    mapping[
                        local_index
                    ] = (
                        values[0] + 1
                    )

    if not mapping and model_output:
        values = (
            _collect_page_map_values(
                model_output
            )
        )

        unique = list(
            dict.fromkeys(values)
        )

        for local_index, original_index in enumerate(
            unique
        ):
            mapping[
                local_index
            ] = (
                original_index + 1
            )

    for position, page in enumerate(
        structured_pages
    ):
        local_index = int(
            page.get(
                "page_idx",
                position,
            )
        )

        mapping.setdefault(
            local_index,
            local_index + 1,
        )

    return mapping


def discover_page_semantics(
    structured: dict[str, Any],
    middle:
        dict[str, Any] | None = None,
    model_output:
        dict[str, Any] | None = None,
) -> dict[str, Any]:

    documents = [
        structured,
        middle or {},
        model_output or {},
    ]

    source_counts = []

    for obj in documents:
        count = _positive_int(
            _nested_get(
                obj,
                "metadata",
                "document",
                "page_count",
            )
        )

        if count:
            source_counts.append(
                count
            )

    source_page_count = (
        max(source_counts)
        if source_counts
        else None
    )

    page_map = (
        derive_source_page_map(
            structured,
            model_output,
        )
    )

    parsed_pages = sorted(
        set(page_map.values())
    )

    parsed_page_count = len(
        parsed_pages
    )

    explicit_full_flags = []

    for obj in documents:
        if not isinstance(
            obj,
            dict,
        ):
            continue

        value = obj.get(
            "is_full_document"
        )

        if isinstance(
            value,
            bool,
        ):
            explicit_full_flags.append(
                value
            )

    mathematically_full = False

    if (
        source_page_count
        and parsed_page_count
        == source_page_count
    ):
        mathematically_full = (
            parsed_pages
            == list(
                range(
                    1,
                    source_page_count + 1,
                )
            )
        )

    if False in explicit_full_flags:
        is_full_document = False

    elif True in explicit_full_flags:
        is_full_document = (
            mathematically_full
        )

    else:
        is_full_document = (
            mathematically_full
        )

    return {
        "source_page_count":
            source_page_count,

        "parsed_page_count":
            parsed_page_count,

        "parsed_pages":
            parsed_pages,

        "is_full_document":
            is_full_document,

        "page_map":
            page_map,
    }


def detect_bbox_coordinate_space(
    structured: dict[str, Any],
) -> str:
    bboxes = []

    for page in structured.get(
        "pages",
        [],
    ):
        for block in page.get(
            "blocks",
            [],
        ):
            if not isinstance(
                block,
                dict,
            ):
                continue

            bbox = block.get("bbox")

            if (
                isinstance(
                    bbox,
                    list,
                )
                and len(bbox) == 4
                and all(
                    isinstance(
                        v,
                        (int, float),
                    )
                    for v in bbox
                )
            ):
                bboxes.append(bbox)

    if not bboxes:
        return "unknown"

    if all(
        0.0 <= float(value) <= 1.0
        for bbox in bboxes
        for value in bbox
    ):
        return "normalized_0_1"

    return "unknown_or_absolute"


def discover_runtime_identity(
    structured: dict[str, Any],
    worker_result:
        dict[str, Any] | None = None,
) -> dict[str, Any]:

    worker_result = (
        worker_result or {}
    )

    producer = (
        _nested_get(
            structured,
            "metadata",
            "producer",
        )
        or {}
    )

    mineru_ext = (
        _nested_get(
            structured,
            "extensions",
            "mineru",
        )
        or _nested_get(
            structured,
            "metadata",
            "extensions",
            "mineru",
        )
        or {}
    )

    parser_name = (
        producer.get("name")
        if isinstance(
            producer,
            dict,
        )
        else None
    )

    parser_version = (
        producer.get("version")
        if isinstance(
            producer,
            dict,
        )
        else None
    )

    parser_tier = (
        mineru_ext.get("tier")
        if isinstance(
            mineru_ext,
            dict,
        )
        else None
    )

    parse_mode = (
        mineru_ext.get(
            "parse_mode"
        )
        if isinstance(
            mineru_ext,
            dict,
        )
        else None
    )

    parser_model = (
        _deep_find_first(
            worker_result,
            {
                "model_id",
                "model_name",
            },
        )
        or (
            mineru_ext.get(
                "model"
            )
            if isinstance(
                mineru_ext,
                dict,
            )
            else None
        )
    )

    parser_engine = (
        _deep_find_first(
            worker_result,
            {
                "engine",
                "inference_engine",
            },
        )
    )

    return {
        "parser_name":
            (
                str(parser_name)
                if parser_name
                else None
            ),

        "parser_version":
            (
                str(parser_version)
                if parser_version
                else None
            ),

        "parser_tier":
            (
                str(parser_tier)
                if parser_tier
                else None
            ),

        "parse_mode":
            (
                str(parse_mode)
                if parse_mode
                else None
            ),

        "parser_model":
            (
                str(parser_model)
                if parser_model
                else None
            ),

        "parser_engine":
            (
                str(parser_engine)
                if parser_engine
                else None
            ),
    }


def assess_canonical_readiness(
    *,
    source_page_count:
        int | None,
    parsed_pages:
        list[int],
    is_full_document: bool,
    missing_required:
        list[str] | None = None,
) -> tuple[bool, list[str]]:

    reasons: list[str] = []

    missing_required = (
        missing_required or []
    )

    if source_page_count is None:
        reasons.append(
            "source_page_count_unknown"
        )

    if not parsed_pages:
        reasons.append(
            "no_parsed_pages"
        )

    if (
        source_page_count
        is not None
        and parsed_pages
        and len(parsed_pages)
        != source_page_count
    ):
        reasons.append(
            "parsed_page_count_mismatch"
        )

    if (
        source_page_count
        is not None
        and parsed_pages
        and parsed_pages
        != list(
            range(
                1,
                source_page_count + 1,
            )
        )
    ):
        reasons.append(
            "parsed_pages_not_complete_sequence"
        )

    if not is_full_document:
        reasons.append(
            "partial_document"
        )

    for artifact in sorted(
        missing_required
    ):
        reasons.append(
            f"missing_artifact:{artifact}"
        )

    return (
        len(reasons) == 0,
        reasons,
    )


def _load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.is_file():
        return {}

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def _artifact_record(
    root: Path,
    path: Path,
    name: str,
    required: bool,
) -> dict[str, Any]:
    return {
        "artifact_name":
            name,

        "rel_path":
            str(
                path.resolve().relative_to(
                    root.resolve()
                )
            ),

        "sha256":
            sha256_file(path),

        "size_bytes":
            path.stat().st_size,

        "required":
            bool(required),
    }


def harden_document(
    doc_id: str,
) -> dict[str, Any]:

    paths = RAGPaths.from_env()

    root = Path(
        os.environ["RAG_ROOT"]
    )

    db = (
        paths.sqlite
        / "rag.db"
    )

    with connect(db) as conn:
        doc = conn.execute(
            """
            SELECT *
            FROM documents
            WHERE doc_id = ?
            """,
            (doc_id,),
        ).fetchone()

        if doc is None:
            raise KeyError(
                f"unknown document: "
                f"{doc_id}"
            )

        doc = dict(doc)

    normalized_dir = (
        paths.normalized
        / doc_id
    )

    structured_path = (
        normalized_dir
        / "structured_content.json"
    )

    middle_path = (
        normalized_dir
        / "middle_json.json"
    )

    model_output_path = (
        normalized_dir
        / "model_output.json"
    )

    worker_result_path = (
        normalized_dir
        / "worker_result.json"
    )

    markdown_path = (
        normalized_dir
        / "markdown.md"
    )

    structured = _load_json(
        structured_path
    )

    if not structured:
        raise RuntimeError(
            "structured_content.json "
            "missing or empty"
        )

    middle = _load_json(
        middle_path
    )

    model_output = _load_json(
        model_output_path
    )

    worker_result = _load_json(
        worker_result_path
    )

    semantics = (
        discover_page_semantics(
            structured,
            middle,
            model_output,
        )
    )

    runtime = (
        discover_runtime_identity(
            structured,
            worker_result,
        )
    )

    original_filename = (
        doc.get(
            "original_filename"
        )
    )

    original_path = (
        paths.originals
        / doc_id
        / original_filename
    )

    artifact_specs = [
        (
            "original",
            original_path,
            True,
        ),
        (
            "markdown",
            markdown_path,
            True,
        ),
        (
            "structured_content",
            structured_path,
            True,
        ),
        (
            "middle_json",
            middle_path,
            True,
        ),
        (
            "model_output",
            model_output_path,
            True,
        ),
        (
            "worker_result",
            worker_result_path,
            True,
        ),
    ]

    artifacts = []

    missing_required = []

    for (
        name,
        path,
        required,
    ) in artifact_specs:

        if not path.is_file():
            if required:
                missing_required.append(
                    name
                )

            continue

        artifacts.append(
            _artifact_record(
                root,
                path,
                name,
                required,
            )
        )

    eligible, reasons = (
        assess_canonical_readiness(
            source_page_count=(
                semantics[
                    "source_page_count"
                ]
            ),
            parsed_pages=(
                semantics[
                    "parsed_pages"
                ]
            ),
            is_full_document=(
                semantics[
                    "is_full_document"
                ]
            ),
            missing_required=(
                missing_required
            ),
        )
    )

    bbox_space = (
        detect_bbox_coordinate_space(
            structured
        )
    )

    manifest = {
        "schema_version":
            CANONICAL_SCHEMA_VERSION,

        "doc_id":
            doc_id,

        "artifacts":
            artifacts,
    }

    source_id = (
        doc.get("source_id")
        or doc_id
    )

    with connect(db) as conn:
        conn.execute(
            "BEGIN IMMEDIATE"
        )

        conn.execute(
            """
            UPDATE documents

            SET
                source_id = ?,
                source_page_count = ?,
                parsed_page_count = ?,
                parsed_pages_json = ?,
                is_full_document = ?,

                parser_name = ?,
                parser_version = ?,
                parser_tier = ?,
                parse_mode = ?,
                parser_model = ?,
                parser_engine = ?,

                bbox_coordinate_space = ?,

                artifact_manifest_json = ?,

                readiness_eligible = ?,
                readiness_reasons_json = ?,

                canonical_schema_version = ?,
                updated_at =
                    CURRENT_TIMESTAMP

            WHERE doc_id = ?
            """,
            (
                source_id,

                semantics[
                    "source_page_count"
                ],

                semantics[
                    "parsed_page_count"
                ],

                json.dumps(
                    semantics[
                        "parsed_pages"
                    ]
                ),

                int(
                    semantics[
                        "is_full_document"
                    ]
                ),

                runtime[
                    "parser_name"
                ],

                runtime[
                    "parser_version"
                ],

                runtime[
                    "parser_tier"
                ],

                runtime[
                    "parse_mode"
                ],

                runtime[
                    "parser_model"
                ],

                runtime[
                    "parser_engine"
                ],

                bbox_space,

                json.dumps(
                    manifest,
                    ensure_ascii=False,
                ),

                int(eligible),

                json.dumps(
                    reasons,
                    ensure_ascii=False,
                ),

                CANONICAL_SCHEMA_VERSION,

                doc_id,
            ),
        )

        conn.execute(
            """
            DELETE FROM document_artifacts
            WHERE doc_id = ?
            """,
            (doc_id,),
        )

        for artifact in artifacts:
            conn.execute(
                """
                INSERT INTO document_artifacts (
                    doc_id,
                    artifact_name,
                    rel_path,
                    sha256,
                    size_bytes,
                    required
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    artifact[
                        "artifact_name"
                    ],
                    artifact[
                        "rel_path"
                    ],
                    artifact[
                        "sha256"
                    ],
                    artifact[
                        "size_bytes"
                    ],
                    int(
                        artifact[
                            "required"
                        ]
                    ),
                ),
            )

        conn.commit()

    return {
        "doc_id":
            doc_id,

        "source_page_count":
            semantics[
                "source_page_count"
            ],

        "parsed_page_count":
            semantics[
                "parsed_page_count"
            ],

        "parsed_pages":
            semantics[
                "parsed_pages"
            ],

        "is_full_document":
            semantics[
                "is_full_document"
            ],

        "readiness_eligible":
            eligible,

        "readiness_reasons":
            reasons,

        "bbox_coordinate_space":
            bbox_space,

        "runtime":
            runtime,

        "artifact_count":
            len(artifacts),
    }


def assert_document_can_be_ready(
    doc_id: str,
) -> None:
    paths = RAGPaths.from_env()

    db = paths.sqlite / "rag.db"

    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT
                readiness_eligible,
                readiness_reasons_json
            FROM documents
            WHERE doc_id = ?
            """,
            (doc_id,),
        ).fetchone()

    if row is None:
        raise KeyError(
            f"unknown document: {doc_id}"
        )

    if not bool(
        row["readiness_eligible"]
    ):
        reasons = json.loads(
            row["readiness_reasons_json"]
            or "[]"
        )

        raise RuntimeError(
            "document cannot become READY: "
            + ", ".join(reasons)
        )
