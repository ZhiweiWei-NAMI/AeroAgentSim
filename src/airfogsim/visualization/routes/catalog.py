from fastapi import APIRouter, HTTPException, Query

from airfogsim.utils.logging_config import get_logger

from ..app import catalog_service


router = APIRouter()
logger = get_logger(__name__)


@router.get("/agents")
async def get_agent_catalog(scope: str = Query("all")):
    try:
        return list(catalog_service.get_agents(scope=scope))
    except Exception as exc:
        logger.error(f"Failed to load agent catalog: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/components")
async def get_component_catalog():
    try:
        return list(catalog_service.get_components())
    except Exception as exc:
        logger.error(f"Failed to load component catalog: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/tasks")
async def get_task_catalog(scope: str = Query("all")):
    try:
        return list(catalog_service.get_tasks(scope=scope))
    except Exception as exc:
        logger.error(f"Failed to load task catalog: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/workflows")
async def get_workflow_catalog(scope: str = Query("all")):
    try:
        return list(catalog_service.get_workflows(scope=scope))
    except Exception as exc:
        logger.error(f"Failed to load workflow catalog: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/compatibility")
async def get_catalog_compatibility(scope: str = Query("all")):
    try:
        return catalog_service.get_compatibility(scope=scope)
    except Exception as exc:
        logger.error(f"Failed to load compatibility matrix: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
