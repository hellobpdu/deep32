"""Restores disc 1's Library start page from its own generator with no installed packs (source review; release specification section 15 items 33-34).

PROPOSED for the lead's slot 990-999; nothing applies it unless the lead passes
--slot lead=<this folder> to apply-transforms.py.

What it changes
  ship/tools/mkindex.py: --empty reads no pack manifests and writes the existing
    page head, style, title and heading, followed by the closing tags. The normal
    manifest-based invocation and its output are unchanged.
  ship/tools/stage.sh: disc 1 runs that generator with --empty, so d32lib -home
    has a valid page before disc 2 imports any packs, and installs the generator
    in the existing ship libexec folder. The importer regenerates the page from
    installed manifests after each import (the command is in DOUBTS.md).
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101 to 806 and 990.
    Returns the changed files.
Exit
  Raises when a target file or text is missing or appears a different number of
    times: never a silent skip. A second run raises.
"""
import os

EDITS = {
    "ship/tools/mkindex.py": [
        ("root = sys.argv[1]\n",
         'empty = sys.argv[1:] == ["--empty"]\n'
         'root = "" if empty else sys.argv[1]\n', 1),
        ('with open(os.path.join(raw, "cisco", "manifest.jsonl"), encoding="utf-8") as f:\n'
         '    for line in f:\n'
         '        d = json.loads(line)\n'
         '        if d.get("role") == "hub" or not d.get("file"):\n'
         '            continue\n'
         '        cisco.append((d.get("title") or os.path.basename(d["file"]), d.get("date") or "", d["file"], d.get("role")))\n',
         'if not empty:\n'
         '    with open(os.path.join(raw, "cisco", "manifest.jsonl"), encoding="utf-8") as f:\n'
         '        for line in f:\n'
         '            d = json.loads(line)\n'
         '            if d.get("role") == "hub" or not d.get("file"):\n'
         '                continue\n'
         '            cisco.append((d.get("title") or os.path.basename(d["file"]), d.get("date") or "", d["file"], d.get("role")))\n', 1),
        ('rfc_n = len(os.listdir(os.path.join(raw, "ietf", "rfc", "txt")))\n'
         'draft_n = len(os.listdir(os.path.join(raw, "ietf", "drafts", "txt")))\n',
         'rfc_n = 0 if empty else len(os.listdir(os.path.join(raw, "ietf", "rfc", "txt")))\n'
         'draft_n = 0 if empty else len(os.listdir(os.path.join(raw, "ietf", "drafts", "txt")))\n', 1),
        ('if encyc is None:\n',
         'if empty:\n'
         '    w("<h1>Deep32/UX Library</h1>")\n'
         '    w("</body></html>")\n'
         '    sys.stdout.write("\\n".join(out) + "\\n")\n'
         '    raise SystemExit(0)\n'
         'if encyc is None:\n', 1),
    ],
    "ship/tools/stage.sh": [
        ('\t/usr/local/bin/python3.14 -I $S/ship/tools/mkindex.py $L/libsrc >$A/usr/local/share/d32/library/index.html\n',
         '\tinstall -m 0444 $S/ship/tools/mkindex.py $A/usr/local/d32/ship/libexec/mkindex.py\n'
         '\t/usr/local/bin/python3.14 -I $S/ship/tools/mkindex.py --empty >$A/usr/local/share/d32/library/index.html\n', 1),
    ],
}


def apply(tree_dir):
    changed = []
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
        for old, new, count in EDITS[rel]:
            found = text.count(old)
            if found != count:
                raise ValueError("%s: %r found %d times, expected %d" % (rel, old[:60], found, count))
            text = text.replace(old, new)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        changed.append(rel)
    return changed
