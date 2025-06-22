"""
AirFogSim 频率管理与信号集成示例

该示例展示了FrequencyManager和SignalDataProvider的集成，
演示了如何在通信过程中自动创建和管理信号源。
主要功能包括：
1. 创建通信代理和感知代理
2. 通信代理之间请求频率资源进行通信
3. 感知代理检测通信信号

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import random
import time
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.communication import CommunicationComponent
from airfogsim.component.em_sensor import EMSensingComponent
from airfogsim.manager.airspace import AirspaceManager
from airfogsim.manager.frequency import FrequencyManager
from airfogsim.dataprovider.signal import SignalDataProvider
from airfogsim.core.trigger import TimeTrigger
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def create_communication_drone(env, drone_id, position, properties=None):
    """
    创建具有通信能力的无人机

    Args:
        env: 仿真环境
        drone_id: 无人机ID
        position: 初始位置
        properties: 额外属性

    Returns:
        DroneAgent: 创建的无人机
    """
    # 合并属性
    props = {
        'position': position,
        'battery_level': 100.0,
        'max_speed': 10.0,
        'max_acceleration': 2.0
    }
    if properties:
        props.update(properties)

    # 创建无人机
    drone = DroneAgent(env, drone_id, props)

    # 添加移动组件
    drone.add_component(
        MoveToComponent(
            env=env,
            agent=drone,
            properties={
                'max_speed': props.get('max_speed', 10.0),
                'max_acceleration': props.get('max_acceleration', 2.0),
                'energy_consumption_rate': 2.0  # 每秒消耗电池百分比
            }
        )
    )

    # 添加通信组件
    drone.add_component(
        CommunicationComponent(
            env=env,
            agent=drone,
            properties={
                'max_power': 100.0,  # 最大发射功率(mW)
                'default_frequency': (2400, 2500),  # 默认频率范围(MHz)
                'antenna_gain': 2.0,  # 天线增益(dBi)
                'energy_consumption_rate': 1.5  # 每秒消耗电池百分比
            }
        )
    )

    return drone


def create_sensing_drone(env, drone_id, position, properties=None):
    """
    创建具有电磁感知能力的无人机

    Args:
        env: 仿真环境
        drone_id: 无人机ID
        position: 初始位置
        properties: 额外属性

    Returns:
        DroneAgent: 创建的无人机
    """
    # 合并属性
    props = {
        'position': position,
        'battery_level': 100.0,
        'max_speed': 10.0,
        'max_acceleration': 2.0
    }
    if properties:
        props.update(properties)

    # 创建无人机
    drone = DroneAgent(env, drone_id, props)

    # 添加移动组件
    drone.add_component(
        MoveToComponent(
            env=env,
            agent=drone,
            properties={
                'max_speed': props.get('max_speed', 10.0),
                'max_acceleration': props.get('max_acceleration', 2.0),
                'energy_consumption_rate': 2.0  # 每秒消耗电池百分比
            }
        )
    )

    # 添加电磁感知组件
    drone.add_component(
        EMSensingComponent(
            env=env,
            agent=drone,
            properties={
                'sensing_interval': 1.0,  # 每秒感知一次
                'sensitivity_threshold': -90.0,  # 灵敏度阈值 (dBm)
                'frequency_bands': [(2400, 2500), (5000, 5200)],  # 感知频段
                'energy_consumption_rate': 1.0  # 每秒消耗电池百分比
            }
        )
    )

    # 注册事件处理器
    env.event_registry.subscribe(
        drone.id,
        'EMSensor.signal_detected',
        f"{drone.id}_signal_detected_handler",
        lambda event_data: on_signal_detected(drone, event_data)
    )

    env.event_registry.subscribe(
        drone.id,
        'EMSensor.signal_lost',
        f"{drone.id}_signal_lost_handler",
        lambda event_data: on_signal_lost(drone, event_data)
    )

    return drone


def on_signal_detected(drone, event_data):
    """
    信号检测事件处理器

    Args:
        drone: 无人机
        event_data: 事件数据
    """
    # 使用 signal_id 作为主要标识，兼容 source_id
    signal_id = event_data.get('signal_id', event_data.get('source_id', 'unknown'))
    logger.info(f"时间 {drone.env.now:.1f}: {drone.id} 检测到信号 {signal_id}")

    # 安全获取信号属性，避免KeyError
    if 'center_frequency' in event_data and 'bandwidth' in event_data:
        logger.info(f"  频率: {event_data['center_frequency']:.1f} MHz, 带宽: {event_data['bandwidth']:.1f} MHz")

    if 'received_power' in event_data and 'snr' in event_data:
        logger.info(f"  接收功率: {event_data['received_power']:.1f} dBm, SNR: {event_data['snr']:.1f} dB")

    if 'signal_type' in event_data:
        logger.info(f"  信号类型: {event_data['signal_type']}")

    # 如果是通信信号，显示目标ID
    if event_data.get('target_id'):
        logger.info(f"  目标ID: {event_data['target_id']}")


def on_signal_lost(drone, event_data):
    """
    信号丢失事件处理器

    Args:
        drone: 无人机
        event_data: 事件数据
    """
    # 使用 signal_id 而不是 source_id，兼容两种情况
    signal_id = event_data.get('signal_id', event_data.get('source_id', 'unknown'))
    logger.info(f"时间 {drone.env.now:.1f}: {drone.id} 丢失信号 {signal_id}")


def random_movement(env, drone, max_distance=100.0, move_interval=10.0):
    """
    随机移动进程

    Args:
        env: 仿真环境
        drone: 无人机
        max_distance: 最大移动距离
        move_interval: 移动间隔
    """
    try:
        while True:
            # 等待一段时间
            yield env.timeout(move_interval)

            # 获取当前位置
            current_position = drone.get_state('position')
            if not current_position:
                continue

            # 生成随机目标位置
            target_position = (
                current_position[0] + random.uniform(-max_distance, max_distance),
                current_position[1] + random.uniform(-max_distance, max_distance),
                max(5.0, current_position[2] + random.uniform(-20, 20))
            )

            # 移动到目标位置
            logger.info(f"时间 {env.now:.1f}: {drone.id} 开始移动到 {target_position}")

            # 创建移动任务
            move_task = drone.task_manager.create_task(
                'MoveToTask',
                drone,
                'MoveTo',
                'MoveTo',
                target_state={'position': target_position},
                properties={
                    'speed': random.uniform(5.0, 10.0)
                }
            )

            # 等待任务完成
            # 注意：Task对象没有wait方法，我们需要等待一段时间
            yield env.timeout(10.0)  # 等待10秒，假设任务在这段时间内完成

            logger.info(f"时间 {env.now:.1f}: {drone.id} 到达位置 {drone.get_state('position')}")

    except Exception as e:
        logger.info(f"随机移动进程异常: {e}")


def communication_process(env, source_drone, target_drone, comm_interval=20.0, comm_duration=5.0):
    """
    通信进程

    Args:
        env: 仿真环境
        source_drone: 源无人机
        target_drone: 目标无人机
        comm_interval: 通信间隔
        comm_duration: 通信持续时间
    """
    try:
        while True:
            # 等待一段时间
            yield env.timeout(comm_interval)

            # 获取频率管理器
            frequency_manager = env.frequency_manager

            # 请求频率资源
            logger.info(f"时间 {env.now:.1f}: {source_drone.id} 请求与 {target_drone.id} 通信")

            # 设置通信目标
            source_drone.update_state('trans_target_agent_id', target_drone.id)
            source_drone.update_state('transmitting_status', 'transmitting')

            # 请求频率资源
            resource_ids = frequency_manager.request_resource(
                source_id=source_drone.id,
                target_id=target_drone.id,
                power_db=20.0,  # 20 dBm
                requirements={
                    'preferred_frequency': 2450.0,
                    'min_bandwidth': 20.0
                }
            )

            if resource_ids:
                logger.info(f"时间 {env.now:.1f}: {source_drone.id} 获得频率资源 {resource_ids}")

                # 通信持续一段时间
                yield env.timeout(comm_duration)

                # 释放频率资源
                frequency_manager.release_resource(
                    source_id=source_drone.id,
                    target_id=target_drone.id,
                    resource_ids=resource_ids
                )

                logger.info(f"时间 {env.now:.1f}: {source_drone.id} 释放频率资源 {resource_ids}")
            else:
                logger.warning(f"时间 {env.now:.1f}: {source_drone.id} 无法获得频率资源")

            # 重置通信状态
            source_drone.update_state('transmitting_status', 'idle')
            source_drone.update_state('trans_target_agent_id', '')

    except Exception as e:
        logger.error(f"通信进程异常: {e}")


def main():
    """主函数"""
    # 创建仿真环境
    env = Environment()

    # 创建空域管理器
    airspace_manager = AirspaceManager(env)
    env.airspace_manager = airspace_manager

    # 创建频率管理器，启用信号提供者集成
    frequency_manager = FrequencyManager(
        env=env,
        total_bandwidth=200.0,
        block_bandwidth=10.0,
        start_frequency=2400.0,
        power_limit=100.0,
        config={
            'use_signal_provider_for_sinr': True  # 使用SignalDataProvider进行SINR计算
        }
    )
    env.frequency_manager = frequency_manager

    # 创建信号数据提供者
    signal_provider = SignalDataProvider(
        env=env,
        config={
            'propagation_model': 'free_space',
            'default_noise_floor': -100.0,
            'weather_enabled': False
        }
    )
    env.signal_data_provider = signal_provider

    # 创建通信无人机
    comm_drones = []
    for i in range(3):
        # 随机位置
        position = (
            random.uniform(-50, 50),
            random.uniform(-50, 50),
            random.uniform(10, 30)
        )

        # 创建无人机
        drone = create_communication_drone(
            env=env,
            drone_id=f"comm_drone_{i}",
            position=position
        )

        # 注册到环境
        env.register_agent(drone)

        # 启动随机移动进程
        env.process(random_movement(env, drone, move_interval=15.0))

        comm_drones.append(drone)

    # 创建感知无人机
    sensing_drones = []
    for i in range(2):
        # 随机位置
        position = (
            random.uniform(-50, 50),
            random.uniform(-50, 50),
            random.uniform(10, 30)
        )

        # 创建无人机
        drone = create_sensing_drone(
            env=env,
            drone_id=f"sensing_drone_{i}",
            position=position
        )

        # 注册到环境
        env.register_agent(drone)

        # 启动随机移动进程
        env.process(random_movement(env, drone, move_interval=10.0))

        sensing_drones.append(drone)

    # 启动通信进程
    for i in range(len(comm_drones)):
        for j in range(len(comm_drones)):
            if i != j:
                # 创建通信进程
                env.process(communication_process(
                    env=env,
                    source_drone=comm_drones[i],
                    target_drone=comm_drones[j],
                    comm_interval=random.uniform(15.0, 30.0),
                    comm_duration=random.uniform(3.0, 8.0)
                ))

    # 运行仿真
    logger.info("开始仿真...")
    env.run(until=100)
    logger.info("仿真结束")


if __name__ == "__main__":
    main()
