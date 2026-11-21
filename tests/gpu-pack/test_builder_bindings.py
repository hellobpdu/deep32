"""End-to-end offline image binding across stage, record, merge and validation."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import test_builder_stage as fixtures
from runtime_fixtures import make_image, inspect_row

BUILD = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack' / 'build'
sys.path.insert(0, str(BUILD))
import builder_inputs as inputs
import builder_stage as stage_tool

spec = importlib.util.spec_from_file_location('binding_validate', BUILD / 'validate.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class BuilderBindings(unittest.TestCase):
    def fixture(self, oci):
        f = fixtures.BuilderStage()
        f.setUp()
        self.addCleanup(f.doCleanups)
        measured = {}
        for role in ('base', 'qdrant'):
            source = None if oci else f.refs[role].split('@')[1]
            row = make_image(f.archives / (role + '.tar'), role, oci=oci, source_digest=source)
            measured[role] = row
            f.refs[role] = stage_tool.PREFIX[role] + '@' + row['index_digest']
            inspected = inspect_row(row, index=oci)
            inspected.update(RepoDigests=[stage_tool.REPOSITORY[role] + '@' + row['index_digest']],
                             RepoTags=[stage_tool.PREFIX[role]], Config={'Labels': {}})
            fixtures.put_json(f.archives / (role + '.inspect.json'), [inspected])
        fixtures.put(f.ctx / 'compose/engine.Dockerfile', 'FROM ' + f.refs['base'] + '\n')
        fixtures.put_json(f.ctx / 'compose/compose.json', {'services': {
            'qdrant': {'image': f.refs['qdrant']}, 'qdrant-c3': {'image': f.refs['qdrant']}}})
        return f, measured

    def test_stage_record_merge_and_final_validation_both_stores(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                f, measured = self.fixture(oci)
                result = f.run_stage()
                config = inputs.checked_inputs(f.out, f.kit)
                for role in ('base', 'qdrant'):
                    self.assertEqual(config[role]['config_digest'], measured[role]['config_digest'])
                    self.assertEqual(config[role]['index_digest'], measured[role]['index_digest'])
                    self.assertEqual(result['images'][role]['inspected_id'],
                                     measured[role]['index_digest' if oci else 'config_digest'])
                output = f.root / 'built'
                builder = make_image(output / 'images/builder.tar', 'builder', oci=oci)
                shutil.copyfile(f.out / 'builder/qdrant.tar', output / 'images/qdrant.tar')
                fixtures.put(output / 'context/Dockerfile', '# synthetic build input\n')
                def inspect(argv, **kwargs):
                    row = builder if argv[-1] == 'deep32-local/builder:locked' else measured['qdrant']
                    return json.dumps([inspect_row(row, index=oci)])
                with patch.object(inputs.subprocess, 'check_output', side_effect=inspect):
                    inputs.record(f.out, output, f.kit)
                fixtures.put_json(f.out / 'payload/images/index.json', [])
                fixtures.put_json(f.out / 'pins.json', {'runtime': {}})
                inputs.merge(f.out, output, f.kit)
                pins = json.loads((f.out / 'pins.json').read_text())
                rows = json.loads((f.out / 'payload/images/index.json').read_text())
                for role in ('converter', 'door', 'reranker', 'tei-cpu'):
                    source = 'sha256:' + 'a' * 64 if role == 'tei-cpu' and not oci else None
                    row = make_image(f.out / 'payload/images' / (role + '.tar'), role,
                                     oci=oci, source_digest=source)
                    rows.append(row)
                    if role != 'tei-cpu':
                        pins['runtime'][role + '_image_id'] = row['index_digest'] or row['config_digest']
                    else:
                        pins['tei'] = {'variants': {'cpu': {'digest': row['index_digest']}}}
                fixtures.put_json(f.out / 'payload/images/index.json', rows)
                validator.validate_images(f.out / 'payload', pins)
                correct_id = pins['runtime']['builder_image_id']
                for invalid_id in (None, '', 'latest', 'sha256:' + '0' * 64):
                    pins['runtime']['builder_image_id'] = invalid_id
                    with self.assertRaisesRegex(ValueError, 'application image pin differs'):
                        validator.validate_images(f.out / 'payload', pins)
                pins['runtime']['builder_image_id'] = correct_id
                for row in rows:
                    self.assertEqual(row['image_id'], row['config_digest'])
                qdrant = next(row for row in rows if row['role'] == 'qdrant')
                qdrant['config_digest'] = 'sha256:' + 'e' * 64
                fixtures.put_json(f.out / 'payload/images/index.json', rows)
                with self.assertRaisesRegex(ValueError, 'config digest'):
                    validator.validate_images(f.out / 'payload', pins)

    def test_resealed_classic_pin_with_wrong_inspected_id_refuses(self):
        f, _ = self.fixture(False)
        f.run_stage()
        path = f.out / 'builder/pins.json'
        config = json.loads(path.read_text())
        config['base']['inspected_id'] = config['base']['index_digest']
        fixtures.put_json(path, config)
        with self.assertRaisesRegex(ValueError, 'classic measured image id differs'):
            inputs.checked_inputs(f.out, f.kit)

    def test_record_refuses_foreign_loaded_id_and_multiple_inspects(self):
        f, _ = self.fixture(True)
        f.run_stage()
        output = f.root / 'built'
        make_image(output / 'images/builder.tar', 'builder', oci=True)
        for inspected in ([], [{'Id': 'sha256:' + 'f' * 64, 'Os': 'linux', 'Architecture': 'amd64'}],
                          [{'Os': 'linux', 'Architecture': 'amd64'}],
                          [{'Id': None, 'Os': 'linux', 'Architecture': 'amd64'}]):
            with self.subTest(inspected=inspected):
                with patch.object(inputs.subprocess, 'check_output', return_value=json.dumps(inspected)):
                    with self.assertRaises(ValueError):
                        inputs.record(f.out, output, f.kit)
                self.assertFalse((output / 'builder-images.json').exists())


if __name__ == '__main__':
    unittest.main()
