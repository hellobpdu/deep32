"""Names OpenBSD 8.0's own Python package in disc 1's package roots (release specification section 3, disc 1 builds and tests).

LEAD transform for the lead's slot 990-999 (disc 1 v0 lab build, 2026-11-15: stage 80 stopped in mkimage-ship.sh's
package closure, "root ... is not installed on this VM", once the other roots were installed on the build VM).

What it changes
  ship/install/pkg-roots: "python-3.14.9" -> "python-3.14.7p0". Disc 1 takes its packages from OpenBSD 8.0's signed
  list (/build/pkgrepo, provision-910 workstation-pkgs; push-tree's resolve-pkgs printed "python-3.14.9 ->
  python-3.14.7p0" among its 13 resolutions). The u15 laptop's 3.14.9 is not in that list. Python is the only root
  pinned to a version (the other 24 are bare names, which pkgclosure.sh and mkimage-ship.sh match to whatever version
  the list has), so it is the only root the resolution left stale. The same interpreter, /usr/local/bin/python3.14.
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101 to 995. Returns the changed files.
Exit
  Raises when the target file or text is missing or appears a different number of times: never a silent skip.
"""
import os

EDITS = {
    "ship/install/pkg-roots": [
        ("\npython-3.14.9\n", "\npython-3.14.7p0\n", 1),
    ],
}


def apply(tree_dir):
    changed = []
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("996: missing " + rel)
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        for old, new, count in EDITS[rel]:
            n = text.count(old)
            if n != count:
                raise ValueError("996: %s: expected %d occurrence(s) of %r, found %d" % (rel, count, old[:60], n))
            text = text.replace(old, new)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        changed.append(rel)
    return changed
