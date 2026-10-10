from __future__ import annotations

import json
import math
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import ConfigSnapshot, RegistryReference, RunManifest, SpatialSnapshot


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


class RunRepository:
    ACTIVE_STATUSES = {"created", "starting", "running", "paused"}

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else self._default_base_dir()
        self.runs_dir = self.base_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.latest_pointer = self.base_dir / "latest_run.txt"
        self._lock = threading.RLock()
        self._active_run_id: Optional[str] = None
        self._latest_spatial: Dict[str, Dict[str, Any]] = {}
        self._recent_logs: Dict[str, Dict[str, str]] = {}

    @staticmethod
    def _default_base_dir() -> Path:
        runtime_dir = os.getenv("AEROAGENTSIM_RUNTIME_DIR") or os.getenv("AIRFOGSIM_RUNTIME_DIR")
        if runtime_dir:
            return Path(runtime_dir)

        db_path = os.getenv("AEROAGENTSIM_DB_PATH") or os.getenv("AIRFOGSIM_DB_PATH")
        if db_path:
            return Path(db_path).expanduser().resolve().parent

        project_root = Path(__file__).resolve().parents[3]
        preferred = project_root / "runtime" / "aeroagentsim"
        legacy = project_root / "runtime" / "workbench"
        return preferred if preferred.exists() or not legacy.exists() else legacy

    @property
    def active_run_id(self) -> Optional[str]:
        return self._active_run_id

    def create_run(
        self,
        snapshot: ConfigSnapshot,
        registry_references: Optional[List[RegistryReference]] = None,
    ) -> RunManifest:
        run_id = self._generate_run_id()
        run_dir = self.runs_dir / run_id
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow_states").mkdir(parents=True, exist_ok=True)
        (run_dir / "trajectories").mkdir(parents=True, exist_ok=True)
        (run_dir / "spatial").mkdir(parents=True, exist_ok=True)
        (run_dir / "metrics").mkdir(parents=True, exist_ok=True)

        manifest = RunManifest(
            run_id=run_id,
            config_id=snapshot.config_id or "unknown",
            config_name=snapshot.name,
            coordinate_mode=snapshot.coordinate_mode,
            status="created",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            output_dir=str(run_dir),
            latest_speed=snapshot.simulation_speed,
            simulation_time=0.0,
            registry_references=registry_references or [],
        )

        with self._lock:
            self._active_run_id = run_id
            self._latest_spatial[run_id] = {
                "run_id": run_id,
                "timestamp": 0.0,
                "coordinate_mode": snapshot.coordinate_mode,
                "bounds": {},
                "agents": [],
            }
            self._recent_logs[run_id] = {}
            self._write_manifest(manifest)
            (run_dir / "config_snapshot.json").write_text(
                json.dumps(_json_safe(_model_dump(snapshot)), indent=2, ensure_ascii=False)
            )
            self.latest_pointer.write_text(run_id)

        return manifest

    def get_manifest(self, run_id: str) -> RunManifest:
        resolved_id = self.resolve_run_id(run_id)
        path = self._run_dir(resolved_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Run manifest not found: {resolved_id}")
        return RunManifest(**json.loads(path.read_text()))

    def delete_run(self, run_id: str) -> RunManifest:
        resolved_id = self.resolve_run_id(run_id)
        manifest = self.get_manifest(resolved_id)
        status = str(manifest.status or "").lower()
        if resolved_id == self._active_run_id and status in self.ACTIVE_STATUSES:
            raise RuntimeError("Active run cannot be deleted. Stop/reset it first.")

        run_dir = self._run_dir(resolved_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found: {resolved_id}")

        with self._lock:
            shutil.rmtree(run_dir)
            self._latest_spatial.pop(resolved_id, None)
            self._recent_logs.pop(resolved_id, None)
            if self._active_run_id == resolved_id:
                self._active_run_id = None
            self._refresh_latest_pointer()

        return manifest

    def list_runs(self, limit: int = 50) -> List[RunManifest]:
        manifests: List[RunManifest] = []
        for path in sorted(self.runs_dir.glob("*/manifest.json"), reverse=True):
            manifests.append(RunManifest(**json.loads(path.read_text())))
            if len(manifests) >= limit:
                break
        return manifests

    def update_status(
        self,
        status: str,
        sim_time: float = 0.0,
        speed: float = 1.0,
        run_id: Optional[str] = None,
    ) -> None:
        target_run_id = run_id or self._active_run_id
        if not target_run_id:
            return
        manifest = self.get_manifest(target_run_id)
        manifest.status = status.lower()
        manifest.latest_sim_time = sim_time
        manifest.latest_speed = speed
        manifest.simulation_time = sim_time
        manifest.updated_at = _utc_now()
        if manifest.status in {"running", "paused"} and manifest.started_at is None:
            manifest.started_at = _utc_now()
        if manifest.status in {"completed", "error", "stopped"}:
            manifest.ended_at = _utc_now()
        self._write_manifest(manifest)

    def append_log(self, payload: Dict[str, Any], run_id: Optional[str] = None) -> None:
        target_run_id = run_id or payload.get("run_id") or self._active_run_id
        if not target_run_id:
            return
        payload = dict(payload)
        payload.setdefault("run_id", target_run_id)
        payload.setdefault("recorded_at", _utc_now())
        source = payload.get("source")
        if source:
            self._recent_logs.setdefault(target_run_id, {})[source] = payload.get("message", "")
        self._append_jsonl(self._run_dir(target_run_id) / "logs" / "events.jsonl", payload)

    def record_workflow_state(self, payload: Dict[str, Any]) -> None:
        if not self._active_run_id:
            return
        data = dict(payload)
        data.setdefault("run_id", self._active_run_id)
        data.setdefault("recorded_at", _utc_now())
        self._append_jsonl(
            self._run_dir(self._active_run_id) / "workflow_states" / "states.jsonl",
            data,
        )

    def record_spatial_snapshot(self, payload: Dict[str, Any]) -> None:
        if not self._active_run_id:
            return
        data = dict(payload)
        data.setdefault("run_id", self._active_run_id)
        snapshot = SpatialSnapshot(**data)
        dumped = _json_safe(_model_dump(snapshot))
        run_dir = self._run_dir(self._active_run_id)
        self._latest_spatial[self._active_run_id] = dumped
        (run_dir / "spatial" / "latest.json").write_text(
            json.dumps(dumped, indent=2, ensure_ascii=False)
        )
        self._append_jsonl(run_dir / "spatial" / "history.jsonl", dumped)
        for agent in dumped.get("agents", []):
            point = {
                "run_id": self._active_run_id,
                "timestamp": dumped.get("timestamp", 0.0),
                "agent_id": agent.get("agent_id"),
                "agent_type": agent.get("agent_type"),
                "position": agent.get("position"),
                "display_position": agent.get("display_position"),
                "status": agent.get("status"),
                "current_workflow": agent.get("current_workflow"),
                "current_task": agent.get("current_task"),
                "current_task_id": agent.get("current_task_id"),
            }
            self._append_jsonl(run_dir / "trajectories" / "points.jsonl", point)

    def get_spatial_snapshot(self, run_id: str) -> Dict[str, Any]:
        resolved_id = self.resolve_run_id(run_id)
        if resolved_id in self._latest_spatial:
            return self._latest_spatial[resolved_id]
        path = self._run_dir(resolved_id) / "spatial" / "latest.json"
        if not path.exists():
            manifest = self.get_manifest(resolved_id)
            return {
                "run_id": resolved_id,
                "timestamp": manifest.latest_sim_time,
                "coordinate_mode": manifest.coordinate_mode,
                "bounds": {},
                "agents": [],
            }
        return json.loads(path.read_text())

    def get_logs(self, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self._tail_jsonl(self._run_dir(self.resolve_run_id(run_id)) / "logs" / "events.jsonl", limit)

    def get_recent_logs(self, run_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        resolved_id = self.resolve_run_id(run_id)
        return self.get_logs(resolved_id, limit=limit)

    def get_trajectories(
        self,
        run_id: str,
        agent_id: Optional[str] = None,
        limit: int = 2000,
    ) -> Dict[str, Any]:
        resolved_id = self.resolve_run_id(run_id)
        points = self._tail_jsonl(
            self._run_dir(resolved_id) / "trajectories" / "points.jsonl",
            limit,
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for point in points:
            current_agent_id = point.get("agent_id")
            if agent_id and current_agent_id != agent_id:
                continue
            if current_agent_id not in grouped:
                grouped[current_agent_id] = {
                    "agent_id": current_agent_id,
                    "agent_type": point.get("agent_type"),
                    "points": [],
                }
            grouped[current_agent_id]["points"].append(point)
        manifest = self.get_manifest(resolved_id)
        return {
            "run_id": resolved_id,
            "coordinate_mode": manifest.coordinate_mode,
            "trajectories": list(grouped.values()),
        }

    def get_recent_log_message(self, source: str) -> Optional[str]:
        if not self._active_run_id:
            return None
        return self._recent_logs.get(self._active_run_id, {}).get(source)

    def resolve_run_id(self, run_id: Optional[str]) -> str:
        if run_id in {None, "", "current", "latest"}:
            if self._active_run_id:
                return self._active_run_id
            if self.latest_pointer.exists():
                return self.latest_pointer.read_text().strip()
            raise FileNotFoundError("No run available.")
        return str(run_id)

    def _write_manifest(self, manifest: RunManifest) -> None:
        path = self._run_dir(manifest.run_id) / "manifest.json"
        payload = json.dumps(_json_safe(_model_dump(manifest)), indent=2, ensure_ascii=False)
        temp_path = path.with_suffix(".json.tmp")
        with self._lock:
            temp_path.write_text(payload)
            temp_path.replace(path)

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = _json_safe(payload)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe_payload, ensure_ascii=False) + "\n")

    def _tail_jsonl(self, path: Path, limit: int) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        return [_json_safe(json.loads(line)) for line in lines[-limit:]]

    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def _refresh_latest_pointer(self) -> None:
        remaining_run_ids = sorted(
            path.parent.name for path in self.runs_dir.glob("*/manifest.json")
        )
        if not remaining_run_ids:
            if self.latest_pointer.exists():
                self.latest_pointer.unlink()
            return
        self.latest_pointer.write_text(remaining_run_ids[-1])

    def _generate_run_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"run_{stamp}_{uuid.uuid4().hex[:8]}"
