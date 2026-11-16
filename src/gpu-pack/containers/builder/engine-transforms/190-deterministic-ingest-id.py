"""Makes the engine's ingest audit id deterministic: ingest_runs.ingest_id is derived from the ingest's inputs instead of uuid4.

What it changes
  rag/core/src/rag_core/ingest.py, one change in two places:
  - a new function deterministic_ingest_id() above ingest_pdf(). The id is "ing_" followed by the first 32
    hex digits (the same shape uuid4().hex gave) of SHA-256 over: the fixed namespace "deep32-ingest-id/1", the
    document's SHA-256, the source name, the source URI (empty when none), the original file's path relative
    to the engine root in forward-slash form (its file name alone when it lies outside the root) and an
    ordinal. The ordinal is the first value from 0 up whose id is not yet in ingest_runs, so a duplicate or a
    re-ingest of the same file gets the next free id and the primary key never collides.
  - ingest_pdf() calls it where it called uuid.uuid4().
  Nothing else changes: the same rows are written, in the same order, with the same columns. Two builds of one
  corpus, in the same document order, now write the same ingest ids at any build root, so rag.db is byte for byte
  the same once the clock columns are equal and the build root path is the same (documents.original_path,
  canonical_path and ingest_runs.source_path still name it; the fixture builder sets both). The import
  of uuid stays (unused) so that the transform touches only what it must.
Usage
  apply(tree_dir): tree_dir holds rag/core/src/rag_core/ingest.py (the export tree or a vendored copy of it).
  Returns the changed files. Imported by export/run-export.py like the other engine transforms.
Exit
  Raises when a target is missing or appears a different number of times, so it applies once and fails on a
  rerun or on an empty tree: never a silent skip.
"""
import os

TARGET = "rag/core/src/rag_core/ingest.py"

OLD_CALL = b'    ingest_id = "ing_" + uuid.uuid4().hex\n'
NEW_CALL = b'    ingest_id = deterministic_ingest_id(paths, digest, original, source_name, source_uri)\n'

ANCHOR = b"def ingest_pdf(\n"
HELPER = b'''INGEST_ID_NAMESPACE = "deep32-ingest-id/1"


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
            material = "\\0".join((
                INGEST_ID_NAMESPACE, digest, source_name, source_uri or "", where, str(ordinal),
            ))
            candidate = "ing_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
            taken = conn.execute(
                "SELECT 1 FROM ingest_runs WHERE ingest_id = ?", (candidate,)
            ).fetchone()
            if taken is None:
                return candidate
            ordinal += 1


'''


def apply(tree_dir):
    path = os.path.join(tree_dir, *TARGET.split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError("target missing: %s" % TARGET)
    with open(path, "rb") as f:
        data = f.read()
    for needle, name in ((OLD_CALL, "the uuid4 ingest id line"), (ANCHOR, "the ingest_pdf definition")):
        found = data.count(needle)
        if found != 1:
            raise RuntimeError("%s: expected 1 of %s, found %d; the target changed or this transform already ran"
                               % (TARGET, name, found))
    if b"def deterministic_ingest_id(" in data:
        raise RuntimeError("%s: deterministic_ingest_id already present: this transform already ran" % TARGET)
    data = data.replace(OLD_CALL, NEW_CALL).replace(ANCHOR, HELPER + ANCHOR)
    with open(path, "wb") as f:
        f.write(data)
    return [TARGET]
