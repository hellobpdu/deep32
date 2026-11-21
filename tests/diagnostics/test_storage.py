"""Invented reservation observations; no native storage observation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/diagnostics'))
from deep32_diag.errors import DiagError
from deep32_diag.models import State
from deep32_diag.storage import StorageBudget as B, StorageReading as R, storage_rows


class StorageTests(unittest.TestCase):
    def rows(self, free, *, backing=True):
        return storage_rows((B('content', 'private-fs', 10, 30), B('state', 'private-fs', 5, 10)),
                            {'private-fs': R(free, backing)}, role='workstation')

    def test_shared_filesystem_is_charged_once_with_summed_budgets(self):
        rows = self.rows(40)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, State.OK)
        self.assertEqual(dict(rows[0].observed_items),
                         {'free_bytes': 40, 'required_bytes': 40, 'safe_running_bytes': 15})

    def test_thresholds(self):
        for free, state in ((39, State.WARN), (15, State.WARN), (14, State.FAIL),
                            (0, State.FAIL), (None, State.FAIL)):
            with self.subTest(free=free):
                self.assertEqual(self.rows(free)[0].state, state)

    def test_windows_backing_path_requires_qualification(self):
        self.assertEqual(self.rows(100, backing=False)[0].state, State.FAIL)

    def test_separate_filesystems(self):
        rows = storage_rows((B('data', 'one', 5, 10), B('docker', 'two', 5, 10)),
                            {'one': R(10)}, role='forge')
        self.assertEqual([x.state for x in rows], [State.OK, State.FAIL])

    def test_aliases_budgets_and_primitive_types(self):
        for b in (B('../private', 'one', 1, 2), B('data', 'one', True, 2),
                  B('data', 'one', 2, 1), B('data', '', 1, 2)):
            with self.subTest(b=b):
                with self.assertRaises(DiagError):
                    storage_rows((b,), {}, role='forge')
        with self.assertRaises(DiagError):
            self.rows(True)

    def test_overflow_and_duplicate_reservations_refused(self):
        for budgets in ((B('data', 'x', 1, 2), B('data', 'x', 1, 2)),
                        (B('one', 'x', 1, 2**63-1), B('two', 'x', 1, 2))):
            with self.assertRaises(DiagError):
                storage_rows(budgets, {}, role='forge')


if __name__ == '__main__':
    unittest.main()
