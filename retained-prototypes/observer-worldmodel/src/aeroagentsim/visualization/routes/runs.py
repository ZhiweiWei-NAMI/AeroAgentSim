from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from aeroagentsim.utils.logging_config import get_logger

from ..app import data_service, run_repository, sim_integration, workspace_service
from ..schemas import RunStartRequest


router = APIRouter()
logger = get_logger(__name__)


def _run_error_detail(
    *,
    run_id: Optional[str],
    detail: str,
    errors: Optional[list] = None,
    warnings: Optional[list] = None,
    checks: Optional[list] = None,
    recent_logs: Optional[list] = None,
):
    return {
        "detail": detail,
        "run_id": run_id,
        "errors": list(errors or []),
        "warnings": list(warnings or []),
        "checks": list(checks or []),
        "recent_logs": list(recent_logs or []),
    }


def _reconcile_stale_runs() -> None:
    if sim_integration.simulation_status not in {"STOPPED", "ERROR", "COMPLETED"}:
        return
    manifests = run_repository.list_runs(limit=500)
    for manifest in manifests:
        if str(manifest.status or "").lower() not in {"created", "starting", "running", "paused"}:
            continue
        run_repository.update_status(
            "stopped",
            manifest.latest_sim_time,
            manifest.latest_speed,
            run_id=manifest.run_id,
        )


@router.get("")
async def list_runs(limit: int = Query(50, ge=1, le=500)):
    try:
        _reconcile_stale_runs()
        return run_repository.list_runs(limit=limit)
    except Exception as exc:
        logger.error(f"Failed to list runs: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("")
async def create_run(request: RunStartRequest):
    manifest = None
    try:
        config_id = request.config_id or "current"
        snapshot = workspace_service.get_snapshot(config_id)
        validation = workspace_service.validate_snapshot(config_id)
        if not validation.is_valid:
            raise HTTPException(status_code=400, detail=validation.errors)

        if sim_integration.simulation_status != "STOPPED":
            await sim_integration.reset_simulation()

        sim_integration.clear_startup_error()
        data_service.clear_data()
        registry_references = workspace_service.config_compiler.collect_registry_references(snapshot)
        manifest = run_repository.create_run(snapshot, registry_references=registry_references)
        sim_integration.activate_run(manifest)

        compiled_config = workspace_service.compile_snapshot(config_id)
        preflight = workspace_service.preflight_snapshot(config_id)
        if not preflight.is_ready:
            for warning in preflight.warnings:
                run_repository.append_log(
                    {
                        "source": "Preflight",
                        "message": warning,
                        "level": "warning",
                    },
                    run_id=manifest.run_id,
                )
            for error in preflight.errors:
                run_repository.append_log(
                    {
                        "source": "Preflight",
                        "message": error,
                        "level": "error",
                    },
                    run_id=manifest.run_id,
                )
            run_repository.update_status("error", 0.0, snapshot.simulation_speed)
            detail = _run_error_detail(
                run_id=manifest.run_id,
                detail="Simulation preflight failed.",
                errors=preflight.errors,
                warnings=preflight.warnings,
                checks=[check.model_dump() for check in preflight.checks],
                recent_logs=run_repository.get_recent_logs(manifest.run_id, limit=50),
            )
            sim_integration.set_startup_error(detail)
            raise HTTPException(status_code=409, detail=detail)

        await sim_integration.configure_environment(compiled_config)
        start_result = await sim_integration.start_simulation()
        if start_result.get("status") == "error":
            run_repository.update_status("error", 0.0, snapshot.simulation_speed)
            detail = _run_error_detail(
                run_id=manifest.run_id,
                detail=start_result.get("message") or "Simulation setup failed.",
                errors=[start_result.get("message") or "Simulation setup failed."],
                recent_logs=run_repository.get_recent_logs(manifest.run_id, limit=50),
            )
            sim_integration.set_startup_error(detail)
            raise HTTPException(status_code=500, detail=detail)

        run_repository.update_status("starting", 0.0, snapshot.simulation_speed)
        sim_integration.clear_startup_error()
        return run_repository.get_manifest(manifest.run_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to create run: {exc}")
        if manifest is not None:
            run_repository.append_log(
                {
                    "source": "RunController",
                    "message": f"Unexpected startup exception: {exc}",
                    "level": "error",
                },
                run_id=manifest.run_id,
            )
            run_repository.update_status("error", 0.0, snapshot.simulation_speed if 'snapshot' in locals() else 1.0)
            detail = _run_error_detail(
                run_id=manifest.run_id,
                detail="Unexpected simulation startup failure.",
                errors=[str(exc)],
                recent_logs=run_repository.get_recent_logs(manifest.run_id, limit=50),
            )
            sim_integration.set_startup_error(detail)
            raise HTTPException(status_code=500, detail=detail)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{run_id}/status")
async def get_run_status(run_id: str):
    try:
        _reconcile_stale_runs()
        return run_repository.get_manifest(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch run status {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{run_id}/pause")
async def pause_run(run_id: str):
    try:
        resolved_run_id = run_repository.resolve_run_id(run_id)
        if resolved_run_id != run_repository.active_run_id:
            raise HTTPException(status_code=409, detail="Only the active run can be paused.")
        result = await sim_integration.pause_simulation()
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to pause run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{run_id}/resume")
async def resume_run(run_id: str):
    try:
        resolved_run_id = run_repository.resolve_run_id(run_id)
        if resolved_run_id != run_repository.active_run_id:
            raise HTTPException(status_code=409, detail="Only the active run can be resumed.")
        return await sim_integration.resume_simulation()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to resume run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{run_id}/reset")
async def reset_run(run_id: str):
    try:
        resolved_run_id = run_repository.resolve_run_id(run_id)
        if resolved_run_id != run_repository.active_run_id:
            raise HTTPException(status_code=409, detail="Only the active run can be reset.")
        return await sim_integration.reset_simulation()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to reset run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{run_id}")
async def delete_run(run_id: str):
    try:
        manifest = run_repository.delete_run(run_id)
        return {
            "status": "deleted",
            "run_id": manifest.run_id,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to delete run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    limit: int = Query(200, ge=1, le=5000),
):
    try:
        return run_repository.get_logs(run_id, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch logs for run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{run_id}/trajectories")
async def get_run_trajectories(
    run_id: str,
    agent_id: Optional[str] = Query(None),
    limit: int = Query(2000, ge=1, le=20000),
):
    try:
        return run_repository.get_trajectories(run_id, agent_id=agent_id, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch trajectories for run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{run_id}/spatial")
async def get_run_spatial(run_id: str):
    try:
        return run_repository.get_spatial_snapshot(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to fetch spatial snapshot for run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
