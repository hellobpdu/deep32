#!/usr/bin/env python3
"""Offline POSIX wrapper contract with an inert shared-transfer gateway.

The shared transfer state machine has its own transport matrix. This suite runs
the actual Bash entrypoint, checking argument vectors, exits, local no-copy
behaviour and the preparation handoff without sockets or host changes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def msys(value):
    value = str(value).replace('\\', '/')
    if len(value) > 2 and value[1:3] == ':/':
        return '/' + value[0].lower() + value[2:]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', required=True, type=Path)
    parser.add_argument('--bash', type=Path, default=Path('C:/Program Files/Git/bin/bash.exe'))
    args = parser.parse_args()
    root = args.scratch_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise SystemExit('Supply an empty per-suite scratch directory')
    repo = Path(__file__).resolve().parents[2]
    tools, shim, temp = root / 'src/release-tools', root / 'shim', root / 'temp'
    for folder in (tools, shim, temp):
        folder.mkdir(parents=True)
    inventory = root / 'install/deep32-inventory.sh'
    inventory.parent.mkdir()
    inventory.write_bytes(b'echo synthetic-prerequisite-check >&2\nexit 10\n')
    for name in ('deep32-get.sh', 'deep32_get_manifest.py'):
        shutil.copyfile(repo / 'src/release-tools' / name, tools / name)
    events = root / 'synthetic-helper-events.jsonl'
    guard = root / 'synthetic-network-guard.txt'
    payload = b'INVENTED WRAPPER TEST IMAGE\n'
    source = root / 'synthetic read-only media'
    source.mkdir()
    (source / 'synthetic.iso').write_bytes(payload)
    for name in ('synthetic-core.iso', 'synthetic-embedder.iso'):
        (source / name).write_bytes(payload + name.encode('ascii'))
    # No authenticator is substituted in the product; this inert gateway tests
    # only the wrapper's shared-helper contract, not release trust.
    gateway = '''import hashlib,json,pathlib,sys
sys.stdout.reconfigure(newline='\\n')
args=sys.argv[1:]
events=pathlib.Path(EVENTS)
with events.open('a',encoding='ascii') as stream:
    stream.write(json.dumps(args)+'\\n')
if '--repo' in args and args[args.index('--repo')+1]=='synthetic/exit':
    raise SystemExit(10)
if '--repo' in args and args[args.index('--repo')+1]=='synthetic/malformed':
    print('invalid\\tselection')
    raise SystemExit(0)
local='--from' in args
folder=pathlib.Path(args[args.index('--from' if local else '--to')+1])
names=['synthetic.img' if args[0]=='disc1' else 'synthetic.iso']
if args[0]=='disc3':
    names=['synthetic-core.iso','synthetic-embedder.iso']
    if '--ingestion' in args or '--full' in args: names.append('synthetic-ingestion.iso')
    if '--full' in args: names.append('synthetic-full.iso')
if '--repo' in args and args[args.index('--repo')+1]=='synthetic/duplicate': names=names[:1]*2
for name in names:
    image=folder/name
    if not local and not image.exists(): image.write_bytes(PAYLOAD)
    print(str(image.resolve())+'\\t'+image.name+'\\t'+str(image.stat().st_size)+'\\t'+hashlib.sha256(image.read_bytes()).hexdigest())
'''.replace('EVENTS', repr(str(events))).replace('PAYLOAD', repr(payload))
    (tools / 'deep32_get_whole.py').write_text(gateway, encoding='ascii', newline='\n')
    (shim / 'python3').write_text('#!/usr/bin/env bash\nexec ' + "'" + msys(sys.executable) + "'" + ' "$@"\n', encoding='ascii', newline='\n')
    (shim / 'curl').write_text('#!/usr/bin/env bash\nprintf attempted > ' + "'" + msys(guard) + "'" + '\nexit 97\n', encoding='ascii', newline='\n')
    for item in shim.iterdir():
        item.chmod(0o700)
    env = os.environ.copy()
    env.update(PATH=str(shim) + os.pathsep + env.get('PATH', ''), PYTHONUTF8='1',
               TEMP=str(temp), TMP=str(temp), TMPDIR=msys(temp),
               SYNTHETIC_TEST_SHIM=msys(shim), RELEASE_TEST_ROOT=str(root))
    commands, checks = [], []

    def check(name, condition):
        checks.append({'id': name, 'pass': bool(condition)})
        if not condition:
            raise AssertionError(name)

    def run(name, arguments, expected=0):
        work = root / name
        work.mkdir()
        command = [str(args.bash), '--noprofile', '--norc', '-c',
                   'export PATH="$SYNTHETIC_TEST_SHIM:$PATH"; test "$(command -v curl)" = "$SYNTHETIC_TEST_SHIM/curl" || exit 97; source "$1" "${@:2}"',
                   '--', msys(tools / 'deep32-get.sh'),
                   *[msys(item) if isinstance(item, Path) else str(item) for item in arguments]]
        result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=90)
        commands.append({'id': name, 'command': command, 'exit': result.returncode})
        (work / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
        (work / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        check(name + '-exit', result.returncode == expected)
        return work, result

    subprocess.run([str(args.bash), '--noprofile', '--norc', '-n', msys(tools / 'deep32-get.sh')], check=True, env=env)
    check('bash-parses', True)
    for alias in ('--from', '--source'):
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
        work, _ = run('local-' + alias[2:], ['disc2', alias, source])
        check(alias + '-no-destination', not (work / 'deep32-downloads').exists())
        check(alias + '-no-source-writes', before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()})
        record = json.loads(events.read_text(encoding='ascii').splitlines()[-1])
        check(alias + '-shared-local-only', '--from' in record and '--to' not in record and '--cache' not in record)
    work, _ = run('default-online', ['disc2'])
    record = json.loads(events.read_text(encoding='ascii').splitlines()[-1])
    check('default-alpha2', record[record.index('--release') + 1] == 'v0.1.0-alpha.2')
    check('one-online-image', len(list(work.rglob('*.iso'))) == 1)
    check('whole-no-part-response-or-join', not any(p.name.endswith(('.download', '.response', '.joining')) or '.part' in p.name for p in work.rglob('*')))
    work, _ = run('selection-forwarding', ['disc3', '--gpu-class', 'synthetic-class', '--ingestion', '--full', '--repo', 'synthetic/release'])
    check('four-selected-bundles-one-copy-each', sorted(p.name for p in work.rglob('*.iso')) == ['synthetic-core.iso', 'synthetic-embedder.iso', 'synthetic-full.iso', 'synthetic-ingestion.iso'])
    record = json.loads(events.read_text(encoding='ascii').splitlines()[-1])
    check('capabilities-forwarded-exactly', record[record.index('--gpu-class') + 1] == 'synthetic-class' and '--ingestion' in record and '--full' in record)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    work, _ = run('local-core-and-class', ['disc3', '--gpu-class', 'turing', '--from', source])
    check('multi-local-no-destination', not (work / 'deep32-downloads').exists())
    check('multi-local-no-copy', before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()})
    run('duplicate-capability-record', ['disc3', '--gpu-class', 'turing', '--from', source, '--repo', 'synthetic/duplicate'], 1)
    before = events.read_bytes()
    work, result = run('split-install-reaches-prerequisites',
                       ['disc3', '--gpu-class', 'turing', '--install', '--journey', 'J2'], 10)
    check('split-install-guard-accepted', 'synthetic-prerequisite-check' in result.stderr and 'ingestion ISO' in result.stdout)
    check('preflight-before-helper-or-target', events.read_bytes() == before and not (work / 'deep32-downloads').exists())
    for name, arguments in (
        ('missing-class-refusal', ['disc3', '--install', '--journey', 'J2']),
        ('workstation-install-refusal', ['disc1', '--install']),
        ('content-install-refusal', ['disc2', '--install']),
        ('split-full-install-refusal', ['disc3', '--gpu-class', 'turing', '--install', '--journey', 'J2', '--full']),
    ):
        work, result = run(name, arguments, 1)
        check(name + '-before-prerequisites', 'synthetic-prerequisite-check' not in result.stderr)
        check(name + '-before-helper-or-target', events.read_bytes() == before and not (work / 'deep32-downloads').exists())
    run('shared-exit-preserved', ['disc2', '--repo', 'synthetic/exit'], 10)
    run('malformed-result-refused', ['disc2', '--repo', 'synthetic/malformed'], 1)
    for name, options in (
        ('both-local-aliases', ['--from', source, '--source', source]),
        ('local-with-target', ['--from', source, '--to', root / 'forbidden-target']),
        ('source-with-target', ['--source', source, '--to', root / 'forbidden-source-target']),
        ('duplicate-local-option', ['--from', source, '--from', source]),
        ('wrong-capability-disc', ['--gpu-class', 'synthetic-class']),
        ('malformed-repository', ['--repo', 'https://synthetic.invalid/release']),
        ('local-line-feed', ['--from', str(source) + '\n']),
        ('local-carriage-return', ['--from', str(source) + '\r']),
        ('local-tab', ['--from', str(source) + '\t']),
        ('local-escape', ['--from', str(source) + '\x1b']),
    ):
        before = events.read_text(encoding='ascii')
        run(name, ['disc2', *options], 1)
        check(name + '-before-helper', before == events.read_text(encoding='ascii'))
    work, _ = run('legacy-local-in-place', ['disc2', '--release', 'v0.1.0-alpha.1', '--source', source])
    check('legacy-local-no-cache', not (work / 'deep32-downloads').exists())
    work, _ = run('local-failure-before-preparation-target', ['disc1', '--from', root / 'missing-local-media', '--prepare-vm', '--hypervisor', 'kvm'], 1)
    check('failed-local-vm-no-target', not (work / 'deep32-downloads').exists())
    # Use a separate aligned invented installer to exercise the real VM writer.
    vm_source = root / 'synthetic-vm-media'
    vm_source.mkdir()
    (vm_source / 'synthetic.img').write_bytes(bytes(1024 * 1024))
    work, _ = run('local-vm-handoff', ['disc1', '--from', vm_source, '--prepare-vm', '--hypervisor', 'kvm'])
    # The VM helper names its directory after the full source stem.
    installers = list(work.rglob('installer.img'))
    check('local-vm-original-path-and-bytes', len(installers) == 1 and installers[0].read_bytes() == bytes(1024 * 1024))
    check('local-vm-source-not-copied-to-download', not (work / 'deep32-downloads/synthetic.img').exists())
    check('no-curl-in-whole-or-local-path', not guard.exists())
    check('host-temporaries-cleaned', not any(temp.iterdir()))
    summary = {'checks': checks, 'commands': commands}
    (root / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='ascii')
    print('PASS: %d assertions; %d commands' % (len(checks), len(commands)))


if __name__ == '__main__':
    main()
