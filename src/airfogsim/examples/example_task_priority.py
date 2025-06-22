#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim任务优先级和抢占示例

该示例演示了如何使用AirFogSim的任务优先级和抢占机制。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.img_sensor import ImageSensingComponent
from airfogsim.component.computation import ComputationComponent
from airfogsim.core.enums import TaskPriority
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def setup_environment():
    """
    设置仿真环境

    Returns:
        Environment: 仿真环境
    """
    # 创建环境
    env = Environment(visual_interval=1)

    # 创建文件管理器（FileCollectTask需要）
    from airfogsim.manager.file import FileManager
    env.file_manager = FileManager(env)

    return env

def create_drone(env):
    """
    创建无人机

    Args:
        env: 仿真环境

    Returns:
        DroneAgent: 无人机代理
    """
    # 创建无人机
    drone = DroneAgent(
        env,
        "priority_drone",
        properties={
            'position': (0, 0, 10),
            'battery_level': 100,
            'max_speed': 10,  # m/s
            'weight': 1.5,  # kg
            'max_payload_weight': 3.0  # kg
        }
    )

    # 添加组件
    move_component = MoveToComponent(env, drone, name="MoveToComponent")
    sensing_component = ImageSensingComponent(env, drone, name="SensingComponent")
    compute_component = ComputationComponent(env, drone, name="ComputationComponent")

    drone.add_component(move_component)
    drone.add_component(sensing_component)
    drone.add_component(compute_component)

    # 打印组件名称
    logger.info(f"\n无人机组件名称:")
    for component_name, component in drone.components.items():
        logger.info(f"- {component_name}: {component.__class__.__name__}")

    # 注册到环境
    env.register_agent(drone)

    return drone

def run_priority_task_demo():
    """
    运行带优先级任务的示例
    """
    # 设置环境
    env = setup_environment()

    # 创建无人机
    drone = create_drone(env)

    # 安排任务添加
    def add_low_priority_task():
        logger.info(f"\n时间 {env.now}: 添加低优先级移动任务")
        drone.add_task_to_queue(
            component_name="MoveToComponent",
            task_name="低优先级移动",
            task_class="MoveToTask",
            priority=TaskPriority.LOW,
            preemptive=False,
            target_state={'position': (100, 100, 20)},
            properties={
                'target_position': (100, 100, 20),
                'duration': 30  # 任务持续30秒
            }
        )

    def add_high_priority_task():
        logger.info(f"\n时间 {env.now}: 添加高优先级移动任务")
        drone.add_task_to_queue(
            component_name="MoveToComponent",
            task_name="高优先级移动",
            task_class="MoveToTask",
            priority=TaskPriority.HIGH,
            preemptive=True,  # 可抢占
            target_state={'position': (-100, -100, 30)},
            properties={
                'target_position': (-100, -100, 30),
                'duration': 20  # 任务持续20秒
            }
        )

    def add_critical_priority_task():
        logger.info(f"\n时间 {env.now}: 添加关键优先级图像采集任务")
        drone.add_task_to_queue(
            component_name="SensingComponent",
            task_name="关键优先级图像采集",
            task_class="FileCollectTask",
            priority=TaskPriority.CRITICAL,
            preemptive=True,  # 可抢占
            properties={
                'file_name': 'high_priority_image',
                'file_type': 'image',
                'file_size': 5120,  # 5MB
                'content_type': 'image',
                'sensing_difficulty': 1.5,  # 高分辨率图像采集难度更高
                'duration': 15  # 任务持续15秒
            }
        )

    # 安排任务添加的时间
    def delayed_task(delay, task_func):
        yield env.timeout(delay)
        task_func()

    env.process(delayed_task(5, add_low_priority_task))
    env.process(delayed_task(10, add_high_priority_task))
    env.process(delayed_task(15, add_critical_priority_task))

    # 添加一个监控进程，每秒打印任务状态
    def monitor_tasks():
        while True:
            yield env.timeout(1)
            tasks = drone.managed_tasks
            if tasks:
                logger.info(f"\n时间 {env.now:.1f}: 当前正在执行的任务:")
                for _, task_info in tasks.items():
                    if task_info['status'] == 'running':
                        task = task_info['task']
                        priority = task.priority.name if hasattr(task, 'priority') else 'UNKNOWN'
                        progress = f"{task.progress*100:.1f}%" if hasattr(task, 'progress') else 'N/A'
                        logger.info(f"  - {task.name} (优先级: {priority}, 进度: {progress})")

            # 打印任务队列
            if drone.task_queue:
                logger.info(f"  任务队列: {len(drone.task_queue)} 个任务等待执行")

    env.process(monitor_tasks())

    # 手动启动任务调度器
    env.process(drone._task_scheduler())

    # 运行仿真
    logger.info("开始运行仿真...")
    env.run(until=600)
    logger.info("\n仿真结束")

    # 打印统计信息
    logger.info("\n任务执行统计:")
    completed = 0
    preempted = 0
    task_details = []

    for _, task_info in drone.managed_tasks.items():
        task = task_info['task']
        status = task.status.name if hasattr(task, 'status') else 'UNKNOWN'
        priority = task.priority.name if hasattr(task, 'priority') else 'UNKNOWN'
        preemption_count = task.preemption_count if hasattr(task, 'preemption_count') else 0
        progress = f"{task.progress*100:.1f}%" if hasattr(task, 'progress') else 'N/A'

        task_details.append({
            'name': task.name,
            'status': status,
            'priority': priority,
            'preemption_count': preemption_count,
            'progress': progress
        })

        if status == 'COMPLETED':
            completed += 1
        if preemption_count > 0:
            preempted += 1

    logger.info(f"  - 完成任务数: {completed}")
    logger.info(f"  - 被抢占任务数: {preempted}")

    logger.info("\n任务详情:")
    for task in task_details:
        logger.info(f"  - {task['name']} (优先级: {task['priority']}, 状态: {task['status']}, 进度: {task['progress']}, 被抢占次数: {task['preemption_count']})")

if __name__ == "__main__":
    run_priority_task_demo()
