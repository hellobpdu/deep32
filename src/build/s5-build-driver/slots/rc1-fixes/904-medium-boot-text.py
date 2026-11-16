"""Sets the guided medium boot line to the release wording (release specification section 12).

Counts are measured after 902. Each expected text must be present exactly
as recorded; a missing file, changed count or second application raises.
"""
import os

EDITS = {'ship/install/boot.conf.guided': [(b'echo Deep32/UX D.32.12 First light PREVIEW (test-signed, d32test key'
                                    b's). Guided install: it asks the disk, passphrases and passwords.\n',
                                    b'echo Deep32 Workstation 1.0. Guided install: it asks for the disk, t'
                                    b'he passphrases and the passwords.\n',
                                    1)]}


def apply(tree_dir):
    out = []
    for rel in sorted(EDITS):
        p = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(p):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(p, "rb") as f:
            data = f.read()
        for old, new, want in EDITS[rel]:
            got = data.count(old)
            if got != want:
                raise ValueError("%s: expected %d of %r, found %d" % (rel, want, old[:80], got))
            data = data.replace(old, new)
        with open(p, "wb") as f:
            f.write(data)
        out.append(rel)
    return out
