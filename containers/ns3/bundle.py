"""Assemble a small Ubuntu-24.04-derived rootfs without a registry download."""

import os
import re
import shutil
import subprocess
from pathlib import Path

root = Path("/out")
(root / "opt/aeroagentsim/bin").mkdir(parents=True, exist_ok=True)
(root / "opt/aeroagentsim/lib").mkdir(parents=True, exist_ok=True)
(root / "tmp").mkdir(exist_ok=True)
os.chmod(root / "tmp", 0o1777)
provider = Path("/opt/ns-3.48/build/scratch/ns3.48-aero-ns3-provider-optimized")
shutil.copy2(provider, root / "opt/aeroagentsim/bin/ns3-provider")
libs = list(Path("/opt/ns-3.48/build/lib").glob("*.so"))
for lib in libs:
    shutil.copy2(lib, root / "opt/aeroagentsim/lib" / lib.name)
(root / "usr/bin").mkdir(parents=True, exist_ok=True)
shutil.copy2(Path("/usr/bin/python3").resolve(), root / "usr/bin/python3")
stdlib = Path("/usr/lib/python3.12")
shutil.copytree(
    stdlib,
    root / "usr/lib/python3.12",
    ignore=shutil.ignore_patterns("__pycache__", "test", "tests"),
)
# Copy every dynamic dependency of the binary, ns-3 libs and stdlib extensions.
# Preserve the loader's path, dereference libraries so no links dangle.
candidates = [
    provider,
    Path("/usr/bin/python3"),
    *libs,
    *stdlib.glob("lib-dynload/*.so"),
]
for candidate in candidates:
    output = subprocess.check_output(["ldd", str(candidate)], text=True)
    if "not found" in output:
        raise RuntimeError(f"missing dynamic dependency for {candidate}: {output}")
    for match in re.finditer(r"(?:=>\s+)?(/[^\s()]+)", output):
        source = Path(match.group(1))
        if str(source).startswith("/opt/ns-3.48/"):
            continue  # supplied by LD_LIBRARY_PATH at runtime
        destination = root / source.relative_to("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.resolve(), destination)
