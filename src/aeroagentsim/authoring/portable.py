"""Portable Studio YAML carries the actual workspace resources, without runs."""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any

FORMAT = "aeroagentsim.studio/v1"
WORKSPACE = "${STUDIO_WORKSPACE}/"
GEOGRAPHY = {"region", "scene", "network", "network_state"}
_RESERVED = {"draft.json", "draft.pending.json", "versions", "__pycache__"}


def resource_path(name: Any) -> Path:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("import.files: nonempty relative resource path required")
    path = Path(name)
    if path.is_absolute() or path.as_posix() != name or any(
        part in {".", ".."} | _RESERVED or part.startswith("validation-")
        for part in path.parts
    ):
        raise ValueError(f"import.files: unsafe or reserved resource path {name!r}")
    return path


def bind_paths(value: Any, source: str, target: str) -> Any:
    """Rebase only absolute paths into the exporting workspace, recursively."""
    if isinstance(value, dict):
        return {key: bind_paths(child, source, target) for key, child in value.items()}
    if isinstance(value, list):
        return [bind_paths(child, source, target) for child in value]
    if isinstance(value, str) and value.startswith(source):
        return target + value[len(source):]
    return value


def bind_resource(name: str, data: bytes, source: str, target: str) -> bytes:
    # Authored configuration can contain absolute resource paths. Generated
    # SUMO XML (including its converter receipt), OSM and binary assets retain
    # their exact source bytes and are not configuration to be rewritten.
    if Path(name).suffix in {".yaml", ".yml", ".json"}:
        return data.replace(source.encode(), target.encode())
    return data


def pack(draft: dict[str, Any], directory: Path) -> dict[str, Any]:
    root = str(directory.resolve()) + "/"
    files = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part in _RESERVED or part.startswith("validation-") for part in relative.parts):
            continue
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"export: resource symlink escapes workspace: {relative}")
        if not path.is_file():
            continue
        name = relative.as_posix()
        resource_path(name)
        data = bind_resource(name, path.read_bytes(), root, WORKSPACE)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                       "base64": base64.b64encode(data).decode("ascii")}
    geography = {key: draft[key] for key in GEOGRAPHY if key in draft}
    if not files and not geography:
        return draft["scenario"]
    return {"format": FORMAT, "scenario": bind_paths(draft["scenario"], root, WORKSPACE),
            "files": files, "geography": geography,
            **({"template": draft["template"]} if "template" in draft else {})}


def unpack(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    if set(document) - {"format", "scenario", "files", "geography", "template"} or not {
        "format", "scenario", "files", "geography"
    } <= document.keys():
        raise ValueError("import: Studio bundle requires scenario, files and geography; unknown keys rejected")
    if document["format"] != FORMAT:
        raise ValueError("import: unsupported Studio bundle format")
    scenario, resources, geography = document["scenario"], document["files"], document["geography"]
    if not isinstance(scenario, dict) or not isinstance(resources, dict) or not isinstance(geography, dict):
        raise TypeError("import: scenario, files and geography must be mappings")
    if geography.keys() - GEOGRAPHY or ("region" in geography) != ("scene" in geography):
        raise ValueError("import.geography: region and scene must be supplied together; unknown keys rejected")
    for key in ("region", "scene", "network"):
        if key in geography and not isinstance(geography[key], dict):
            raise TypeError(f"import.geography.{key}: mapping required")
    if "network" in geography and "region" not in geography:
        raise ValueError("import.geography: a network requires its applied region")
    geography = dict(geography)
    if "network_state" in geography:
        state = geography["network_state"]
        # Existing BE-2 exports used a status object in this same field.
        if isinstance(state, dict) and state.get("status") in {"missing", "stale", "generated"}:
            state = "none" if state["status"] == "missing" else state["status"]
        if not isinstance(state, str) or state not in {"none", "stale", "generated"}:
            raise ValueError("import.geography.network_state: explicit none/stale/generated state required")
        geography["network_state"] = state
    if "template" in document:
        template = document["template"]
        if not isinstance(template, dict) or set(template) != {"id", "knobs"} or not isinstance(template["id"], str) or not template["id"].strip() or not isinstance(template["knobs"], dict):
            raise ValueError("import.template: nonempty id and knob mapping required")
    files = {}
    for name, record in resources.items():
        resource_path(name)
        if not isinstance(record, dict) or set(record) != {"sha256", "base64"} or any(
            not isinstance(record[key], str) for key in record
        ):
            raise ValueError(f"import.files.{name}: sha256 and base64 strings required")
        try:
            data = base64.b64decode(record["base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"import.files.{name}: invalid base64") from exc
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"import.files.{name}: resource digest mismatch")
        files[name] = data
    if "region" in geography and "region.osm.xml" not in files:
        raise ValueError("import.geography: the applied region's OSM crop is missing")
    if "network" in geography and "network.net.xml" not in files:
        raise ValueError("import.geography: the saved SUMO network is missing")
    metadata = {**geography, **({"template": document["template"]} if "template" in document else {})}
    return scenario, files, metadata


def write_resources(files: dict[str, bytes], directory: Path) -> None:
    root = directory.resolve()
    targets = {name: root / resource_path(name) for name in files}
    for name, target in targets.items():
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"import.files: destination escapes workspace: {name}")
        resource_path(target.resolve().relative_to(root).as_posix())
    for name, target in targets.items():
        data = files[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".import-pending")
        pending.write_bytes(bind_resource(name, data, WORKSPACE, str(root) + "/"))
        pending.replace(target)
