"""Sets release wording in installed status, launcher and Library content (release specification section 12).

Counts are measured after 905 and before the lead slot. Each file must contain
the exact lead-supplied text once. Missing files, count drift and reruns raise.
"""
import os

EDITS = {'distrib/site/usr/local/d32/libexec/d32-import/vendor/mkindex.py': [(b'First light PREVIEW (D.32.12): the c'
                                                                      b'ontent baked into this image. ',
                                                                      b'The content installed with this syst'
                                                                      b'em. ',
                                                                      1)],
 'lanes/u12-ask/launcher/out/files/d32/a/bin/e26': [(b'"${1#-} is not in Deep32 Library build 2 (PREVIEW). '
                                                     b'It arrives with the Library trains."',
                                                     b'"${1#-} is not in this release of the Deep32 Library'
                                                     b' yet."',
                                                     1)],
 'ship/apps/usr/local/d32/ship/bin/d32status': [(b'print -r -- "Deep32/UX ${rel:-D.32.12}: status"',
                                                 b'print -r -- "Deep32/UX ${rel:-(release unknown)}: status"',
                                                 1)],
 'ship/apps/usr/local/d32/ship/libexec/d32-notyet': [(b'"$name is not in this First light PREVIEW yet. It ar'
                                                      b'rives in a later Deep32/UX release; its place on the'
                                                      b' front panel is kept so nothing moves when it does."',
                                                      b'"$name is not in this release yet. Its place on the '
                                                      b'front panel is kept so nothing moves when it arrives'
                                                      b'."',
                                                      1)],
 'ship/site/etc/d32/site.d/21-ship-media.sh': [(b'not a Deep32 First light medium',
                                                b'not a Deep32 Workstation medium',
                                                1)],
 'ship/tools/mkindex.py': [(b'First light PREVIEW (D.32.12): the content baked into this image. ',
                            b'The content installed with this system. ',
                            1)],
 'ship/tools/mksite-ship.sh': [(b'# /etc/d32setup.conf: First light PREVIEW ($relid), ship lane. d32setup '
                                b'(DIST-INST lane) writes this at run time in a\n# later release; here the '
                                b'image carries it: the medium is found by its DUID; no network is configu'
                                b'red at install.',
                                b'# /etc/d32setup.conf: Deep32 Workstation ($relid). The medium is found b'
                                b'y its DUID; no network is configured at install.',
                                1)],
 'ship/tools/stage.sh': [(b"# Deep32 First light PREVIEW (ship lane): the Library's baked content, read-only",
                          b"# Deep32 Workstation: the Library's installed content, read-only",
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
