from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Any, Optional
import json
import asyncio
from datetime import datetime
import logging
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import Depends, HTTPException, status
import secrets

from .data_service import SimulationDataService
from .real_integration import RealSimulationIntegration
from .models import (
    DroneState, WorkflowConfig, AgentConfig, AirspaceConfig,
    FrequencyConfig, LandingSpotConfig, EnvironmentConfig,
    UserCredentials, AuthResponse
)

# 创建安全依赖
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
# 活跃令牌存储，生产环境应使用Redis等
active_tokens = {}

# 设置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="AirFogSim API", description="无人机仿真可视化系统API")

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化数据服务和仿真集成
data_service = SimulationDataService()
sim_integration = RealSimulationIntegration(data_service)

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket客户端连接，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket客户端断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# 注意：数据模型已移至models.py

# 身份验证API
@app.post("/token", response_model=AuthResponse)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """获取访问令牌"""
    # 验证用户凭据
    if not data_service.verify_user(form_data.username, form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码不正确",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 生成令牌
    token = secrets.token_hex(32)
    # 存储令牌（实际应用中可能需要设置过期时间）
    active_tokens[token] = form_data.username
    
    return {"access_token": token, "token_type": "bearer"}

# 验证令牌的依赖函数
async def get_current_user(token: str = Depends(oauth2_scheme)):
    """验证令牌并返回当前用户"""
    if token not in active_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效令牌或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return active_tokens[token]

# API路由
@app.get("/")
async def root():
    return {"message": "AirFogSim API 可视化系统"}

# 仿真控制API
@app.post("/api/simulation/start")
async def start_simulation():
    try:
        await sim_integration.start_simulation()
        return {"status": "success", "message": "仿真已启动"}
    except Exception as e:
        logger.error(f"启动仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/simulation/pause")
async def pause_simulation():
    try:
        await sim_integration.pause_simulation()
        return {"status": "success", "message": "仿真已暂停"}
    except Exception as e:
        logger.error(f"暂停仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/simulation/resume")
async def resume_simulation():
    try:
        await sim_integration.resume_simulation()
        return {"status": "success", "message": "仿真已恢复"}
    except Exception as e:
        logger.error(f"恢复仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/simulation/reset")
async def reset_simulation():
    try:
        await sim_integration.reset_simulation()
        return {"status": "success", "message": "仿真已重置"}
    except Exception as e:
        logger.error(f"重置仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/simulation/configure")
async def configure_simulation(config: EnvironmentConfig):
    try:
        # 使用model_dump()替代dict()，兼容Pydantic V2
        config_dict = config.model_dump() if hasattr(config, 'model_dump') else config.dict()
        result = await sim_integration.configure_environment(config_dict)
        return result
    except Exception as e:
        logger.error(f"配置仿真环境失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 无人机数据API
@app.get("/api/drones")
async def get_drones():
    try:
        drones = data_service.get_all_drones()
        return drones
    except Exception as e:
        logger.error(f"获取无人机列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/drones/{drone_id}")
async def get_drone(drone_id: str):
    try:
        drone = data_service.get_drone(drone_id)
        if not drone:
            raise HTTPException(status_code=404, detail="未找到指定的无人机")
        return drone
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取无人机数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/drones/{drone_id}/history")
async def get_drone_history(
    drone_id: str, 
    start_time: Optional[float] = Query(None, description="开始时间(仿真时间)"),
    end_time: Optional[float] = Query(None, description="结束时间(仿真时间)"),
    limit: int = Query(100, description="最大记录数")
):
    try:
        history = data_service.get_drone_history(drone_id, start_time, end_time, limit)
        return history
    except Exception as e:
        logger.error(f"获取无人机历史数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/drones/{drone_id}/trajectory")
async def get_drone_trajectory(
    drone_id: str,
    start_time: Optional[float] = Query(None, description="开始时间(仿真时间)"),
    end_time: Optional[float] = Query(None, description="结束时间(仿真时间)"),
    interval: float = Query(1.0, description="采样间隔")
):
    try:
        trajectory = data_service.get_drone_trajectory(drone_id, start_time, end_time, interval)
        return trajectory
    except Exception as e:
        logger.error(f"获取无人机轨迹数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 工作流API
@app.get("/api/workflows")
async def get_workflows():
    try:
        workflows = data_service.get_all_workflows()
        return workflows
    except Exception as e:
        logger.error(f"获取工作流列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    try:
        workflow = data_service.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="未找到指定的工作流")
        return workflow
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取工作流数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/workflows")
async def create_workflow(workflow: WorkflowConfig):
    try:
        # 将参数转换为RealSimulationIntegration.add_workflow需要的格式
        workflow_config = {
            "name": workflow.name,
            "type": workflow.type,
            "agent_id": workflow.agent_id,
            "details": workflow.parameters
        }
        workflow_id = await sim_integration.add_workflow(workflow_config)
        return {"status": "success", "workflow_id": workflow_id}
    except Exception as e:
        logger.error(f"创建工作流失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    try:
        success = await sim_integration.delete_workflow(workflow_id)
        if not success:
            raise HTTPException(status_code=404, detail="未找到指定的工作流")
        return {"status": "success", "message": "工作流已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除工作流失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 智能体API
@app.get("/api/agents")
async def get_agents():
    try:
        agents = data_service.get_all_agents()
        return agents
    except Exception as e:
        logger.error(f"获取智能体列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    try:
        agent = data_service.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="未找到指定的智能体")
        return agent
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取智能体数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/agents")
async def create_agent(agent: AgentConfig):
    try:
        # 将参数转换为RealSimulationIntegration.add_agent需要的格式
        agent_config = {
            "name": agent.name,
            "type": agent.type,
            "position": agent.initial_position,
            "battery": agent.initial_battery,
            "components": agent.components,
            "properties": agent.properties
        }
        agent_id = await sim_integration.add_agent(agent_config)
        return {"status": "success", "agent_id": agent_id}
    except Exception as e:
        logger.error(f"创建智能体失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        success = await sim_integration.delete_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="未找到指定的智能体")
        return {"status": "success", "message": "智能体已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除智能体失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 导入模板配置
from .config import DEFAULT_WORKFLOW_TEMPLATES, DEFAULT_AGENT_TEMPLATES

# 模板和配置API
@app.get("/api/templates/workflows")
async def get_workflow_templates():
    """返回预定义的工作流模板"""
    return DEFAULT_WORKFLOW_TEMPLATES

@app.get("/api/templates/agents")
async def get_agent_templates():
    """获取智能体模板"""
    return DEFAULT_AGENT_TEMPLATES

# WebSocket接口
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("WebSocket连接请求")
    await manager.connect(websocket)
    logger.info("WebSocket连接已建立")
    
    # 创建一个任务来定期检查队列
    queue_check_task = None
    
    # 发送初始状态
    try:
        initial_status = {
            "type": "sim_status",
            "status": sim_integration.simulation_status,
            "time": sim_integration.simulation_time,
            "speed": sim_integration.simulation_speed
        }
        await websocket.send_text(json.dumps(initial_status))
        logger.info(f"已发送初始状态: {initial_status}")
    except Exception as e:
        logger.error(f"发送初始状态失败: {str(e)}")
    
    async def check_update_queue():
        """定期检查更新队列并发送给客户端"""
        update_count = 0
        last_log_time = datetime.now()
        
        try:
            logger.info("开始更新队列检查任务")
            while True:
                try:
                    # 获取队列中的更新
                    updates = sim_integration.get_updates(max_items=20)
                    
                    # 发送所有更新给客户端
                    for update in updates:
                        try:
                            await websocket.send_text(json.dumps(update))
                            update_count += 1
                            
                            # 每100条消息或每30秒记录一次日志
                            current_time = datetime.now()
                            time_diff = (current_time - last_log_time).total_seconds()
                            if update_count % 100 == 0 or time_diff > 30:
                                logger.info(f"WebSocket已发送 {update_count} 条更新")
                                last_log_time = current_time
                                
                        except Exception as e:
                            logger.error(f"发送更新消息失败: {str(e)}")
                            # 如果发送失败，可能是连接已断开
                            return
                    
                    # 如果没有更新，短暂休眠
                    if not updates:
                        await asyncio.sleep(0.1)
                    
                    # 这个短暂停顿确保了其他任务有机会执行
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    logger.error(f"处理更新队列时出错: {str(e)}")
                    # 短暂暂停后继续尝试
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info(f"更新队列检查任务被取消，共发送了 {update_count} 条更新")
            raise
        except Exception as e:
            logger.error(f"更新队列检查任务异常终止: {str(e)}")
    
    try:
        # 启动队列检查任务
        queue_check_task = asyncio.create_task(check_update_queue())
        logger.info("队列检查任务已启动")
        
        # 保持连接打开，处理客户端请求
        while True:
            # 接收并处理客户端消息
            data = await websocket.receive_text()
            message = json.loads(data)
            logger.info(f"收到WebSocket消息: {message.get('type')}")
            
            # 处理客户端命令
            if message.get("type") == "sim_control":
                command = message.get("command")
                logger.info(f"执行仿真控制命令: {command}")
                
                if command == "start":
                    await sim_integration.start_simulation()
                elif command == "pause":
                    await sim_integration.pause_simulation()
                elif command == "resume":
                    await sim_integration.resume_simulation()
                elif command == "reset":
                    await sim_integration.reset_simulation()
                elif command == "set_speed":
                    speed = message.get("speed", 1.0)
                    await sim_integration.set_simulation_speed(speed)
                
                # 发送确认消息
                await websocket.send_text(json.dumps({
                    "type": "sim_control_response",
                    "command": command,
                    "status": "success"
                }))
                
            # 处理配置命令
            elif message.get("type") == "sim_config":
                config_data = message.get("config", {})
                logger.info(f"应用仿真配置: {len(config_data)} 个配置项")
                
                await sim_integration.configure_environment(config_data)
                await websocket.send_text(json.dumps({
                    "type": "sim_config_response",
                    "status": "success",
                    "message": "配置已应用"
                }))
                
    except WebSocketDisconnect:
        logger.info("WebSocket连接断开")
        # 连接断开时取消队列检查任务并断开连接
        if queue_check_task:
            logger.info("正在取消队列检查任务")
            queue_check_task.cancel()
            try:
                await queue_check_task
            except asyncio.CancelledError:
                logger.info("队列检查任务已成功取消")
            except Exception as e:
                logger.error(f"取消队列检查任务时出错: {str(e)}")
        
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket处理中发生错误: {str(e)}")
        # 确保在发生错误时也能清理资源
        if queue_check_task:
            queue_check_task.cancel()
        manager.disconnect(websocket)
