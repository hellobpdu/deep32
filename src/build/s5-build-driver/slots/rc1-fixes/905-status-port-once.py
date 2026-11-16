"""Opens the optional status port once and keeps console status when it is busy (release specification section 12).

Counts are measured after 902. Each expected text must be present exactly
as recorded; a missing file, changed count or second application raises.
"""
import os

EDITS = {'distrib/site/usr/local/d32/libexec/d32-c6.sh': [(b'    fi\n    return 0\n}\n\n# c6_current:',
                                                   b'    fi\n    if [ -n "${C6_SERIAL-}" ] && [ "${C6_SERI'
                                                   b'AL_TRIED-0}" != 1 ]; then\n        C6_SERIAL_TRIED=1\n'
                                                   b'        if { command exec 9>>"$C6_SERIAL"; } 2>/dev/null'
                                                   b'; then\n            C6_SERIAL_READY=1\n        else\n  '
                                                   b'          C6_SERIAL_READY=0\n            echo "D32: s'
                                                   b'tatus port busy; status lines go to the console only'
                                                   b'"\n        fi\n    fi\n    return 0\n}\n\n# c6_current:',
                                                   1),
                                                  (b'            _d32target=${C6_SERIAL-}\n            [ -'
                                                   b'n "$_d32target" ] || continue\n',
                                                   b'            [ "${C6_SERIAL_READY-0}" = 1 ] || contin'
                                                   b'ue\n            _d32target=serial\n',
                                                   1),
                                                  (b'        if [ "$_d32target" = stdout ]; then\n        '
                                                   b'    (echo "$_d32line") </dev/null &\n        else\n',
                                                   b'        if [ "$_d32target" = stdout ]; then\n        '
                                                   b'    (echo "$_d32line") </dev/null &\n        elif [ "'
                                                   b'$_d32target" = serial ]; then\n            (echo "$_d'
                                                   b'32line" >&9) </dev/null &\n        else\n',
                                                   1),
                                                  (b'    [ -n "${C6_SERIAL-}" ] || return 0\n    (echo "$_'
                                                   b'd32line" >>"$C6_SERIAL") </dev/null &\n',
                                                   b'    [ "${C6_SERIAL_READY-0}" = 1 ] || return 0\n    ('
                                                   b'echo "$_d32line" >&9) </dev/null &\n',
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
