"""Restores the laptop's HP-UX CDE, AIX CDE and HP VUE look names and ids (release specification section 15, items 31 and 33; findings D9/F6).

What it changes
  Reverses exactly the six HP/AIX/VUE name, id and note replacements in
  transform 602's appearance.c table. The Encarta 95-99 replacements and
  every Microsoft wording and credit removal stay, as does transform 603.
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101-806 and 990.
  Returns the changed files.
Exit
  Raises when a target or expected text is missing or its count differs.
  A second run on its output raises, as does an empty folder.
"""
import os

EDITS = {
    'library/src/app/appearance.c': [
        ('{ "copper", "Copper CDE",',
         '{ "hpux", "HP-UX CDE",', 1),
        ('{ "bluegray", "Blue-gray CDE",',
         '{ "aix", "AIX CDE",', 1),
        ('{ "rose", "Rose workstation",',
         '{ "hpvue", "HP VUE",', 1),
        ('/* bluegray  4.85 */',
         '/* aix       4.85 */', 1),
        ('/* rose      4.85 */',
         '/* hpvue     4.85 */', 1),
        ("and Copper's #EDA870, 2.0:1)",
         "and HP-UX's #EDA870, 2.0:1)", 1),
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
