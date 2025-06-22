"""
AirFogSim任务队列排序示例

该示例演示了任务队列如何按照优先级和工作流开始时间排序，而不需要创建任务实例。
主要内容包括：
1. 创建具有不同优先级的任务
2. 观察任务队列的排序结果
3. 验证任务队列排序不会创建任务实例

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.core.enums import TaskPriority
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.workflow.inspection import create_inspection_workflow
import random
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def setup_environment():
    """设置仿真环境"""
    env = Environment()
    return env


def run_task_queue_sort_demo():
    """
    运行任务队列排序示例
    """
    # 设置环境
    env = setup_environment()

    # 创建无人机
    drone = DroneAgent(
        env,
        "sort_demo_drone",
        properties={
            'position': (0, 0, 10),
            'battery_level': 100,
            'max_speed': 10,  # m/s
            'weight': 1.5,  # kg
            'max_payload_weight': 3.0  # kg
        }
    )
    
    # 添加组件到无人机
    move_component = MoveToComponent(env, drone)
    
    # 注册组件
    drone.add_component(move_component)
    
    # 注册代理
    env.register_agent(drone)
    
    # 创建工作流
    inspection_points1 = [(100, 100, 20), (200, 200, 30)]
    inspection_points2 = [(300, 300, 40), (400, 400, 50)]
    
    workflow1 = create_inspection_workflow(
        env, 
        drone, 
        inspection_points1,
        task_priority=TaskPriority.NORMAL,
        task_preemptive=False
    )
    
    # 启动第一个工作流
    workflow1.start()
    
    # 等待5秒后启动第二个工作流
    def start_second_workflow():
        yield env.timeout(5)
        logger.info(f"\n时间 {env.now}: 启动第二个工作流")
        workflow2 = create_inspection_workflow(
            env, 
            drone, 
            inspection_points2,
            task_priority=TaskPriority.HIGH,
            task_preemptive=True
        )
        workflow2.start()
        
        # 添加一些手动任务到队列
        yield env.timeout(2)
        logger.info(f"\n时间 {env.now}: 添加低优先级任务")
        drone.add_task_to_queue(
            component_name="MoveTo",
            task_name="低优先级移动",
            task_class="MoveToTask",
            target_state={'position': (500, 500, 60)},
            properties={
                'target_position': (500, 500, 60),
                'priority': 'low'
            }
        )
        
        yield env.timeout(1)
        logger.info(f"\n时间 {env.now}: 添加关键优先级任务")
        drone.add_task_to_queue(
            component_name="MoveTo",
            task_name="关键优先级移动",
            task_class="MoveToTask",
            target_state={'position': (600, 600, 70)},
            properties={
                'target_position': (600, 600, 70),
                'priority': 'critical'
            }
        )
        
        # 打印任务队列
        logger.info(f"\n时间 {env.now}: 任务队列排序后:")
        for i, task in enumerate(drone.task_queue):
            priority = task.get('properties', {}).get('priority', 'normal')
            workflow_id = task.get('workflow_id', 'none')
            logger.info(f"  {i+1}. {task['task_name']} - 优先级: {priority}, 工作流: {workflow_id}")
    
    env.process(start_second_workflow())
    
    # 运行模拟
    logger.info("开始模拟...")
    env.run(until=20)
    logger.info("模拟结束")


if __name__ == "__main__":
    run_task_queue_sort_demo()
