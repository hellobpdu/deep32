"""Makes the one build-root path the engine stores in section provenance root-relative: sections.provenance_json names "<root>/normalized/<doc_id>/middle_json.json" instead of the absolute path of the build machine (contract C3 rule T7).

What it changes
  rag/core/src/rag_core/ingest.py, one change: the value of "mineru_middle_json" in the provenance written for
  every section becomes the literal "<root>/" followed by the file's path relative to the engine root, in
  forward-slash form. It is the same string the fixture builder's rewrite produced after the
  build, now written at ingest, so a real build needs no rewrite pass and its rows keep the key order the engine
  writes. Nothing in the engine reads this value back (searched: chunker, dense, structure, api, scripts).
  documents.original_path, documents.canonical_path and ingest_runs.source_path stay absolute; a build at the
  neutral root /var/lib/deep32/rag-data/<generation> makes them the public installation path that C3 allows.
Usage
  apply(tree_dir): tree_dir holds rag/core/src/rag_core/ingest.py. Returns the changed files. Apply after 190.
Exit
  Raises when the target is missing or appears a different number of times (so a rerun fails): never a silent
  skip.
"""
import os

TARGET = "rag/core/src/rag_core/ingest.py"

OLD = (b'                        "mineru_middle_json":\n'
       b'                            str(normalized_dir / "middle_json.json"),\n')
NEW = (b'                        "mineru_middle_json":\n'
       b'                            "<root>/" + (normalized_dir / "middle_json.json")\n'
       b'                            .relative_to(paths.root).as_posix(),\n')


def apply(tree_dir):
    path = os.path.join(tree_dir, *TARGET.split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError("target missing: %s" % TARGET)
    with open(path, "rb") as f:
        data = f.read()
    found = data.count(OLD)
    if found != 1:
        raise RuntimeError("%s: expected 1 absolute provenance path, found %d; the target changed or this "
                           "transform already ran" % (TARGET, found))
    with open(path, "wb") as f:
        f.write(data.replace(OLD, NEW))
    return [TARGET]
