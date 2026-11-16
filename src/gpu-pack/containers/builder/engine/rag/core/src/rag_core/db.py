from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rag_core.paths import RAGPaths


SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    source_uri TEXT,
    mime_type TEXT,
    original_filename TEXT NOT NULL,

    original_path TEXT,
    canonical_path TEXT,

    parse_backend TEXT,
    parser_version TEXT,
    parse_tier TEXT,

    title TEXT,
    authors_json TEXT NOT NULL DEFAULT '[]',
    page_count INTEGER,

    status TEXT NOT NULL,

    metadata_json TEXT NOT NULL DEFAULT '{}',

    error_message TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_sha256
ON documents(sha256);

CREATE INDEX IF NOT EXISTS idx_documents_status
ON documents(status);


CREATE TABLE IF NOT EXISTS sections (
    section_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    parent_section_id TEXT,

    ordinal INTEGER NOT NULL,
    level INTEGER NOT NULL,

    heading TEXT,
    heading_path_json TEXT NOT NULL DEFAULT '[]',

    text TEXT NOT NULL DEFAULT '',

    page_start INTEGER,
    page_end INTEGER,
    provenance_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY(doc_id)
        REFERENCES documents(doc_id)
        ON DELETE CASCADE,

    FOREIGN KEY(parent_section_id)
        REFERENCES sections(section_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sections_doc_ordinal
ON sections(doc_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_sections_parent
ON sections(parent_section_id);


CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,

    chunk_id TEXT NOT NULL UNIQUE,
    chunk_sha256 TEXT NOT NULL,

    doc_id TEXT NOT NULL,
    section_id TEXT,

    ordinal INTEGER NOT NULL,

    heading_path_json TEXT NOT NULL DEFAULT '[]',

    text TEXT NOT NULL,
    embedding_text TEXT NOT NULL,

    token_count INTEGER NOT NULL,
    char_count INTEGER NOT NULL,

    page_start INTEGER,
    page_end INTEGER,

    prev_chunk_id TEXT,
    next_chunk_id TEXT,

    provenance_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(doc_id)
        REFERENCES documents(doc_id)
        ON DELETE CASCADE,

    FOREIGN KEY(section_id)
        REFERENCES sections(section_id)
        ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_doc_ordinal
ON chunks(doc_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_chunks_section
ON chunks(section_id);

CREATE INDEX IF NOT EXISTS idx_chunks_sha256
ON chunks(chunk_sha256);


CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    heading_path_json,
    content='chunks',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);


CREATE TRIGGER IF NOT EXISTS chunks_ai
AFTER INSERT ON chunks
BEGIN
    INSERT INTO chunk_fts(rowid, text, heading_path_json)
    VALUES (
        new.rowid,
        new.text,
        new.heading_path_json
    );
END;


CREATE TRIGGER IF NOT EXISTS chunks_ad
AFTER DELETE ON chunks
BEGIN
    INSERT INTO chunk_fts(
        chunk_fts,
        rowid,
        text,
        heading_path_json
    )
    VALUES (
        'delete',
        old.rowid,
        old.text,
        old.heading_path_json
    );
END;


CREATE TRIGGER IF NOT EXISTS chunks_au
AFTER UPDATE ON chunks
BEGIN
    INSERT INTO chunk_fts(
        chunk_fts,
        rowid,
        text,
        heading_path_json
    )
    VALUES (
        'delete',
        old.rowid,
        old.text,
        old.heading_path_json
    );

    INSERT INTO chunk_fts(
        rowid,
        text,
        heading_path_json
    )
    VALUES (
        new.rowid,
        new.text,
        new.heading_path_json
    );
END;


CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,

    asset_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,

    page INTEGER,
    bbox_json TEXT,

    caption TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY(doc_id)
        REFERENCES documents(doc_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assets_doc
ON assets(doc_id);


CREATE TABLE IF NOT EXISTS index_state (
    chunk_id TEXT PRIMARY KEY,

    lexical_indexed INTEGER NOT NULL DEFAULT 0,
    dense_indexed INTEGER NOT NULL DEFAULT 0,
    late_interaction_indexed INTEGER NOT NULL DEFAULT 0,

    embedding_model TEXT,
    embedding_revision TEXT,

    colbert_model TEXT,
    colbert_revision TEXT,

    indexed_at TEXT,

    FOREIGN KEY(chunk_id)
        REFERENCES chunks(chunk_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS ingest_runs (
    ingest_id TEXT PRIMARY KEY,

    doc_id TEXT,
    source_path TEXT NOT NULL,

    status TEXT NOT NULL,
    stage TEXT NOT NULL,

    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,

    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',

    FOREIGN KEY(doc_id)
        REFERENCES documents(doc_id)
        ON DELETE SET NULL
);
"""


# Opt-in doc types in a BM25 table of their own (scripts/build_root.py --opt-in-doc-types). chunk_fts then holds
# exactly the other chunks, so a search that leaves the opt-in doc types out ranks over the very rows, and with the
# very N, document frequencies and average lengths, of a root built without them (rag_core.retrievers.lexical);
# with all chunks in one table and the opt-in rows filtered out, bm25() still counted them. A root without opt-in
# doc types has neither table, and its triggers are SCHEMA's.
DEFAULT_FTS = "chunk_fts"
OPTIN_FTS = "chunk_fts_optin"
# the documents whose chunks chunk_fts_optin holds; every other chunk is in chunk_fts
OPTIN_DOCS = "chunk_fts_optin_docs"

# The chunk triggers of a split root: each chunk goes to the table of its document, never to both. They replace
# SCHEMA's under the same names, so initialize() (CREATE TRIGGER IF NOT EXISTS) keeps them.
OPTIN_TRIGGERS = (
    """
    CREATE TRIGGER chunks_ai
    AFTER INSERT ON chunks
    BEGIN
        INSERT INTO chunk_fts(rowid, text, heading_path_json)
        SELECT new.rowid, new.text, new.heading_path_json
        WHERE new.doc_id NOT IN (SELECT doc_id FROM chunk_fts_optin_docs);

        INSERT INTO chunk_fts_optin(rowid, text, heading_path_json)
        SELECT new.rowid, new.text, new.heading_path_json
        WHERE new.doc_id IN (SELECT doc_id FROM chunk_fts_optin_docs);
    END
    """,
    """
    CREATE TRIGGER chunks_ad
    AFTER DELETE ON chunks
    BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, text, heading_path_json)
        SELECT 'delete', old.rowid, old.text, old.heading_path_json
        WHERE old.doc_id NOT IN (SELECT doc_id FROM chunk_fts_optin_docs);

        INSERT INTO chunk_fts_optin(chunk_fts_optin, rowid, text, heading_path_json)
        SELECT 'delete', old.rowid, old.text, old.heading_path_json
        WHERE old.doc_id IN (SELECT doc_id FROM chunk_fts_optin_docs);
    END
    """,
    """
    CREATE TRIGGER chunks_au
    AFTER UPDATE ON chunks
    BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, text, heading_path_json)
        SELECT 'delete', old.rowid, old.text, old.heading_path_json
        WHERE old.doc_id NOT IN (SELECT doc_id FROM chunk_fts_optin_docs);

        INSERT INTO chunk_fts_optin(chunk_fts_optin, rowid, text, heading_path_json)
        SELECT 'delete', old.rowid, old.text, old.heading_path_json
        WHERE old.doc_id IN (SELECT doc_id FROM chunk_fts_optin_docs);

        INSERT INTO chunk_fts(rowid, text, heading_path_json)
        SELECT new.rowid, new.text, new.heading_path_json
        WHERE new.doc_id NOT IN (SELECT doc_id FROM chunk_fts_optin_docs);

        INSERT INTO chunk_fts_optin(rowid, text, heading_path_json)
        SELECT new.rowid, new.text, new.heading_path_json
        WHERE new.doc_id IN (SELECT doc_id FROM chunk_fts_optin_docs);
    END
    """,
)

# every chunk into the table of its document, in rowid order (as the triggers fill a table during a build)
FILL_SQL = """
INSERT INTO {table}(rowid, text, heading_path_json)
SELECT rowid, text, heading_path_json
FROM chunks
WHERE doc_id {op} (SELECT doc_id FROM chunk_fts_optin_docs)
ORDER BY rowid
"""

# rows of a BM25 table (its docsize shadow table: one row per indexed chunk), and those of the wrong documents
SPLIT_CHECK_SQL = """
SELECT
    (SELECT count(*) FROM {table}_docsize),
    (SELECT count(*) FROM chunks WHERE doc_id {op} (SELECT doc_id FROM chunk_fts_optin_docs)),
    (SELECT count(*) FROM {table}_docsize AS d JOIN chunks AS c ON c.rowid = d.id
     WHERE c.doc_id {op} (SELECT doc_id FROM chunk_fts_optin_docs))
"""


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def is_split(conn: sqlite3.Connection) -> bool:
    """True when the root keeps the opt-in doc types' chunks in chunk_fts_optin (split_optin_fts)."""
    return has_table(conn, OPTIN_FTS) and has_table(conn, OPTIN_DOCS)


def split_optin_fts(conn: sqlite3.Connection, doc_ids) -> None:
    """Index the chunks of `doc_ids` (the documents of the opt-in doc types) in chunk_fts_optin and every other
    chunk in chunk_fts, in one transaction: chunk_fts_optin made with chunk_fts's own definition (the CREATE
    statement this database holds, renamed: same columns, tokenizer and external content), the documents recorded
    in chunk_fts_optin_docs, the chunk triggers routed (OPTIN_TRIGGERS), and both tables emptied ('delete-all')
    and filled again from chunks, each with its own rows only: never an FTS 'rebuild', which would index every
    chunk in one table. Idempotent: whatever the tables held before (a resumed build adds or re-chunks
    documents), they hold the split afterwards. The caller checks and optimizes them (check_optin_split)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_table(conn, OPTIN_FTS):
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (DEFAULT_FTS,)
            ).fetchone()[0]
            if ddl.count(DEFAULT_FTS) != 1:
                raise RuntimeError(f"unexpected {DEFAULT_FTS} definition: {ddl}")
            conn.execute(ddl.replace(DEFAULT_FTS, OPTIN_FTS))
        conn.execute(f"CREATE TABLE IF NOT EXISTS {OPTIN_DOCS} (doc_id TEXT PRIMARY KEY)")
        conn.execute(f"DELETE FROM {OPTIN_DOCS}")
        conn.executemany(f"INSERT INTO {OPTIN_DOCS} (doc_id) VALUES (?)", [(d,) for d in sorted(set(doc_ids))])
        for name in ("chunks_ai", "chunks_ad", "chunks_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        for trigger in OPTIN_TRIGGERS:
            conn.execute(trigger)
        for table, op in ((DEFAULT_FTS, "NOT IN"), (OPTIN_FTS, "IN")):
            conn.execute(f"INSERT INTO {table}({table}) VALUES ('delete-all')")
            conn.execute(FILL_SQL.format(table=table, op=op))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# FTS5's check of an external-content table's index alone: rank 0 leaves the content table out, rank 1 also compares
# the index with it. Each table of a split root indexes part of chunks, so any check that compares one with chunks
# reports it corrupt ("database disk image is malformed"): rank 1 on every SQLite, and every form, the bare one and
# rank 0 included, on an SQLite as old as 3.33.0 (3.39.4 has rank 0). Hence a probe of the library, not its version.
INDEX_CHECK_SQL = "INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 0)"


def fts5_checks_index_alone() -> bool:
    """True when this SQLite checks an external-content FTS5 index without comparing it with its content table
    (INDEX_CHECK_SQL), as a split root's tables need: probed on an in-memory table that indexes one of its content
    table's two rows."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE content (text)")
        conn.execute("CREATE VIRTUAL TABLE fts USING fts5(text, content='content')")
        conn.executemany("INSERT INTO content (rowid, text) VALUES (?, ?)", [(1, "indexed"), (2, "elsewhere")])
        conn.execute("INSERT INTO fts (rowid, text) VALUES (1, 'indexed')")
        try:
            conn.execute(INDEX_CHECK_SQL.format(table="fts"))
        except sqlite3.DatabaseError:
            return False
        return True
    finally:
        conn.close()


def check_optin_split(conn: sqlite3.Connection) -> dict[str, int]:
    """Rows of chunk_fts and chunk_fts_optin, each exactly the chunks of its documents (RuntimeError otherwise)."""
    counts = {}
    for table, op in ((DEFAULT_FTS, "NOT IN"), (OPTIN_FTS, "IN")):
        rows, wanted, right = conn.execute(SPLIT_CHECK_SQL.format(table=table, op=op)).fetchone()
        if not rows == wanted == right:
            raise RuntimeError(f"{table} holds {rows} rows, {right} of them chunks it should hold, of {wanted}")
        counts[table] = rows
    return counts


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA busy_timeout=5000")

    return conn


def initialize(paths: RAGPaths) -> Path:
    paths.ensure()

    db_path = paths.sqlite / "rag.db"

    with connect(db_path) as conn:
        conn.executescript(SCHEMA)

        # Validate that this SQLite build really has FTS5.
        conn.execute(
            "SELECT rowid FROM chunk_fts LIMIT 1"
        ).fetchall()

    return db_path


def database_health(paths: RAGPaths) -> dict:
    db_path = paths.sqlite / "rag.db"

    with connect(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                """
            )
        }

        result = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        return {
            "database": str(db_path),
            "integrity": result,
            "documents": conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0],
            "chunks": conn.execute(
                "SELECT COUNT(*) FROM chunks"
            ).fetchone()[0],
            "fts5": "chunk_fts" in tables,
        }
