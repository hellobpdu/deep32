"""Profile and apt dependency tests with synthetic signed-index records only."""
import copy
import importlib.util
import os
from pathlib import Path
import types
import unittest
from unittest import mock

SOURCE = Path(os.environ.get('D32_RESOLVER', Path(__file__).resolve().parents[2] / 'src' / 'build' / 'disc3-pins' / 'resolve-pins.py'))
SPEC = importlib.util.spec_from_file_location('profile_resolver', SOURCE)
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)
D = 'deferred: v1.1 ISO form'


def fixture(which='pack'):
    names = ['docker.io', 'containerd', 'runc', 'docker-compose-v2', 'tailscale', 'python3',
             'openssl', 'ca-certificates', 'gnupg', 'util-linux'] + sorted(resolver.NVIDIA_TOOLKIT)
    doc = {'release_profile': which, 'packages': [name + '=TO-PIN' for name in names] + ['kmod=' + D],
           'ubuntu': {'driver_packages': ['nvidia-utils-580-server=' + D], 'kernel_package': 'linux-generic=' + D,
                      'modules_package': 'linux-modules-nvidia-580-server-generic=' + D,
                      'module_version': D, 'module_signer': D},
           'runtime': {}, 'models': {}, 'tei': {'variants': {}},
           'fetch': {'repositories': {repo: {'packages': [{'name': 'synthetic', 'version': '1'}], 'fingerprint': 'A' * 40}
                                      for repo in resolver.REPOSITORIES}}}
    indexes = {repo: {} for repo in resolver.REPOSITORIES}
    bases = {repo: 'https://example.invalid/' + repo + '/' for repo in resolver.REPOSITORIES}
    rows = []
    for name in names + ['libc6']:
        repo = 'nvidia' if name in resolver.NVIDIA_TOOLKIT else 'tailscale' if name == 'tailscale' else 'ubuntu-noble'
        indexes[repo][(name, '1', 'amd64')] = {'SHA256': 'b' * 64, 'Filename': 'pool/' + name + '.deb',
                                            '_index': 'Packages', 'Size': '1'}
        rows.append('Inst ' + name + ' (1 synthetic [amd64])')
    output = '\n'.join(['D32-INST ' + row for row in rows] + ['D32-KIT ' + row for row in rows]) + '\n'
    return doc, indexes, bases, output


class PackProfile(unittest.TestCase):
    def resolve(self, extra='', kit_extra=''):
        doc, indexes, bases, output = fixture()
        output += extra + kit_extra
        ctx = types.SimpleNamespace(lead={})
        with mock.patch.object(resolver, 'load_indexes', return_value=(indexes, bases)), \
                mock.patch.object(resolver.AptClosure, 'apt_container', return_value=('sha256:' + 'c' * 64, output)) as apt:
            result = resolver.AptClosure().resolve(ctx, doc)
        return doc, result, apt

    def test_profile_switch_skips_iso_and_module_families_and_release_key(self):
        doc = fixture()[0]
        for which in ('pack', 'iso'):
            doc['release_profile'] = which
            items = resolver.all_items(doc)
            families = {item.family for item in items}
            self.assertEqual('iso' in families, which == 'iso')
            self.assertEqual('module' in families, which == 'iso')
            self.assertEqual(any(isinstance(item, resolver.KeyItem) and item.name == 'ubuntu-release' for item in items), which == 'iso')
        del doc['release_profile']
        self.assertEqual(resolver.profile(doc), 'iso')

    def test_pack_direct_package_set_retains_every_wsl_need(self):
        doc = fixture()[0]
        active = {row[2] for row in resolver.direct_entries(doc)}
        self.assertTrue({'docker.io', 'containerd', 'runc', 'docker-compose-v2', 'tailscale', 'python3'} <= active)
        self.assertTrue(set(resolver.NVIDIA_TOOLKIT) <= active)
        self.assertFalse(any(resolver.iso_package(name) for name in active))
        doc['release_profile'] = 'iso'
        self.assertTrue({'kmod', 'nvidia-utils-580-server', 'linux-generic', 'linux-modules-nvidia-580-server-generic'} <=
                        {row[2] for row in resolver.direct_entries(doc)})

    def test_pack_apt_closure_has_toolkit_without_kernel_driver_or_module(self):
        doc, result, apt = self.resolve()
        request = apt.call_args.args[3]
        self.assertFalse(any(resolver.iso_package(name) for name in request))
        names = {row['name'] for repo in resolver.REPOSITORIES for row in result.updates[('fetch', 'repositories', repo, 'packages')]}
        self.assertTrue(set(resolver.NVIDIA_TOOLKIT) <= names)
        self.assertIn('libc6', names)
        self.assertFalse(any(resolver.iso_package(name) for name in names))
        self.assertNotIn(('ubuntu', 'module_version'), result.updates)
        for repo in resolver.REPOSITORIES:
            doc['fetch']['repositories'][repo]['packages'] = result.updates[('fetch', 'repositories', repo, 'packages')]
        doc['packages'] = result.updates[('packages',)]
        self.assertFalse(resolver.AptClosure().pending(types.SimpleNamespace(), doc))

    def test_dependency_driver_components_are_refused(self):
        for name in ('libnvidia-gl-580-server', 'xserver-xorg-video-nvidia-580-server', 'nvidia-firmware-580-server',
                     'linux-image-6.8.0-149-generic'):
            with self.subTest(name=name), self.assertRaisesRegex(resolver.Refusal, 'ISO-only package'):
                self.resolve('D32-INST Inst ' + name + ' (1 synthetic [amd64])\n')

    def test_with_recommends_driver_dependency_is_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'install-time simulation contains an ISO-only'):
            self.resolve(kit_extra='D32-KIT Inst libnvidia-gl-580-server (1 synthetic [amd64])\n')

    def test_new_driver_requirements_are_reviewed_exact_lead_inputs(self):
        doc = fixture()[0]
        doc['runtime'] = {'cuda_minimum_driver': 'TO-PIN', 'tei_minimum_driver': 'TO-PIN'}
        specs = {field: {'version': '580.1.2', 'source': 'https://vendor.invalid/driver-requirements',
                         'review': 'Synthetic reviewed version for the immutable image'} for field in doc['runtime']}
        ctx = types.SimpleNamespace(lead={'driver_requirements': specs})
        result = resolver.LeadDriverRequirements().resolve(ctx, doc)
        for field in specs:
            self.assertEqual(result.updates[('runtime', field)], '580.1.2')
        for change in ({'version': 'latest'}, {'version': None}, {'source': 'file:///synthetic'}, {'review': ''},
                       {'review': ' '}, {'source': 'https://user:pass@vendor.invalid/doc'}):
            broken = copy.deepcopy(specs)
            broken['cuda_minimum_driver'].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(resolver.Refusal, 'needs an exact numeric version'):
                resolver.LeadDriverRequirements().resolve(types.SimpleNamespace(lead={'driver_requirements': broken}), doc)


if __name__ == '__main__':
    unittest.main()
