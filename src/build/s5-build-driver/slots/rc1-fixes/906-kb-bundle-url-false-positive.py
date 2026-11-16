"""Lets URLs pass the disc 1 KB importer's provenance test (release specification section 3).

What it changes
  Only the drive-letter alternative in provenance_bad: the drive letter must start
  the string or follow a character outside A-Z, a-z, 0-9, plus, dot and minus.
  The measured count on the post-902 tree is one; every other byte is preserved.
Usage
  apply(tree_dir): tree_dir is the root of the Workstation tree. Returns the changed files.
Exit
  Raises when the target is missing or the measured count differs, including on a second run.
"""
import os

RULES = [
    ("provenance drive-letter", r"re.search(r'[A-Za-z]:[/\\]|(?:/Users/|/home/)|\\Users\\'",
     r"re.search(r'(?<![A-Za-z0-9+.-])[A-Za-z]:[/\\]|(?:/Users/|/home/)|\\Users\\'"),
]

CONTENT = {
    "distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_bundle.py": {
        "provenance drive-letter": 1,
    },
}


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


def apply(tree_dir):
    out = []
    for rel in sorted(CONTENT):
        path = _path(tree_dir, rel)
        if not os.path.isfile(path):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(path, "rb") as stream:
            data = stream.read()
        for name, old, new in RULES:
            want = CONTENT[rel].get(name, 0)
            before = old.encode("ascii")
            after = new.encode("ascii")
            got = data.count(before)
            if got != want:
                raise ValueError("%s: rule %r expected %d, found %d" % (rel, name, want, got))
            if want:
                data = data.replace(before, after)
        with open(path, "wb") as stream:
            stream.write(data)
        out.append(rel)
    return sorted(set(out))
