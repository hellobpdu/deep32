#!/usr/bin/env python3
"""Refuse a converter build until its complete pinned AGPL text is present.

The lead supplies a local licence file and its SHA-256 to the pin resolver.
This verifier checks the staged payload text against that pin before a build.
It never fetches the licence or changes the source kit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse


def check(stage, pins):
    licence = Path(stage) / 'payload/licences/AGPL-3.0.txt'
    if licence.is_symlink() or not licence.is_file():
        raise ValueError('complete AGPL text is missing')
    text = licence.read_bytes()
    if b'TO-PIN' in text or b'PLACEHOLDER' in text:
        raise ValueError('AGPL placeholder: supply pinned source text before building')
    # Structural checks catch a truncated notice even when its hash was copied.
    # Authenticity comes from the lead-reviewed hash and source provenance.
    if (len(text) < 30000 or b'GNU AFFERO GENERAL PUBLIC LICENSE' not in text
            or b'Version 3, 19 November 2007' not in text or b'END OF TERMS AND CONDITIONS' not in text):
        raise ValueError('AGPL text is incomplete')
    measured = hashlib.sha256(text).hexdigest()
    records = json.loads(Path(pins).read_text(encoding='ascii'))['fetch']['files']
    rows = [row for row in records if row['path'] == 'payload/licences/AGPL-3.0.txt']
    if (len(rows) != 1 or not re.fullmatch(r'[0-9a-f]{64}', rows[0]['sha256'])
            or rows[0]['sha256'] != measured):
        raise ValueError('AGPL text differs from the payload licence pin')
    source = urllib.parse.urlsplit(rows[0]['url'])
    if source.scheme != 'file' or source.netloc not in {'', 'localhost'}:
        raise ValueError('AGPL text must have a reviewed local file source')
    return measured


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--pins', type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        print(check(a.stage, a.pins))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('agpl_check: REFUSE: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
