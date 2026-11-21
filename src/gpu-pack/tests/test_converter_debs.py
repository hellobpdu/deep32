#!/usr/bin/env python3
"""Check the converter-only offline Debian closure before building a context."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / 'build'))
from images import CONVERTER_DEB_FORMAT, stage_converter_debs


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ConverterDebianInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.stage = root / 'stage'
        self.input = self.stage / 'converter'
        self.debs = self.input / 'debs'
        self.debs.mkdir(parents=True)
        self.context = root / 'context'
        self.context.mkdir()
        self.base = 'docker.io/library/python@sha256:' + 'a' * 64
        self.baseline = 'b' * 64
        self.plan = (
            '0 upgraded, 3 newly installed, 0 to remove and 12 not upgraded.\n'
            'Inst libgl1 (1.6.0-1 Debian:12/oldstable [amd64])\n'
            'Inst libglx-mesa0 (22.3.6-1+deb12u1 Debian:12/oldstable [amd64])\n'
            'Inst debian-test-data (1 Debian:12/oldstable [all])\n'
        ).encode('ascii')
        self.rows = []
        for name, version, arch in (('libgl1', '1.6.0-1', 'amd64'),
                                    ('libglx-mesa0', '22.3.6-1+deb12u1', 'amd64'),
                                    ('debian-test-data', '1', 'all')):
            filename = f'{name}_{version}_{arch}.deb'
            data = name.encode('ascii')
            (self.debs / filename).write_bytes(data)
            self.rows.append(dict(name=name, version=version, architecture=arch, file=filename,
                                  size=len(data), sha256=digest(data)))
        self.manifest = dict(format=CONVERTER_DEB_FORMAT, suite='bookworm', architecture='amd64',
                             base_image=self.base, base_dpkg_status_sha256=self.baseline,
                             apt_plan_sha256=digest(self.plan), packages=self.rows)
        self.write_inputs()

    def write_inputs(self, pin=True):
        self.manifest['apt_plan_sha256'] = digest(self.plan)
        (self.input / 'apt-plan.txt').write_bytes(self.plan)
        raw = json.dumps(self.manifest, sort_keys=True).encode('ascii')
        (self.input / 'debs.json').write_bytes(raw)
        if pin:
            (self.stage / 'pins.json').write_text(json.dumps({'runtime': {
                'base_image': self.base, 'converter_debian_closure_sha256': digest(raw),
            }}), encoding='ascii')

    def refuse(self, reason):
        with self.assertRaisesRegex(ValueError, reason):
            stage_converter_debs(self.stage, self.context)
        self.assertFalse((self.context / 'debs').exists())

    def test_full_solver_closure_and_exact_base_stamp_stage(self):
        stage_converter_debs(self.stage, self.context)
        staging = self.context / 'debs'
        self.assertEqual({path.name for path in staging.iterdir()},
                         {row['file'] for row in self.rows} | {'base-dpkg.sha256'})
        self.assertEqual((staging / 'base-dpkg.sha256').read_bytes(),
                         (self.baseline + '  /var/lib/dpkg/status\n').encode('ascii'))

    def test_declared_gl_archives_without_full_solver_closure_refuse(self):
        self.rows.pop(1)
        self.write_inputs()
        self.refuse('full APT simulation')

    def test_changed_manifest_and_base_refuse(self):
        self.manifest['base_image'] = self.base[:-1] + 'b'
        self.write_inputs(pin=False)
        self.refuse('manifest hash')
        self.write_inputs()
        self.refuse('invalid pinned Bookworm')

    def test_changed_plan_and_upgrade_refuse(self):
        self.plan = self.plan.replace(b'Inst libgl1 (', b'Inst libgl1 [1.5] (')
        self.write_inputs()
        self.refuse('upgrade')
        (self.input / 'apt-plan.txt').write_bytes(b'changed')
        self.refuse('simulation hash')

    def test_package_tampering_and_extra_or_missing_files_refuse(self):
        path = self.debs / self.rows[0]['file']
        path.write_bytes(b'bad')
        self.refuse('archive differs')
        path.unlink()
        self.refuse('missing or unlisted')
        path.write_bytes(b'libgl1')
        (self.debs / 'unexpected.deb').write_bytes(b'extra')
        self.refuse('missing or unlisted')

    def test_linked_package_refuses_even_when_bytes_match(self):
        original = self.debs / self.rows[0]['file']
        target = self.context / original.name
        original.replace(target)
        try:
            os.symlink(target, original)
        except OSError as exc:
            self.skipTest(f'symbolic links unavailable: {exc}')
        self.refuse('linked converter build input')


if __name__ == '__main__':
    unittest.main()
