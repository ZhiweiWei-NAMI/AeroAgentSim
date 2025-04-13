"""
AirFogSim类检查工具使用示例

该模块展示了如何使用类检查工具来检查系统中已实现的各种类及其关键信息。
可以作为独立脚本运行，也可以作为模块导入。

使用方法:
    python -m airfogsim.helper.examples

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

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

def example_check_all_classes():
    """检查所有类的示例"""
    print("\n示例1: 检查所有类")
    env = setup_environment()
    check_all_classes(env)

def example_check_specific_classes():
    """检查特定类型的类的示例"""
    print("\n示例2: 检查特定类型的类")
    env = setup_environment()
    
    print("\n2.1 检查代理类")
    check_agent_classes(env)
    
    print("\n2.2 检查组件类")
    check_component_classes(env)
    
    print("\n2.3 检查任务类")
    check_task_classes(env)
    
    print("\n2.4 检查工作流类")
    check_workflow_classes(env)

def example_find_compatible_agents():
    """查找兼容的代理类的示例"""
    print("\n示例3: 查找兼容的代理类")
    env = setup_environment()
    
    # 查找支持位置和电池电量状态的代理类
    required_states = ['position', 'battery_level']
    find_compatible_agents(env, required_states)

def example_find_compatible_components():
    """查找兼容的组件类的示例"""
    print("\n示例4: 查找兼容的组件类")
    env = setup_environment()
    
    # 创建一个临时代理
    from airfogsim.agent.drone import DroneAgent
    agent = DroneAgent(env, "temp_drone")
    
    # 查找产生速度指标的组件类
    required_metrics = ['speed']
    find_compatible_components(env, agent, required_metrics)

def example_find_compatible_tasks():
    """查找兼容的任务类的示例"""
    print("\n示例5: 查找兼容的任务类")
    env = setup_environment()
    
    # 创建一个临时代理
    from airfogsim.agent.drone import DroneAgent
    agent = DroneAgent(env, "temp_drone")
    
    # 为代理添加移动组件
    from airfogsim.component.mobility import MoveToComponent
    agent.add_component(MoveToComponent(env, agent))
    
    # 查找产生位置和方向状态的任务类
    required_states = ['position', 'direction']
    find_compatible_tasks(env, agent, required_states=required_states)

def main():
    """主函数"""
    print("="*80)
    print("AirFogSim类检查工具使用示例")
    print("="*80)
    
    # 示例1: 检查所有类
    example_check_all_classes()
    
    # 示例2: 检查特定类型的类
    example_check_specific_classes()
    
    # 示例3: 查找兼容的代理类
    example_find_compatible_agents()
    
    # 示例4: 查找兼容的组件类
    example_find_compatible_components()
    
    # 示例5: 查找兼容的任务类
    example_find_compatible_tasks()

if __name__ == "__main__":
    main()
