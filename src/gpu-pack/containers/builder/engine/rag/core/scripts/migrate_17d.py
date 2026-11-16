#!/usr/bin/env python3

from rag_core.db import connect
from rag_core.paths import RAGPaths


DOCUMENT_COLUMNS = [
    "source_id TEXT",
    "source_uri TEXT",
    "revision TEXT",
    "active INTEGER NOT NULL DEFAULT 1",
    "supersedes_doc_id TEXT",

    "source_page_count INTEGER",
    "parsed_page_count INTEGER",
    "parsed_pages_json TEXT NOT NULL DEFAULT '[]'",
    "is_full_document INTEGER NOT NULL DEFAULT 0",

    "parser_name TEXT",
    "parser_version TEXT",
    "parser_tier TEXT",
    "parse_mode TEXT",
    "parser_model TEXT",
    "parser_engine TEXT",

    "bbox_coordinate_space TEXT",

    "artifact_manifest_json TEXT NOT NULL DEFAULT '{}'",

    "readiness_eligible INTEGER NOT NULL DEFAULT 0",
    "readiness_reasons_json TEXT NOT NULL DEFAULT '[]'",

    "canonical_schema_version TEXT",
]


def existing_columns(conn, table):
    return {
        row["name"]
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        )
    }


def add_missing_columns(
    conn,
    table,
    definitions,
):
    existing = existing_columns(
        conn,
        table,
    )

    added = []

    for definition in definitions:
        name = definition.split()[0]

        if name in existing:
            continue

        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {definition}
            """
        )

        added.append(name)

    return added


def main():
    paths = RAGPaths.from_env()
    db = paths.sqlite / "rag.db"

    with connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")

        added = add_missing_columns(
            conn,
            "documents",
            DOCUMENT_COLUMNS,
        )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_artifacts (
                doc_id TEXT NOT NULL,
                artifact_name TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                required INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    doc_id,
                    artifact_name
                ),

                FOREIGN KEY (doc_id)
                    REFERENCES documents(doc_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS
                idx_document_artifacts_doc
            ON document_artifacts(doc_id);

            CREATE INDEX IF NOT EXISTS
                idx_documents_source_id
            ON documents(source_id);

            CREATE INDEX IF NOT EXISTS
                idx_documents_active
            ON documents(active);

            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT INTO schema_meta (
                key,
                value
            )
            VALUES (
                'canonical_schema_version',
                '17d-v1'
            )
            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value;
            """
        )

        conn.commit()

    print(
        "17D1 migration: PASS"
    )

    print(
        "added columns:",
        ", ".join(added)
        if added
        else "<already present>",
    )


if __name__ == "__main__":
    main()
