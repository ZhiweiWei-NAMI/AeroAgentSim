#!/usr/bin/env python3
"""Verify the recovery snapshot and losslessly materialize original P08 files."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json

ROOT = Path(__file__).resolve().parent

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def contained(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Unsafe manifest path: ' + name)
    return path

def restore(output, check_only=False):
    manifest = json.loads((ROOT / 'RECOVERY-MANIFEST.json').read_text())
    checked = written = total = 0
    for row in manifest['files']:
        source = contained(ROOT, row['stored_path'])
        packed = source.read_bytes()
        if len(packed) != row['stored_bytes'] or sha256(packed) != row['stored_sha256']:
            raise ValueError('Stored snapshot integrity failure: ' + row['stored_path'])
        raw = gzip.decompress(packed) if row['encoding'] == 'gzip' else packed
        if len(raw) != row['bytes'] or sha256(raw) != row['sha256']:
            raise ValueError('Original-byte integrity failure: ' + row['path'])
        if not check_only:
            target = contained(output, row['path'])
            if target.exists():
                if target.read_bytes() != raw:
                    raise FileExistsError('Refusing to overwrite different bytes: ' + str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(raw)
                if row['path'].startswith('data/inputs/'):
                    target.chmod(0o444)
                written += 1
        checked += 1
        total += len(raw)
    graph = next(row for row in manifest['files'] if row['path'] == 'data/graph.json')
    assert graph['sha256'] == manifest['graph_sha256']
    print(json.dumps({'checked_files': checked, 'written_files': written,
                      'original_bytes': total, 'graph_sha256': graph['sha256'],
                      'output': str(output.resolve()), 'check_only': check_only}, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT,
                        help='Output workbench root; default: alongside this script')
    parser.add_argument('--check-only', action='store_true',
                        help='Verify all stored and decoded bytes without writing')
    args = parser.parse_args()
    restore(args.output, args.check_only)
