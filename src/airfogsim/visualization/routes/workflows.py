from fastapi import APIRouter, HTTPException
from airfogsim.utils.logging_config import get_logger
from ..models import WorkflowConfig
from ..app import data_service, sim_integration

# 创建路由器
router = APIRouter()

# 设置日志记录
logger = get_logger(__name__)

# 工作流API
@router.get("/")
async def get_workflows():
    try:
        workflows = data_service.get_all_workflows()
        return workflows
    except Exception as e:
        logger.error(f"获取工作流列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{workflow_id}")
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

@router.post("/")
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

@router.delete("/{workflow_id}")
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