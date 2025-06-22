from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from airfogsim.utils.logging_config import get_logger
from fastapi import WebSocket, WebSocketDisconnect
from .data_service import SimulationDataService
from .real_integration import RealSimulationIntegration
from .ws_manager import ConnectionManager

# 设置日志记录
logger = get_logger(__name__)

# 创建FastAPI应用
app = FastAPI(title="AirFogSim API", description="无人机仿真可视化系统API")

# 初始化数据服务和仿真集成
data_service = SimulationDataService()
sim_integration = RealSimulationIntegration(data_service)
manager = ConnectionManager()

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录 - 使用/static路径而不是根路径
app.mount("/static", StaticFiles(directory="frontend/public"), name="static")

# API路由
@app.get("/")
async def root():
    return {"message": "AirFogSim API 可视化系统"}

# 导入并包含所有路由模块
from .routes import simulation, entities, workflows, agents, templates, traffic
from .websocket import ws_handler

# 注册路由
app.include_router(simulation.router, prefix="/api/simulation", tags=["simulation"])
app.include_router(entities.router, prefix="/api", tags=["entities"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(traffic.router, prefix="/api/traffic", tags=["traffic"])
# 注册WebSocket路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_handler.websocket_endpoint(websocket)