"""Include the canonical skill directories and scenarios in built wheels."""
from pathlib import Path
from shutil import copytree
from setuptools import setup
from setuptools.command.build_py import build_py

class BuildResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / "aerorescue" / "resources"
        for name in ("skills", "scenarios"):
            copytree(root / name, destination / name, dirs_exist_ok=True)

setup(cmdclass={"build_py": BuildResources})
