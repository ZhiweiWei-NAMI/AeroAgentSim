#!/usr/bin/env python3
"""Recover exact accepted source bytes from a checked lossless migration package.

The package, rather than graph.json alone, is lossless. Unmodeled document-level
metadata is retained in the immutable input snapshots. Every graph occurrence can
also be looked up by its reversible source pointer and checked raw object digest.
"""
import argparse
import hashlib
import json
from pathlib import Path
from import_catalogs import at_pointer, digest

def recover_artifact(artifact, data_root=None):
 root=Path(data_root) if data_root else Path(__file__).resolve().parents[1]/'data'
 manifest=json.loads((root/'input-manifest.json').read_text())
 source=next(x for x in manifest['inputs'] if x['artifact']==artifact)
 data=(root/source['path']).read_bytes()
 if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('Input snapshot integrity failure: '+artifact)
 return data

def recover_occurrence(alias, data_root=None):
 source=alias['source_record'];data=recover_artifact(source['artifact'],data_root)
 if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('Occurrence source-version linkage failure: '+source['artifact'])
 doc=json.loads(data)
 raw=at_pointer(doc,source['pointer'])
 if digest(raw)!=alias['raw_sha256']:raise ValueError('Occurrence integrity failure: '+source['pointer'])
 return raw

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('artifact');ap.add_argument('--data-root',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 data=recover_artifact(args.artifact,args.data_root)
 with args.output.open('xb') as f:f.write(data)
 print(str(args.output))
