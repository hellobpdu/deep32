#!/usr/bin/env python3
"""Canonical offline SPDX metadata. Exit 0 success; 2 invalid/missing inputs."""
import argparse
from pathlib import Path
import sys

# -I excludes the script directory; only this trusted sibling package is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sbom.common import canonical, identifier, public_text
from sbom.workflow import contents, finalise, generator_sha256, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('generator-hash')
    for verb in ('contents', 'finalise', 'verify'):
        command = commands.add_parser(verb)
        command.add_argument('--manifest', required=True)
        command.add_argument('--root', action='append', default=[], metavar='ID=PATH')
        if verb == 'verify':
            command.add_argument('--sboms', required=True)
        else:
            command.add_argument('--out', required=True)
        if verb == 'finalise':
            command.add_argument('--contents', required=True)
        if verb in ('finalise', 'verify'):
            # additive frozen public contract input.
            command.add_argument('--contract', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'generator-hash':
            print(generator_sha256())
            return 0
        roots = {}
        for row in args.root:
            name, separator, path = row.partition('=')
            identifier(name)
            if not separator or not path or name in roots:
                raise ValueError('unique --root ID=PATH mappings required')
            roots[name] = Path(path)
        if args.command == 'contents':
            result = contents(args.manifest, roots, args.out)
        elif args.command == 'finalise':
            result = finalise(args.manifest, roots, args.contents, args.out, contract_path=args.contract)
        else:
            result = verify(args.manifest, roots, args.sboms, contract_path=args.contract)
        print(canonical(result), end='')
        return 0
    except (ValueError, OSError, OverflowError) as exc:
        # technical refusal text for review. Never echo OS paths.
        detail = 'missing or unreadable input' if isinstance(exc, OSError) else str(exc)
        try:
            detail = public_text(detail)
        except ValueError:
            detail = 'invalid input metadata'
        print('SBOM verification failed: ' + detail + '.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
