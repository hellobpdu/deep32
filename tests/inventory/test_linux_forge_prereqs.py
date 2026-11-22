#!/usr/bin/env python3
"""Run the real Linux Forge C10 CLI with task-owned read-only host stubs."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASH = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')
SCRATCH = None
UNPROVEN = {'platform.recipe', 'host.membership-confirmed', 'gpu.release-envelope',
            'gpu.memory', 'gpu.ports', 'docker.storage', 'storage.plan'}
LIVE_CHECKS = {
    'host.tailscale-installed', 'host.tailscale-daemon', 'host.tailscale-signed-in',
    'host.magicdns', 'host.https-certificates', 'docker.local-context',
    'docker.server', 'docker.compose', 'docker.nvidia-runtime', 'docker.containerd',
}
FIXTURE_NAME = 'synthetic-host.synthetic-hidden-tailnet.ts.net'
DOCKER_ENDPOINT = 'unix:///var/run/docker.sock'

GATEWAY = r'''#!/bin/sh
set -u
tool=${0##*/}
printf '%s|%s\n' "$tool" "$*" >> "$SYNTHETIC_FORGE_LOG"
case "$tool:$*" in
    git:--version) echo 'git version 2.54.0' ;;
    gpg:--version) echo 'gpg 2.4.9' ;;
    python3:--version) echo 'Python 3.12.9' ;;
    curl:--version) echo 'curl 8.10.1' ;;
    uname:-s) echo Linux ;;
    uname:-m) echo x86_64 ;;
    tailscale:version) echo '1.90.1' ;;
    'tailscale:status --json') printf '%s\n' "$SYNTHETIC_FORGE_TAILSCALE" ;;
    'docker:--host unix:///var/run/docker.sock version --format {{.Server.Version}}') echo '29.1.3' ;;
    'docker:--host unix:///var/run/docker.sock compose version --short') echo '2.40.3' ;;
    'docker:--host unix:///var/run/docker.sock info --format {{json .}}') printf '%s\n' "$SYNTHETIC_FORGE_DOCKER" ;;
    'nvidia-smi:--query-gpu=compute_cap --format=csv,noheader') echo '8.6' ;;
    'python3:-I -B '*forge_preflight.py*)
        test "$4" = --source && test "$6" = --gpu-class || exit 97
        test "${14}" = --plan && test "${16}" = --accept-plan || exit 97
        test "${18}" = --journey && test "${19}" = J2 || exit 97
        test "${20}" = --os-id && test "${21}" = ubuntu || exit 97
        test "${22}" = --os-version && test "${23}" = 24.04 || exit 97
        test "$(cat)" = "{\"docker\":$SYNTHETIC_FORGE_DOCKER,\"tailscale\":$SYNTHETIC_FORGE_TAILSCALE}" || exit 97
        printf '%s\n' "$SYNTHETIC_FORGE_PROOF"
        ;;
    'python3:-I -B '*)
        case "$3" in */src/release-tools/deep32_get_whole.py) ;; *) exit 97 ;; esac
        if [ "${11}" = --ingestion ]; then
            [ "${SYNTHETIC_FORGE_INGESTION:-0}" = 1 ] || exit 97
            a1=$1 a2=$2 a3=$3 a4=$4 a5=$5 a6=$6 a7=$7 a8=$8 a9=$9 a10=${10}
            shift 11
            set -- "$a1" "$a2" "$a3" "$a4" "$a5" "$a6" "$a7" "$a8" "$a9" "$a10" "$@"
        else
            [ "${SYNTHETIC_FORGE_INGESTION:-0}" = 0 ] || exit 97
        fi
        [ "$#" -eq 19 ] && [ "$4 $5 $6 $7" = 'disc3 --release v0.1.0-alpha.3 --from' ] || exit 97
        [ "$8" = "$SYNTHETIC_FORGE_MEDIA" ] && [ "$9 ${10}" = '--gpu-class ampere86' ] || exit 97
        [ "${11}" = --key ] && [ "${13}" = --fingerprint ] || exit 97
        [ "${15} ${16}" = '--forge-storage --forge-data-root' ] || exit 97
        [ "${17}" = "$SYNTHETIC_FORGE_DATA" ] && [ "${18} ${19}" = '--gpg synthetic-gpg' ] || exit 97
        [ "$(cat)" = "$SYNTHETIC_FORGE_DOCKER" ] || exit 97
        printf '%s\n' "$SYNTHETIC_FORGE_STORAGE"
        ;;
    *) printf '%s|%s\n' "$tool" "$*" >> "$SYNTHETIC_FORGE_UNEXPECTED"; exit 97 ;;
esac
'''

NAMESPACE = r'''#!/bin/sh
set -u
# Exercise the bounded runner's command handoff without creating a namespace.
[ "$#" -ge 10 ] || exit 97
[ "$1 $2 $3 $4 $5" = '--user --map-current-user --pid --fork --kill-child=KILL' ] || exit 97
[ "$7" = '-c' ] && [ "$9" = inventory-tool ] || exit 97
case "${10}" in
    git|gpg|python3|curl|tailscale|docker|nvidia-smi|*/bash|*/bash.exe) ;;
    *) printf 'unexpected namespace child|%s\n' "${10}" >> "$SYNTHETIC_FORGE_UNEXPECTED"; exit 97 ;;
esac
shift 5
exec "$@"
'''

STARTUP = r'''
PATH="$SYNTHETIC_FORGE_BIN:/usr/bin:/bin"
export PATH
# Git Bash has no real Linux Docker socket. Replace only that file predicate.
function [ {
    if [[ $1 == -S && ($2 == /var/run/docker.sock || $2 == "$DEEP32_INVENTORY_TEST_ROOT/var/run/docker.sock") ]]; then
        [[ $SYNTHETIC_FORGE_SOCKET == yes ]]
    else
        builtin [ "$@"
    fi
}
source "$0" "$@"
'''


def shell_path(path):
    plain = Path(path).as_posix()
    if len(plain) > 2 and plain[1:3] == ':/':
        return '/' + plain[0].lower() + plain[2:]
    return plain


def program(path, content):
    path.write_text(content, encoding='ascii', newline='\n')
    path.chmod(0o700)


class ForgeHostTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='c10-forge-host-', dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('git', 'gpg', 'python3', 'curl', 'uname', 'tailscale', 'docker', 'nvidia-smi',
                     'ssh', 'wget', 'sudo', 'apt', 'wsl.exe'):
            program(self.bin / name, GATEWAY)
        program(self.bin / 'unshare', NAMESPACE)
        self.fake_root = self.root / 'linux-root'
        (self.fake_root / 'etc').mkdir(parents=True)
        (self.fake_root / 'etc/os-release').write_bytes(b'ID=ubuntu\nVERSION_ID="24.04"\n')
        self.audit = self.root / 'probe-argv.txt'
        self.unexpected = self.root / 'unexpected-argv.txt'

    def invoke(self, *extra, journey='J2', role='gpu-host', platform='Linux',
               docker=None, tailscale=None, has_socket=True, storage=None, proof=None, text=False, ingestion=False):
        config = {'OSType': 'linux', 'Runtimes': {'nvidia': {}}, 'DockerRootDir': '/synthetic/docker',
                  'ContainerdCommit': {'ID': 'synthetic-containerd-revision'}}
        status = {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': FIXTURE_NAME + '.'},
                  'CurrentTailnet': {'MagicDNSEnabled': True, 'Name': FIXTURE_NAME},
                  'CertDomains': [FIXTURE_NAME]}
        script = ROOT / 'install/deep32-inventory.sh'
        env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'HOME', 'USERPROFILE')
               if key in os.environ}
        env.update(DEEP32_INVENTORY_TEST_ROOT=shell_path(self.fake_root),
                   DEEP32_INVENTORY_TEST_UNAME_S=platform, DEEP32_INVENTORY_TEST_UNAME_M='x86_64',
                   SYNTHETIC_FORGE_SOCKET='yes' if has_socket else 'no',
                   SYNTHETIC_FORGE_BIN=shell_path(self.bin), SYNTHETIC_FORGE_LOG=shell_path(self.audit),
                   SYNTHETIC_FORGE_UNEXPECTED=shell_path(self.unexpected),
                   SYNTHETIC_FORGE_TAILSCALE=json.dumps(tailscale if tailscale is not None else status),
                   SYNTHETIC_FORGE_DOCKER=json.dumps(docker if docker is not None else config),
                   DOCKER_HOST='ssh://synthetic-do-not-contact.invalid',
                   DOCKER_CONTEXT='synthetic-remote-selection')
        if proof is not None:
            env['SYNTHETIC_FORGE_PROOF'] = json.dumps(proof)
        if ingestion:
            env['SYNTHETIC_FORGE_INGESTION'] = '1'   # the stub then expects --ingestion from --forge-ingestion
        if storage is not None:
            env.update(SYNTHETIC_FORGE_MEDIA=shell_path(self.root / 'local-iso-set'),
                       SYNTHETIC_FORGE_DATA=shell_path(self.root / 'forge-data'),
                       SYNTHETIC_FORGE_STORAGE=json.dumps(storage))
        args = [str(BASH), '--noprofile', '--norc', '-c', STARTUP, shell_path(script),
                '--journey', journey, '--role', role, '--check-prereqs', *(() if text else ('--format', 'json'))]
        result = subprocess.run([*args, *extra], env=env, cwd=self.root, check=False,
                                capture_output=True, text=True, timeout=45)
        self.assertFalse(self.unexpected.exists(),
                         self.unexpected.read_text() if self.unexpected.exists() else None)
        self.assertNotIn(FIXTURE_NAME, result.stdout + result.stderr)
        return result

    def assert_status(self, result, code):
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        self.assertEqual('', result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual('deep32.inventory.v2', data['schema'])
        self.assertEqual('linux', data['platform'])
        self.assertEqual(['gpu-host'], data['selected_roles'])
        return data, {check['id']: check for check in data['prerequisites']}

    def test_host_probes_cannot_waive_unproven_install_checks(self):
        data, checks = self.assert_status(self.invoke(), 10)
        self.assertEqual([], data['storage_plan'])
        for key in UNPROVEN:
            with self.subTest(check=key):
                self.assertTrue(checks[key]['required'])
                self.assertEqual('unknown', checks[key]['state'])
                self.assertTrue(checks[key]['code'].endswith('_UNKNOWN'))
        for key in LIVE_CHECKS:
            self.assertTrue(checks[key]['required'], key)
            self.assertEqual('met', checks[key]['state'], key)
        calls = self.audit.read_text().splitlines()
        self.assertEqual([f'docker|--host {DOCKER_ENDPOINT} version --format {{{{.Server.Version}}}}',
                          f'docker|--host {DOCKER_ENDPOINT} compose version --short',
                          f'docker|--host {DOCKER_ENDPOINT} info --format {{{{json .}}}}'],
                         [line for line in calls if line.startswith('docker|')])

    def test_discovered_cdi_is_evidence_but_configuration_alone_is_not(self):
        config = {'OSType': 'linux', 'Runtimes': {}, 'CDISpecDirs': ['/etc/cdi']}
        _, checks = self.assert_status(self.invoke(journey='J4',
                                          docker={**config, 'DiscoveredDevices': [{'Name': 'nvidia.com/gpu=0'}]}), 10)
        self.assertEqual('met', checks['docker.nvidia-runtime']['state'])
        _, checks = self.assert_status(self.invoke(journey='J4', docker=config), 10)
        self.assertNotEqual('met', checks['docker.nvidia-runtime']['state'])

    def test_missing_publication_evidence_refuses(self):
        _, checks = self.assert_status(self.invoke(tailscale={
            'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': FIXTURE_NAME},
            'CurrentTailnet': {'MagicDNSEnabled': False},
            'CertDomains': ['synthetic-other-host.synthetic-hidden-tailnet.ts.net'],
        }), 10)
        for key in ('host.magicdns', 'host.https-certificates'):
            self.assertTrue(checks[key]['required'])
            self.assertNotEqual('met', checks[key]['state'])

    def test_non_linux_engine_is_not_local_linux_evidence(self):
        _, checks = self.assert_status(self.invoke(docker={
            'OSType': 'windows', 'Runtimes': {'nvidia': {}},
        }), 10)
        for key in ('docker.local-context', 'docker.nvidia-runtime'):
            self.assertEqual('missing', checks[key]['state'])

    def test_unreachable_containerd_is_missing(self):
        # The daemon reports "N/A" when it cannot reach containerd (a masked or stopped containerd.service).
        config = {'OSType': 'linux', 'Runtimes': {'nvidia': {}}, 'DockerRootDir': '/synthetic/docker'}
        for docker in (config, dict(config, ContainerdCommit={'ID': 'N/A'})):
            with self.subTest(docker=docker):
                _, checks = self.assert_status(self.invoke(docker=docker), 10)
                self.assertTrue(checks['docker.containerd']['required'])
                self.assertEqual('missing', checks['docker.containerd']['state'])
                self.assertEqual('met', checks['docker.server']['state'])

    def test_reachable_containerd_without_revision_is_met(self):
        # Ubuntu 24.04's packaged containerd 2.2.1 reports an empty revision while Docker reaches it (lab, 2026-11-22).
        config = {'OSType': 'linux', 'Runtimes': {'nvidia': {}}, 'DockerRootDir': '/synthetic/docker',
                  'ContainerdCommit': {'ID': ''}}
        _, checks = self.assert_status(self.invoke(docker=config), 10)   # 10: the fixture leaves other checks open
        self.assertEqual('met', checks['docker.containerd']['state'])
        self.assertEqual('met', checks['docker.server']['state'])

    def test_absent_local_socket_never_queries_docker(self):
        _, checks = self.assert_status(self.invoke(has_socket=False), 10)
        self.assertNotEqual('met', checks['docker.server']['state'])
        self.assertNotIn('docker|', self.audit.read_text())

    def test_missing_gpu_tool_is_a_host_refusal(self):
        (self.bin / 'nvidia-smi').unlink()
        _, checks = self.assert_status(self.invoke(), 10)
        gpu = {'gpu.driver', 'gpu.capability', 'gpu.memory'}
        for key in gpu:
            self.assertTrue(checks[key]['required'])
            self.assertEqual('missing', checks[key]['state'])

    def test_forge_storage_routes_local_evidence_and_aggregates_shared_filesystems(self):
        # The release helper's signature and storage observation are tested
        # independently. Here it provides measured rows and sees socket info.
        media = shell_path(self.root / 'local-iso-set')
        target = shell_path(self.root / 'forge-data')
        rows = [
            {'filesystem_id': 'synthetic-shared-private-id', 'needed_bytes': 50, 'free_bytes': 110},
            {'filesystem_id': 'synthetic-shared-private-id', 'needed_bytes': 60, 'free_bytes': 100},
            {'filesystem_id': 'synthetic-other-private-id', 'needed_bytes': 30, 'free_bytes': 50},
        ]
        result = self.invoke('--forge-media', media, '--forge-class', 'ampere86',
                             '--forge-data-root', target, '--forge-gpg', 'synthetic-gpg',
                             storage=rows)
        data, checks = self.assert_status(result, 10)
        self.assertEqual('met', checks['docker.storage']['state'])
        self.assertEqual('met', checks['gpu.capability']['state'])
        self.assertEqual('missing', checks['storage.plan']['state'])
        self.assertEqual([{'filesystem': 'filesystem-1', 'needed_bytes': 110, 'free_bytes': 100},
                          {'filesystem': 'filesystem-2', 'needed_bytes': 30, 'free_bytes': 50}],
                         data['storage_plan'])
        self.assertNotIn('synthetic-shared-private-id', result.stdout)

    def test_forge_storage_with_the_ingestion_add_on_passes_it_to_the_helper(self):
        rows = [{'filesystem_id': 'synthetic-shared-private-id', 'needed_bytes': 50, 'free_bytes': 110},
                {'filesystem_id': 'synthetic-shared-private-id', 'needed_bytes': 60, 'free_bytes': 100},
                {'filesystem_id': 'synthetic-other-private-id', 'needed_bytes': 30, 'free_bytes': 50}]
        result = self.invoke('--forge-media', shell_path(self.root / 'local-iso-set'), '--forge-class', 'ampere86',
                             '--forge-data-root', shell_path(self.root / 'forge-data'), '--forge-gpg', 'synthetic-gpg',
                             '--forge-ingestion', storage=rows, ingestion=True)
        data, checks = self.assert_status(result, 10)
        self.assertEqual('met', checks['docker.storage']['state'])
        self.assertEqual('missing', checks['storage.plan']['state'])
        self.assertEqual([{'filesystem': 'filesystem-1', 'needed_bytes': 110, 'free_bytes': 100},
                          {'filesystem': 'filesystem-2', 'needed_bytes': 30, 'free_bytes': 50}],
                         data['storage_plan'])

    def test_completed_forge_evidence_passes_every_mandatory_gate(self):
        rows = [{'filesystem_id': 'synthetic-volume', 'needed_bytes': 88, 'free_bytes': 100}]
        collected = {key: True for key in (
            'platform.recipe', 'host.membership-confirmed', 'gpu.release-envelope',
            'gpu.driver', 'gpu.capability', 'gpu.memory', 'gpu.ports', 'docker.storage',
        )}
        args = ('--forge-media', shell_path(self.root / 'synthetic-outer'), '--forge-class', 'ampere86',
                '--forge-data-root', shell_path(self.root / 'synthetic-data'),
                '--forge-plan', shell_path(self.root / 'synthetic-plan.json'), '--accept-plan', 'a' * 64)
        proof = {'prerequisites': collected, 'details': {}, 'storage_plan': rows}
        _, checks = self.assert_status(self.invoke(*args, proof=proof), 0)
        self.assertTrue(all(row['state'] == 'met' for row in checks.values() if row['required']))
        for row in (*collected, 'storage.plan'):
            self.assertTrue(checks[row]['required'], row)
        collected['gpu.ports'] = False
        proof['details']['gpu.ports'] = 'Port 8081 is occupied by an unverified local TCP listener.'
        _, failed = self.assert_status(self.invoke(*args, proof=proof), 10)
        self.assertTrue(failed['gpu.ports']['required'])
        self.assertEqual('missing', failed['gpu.ports']['state'])
        self.assertIn('8081', failed['gpu.ports']['fix'])

    def test_owner_waiver_line_is_printed_once_and_waives_no_other_check(self):
        rows = [{'filesystem_id': 'synthetic-volume', 'needed_bytes': 88, 'free_bytes': 100}]
        collected = {key: True for key in (
            'platform.recipe', 'host.membership-confirmed', 'gpu.release-envelope',
            'gpu.driver', 'gpu.capability', 'gpu.memory', 'gpu.ports', 'docker.storage',
        )}
        notice = ('Note: this host type is accepted for 0.1.0-alpha.3 under an owner waiver dated 2026-11-18 '
                  '(recipe synthetic-waived-recipe). I have not yet run a full install on it; '
                  'FORGE-PREREQUISITES.json lists what was not run.')
        args = ('--forge-media', shell_path(self.root / 'synthetic-outer'), '--forge-class', 'ampere86',
                '--forge-data-root', shell_path(self.root / 'synthetic-data'),
                '--forge-plan', shell_path(self.root / 'synthetic-plan.json'), '--accept-plan', 'a' * 64)
        proof = {'prerequisites': collected, 'details': {}, 'storage_plan': rows, 'notice': notice}

        def text(**changes):
            result = self.invoke(*args, proof=dict(proof, **changes), text=True)
            self.assertEqual('', result.stderr)
            return result.returncode, result.stdout.splitlines()

        code, lines = text()
        self.assertEqual(0, code, lines)
        self.assertEqual(1, lines.count(notice))
        self.assertEqual(notice, lines[-2])
        self.assertEqual('Prerequisites passed for gpu-host; review the selected installation plan before changes.',
                         lines[-1])
        self.assertTrue(any(line.startswith('platform.recipe=met: ') for line in lines))
        passed_code, passed_lines = text(notice=None)
        self.assertEqual((0, [line for line in lines if line != notice]), (passed_code, passed_lines))
        # The machine-readable report keeps its contract; the line is for the person at the terminal.
        data, checks = self.assert_status(self.invoke(*args, proof=proof), 0)
        self.assertNotIn('notice', data)
        self.assertNotIn(notice, json.dumps(data))
        self.assertEqual('met', checks['platform.recipe']['state'])
        # Every other check and the storage budget still decide the result under a waiver.
        code, lines = text(prerequisites=dict(collected, **{'gpu.driver': False}))
        self.assertEqual(10, code)
        self.assertIn(notice, lines)
        self.assertTrue(any(line.startswith('gpu.driver=missing: ') for line in lines))
        code, lines = text(storage_plan=[dict(rows[0], free_bytes=87)])
        self.assertEqual(10, code)
        self.assertTrue(any(line.startswith('storage.plan=missing: ') for line in lines))
        # No accepted recipe, no line; and only one plain printable line is ever shown.
        code, lines = text(prerequisites=dict(collected, **{'platform.recipe': False}))
        self.assertEqual(10, code)
        self.assertNotIn(notice, lines)
        for unsafe in ('x' * 301, 7):
            with self.subTest(unsafe=unsafe):
                code, shown = text(notice=unsafe)
                self.assertEqual((0, passed_lines), (code, shown))
        # The shell JSON reader turns control escapes into spaces in every string, so this is still one line.
        code, shown = text(notice='two\nlines')
        self.assertEqual((0, passed_lines[:-1] + ['two lines', passed_lines[-1]]), (code, shown))

    def test_unsupported_and_conflicting_options_refuse_before_probes(self):
        facts = self.root / 'synthetic-facts.json'
        facts.write_bytes(b'{"facts":{"os":{"family":"linux"}}}\n')
        media = shell_path(self.root / 'local-iso-set')
        target = shell_path(self.root / 'forge-data')
        cases = (self.invoke(journey='J6'), self.invoke('--net'),
                 self.invoke('--forge-media', media),
                 self.invoke('--facts', shell_path(facts), '--forge-media', media,
                             '--forge-class', 'ampere86', '--forge-data-root', target))
        for result in cases:
            self.assertIn(result.returncode, (2, 10), result.stdout + result.stderr)
        self.assertFalse(self.audit.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SCRATCH = args.scratch_root.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
