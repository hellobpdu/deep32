"""Frozen shared receipt to diagnostic policy integration; invented metadata."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/release-tools'))
import contract_validate
from test_core import SyntheticCase, canonical
from deep32_diag import trust


class FrozenReceipt(SyntheticCase):
    def fixture(self):
        value = json.loads((ROOT / 'src/release-tools/contracts/fixtures/valid/doctor-install.json').read_text())
        value['services'] = [dict(id='embeddings', image_id='sha256:' + '3' * 64,
                                  required=True, mode='running')]
        value['models'] = [dict(role='embedding', model='public-demo/model', revision='4' * 40,
                               files_manifest_sha256='5' * 64)]
        value['ports'] = [dict(id='embedding-port', port=8081, service='embeddings', owner='door')]
        value['routes'] = [dict(id='forge-route', peer_role='forge', port=443, owner='gateway')]
        release = self.make_trust().verify()
        inventory = {key: copy.deepcopy(value[key]) for key in trust._FAMILIES}
        binding = {key: copy.deepcopy(value[key]) for key in ('trust', 'capability_manifest_sha256', 'ntp_service')}
        return value, release, inventory, binding

    def parse(self, value, release, inventory, binding, **kwargs):
        return trust.validate_receipt(canonical(value), release, inventory,
            role=value['role'], platform=value['platform'],
            authoritative_content=kwargs.get('content', value['content']),
            authoritative_binding=binding)

    def test_shared_contract_fixture_reaches_immutable_probe_projection(self):
        value, release, inventory, binding = self.fixture()
        contract_validate.validate_contract('doctor-install', value)
        result = self.parse(value, release, inventory, binding)
        self.assertEqual((result.role, result.platform), ('workstation', 'openbsd'))
        self.assertEqual(result.inventory['models'][0]['alias'], 'embeddings')
        self.assertEqual(result.inventory['models'][0]['files_manifest_sha256'], '5' * 64)
        self.assertEqual(result.inventory['filesystems'][0]['required_bytes'], 2048)
        self.assertEqual(result.fingerprint, value['content']['corpus_fingerprint'])
        self.assertEqual(result.root_sha256, binding['trust']['root_sha256'])
        self.assertEqual(result.mirrors_sha256, binding['trust']['mirrors_sha256'])
        self.assertEqual(result.content_manifest_sha256, value['content']['manifest_sha256'])
        self.assertEqual(result.ntp_service, value['ntp_service'])
        value['services'][0]['image_id'] = 'sha256:' + '0' * 64
        self.assertEqual(result.inventory['services'][0]['image'], 'sha256:' + '3' * 64)
        with self.assertRaises(TypeError):
            result.inventory['ports'][0]['owner'] = 'changed'

    def test_receipt_cannot_supply_its_own_trust_or_installation_inventory(self):
        value, release, inventory, binding = self.fixture()
        self.assert_code('untrusted-release', self.parse, value, release, inventory, None)
        for name in ('root_sha256', 'mirrors_sha256', 'signer_fingerprint'):
            changed = copy.deepcopy(value)
            changed['trust'][name] = '0' * (40 if name == 'signer_fingerprint' else 64)
            self.assert_code('untrusted-release', self.parse, changed, release, inventory, binding)
        changed = copy.deepcopy(value)
        changed['ports'][0]['port'] = 8082
        self.assert_code('inventory-mismatch', self.parse, changed, release, inventory, binding)
        self.assert_code('inventory-mismatch', self.parse, value, release, inventory, binding, content=None)

    def test_capability_content_and_ntp_changes_remain_bound(self):
        value, release, inventory, binding = self.fixture()
        for field, replacement in (('capability_manifest_sha256', '6' * 64), ('ntp_service', 'other')):
            changed = copy.deepcopy(value)
            changed[field] = replacement
            self.assert_code('untrusted-release', self.parse, changed, release, inventory, binding)
        for field in ('corpus_fingerprint', 'manifest_sha256'):
            changed = copy.deepcopy(value)
            changed['content'][field] = '7' * 64
            self.assert_code('inventory-mismatch', self.parse, changed, release, inventory, binding,
                             content=value['content'])

    def test_unknown_fields_duplicate_identities_and_bool_ports_refused(self):
        value, release, inventory, binding = self.fixture()
        for family in trust._FAMILIES:
            changed = copy.deepcopy(value)
            changed[family][0]['unknown'] = 'synthetic'
            self.assert_code('invalid-receipt', self.parse, changed, release, inventory, binding)
            changed = copy.deepcopy(value)
            changed[family] *= 2
            self.assert_code('invalid-receipt', self.parse, changed, release, inventory, binding)
        changed = copy.deepcopy(value)
        changed['ports'][0]['port'] = True
        self.assert_code('invalid-receipt', self.parse, changed, release, inventory, binding)

    def test_unsealed_release_and_legacy_wire_shape_refused(self):
        value, release, inventory, binding = self.fixture()
        forged = trust.VerifiedRelease(release.manifest, release.manifest_sha256, trust.PIN, 'synthetic')
        self.assert_code('untrusted-release', self.parse, value, forged, inventory, binding)
        raw, projected = self.receipt(release)
        self.assert_code('invalid-receipt', trust.validate_receipt, canonical(raw), release, projected,
                         role='headend', platform='linux')

    def test_on_demand_mode_never_invents_an_authenticated_active_job(self):
        value, release, inventory, binding = self.fixture()
        value['services'][0].update(required=False, mode='on-demand')
        inventory['services'] = copy.deepcopy(value['services'])
        contract_validate.validate_contract('doctor-install', value)
        service = self.parse(value, release, inventory, binding).inventory['services'][0]
        self.assertEqual(service['mode'], 'on-demand')
        self.assertIs(service['active_job'], False)

    def test_forge_requires_capability_and_content_belongs_to_workstation(self):
        value, release, inventory, binding = self.fixture()
        value.update(role='forge', platform='linux', content=None)
        self.assert_code('invalid-receipt', self.parse, value, release, inventory, binding)
        value['capability_manifest_sha256'] = '6' * 64
        binding['capability_manifest_sha256'] = '6' * 64
        contract_validate.validate_contract('doctor-install', value)
        self.assertEqual(self.parse(value, release, inventory, binding).role, 'forge')
        value['content'] = dict(generation='synthetic', corpus_fingerprint='7' * 64, manifest_sha256='8' * 64)
        self.assert_code('invalid-receipt', self.parse, value, release, inventory, binding)


if __name__ == '__main__':
    unittest.main()
