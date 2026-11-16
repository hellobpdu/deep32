"""Makes kb.sqlite's key tables independent of Python's hash seed: build_kb inserts the iana_key and glossary_key rows of one entry in sorted order instead of set order.

What it changes
  rag/core/src/rag_core/kb.py, one change in two places: the two lines

      "INSERT INTO iana_key VALUES (?, ?)", [(k, i) for k in keys if k]
      "INSERT INTO glossary_key VALUES (?, ?)", [(k, i) for k in keys if k]

  iterate sorted(keys) instead of the set keys. A set of strings iterates in an order that follows the
  interpreter's hash seed, and -I (which implies -E) gives every process a random seed, so two builds wrote the
  same rows in different orders and kb.sqlite differed in bytes while its sorted dump was equal. The rows, their
  values and every lookup result stay the same; only the physical row order is now fixed.
Usage
  apply(tree_dir): tree_dir holds rag/core/src/rag_core/kb.py. Returns the changed files.
Exit
  Raises when a target is missing or appears a different number of times (so a rerun fails): never a silent skip.
"""
import os

TARGET = "rag/core/src/rag_core/kb.py"

EDITS = [
    (b'"INSERT INTO iana_key VALUES (?, ?)", [(k, i) for k in keys if k]',
     b'"INSERT INTO iana_key VALUES (?, ?)", [(k, i) for k in sorted(keys) if k]'),
    (b'"INSERT INTO glossary_key VALUES (?, ?)", [(k, i) for k in keys if k]',
     b'"INSERT INTO glossary_key VALUES (?, ?)", [(k, i) for k in sorted(keys) if k]'),
]


def apply(tree_dir):
    path = os.path.join(tree_dir, *TARGET.split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError("target missing: %s" % TARGET)
    with open(path, "rb") as f:
        data = f.read()
    for old, new in EDITS:
        found = data.count(old)
        if found != 1:
            raise RuntimeError("%s: expected 1 of %r, found %d; the target changed or this transform already ran"
                               % (TARGET, old[:40], found))
        data = data.replace(old, new)
    with open(path, "wb") as f:
        f.write(data)
    return [TARGET]
