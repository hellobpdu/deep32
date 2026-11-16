"""Public knowledge-base source helper."""
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
