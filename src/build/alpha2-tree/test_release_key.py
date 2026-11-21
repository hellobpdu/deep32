"""Offline release-key mapping and site-permission regression tests."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location('release_tree', HERE / 'make-alpha2-tree.py')
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


class MemoryGit(tool.GitSource):
    commit, published = 'b' * 40, 'a' * 40

    def __init__(self, key=b'synthetic public key one\n', mode='100644', collision=False):
        self.contents = {}
        self.trees = {}
        for revision in (self.published, self.commit):
            files = {'src/workstation/kept': (b'kept\n', '100644'),
                     'src/model-bridge/d32_model_bridge.py': (b'# bridge\n', '100644')}
            files.update({'src/' + name: (b'# diagnostic\n', '100644')
                          for name in tool.REQUIRED_DIAGNOSTICS})
            files.update({name: (key if name.endswith('.gpg') else b'A' * 40 + b'\n', mode)
                          for name in tool.RELEASE_FILES})
            files.update({name: (b'license or notice text\n', '100644') for name in tool.LICENSE_FILES})
            if collision:
                files['src/workstation/' + tool.RELEASE_FILES['install/release-key.gpg']] = (b'duplicate', '100644')
            records = []
            for name, (content, file_mode) in sorted(files.items()):
                oid = hashlib.sha256(content).hexdigest()
                self.contents[oid] = content
                records.append(f'{file_mode} blob {oid}\t{name}'.encode())
            self.trees[revision] = b'\0'.join(records) + b'\0'

    def run(self, *args):
        if args[0] != 'ls-tree':
            raise AssertionError(args)
        for name in tool.RELEASE_FILES:
            assert name in args
        return self.trees[args[3]]

    def blob(self, row):
        return self.contents[row['oid']]

    def reference_names(self):
        return set()


class ReleaseKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='release-key-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def build(self, source, name):
        data = b'kept\n'
        archive = self.work / 'input.tar'
        with tarfile.open(archive, 'w') as output:
            member = tarfile.TarInfo('kept')
            member.mode, member.size = 0o644, len(data)
            output.addfile(member, io.BytesIO(data))
        pin = self.work / 'input.PIN.sha256'
        pin.write_bytes(tool.pin_bytes({'kept': hashlib.sha256(data).hexdigest()}))
        return tool.build(archive, pin, source, self.work / name, deletions={})

    def test_committed_keys_map_to_site_with_read_only_mode(self):
        source = MemoryGit()
        mapped = source.mapped(source.commit)
        for name, target in tool.RELEASE_FILES.items():
            self.assertEqual(mapped[target]['source'], name)
            self.assertEqual(mapped[target]['mode'], 0o444)
        report = self.build(source, 'first')
        with tarfile.open(self.work / 'first' / report['tree_archive']) as archive:
            for name, target in tool.RELEASE_FILES.items():
                entry = archive.getmember(target)
                self.assertEqual(entry.mode, 0o444)
                self.assertEqual((entry.uid, entry.gid), (0, 0))
                self.assertEqual(archive.extractfile(entry).read(), source.blob(mapped[target]))

    def test_changed_committed_key_changes_tree_hash(self):
        one = self.build(MemoryGit(), 'one')
        two = self.build(MemoryGit(b'synthetic public key two\n'), 'two')
        self.assertNotEqual(one['tree_hash'], two['tree_hash'])
        self.assertNotEqual((self.work/'one'/'PIN.sha256').read_bytes(),
                            (self.work/'two'/'PIN.sha256').read_bytes())

    def test_key_links_are_refused(self):
        with self.assertRaisesRegex(tool.Refusal, 'not a regular file'):
            MemoryGit(mode='120000').mapped('a' * 40)

    def test_duplicate_key_destination_is_refused(self):
        with self.assertRaisesRegex(tool.Refusal, 'duplicate mapped'):
            MemoryGit(collision=True).mapped('a' * 40)

    def test_site_staging_applies_read_only_after_blanket_mode(self):
        stage = (ROOT/'src/workstation/ship/tools/mksite-ship.sh').read_text()
        # Execute the actual normalization lines, replacing only ownership with
        # a recorder because this suite is unprivileged and also runs on Windows.
        own = re.search(r'^chown -R root:wheel \$st$', stage, re.M)[0]
        blanket = re.search(r'^find \$st -type f -exec chmod 0644 \{\} \+$', stage, re.M)[0]
        fixed = re.search(r'^chmod 0444 "\$st/usr/local/share/d32/release/release-key.gpg" \\\n\t"\$st/usr/local/share/d32/release/release-key.fingerprint" \\\n\t"\$st/etc/d32/content-compatible-versions"$', stage, re.M)[0]
        self.assertLess(stage.index(own), stage.index(blanket))
        self.assertLess(stage.index(blanket), stage.index(fixed))
        files = ['usr/local/share/d32/release/release-key.gpg',
                 'usr/local/share/d32/release/release-key.fingerprint',
                 'etc/d32/content-compatible-versions']
        for name in files:
            p = self.work/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'synthetic fixture\n')
        script = self.work/'permissions.sh'
        script.write_text('set -eu\nchown() { printf "%s\\n" "$*" > "$st/ownership.txt"; }\n' +
                          own + '\n' + blanket + '\n' + fixed + '\n', newline='\n')
        shell = 'C:/Program Files/Git/usr/bin/sh.exe' if os.name == 'nt' else '/bin/sh'
        proc = subprocess.run([shell, str(script)], env={**os.environ, 'st': self.work.as_posix()},
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((self.work/'ownership.txt').read_text().strip(), '-R root:wheel ' + self.work.as_posix())
        for name in files:
            self.assertFalse((self.work/name).stat().st_mode & 0o222, name)
            (self.work/name).chmod(0o644)

    def test_runbook_fingerprint_matches_public_readme(self):
        fingerprint = (ROOT/'install/release-key.fingerprint').read_text().splitlines()[0]
        readme = (ROOT/'README.md').read_text()
        verification = readme.split('## Verifying', 1)[1]
        displayed = re.search(r'```\n([A-F0-9 ]+)\n```', verification)[1]
        self.assertEqual(''.join(displayed.split()), fingerprint)
        runbook = (HERE/'RUNBOOK-910.md').read_text()
        self.assertIn('= ' + fingerprint, runbook)
        self.assertIn("root:wheel:444", runbook)


if __name__ == '__main__':
    unittest.main()
