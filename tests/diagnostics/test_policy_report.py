"""Role policy output stays a static allowlist, including exact L3 wording."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/diagnostics'))
from deep32_diag.errors import DiagError
from deep32_diag.models import State, TAILNET_FIX
from deep32_diag.report import Reason, row, build_report
from deep32_diag.wording import POLICIES


class RolePolicyTests(unittest.TestCase):
    def render(self, item, role='forge', platform='linux'):
        return build_report(role, platform, 'invented-node', '2026-11-17T12:00:00Z', [item])['checks'][0]

    def test_static_policy_strings_are_bounded_ascii(self):
        for key, (fix, sources) in POLICIES.items():
            self.assertLessEqual(len(fix), 768, key)
            self.assertTrue(all(32 <= ord(c) <= 126 for c in fix), key)
            for source in sources:
                self.assertLessEqual(len(source), 512, key)
                self.assertTrue(all(32 <= ord(c) <= 126 for c in source), key)

    def test_policy_cannot_select_another_family_or_inject_text(self):
        for policy in ('forge.container', 'forge.driver\nsecret', 'headend.driver'):
            with self.assertRaises(DiagError):
                row('driver', State.FAIL, Reason.UNAVAILABLE, policy=policy)
        with self.assertRaises(DiagError):
            self.render(row('driver', State.FAIL, Reason.UNAVAILABLE, policy='forge.driver'),
                        'workstation','openbsd')

    def test_l3_fix_exact_for_both_roles(self):
        for role, platform in (('forge','linux'),('workstation','openbsd')):
            item = row('tailnet-path.peer',State.WARN,Reason.UNAVAILABLE,
                       {'state':'relayed','peer_role':'forge'},policy=role+'.tailnet-path')
            self.assertEqual(self.render(item,role,platform)['fix'],TAILNET_FIX)

    def test_platform_hook_is_fixed_without_private_path(self):
        item = row('container.embeddings',State.FAIL,Reason.UNAVAILABLE,policy='forge.container')
        for platform, suffix in (('linux','.sh'),('windows','.ps1')):
            self.assertIn('startup-hook-v1'+suffix,self.render(item,platform=platform)['fix'])

    def test_inactive_optional_service_has_no_repair_fix(self):
        item = row('container.converter',State.SKIP,Reason.INAPPLICABLE,policy='forge.container')
        self.assertTrue(self.render(item)['fix'].startswith('No repair is needed;'))

    def test_runtime_configuration_does_not_assert_device_execution(self):
        item = row('gpu-runtime',State.OK,Reason.PASSED,policy='forge.gpu-runtime')
        self.assertIn('configuration',self.render(item)['detail'])
        self.assertIsNone(self.render(item)['fix'])


if __name__ == '__main__':
    unittest.main()
