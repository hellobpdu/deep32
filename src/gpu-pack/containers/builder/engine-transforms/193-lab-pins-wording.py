"""Finishes the engine cut's wording: the comments and docstrings that still call the release pins "lab-pinned" or "the lab pins" say "pinned" and "the release pins".

What it changes
  Comments and docstrings only, in six files: rag_core/api_v2.py (3), the engine's tests/test_prefer_release.py
  (2), rag_edge.py's render_also_in docstring (1) and its rendered copy tests/edge_242.py (1), the gateway's
  tests/test_also_in.py (2) and tests/test_prefer_release.py (2). "lab-pinned" becomes "pinned", "the lab pins"
  becomes "the release pins". No code, no string a client sees, no test assertion changes (the
  render_also_in docstring is not part of any tool description). Export transform 040 already made the
  gateway's user-visible sentence a setting; this removes the last internal mentions engine_cut_check.py finds.
Usage
  apply(tree_dir): tree_dir is the export tree (rag/core and rag/edge). Returns the changed files.
Exit
  Raises when a file is missing or a phrase appears a different number of times (so a rerun fails).
"""
import os

EDITS = {
    "rag/core/src/rag_core/api_v2.py": [(b"lab-pinned", b"pinned", 3)],
    "rag/core/tests/test_prefer_release.py": [(b"lab-pinned", b"pinned", 1), (b"the lab pins", b"the release pins", 1)],
    "rag/edge/rag_edge.py": [(b"lab-pinned", b"pinned", 1)],
    "rag/edge/tests/edge_242.py": [(b"lab-pinned", b"pinned", 1)],
    "rag/edge/tests/test_also_in.py": [(b"lab-pinned", b"pinned", 2)],
    "rag/edge/tests/test_prefer_release.py": [(b"lab-pinned", b"pinned", 1), (b"the lab pins", b"the release pins", 1)],
}


def apply(tree_dir):
    staged = {}
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(path, "rb") as f:
            data = f.read()
        for old, new, count in EDITS[rel]:
            found = data.count(old)
            if found != count:
                raise RuntimeError("%s: expected %d of %r, found %d; the target changed or this transform already ran"
                                   % (rel, count, old, found))
            data = data.replace(old, new)
        staged[rel] = (path, data)
    for rel, (path, data) in staged.items():
        with open(path, "wb") as f:
            f.write(data)
    return sorted(staged)
