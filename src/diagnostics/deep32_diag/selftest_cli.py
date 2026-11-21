"""Fixed selftest CLI; source execution has no installed native loader."""
import argparse
from pathlib import Path
import sys

from . import selftest


class _Arguments(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Arguments()


def parse_arguments(argv, *, installed_role=None):
    if (type(argv) not in (list, tuple) or len(argv) > 16
            or any(type(a) is not str or len(a) > 4096 or
                   any(ord(c) < 32 or ord(c) == 127 for c in a) for a in argv)):
        raise _Arguments()
    parser = _Parser(prog='deep32 selftest', add_help=False, allow_abbrev=False)
    parser.add_argument('command', choices=('selftest',))
    parser.add_argument('--agent', choices=('codex', 'claude'))
    parser.add_argument('--profile-dir')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--details', action='store_true')
    seen = set()
    for arg in argv:
        if arg.startswith('--'):
            name = arg.split('=', 1)[0]
            if name in seen:
                raise _Arguments()
            seen.add(name)
    args = parser.parse_args(argv)
    if args.agent is not None or args.profile_dir is not None:
        if (args.agent is None or args.profile_dir is None or not Path(args.profile_dir).is_absolute()
                or '..' in Path(args.profile_dir).parts or installed_role not in (None, 'headend')):
            raise _Arguments()
    elif installed_role == 'headend':
        raise _Arguments()
    return args


def main(argv=None, *, stdout=None, evidence=None, budget=None, hostname='local-host', checked_at=None):
    argv = sys.argv[1:] if argv is None else argv
    output = sys.stdout if stdout is None else stdout
    json_requested = type(argv) in (list, tuple) and any(type(a) is str and
        (a == '--json' or a.startswith('--json=')) for a in argv)
    role = evidence.role if type(evidence) is selftest.SelftestEvidence else None
    try:
        args = parse_arguments(argv, installed_role=role)
    except (Exception, KeyboardInterrupt):
        result = selftest.incomplete('arguments', hostname=hostname, checked_at=checked_at)
    else:
        with selftest.interruptions():
            result = selftest.run_selftest(evidence=evidence, agent=args.agent,
                profile_dir=args.profile_dir, details=args.details, budget=budget,
                hostname=hostname, checked_at=checked_at)
    output.write(selftest.render_json(result) if json_requested else selftest.render_text(result))
    return result['exit_code']
