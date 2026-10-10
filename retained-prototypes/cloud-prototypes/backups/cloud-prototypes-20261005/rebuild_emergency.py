#!/usr/bin/env python3
"""Recover the archived emergency graph in a new output directory.

Original source files stay byte-for-byte unchanged in docs/examples. This wrapper
copies them and changes only two machine-specific paths in the copied DOM test.
It does not start a browser, contact a backend, or claim visual verification.
"""
from pathlib import Path
import argparse, json, os, shutil, subprocess, sys
p=argparse.ArgumentParser()
p.add_argument('output',type=Path,help='A new directory for derived HTML/JSON and source copies')
a=p.parse_args(); out=a.output.resolve()
if out.exists(): raise SystemExit('Output already exists; choose a new directory to avoid overwriting work.')
repo=Path(__file__).resolve().parents[2]
app=repo/'frontend/console-prototype'
jsdom=app/'node_modules/jsdom'
if not jsdom.exists(): raise SystemExit('Run npm ci in frontend/console-prototype first.')
out.mkdir(parents=True)
src=out/'emergency-delivery'
shutil.copytree(repo/'docs/examples/emergency-delivery',src,ignore=shutil.ignore_patterns('__pycache__'))
projection=out/'emergency-canonical-projection.json'
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
with projection.open('w') as f:
 subprocess.run(['node',str(app/'scripts/emit-emergency-graph.mjs')],stdout=f,env=env,check=True)
subprocess.run([sys.executable,str(src/'build_fixture.py')],env=env,check=True)
subprocess.run([sys.executable,str(src/'build_canonical_page.py'),str(projection)],env=env,check=True)
test=src/'test_canonical_graph.cjs'
s=test.read_text().replace("require('/workspace/shared/AeroAgentSim-workbench/frontend/console-prototype/node_modules/jsdom')",'require('+json.dumps(str(jsdom))+')').replace("fs.readFileSync('/tmp/emergency-canonical-projection.json')",'fs.readFileSync('+json.dumps(str(projection))+')')
test.write_text(s)
r=subprocess.run(['node',str(test)],env=env,capture_output=True,text=True)
(out/'canonical-dom-test.stdout.txt').write_text(r.stdout)
(out/'canonical-dom-test.stderr.txt').write_text(r.stderr)
print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr)
r.check_returncode()
print('Recovered HTML:',out/'emergency-delivery-graph.html')
