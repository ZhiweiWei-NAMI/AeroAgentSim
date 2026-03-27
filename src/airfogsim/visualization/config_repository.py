from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import ConfigSnapshot


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class ConfigRepository:
    def __init__(self, base_dir: str = "runtime/aeroagentsim") -> None:
        self.base_dir = Path(base_dir)
        self.config_dir = self.base_dir / "configs"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.latest_pointer = self.base_dir / "latest_config.txt"

    def save_snapshot(self, snapshot: ConfigSnapshot) -> ConfigSnapshot:
        now = _utc_now()
        payload = _model_dump(snapshot)
        payload["source_config_id"] = payload.get("source_config_id") or payload.get("config_id")
        payload["config_id"] = self._generate_config_id()
        payload["created_at"] = payload.get("created_at") or now
        payload["updated_at"] = now

        persisted = ConfigSnapshot(**payload)
        path = self.config_dir / f"{persisted.config_id}.json"
        path.write_text(json.dumps(_model_dump(persisted), indent=2, ensure_ascii=False))
        self.latest_pointer.write_text(persisted.config_id)
        return persisted

    def get_snapshot(self, config_id: str) -> ConfigSnapshot:
        resolved_id = self.resolve_config_id(config_id)
        path = self.config_dir / f"{resolved_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Config snapshot not found: {resolved_id}")
        return ConfigSnapshot(**json.loads(path.read_text()))

    def resolve_config_id(self, config_id: Optional[str]) -> str:
        if config_id in {None, "", "current", "latest", "default"}:
            if not self.latest_pointer.exists():
                raise FileNotFoundError("No saved config snapshot available.")
            return self.latest_pointer.read_text().strip()
        return str(config_id)

    def list_snapshots(self, limit: int = 20) -> List[ConfigSnapshot]:
        snapshots: List[ConfigSnapshot] = []
        for path in sorted(self.config_dir.glob("*.json"), reverse=True):
            snapshots.append(ConfigSnapshot(**json.loads(path.read_text())))
            if len(snapshots) >= limit:
                break
        return snapshots

    def has_snapshots(self) -> bool:
        return any(self.config_dir.glob("*.json"))

    def _generate_config_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"cfg_{stamp}_{uuid.uuid4().hex[:8]}"
