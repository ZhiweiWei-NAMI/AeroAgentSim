"""Installed public resources; no platform repository-root assumptions."""
from importlib.resources import files
from pathlib import Path
import os

def resource_path(*parts: str) -> Path:
    resource = files(__package__).joinpath(*parts)
    path = Path(str(resource))
    if not path.exists():
        raise FileNotFoundError(f"Missing installed AeroRescue resource: {'/'.join(parts)}; install a built wheel or provide the explicit configuration path")
    return path

def skills_path() -> Path:
    configured = os.environ.get("AAS_SKILLS_DIR")
    return Path(configured) if configured else resource_path("skills")

def scenario_path() -> Path:
    configured = os.environ.get("RESCUE_CONFIG")
    return Path(configured) if configured else resource_path("scenarios", "rooftop-flood-companion.yaml")
