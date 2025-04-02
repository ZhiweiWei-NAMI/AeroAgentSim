"""
默认模板配置，包含工作流模板和智能体模板
"""
import importlib

def _get_workflow_templates():
    """动态获取所有工作流模板"""
    try:
        from airfogsim.workflow import get_workflow_descriptions
        workflow_classes = get_workflow_descriptions()
        
        templates = []
        for wf in workflow_classes:
            # 从属性模板中提取参数
            parameters = []
            if hasattr(wf, 'properties') and wf['properties']:
                parameters = list(wf['properties'].keys())
            
            templates.append({
                "id": wf['id'].lower().replace('workflow', ''),
                "name": wf['name'],
                "description": wf['description'],
                "parameters": parameters
            })
        
        return templates
    except (ImportError, AttributeError):
        # 如果无法导入或出现错误，返回默认模板
        return [
            {
                "id": "inspection",
                "name": "巡检工作流",
                "description": "按指定路径点进行巡视检查",
                "parameters": ["inspection_points", "target_altitude"]
            },
            {
                "id": "charging",
                "name": "充电工作流",
                "description": "飞往充电站并充电",
                "parameters": ["charging_station", "target_charge_level"]
            }
        ]

def _get_agent_templates():
    """动态获取所有代理模板"""
    try:
        from airfogsim.agent import get_agent_descriptions
        agent_classes = get_agent_descriptions()
        
        templates = []
        for agent in agent_classes:
            templates.append({
                "id": agent['id'].lower().replace('agent', ''),
                "name": agent['name'],
                "description": agent['description'],
                "components": ["MoveTo", "Charging", "Compute"]  # 默认组件，实际应从agent中获取
            })
        
        return templates
    except (ImportError, AttributeError):
        # 如果无法导入或出现错误，返回默认模板
        return [
            {
                "id": "drone",
                "name": "无人机",
                "description": "标准无人机智能体",
                "components": ["MoveTo", "Charging", "Compute"]
            },
            {
                "id": "ground_station",
                "name": "地面站",
                "description": "固定地面控制站",
                "components": ["Charging", "Compute"]
            }
        ]

# 动态生成模板
DEFAULT_WORKFLOW_TEMPLATES = _get_workflow_templates()
DEFAULT_AGENT_TEMPLATES = _get_agent_templates()