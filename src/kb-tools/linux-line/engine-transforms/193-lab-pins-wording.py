"""Public knowledge-base source helper."""
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
