"""WSL's bridge setup is checked with scratch files and shell functions only."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from kit_support import KIT, module


provision = module('wsl_bridge_provision', 'forms/provision.py')
EXPECTED_DAEMON = {
    'bridge': 'deep32br0', 'iptables': False, 'ip6tables': False,
    'default-address-pools': [{'base': '10.213.0.0/17', 'size': 24}],
    'runtimes': {'nvidia': {'path': 'nvidia-container-runtime', 'runtimeArgs': []}},
}
ISO_BRANCH = b'''else
  cat > /etc/docker/daemon.json.new <<'EOF'
{
  "runtimes": {
    "nvidia": {"path": "nvidia-container-runtime", "runtimeArgs": []}
  }
}
EOF
fi
'''


def bash():
    if os.name == 'nt':
        path = Path('C:/Program Files/Git/bin/bash.exe')
        if not path.is_file():
            raise unittest.SkipTest('Git Bash is required for offline shell checks')
        return str(path)
    return '/bin/bash'


def shell_path(path):
    value = Path(path).resolve().as_posix()
    if os.name == 'nt':
        value = '/' + value[0].lower() + value[2:]
    return value


def heredoc(source, path):
    return source.split("cat > " + path + " <<'EOF'\n", 1)[1].split('\nEOF\n', 1)[0] + '\n'


class WslBridgeTests(unittest.TestCase):
    def test_exact_daemon_and_two_producers_agree(self):
        source = (KIT / 'autoinstall/install-packages.sh').read_text(encoding='ascii')
        self.assertEqual(provision.WSL_DAEMON, EXPECTED_DAEMON)
        self.assertEqual(json.loads(heredoc(source, '/etc/docker/daemon.json.new')), EXPECTED_DAEMON)
        self.assertEqual(heredoc(source, '/usr/local/sbin/deep32-bridge'), provision.WSL_BRIDGE_SCRIPT)
        self.assertEqual(heredoc(source, '/etc/systemd/system/docker.service.d/deep32-bridge.conf'),
                         provision.WSL_BRIDGE_DROPIN)
        self.assertEqual(provision.WSL_BRIDGE_DROPIN,
                         '[Service]\nExecStartPre=/usr/local/sbin/deep32-bridge\n')
        self.assertNotIn('"bridge": "none"', source)
        self.assertNotIn("'bridge': 'none'", (KIT / 'forms/provision.py').read_text(encoding='ascii'))
        self.assertFalse(ipaddress.ip_network('10.213.255.0/24').overlaps(ipaddress.ip_network('10.213.0.0/17')))

    def test_iso_branch_is_byte_identical_to_round_one(self):
        source = (KIT / 'autoinstall/install-packages.sh').read_bytes()
        start = source.index(b'else\n  cat > /etc/docker/daemon.json.new')
        end = source.index(b'\nfi\n', start) + len(b'\nfi\n')
        self.assertEqual(source[start:end], ISO_BRANCH)

    def test_preinstall_files_and_reload_exist_for_wsl_only(self):
        source = (KIT / 'autoinstall/install-packages.sh').read_text(encoding='ascii')
        install = source.index('DEBIAN_FRONTEND=noninteractive apt-get')
        block = source[source.index("# docker.io's postinst"):install]
        for token in ('cat > /usr/local/sbin/deep32-bridge',
                      'cat > /etc/systemd/system/docker.service.d/deep32-bridge.conf',
                      'systemctl daemon-reload'):
            self.assertLess(source.index(token), install)
        for form in ('wsl', 'iso'):
            with self.subTest(form=form), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                code = block
                for old, new in (('/etc/docker', 'docker'), ('/usr/local/sbin', 'sbin'),
                                 ('/etc/systemd/system', 'systemd')):
                    code = code.replace(old, shell_path(root / new))
                trace = root / 'commands'
                stub = 'systemctl() { printf "%s\\n" "$*" >> ' + shlex.quote(shell_path(trace)) + '; }\n'
                result = subprocess.run([bash(), '--noprofile', '--norc', '-s'],
                                        input=('set -eu\nform=' + form + '\n' + stub + code).encode('ascii'),
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                daemon = json.loads((root / 'docker/daemon.json').read_text(encoding='ascii'))
                if form == 'wsl':
                    self.assertEqual(daemon, EXPECTED_DAEMON)
                    self.assertEqual((root / 'sbin/deep32-bridge').read_text(encoding='ascii'), provision.WSL_BRIDGE_SCRIPT)
                    expected = provision.WSL_BRIDGE_DROPIN.replace('/usr/local/sbin', shell_path(root / 'sbin'))
                    self.assertEqual((root / 'systemd/docker.service.d/deep32-bridge.conf').read_text(encoding='ascii'), expected)
                    self.assertEqual(trace.read_text(encoding='ascii'), 'daemon-reload\n')
                else:
                    self.assertEqual(daemon, {'runtimes': EXPECTED_DAEMON['runtimes']})
                    self.assertFalse((root / 'sbin').exists())
                    self.assertFalse((root / 'systemd').exists())
                    self.assertFalse(trace.exists())

    def test_bridge_script_is_posix_idempotent_and_keeps_existing_ipv4(self):
        script = provision.WSL_BRIDGE_SCRIPT
        self.assertTrue(script.startswith('#!/bin/sh\n'))
        self.assertNotIn('docker0', script)
        for initial in ('absent', 'link', 'ipv4'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if initial != 'absent':
                    (root / 'link').touch()
                if initial == 'ipv4':
                    (root / 'address').write_text('10.44.0.1/24\n', encoding='ascii', newline='\n')
                stub = '''ip() {
  printf '%s\n' "$*" >> "$TEST_STATE/commands"
  case "$*" in
    'link show dev deep32br0') test -f "$TEST_STATE/link";;
    'link add name deep32br0 type bridge')
      test ! -f "$TEST_STATE/link" || return 10
      : > "$TEST_STATE/link";;
    '-4 addr show dev deep32br0')
      if test -f "$TEST_STATE/address"; then
        printf '    inet %s\n' "$(cat "$TEST_STATE/address")"
      fi;;
    'addr add 10.213.255.1/24 dev deep32br0')
      test -f "$TEST_STATE/link" && test ! -f "$TEST_STATE/address" || return 11
      printf '10.213.255.1/24\n' > "$TEST_STATE/address";;
    'link set dev deep32br0 up') test -f "$TEST_STATE/link";;
    *) return 12;;
  esac
}
'''
                code = stub + '\nbridge_setup() {\n' + script + '}\nbridge_setup\nbridge_setup\n'
                result = subprocess.run([bash(), '--noprofile', '--norc', '--posix', '-s'],
                                        input=code.encode('ascii'), capture_output=True, timeout=10,
                                        env=dict(os.environ, TEST_STATE=shell_path(root)))
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = (root / 'commands').read_text(encoding='ascii').splitlines()
                self.assertEqual(calls.count('link add name deep32br0 type bridge'), int(initial == 'absent'))
                self.assertEqual(calls.count('addr add 10.213.255.1/24 dev deep32br0'), int(initial != 'ipv4'))
                self.assertEqual(calls.count('link set dev deep32br0 up'), 2)
                self.assertEqual((root / 'address').read_text(encoding='ascii'),
                                 ('10.44.0.1/24' if initial == 'ipv4' else '10.213.255.1/24') + '\n')


if __name__ == '__main__':
    unittest.main()
