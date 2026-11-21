#!/usr/bin/env python3
"""Offline Forge/full install matrices using authenticated synthetic release media."""
import argparse
import importlib.util
import json
from pathlib import Path


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scratch-root', type=Path, required=True)
    args = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    regression = module('release_regression', Path(__file__).with_name('test_release_tools.py'))
    settings = module('settings', repo / 'src/release-tools/deep32_settings.py')
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    options = argparse.Namespace(tools_dir=repo / 'src/release-tools', scratch_root=args.scratch_root,
        baseline_only=False, bash=Path('C:/Program Files/Git/bin/bash.exe'),
        powershell=Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'),
        gpg=Path('C:/Program Files/GnuPG/bin/gpg.exe'), gpgconf=Path('C:/Program Files/GnuPG/bin/gpgconf.exe'),
        keygen_gpg=Path('C:/Program Files/GnuPG/bin/gpg.exe'))
    suite = regression.Suite(options)
    try:
        suite.setup()
        release = suite.assemble('synthetic-full-release')
        suite.sign(release, 'synthetic-full-release')
        meta = json.loads((release / 'ASSEMBLY.json').read_bytes())
        disc = next(row for row in meta['discs'] if row['role'] == 'disc3')
        content = suite.root / 'synthetic-content'
        content.mkdir()
        backing = suite.root / 'synthetic-docker-storage'
        backing.mkdir()
        media = suite.root / 'synthetic-mounted-media'
        (media / 'forms').mkdir(parents=True)
        (media / 'forms/provision.py').write_text('# synthetic inert media\n', encoding='ascii')
        (media / 'deep32/payload').mkdir(parents=True)
        events = suite.root / 'synthetic-full-events.txt'
        dispatcher = suite.root / 'synthetic-full-dispatch.py'
        regression.text(dispatcher, r'''import importlib.util,json,os,pathlib,subprocess,sys
original=sys.argv[1:]
args=[v for v in original if v not in ('-I','-B')]
event=pathlib.Path(os.environ['SYNTHETIC_FULL_EVENTS'])
if len(args)>1 and pathlib.Path(args[0]).name=='deep32_settings.py':
    if args[1]=='stage':
        spec=importlib.util.spec_from_file_location('synthetic_settings',args[0]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        directory=pathlib.Path(args[args.index('--directory')+1]);m.protect(directory,directory=True)
        plan=m.read_json(args[args.index('--plan')+1]);m.stage(plan,directory)
        with event.open('a') as f:f.write('settings\n')
        raise SystemExit(0)
    if args[1]=='run-loader':
        assert any(v.endswith('load-docker.sh') for v in args)
        role=os.environ['SYNTHETIC_FULL_ROLE']
        assert args.count('--docker-role')==1 and args[args.index('--docker-role')+1]==role
        assert args[args.index('--platform')+1]=='linux'
        staged=pathlib.Path(args[args.index('--settings')+1])
        assert staged.read_bytes()==pathlib.Path(os.environ['SYNTHETIC_FULL_CHOICES']).read_bytes()
        for flag,variable in [('--data-root','SYNTHETIC_FULL_DATA'),('--docker-storage-path','SYNTHETIC_FULL_STORAGE')]:
            assert args.count(flag)==1 and pathlib.Path(args[args.index(flag)+1])==pathlib.Path(os.environ[variable])
        if role=='full':
            assert args.count('--content')==1 and pathlib.Path(args[args.index('--content')+1])==pathlib.Path(os.environ['SYNTHETIC_FULL_CONTENT'])
        else:
            assert '--content' not in args and '--engine' not in args and '--gateway' not in args
        with event.open('a') as f:f.write('loader\n')
        raise SystemExit(0)
raise SystemExit(subprocess.call([sys.executable,*original],env=os.environ.copy()))
''')
        regression.text(suite.shim / 'python3', '#!/usr/bin/env bash\nexec "$RELEASE_TEST_PYTHON" -I -B "$SYNTHETIC_FULL_DISPATCH" "$@"\n')
        guard_sh = suite.root / 'synthetic-full-guard.sh'
        regression.text(guard_sh, r'''#!/usr/bin/env bash
set -euo pipefail
record() { printf '%s\n' "$1" >> "$SYNTHETIC_FULL_EVENTS_SH"; }
id() { printf '%s\n' 0; }
bash() { if [[ "$*" == *install/deep32-inventory.sh* ]]; then record prerequisites; [[ "${SYNTHETIC_FULL_PREREQUISITES:-pass}" == pass ]]; return; fi; return 97; }
# The wrapper already created this mount directory. Create only its children;
# recursive mkdir unnecessarily revisits protected Windows parent directories.
mount() { local destination=${@: -1}; record mount; mkdir -- "$destination/forms" "$destination/deep32"; mkdir -- "$destination/deep32/payload"; : >"$destination/forms/load-docker.sh"; }
umount() { record unexpected-unmount; return 97; }
export PATH="$RELEASE_TEST_SHIM:/usr/bin:/bin:$PATH"
source "$1" "${@:2}"
''')
        guard_ps = suite.root / 'synthetic-full-guard.ps1'
        regression.text(guard_ps, r'''$ErrorActionPreference='Stop'
. $env:SYNTHETIC_FULL_TOOL
function Record([string]$Value){[IO.File]::AppendAllText($env:SYNTHETIC_FULL_EVENTS,$Value+"`n")}
function Get-Command {param([string]$Name,[string]$CommandType,[string]$ErrorAction)
    if($Name -in @('Mount-DiskImage','docker.exe')){return @{Name=$Name}}
    Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction $ErrorAction
}
function Test-Deep32InstallPrerequisites {param([string]$Journey);Record prerequisites;if($env:SYNTHETIC_FULL_PREREQUISITES -ceq 'missing'){exit 10}}
$script:realPlainPath=${function:Get-Deep32PlainPath}
function Get-Deep32PlainPath {param([string]$Path,[switch]$Directory,[switch]$MayNotExist)
    if($Path -match '^[A-Za-z]:\\$'){$Path=$env:SYNTHETIC_FULL_MEDIA}
    elseif($Path -match '^[A-Za-z]:\\(forms|deep32\\payload)(\\|$)'){$Path=Join-Path $env:SYNTHETIC_FULL_MEDIA $Path.Substring(3)}
    & $script:realPlainPath -Path $Path -Directory:$Directory -MayNotExist:$MayNotExist
}
function Get-DiskImage {param([string]$ImagePath,[string]$ErrorAction);return [pscustomobject]@{Attached=$false}}
function Mount-DiskImage {param([string]$ImagePath,[string]$Access,[switch]$PassThru,[string]$ErrorAction);if($Access -cne 'ReadOnly'){throw 'Not read-only'};Record mount;return [pscustomobject]@{Attached=$true}}
function Dismount-DiskImage {throw 'Unexpected Docker-role detach'}
function Get-Volume {param([Parameter(ValueFromPipeline=$true)]$InputObject);process{return [pscustomobject]@{DriveLetter=$env:SYNTHETIC_FULL_MEDIA.Substring(0,1)}}}
function Invoke-Deep32DockerLoader {param([string[]]$Arguments)
    if($Arguments[0] -notlike '*forms\provision.py'){throw 'Wrong shared provisioner'}
    if($Arguments[[Array]::IndexOf($Arguments,'--platform')+1] -cne 'windows'){throw 'Wrong host platform'}
    if(@($Arguments | Where-Object {$_ -ceq '--docker-role'}).Count -ne 1 -or $Arguments[[Array]::IndexOf($Arguments,'--docker-role')+1] -cne $env:SYNTHETIC_FULL_ROLE){throw 'Wrong role'}
    foreach($pair in @(@('--data-root',$env:SYNTHETIC_FULL_DATA),@('--docker-storage-path',$env:SYNTHETIC_FULL_STORAGE))){
        if(@($Arguments | Where-Object {$_ -ceq $pair[0]}).Count -ne 1 -or $Arguments[[Array]::IndexOf($Arguments,$pair[0])+1] -cne $pair[1]){throw 'Wrong data or backing storage path'}
    }
    if($env:SYNTHETIC_FULL_ROLE -ceq 'full'){
        if(@($Arguments | Where-Object {$_ -ceq '--content'}).Count -ne 1 -or $Arguments[[Array]::IndexOf($Arguments,'--content')+1] -cne $env:SYNTHETIC_FULL_CONTENT){throw 'Wrong full-role content path'}
    }elseif($Arguments -ccontains '--content' -or $Arguments -ccontains '--engine' -or $Arguments -ccontains '--gateway'){throw 'Forge must not receive full-role inputs'}
    $staged=$Arguments[[Array]::IndexOf($Arguments,'--settings')+1]
    if([IO.File]::ReadAllText($staged) -cne [IO.File]::ReadAllText($env:SYNTHETIC_FULL_CHOICES)){throw 'Missing protected C7 settings'}
    Record settings;Record loader
}
$values=Get-Content -LiteralPath $env:SYNTHETIC_FULL_ARGS -Raw | ConvertFrom-Json
try {Invoke-Deep32Main -Arguments $values} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
''')
        suite.env.update(SYNTHETIC_FULL_DISPATCH=str(dispatcher), SYNTHETIC_FULL_EVENTS=str(events),
            SYNTHETIC_FULL_EVENTS_SH=regression.msys(events), SYNTHETIC_FULL_MEDIA=str(media),
            SYNTHETIC_FULL_TOOL=str(suite.tools / 'deep32-get.ps1'))
        for kind, platform, role, journey, profile in [
                ('sh', 'linux', 'forge', 'J2', 'two-machines'),
                ('ps1', 'windows', 'forge', 'J1', 'win-nvidia'),
                ('sh', 'linux', 'full', 'J3', 'headless'),
                ('ps1', 'windows', 'full', 'J3', 'headless')]:
            case = role + '-' + kind
            choice = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge', **settings.DEFAULTS, 'profile': profile}
            choice_path = suite.root / ('synthetic-' + case + '-choices.json')
            choice_path.write_bytes(settings.canonical(choice))
            case_root = suite.root / ('synthetic-case-' + case)
            case_root.mkdir()
            suite.command_cwd = case_root
            out = case_root / 'deep32-downloads'
            data = suite.root / ('synthetic-data-' + case)
            plan = settings.make_plan(choice, disc['sha256'], out, journey, 'docker', platform, role,
                                      settings.validator().profiles, data, content if role == 'full' else None, backing)
            suite.check(case + '-plan-role-and-target', plan['identity']['docker_role'] == role and
                        plan['identity']['target'] == str(data / 'config/settings.json') and
                        ('content' in plan['identity']) == (role == 'full'))
            suite.env.update(SYNTHETIC_FULL_ROLE=role, SYNTHETIC_FULL_CHOICES=str(choice_path),
                SYNTHETIC_FULL_DATA=str(data), SYNTHETIC_FULL_STORAGE=str(backing), SYNTHETIC_FULL_CONTENT=str(content))
            base = ['disc3', '--install', '--journey', journey, '--non-interactive',
                '--data-root', data, '--docker-storage-path', backing, '--from', release, '--release', 'v0.1.0-alpha.1',
                '--key', suite.key, '--fingerprint', suite.fingerprint]
            if role == 'full':
                base += ['--docker-role', 'full', '--content', content]
            def run(label, extra, expected):
                events.write_text('', encoding='ascii')
                argv = base + extra
                if kind == 'sh':
                    return suite.run(case + '-' + label, [options.bash, '--noprofile', '--norc', regression.msys(guard_sh),
                        regression.msys(suite.tools / 'deep32-get.sh'), *[regression.msys(v) if isinstance(v, Path) else str(v) for v in argv]], expected)
                arguments = suite.root / 'synthetic-full-args.json'
                arguments.write_text(json.dumps([str(v) for v in argv]), encoding='ascii')
                suite.env['SYNTHETIC_FULL_ARGS'] = str(arguments)
                return suite.ps(case + '-' + label, guard_ps, expected=expected)
            run('choices-required', [], 1)
            suite.check(case + '-missing-choices-no-loader', 'loader' not in events.read_text())
            result = run('consent-required', ['--settings', choice_path], 1)
            suite.check(case + '-consent-path-bound', plan['digest'] in result.stdout)
            suite.check(case + '-consent-before-mount-and-target', 'mount' not in events.read_text() and not data.exists())
            run('wrong-consent-refused', ['--settings', choice_path, '--accept-plan', '0' * 64], 1)
            suite.check(case + '-wrong-consent-before-mount-and-target', 'mount' not in events.read_text() and not data.exists())
            run('accepted-loader', ['--settings', choice_path, '--accept-plan', plan['digest']], 0)
            order = events.read_text().splitlines()
            suite.check(case + '-one-loader-after-settings', order.count('loader') == 1 and order.index('settings') < order.index('loader'), repr(order))
            suite.check(case + '-prerequisites-twice-and-retained-media', order.count('prerequisites') == 2 and 'mount' in order and 'unexpected-unmount' not in order, repr(order))
            suite.check(case + '-protected-stage-cleaned', not list(out.glob('.settings-*')) and not list(out.glob('.deep32-*/.metadata.*')))
            run('wsl-selection-refused', ['--form', 'wsl', '--settings', choice_path], 10)
            suite.check(case + '-unsupported-form-before-host-gate', not events.read_text())
        print('PASS: %d assertions; %d commands' % (len(suite.checks), len(suite.commands)))
    finally:
        suite.cleanup()


if __name__ == '__main__':
    main()
