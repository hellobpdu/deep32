"""Fixed doctor command; no arbitrary URL, token, environment or repair input."""
import argparse
from pathlib import Path
import sys

from . import doctor, report
from .models import State
from .report import Reason, row


class ArgumentsError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentsError()


def _path(value):
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or not Path(value).is_absolute()):
        raise ArgumentsError()
    return value


def parse_arguments(argv, *, installed_role=None):
    if (type(argv) not in (tuple, list) or len(argv) > 32
            or any(type(arg) is not str or len(arg) > 4096 for arg in argv)):
        raise ArgumentsError()
    parser = _Parser(prog='deep32', add_help=False, allow_abbrev=False)
    parser.add_argument('command', choices=('doctor',))
    parser.add_argument('--role', choices=('workstation', 'forge', 'headend'))
    parser.add_argument('--data-root')
    parser.add_argument('--agent', choices=('codex', 'claude'))
    parser.add_argument('--profile-dir')
    parser.add_argument('--json', action='store_true')
    seen = set()
    for arg in argv:
        if arg.startswith('--'):
            key = arg.split('=', 1)[0]
            if key in seen:
                raise ArgumentsError()
            seen.add(key)
    args = parser.parse_args(argv)
    args.role = args.role or installed_role
    if args.role not in ('workstation', 'forge', 'headend'):
        raise ArgumentsError()
    if args.role == 'headend':
        if args.agent is None or args.profile_dir is None or args.data_root is not None:
            raise ArgumentsError()
        _path(args.profile_dir)
    elif args.agent is not None or args.profile_dir is not None:
        raise ArgumentsError()
    if args.data_root is not None:
        if args.role != 'forge':
            raise ArgumentsError()
        _path(args.data_root)
    return args


def main(argv=None, *, stdout=None, evidence=None, platform=None, hostname=None,
         checked_at=None, budget=None):
    argv = sys.argv[1:] if argv is None else argv
    if type(argv) in (tuple, list) and argv and argv[0] == 'selftest':
        from .selftest_cli import main as selftest_main
        return selftest_main(argv, stdout=stdout, evidence=evidence, budget=budget,
                             hostname=hostname or 'local-host', checked_at=checked_at)
    output = sys.stdout if stdout is None else stdout
    json_requested = (type(argv) in (tuple, list) and any(type(arg) is str
        and (arg == '--json' or arg.startswith('--json=')) for arg in argv))
    try:
        receipt = evidence.receipt if type(evidence) is doctor.DoctorEvidence else None
        args = parse_arguments(argv, installed_role=receipt.role if receipt is not None else None)
    except ArgumentsError:
        result = report.build_report(None, None, 'local-host', '1970-01-01T00:00:00Z',
                                     [row('arguments', State.FAIL, Reason.UNAVAILABLE)])
    except (Exception, KeyboardInterrupt):
        result = report.build_report(None, None, 'local-host', '1970-01-01T00:00:00Z',
            [row('installation', State.FAIL, Reason.UNAVAILABLE),
             row('doctor', State.FAIL, Reason.UNAVAILABLE)], complete=False)
    else:
        result = doctor.run_doctor(args.role, evidence=evidence, platform=platform,
                                  hostname=hostname, checked_at=checked_at, budget=budget,
                                  data_root=args.data_root, agent=args.agent, profile_dir=args.profile_dir)
    output.write(report.render_json(result) if json_requested else report.render_text(result))
    return result['exit_code']
