"""Copy authoritative scenario resources into the wheel build output.

``scenarios/`` remains the single maintained source tree. Copy complete demo
and template trees, including shared root files and nested assets, into
``build_lib/aeroagentsim/{demo_data,template_data}`` for installed use.
"""

from __future__ import annotations

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

DEMO = Path("scenarios") / "demos" / "traffic-accident"
TEMPLATES = Path("scenarios") / "templates"


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        if self.dry_run or self.build_lib is None:
            return
        package = Path(self.build_lib) / "aeroagentsim"
        for source, destination in (
            (DEMO, package / "demo_data" / DEMO.name),
            (TEMPLATES, package / "template_data"),
        ):
            if not source.is_dir():
                raise FileNotFoundError(f"Missing scenario resource tree: {source}")
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if "__pycache__" in relative.parts or path.suffix in {
                    ".pyc", ".pyo", ".pyd",
                }:
                    continue
                target = destination / relative
                if path.is_dir():
                    self.mkpath(str(target))
                else:
                    self.mkpath(str(target.parent))
                    self.copy_file(str(path), str(target), preserve_mode=0)


setup(cmdclass={"build_py": build_py})
