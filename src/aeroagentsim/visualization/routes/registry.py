from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from aeroagentsim.utils.logging_config import get_logger

from ..app import registry_service


router = APIRouter()
logger = get_logger(__name__)


@router.get("/{kind}")
async def list_registry_definitions(
    kind: str,
    definition_id: Optional[str] = Query(None),
):
    try:
        definitions = registry_service.list_definitions(kind, definition_id=definition_id)
        return [
            {
                **(
                    definition.model_dump()
                    if hasattr(definition, "model_dump")
                    else definition.dict()
                ),
                "usage": registry_service.get_usage(kind, definition.id, definition.version),
            }
            for definition in definitions
        ]
    except Exception as exc:
        logger.error(f"Failed to list registry definitions for {kind}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{kind}")
async def create_registry_definition(kind: str, payload: Dict[str, Any]):
    try:
        return registry_service.save_definition(kind, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to create registry definition for {kind}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{kind}/{definition_id}")
async def get_registry_definition(
    kind: str,
    definition_id: str,
    version: Optional[str] = Query(None),
):
    try:
        definition = registry_service.get_definition(kind, definition_id, version=version)
        return {
            **(
                definition.model_dump()
                if hasattr(definition, "model_dump")
                else definition.dict()
            ),
            "usage": registry_service.get_usage(kind, definition.id, definition.version),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to get registry definition {kind}/{definition_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{kind}/{definition_id}")
async def update_registry_definition(
    kind: str,
    definition_id: str,
    payload: Dict[str, Any],
):
    try:
        payload = dict(payload)
        payload["id"] = definition_id
        return registry_service.save_definition(kind, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to update registry definition {kind}/{definition_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{kind}/{definition_id}")
async def delete_registry_definition(
    kind: str,
    definition_id: str,
    version: Optional[str] = Query(None),
):
    try:
        registry_service.delete_definition(kind, definition_id, version=version)
        return {"status": "deleted"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to delete registry definition {kind}/{definition_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{kind}/{definition_id}/validate")
async def validate_registry_definition(
    kind: str,
    definition_id: str,
    payload: Optional[Dict[str, Any]] = None,
    version: Optional[str] = Query(None),
):
    try:
        if payload is None:
            payload = registry_service.get_definition(kind, definition_id, version=version)
        else:
            payload = dict(payload)
            payload["id"] = definition_id
            if version and not payload.get("version"):
                payload["version"] = version
        return registry_service.validate_definition(kind, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to validate registry definition {kind}/{definition_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
