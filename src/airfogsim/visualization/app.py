import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from airfogsim.utils.logging_config import get_logger
from fastapi import WebSocket
from .data_service import SimulationDataService
from .catalog_service import CatalogService
from .config_compiler import ConfigCompiler
from .config_repository import ConfigRepository
from .real_integration import RealSimulationIntegration
from .registry_service import RegistryService
from .run_repository import RunRepository
from .workspace_service import WorkspaceService
from .ws_manager import ConnectionManager
from .schemas import HealthResponse

# 设置日志记录
logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
DB_PATH = Path(
    os.getenv(
        "AEROAGENTSIM_DB_PATH",
        str(PROJECT_ROOT / "runtime" / "aeroagentsim" / "lowspace_sim.db"),
    )
)

# 创建FastAPI应用
app = FastAPI(
    title="AeroAgentSim API",
    description="AeroAgentSim developer workbench API",
)

# 初始化数据服务和仿真集成
data_service = SimulationDataService(db_path=DB_PATH)
registry_service = RegistryService()
catalog_service = CatalogService(registry_service=registry_service)
config_repository = ConfigRepository()
config_compiler = ConfigCompiler(catalog_service)
workspace_service = WorkspaceService(
    catalog_service,
    config_repository,
    config_compiler,
)
run_repository = RunRepository()
sim_integration = RealSimulationIntegration(data_service, run_repository=run_repository)
manager = ConnectionManager()
LOCAL_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8002",
    "http://127.0.0.1:8002",
]

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录 - 使用/static路径而不是根路径
if FRONTEND_PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_PUBLIC_DIR)), name="static")
else:
    logger.warning("Static frontend directory not found: %s", FRONTEND_PUBLIC_DIR)

# API路由
@app.get("/")
async def root():
    return {"message": "AeroAgentSim API developer workbench"}


@app.get("/api/health")
async def health() -> HealthResponse:
    db_writable = False
    if data_service.conn is not None:
        try:
            data_service.conn.execute("SELECT 1")
            db_writable = os.access(Path(data_service.db_path).parent, os.W_OK)
        except sqlite3.Error:
            db_writable = False

    active_run_id = (
        run_repository.active_run_id
        if sim_integration.simulation_status in {"RUNNING", "PAUSED"}
        else None
    )
    return HealthResponse(
        backend_available=True,
        db_writable=db_writable,
        simulation_status=sim_integration.simulation_status,
        active_run_id=active_run_id,
        recent_startup_error=sim_integration.last_startup_error,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/api/runtime/reset")
async def reset_runtime():
    result = await sim_integration.reset_simulation()
    data_service.clear_data()
    return {
        **result,
        "simulation_status": sim_integration.simulation_status,
        "active_run_id": None,
    }

# 导入并包含所有路由模块
from .routes import (
    agents,
    catalog,
    configs,
    entities,
    registry,
    runs,
    simulation,
    templates,
    traffic,
    workflows,
)
from .websocket import ws_handler

# 注册路由
app.include_router(simulation.router, prefix="/api/simulation", tags=["simulation"])
app.include_router(entities.router, prefix="/api", tags=["entities"])
app.include_router(catalog.router, prefix="/api/catalog", tags=["catalog"])
app.include_router(registry.router, prefix="/api/registry", tags=["registry"])
app.include_router(configs.router, prefix="/api/configs", tags=["configs"])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(traffic.router, prefix="/api/traffic", tags=["traffic"])
# 注册WebSocket路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_handler.websocket_endpoint(websocket)
