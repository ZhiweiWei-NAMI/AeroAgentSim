"""
AirFogSim类检查工具

该模块提供工具函数，用于检查系统中已实现的各种类及其关键信息。
主要功能包括：
1. 检查所有已注册的类
2. 根据需求查找兼容的类
3. 展示类的关键信息

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import importlib
from typing import List, Dict, Any, Optional, Set, Type, Tuple
from tabulate import tabulate
import textwrap
import inspect

from airfogsim.core.agent import Agent
from airfogsim.core.component import Component
from airfogsim.core.task import Task
from airfogsim.core.workflow import Workflow
from airfogsim.core.environment import Environment
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def _wrap_text(text, width=60):
    """将长文本按指定宽度换行"""
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width))

def check_all_classes(env: Environment) -> None:
    """
    检查系统中所有已注册的类
    
    Args:
        env: 仿真环境
    """
    print("\n" + "="*80)
    print("AirFogSim系统类检查工具")
    print("="*80)
    
    check_agent_classes(env)
    check_component_classes(env)
    check_task_classes(env)
    check_workflow_classes(env)

def check_agent_classes(env: Environment) -> None:
    """
    检查系统中所有已注册的代理类
    
    Args:
        env: 仿真环境
    """
    print("\n" + "-"*80)
    print("代理(Agent)类检查")
    print("-"*80)
    
    agent_classes = env.agent_manager.get_all_agent_classes()
    
    if not agent_classes:
        print("未找到任何代理类")
        return
    
    agent_info = []
    for agent_class in agent_classes:
        # 获取代理类支持的状态
        states = set()
        for cls in agent_class.__mro__:
            if hasattr(cls, '_state_templates'):
                states.update(cls._state_templates.keys())
        
        # 获取代理类描述
        description = agent_class.get_description() if hasattr(agent_class, 'get_description') else agent_class.__doc__ or ""
        
        agent_info.append({
            'name': agent_class.__name__,
            'description': _wrap_text(description),
            'states': ", ".join(sorted(states)),
            'module': agent_class.__module__
        })
    
    # 按名称排序
    agent_info.sort(key=lambda x: x['name'])
    
    # 使用tabulate打印表格
    headers = ["代理类名", "描述", "支持的状态", "模块"]
    table_data = [[info['name'], info['description'], info['states'], info['module']] for info in agent_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(agent_info)} 个代理类")

def check_component_classes(env: Environment) -> None:
    """
    检查系统中所有已注册的组件类
    
    Args:
        env: 仿真环境
    """
    print("\n" + "-"*80)
    print("组件(Component)类检查")
    print("-"*80)
    
    component_classes = env.component_manager.get_all_component_classes()
    
    if not component_classes:
        print("未找到任何组件类")
        return
    
    component_info = []
    for component_class in component_classes:
        # 获取组件类产生的指标和监控的状态
        produced_metrics = getattr(component_class, 'PRODUCED_METRICS', [])
        monitored_states = getattr(component_class, 'MONITORED_STATES', [])
        
        # 获取组件类描述
        description = component_class.__doc__ or f"{component_class.__name__} 组件"
        
        component_info.append({
            'name': component_class.__name__,
            'description': _wrap_text(description),
            'produced_metrics': ", ".join(produced_metrics),
            'monitored_states': ", ".join(monitored_states),
            'module': component_class.__module__
        })
    
    # 按名称排序
    component_info.sort(key=lambda x: x['name'])
    
    # 使用tabulate打印表格
    headers = ["组件类名", "描述", "产生的指标", "监控的状态", "模块"]
    table_data = [[info['name'], info['description'], info['produced_metrics'], info['monitored_states'], info['module']] for info in component_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(component_info)} 个组件类")

def check_task_classes(env: Environment) -> None:
    """
    检查系统中所有已注册的任务类
    
    Args:
        env: 仿真环境
    """
    print("\n" + "-"*80)
    print("任务(Task)类检查")
    print("-"*80)
    
    task_classes = env.task_manager.get_all_task_classes()
    
    if not task_classes:
        print("未找到任何任务类")
        return
    
    task_info = []
    for task_class in task_classes:
        # 获取任务类所需的指标和产生的状态
        necessary_metrics = getattr(task_class, 'NECESSARY_METRICS', [])
        produced_states = getattr(task_class, 'PRODUCED_STATES', [])
        
        # 获取任务类描述
        description = task_class.__doc__ or f"{task_class.__name__} 任务"
        
        task_info.append({
            'name': task_class.__name__,
            'description': _wrap_text(description),
            'necessary_metrics': ", ".join(necessary_metrics),
            'produced_states': ", ".join(produced_states),
            'module': task_class.__module__
        })
    
    # 按名称排序
    task_info.sort(key=lambda x: x['name'])
    
    # 使用tabulate打印表格
    headers = ["任务类名", "描述", "所需指标", "产生的状态", "模块"]
    table_data = [[info['name'], info['description'], info['necessary_metrics'], info['produced_states'], info['module']] for info in task_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(task_info)} 个任务类")

def check_workflow_classes(env: Environment) -> None:
    """
    检查系统中所有已注册的工作流类
    
    Args:
        env: 仿真环境
    """
    print("\n" + "-"*80)
    print("工作流(Workflow)类检查")
    print("-"*80)
    
    # 从airfogsim.workflow模块获取所有工作流类
    import airfogsim.workflow
    workflow_classes = airfogsim.workflow.get_all_workflow_classes()
    
    if not workflow_classes:
        print("未找到任何工作流类")
        return
    
    workflow_info = []
    for workflow_class in workflow_classes:
        # 获取工作流类的属性模板
        property_templates = workflow_class.get_property_templates() if hasattr(workflow_class, 'get_property_templates') else {}
        
        # 获取工作流类描述
        description = workflow_class.get_description() if hasattr(workflow_class, 'get_description') else workflow_class.__doc__ or ""
        
        # 获取工作流类的创建函数
        create_function = None
        for name, obj in inspect.getmembers(workflow_class.__module__):
            if (inspect.isfunction(obj) and 
                name.startswith('create_') and 
                name.endswith('_workflow') and
                workflow_class.__name__ in name):
                create_function = name
                break
        
        workflow_info.append({
            'name': workflow_class.__name__,
            'description': _wrap_text(description),
            'properties': ", ".join(property_templates.keys()),
            'create_function': create_function or "无",
            'module': workflow_class.__module__
        })
    
    # 按名称排序
    workflow_info.sort(key=lambda x: x['name'])
    
    # 使用tabulate打印表格
    headers = ["工作流类名", "描述", "属性", "创建函数", "模块"]
    table_data = [[info['name'], info['description'], info['properties'], info['create_function'], info['module']] for info in workflow_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(workflow_info)} 个工作流类")

def find_compatible_agents(env: Environment, required_states: List[str]) -> None:
    """
    查找支持指定状态的代理类
    
    Args:
        env: 仿真环境
        required_states: 需要的状态列表
    """
    print("\n" + "-"*80)
    print(f"查找支持以下状态的代理类: {', '.join(required_states)}")
    print("-"*80)
    
    compatible_agents = env.agent_manager.find_compatible_agents(required_states)
    
    if not compatible_agents:
        print("未找到兼容的代理类")
        return
    
    agent_info = []
    for agent_class, score in compatible_agents:
        # 获取代理类支持的状态
        states = set()
        for cls in agent_class.__mro__:
            if hasattr(cls, '_state_templates'):
                states.update(cls._state_templates.keys())
        
        # 获取代理类描述
        description = agent_class.get_description() if hasattr(agent_class, 'get_description') else agent_class.__doc__ or ""
        
        agent_info.append({
            'name': agent_class.__name__,
            'description': _wrap_text(description),
            'states': ", ".join(sorted(states)),
            'compatibility_score': f"{score:.2f}",
            'module': agent_class.__module__
        })
    
    # 使用tabulate打印表格
    headers = ["代理类名", "描述", "支持的状态", "兼容性分数", "模块"]
    table_data = [[info['name'], info['description'], info['states'], info['compatibility_score'], info['module']] for info in agent_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(agent_info)} 个兼容的代理类")

def find_compatible_components(env: Environment, agent: Agent, required_metrics: Optional[List[str]] = None) -> None:
    """
    查找与代理兼容的组件类
    
    Args:
        env: 仿真环境
        agent: 代理
        required_metrics: 需要的指标列表（可选）
    """
    print("\n" + "-"*80)
    print(f"查找与代理 {agent.id} 兼容的组件类")
    if required_metrics:
        print(f"需要的指标: {', '.join(required_metrics)}")
    print("-"*80)
    
    compatible_components = env.component_manager.find_compatible_components(agent, required_metrics)
    
    if not compatible_components:
        print("未找到兼容的组件类")
        return
    
    component_info = []
    for component_class, score in compatible_components:
        # 获取组件类产生的指标和监控的状态
        produced_metrics = getattr(component_class, 'PRODUCED_METRICS', [])
        monitored_states = getattr(component_class, 'MONITORED_STATES', [])
        
        # 获取组件类描述
        description = component_class.__doc__ or f"{component_class.__name__} 组件"
        
        component_info.append({
            'name': component_class.__name__,
            'description': _wrap_text(description),
            'produced_metrics': ", ".join(produced_metrics),
            'monitored_states': ", ".join(monitored_states),
            'compatibility_score': f"{score:.2f}",
            'module': component_class.__module__
        })
    
    # 使用tabulate打印表格
    headers = ["组件类名", "描述", "产生的指标", "监控的状态", "兼容性分数", "模块"]
    table_data = [[info['name'], info['description'], info['produced_metrics'], info['monitored_states'], info['compatibility_score'], info['module']] for info in component_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(component_info)} 个兼容的组件类")

def find_compatible_tasks(env: Environment, agent: Agent, workflow: Optional[Workflow] = None, required_states: Optional[List[str]] = None) -> None:
    """
    查找与代理和工作流兼容的任务类
    
    Args:
        env: 仿真环境
        agent: 代理
        workflow: 工作流（可选）
        required_states: 需要的状态列表（可选）
    """
    print("\n" + "-"*80)
    print(f"查找与代理 {agent.id} 兼容的任务类")
    if workflow:
        print(f"工作流: {workflow.id}")
    if required_states:
        print(f"需要的状态: {', '.join(required_states)}")
    print("-"*80)
    
    compatible_tasks = env.task_manager.find_compatible_tasks(agent, workflow, required_states)
    
    if not compatible_tasks:
        print("未找到兼容的任务类")
        return
    
    task_info = []
    for task_class, score in compatible_tasks:
        # 获取任务类所需的指标和产生的状态
        necessary_metrics = getattr(task_class, 'NECESSARY_METRICS', [])
        produced_states = getattr(task_class, 'PRODUCED_STATES', [])
        
        # 获取任务类描述
        description = task_class.__doc__ or f"{task_class.__name__} 任务"
        
        task_info.append({
            'name': task_class.__name__,
            'description': _wrap_text(description),
            'necessary_metrics': ", ".join(necessary_metrics),
            'produced_states': ", ".join(produced_states),
            'compatibility_score': f"{score:.2f}",
            'module': task_class.__module__
        })
    
    # 使用tabulate打印表格
    headers = ["任务类名", "描述", "所需指标", "产生的状态", "兼容性分数", "模块"]
    table_data = [[info['name'], info['description'], info['necessary_metrics'], info['produced_states'], info['compatibility_score'], info['module']] for info in task_info]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"共找到 {len(task_info)} 个兼容的任务类")
