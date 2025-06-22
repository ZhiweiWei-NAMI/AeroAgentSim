from fastapi import APIRouter
from airfogsim.utils.logging_config import get_logger
from ..config import DEFAULT_WORKFLOW_TEMPLATES, DEFAULT_AGENT_TEMPLATES

# 设置日志记录
logger = get_logger(__name__)

# 创建路由器
router = APIRouter()

# 模板和配置API
@router.get("/workflows")
async def get_workflow_templates():
    """返回预定义的工作流模板"""
    return DEFAULT_WORKFLOW_TEMPLATES

@router.get("/agents")
async def get_agent_templates():
    """获取智能体模板"""
    return DEFAULT_AGENT_TEMPLATES