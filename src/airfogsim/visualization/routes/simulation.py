from fastapi import APIRouter, HTTPException

from airfogsim.utils.logging_config import get_logger


router = APIRouter()
logger = get_logger(__name__)
DEPRECATION_DETAIL = (
    "Legacy /api/simulation endpoints are disabled. "
    "Use /api/configs for draft editing and /api/runs for start, pause, resume, and reset."
)


@router.post("/start")
async def start_simulation():
    logger.warning("Rejected deprecated /api/simulation/start request")
    raise HTTPException(status_code=410, detail=DEPRECATION_DETAIL)


@router.post("/pause")
async def pause_simulation():
    logger.warning("Rejected deprecated /api/simulation/pause request")
    raise HTTPException(status_code=410, detail=DEPRECATION_DETAIL)


@router.post("/resume")
async def resume_simulation():
    logger.warning("Rejected deprecated /api/simulation/resume request")
    raise HTTPException(status_code=410, detail=DEPRECATION_DETAIL)


@router.post("/reset")
async def reset_simulation():
    logger.warning("Rejected deprecated /api/simulation/reset request")
    raise HTTPException(status_code=410, detail=DEPRECATION_DETAIL)


@router.post("/configure")
async def configure_simulation():
    logger.warning("Rejected deprecated /api/simulation/configure request")
    raise HTTPException(status_code=410, detail=DEPRECATION_DETAIL)
