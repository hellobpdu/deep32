"""The public path rule: Debian file names with an encoded epoch pass, unsafe names still refuse."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'release-tools'))
from sbom import common


class PublicPathTests(unittest.TestCase):
    def test_percent_encoded_epoch_in_a_package_file_name_is_accepted(self):
        for name in ('builder/debs/git-man_1%3a2.47.3-0+deb13u1_all.deb',
                     'builder/debs/git_1%3A2.47.3-0+deb13u1_amd64.deb',
                     'a/b+c@d_e-f.g'):
            self.assertEqual(common.public_path(name), name)

    def test_a_debian_tilde_inside_a_name_is_accepted(self):
        # The real disc 3 stage holds these; Debian versions sort pre-releases with '~'.
        for name in ('builder/debs/libexpat1_2.9.0-1~deb13u1_amd64.deb',
                     'payload/repos/ubuntu/pool/main/r/runc-app/runc_1.3.4-0ubuntu1~24.04.1_amd64.deb',
                     'payload/repos/ubuntu/pool/universe/d/docker-compose-v2/'
                     'docker-compose-v2_2.40.3+ds1-0ubuntu1~24.04.1_amd64.deb'):
            self.assertEqual(common.public_path(name), name)

    def test_a_segment_may_not_start_with_a_tilde(self):
        for name in ('~', '~/a', 'a/~b', 'a/~/b', '~root/x'):
            with self.assertRaisesRegex(ValueError, 'safe relative public path required'):
                common.public_path(name)

    def test_a_percent_sign_must_start_a_two_digit_hex_escape(self):
        for name in ('a/50%', 'a/%', 'a/%3', 'a/%zz.deb', 'a/%%3a', 'a/100%_done'):
            with self.assertRaisesRegex(ValueError, 'safe relative public path required'):
                common.public_path(name)

    def test_unsafe_names_still_refuse(self):
        for name in ('', '/abs', 'a//b', 'a/./b', 'a/../b', '..', 'a\\b', 'c:/x', 'a b', 'a/b?c',
                     'x' * 513, 'a/b\n'):
            with self.assertRaisesRegex(ValueError, 'safe relative public path required'):
                common.public_path(name)
        with self.assertRaisesRegex(ValueError, 'safe relative public path required'):
            common.public_path(None)

    def test_an_escape_is_never_decoded(self):
        # '%2e%2e' stays a literal name; nothing decodes it into '..'.
        self.assertEqual(common.public_path('a/%2e%2e/b'), 'a/%2e%2e/b')

    def test_private_names_still_refuse(self):
        for name in ('secrets/x', 'a/.ssh/id', 'a/sealed/k.json', 'a/priv-notes/x', 'a/site.env', 'a/q0123.json'):
            with self.assertRaisesRegex(ValueError, 'excluded private input path'):
                common.public_path(name)


if __name__ == '__main__':
    unittest.main()
