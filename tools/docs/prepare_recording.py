"""Check the real recording service and create the shared story manifest.

Runs are created, started and operated by the workbench recording stories.
This step never manufactures journals, provider responses or photographs.
"""
from __future__ import annotations
import argparse
import json
import urllib.request
from pathlib import Path


def prepare(base: str, output: Path) -> None:
    with urllib.request.urlopen(base.rstrip('/') + '/v1/templates', timeout=30) as response:
        templates = json.load(response)
    if not templates:
        raise RuntimeError('The recording service returned no templates')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'baseURL': base, 'stories': {}}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    prepare(args.base_url, args.output)
