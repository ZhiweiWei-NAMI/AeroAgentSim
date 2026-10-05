#!/usr/bin/env python3
"""Run importer tests and persist a small version-bound result, never runtime QA."""
import hashlib
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
suite=unittest.defaultTestLoader.discover(str(ROOT/'pipeline'),pattern='test_*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={'scope':'offline_pipeline_preservation_identity_types_and_time_only','passed':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'graph_sha256':hashlib.sha256((ROOT/'data/graph.json').read_bytes()).hexdigest(),'native_execution':False,'simulator_execution':False,'real_host_verified':False}
(ROOT/'data/pipeline-test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
raise SystemExit(0 if result.wasSuccessful() else 1)
