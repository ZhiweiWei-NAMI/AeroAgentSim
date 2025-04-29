"""
AirFogSim 信号感知示例

该示例展示了如何使用SignalDataProvider和EMSensingComponent进行电磁信号感知。
主要功能包括：
1. 创建信号数据提供者和信号源
2. 创建具有电磁感知能力的代理
3. 模拟代理感知周围环境中的信号

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import random
import time
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.em_sensor import EMSensingComponent
from airfogsim.manager.airspace import AirspaceManager
from airfogsim.manager.frequency import FrequencyManager
from airfogsim.dataprovider.signal import SignalDataProvider, SignalSource
from airfogsim.dataprovider.signal_integration import ExternalSignalSourceIntegration
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def create_em_sensing_drone(env, drone_id, position, properties=None):
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
    logger.info(f"时间 {drone.env.now:.1f}: {drone.id} 检测到信号 {event_data['source_id']}")
    logger.info(f"  频率: {event_data['center_frequency']:.1f} MHz, 带宽: {event_data['bandwidth']:.1f} MHz")
    logger.info(f"  接收功率: {event_data['received_power']:.1f} dBm, SNR: {event_data['snr']:.1f} dB")
    logger.info(f"  信号类型: {event_data['signal_type']}")
    logger.info(f"  信号源位置: {event_data['position']}")


def on_signal_lost(drone, event_data):
    """
    信号丢失事件处理器

    Args:
        drone: 无人机
        event_data: 事件数据
    """
    logger.info(f"时间 {drone.env.now:.1f}: {drone.id} 丢失信号 {event_data['source_id']}")


def create_fixed_signal_sources(env, signal_provider, count=3):
    """
    创建固定信号源

    Args:
        env: 仿真环境
        signal_provider: 信号数据提供者
        count: 信号源数量

    Returns:
        List[str]: 创建的信号源ID列表
    """
    source_ids = []

    for i in range(count):
        # 随机位置
        position = (
            random.uniform(-100, 100),
            random.uniform(-100, 100),
            random.uniform(0, 50)
        )

        # 随机频率
        if random.random() < 0.7:
            # 70%概率在2.4GHz频段
            center_frequency = random.uniform(2400, 2500)
        else:
            # 30%概率在5GHz频段
            center_frequency = random.uniform(5000, 5200)

        # 随机带宽
        bandwidth = random.choice([5.0, 10.0, 20.0])

        # 随机功率
        transmit_power_dbm = random.uniform(10.0, 30.0)

        # 创建信号源
        source = SignalSource(
            source_id=f"fixed_signal_{i}",
            position=position,
            transmit_power_dbm=transmit_power_dbm,
            center_frequency=center_frequency,
            bandwidth=bandwidth,
            signal_type='fixed_transmitter',
            is_active=True
        )

        # 添加到信号数据提供者
        signal_provider.add_signal_source(source)
        source_ids.append(source.source_id)

        logger.info(f"创建固定信号源 {source.source_id} 在位置 {position}")
        logger.info(f"  频率: {center_frequency:.1f} MHz, 带宽: {bandwidth:.1f} MHz")
        logger.info(f"  发射功率: {transmit_power_dbm:.1f} dBm")

    return source_ids


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


def main():
    """主函数"""
    # 创建仿真环境
    env = Environment()

    # 创建空域管理器
    airspace_manager = AirspaceManager(env)
    env.airspace_manager = airspace_manager

    # 创建频率管理器
    frequency_manager = FrequencyManager(
        env=env,
        total_bandwidth=200.0,
        block_bandwidth=10.0,
        start_frequency=2400.0,
        power_limit=100.0
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

    # 创建信号源集成
    signal_integration = ExternalSignalSourceIntegration(
        env=env,
        config={
            'auto_load': False
        }
    )

    # 创建固定信号源
    fixed_source_ids = create_fixed_signal_sources(env, signal_provider, count=5)

    # 创建具有电磁感知能力的无人机
    drones = []
    for i in range(3):
        # 随机位置
        position = (
            random.uniform(-50, 50),
            random.uniform(-50, 50),
            random.uniform(10, 30)
        )

        # 创建无人机
        drone = create_em_sensing_drone(
            env=env,
            drone_id=f"em_drone_{i}",
            position=position
        )

        # 注册到环境
        env.register_agent(drone)

        # 启动随机移动进程
        env.process(random_movement(env, drone))

        drones.append(drone)

    # 运行仿真
    logger.info("开始仿真...")
    env.run(until=100)
    logger.info("仿真结束")


if __name__ == "__main__":
    main()
