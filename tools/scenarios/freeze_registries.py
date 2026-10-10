"""Freeze original compile selections without losing scenario-owned contracts.

Run from the repository root with an installed platform:
  python tools/scenarios/freeze_registries.py --source-ref REF \
      --aerograph-root /path/to/AeroGraph

REF must contain the authored scenarios before their snapshot conversion.
Only registry blocks change; entities, engine configuration and inputs stay put.
The source checkout is read-only and is unnecessary after generation.
"""

from __future__ import annotations

import argparse
import copy
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.integrations.aerograph import Policy, Selection, compile_registry
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader

SCENARIOS = {
    "p1-slice.yaml": "motion",
    "p1-scale.yaml": "scale",
    "p1-spectrum.yaml": "spectrum",
    "predicates-demo.yaml": "predicates",
    "visual/p1-slice-city.yaml": "motion",
    "visual/p1-scale-city.yaml": "scale",
    "agents/llm-dispatch.yaml": "motion",
}


def original(root: Path, ref: str, name: str) -> dict[str, Any]:
    source = subprocess.run(
        ["git", "show", f"{ref}:scenarios/{name}"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return yaml.load(source, Loader=UniqueLoader)["registry"]


def selection(spec: dict[str, Any]) -> Selection:
    return Selection(
        tuple(spec["types"]),
        tuple(spec["fields"]) if "fields" in spec else None,
        tuple(spec["relations"]) if "relations" in spec else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--aerograph-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    directory = root / "scenarios/registry"
    directory.mkdir(exist_ok=True)
    recipes: dict[str, dict[str, Any]] = {}
    replacements: list[tuple[Path, str]] = []
    for name, profile in SCENARIOS.items():
        registry = original(root, args.source_ref, name)
        spec = registry.pop("compile")
        prior = recipes.get(profile)
        if prior is not None and spec != prior:
            raise ValueError(f"{name}: shared snapshot selection differs")
        if prior is None:
            compile_registry(
                args.aerograph_root, selection(spec), Policy(**spec.get("policy", {}))
            ).write_snapshot(directory / f"{profile}.snapshot.json")
            recipes[profile] = spec
        path = root / "scenarios" / name
        snapshot = directory / f"{profile}.snapshot.json"
        registry["snapshot"] = Path(os.path.relpath(snapshot, path.parent)).as_posix()
        text = path.read_text()
        before, rest = text.split("\nentities:", 1)
        prefix = before.split("\nregistry:", 1)[0]
        # Remove the obsolete authored-schema disclaimer from the conversion.
        prefix = "\n".join(line for line in prefix.splitlines() if not line.startswith(
            ("# Public profile:", "# These overlays")
        ))
        updated = prefix + "\n" + yaml.safe_dump({"registry": registry}, sort_keys=False)
        updated += "entities:" + rest
        # Validate every complete scenario before changing any scenario file.
        load_scenario(yaml.load(updated, Loader=UniqueLoader), base=path.parent)
        replacements.append((path, updated))
    # Weather model tests use the original motion selection extended by wind.
    weather = copy.deepcopy(recipes["motion"])
    weather["types"].append("oo:WindField")
    weather["fields"].append("oo:digital_twin.wind.horizontalVelocity")
    compile_registry(
        args.aerograph_root, selection(weather), Policy(**weather.get("policy", {}))
    ).write_snapshot(directory / "motion-weather.snapshot.json")
    for path, updated in replacements:
        path.write_text(updated)
        print(path.relative_to(root))


if __name__ == "__main__":
    main()
