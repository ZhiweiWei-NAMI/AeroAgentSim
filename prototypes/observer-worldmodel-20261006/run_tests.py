"""Standard-library runner. Missing optional dependencies are reported as skips."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import unittest
p=argparse.ArgumentParser();p.add_argument('--require-torch',action='store_true');args=p.parse_args()
root=Path(__file__).parent
suite=unittest.defaultTestLoader.discover(str(root/'tests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
summary={'tests_run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
         'failed':len(result.failures),'errors':len(result.errors),'skipped':[{'test':str(t),'reason':r} for t,r in result.skipped],
         'torch_available':importlib.util.find_spec('torch') is not None,
         'scope':'synthetic fixtures only; skipped tests are NOT passes'}
(root/'reports'/'unit-tests.json').write_text(json.dumps(summary,indent=2)+'\n')
if args.require_torch and not summary['torch_available']:
    print('BLOCKED: --require-torch set but PyTorch is absent',file=sys.stderr);sys.exit(2)
sys.exit(0 if result.wasSuccessful() else 1)
