#!/usr/bin/env python3
"""Package private source/provenance separately from the standalone offline HTML."""
from pathlib import Path
import hashlib,json,zipfile
ROOT=Path(__file__).resolve().parents[1];UI=ROOT/'ui'
output=UI/'P08-graph-workbench-private-source.zip'
files=[]
for folder in ['ui','data','spec','pipeline']:
    for path in (ROOT/folder).rglob('*'):
        rel=path.relative_to(ROOT)
        if not path.is_file() or any(part in ['node_modules','__pycache__','.git'] for part in rel.parts):continue
        if path.suffix in ['.zip','.pyc'] or path.name in ['P08-graph-explorer-offline.html','package-report.json','package-manifest.json']:continue
        files.append(path)
for folder in ['review/domain','review/technical']:
    for path in (ROOT/folder).glob('*'):
        if path.is_file() and 'initial' not in path.name and path.suffix!='.pyc':files.append(path)
files=sorted(set(files));manifest={}
with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    archive.writestr('p08-graph-workbench/START_HERE.md',(UI/'README.md').read_text())
    for path in files:
        rel=path.relative_to(ROOT).as_posix();payload=path.read_bytes();manifest[rel]={'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()};archive.write(path,'p08-graph-workbench/'+rel)
    archive.writestr('p08-graph-workbench/SHA256SUMS.txt',''.join(f'{entry["sha256"]}  {path}\n' for path,entry in manifest.items()))
with zipfile.ZipFile(output) as archive:
    bad=archive.testzip()
    if bad:raise RuntimeError(f'Invalid zip member {bad}')
report={'file':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'source_files':len(files),'zip_crc_check':'passed','graph_sha256':manifest['data/graph.json']['sha256'],'omitted_redundant_artifact':'P08-graph-explorer-offline.html is delivered separately; regenerate with python3 ui/build-offline.py','excluded':'node_modules, caches, large review rebuild copy, historical initial logs','visual_verification':'not_run'}
assert report['bytes']<50*1024*1024,report
(UI/'package-report.json').write_text(json.dumps(report,indent=2)+'\n')
(UI/'package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(report,indent=2))
