"""Offline proof that both hosts render the same Linux container topology."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

KIT = Path(__file__).resolve().parents[2] / 'src/gpu-pack'
spec = importlib.util.spec_from_file_location('host_paths_runtime', KIT / 'containers/full/runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
IMAGES = {name: 'sha256:' + hashlib.sha256(name.encode('ascii')).hexdigest() for name in runtime.IMAGE_KEYS}
CONFIG = {'models': {'tei': {'revision': 'a' * 40}}, 'runtime': {'reranker_dtype': 'float16'},
          'tailscale_dns_name': 'fixture.fixture.ts.net', 'full': {'model_bridge': {
              'schema': 'deep32.model-bridge.v1', 'GPU_EXT_EMBED_MODEL_ID': 'fixture/embed',
              'GPU_EXT_RERANK_MODEL_ID': 'fixture/reranker', 'GPU_EXT_EMBED_REVISION': 'a' * 40,
              'GPU_EXT_RERANK_REVISION': 'b' * 40}}}


class HostPaths(unittest.TestCase):
    def test_drive_paths_are_normalised_without_changing_spaces(self):
        self.assertEqual(runtime.absolute_path(r'c:\Deep32 data\state'), 'C:/Deep32 data/state')
        self.assertEqual(runtime.absolute_path('D:/media/forge'), 'D:/media/forge')
        self.assertEqual(runtime.absolute_path('/opt/deep32'), '/opt/deep32')

    def test_unsafe_local_paths_are_rejected(self):
        for value in ('C:/', 'C:relative', '//server/share', r'\\?\C:\state', 'C:/a/../b',
                      'C:/a//b', 'C:/$state', 'C:/a:stream', 'C:/state.', 'C:/NUL.txt',
                      'C:/state/CON', 'C:/a?b', 'C:/a\nb', '/', 'relative', '/opt/../state'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.absolute_path(value)

    def test_windows_and_linux_use_identical_services_and_container_paths(self):
        linux = runtime.prepare_config('/media/forge/kit', '/media/forge/payload', '/opt/deep32',
                                       '/media/content', IMAGES, CONFIG)
        windows = runtime.prepare_config('E:/forge/kit', 'E:/forge/payload', 'C:/Deep32 state',
                                         'F:/content', IMAGES, CONFIG)
        self.assertEqual(set(linux['services']), set(windows['services']))
        # Only the host-side bind and secret locations vary. All service images,
        # targets, restart policies, health checks and publication stay the same.
        def host_locations_removed(value):
            if isinstance(value, dict):
                return {key: '<host-path>' if key in ('source', 'file') else host_locations_removed(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [host_locations_removed(item) for item in value]
            return value
        self.assertEqual(host_locations_removed(linux), host_locations_removed(windows))
        source = windows['services']['embeddings']['volumes'][0]['source']
        self.assertEqual(source, 'E:/forge/payload/models/tei')
        self.assertNotIn('C:\\', json.dumps(windows))

    def test_actual_mounted_windows_pack_layout_uses_drive_roots(self):
        value = runtime.prepare_config('E:/', 'E:/deep32/payload', 'C:/Deep32', 'F:/', IMAGES, CONFIG)
        self.assertEqual(value['services']['door']['volumes'][0]['source'], 'E:/containers/full/health.py')
        content = next(row for row in value['services']['importer']['volumes'] if row['target'] == '/media/content')
        self.assertEqual(content['source'], 'F:/')
        with self.assertRaises(ValueError):
            runtime.prepare_config('E:/', 'E:/deep32/payload', 'C:/', 'F:/', IMAGES, CONFIG)

    def test_windows_case_does_not_bypass_installation_separation(self):
        for content in ('c:/deep32 STATE', 'c:/deep32 state/content', 'C:/'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                runtime.prepare_config('E:/kit', 'E:/payload', 'C:/Deep32 state', content, IMAGES, CONFIG)

    def test_windows_recorded_release_kit_is_allowed(self):
        kit = 'c:/DEEP32 state/releases/' + 'f' * 64 + '/kit'
        value = runtime.prepare_config(kit, 'E:/payload', 'C:/Deep32 state', 'F:/content', IMAGES, CONFIG)
        self.assertTrue(value['services']['door']['volumes'][0]['source'].endswith('/kit/containers/full/health.py'))

    def test_mixed_host_paths_are_refused(self):
        with self.assertRaises(ValueError):
            runtime.prepare_config('/media/kit', 'E:/payload', 'C:/state', 'F:/content', IMAGES, CONFIG)


if __name__ == '__main__':
    unittest.main(verbosity=2)
