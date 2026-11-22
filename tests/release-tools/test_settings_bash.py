#!/usr/bin/env python3
"""Actual Bash get entrypoint with signed synthetic media and inert host gateways.

Every install write is redirected to the supplied task scratch tree. The test
never invokes mount, Docker, an inventory probe, WSL or a real loader.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scratch-root', required=True, type=Path)
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    regression = module('release_regression', Path(__file__).with_name('test_release_tools.py'))
    settings = module('settings', repo / 'src/release-tools/deep32_settings.py')
    options = argparse.Namespace(tools_dir=repo / 'src/release-tools', scratch_root=args.scratch_root,
        baseline_only=False,
        bash=Path('C:/Program Files/Git/bin/bash.exe'), powershell=Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'),
        gpg=Path('C:/Program Files/GnuPG/bin/gpg.exe'), gpgconf=Path('C:/Program Files/GnuPG/bin/gpgconf.exe'),
        keygen_gpg=Path('C:/Program Files/GnuPG/bin/gpg.exe'))
    suite = regression.Suite(options)
    try:
        suite.setup()
        release = suite.assemble('synthetic-release')
        suite.sign(release, 'synthetic-release')
        meta = json.loads((release / 'ASSEMBLY.json').read_bytes())
        disc = next(row for row in meta['discs'] if row['role'] == 'disc3')
        choice = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge', **settings.DEFAULTS}
        choice_path = suite.root / 'synthetic-choices.json'
        choice_path.write_bytes(settings.canonical(choice))
        out = suite.root / 'deep32-downloads'
        data_root = suite.root / 'synthetic-data'
        plan = settings.make_plan(choice, disc['sha256'], out, 'J2', 'docker', 'linux', 'forge', settings.validator().profiles, data_root)
        events = suite.root / 'synthetic-events.txt'
        target = suite.root / 'synthetic-install/settings.json'
        dispatcher = suite.root / 'synthetic-python-dispatch.py'
        regression.text(dispatcher, r'''import importlib.util,json,os,pathlib,subprocess,sys
original=sys.argv[1:]
args=[value for value in original if value not in ('-I','-B')]
if len(args)>1 and pathlib.Path(args[0]).name=='deep32_settings.py' and args[1]=='stage':
    spec=importlib.util.spec_from_file_location('synthetic_settings',args[0]); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    plan=m.read_json(args[args.index('--plan')+1]); accepted=args[args.index('--accept-plan')+1]
    directory=pathlib.Path(args[args.index('--directory')+1]);m.protect(directory,directory=True);m.stage(plan,directory)
    with open(os.environ['SYNTHETIC_SETTINGS_EVENTS'],'a',encoding='ascii') as stream: stream.write('settings\n')
    raise SystemExit(0)
if len(args)>1 and pathlib.Path(args[0]).name=='deep32_settings.py' and args[1]=='run-loader':
    if not any(value.endswith('load-docker.sh') for value in args):
        raise SystemExit('Synthetic loader gateway refused')
    spec=importlib.util.spec_from_file_location('synthetic_settings',args[0]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    staged=pathlib.Path(args[args.index('--settings')+1]);plan=m.read_json(staged.parent/'plan.json')
    assert args[args.index('--docker-role')+1]=='forge' and '--content' not in args
    if os.environ.get('SYNTHETIC_LOADER_FAIL'):
        raise SystemExit(1)
    m.install(plan,plan['digest'],pathlib.Path(os.environ['SYNTHETIC_SETTINGS_TARGET']),staging=True)
    with open(os.environ['SYNTHETIC_SETTINGS_EVENTS'],'a',encoding='ascii') as stream: stream.write('loader\n')
    raise SystemExit(0)
raise SystemExit(subprocess.call([sys.executable,*original],env=os.environ.copy()))
''')
        regression.text(suite.shim / 'python3', '#!/usr/bin/env bash\nexec "$RELEASE_TEST_PYTHON" -I -B "$SYNTHETIC_PYTHON_DISPATCH" "$@"\n')
        guard = suite.root / 'synthetic-install-guard.sh'
        regression.text(guard, r'''#!/usr/bin/env bash
set -euo pipefail
record() { printf '%s\n' "$1" >> "$SYNTHETIC_SETTINGS_EVENTS"; }
id() { printf '%s\n' 0; }
bash() {
  if [[ "$*" == *install/deep32-inventory.sh* ]]; then record prerequisites; [[ "${SYNTHETIC_PREREQUISITES:-pass}" == pass ]]; return; fi
  if [[ "$*" == *forms/load-docker.sh* ]]; then
    [[ -f "$SYNTHETIC_SETTINGS_TARGET" ]] || return 97
    record loader; return 0
  fi
  printf '%s\n' 'Unexpected shell gateway refused' >&2; return 97
}
# The wrapper already created this mount directory. Create only its children;
# recursive mkdir unnecessarily revisits protected Windows parent directories.
mount() { local destination=${@: -1}; record mount; mkdir -- "$destination/forms" "$destination/deep32"; mkdir -- "$destination/deep32/payload"; : >"$destination/forms/load-docker.sh"; }
umount() { local destination=${@: -1}; record umount; rm -f -- "$destination/forms/load-docker.sh"; rmdir -- "$destination/forms" "$destination/deep32/payload" "$destination/deep32"; }
export PATH="$RELEASE_TEST_SHIM:$PATH"
source "$1" "${@:2}"
''')
        suite.env.update(SYNTHETIC_PYTHON_DISPATCH=str(dispatcher), SYNTHETIC_SETTINGS_TARGET=str(target), SYNTHETIC_SETTINGS_EVENTS=str(events))
        # Only shell builtins consume these two paths in the guard.
        suite.env['SYNTHETIC_SETTINGS_TARGET'] = regression.msys(target)
        suite.env['SYNTHETIC_SETTINGS_EVENTS'] = regression.msys(events)
        # Native Python needs native spellings after crossing MSYS. The dispatch
        # canonicalises its explicit synthetic paths, without inspecting a home.
        dispatch_text = dispatcher.read_text(encoding='ascii').replace("original=sys.argv[1:]", "original=sys.argv[1:]\nfor key in ('SYNTHETIC_SETTINGS_TARGET','SYNTHETIC_SETTINGS_EVENTS'):\n    value=os.environ[key]\n    if len(value)>3 and value[0]=='/' and value[2]=='/': os.environ[key]=value[1].upper()+':'+value[2:]")
        regression.text(dispatcher, dispatch_text)
        base = ['disc3', '--install', '--journey', 'J2', '--docker-role', 'forge', '--data-root', data_root, '--non-interactive',
                '--from', release, '--release', 'v0.1.0-alpha.1', '--key', suite.key, '--fingerprint', suite.fingerprint]
        def run(name, extra, expected):
            converted = [regression.msys(value) if isinstance(value, Path) else str(value) for value in base + extra]
            return suite.run(name, [options.bash, '--noprofile', '--norc', regression.msys(guard),
                regression.msys(suite.tools / 'deep32-get.sh'), *converted], expected)
        # Future additions may move the trust parser while retaining the option.
        if '--fingerprint' not in (suite.tools / 'deep32-get.sh').read_text(encoding='ascii'):
            base = base[:-2]
        result = run('synthetic-missing-choices-refused', [], 1)
        suite.check('missing-choices-no-target-or-loader', not target.exists() and 'loader' not in events.read_text(encoding='ascii'))
        result = run('synthetic-plan-consent-required', ['--settings', choice_path], 1)
        suite.check('consent-digest-printed', plan['digest'] in result.stdout)
        suite.check('consent-refusal-no-target-or-loader', not target.exists() and 'loader' not in events.read_text(encoding='ascii'))
        events.write_text('', encoding='ascii')
        run('synthetic-valid-settings-before-one-loader', ['--settings', choice_path, '--accept-plan', plan['digest']], 0)
        order = events.read_text(encoding='ascii').splitlines()
        suite.check('one-loader-and-settings-before-loader', order.count('loader') == 1 and order.index('settings') < order.index('loader'), repr(order))
        suite.check('prerequisites-twice-before-settings', order[:1] == ['prerequisites'] and order.count('prerequisites') == 2 and order.index('mount') < order.index('settings'), repr(order))
        before = target.stat().st_mtime_ns
        events.write_text('', encoding='ascii')
        run('synthetic-repeat-idempotent', ['--settings', choice_path, '--accept-plan', plan['digest']], 0)
        suite.check('repeat-does-not-rewrite-settings', target.stat().st_mtime_ns == before)
        suite.check('repeat-one-loader', events.read_text(encoding='ascii').splitlines().count('loader') == 1)
        # A failed Forge loader ends the getter with a non-zero status (set -e passes the helper's 1 through), and the
        # handoff line is never printed.
        suite.env['SYNTHETIC_LOADER_FAIL'] = '1'
        failed = run('synthetic-loader-failure-exits-nonzero', ['--settings', choice_path, '--accept-plan', plan['digest']], 1)
        suite.check('loader-failure-no-handoff-line', 'Disc3 loader returned; settings handoff completed.' not in failed.stdout)
        del suite.env['SYNTHETIC_LOADER_FAIL']
        # An early prerequisite failure creates no destination/cache at all.
        denied_root = suite.root / 'synthetic-denied'
        denied_root.mkdir()
        suite.command_cwd = denied_root
        denied_out = denied_root / 'deep32-downloads'
        suite.env['SYNTHETIC_PREREQUISITES'] = 'missing'
        events.write_text('', encoding='ascii')
        run('synthetic-prerequisite-failure-before-writes', ['--settings', choice_path], 10)
        suite.check('prerequisite-failure-no-download-target-or-loader', not denied_out.exists() and events.read_text(encoding='ascii').splitlines() == ['prerequisites'])
        print('PASS: %d assertions; %d commands' % (len(suite.checks), len(suite.commands)))
    finally:
        suite.cleanup()


if __name__ == '__main__':
    main()
