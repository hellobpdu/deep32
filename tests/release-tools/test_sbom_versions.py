#!/usr/bin/env python3
"""Exact installed versions keep their native Debian and Python forms in SBOM inputs and SPDX output."""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/release-tools'))
from sbom import common, spdx  # noqa: E402


class ExactVersions(unittest.TestCase):
    def test_native_versions_are_accepted(self):
        for value in ('1:2.39-1', '1.0~rc1-1', '2.36-9+deb12u9', '1!2.0', '2.1.0+cpu', '3.12.7', 'v5.4.0p1'):
            with self.subTest(value=value):
                self.assertEqual(common.exact_version(value), value)

    def test_unsafe_or_placeholder_versions_are_refused(self):
        for value in ('', ' 1.0', '1.0/2', '1.0\\2', '1.0@x', '1.0#x', '1.0?x', '1 0', ':1.0', 'TO-PIN',
                      'unknown', 'NOASSERTION', 'x' * 129, None, 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    common.exact_version(value)

    def test_spdx_package_keeps_the_exact_version(self):
        package = spdx._package('SPDXRef-Package-x', 'libc6', '2.36-9+deb12u9', 'LIBRARY')
        self.assertEqual(package['versionInfo'], '2.36-9+deb12u9')
        package = spdx._package('SPDXRef-Package-y', 'bash', '1:5.2.15-2', 'APPLICATION')
        self.assertEqual(package['versionInfo'], '1:5.2.15-2')


if __name__ == '__main__':
    unittest.main()
