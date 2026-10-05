"""Rebuild all generated schemas/registries/readable mapping tables offline."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parent
for script in ['build_contract.py','build_semantic_facets.py','write_decisions.py']:
    subprocess.run([sys.executable,str(root/script)],cwd=root,check=True)
