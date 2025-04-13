"""
AirFogSim类查找工具

该模块提供命令行工具，用于查找和展示系统中已实现的各种类及其关键信息。
可以作为独立脚本运行，也可以作为模块导入。

使用方法:
    python -m airfogsim.helper.class_finder [options]

选项:
    --all: 显示所有类
    --agent: 显示代理类
    --component: 显示组件类
    --task: 显示任务类
    --workflow: 显示工作流类
    --find-agent: 查找支持指定状态的代理类，格式: --find-agent state1,state2,...
    --find-component: 查找产生指定指标的组件类，格式: --find-component metric1,metric2,...
    --find-task: 查找产生指定状态的任务类，格式: --find-task state1,state2,...

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import argparse
import sys
from typing import List, Dict, Any, Optional

from airfogsim.core.environment import Environment
from airfogsim.helper.class_checker import (
    check_all_classes,
    check_agent_classes,
    check_component_classes,
    check_task_classes,
    check_workflow_classes,
    find_compatible_agents,
    find_compatible_components,
    find_compatible_tasks
)

def setup_environment() -> Environment:
    """
    设置仿真环境
    
    Returns:
        Environment: 创建的仿真环境
    """
    env = Environment(visual_interval=0)
    return env

def parse_args():
    """
    解析命令行参数
    
    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(description="AirFogSim类查找工具")
    
    # 显示选项
    parser.add_argument("--all", action="store_true", help="显示所有类")
    parser.add_argument("--agent", action="store_true", help="显示代理类")
    parser.add_argument("--component", action="store_true", help="显示组件类")
    parser.add_argument("--task", action="store_true", help="显示任务类")
    parser.add_argument("--workflow", action="store_true", help="显示工作流类")
    
    # 查找选项
    parser.add_argument("--find-agent", type=str, help="查找支持指定状态的代理类，格式: state1,state2,...")
    parser.add_argument("--find-component", type=str, help="查找产生指定指标的组件类，格式: metric1,metric2,...")
    parser.add_argument("--find-task", type=str, help="查找产生指定状态的任务类，格式: state1,state2,...")
    
    args = parser.parse_args()
    
    # 如果没有指定任何选项，则显示帮助信息
    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)
    
    return args

def main():
    """主函数"""
    args = parse_args()
    
    # 设置环境
    env = setup_environment()
    
    # 显示所有类
    if args.all:
        check_all_classes(env)
        return
    
    # 显示特定类型的类
    if args.agent:
        check_agent_classes(env)
    
    if args.component:
        check_component_classes(env)
    
    if args.task:
        check_task_classes(env)
    
    if args.workflow:
        check_workflow_classes(env)
    
    # 查找特定类
    if args.find_agent:
        states = [s.strip() for s in args.find_agent.split(",")]
        find_compatible_agents(env, states)
    
    if args.find_component:
        # 由于find_compatible_components需要一个agent参数，这里创建一个临时代理
        from airfogsim.agent.drone import DroneAgent
        agent = DroneAgent(env, "temp_drone")
        
        metrics = [m.strip() for m in args.find_component.split(",")]
        find_compatible_components(env, agent, metrics)
    
    if args.find_task:
        # 由于find_compatible_tasks需要一个agent参数，这里创建一个临时代理
        from airfogsim.agent.drone import DroneAgent
        agent = DroneAgent(env, "temp_drone")
        
        states = [s.strip() for s in args.find_task.split(",")]
        find_compatible_tasks(env, agent, required_states=states)

if __name__ == "__main__":
    main()
