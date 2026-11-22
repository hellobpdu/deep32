"""The builder derives a new generation with 0.1.0-alpha.3's serving settings (WP-E E9): builder.SERVED_SETTINGS goes
to derive_root.py as --set items, the six of alpha.2 first, then the values the disc 1 release overlay ships."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve()
KIT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location('served_settings_builder', KIT / 'containers/builder/builder.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

EXPECTED = [
    ('retrieval.grep.regex_engine', True),
    ('retrieval.reranker.candidates', 15),
    ('retrieval.reranker.max_chars', 1600),
    ('retrieval.reranker.window', 'centred'),
    ('retrieval.reranker.search_many_candidates', 10),
    ('retrieval.fold_identical', True),
    ('retrieval.evidence.tier1_chars', 3200),
    ('retrieval.evidence.covered_titles', True),
    ('retrieval.evidence.table_context', True),
    ('retrieval.verify.ellipsis', 'bounded'),
    ('retrieval.verify.max_gap_chars', 300),
    ('retrieval.search_many.rerank_isolation', True),
    ('serving.pipeline.rerank_gate', 1),
]


def parse_value(text):
    """derive_root.parse_value: JSON, or else the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text


class ServedSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='served-settings-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'engine').mkdir()
        served = root / 'served-one'
        served.mkdir()
        (served / 'BUILD_REPORT.json').write_text(
            json.dumps({'status': 'complete', 'failed_steps': [], 'documents': {'failed': 0}}), encoding='ascii')
        self.args = SimpleNamespace(
            generation='build-one', served_generation='served-one', engine_root=str(root / 'engine'),
            build_root=str(root / 'build-one'), served_root=str(served), eval_root=None,
            kept_manifest=str(root / 'kept.json'), python=sys.executable, corpus_repo='synthetic-corpus',
            corpus_ref='synthetic-ref', expect_fingerprint='synthetic-fingerprint', export_dir=str(root / 'export'),
            recipient='synthetic-recipient')

    def test_derive_root_is_given_every_setting_in_order(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)

        builder.build(self.args, runner=runner, environ={})
        [derive] = [argv for argv in calls if Path(argv[3]).name == 'derive_root.py']
        sets = [derive[i + 1] for i, item in enumerate(derive) if item == '--set']
        self.assertEqual(sets, list(builder.SERVED_SETTINGS))
        parsed = [(key, parse_value(value)) for key, _, value in (item.partition('=') for item in sets)]
        self.assertEqual(parsed, EXPECTED)

    def test_the_values_are_the_release_overlays(self):
        overlay = KIT.parent / 'workstation/ship/site/usr/local/d32/etc/engine-overlay'
        shipped = {}
        for name in ('retrieval', 'serving'):
            def flat(prefix, value):
                if isinstance(value, dict):
                    for key, inner in value.items():
                        yield from flat(f'{prefix}.{key}', inner)
                else:
                    yield prefix, value
            shipped.update(flat(name, json.loads((overlay / f'{name}.json').read_text(encoding='utf-8'))))
        self.assertEqual(dict(EXPECTED[6:]), shipped)


if __name__ == '__main__':
    unittest.main()
