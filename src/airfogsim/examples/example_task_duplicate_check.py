"""
AirFogSim任务重复检查示例

该示例演示了如何避免重复添加相同的任务到队列中，以及如何避免重复执行相同的任务。
主要内容包括：
1. 检查任务是否已经在队列中
2. 检查任务是否已经在执行中
3. 演示任务队列按优先级和工作流开始时间排序

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.core.enums import TaskPriority
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
import random
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def setup_environment():
    """设置仿真环境"""
    env = Environment()
    return env


def run_task_duplicate_check_demo():
    """
    运行任务重复检查示例
    """
    # 设置环境
    env = setup_environment()

    # 创建无人机
    drone = DroneAgent(
        env,
        "duplicate_check_demo_drone",
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

    # 打印组件名称
    logger.info(f"时间 {env.now}: 无人机组件名称: {move_component.name}")

    # 注册代理
    env.register_agent(drone)

    # 模拟添加相同任务的情况
    def add_duplicate_tasks():
        # 添加第一个任务
        logger.info(f"\n时间 {env.now}: 添加第一个移动任务")
        drone.add_task_to_queue(
            component_name="MoveTo",
            task_name="移动到目标点",
            task_class="MoveToTask",
            target_state={'position': (100, 100, 20)},
            properties={
                'target_position': (100, 100, 20),
                'duration': 10  # 任务持续10秒
            }
        )

        # 立即尝试添加相同的任务（应该被队列检查过滤）
        logger.info(f"\n时间 {env.now}: 尝试添加相同的任务（应该被队列检查过滤）")
        drone.add_task_to_queue(
            component_name="MoveTo",
            task_name="移动到目标点",
            task_class="MoveToTask",
            target_state={'position': (100, 100, 20)},
            properties={
                'target_position': (100, 100, 20),
                'duration': 10  # 任务持续10秒
            }
        )

        # 处理任务队列，开始执行任务
        drone._process_task_queue()

        # 等待5秒后再次尝试添加相同任务（此时第一个任务应该正在执行）
        yield env.timeout(5)
        logger.info(f"\n时间 {env.now}: 尝试添加相同的任务（应该被执行检查过滤）")

        # 模拟工作流添加任务
        task_info = {
            'component': "MoveTo",
            'task_name': "移动到目标点",
            'task_class': "MoveToTask",
            'target_state': {'position': (100, 100, 20)},
            'properties': {
                'target_position': (100, 100, 20),
                'duration': 10
            }
        }

        # 检查是否已经有相同的任务在队列中或正在执行
        if not drone._is_task_in_queue(task_info) and not drone._is_task_being_executed(task_info):
            logger.info("  任务可以添加到队列")
            drone.add_task_to_queue(
                component_name=task_info['component'],
                task_name=task_info['task_name'],
                task_class=task_info['task_class'],
                target_state=task_info.get('target_state'),
                properties=task_info.get('properties')
            )
        else:
            logger.info("  任务已经在队列中或正在执行，不添加")

        # 等待10秒后再次尝试添加相同任务（此时第一个任务应该已经完成）
        yield env.timeout(10)
        logger.info(f"\n时间 {env.now}: 再次添加相同的任务（此时应该可以添加，因为之前的任务已完成）")

        # 检查是否已经有相同的任务在队列中或正在执行
        if not drone._is_task_in_queue(task_info) and not drone._is_task_being_executed(task_info):
            logger.info("  任务可以添加到队列")
            drone.add_task_to_queue(
                component_name=task_info['component'],
                task_name=task_info['task_name'],
                task_class=task_info['task_class'],
                target_state=task_info.get('target_state'),
                properties=task_info.get('properties')
            )
        else:
            logger.info("  任务已经在队列中或正在执行，不添加")

    # 启动任务添加进程
    env.process(add_duplicate_tasks())

    # 运行模拟
    logger.info("开始模拟...")
    env.run(until=30)
    logger.info("模拟结束")


if __name__ == "__main__":
    run_task_duplicate_check_demo()
