from fastapi import APIRouter, HTTPException
from airfogsim.utils.logging_config import get_logger
from ..models import AgentConfig
from ..app import data_service, sim_integration

# 设置日志记录
logger = get_logger(__name__)

# 创建路由器
router = APIRouter()

# 智能体API
@router.get("/")
async def get_agents():
    try:
        agents = data_service.get_all_agents()
        return agents
    except Exception as e:
        logger.error(f"获取智能体列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{agent_id}")
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

@router.post("/")
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

@router.delete("/{agent_id}")
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