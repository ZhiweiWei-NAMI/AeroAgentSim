from fastapi import APIRouter, HTTPException
from airfogsim.utils.logging_config import get_logger
from ..models import EnvironmentConfig
from ..app import sim_integration

# 创建路由器
router = APIRouter()

# 设置日志记录
logger = get_logger(__name__)

# 仿真控制API
@router.post("/start")
async def start_simulation():
    try:
        await sim_integration.start_simulation()
        return {"status": "success", "message": "仿真已启动"}
    except Exception as e:
        logger.error(f"启动仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/pause")
async def pause_simulation():
    try:
        await sim_integration.pause_simulation()
        return {"status": "success", "message": "仿真已暂停"}
    except Exception as e:
        logger.error(f"暂停仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/resume")
async def resume_simulation():
    try:
        await sim_integration.resume_simulation()
        return {"status": "success", "message": "仿真已恢复"}
    except Exception as e:
        logger.error(f"恢复仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset")
async def reset_simulation():
    try:
        await sim_integration.reset_simulation()
        return {"status": "success", "message": "仿真已重置"}
    except Exception as e:
        logger.error(f"重置仿真失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/configure")
async def configure_simulation(config: EnvironmentConfig):
    try:
        # 使用model_dump()替代dict()，兼容Pydantic V2
        config_dict = config.model_dump() if hasattr(config, 'model_dump') else config.dict()
        result = await sim_integration.configure_environment(config_dict)
        return result
    except Exception as e:
        logger.error(f"配置仿真环境失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))