#!/usr/bin/env python3
"""Offline checks of disc 2 over the network (sbin/d32-content-fetch) and what it touches, with no OpenBSD.

d32-content-fetch runs in process under a synthetic root (D32_ROOT). mount, umount, vnconfig, pfctl, tailscale and
d32-c6 are answered by a stand-in that keeps a mount table and the vnd units and records every call, and the image is
served by an HTTP server on 127.0.0.1 inside the test that honours Range and can drop a connection, ignore Range or
answer 404 (the test hook D32_FETCH_TEST_ORIGIN sends every request there). No other network is used.

Covered: the command line, the source URLs and the tailnet peers, a download that stops and carries on (Range),
refusals (no space, an update staged or running, a path on the active slot, a slot that is not its own file system),
the vnd device rule (importer, imp_disc, the Import Report window, the first start's probe), cleanup, re-attach after
a restart, the report's stage fetch, First Start's model and the shipped configuration (doas, pf, unbound, strings).
On Windows the shell is Git for Windows' bash by its full path, never a bare bash. Set D32_TEST_WORK to keep the
fixtures in a chosen folder.
"""
import contextlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / 'src/workstation/distrib/site'
D32 = SITE / 'usr/local/d32'
IMPORTER = D32 / 'libexec/d32-import'
STRINGS = SITE / 'usr/local/share/d32/setup/strings.tsv'
sys.path.insert(0, str(IMPORTER))
import d32import  # noqa: E402
import imp_disc  # noqa: E402
import imp_fs  # noqa: E402
import imp_paths  # noqa: E402
import imp_report  # noqa: E402

WINDOWS = os.name == 'nt'
SHELL = (Path('C:/Program Files/Git/bin/bash.exe') if WINDOWS else
         Path(shutil.which('ksh') or shutil.which('bash') or '/bin/sh'))
VERSION = '0.1.0-alpha.2'
IMAGE_NAME = 'deep32-%s-disc2-content.iso' % VERSION
CODE = 'ABCDE23456'
MIB = 1 << 20


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


FETCH = load('d32_content_fetch', D32 / 'sbin/d32-content-fetch')
UIJSON = load('content_fetch_uijson', D32 / 'libexec/d32-uijson')


def disc_image(size=3 * MIB + 12345, disc=True):
    """Bytes that look like an ISO 9660 image to d32-content-fetch: the volume descriptor at sector 16."""
    data = bytearray((i * 7 + 3) % 251 for i in range(size))
    if disc:
        data[32768:32774] = b'\x01CD001'
    return bytes(data)


# ---------------------------------------------------------------------------------------------- the stand-ins

class Server:
    """The image over HTTP on 127.0.0.1, with Range; each request is recorded as (path, Range header)."""

    def __init__(self, data, path):
        self.data, self.path, self.requests = data, path, []
        self.drop_after = []         # per request in turn: stop after this many body bytes (None: send all)
        self.ignore_range = False
        self.status = None
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.requests.append((self.path, self.headers.get('Range')))
                if server.status or self.path != server.path:
                    self.send_error(server.status or 404)
                    return
                data, start = server.data, 0
                match = re.fullmatch(r'bytes=([0-9]+)-', self.headers.get('Range') or '')
                if match and not server.ignore_range:
                    start = int(match.group(1))
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header('Content-Range', 'bytes */%d' % len(data))
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(data) - 1, len(data)))
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(data) - start))
                self.end_headers()
                body = data[start:]
                limit = server.drop_after.pop(0) if server.drop_after else None
                self.wfile.write(body if limit is None else body[:limit])
                self.wfile.flush()
                self.close_connection = True

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self):
        return 'http://127.0.0.1:%d' % self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class System:
    """mount, umount, vnconfig, pfctl, tailscale and d32-c6 of a Workstation whose active slot is a (sd0d) and
    inactive slot b (sd0e), both read-only; four vnd units, all free."""

    def __init__(self):
        self.mounts = {'/': ['/dev/sd0a', 'ffs', ['local', 'wxallowed']],
                       '/d32/a': ['/dev/sd0d', 'ffs', ['local', 'nodev', 'nosuid', 'read-only', 'noatime']],
                       '/d32/b': ['/dev/sd0e', 'ffs', ['local', 'nodev', 'nosuid', 'read-only', 'noatime']],
                       '/var': ['/dev/sd0f', 'ffs', ['local', 'nodev', 'nosuid']]}
        self.units = {'vnd%d' % n: None for n in range(4)}
        self.calls, self.spawned = [], []
        self.status = {'BackendState': 'Running', 'Peer': {}}
        self.import_status = 0

    def run(self, name, args, timeout=120):
        args = [str(a) for a in args]
        self.calls.append((name,) + tuple(args))
        out, status = '', 0
        if name == 'mount' and not args:
            out = ''.join('%s on %s type %s (%s)\n' % (dev, point, kind, ', '.join(options))
                          for point, (dev, kind, options) in self.mounts.items())
        elif name == 'mount' and args[:2] == ['-u', '-o']:
            options = self.mounts[args[3]][2]
            options[:] = [o for o in options if o != 'read-only']
            if args[2].startswith('ro'):
                options.insert(3, 'read-only')
        elif name == 'umount':
            self.mounts = {p: row for p, row in self.mounts.items() if p != args[0]}
        elif name == 'vnconfig' and args == ['-l']:
            out = ''.join('%s: %s\n' % (unit, 'covering %s on sd0e, inode 7' % file if file else 'not in use')
                          for unit, file in self.units.items())
        elif name == 'vnconfig' and args[0] == '-u':
            self.units[args[1]] = None
        elif name == 'vnconfig':
            unit = args[0] if len(args) == 2 else next(u for u, f in self.units.items() if f is None)
            self.units[unit] = args[-1]
            out = '' if len(args) == 2 else unit + '\n'
        elif name == 'tailscale':
            out = json.dumps(self.status)
        return subprocess.CompletedProcess([name] + args, status, out, '')

    def spawn(self, name, args, wait=False):
        self.spawned.append((name,) + tuple(args))
        return self.import_status if name == 'd32-content-import' else 0

    def named(self, name):
        return [call[1:] for call in self.calls if call[0] == name]


class _Fixture(unittest.TestCase):
    """A synthetic Workstation root, the stand-ins, and d32-content-fetch run in process."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='content-fetch-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('etc/d32', 'd32/a', 'd32/b', 'd32/var', 'var/run/d32/c6', 'usr/local/share/d32/setup',
                       'stubs'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/d32/release').write_text(VERSION + '\n', encoding='ascii', newline='\n')
        (self.root / 'var/run/d32/c6/boot').write_text('0123abcd\n', encoding='ascii', newline='\n')
        shutil.copyfile(STRINGS, self.root / 'usr/local/share/d32/setup/strings.tsv')
        self.data = disc_image()
        self.server = Server(self.data, '/download/deep32-%s/%s' % (VERSION, IMAGE_NAME))
        self.addCleanup(self.server.close)
        self.env = {'D32_ROOT': str(self.root), 'D32_STUBS': str(self.root / 'stubs'),
                    'D32_TEST_CLOCK': '1800000000', 'D32_TEST_RAND': '0123abcd',
                    'D32_TEST_FREE_BYTES': str(40 << 30), 'D32_TEST_LIVE_PIDS': '',
                    'D32_FETCH_TEST_ORIGIN': self.server.origin}
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        self.system = System()
        self.active = 'a'
        for module, name, value in ((imp_paths, '_HOOKS', None), (imp_paths, '_CLOCK_READS', 0),
                                    (FETCH, 'run', self.system.run), (FETCH, 'spawn', self.system.spawn),
                                    (FETCH, 'read_active', lambda: self.active), (FETCH, 'RETRY_PAUSE', 0)):
            patch = mock.patch.object(module, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def fetch(self, *argv, **env):
        os.environ.update(env)
        imp_paths._HOOKS = None
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'stdout', out), contextlib.redirect_stderr(err):
            status = FETCH.main(list(argv))
        self.out, self.err = out.getvalue(), err.getvalue()
        return status

    def node(self, path):
        return self.root / path.lstrip('/')

    @property
    def image(self):
        return '/d32/b/d32-disc2/' + IMAGE_NAME

    def state(self):
        path = self.node('/var/db/d32/fetch/state.json')
        return json.loads(path.read_text(encoding='ascii')) if path.exists() else None

    def report(self):
        raw = self.node('/var/db/d32/import/report.json').read_bytes()
        self.assertEqual(UIJSON.check(raw), [])
        return json.loads(raw.decode('ascii'))

    def window(self):
        words = UIJSON.Words(str(STRINGS))
        return dict((row[0], row[1:]) for row in UIJSON.render_report(
            str(self.node('/var/db/d32/import/report.json')), words, 1800000100, pid_check=False))

    def update_lock(self):
        lock = self.node('/var/db/d32upd/.lock')
        if not lock.exists():
            return None
        return ((lock / 'pid').read_text(encoding='ascii'),
                (lock / 'owner').read_text(encoding='ascii') if (lock / 'owner').exists() else None)

    def remounts(self):
        return [call[1:] for call in self.system.calls if call[:2] == ('mount', '-u')]

    def c6(self):
        return [call[-1][4:] for call in self.system.calls if call[0] == 'd32-c6']

    def complete_download(self):
        self.assertEqual(self.fetch('--mirror'), 0, self.out)
        return self.state()


# ---------------------------------------------------------------------------------------------- the command line

class CommandLine(_Fixture):
    def test_argument_rules(self):
        good = {(): 'screens', ('--mirror',): 'mirror', ('--attach',): 'attach', ('--cleanup',): 'cleanup',
                ('--status',): 'status'}
        for argv, action in good.items():
            self.assertEqual(FETCH.parse_args(list(argv))['action'], action)
        for argv, code in ((['--peer', 'workpc', '--code', CODE], CODE),
                           (['--code', 'abcde-23456', '--peer', 'workpc.tail1234.ts.net'], CODE),
                           (['--peer', 'deep32-pc', '--code', 'AOCDE 1GH8J'], 'AOCDEIGHBJ'),
                           (['--peer', 'pc', '--code', 'abcde 0fgh1'], 'ABCDEOFGHI')):
            with self.subTest(argv=argv):
                options = FETCH.parse_args(argv)
                self.assertEqual((options['action'], options['code']), ('peer', code))
        for argv in (['--peer', 'pc'], ['--code', CODE], ['--mirror', '--attach'], ['--mirror', '--mirror'],
                     ['--peer', 'pc', '--code', 'ABCDE2345'], ['--peer', 'pc', '--code', 'ABCDE234567'],
                     ['--peer', 'pc', '--code', 'ABCDE2345!'], ['--peer', 'bad name', '--code', CODE],
                     ['--peer', '-pc', '--code', CODE], ['--peer', 'pc', '--peer', 'pc'], ['--url', 'x'],
                     ['--peer', 'pc', '--code', CODE, '--mirror'], ['mirror'], ['--status', 'x'],
                     ['--peer', 'pc/../x', '--code', CODE]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                FETCH.parse_args(argv)
        self.assertEqual(self.fetch('--bogus'), 2)
        self.assertEqual(self.err, FETCH.USAGE_TEXT + '\n')
        self.assertEqual(self.system.calls, [])

    def test_urls_come_from_the_release_disc_1_carries(self):
        self.assertEqual(FETCH.mirror_url(VERSION), 'https://archive.org/download/deep32-0.1.0-alpha.2/'
                                                    'deep32-0.1.0-alpha.2-disc2-content.iso')
        self.assertEqual(FETCH.peer_url('workpc.tail1234.ts.net', CODE, VERSION),
                         'https://workpc.tail1234.ts.net:9447/d32-disc2-ABCDE23456/'
                         'deep32-0.1.0-alpha.2-disc2-content.iso')
        for args in (('workpc', CODE, VERSION), ('Work.ts.net', CODE, VERSION), ('a.ts.net', 'abcde23456', VERSION),
                     ('a.ts.net', CODE, '../x'), ('a.ts.net', CODE, '')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                FETCH.peer_url(*args)
        with self.assertRaises(ValueError):
            FETCH.mirror_url('a/b')
        self.assertEqual(FETCH.system_version(), VERSION)
        (self.root / 'etc/d32/release').write_text('0.1.0-alpha.3 seq 2\n', encoding='ascii')
        self.assertEqual(FETCH.system_version(), '0.1.0-alpha.3')
        for text in ('', '\n', '../../x\n', '-x\n'):
            (self.root / 'etc/d32/release').write_text(text, encoding='ascii')
            with self.subTest(release=text), self.assertRaises(FETCH.Stop) as caught:
                FETCH.system_version()
            self.assertEqual(caught.exception.text, 'fetch.refuse.version')

    def test_tailnet_peers_are_the_online_ones(self):
        status = {'BackendState': 'Running', 'Self': {'HostName': 'deep32-ws'}, 'Peer': {
            'k1': {'HostName': 'WorkPC', 'DNSName': 'workpc.tail1234.ts.net.', 'Online': True,
                   'TailscaleIPs': ['fd7a:115c:a1e0::1', '100.101.102.103']},
            'k2': {'HostName': 'laptop', 'DNSName': 'laptop.tail1234.ts.net.', 'Online': False,
                   'TailscaleIPs': ['100.64.0.9']},
            'k3': {'HostName': 'old', 'DNSName': 'old.tail1234.ts.net.', 'Online': True, 'Expired': True,
                   'TailscaleIPs': ['100.64.0.10']},
            'k4': {'HostName': 'noip', 'DNSName': 'noip.tail1234.ts.net.', 'Online': True,
                   'TailscaleIPs': ['192.168.1.4']},
            'k5': {'HostName': 'bad', 'DNSName': 'bad_name.tail1234.ts.net.', 'Online': True,
                   'TailscaleIPs': ['100.64.0.11']},
            'k6': {'HostName': 'deep32-forge', 'DNSName': 'deep32-forge.tail1234.ts.net.', 'Online': True,
                   'TailscaleIPs': ['100.64.0.12']}}}
        peers = FETCH.parse_peers(status)
        self.assertEqual([(p['name'], p['dns'], p['ipv4']) for p in peers],
                         [('deep32-forge', 'deep32-forge.tail1234.ts.net', '100.64.0.12'),
                          ('workpc', 'workpc.tail1234.ts.net', '100.101.102.103')])
        for name in ('workpc', 'WORKPC', 'workpc.tail1234.ts.net', 'WorkPC'):
            self.assertEqual(FETCH.pick_peer(peers, name)['ipv4'], '100.101.102.103')
        for name, text in (('laptop', 'fetch.peer.unknown'), ('x', 'fetch.peer.unknown')):
            with self.assertRaises(FETCH.Stop) as caught:
                FETCH.pick_peer(peers, name)
            self.assertEqual(caught.exception.text, text)
        for bad, text in (({'BackendState': 'NeedsLogin'}, 'fetch.peers.signin'), ([], 'fetch.peers.status'),
                          ({'BackendState': 'Running', 'Peer': []}, 'fetch.peers.status')):
            with self.assertRaises(FETCH.Stop) as caught:
                FETCH.parse_peers(bad)
            self.assertEqual(caught.exception.text, text)


# ---------------------------------------------------------------------------------------------- downloads

class Download(_Fixture):
    def test_download_that_stops_carries_on_from_where_it_stopped(self):
        # The first run's connection drops after 1.25 MB and no retry is allowed: the part is kept.
        self.server.drop_after = [1310720]
        with mock.patch.object(FETCH, 'RETRIES', 0):
            self.assertEqual(self.fetch('--mirror'), 1)
        self.assertIn('The download stopped: the connection dropped again and again.', self.out)
        part = self.node(self.image + '.part')
        self.assertEqual(part.read_bytes(), self.data[:1310720])
        state = self.state()
        self.assertEqual((state['complete'], state['size'], state['slot'], state['image']),
                         (False, len(self.data), '/d32/b', self.image))
        self.assertEqual(self.update_lock(), ('1\n', 'd32-content-fetch\n'))
        report = self.report()
        self.assertEqual((report['state'], report['outcome'], report['stage']), ('failed', 'failed', 'fetch'))
        self.assertEqual([(e['code'], e['stage'], e['args']) for e in report['errors']],
                         [('IMPORT_FETCH', 'fetch', {'reason': 'dropped'})])
        self.assertEqual(report['source']['device'], 'network')
        self.assertFalse(report['resume']['resumable'])
        window = self.window()
        self.assertEqual(window['again'], ('no',))
        self.assertEqual(window['hint'][0], 'Choose Get Disc 2 over the Network in First Start to carry on. '
                                            'What is already downloaded is kept.')
        self.assertIn('the download of disc 2 stopped: the connection dropped again and again', window['problem'][0])
        self.assertEqual(self.c6()[-1], 'the download of disc 2 stopped: get it over the network again in First '
                                        'Start, or attach disc 2')
        # The slot was writable only while the file was written, with the options it keeps.
        self.assertEqual(self.remounts(), [('-u', '-o', 'rw,nodev,nosuid,noatime', '/d32/b'),
                                           ('-u', '-o', 'ro,nodev,nosuid,noatime', '/d32/b')])
        self.assertIn('read-only', self.system.mounts['/d32/b'][2])
        # The second run asks for the rest only, and the image is whole, renamed, read-only and attached.
        self.system.calls.clear()
        self.assertEqual(self.fetch('--mirror'), 0, self.out)
        self.assertEqual(self.server.requests[-1], (self.server.path, 'bytes=1310720-'))
        self.assertEqual(self.node(self.image).read_bytes(), self.data)
        self.assertFalse(part.exists())
        state = self.state()
        self.assertEqual((state['complete'], state['unit'], state['source']), (True, 'vnd0', 'mirror'))
        self.assertEqual(self.system.units['vnd0'], self.image)
        self.assertEqual(self.node('/var/db/d32/fetch/iso').read_text(encoding='ascii'), self.image + '\n')
        self.assertIn('read-only', self.system.mounts['/d32/b'][2])
        self.assertTrue(all(call[-1] == '/d32/b' for call in self.remounts()))
        self.assertEqual(self.system.named('pfctl'), [('-t', 'd32_fetch', '-T', 'add', '127.0.0.1'),
                                                      ('-t', 'd32_fetch', '-T', 'flush')])
        self.assertEqual(self.update_lock(), ('1\n', 'd32-content-fetch\n'))
        # The report's stage fetch is done; the first start's follower is started to import it.
        report = self.report()
        fetch = report['stages'][0]
        self.assertEqual((fetch['id'], fetch['state'], fetch['done_bytes'], fetch['total_bytes']),
                         ('fetch', 'done', len(self.data), len(self.data)))
        self.assertEqual(report['progress']['done_bytes'], len(self.data))
        self.assertEqual(self.window()['state'], ('Disc 2 is downloaded. The import starts in a moment.',))
        self.assertEqual(self.system.spawned, [('d32-firstboot', 'resume')])
        self.assertIn('Carrying on from 1 MB.', self.out)
        self.assertIn('It is attached to this Workstation as vnd0.', self.out)
        self.assertEqual(self.c6()[-1], 'disc 2 is downloaded and attached; the import starts in a moment')
        # Nothing is ever written to the active slot.
        self.assertEqual(list(self.node('/d32/a').iterdir()), [])

    def test_a_dropped_connection_is_retried_in_the_same_run(self):
        self.server.drop_after = [700000, 900000]
        self.assertEqual(self.fetch('--mirror'), 0, self.out)
        self.assertEqual([r[1] for r in self.server.requests], ['bytes=0-', 'bytes=700000-', 'bytes=1600000-'])
        self.assertEqual(self.node(self.image).read_bytes(), self.data)

    def test_a_source_without_range_sends_it_whole_again(self):
        self.server.drop_after = [500000]
        with mock.patch.object(FETCH, 'RETRIES', 0):
            self.assertEqual(self.fetch('--mirror'), 1)
        self.server.ignore_range = True
        self.assertEqual(self.fetch('--mirror'), 0, self.out)
        self.assertEqual(self.server.requests[-1][1], 'bytes=500000-')
        self.assertEqual(self.node(self.image).read_bytes(), self.data)
        self.assertEqual(self.report()['stages'][0]['done_bytes'], len(self.data))

    def test_a_tailnet_pc_is_asked_for_its_code_path(self):
        self.system.status['Peer'] = {'k': {'HostName': 'workpc', 'DNSName': 'workpc.tail1234.ts.net.',
                                            'Online': True, 'TailscaleIPs': ['100.101.102.103']}}
        self.server.path = '/d32-disc2-%s/%s' % (CODE, IMAGE_NAME)
        self.assertEqual(self.fetch('--peer', 'workpc', '--code', 'abcde-23456'), 0, self.out)
        self.assertEqual(self.server.requests, [(self.server.path, 'bytes=0-')])
        self.assertEqual(self.system.named('pfctl')[0], ('-t', 'd32_fetch', '-T', 'add', '100.101.102.103'))
        self.assertEqual(self.state()['source'], 'peer')
        self.assertIn('Downloading disc 2 from workpc.', self.out)
        # The report never names the PC or its tailnet address.
        raw = self.node('/var/db/d32/import/report.json').read_text(encoding='ascii')
        self.assertNotIn('workpc', raw)
        self.assertNotIn('100.101', raw)

    def test_a_wrong_code_finds_nothing_and_keeps_nothing(self):
        self.system.status['Peer'] = {'k': {'HostName': 'pc', 'DNSName': 'pc.tail1234.ts.net.', 'Online': True,
                                            'TailscaleIPs': ['100.64.1.2']}}
        self.assertEqual(self.fetch('--peer', 'pc', '--code', 'ZZZZZ77777'), 1)
        self.assertIn('the source has no disc 2 for this Workstation at that address', self.out)
        self.assertIsNone(self.state())
        self.assertIsNone(self.update_lock())
        self.assertFalse(self.node('/d32/b/d32-disc2').exists())
        self.assertEqual(self.remounts(), [])

    def test_a_file_that_is_not_a_disc_image_is_not_kept(self):
        self.server.data = disc_image(disc=False)
        self.assertEqual(self.fetch('--mirror'), 1)
        self.assertIn('the downloaded file is not a disc image', self.out)
        self.assertFalse(self.node(self.image + '.part').exists())
        self.assertFalse(self.node(self.image).exists())
        self.assertIsNone(self.update_lock())
        self.assertEqual(self.report()['errors'][0]['args'], {'reason': 'not-disc'})
        self.assertEqual(self.system.named('vnconfig'), [])

    def test_the_screens_pick_a_pc_take_its_code_and_download(self):
        self.system.status['Peer'] = {'k': {'HostName': 'workpc', 'DNSName': 'workpc.tail1234.ts.net.',
                                            'Online': True, 'TailscaleIPs': ['100.101.102.103']}}
        self.server.path = '/d32-disc2-%s/%s' % (CODE, IMAGE_NAME)
        typed = 'X\nT\n2\n1\nabcde 2345\nabcde-23456\nS\nQ\n\n'
        with mock.patch.object(sys, 'stdin', io.StringIO(typed)):
            self.assertEqual(self.fetch(), 0)
        for text in ('Deep32/UX: disc 2 0.1.0-alpha.2 over the network', '  T  From a PC on your tailnet (it shows '
                     'a code)', 'Choose T, M, S, D or Q.', '  1  workpc', 'That is not a code',
                     'Downloading disc 2 from workpc.', 'It is attached to this Workstation as vnd0.',
                     'Disc 2 is downloaded (3 MB). It is kept until its import has finished.', 'Press Return to close.'):
            self.assertIn(text, self.out)
        self.assertEqual(self.node(self.image).read_bytes(), self.data)
        # D deletes it after a yes
        with mock.patch.object(sys, 'stdin', io.StringIO('D\nN\nD\nY\nQ\n\n')):
            self.assertEqual(self.fetch(), 0)
        self.assertIn('The downloaded disc 2 is deleted and the update area is free again.', self.out)
        self.assertFalse(self.node(self.image).exists())

    def test_after_the_first_start_the_import_runs_here_and_the_image_goes(self):
        (self.root / 'var/db/d32/firstboot').mkdir(parents=True)
        (self.root / 'var/db/d32/firstboot/done').write_text('x\n', encoding='ascii')
        self.assertEqual(self.fetch('--mirror'), 0, self.out)
        self.assertEqual(self.system.spawned, [('d32-content-import', '--device', 'vnd0')])
        self.assertFalse(self.node(self.image).exists())
        self.assertIsNone(self.state())
        self.assertIsNone(self.update_lock())
        self.assertEqual(self.c6(), [])          # the console line belongs to the first start only
        self.assertIn('Disc 2 is imported.', self.out)


# ---------------------------------------------------------------------------------------------- refusals

class Refusals(_Fixture):
    def test_too_little_space_is_refused_before_a_byte_is_written(self):
        self.assertEqual(self.fetch('--mirror', D32_TEST_FREE_BYTES=str(len(self.data) + (1 << 30) - 1)), 4)
        self.assertIn('There is not enough free space on the update area: 1.0 GB needed, 1.0 GB free.', self.out)
        self.assertFalse(self.node('/d32/b/d32-disc2').exists())
        self.assertEqual(self.remounts(), [])
        self.assertIsNone(self.update_lock())
        self.assertIsNone(self.state())
        report = self.report()
        self.assertEqual([(e['code'], e['stage']) for e in report['errors']], [('IMPORT_SPACE', 'fetch')])
        self.assertEqual(self.window()['hint'][0], UIJSON.Words(str(STRINGS))('hint.fetch.space'))

    def test_an_update_staged_or_half_done_is_refused(self):
        for name in ('pending', 'pending.running'):
            with self.subTest(name=name):
                marker = self.node('/d32/var/' + name)
                marker.write_text('apply u3\n', encoding='ascii')
                self.assertEqual(self.fetch('--mirror'), 4)
                self.assertIn('An update waits for the next start of this Workstation', self.out)
                self.assertEqual(self.server.requests, [])
                self.assertIsNone(self.update_lock())
                self.assertFalse(self.node('/d32/b/d32-disc2').exists())
                marker.unlink()

    def test_a_running_update_is_refused_and_a_stale_lock_taken_over(self):
        lock = self.node('/var/db/d32upd/.lock')
        lock.mkdir(parents=True)
        (lock / 'pid').write_text('4242\n', encoding='ascii')
        self.assertEqual(self.fetch('--mirror', D32_TEST_LIVE_PIDS='4242'), 4)
        self.assertIn('An update is running.', self.out)
        self.assertEqual(self.update_lock(), ('4242\n', None))
        self.assertEqual(self.server.requests, [])
        # d32upd's own rule: a lock whose process is gone is taken over
        self.assertEqual(self.fetch('--mirror', D32_TEST_LIVE_PIDS=''), 0, self.out)
        self.assertEqual(self.update_lock(), ('1\n', 'd32-content-fetch\n'))

    def test_a_path_on_the_active_slot_is_never_touched(self):
        self.complete_download()
        # The slots flip (b is active now): the record names a file on the active slot.
        self.active = 'b'
        self.system.calls.clear()
        for verb in ('--attach', '--cleanup'):
            with self.subTest(verb=verb):
                self.assertEqual(self.fetch(verb), 4)
                self.assertIn('a file in its place is not one this program made', self.out)
        self.assertEqual(self.node(self.image).read_bytes(), self.data)
        self.assertEqual(self.remounts(), [])
        self.assertFalse([call for call in self.system.named('vnconfig') if call[0] != '-l'])
        self.assertEqual(self.system.units['vnd0'], self.image)
        with self.assertRaises(FETCH.Stop):
            FETCH.check_path('/d32/a/d32-disc2/' + IMAGE_NAME, '/d32/a', '/d32/b')
        for path in ('/d32/b/d32-disc2/../../a/x.iso', '/d32/b/' + IMAGE_NAME, '/d32/b/d32-disc2/other.iso',
                     '/d32/active/d32-disc2/' + IMAGE_NAME, '/d32/b/d32-disc2/sub/' + IMAGE_NAME):
            with self.subTest(path=path), self.assertRaises(FETCH.Stop):
                FETCH.check_path(path, '/d32/a', '/d32/b')

    def test_a_slot_that_is_not_its_own_file_system_is_refused(self):
        for name, change in (('not mounted', lambda m: m.pop('/d32/b')),
                             ('same device', lambda m: m['/d32/b'].__setitem__(0, '/dev/sd0d')),
                             ('not ffs', lambda m: m['/d32/b'].__setitem__(1, 'msdos'))):
            with self.subTest(case=name):
                self.system = System()
                change(self.system.mounts)
                with mock.patch.object(FETCH, 'run', self.system.run):
                    self.assertEqual(self.fetch('--mirror'), 4)
                self.assertIn('the spare system slot is not a file system of its own', self.out)
                self.assertEqual(self.server.requests, [])
        for target in ('c', '/d32/active', '../a'):
            self.active = target
            self.assertEqual(self.fetch('--mirror'), 4)
            self.assertIn('this Workstation does not say which system slot is in use', self.out)

    def test_another_import_running_is_busy(self):
        lock = self.node('/var/db/d32/import/journal/lock')
        lock.mkdir(parents=True)
        (lock / 'pid').write_text('5151\n', encoding='ascii')
        self.assertEqual(self.fetch('--mirror', D32_TEST_LIVE_PIDS='5151'), 5)
        self.assertEqual(self.server.requests, [])
        self.assertIsNone(self.update_lock())


# ---------------------------------------------------------------------------------------------- cleanup and restart

class Cleanup(_Fixture):
    def test_cleanup_detaches_deletes_and_gives_the_update_area_back(self):
        self.complete_download()
        self.system.mounts['/var/db/d32/import/mnt'] = ['/dev/vnd0c', 'cd9660', ['local', 'read-only']]
        self.system.calls.clear()
        self.assertEqual(self.fetch('--cleanup'), 0, self.out)
        self.assertEqual(self.system.named('umount'), [('/var/db/d32/import/mnt',)])
        self.assertIn(('-u', 'vnd0'), self.system.named('vnconfig'))
        order = [call[:3] for call in self.system.calls if call[0] in ('umount', 'vnconfig', 'mount') and
                 call[1:] not in ((), ('-l',))]
        self.assertEqual(order, [('umount', '/var/db/d32/import/mnt'), ('vnconfig', '-u', 'vnd0'),
                                 ('mount', '-u', '-o'), ('mount', '-u', '-o')])
        self.assertEqual(self.remounts(), [('-u', '-o', 'rw,nodev,nosuid,noatime', '/d32/b'),
                                           ('-u', '-o', 'ro,nodev,nosuid,noatime', '/d32/b')])
        self.assertFalse(self.node('/d32/b/d32-disc2').exists())
        self.assertIsNone(self.state())
        self.assertFalse(self.node('/var/db/d32/fetch/iso').exists())
        self.assertIsNone(self.update_lock())
        self.assertIn(('-t', 'd32_fetch', '-T', 'flush'), self.system.named('pfctl'))
        self.assertIn('The downloaded disc 2 is deleted', self.out)
        self.assertEqual(self.fetch('--cleanup'), 0)
        self.assertIn('No disc 2 is downloaded on this Workstation.', self.out)

    def test_cleanup_removes_a_part_and_releases_the_lock(self):
        self.server.drop_after = [400000]
        with mock.patch.object(FETCH, 'RETRIES', 0):
            self.fetch('--mirror')
        self.assertTrue(self.node(self.image + '.part').exists())
        self.assertEqual(self.fetch('--cleanup'), 0, self.out)
        self.assertFalse(self.node('/d32/b/d32-disc2').exists())
        self.assertIsNone(self.update_lock())

    def test_cleanup_waits_for_an_import(self):
        self.complete_download()
        lock = self.node('/var/db/d32/import/journal/lock')
        lock.mkdir(parents=True)
        (lock / 'pid').write_text('5151\n', encoding='ascii')
        self.assertEqual(self.fetch('--cleanup', D32_TEST_LIVE_PIDS='5151'), 5)
        self.assertTrue(self.node(self.image).exists())
        self.assertEqual(self.system.units['vnd0'], self.image)

    def test_after_a_restart_the_image_is_attached_again(self):
        self.complete_download()
        self.system.units = {'vnd%d' % n: None for n in range(4)}
        self.system.calls.clear()
        self.assertEqual(self.fetch('--attach'), 0, self.out)
        self.assertIn(('vnd0', self.image), self.system.named('vnconfig'))
        self.assertEqual(self.system.units['vnd0'], self.image)
        self.assertEqual(self.remounts(), [])
        self.assertEqual(self.update_lock(), ('1\n', 'd32-content-fetch\n'))
        # attached already: nothing changes
        self.system.calls.clear()
        self.assertEqual(self.fetch('--attach'), 0)
        self.assertEqual(self.system.named('vnconfig'), [('-l',)])
        # no record: nothing at all
        self.fetch('--cleanup')
        self.system.calls.clear()
        self.assertEqual(self.fetch('--attach'), 0)
        self.assertEqual(self.system.calls, [])

    def test_once_its_import_has_finished_the_image_is_removed_at_the_next_start(self):
        self.complete_download()
        report = json.loads(self.node('/var/db/d32/import/report.json').read_text(encoding='ascii'))
        report.update(state='done', outcome='imported')
        report['source']['device'] = 'vnd0'
        self.node('/var/db/d32/import/report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='ascii')
        self.assertEqual(self.fetch('--attach'), 0)
        self.assertFalse(self.node(self.image).exists())
        self.assertIsNone(self.update_lock())

    def test_status_lines(self):
        self.assertEqual(self.fetch('--status'), 0)
        self.assertEqual(self.out, 'state=none\n')
        self.complete_download()
        self.fetch('--status')
        self.assertEqual(self.out.splitlines(), ['state=attached', 'version=' + VERSION, 'source=mirror',
                                                 'slot=/d32/b', 'done=%d' % len(self.data),
                                                 'size=%d' % len(self.data), 'unit=vnd0', 'lock=ours'])


# ---------------------------------------------------------------------------------------------- the vnd device

class VndDevice(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='content-fetch-vnd-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.dict(os.environ, {'D32_ROOT': str(self.root), 'D32_TEST_CLOCK': '1800000000',
                                             'D32_TEST_RAND': '0123abcd', 'D32_STUBS': str(self.root / 'stubs')})
        patch.start()
        self.addCleanup(patch.stop)
        for module, name in ((imp_paths, '_HOOKS'), (imp_paths, '_CLOCK_READS')):
            patch = mock.patch.object(module, name, None if name == '_HOOKS' else 0)
            patch.start()
            self.addCleanup(patch.stop)
        self.paths = imp_paths.workstation()

    def test_importer_and_report_window_take_a_vnd_unit(self):
        for device in ('vnd0', 'vnd9'):
            self.assertEqual(d32import.parse_args(['--node', 'workstation', '--device', device])['device'], device)
        for device in ('vnd', 'vnd10', 'vnd0c', 'rvnd0', 'network'):
            with self.subTest(device=device), self.assertRaises(d32import.UsageError):
                d32import.parse_args(['--node', 'workstation', '--device', device])
        with self.assertRaises(d32import.UsageError):
            d32import.parse_args(['--node', 'forge', '--device', 'vnd0'])
        report = imp_report.Report(self.paths, '20270115T080000Z-0123abcd', 'vnd0')
        report.end_stage('fetch', state='skipped', write=False)
        report.begin_stage('disc')
        raw = self.paths.report.read_bytes()
        self.assertEqual(UIJSON.check(raw), [])
        for device, good in (('vnd0', True), ('vnd9', True), ('network', True), ('sd1', True), ('vnd10', False),
                             ('vnd', False), ('vnd0c', False), ('net', False)):
            with self.subTest(window=device):
                value = json.loads(raw)
                value['source']['device'] = device
                problems = UIJSON.check((json.dumps(value, indent=2) + '\n').encode('ascii'))
                self.assertEqual(problems == [], good, problems)

    def test_imp_disc_mounts_the_whole_vnd_as_iso9660(self):
        mount = self.paths.mount
        for name in ('kb', 'corpus', 'library'):
            (mount / name).mkdir(parents=True, exist_ok=True)
        (mount / 'MANIFEST.json').write_bytes(b'{}\n')
        (mount / 'MANIFEST.json.asc').write_bytes(b'synthetic signature\n')
        self.paths.journal.mkdir(parents=True, exist_ok=True)
        for label in ('label: fictitious\n  c:  100  0  unused\n',
                      'label: DEEP32_CONTENT\n  a:  100  0  ISO9660\n  c:  100  0  ISO9660\n'):
            with self.subTest(label=label):
                commands = []

                def command(name, args, timeout=600):
                    commands.append((name, list(args)))
                    return subprocess.CompletedProcess([name], 0, label if name == 'disklabel' else '', '')
                ctx = SimpleNamespace(p=self.paths, options={'device': 'vnd0'}, journal=SimpleNamespace(data={}),
                                      command=command)
                (self.paths.journal / 'mounted').unlink(missing_ok=True)
                disc = imp_disc.run_disc(ctx)
                self.assertEqual(commands, [('disklabel', ['vnd0']),
                                            ('mount', ['-t', 'cd9660', '-o', 'ro', '/dev/vnd0c', str(mount)])])
                self.assertEqual((disc.device, disc.mounted), ('vnd0', True))

    def test_the_first_start_finds_only_the_downloaded_image(self):
        if not SHELL.is_file():
            self.skipTest('no shell for the probe: ' + str(SHELL))
        probe = load('content_fetch_probe', ROOT / 'tests/workstation/test_firstboot_disc2_probe.py')
        source = (D32 / 'sbin/d32-firstboot').read_text(encoding='ascii')
        functions = ''.join(probe.function(source, name) for name in
                            ('_d32f_disc2_devices', '_d32f_disc2_mount', '_d32f_probe'))
        image = '/d32/b/d32-disc2/' + IMAGE_NAME
        stubs = probe.STUBS + ('vnconfig() { echo "vnconfig $*" >>"$LOG"; [ "$*" = -l ] && cat "$FIX/vnd"; }\n')
        fix = self.root / 'fix'
        (fix / 'media/vnd1c/kb').mkdir(parents=True)
        for name in ('corpus', 'library'):
            (fix / 'media/vnd1c' / name).mkdir()
        for name in ('MANIFEST.json', 'MANIFEST.json.asc'):
            (fix / 'media/vnd1c' / name).write_bytes(b'synthetic\n')
        (fix / 'disknames').write_text('sd0:0123456789abcdef,vnd0:,vnd1:,vnd2:\n', encoding='ascii', newline='\n')
        (fix / 'mounts').write_text(probe.ROOT_SD0, encoding='ascii', newline='\n')
        (fix / 'label.sd0').write_text(probe.LABEL_SYSTEM, encoding='ascii', newline='\n')
        (fix / 'vnd').write_text('vnd0: covering /home/user/other.iso on sd0k, inode 4\nvnd1: covering %s on sd0e, '
                                 'inode 7\nvnd2: not in use\nvnd3: covering %s.part on sd0e, inode 8\n'
                                 % (image, image), encoding='ascii', newline='\n')

        def run(action, record):
            case = self.root / 'case'
            if case.exists():
                shutil.rmtree(case)
            shutil.copytree(fix, case / 'fix')
            if record:
                (case / 'var/db/d32/fetch').mkdir(parents=True)
                (case / 'var/db/d32/fetch/iso').write_text(image + '\n', encoding='ascii', newline='\n')
            script = case / 'case.sh'
            script.write_text('\n'.join([
                'D32_ROOT=' + probe.quote(probe.shell_path(case)), 'D32_FB_DIR="$D32_ROOT/fb"', 'FIX="$D32_ROOT/fix"',
                'LOG="$D32_ROOT/log"', ': >"$LOG"', '. ' + probe.quote(probe.shell_path(D32 / 'libexec/d32-settings.sh')),
                stubs, functions, {'devices': '_d32f_disc2_devices',
                                   'probe': '_d32f_probe; echo "rc=$? device=$D32_FB_DEVICE"'}[action]]) + '\n',
                encoding='ascii', newline='\n')
            args = [str(SHELL), '--noprofile', '--norc', probe.shell_path(script)] if WINDOWS else [str(SHELL), str(script)]
            result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.stderr, '')
            log = [line.replace(probe.shell_path(case), 'ROOT')
                   for line in (case / 'log').read_text(encoding='ascii').splitlines()]
            return result.stdout.split(), log
        devices, log = run('devices', record=True)
        self.assertEqual(devices, ['vnd1'])
        devices, log = run('devices', record=False)
        self.assertEqual(devices, [])
        self.assertNotIn('vnconfig -l', log)
        out, log = run('probe', record=True)
        self.assertEqual(out, ['rc=0', 'device=vnd1'])
        self.assertEqual([line for line in log if line.startswith('mount ')],
                         ['mount -t cd9660 -o ro ROOT/dev/vnd1c ROOT/fb/probe'])


# ---------------------------------------------------------------------------------------------- the report's stages

class ReportStage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='content-fetch-report-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.dict(os.environ, {'D32_ROOT': self.temp.name, 'D32_TEST_CLOCK': '1800000000',
                                             'D32_TEST_RAND': '0123abcd', 'D32_STUBS': self.temp.name})
        patch.start()
        self.addCleanup(patch.stop)
        for name, value in (('_HOOKS', None), ('_CLOCK_READS', 0)):
            patch = mock.patch.object(imp_paths, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.paths = imp_paths.workstation()

    def check(self, value):
        return UIJSON.check((json.dumps(value, indent=2) + '\n').encode('ascii'))

    def test_fetch_leads_the_stages_and_is_validated(self):
        self.assertEqual(imp_report.STAGES[0], 'fetch')
        self.assertEqual(d32import.STAGES, imp_report.STAGES)
        self.assertEqual(UIJSON.STAGES, imp_report.STAGES)
        report = imp_report.Report(self.paths, '20270115T080000Z-0123abcd', 'network')
        report.data['resume']['resumable'] = False
        report.begin_stage('fetch', total_bytes=100)
        good = json.loads(self.paths.report.read_bytes())
        self.assertEqual(self.check(good), [])
        # an import whose fetch stage was not settled before disc ran
        bad = json.loads(json.dumps(good))
        bad['stages'][0]['state'] = 'pending'
        bad['stages'][1]['state'] = 'running'
        bad['stage'] = 'disc'
        self.assertIn('stages before the running one are done or skipped', self.check(bad))
        # out of order
        bad = json.loads(json.dumps(good))
        bad['stages'][0], bad['stages'][1] = bad['stages'][1], bad['stages'][0]
        self.assertIn('stages: every stage, in order', self.check(bad))
        bad['stages'] = bad['stages'][1:] + bad['stages'][:1]
        self.assertTrue(any('the contract has' in fault for fault in self.check(bad)), self.check(bad))
        # a report written before stage fetch existed still reads; one with fetch in another place does not
        legacy = json.loads(json.dumps(good))
        legacy['stages'] = legacy['stages'][1:]
        legacy['stages'][0]['state'] = 'running'
        legacy['stage'] = legacy['resume']['next_stage'] = 'disc'
        self.assertEqual(self.check(legacy), [])
        legacy['stage'] = 'fetch'
        self.assertIn('stage', self.check(legacy))
        # the reasons a download stops for are a closed set
        report.add_error('IMPORT_FETCH', 'fetch', args={'reason': 'dropped'})
        report.finish('failed', resumable=False)
        failed = json.loads(self.paths.report.read_bytes())
        self.assertEqual(self.check(failed), [])
        for reason in UIJSON.FETCH_REASONS:
            failed['errors'][0]['args']['reason'] = reason
            self.assertEqual(self.check(failed), [], reason)
        failed['errors'][0]['args']['reason'] = 'elsewhere'
        self.assertIn('errors[0].args.reason is not a reason a download stops for', self.check(failed))

    def test_the_import_records_the_download_as_its_first_stage(self):
        sys.path.insert(0, str(ROOT / 'tests/workstation'))
        self.addCleanup(sys.path.remove, str(ROOT / 'tests/workstation'))
        single = load('content_fetch_single_pass', ROOT / 'tests/workstation/test_importer_single_pass.py')

        class Import(single._Harness):
            def runTest(self):
                pass
        for device, record, state in (('vnd0', True, 'done'), ('cd0', True, 'skipped'), ('vnd0', False, 'skipped'),
                                      ('vnd1', True, 'skipped')):
            with self.subTest(device=device, record=record):
                harness = Import()
                harness.setUp()
                try:
                    if record:
                        folder = harness.root / 'var/db/d32/fetch'
                        folder.mkdir(parents=True)
                        (folder / 'state.json').write_text(json.dumps({
                            'format': 'deep32-content-fetch', 'format_version': 1, 'version': VERSION,
                            'slot': '/d32/b', 'image': '/d32/b/d32-disc2/' + IMAGE_NAME, 'size': 4096,
                            'complete': True, 'source': 'mirror', 'unit': 'vnd0',
                            'started': '2027-01-15T07:00:00Z', 'finished': '2027-01-15T07:30:00Z'}, indent=2) + '\n',
                            encoding='ascii')
                    result, _ = harness.run_import('--device', device)
                    self.assertEqual(result, 0)
                    report = harness.assert_installed()
                    row = report['stages'][0]
                    self.assertEqual((row['id'], row['state']), ('fetch', state))
                    if state == 'done':
                        self.assertEqual((row['started'], row['finished'], row['total_bytes']),
                                         ('2027-01-15T07:00:00Z', '2027-01-15T07:30:00Z', 4096))
                    self.assertEqual(report['source']['device'], device)
                finally:
                    harness.doCleanups()


# ---------------------------------------------------------------------------------------------- First Start and configuration

class FirstStartAndConfiguration(unittest.TestCase):
    def model(self, line, transport='tailnet'):
        script = ('. ' + _shell_quote(_shell_path(D32 / 'libexec/d32-ui-lib.sh')) + '\n'
                  'd32ui_setup_model "$1" "$2"\n'
                  'printf "net=%s\\nhead=%s\\ntext1=%s\\ntext2=%s\\n" "$D32UI_NET" "$D32UI_HEAD" "$D32UI_TEXT1" '
                  '"$D32UI_TEXT2"\n')
        env = dict(os.environ, D32UI_STRINGS=_shell_path(STRINGS))
        env.pop('D32_ROOT', None)
        result = subprocess.run([str(SHELL), '-c', script, 'model', line + '\ndone=no', transport], env=env,
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.stderr, '')
        return dict(row.split('=', 1) for row in result.stdout.splitlines())

    def test_first_start_offers_the_network_while_it_waits_for_disc_2(self):
        if not SHELL.is_file():
            self.skipTest('no shell: ' + str(SHELL))
        words = UIJSON.Words(str(STRINGS))
        waiting = ('D32S/1 9 workstation boot=0123abcd stage=content state=waiting msg=attach disc 2 (a disc or a '
                   'USB stick), or get it over the network in First Start')
        model = self.model(waiting)
        self.assertEqual((model['net'], model['text2']), ('yes', words('setup.content.net')))
        self.assertEqual(model['head'], words('setup.content.attach'))
        self.assertEqual(self.model(waiting, 'loopback')['net'], '')
        stopped = ('D32S/1 9 workstation boot=0123abcd stage=content state=waiting msg=the download of disc 2 '
                   'stopped: get it over the network again in First Start, or attach disc 2')
        self.assertEqual(self.model(stopped)['text2'], words('setup.content.net.stopped'))
        running = ('D32S/1 10 workstation boot=0123abcd stage=content state=running msg=downloading disc 2: 3.1 GB '
                   'of 19.6 GB (15 %), about 12 min left')
        model = self.model(running)
        self.assertEqual((model['net'], model['head'], model['text1']),
                         ('', words('setup.content.fetch'), 'downloading disc 2: 3.1 GB of 19.6 GB (15 %), about 12 min '
                                                            'left'))
        found = 'D32S/1 11 workstation boot=0123abcd stage=content state=done msg=disc 2 found on vnd0'
        self.assertEqual(self.model(found)['net'], '')

    def test_strings_wiring_and_configuration(self):
        table = dict(line.split('\t', 1) for line in STRINGS.read_text(encoding='ascii').splitlines()
                     if '\t' in line and not line.startswith('#'))
        tool = (D32 / 'sbin/d32-content-fetch').read_text(encoding='ascii')
        used = set(re.findall(r"'((?:fetch|get)\.[a-z0-9.-]+)'", tool)) - {'fetch.from.', 'fetch.why.'}
        used |= {'fetch.from.peer', 'fetch.from.mirror', 'get.title', 'get.root', 'btn.fetch', 'report.stage.fetch',
                 'why.IMPORT_FETCH', 'hint.fetch', 'hint.fetch.space', 'setup.content.net',
                 'setup.content.net.stopped', 'setup.content.fetch', 'report.state.fetching',
                 'report.state.fetch.stopped', 'report.state.fetched'}
        used |= {'fetch.why.' + reason for reason in UIJSON.FETCH_REASONS}
        self.assertEqual(sorted(key for key in used if key not in table), [])
        for key in used:
            self.assertNotIn('!', table[key], key)
            self.assertNotIn('\\', table[key], key)
        # the C6 lines fit the console contract (printable, under 240 characters with numbers filled in)
        for key in ('fetch.c6.progress', 'fetch.c6.progress.eta', 'fetch.c6.done', 'fetch.c6.stopped'):
            line = table[key].replace('{done}', '19.6 GB').replace('{total}', '19.6 GB').replace('{pct}', '99') \
                .replace('{left}', '12 h 59 min')
            self.assertTrue(line.isprintable() and len(line) <= 240, key)
        setup = (D32 / 'libexec/d32-setup').read_text(encoding='ascii')
        self.assertIn('-e $R/usr/local/d32/libexec/d32-content-get &', setup)
        getter = (D32 / 'libexec/d32-content-get').read_text(encoding='ascii')
        self.assertIn('$DOAS $R/usr/local/d32/sbin/d32-content-fetch\n', getter)
        doas = (ROOT / 'src/workstation/ship/u4/etc/doas.conf').read_text(encoding='ascii')
        self.assertIn('\npermit @D32_USER@ as root cmd /usr/local/d32/sbin/d32-content-fetch args\n', doas)
        self.assertNotRegex(doas, r'nopass[^\n]*d32-content-fetch')
        pf = (ROOT / 'src/workstation/ship/u4/etc/pf.conf').read_text(encoding='ascii')
        self.assertIn('table <d32_fetch>      persist ', pf)
        rules = [line for line in pf.splitlines() if '<d32_fetch>' in line and line.startswith('pass')]
        self.assertEqual(rules, [
            'pass out quick on tailnet proto tcp to <d32_fetch>      port 9447 user root label "fetch"',
            'pass out quick on egress  proto tcp to <d32_fetch>      port 443  user root label "fetch"'])
        unbound = (SITE / 'var/unbound/etc/unbound.conf').read_text(encoding='ascii')
        self.assertIn('\tlocal-zone: "archive.org." transparent\n', unbound)
        mksite = (ROOT / 'src/workstation/ship/tools/mksite-ship.sh').read_text(encoding='ascii')
        self.assertIn(' d32-content-get ', mksite)
        self.assertEqual(FETCH.PORT, 9447)
        self.assertEqual(FETCH.TABLE, 'd32_fetch')


def _shell_path(path):
    value = Path(path).resolve().as_posix()
    return '/' + value[0].lower() + value[2:] if WINDOWS else value


def _shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


if __name__ == '__main__':
    unittest.main(verbosity=2)
