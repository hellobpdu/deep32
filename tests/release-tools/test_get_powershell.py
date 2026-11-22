#!/usr/bin/env python3
"""Offline native PowerShell wrapper tests with an inert shared-helper fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


HELPER = r'''import hashlib,json,os,pathlib,sys
root=pathlib.Path(os.environ['DEEP32_SYNTHETIC_ROOT'])
(root/'helper-args.json').write_text(json.dumps(sys.argv[1:]),encoding='ascii')
mode=os.environ.get('DEEP32_SYNTHETIC_MODE','ok')
if mode in ('fail','needs-action','pending'):
    print('Synthetic shared-helper refusal',file=sys.stderr)
    raise SystemExit({'needs-action':10,'pending':3}.get(mode,1))
if mode=='bad-record':
    print('invalid')
else:
    image=pathlib.Path(os.environ['DEEP32_SYNTHETIC_IMAGE'])
    images=[image]
    if sys.argv[1]=='disc3' and 'v0.1.0-alpha.1' not in sys.argv:
        images += [image.parent/'synthetic-embedder.iso']
        if '--ingestion' in sys.argv or '--full' in sys.argv: images += [image.parent/'synthetic-ingestion.iso']
        if '--full' in sys.argv: images += [image.parent/'synthetic-full.iso']
    if mode=='duplicate-record': images=images[:1]*2
    for image in images:
        print(str(image)+'\t'+image.name+'\t'+str(image.stat().st_size)+'\t'+hashlib.sha256(image.read_bytes()).hexdigest())
'''

# This fixture imports the shipping helper and replaces only the GPG subprocess
# responses. Trust parsing, source-path checks and whole-image verification run
# unchanged. Audit guards forbid sockets and writes anywhere on source media.
REAL_HELPER = r'''import importlib.util,json,os,pathlib,sys
root=pathlib.Path(os.environ['DEEP32_SYNTHETIC_ROOT'])
source=pathlib.Path(os.environ['DEEP32_SYNTHETIC_SOURCE']).resolve()
spec=importlib.util.spec_from_file_location('synthetic_real_getter',os.environ['DEEP32_SYNTHETIC_REAL_HELPER'])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def under_source(value):
    return isinstance(value,(str,bytes,os.PathLike)) and pathlib.Path(os.fsdecode(value)).resolve().is_relative_to(source)
def audit(event,args):
    if event.startswith('socket.'):
        raise AssertionError('Local verification attempted a socket')
    if event=='open' and under_source(args[0]) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
        raise AssertionError('Local verification attempted a source write')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink') and any(under_source(value) for value in args[:2]):
        raise AssertionError('Local verification attempted a source mutation')
sys.addaudithook(audit)
class FakeProcess:
    def __init__(self,argv,**kwargs):
        assert '--no-options' in argv and '--no-autostart' in argv and '--no-auto-key-retrieve' in argv
        home=pathlib.Path(argv[argv.index('--homedir')+1]).resolve()
        assert not home.is_relative_to(source) and kwargs['env']['GNUPGHOME']==str(home)
        with (root/'fake-gpg.jsonl').open('a',encoding='ascii') as stream:
            stream.write(json.dumps(argv)+'\n')
        fingerprint='B205B406548627AABF5250D8A078C5F25D025DD6'
        if '--show-keys' in argv:
            self.raw=('pub:\nfpr:::::::::'+fingerprint+':\n').encode('ascii')
        elif '--import' in argv:
            self.raw=('[GNUPG:] IMPORT_OK 1 '+fingerprint+'\n').encode('ascii')
        else:
            self.raw=('[GNUPG:] VALIDSIG '+fingerprint+' 2000-01-01 0 0 4 0 1 10 00\n').encode('ascii')
        self.returncode=1 if os.environ['DEEP32_SYNTHETIC_MODE']=='real-bad-signature' and '--verify' in argv else 0
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def communicate(self,timeout=None):return self.raw,b''
m.manifest.gpg_executable=lambda explicit='': pathlib.Path(sys.executable)
m.manifest.subprocess.Popen=FakeProcess
try:
    m.main(sys.argv[1:])
except Exception as error:
    print('Synthetic real-helper refusal: '+str(error),file=sys.stderr)
    raise SystemExit(1)
'''

HARNESS = r'''$ErrorActionPreference='Stop'
. $env:DEEP32_SYNTHETIC_TOOL
$script:Deep32ScriptDirectory=Join-Path $env:DEEP32_SYNTHETIC_ROOT 'helpers'
function Get-Deep32Python {return $env:DEEP32_SYNTHETIC_PYTHON}
function Invoke-Deep32Fetch {throw 'Legacy payload transport was called'}
function Confirm-Deep32Signature {throw 'Legacy signature route was called'}
function Get-Deep32Manifest {throw 'Legacy manifest route was called'}
function Test-Deep32InstallPrerequisites {param([string]$Journey)
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'prerequisites-called.txt'),'called')
}
function Invoke-Deep32ManifestHelper {
    param([string[]]$Arguments)
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'vm.json'),(ConvertTo-Json -InputObject $Arguments -Compress))
}
function New-Deep32SettingsPlan {
    param([string]$ImageHash,[string]$Destination,[hashtable]$Options,[object[]]$MediaComponents)
    if($MediaComponents){
        [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'plan-components.json'),(ConvertTo-Json -InputObject $MediaComponents -Depth 8 -Compress))
    }
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'plan.json'),(ConvertTo-Json -InputObject $Options -Depth 6 -Compress))
    if($env:DEEP32_SYNTHETIC_MODE -ceq 'changed-after-plan'){
        [IO.File]::WriteAllText($env:DEEP32_SYNTHETIC_IMAGE,'SYNTHETIC CHANGED IMAGE')
    }
    return @{plan=@{marker='synthetic'};path='unused';directory='unused'}
}
function Invoke-Deep32Install {
    param([string]$Image,[string]$Destination,[hashtable]$SettingsPlan)
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'install.json'),(ConvertTo-Json -InputObject @{image=$Image;destination=$Destination;plan=$SettingsPlan.plan} -Compress))
}
function Invoke-Deep32SplitInstall {
    param([object[]]$MediaComponents,[hashtable]$SettingsPlan,[hashtable]$Evidence)
    if ($env:DOCKER_HOST -cne 'npipe:////./pipe/dockerDesktopLinuxEngine' -or $env:DOCKER_CONTEXT -or $env:TS_SOCKET) {
        throw 'The split handoff retained a different host policy.'
    }
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'split-install.json'),(ConvertTo-Json -InputObject $MediaComponents -Depth 8 -Compress))
    [IO.File]::WriteAllText((Join-Path $env:DEEP32_SYNTHETIC_ROOT 'split-evidence.json'),(ConvertTo-Json -InputObject $Evidence -Depth 8 -Compress))
}
function Remove-Deep32SettingsStage {param([string]$Directory)}
$arguments=Get-Content -LiteralPath (Join-Path $env:DEEP32_SYNTHETIC_ROOT 'args.json') -Raw | ConvertFrom-Json
try {Invoke-Deep32Main -Arguments $arguments} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', type=Path, required=True)
    parser.add_argument('--powershell', type=Path, default=Path(os.environ.get('SystemRoot', 'C:/Windows')) /
                        'System32/WindowsPowerShell/v1.0/powershell.exe')
    options = parser.parse_args()
    scratch = options.scratch_root.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if any(scratch.iterdir()):
        raise ValueError('The scratch root must be empty and owned by this test run')
    tool = Path(__file__).resolve().parents[2] / 'src/release-tools/deep32-get.ps1'
    records = []
    checks = 0

    def check(condition, detail):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(detail)

    def run(name, arguments, expected=0, mode='ok'):
        case = scratch / name
        case.mkdir()
        (case / 'helpers').mkdir()
        real = mode.startswith('real-')
        (case / 'helpers/deep32_get_whole.py').write_text(REAL_HELPER if real else HELPER, encoding='ascii')
        (case / 'harness.ps1').write_text(HARNESS, encoding='ascii')
        source = case / "synthetic media's path"
        source.mkdir()
        image = source / 'synthetic-image.img'
        payload = b'INVENTED TEST IMAGE disc1\n'
        image.write_bytes(payload)
        if arguments[0] == 'disc3' and 'v0.1.0-alpha.1' not in arguments:
            for name in ('synthetic-embedder.iso', 'synthetic-ingestion.iso', 'synthetic-full.iso'):
                (source / name).write_bytes(payload + name.encode('ascii'))
        if real:
            fixture = 'mirrors-v2.json' if arguments[0] == 'disc3' else 'mirrors.json'
            data = json.loads((tool.parent / 'contracts/fixtures/valid' / fixture).read_bytes())
            sums = {}
            for disc in data['discs'] + data.get('bundles', []):
                raw = ('INVENTED TEST IMAGE ' + disc.get('role', disc.get('bundle_id')) + '\n').encode('ascii')
                digest = hashlib.sha256(raw).hexdigest()
                disc.update(size=len(raw), sha256=digest, chunk_sha256=[digest])
                (source / disc['file']).write_bytes(raw)
                sums[disc['file']] = digest
                if disc.get('role') == arguments[0] or disc.get('bundle_id') == 'embedder-turing':
                    image = source / disc['file']
            if data['format_version'] == 2:
                canonical = lambda value: (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('ascii')
                cap = json.loads((tool.parent / 'contracts/fixtures/capabilities/manifest.json').read_bytes())
                cap_raw = canonical(cap)
                data['capability_manifest']['sha256'] = hashlib.sha256(cap_raw).hexdigest()
                (source / data['capability_manifest']['file']).write_bytes(cap_raw)
                sums[data['capability_manifest']['file']] = data['capability_manifest']['sha256']
                by_id = {b['id']: b for b in cap['bundles']}
                for descriptor in data['bundles']:
                    descriptor['bundle_sha256'] = hashlib.sha256(canonical(by_id[descriptor['bundle_id']])).hexdigest()
            raw = (json.dumps(data, sort_keys=True, indent=2) + '\n').encode('ascii')
            (source / 'MIRRORS.json').write_bytes(raw)
            sums['MIRRORS.json'] = hashlib.sha256(raw).hexdigest()
            (source / 'SHA256SUMS').write_bytes(''.join(digest + '  ' + name + '\n' for name, digest in sorted(sums.items())).encode('ascii'))
            (source / 'SHA256SUMS.sig').write_bytes(b'SYNTHETIC SIGNATURE, NOT AN OPENPGP SIGNATURE\n')
            (case / 'synthetic-public.gpg').write_bytes(b'SYNTHETIC PUBLIC KEY, NOT AN OPENPGP KEY\n')
            if mode == 'real-corrupt-image':
                image.write_bytes(b'INVENTED CORRUPT IMAGE\n')
            if mode == 'real-corrupt-index':
                (source / 'MIRRORS.json').write_bytes(raw.replace(b'archive.org', b'example.org'))
        source_before = {path.name: path.read_bytes() for path in source.iterdir()}
        arguments = [str(value).replace('{source}', str(source)).replace('{case}', str(case)) for value in arguments]
        (case / 'args.json').write_text(json.dumps(arguments), encoding='ascii')
        environment = os.environ.copy()
        environment.update(TEMP=str(case), TMP=str(case), TMPDIR=str(case), PYTHONUTF8='1',
                           DEEP32_SYNTHETIC_ROOT=str(case), DEEP32_SYNTHETIC_TOOL=str(tool),
                           DEEP32_SYNTHETIC_IMAGE=str(image), DEEP32_SYNTHETIC_PYTHON=sys.executable,
                           DEEP32_SYNTHETIC_HASH=hashlib.sha256(payload).hexdigest(), DEEP32_SYNTHETIC_MODE=mode,
                           DEEP32_SYNTHETIC_SOURCE=str(source), DEEP32_SYNTHETIC_REAL_HELPER=str(tool.parent / 'deep32_get_whole.py'))
        command = [str(options.powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(case / 'harness.ps1')]
        result = subprocess.run(command, cwd=case, env=environment, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)
        records.append({'name': name, 'argv': command, 'arguments': arguments, 'exit': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr})
        (scratch / 'commands.json').write_text(json.dumps(records, sort_keys=True, indent=2) + '\n', encoding='ascii')
        check(result.returncode == expected, name + ': ' + repr((result.returncode, result.stdout, result.stderr)))
        check('Legacy ' not in result.stderr, name + ': legacy alpha.2 route')
        if real:
            check(source_before == {path.name: path.read_bytes() for path in source.iterdir()}, name + ': source changed')
            check(not (case / 'deep32-downloads').exists(), name + ': local destination created')
        return case, result

    case, _ = run('default-online', ['disc2', '--to', '{case}/synthetic destination'])
    forwarded = json.loads((case / 'helper-args.json').read_bytes())
    check(forwarded[forwarded.index('--release') + 1] == 'v0.1.0-alpha.3', 'default release')
    check(forwarded[forwarded.index('--to') + 1] == str(case / 'synthetic destination'), 'literal destination')
    check(not list(case.rglob('*.response')) and not list(case.rglob('*.joining')) and
          not list(case.rglob('*.download')), 'wrapper owns no payload temporaries')
    for index, alias in enumerate(('--from', '-From', '--source', '-sOuRcE')):
        case, _ = run('local-' + str(index), ['disc2', alias, '{source}'])
        forwarded = json.loads((case / 'helper-args.json').read_bytes())
        check('--from' in forwarded and '--to' not in forwarded and '--cache' not in forwarded, 'local helper arguments')
        check(not (case / 'deep32-downloads').exists(), 'local verification created destination')
        check(sorted(path.name for path in (case / "synthetic media's path").iterdir()) == ['synthetic-image.img'],
              'local verification changed source directory')
    for name, arguments in (
            ('aliases-conflict', ['disc2', '--from', '{source}', '--source', '{source}']),
            ('to-conflict', ['disc2', '-From', '{source}', '-To', '{case}/must-not-exist']),
            ('duplicate-native', ['disc2', '-From', '{source}', '--from', '{source}']),
            ('empty-source', ['disc2', '--from', '']),
            ('source-control', ['disc2', '--from', '{source}\t']),
            ('source-uri-query', ['disc2', '--from', 'file:///{source}?synthetic']),
            ('source-uri-fragment', ['disc2', '--from', 'file:///{source}#synthetic']),
            ('unsafe-repository', ['disc2', '--from', '{source}', '--repo', 'example/../project']),
            ('selection-role', ['disc1', '--from', '{source}', '--gpu-class', 'ampere'])):
        case, _ = run(name, arguments, 1)
        check(not (case / 'helper-args.json').exists(), name + ': helper ran')
        check(not (case / 'must-not-exist').exists() and not (case / 'deep32-downloads').exists(), name + ': destination created')
    for mode, expected in (('fail', 1), ('needs-action', 10), ('pending', 3), ('bad-record', 1)):
        case, _ = run('native-' + mode, ['disc2', '--from', '{source}'], expected, mode)
        check(not (case / 'install.json').exists(), 'failure reached install')
    case, _ = run('selection-forwarding', ['disc3', '--from', '{source}', '--gpu-class', 'ampere', '--ingestion',
                                           '--full', '--repo', 'example/project', '--gpg', '{case}/public gpg.exe'])
    forwarded = json.loads((case / 'helper-args.json').read_bytes())
    check('--full' in forwarded and '--ingestion' in forwarded and forwarded[forwarded.index('--gpu-class') + 1] == 'ampere',
          'selection arguments lost')
    check(forwarded[forwarded.index('--repo') + 1] == 'example/project' and
          Path(forwarded[forwarded.index('--gpg') + 1]) == case / 'public gpg.exe', 'literal tool/repository arguments')
    check(not (case / 'deep32-downloads').exists(), 'multi-local records created target')
    case, _ = run('local-core-and-class', ['disc3', '--from', '{source}', '--gpu-class', 'turing'])
    check(not (case / 'deep32-downloads').exists(), 'two verified local records copied payload')
    case, _ = run('duplicate-capability-record', ['disc3', '--from', '{source}', '--gpu-class', 'turing'], 1, 'duplicate-record')
    split = ['disc3', '--install', '--journey', 'J1', '--gpu-class', 'turing', '--from', '{source}',
             '--confirm-tailnet', 'synthetic-operator.ts.net', '--non-interactive', '--accept-plan', 'synthetic-consent',
             '--data-root', '{case}/synthetic data', '--docker-storage-path', '{source}', '--confirm-desktop-isolation']
    case, result = run('split-forge-handoff', split)
    forwarded = json.loads((case / 'helper-args.json').read_bytes())
    check('--ingestion' in forwarded and '--full' not in forwarded, 'split installation did not select ingestion automatically')
    plan = json.loads((case / 'plan-components.json').read_bytes())
    handed = json.loads((case / 'split-install.json').read_bytes())
    check([row['component'] for row in plan] == ['core', 'embedder-turing', 'ingestion'],
          'split plan did not contain all three components in producer order')
    check(plan == handed and not (case / 'install.json').exists(), 'the split handoff changed the selected plan or used the legacy loader')
    choices = json.loads((case / 'plan.json').read_bytes())
    evidence = json.loads((case / 'split-evidence.json').read_bytes())
    check(choices['confirm_tailnet'] == 'synthetic-operator.ts.net' and choices['desktop_isolation_confirmed']
          and evidence['gpu_class'] == 'turing'
          and evidence['source'] == str(case / "synthetic media's path"), 'signed Windows preflight input mismatch')
    for row in plan:
        original = Path(row['image']).read_bytes()
        check(row['disc']['sha256'] == hashlib.sha256(original).hexdigest() and row['disc']['size'] == len(original),
              'split plan uses the exact complete image identity')
    for name, arguments in (
        ('missing-class-refused-early', ['disc3', '--install', '--journey', 'J1']),
        ('workstation-install-refused-early', ['disc1', '--install']),
        ('content-install-refused-early', ['disc2', '--install']),
        ('split-full-refused-early', split + ['--full']),
        ('split-offline-required', split[:6] + split[8:]),
        ('split-noninteractive-tailnet-required', split[:8] + split[10:]),
        ('split-desktop-isolation-required', split[:-1]),
        ('split-backing-path-required', split[:-3] + split[-1:]),
        ('split-unc-source-refused', split[:7] + [r'\\synthetic\share'] + split[8:]),
        ('split-relative-backing-refused', split[:-2] + ['relative-backing'] + split[-1:]),
        ('split-device-data-refused', [r'\\?\C:\synthetic' if item == '{case}/synthetic data' else item for item in split]),
    ):
        case, _ = run(name, arguments, 1)
        check(not any((case / path).exists() for path in ('prerequisites-called.txt', 'helper-args.json',
              'plan.json', 'install.json', 'split-install.json', 'deep32-downloads')), name + ': guard allowed side effects')
    case, _ = run('legacy-local', ['disc2', '--source', '{source}', '--release', 'v0.1.0-alpha.1'])
    check('--from' in json.loads((case / 'helper-args.json').read_bytes()), 'legacy source copied instead of verified')
    case, _ = run('vm-handoff', ['disc1', '--from', '{source}', '--prepare-vm', '--hypervisor', 'hyper-v'])
    forwarded = json.loads((case / 'vm.json').read_bytes())
    check(forwarded[0] == 'prepare-vm' and forwarded[1] == str(case / "synthetic media's path/synthetic-image.img") and
          forwarded[-2] == 'hyper-v', 'verified VM source handoff')
    install = ['disc3', '--release', 'v0.1.0-alpha.1', '--from', '{source}', '--install', '--journey', 'J1', '--non-interactive', '--accept-plan',
               'synthetic-consent', '--data-root', '{case}/synthetic data']
    case, _ = run('forge-handoff', install)
    plan = json.loads((case / 'plan.json').read_bytes())
    check(plan['docker_role'] == 'forge' and plan['form'] == 'docker' and plan['platform'] == 'windows', 'Forge form contract')
    check(json.loads((case / 'install.json').read_bytes())['image'] == str(case / "synthetic media's path/synthetic-image.img"),
          'install uses verified local image')
    case, _ = run('full-handoff', install + ['--docker-role', 'full', '--content', '{source}'])
    plan = json.loads((case / 'plan.json').read_bytes())
    check(plan['docker_role'] == 'full' and plan['content'] == str(case / "synthetic media's path"), 'full role contract')
    case, _ = run('changed-source', install, 1, 'changed-after-plan')
    check((case / 'plan.json').exists() and not (case / 'install.json').exists(), 'changed source reached loader')
    local = ['disc2', '-From', '{source}', '--key', '{case}/synthetic-public.gpg']
    case, result = run('real-local', local, mode='real-ok')
    check('Verified local image ' in result.stderr, 'actual shared local verifier not reached')
    check(len((case / 'fake-gpg.jsonl').read_text().splitlines()) == 3, 'actual signature verification flow not reached')
    for mode in ('real-bad-signature', 'real-corrupt-index', 'real-corrupt-image'):
        run(mode, local, 1, mode)
    multi_local = ['disc3', '--gpu-class', 'turing', '--from', '{source}', '--key', '{case}/synthetic-public.gpg']
    case, result = run('real-local-core-and-class', multi_local, mode='real-ok')
    check(result.stderr.count('Verified local image ') == 2, 'actual multi-media verifier did not produce exact pair')
    run('real-local-corrupt-class', multi_local, 1, 'real-corrupt-image')
    print('PASS: %d assertions; %d PowerShell commands' % (checks, len(records)))


if __name__ == '__main__':
    main()
