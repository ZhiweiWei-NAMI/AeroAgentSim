from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from airfogsim.utils.logging_config import get_logger

from ..app import workspace_service
from ..schemas import ConfigSnapshot


router = APIRouter()
logger = get_logger(__name__)


def _is_empty_draft(snapshot: Optional[ConfigSnapshot]) -> bool:
    if snapshot is None:
        return True
    payload = snapshot.model_dump(exclude_none=True) if hasattr(snapshot, "model_dump") else snapshot.dict(exclude_none=True)
    meaningful_keys = {key for key, value in payload.items() if value not in (None, "", [], {}, 0, 0.0)}
    return meaningful_keys.issubset({"name", "coordinate_mode", "simulation_speed"})


@router.get("/{config_id}")
async def get_config(config_id: str):
    try:
        return workspace_service.get_snapshot(config_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to load config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{config_id}")
async def put_config(config_id: str, snapshot: ConfigSnapshot):
    try:
        return workspace_service.save_snapshot(config_id, snapshot)
    except Exception as exc:
        logger.error(f"Failed to save config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{config_id}/graph")
async def get_config_graph(config_id: str):
    try:
        return workspace_service.build_graph(config_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to build graph for config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{config_id}/graph")
async def preview_config_graph(config_id: str, snapshot: ConfigSnapshot):
    try:
        return workspace_service.build_graph_for_snapshot(snapshot)
    except Exception as exc:
        logger.error(f"Failed to preview graph for config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{config_id}/validate")
async def validate_config(config_id: str, snapshot: Optional[ConfigSnapshot] = Body(default=None)):
    try:
        if snapshot is not None and not _is_empty_draft(snapshot):
            return workspace_service.validate_draft(snapshot)
        return workspace_service.validate_snapshot(config_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to validate config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{config_id}/preflight")
async def preflight_config(config_id: str, snapshot: Optional[ConfigSnapshot] = Body(default=None)):
    try:
        if snapshot is not None and not _is_empty_draft(snapshot):
            return workspace_service.preflight_draft(snapshot)
        return workspace_service.preflight_snapshot(config_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to preflight config {config_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
