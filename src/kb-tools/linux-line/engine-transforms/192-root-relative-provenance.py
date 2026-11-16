"""Public knowledge-base source helper."""
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
