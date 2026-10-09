"""Build-time packaging of the authoritative shipped demo resources.

The demo tree lives at ``scenarios/demos/traffic-accident`` (single source of
truth) and is copied verbatim into ``build_lib/aeroagentsim/demo_data/`` so a
wheel is self-contained. No duplicate maintained copy exists in the repo.
"""

from __future__ import annotations

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

DEMO = Path("scenarios") / "demos" / "traffic-accident"


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        if self.dry_run or self.build_lib is None:
            return
        destination = Path(self.build_lib) / "aeroagentsim" / "demo_data" / DEMO.name
        self.mkpath(str(destination))
        for path in sorted(DEMO.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            target = destination / path.relative_to(DEMO)
            if path.is_dir():
                self.mkpath(str(target))
            else:
                self.copy_file(str(path), str(target), preserve_mode=0)


setup(cmdclass={"build_py": build_py})
