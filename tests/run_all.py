#!/usr/bin/env python3
"""Run every Deep32 test suite, each the way it expects to be run.

    python3 -I -B tests/run_all.py              run everything this host can run
    python3 -I -B tests/run_all.py --list       show the plan and the skip reasons, run nothing
    python3 -I -B tests/run_all.py --only relay --only inventory

Each suite runs from the root of the repository in an empty scratch folder of its own, given to it as an argument
or an environment variable, so nothing is written into the clone. A suite that needs a tool this host lacks is
skipped with a one-line reason. Any test file under the test folders that no suite below covers runs as a plain
`python -B -m unittest FILE`, so a new test file is never silently left out. A single test known to fail on some
Python versions is left out on those versions only, and the suite's line names it and says why.

Exit status: 0 when every suite that ran passed, 1 when any failed or timed out, 2 for bad arguments.
Standard library only; Python 3.12 or newer.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

if sys.version_info < (3, 12):
    sys.exit('run_all.py needs Python 3.12 or newer')

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == 'nt'


def elevated():
    """True on Windows when this process runs elevated (an administrator token), as on GitHub's runner."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


ELEVATED = elevated()

# Folders whose test files belong to some suite. The pairing reference package is a byte-exact copy with its own
# runner; the image build driver's tests under src/build/s5-build-driver need a build host and are not listed here.
TEST_ROOTS = ('tests', 'adapters/tests', 'src/gpu-pack/tests', 'src/kb-tools/linux-line/tests', 'src/build/alpha2-tree')
NOT_SUITES = ('tests/pairing/reference/',)


def tools(missing):
    """Paths of the outside tools some suites drive. On Windows bash is Git for Windows' by full path, never a bare
    `bash`, which can start WSL."""
    found = {}
    if WINDOWS:
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        candidates = {
            'bash': Path(r'C:\Program Files\Git\bin\bash.exe'),
            'powershell': system / 'System32/WindowsPowerShell/v1.0/powershell.exe',
            'gpg': Path(r'C:\Program Files\GnuPG\bin\gpg.exe'),
            'gpgconf': Path(r'C:\Program Files\GnuPG\bin\gpgconf.exe'),
        }
        for name, path in candidates.items():
            if path.is_file():
                found[name] = str(path)
    else:
        for name in ('bash', 'gpg', 'gpgconf'):
            path = shutil.which(name)
            if path:
                found[name] = path
    openssl = shutil.which('openssl')
    if openssl:
        found['openssl'] = openssl
    if 'gpg' in found and 'gpgconf' not in found:
        del found['gpg']
    for name in missing:
        found.pop(name, None)
        if name == 'gpg':
            found.pop('gpgconf', None)
    return found


NEED_TEXT = {
    'windows': 'needs Windows',
    'bash': 'needs bash' + (' (Git for Windows at C:\\Program Files\\Git\\bin\\bash.exe)' if WINDOWS else ''),
    'powershell': 'needs Windows PowerShell 5.1',
    'gpg': 'needs GnuPG' + (' (Gpg4win at C:\\Program Files\\GnuPG\\bin)' if WINDOWS else ''),
    'openssl': 'needs the openssl command on PATH',
    'unelevated': ('needs an unelevated Windows user: Deep32 refuses private files owned by the Administrators '
                   'group and refuses to download elevated, and this host runs the tests elevated'),
}


class Suite:
    def __init__(self, name, argv, covers, scratch=None, env=(), needs=(), mkdir=True, known=()):
        self.name = name
        self.known = known          # known failures: (Python version below which they apply, target, reason), where
        #                             the target is a Class.test id in a script suite or a test file in a discover suite
        self.argv = argv            # '{py}', '{scratch}', '{bash}', '{powershell}', '{gpg}', '{gpgconf}' are filled in
        self.covers = covers        # repository-relative test files this suite runs
        self.scratch = scratch      # flag that takes the scratch folder, if any
        self.env = dict(env)        # variables set for the suite; '{scratch}' is filled in
        self.needs = set(needs)
        self.mkdir = mkdir

    def missing(self, found):
        lacking = [need for need in sorted(self.needs) if (need == 'windows' and not WINDOWS) or
                   (need == 'unelevated' and ELEVATED) or
                   (need not in ('windows', 'unelevated') and need not in found)]
        return '; '.join(NEED_TEXT[need] for need in lacking)

    def skipped_here(self):
        """The known failures that apply to this Python, as (test id, reason)."""
        return [(test, reason) for below, test, reason in self.known if sys.version_info < below]

    def command(self, found, scratch):
        """The argument list, the extra environment and the working folder for this suite."""
        values = dict(found, py=sys.executable, scratch=str(scratch))
        argv = [part.format(**values) for part in self.argv]
        workdir = ROOT
        leave_out = {target for target, _ in self.skipped_here()}
        if leave_out and 'discover' in argv:
            # Run the folder's other test modules from inside it, the way discover would import them.
            folder = argv[argv.index('-s') + 1]
            workdir = ROOT / folder
            argv = argv[:argv.index('discover')] + [Path(path).stem for path in self.covers if path not in leave_out]
        if self.scratch:
            argv += [self.scratch, str(scratch)]
        if leave_out and 'discover' not in self.argv:
            # Name every other test of the file, so unittest runs all of them except the known failures.
            argv += [test for test in test_ids(ROOT / self.covers[0]) if test not in leave_out]
        env = {key: value.format(**values) for key, value in self.env.items()}
        return argv, env, workdir


def test_ids(path):
    """Class.method ids of the unittest tests defined in a test file, in file order."""
    ids, current = [], None
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.match(r'class (\w+)\(.*TestCase\):', line)
        if match:
            current = match.group(1)
        elif line and not line[0].isspace():
            current = None
        match = re.match(r'    def (test\w*)\(', line)
        if match and current:
            ids.append(f'{current}.{match.group(1)}')
    return ids


def files(folder, names, prefix='test_'):
    return [f'{folder}/{prefix}{name}.py' for name in names.split()]


def plain(name, paths, **options):
    return Suite(name, ['{py}', '-B', '-m', 'unittest', *paths], paths, **options)


def script(name, path, flag, extra=(), **options):
    return Suite(name, ['{py}', '-B', path, *extra], [path], scratch=flag, **options)


RT = 'tests/release-tools'
GP = 'src/gpu-pack/tests'
SUITES = [
    # Release tools: deep32-get, verification, assembly, signing and the SBOM tools
    plain('release-tools', files(RT, 'alpha2_assembly bundle_assembly c2_prepare_signing_inputs forge_preflight_windows '
                                     'forge_requirements owner_inputs owner_media sbom_archives sbom_distributions '
                                     'sbom_frozen sbom_inputs sbom_public_path sbom_spdx sbom_versions sbom_workflow '
                                     'storage_declared whole_assembly'), needs=['unelevated']),
    *[script(f'release-tools/{name}', f'{RT}/test_{name}.py', '--scratch-root',
             needs=['unelevated'] if name in ('forge_pair_windows', 'getter_kit', 'settings', 'whole_get') else [])
      for name in 'capabilities forge_pair_windows forge_ports forge_preflight getter_kit gpg_discovery '
                  'settings whole_get'.split()],
    script('release-tools/phase_a_helpers', f'{RT}/test_phase_a_helpers.py', '--scratch-root'),
    script('release-tools/disc3_join', f'{RT}/test_disc3_join.py', '--scratch-root', needs=['bash']),
    script('release-tools/docker_install', f'{RT}/test_docker_install.py', '--scratch-root',
           needs=['windows', 'bash', 'powershell', 'gpg']),
    script('release-tools/get_powershell', f'{RT}/test_get_powershell.py', '--scratch-root',
           extra=['--powershell', '{powershell}'], needs=['windows', 'powershell', 'unelevated']),
    script('release-tools/get_whole_bash', f'{RT}/test_get_whole_bash.py', '--scratch-root',
           extra=['--bash', '{bash}'], needs=['bash']),
    script('release-tools/settings_bash', f'{RT}/test_settings_bash.py', '--scratch-root',
           needs=['windows', 'bash', 'powershell', 'gpg']),
    script('release-tools/signature_class', f'{RT}/test_signature_class.py', '--scratch-root',
           needs=['windows', 'bash', 'powershell']),
    script('release-tools/whole_trust', f'{RT}/test_whole_trust.py', '--scratch-root',
           env={'D32_GIT_BASH': '{bash}'}, needs=['bash']),
    script('release-tools/contracts', f'{RT}/test_contracts.py', '--scratch'),
    *[script(f'release-tools/{name}', f'{RT}/test_{name}.py', '--scratch-root',
             extra=['--tools-dir', 'src/release-tools', '--bash', '{bash}', '--powershell', '{powershell}',
                    '--gpg', '{gpg}', '--gpgconf', '{gpgconf}', '--keygen-gpg', '{gpg}'],
             needs=['windows', 'bash', 'powershell', 'gpg'], mkdir=False)
      for name in ('release_tools', 'phase_a_matrix')],
    Suite('release-tools/settings_handoff', ['{powershell}', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                             f'{RT}/test_settings_handoff.ps1', '-Tools', 'src/release-tools'],
          [f'{RT}/test_settings_handoff.ps1'], scratch='-Scratch', needs=['windows', 'powershell']),
    script('whole-download', 'tests/test-whole-download.py', '--tmp', mkdir=False, needs=['unelevated']),

    # Machine inventory: run_tests.py drives both the shell and the PowerShell form and skips a form it can't run
    Suite('inventory', ['{py}', '-B', 'tests/inventory/run_tests.py'], ['tests/inventory/run_tests.py'],
          scratch='--work'),
    *[script(f'inventory/{name}', f'tests/inventory/{name}.py', '--work')
      for name in ('run_prereqs', 'test_contract_fixes', 'test_storage_numbers')],
    script('inventory/linux_forge_prereqs', 'tests/inventory/test_linux_forge_prereqs.py', '--scratch-root'),

    # The engine's and gateway's own suites need their pinned packages (tests/*/requirements-dev.txt); a Python
    # without them prints one line and passes
    Suite('engine', ['{py}', '-B', 'tests/engine/run_tests.py', '--skip-missing'],
          [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / 'tests/engine').glob('test_*.py'))]),
    Suite('gateway', ['{py}', '-B', 'tests/gateway/run_tests.py', '--skip-missing'],
          [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / 'tests/gateway').glob('test_*.py'))]),

    # Agent adapters, the headend setup and the Ask relay
    plain('adapters/connect', ['adapters/tests/test_connect.py'], env={'DEEP32_CONNECT_TEST_ROOT': '{scratch}'}),
    Suite('relay', ['{py}', '-B', '-m', 'unittest', 'discover', '-s', 'tests/relay'],
          [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / 'tests/relay').glob('test_*.py'))],
          env={'DEEP32_RELAY_TEST_ROOT': '{scratch}'}),
    plain('headend', ['tests/headend/test_setup.py', 'tests/headend/test_skills.py',
                      'tests/headend/test_install_skills.py']),
    Suite('headend/twins', ['{py}', '-B', 'tests/headend/sync_twins.py', '--check'], [], mkdir=False),

    # Pairing, publication and the Forge's Docker role
    *[script(f'pairing/{name}', f'tests/pairing/test_{name}.py', '--tmp')
      for name in ('callers', 'integration', 'pairdesk_port')],
    *[script(f'pairing/{name}', f'tests/pairing/test_{name}.py', '--scratch') for name in ('enrol', 'forge', 'forwarder')],
    Suite('pairing/reference', ['{py}', '-B', 'tests/pairing/reference/tests/run_all.py'], [], scratch='--tmp',
          needs=['openssl']),
    script('publication', 'tests/publication/test_publication.py', '--tmp'),
    plain('docker_role/full_runtime', ['tests/docker_role/test_full_runtime.py'],
          env={'D32_DOCKER_TEST_ROOT': '{scratch}'}, needs=['unelevated']),
    plain('docker_role/host_paths', ['tests/docker_role/test_host_paths.py']),
    script('docker_role/lifecycle', 'tests/docker_role/test_lifecycle.py', '--scratch', needs=['unelevated']),
    script('docker_role/model_bridge', 'tests/docker_role/test_model_bridge.py', '--scratch'),

    # Contracts, Workstation programs, diagnostics and documentation checks
    script('contracts/c6_limits', 'tests/contracts/test_c6_limits.py', '--scratch', extra=['--shell', '{bash}'],
           needs=['bash']),
    plain('hardening', ['tests/hardening/test_prerun_docs.py']),
    # The fuzz targets' seeds and CI matrix; the fuzzing itself needs clang on Linux (tests/fuzz/README.md)
    plain('fuzz/seeds', ['tests/fuzz/test_seeds.py']),
    plain('workstation', files('tests/workstation', 'ask_cite_maps ask_protocol content_fetch d32upd_lock '
                                                    'diagnostics_placement firstboot_disc2_probe importer_content_compat '
                                                    'importer_mount_failure importer_single_pass installer_alpha2'),
          env={'D32_TEST_WORK': '{scratch}'}),
    Suite('diagnostics', ['{py}', '-B', '-m', 'unittest', 'discover', '-s', 'tests/diagnostics'],
          [str(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / 'tests/diagnostics').glob('test_*.py'))],
          env={'DEEP32_DIAG_TEST_ROOT': '{scratch}', 'D32_SUITE_SCRATCH': '{scratch}'}),

    # GPU pack, knowledge-base tools and the release tree builder
    plain('gpu-pack', files(GP, 'alpha2_disc3 alpha2_metadata alpha2_pipeline alpha2_qualify alpha2_rebuild '
                                'builder_eval_paths converter_debs image_binding lean_image_recipes model_cache '
                                'runtime_stage')),
    plain('gpu-pack/host_preflight_hardening', [f'{GP}/test_host_preflight_hardening.py'],
          env={'D32_RUNTIME_TEST_SCRATCH': '{scratch}'}),
    *[script(f'gpu-pack/{name}', f'{GP}/test_{name}.py', '--scratch',
             needs=['unelevated'] if name in ('forge_capabilities', 'forge_docker_role', 'forge_jobs',
                                              'runtime_preflight') else [])
      for name in 'forge_capabilities forge_docker_role forge_jobs forge_publication forge_retained_runtime '
                  'installed_runtime runtime_preflight storage_requirements'.split()],
    plain('kb-tools', files('src/kb-tools/linux-line/tests', 'disc2_assemble w2_identity')),
    plain('build/alpha2-tree', files('src/build/alpha2-tree', 'alpha2_tree check_reuse release_key tree_references')),
]


def discovered():
    """Every test file under the test folders, as repository-relative paths."""
    found = set()
    for folder in TEST_ROOTS:
        base = ROOT / folder
        if not base.is_dir():
            continue
        for pattern in ('test_*.py', 'test-*.py', 'test_*.ps1'):
            for path in base.rglob(pattern):
                relative = path.relative_to(ROOT).as_posix()
                if not relative.startswith(NOT_SUITES):
                    found.add(relative)
    return found


def all_suites():
    covered = {path for suite in SUITES for path in suite.covers}
    extra = []
    for path in sorted(discovered() - covered):
        if path.endswith('.py'):
            extra.append(plain('unlisted:' + path, [path]))
        else:
            message = f'{path} is a PowerShell test with no entry in tests/run_all.py; add one'
            extra.append(Suite('unlisted:' + path, ['{py}', '-c', f'import sys; sys.exit({message!r})'], [path],
                               mkdir=False))
    return SUITES + extra, [s.name for s in extra]


RAN = re.compile(r'^Ran (\d+) tests? in ', re.M)
PASS = re.compile(r'^PASS: (\d+) assertions?', re.M)
CHECKS = re.compile(r'^(\d+) passed, (\d+) failed\s*$', re.M)
GUARDED = re.compile(r'^RAN (\d+) FAILED \d+ ERRORS \d+ SKIPPED (\d+) ', re.M)
TOTAL = re.compile(r'^(?:OK|FAILED)(?: \((.*)\))?\s*$', re.M)


def counts(output):
    tests = sum(int(n) for n in RAN.findall(output)) + sum(int(n) for n, _ in GUARDED.findall(output))
    asserts = sum(int(n) for n in PASS.findall(output))
    checks = CHECKS.findall(output)
    skipped = (sum(int(n) for detail in TOTAL.findall(output) for n in re.findall(r'skipped=(\d+)', detail)) +
               sum(int(n) for _, n in GUARDED.findall(output)))
    parts = []
    if tests:
        parts.append(f'{tests} tests')
    if skipped:
        parts.append(f'{skipped} skipped')
    if asserts:
        parts.append(f'{asserts} assertions')
    for good, bad in checks:
        parts.append(f'{good} checks passed, {bad} failed')
    return ', '.join(parts) or 'no count printed'


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--list', action='store_true', help='show the plan and the skip reasons, run nothing')
    parser.add_argument('--only', action='append', default=[], metavar='TEXT',
                        help='run only suites whose name contains TEXT (repeatable)')
    parser.add_argument('--exclude', action='append', default=[], metavar='TEXT',
                        help='leave out suites whose name contains TEXT (repeatable)')
    parser.add_argument('--without', action='append', default=[], choices=['bash', 'powershell', 'gpg', 'openssl'],
                        help='treat a tool as absent, as on a host without it (repeatable)')
    parser.add_argument('--scratch', type=Path, help='parent folder for the suites\' scratch folders (empty or new)')
    parser.add_argument('--keep', action='store_true', help='keep the scratch folders after a passing run')
    parser.add_argument('--timeout', type=int, default=1800, help='seconds allowed per suite (default 1800)')
    args = parser.parse_args()

    found = tools(args.without)
    suites, unlisted = all_suites()
    chosen = [s for s in suites if (not args.only or any(t in s.name for t in args.only))
              and not any(t in s.name for t in args.exclude)]
    if not chosen:
        print('no suite matches')
        return 2

    print(f'Python {sys.version.split()[0]} at {sys.executable}')
    print('tools: ' + (', '.join(f'{name}={path}' for name, path in sorted(found.items())) or 'none found'))
    if unlisted:
        print('test files no suite lists, run with the default command: ' + ', '.join(unlisted))
    if args.list:
        for suite in chosen:
            reason = suite.missing(found)
            print(f'{"SKIP" if reason else "RUN ":4}  {suite.name}' + (f'  ({reason})' if reason else ''))
            if not reason:
                for target, why in suite.skipped_here():
                    print(f'      leaves out known failure {target}: {why}')
        return 0

    if args.scratch:
        base = args.scratch.resolve()
        base.mkdir(parents=True, exist_ok=True)
        if any(base.iterdir()):
            print(f'{base} is not empty')
            return 2
    else:
        base = Path(tempfile.mkdtemp(prefix='deep32-tests-'))
    logs = base / 'logs'
    logs.mkdir()
    env_base = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONHOME')}

    results = []
    started = time.monotonic()
    for index, suite in enumerate(chosen, 1):
        label = f'[{index}/{len(chosen)}] {suite.name}'
        reason = suite.missing(found)
        if reason:
            results.append((suite.name, 'SKIP', reason))
            print(f'SKIP  {label}: {reason}', flush=True)
            continue
        scratch = base / f'{index:02d}-{re.sub(r"[^A-Za-z0-9_.-]+", "-", suite.name)}'
        if suite.mkdir or suite.env:
            scratch.mkdir()
        argv, env, workdir = suite.command(found, scratch)
        log = logs / (scratch.name + '.log')
        t0 = time.monotonic()
        try:
            with open(log, 'w', encoding='utf-8', errors='replace') as out:
                code = subprocess.run(argv, cwd=workdir, env=dict(env_base, **env), stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            code = 'timeout'
        seconds = time.monotonic() - t0
        output = log.read_text(encoding='utf-8', errors='replace')
        status = 'PASS' if code == 0 else 'FAIL'
        detail = counts(output) + f', {seconds:.0f} s' + ('' if code == 0 else f', exit {code}')
        for test, reason in suite.skipped_here():
            detail += f'; known failure left out: {test} ({reason})'

        results.append((suite.name, status, detail))
        print(f'{status}  {label}: {detail}', flush=True)
        if code != 0:
            tail = [line for line in output.splitlines() if line.strip()][-25:]
            print('      ' + '\n      '.join(tail) + f'\n      full output: {log}', flush=True)

    passed = sum(1 for r in results if r[1] == 'PASS')
    failed = [r for r in results if r[1] == 'FAIL']
    skipped = [r for r in results if r[1] == 'SKIP']
    print(f'\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped, '
          f'{(time.monotonic() - started) / 60:.1f} min')
    for name, _, detail in failed:
        print(f'FAILED  {name}: {detail}')
    for name, _, reason in skipped:
        print(f'SKIPPED {name}: {reason}')
    if failed or args.keep:
        print(f'scratch and logs: {base}')
    else:
        shutil.rmtree(base, ignore_errors=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
