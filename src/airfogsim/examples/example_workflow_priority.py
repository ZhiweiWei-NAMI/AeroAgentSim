"""
AirFogSim工作流优先级示例

该示例演示了如何为工作流设置任务优先级和抢占属性，以及这些属性如何影响任务执行。
主要内容包括：
1. 创建具有不同优先级的工作流
2. 观察任务优先级和抢占行为
3. 展示工作流属性如何传递给任务
4. 演示任务队列按优先级和工作流开始时间排序

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.core.enums import TaskPriority
from airfogsim.workflow.charging import create_charging_workflow
from airfogsim.workflow.inspection import create_inspection_workflow
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent
import random
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def setup_environment():
    """设置仿真环境"""
    env = Environment()

    # 创建着陆点
    # 创建充电站
    charging_station_id = env.landing_manager.create_charging_spot(
        location=(200, 200, 0),
        radius=15.0,
        max_capacity=3,
        charging_power=300.0,
        name="充电站_1"
    )

    # 创建第二个充电站
    charging_station_id2 = env.landing_manager.create_charging_spot(
        location=(400, 400, 0),
        radius=15.0,
        max_capacity=3,
        charging_power=300.0,
        name="充电站_2"
    )
    return env


def run_workflow_priority_demo():
    """
    运行工作流优先级示例
    """
    # 设置环境
    env = setup_environment()

    # 创建无人机
    drone = DroneAgent(
        env,
        "priority_demo_drone",
        properties={
            'position': (0, 0, 10),
            'battery_level': 60,
            'max_speed': 10,  # m/s
            'weight': 1.5,  # kg
            'max_payload_weight': 3.0  # kg
        }
    )

    # 添加组件到无人机
    move_component = MoveToComponent(env, drone)
    charging_component = ChargingComponent(env, drone)

    # 注册组件
    drone.add_component(move_component)
    drone.add_component(charging_component)

    # 注册代理
    env.register_agent(drone)

    # 电量已在创建时设置为60%

    # 创建巡检点
    inspection_points = [
        (100, 100, 20),
        (200, 200, 30),
        (300, 300, 40),
        (400, 400, 50),
        (500, 500, 60)
    ]

    # 创建巡检工作流（普通优先级，不可抢占）
    inspection_workflow = create_inspection_workflow(
        env,
        drone,
        inspection_points,
        task_priority=TaskPriority.NORMAL,
        task_preemptive=False
    )

    # 创建充电工作流（关键优先级，可抢占）
    charging_workflow = create_charging_workflow(
        env,
        drone,
        battery_threshold=40,  # 当电量低于40%时触发充电
        target_charge_level=90,
        task_priority=TaskPriority.CRITICAL,
        task_preemptive=True
    )

    # 模拟电池电量下降
    def battery_drain():
        while True:
            current_level = drone.get_state('battery_level')
            if current_level > 0:
                # 随机减少1-3%的电量
                decrease = random.uniform(1, 3)
                new_level = max(0, current_level - decrease)
                drone.update_state('battery_level', new_level)
                logger.info(f"时间 {env.now:.1f}: 电池电量: {new_level:.1f}%")
            yield env.timeout(5)

    env.process(battery_drain())

    # 启动巡检工作流
    inspection_workflow.start()

    # 运行模拟
    logger.info("开始模拟...")
    env.run(until=500)
    logger.info("模拟结束")


if __name__ == "__main__":
    run_workflow_priority_demo()
