"""Explicit test entry points and a compact machine-readable local verification report."""
from datetime import datetime, timezone
from pathlib import Path
import json
import argparse
import os
import unittest

class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        self.passed.append(test.id())
        super().addSuccess(test)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent / 'test_results.json')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromNames(['test_adapter', 'test_ledger'])
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    report = {'schema': 'fixture.binding-adapter-tests/v1', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'in-memory fixture tests and read-only native Atlas evaluations; no live provider or server test',
              'external_atlas_supplied': bool(os.environ.get('ATLAS_ROOT')), 'tests_run': result.testsRun, 'passed': len(result.passed),
              'failures': [name.id() for name, _ in result.failures], 'errors': [name.id() for name, _ in result.errors],
              'skipped': [(name.id(), reason) for name, reason in result.skipped], 'passed_tests': result.passed}
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
