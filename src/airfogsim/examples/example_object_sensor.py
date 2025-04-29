#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ObjectSensorComponent 示例

该示例展示了如何使用 SensingAgent 感知周围环境中的物理对象。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import random
import simpy
from airfogsim.core.environment import Environment
from airfogsim.agent.sensing_agent import SensingAgent
from airfogsim.agent.drone import DroneAgent
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)


def create_random_drones(env, count=5):
    """创建随机位置的无人机"""
    drones = []

    for i in range(count):
        # 随机位置
        position = (
            random.uniform(-100, 100),
            random.uniform(-100, 100),
            random.uniform(10, 50)
        )

        # 创建无人机
        drone = DroneAgent(
            env=env,
            agent_name=f"drone_{i}",
            properties={
                'position': position,
                'battery_level': random.uniform(50, 100),
                'max_speed': random.uniform(10, 20)
            }
        )

        # 注册到环境（会自动注册到空域管理器）
        env.register_agent(drone)

        # 启动随机移动进程
        env.process(random_movement(env, drone))

        drones.append(drone)

    return drones


def random_movement(env, agent):
    """随机移动进程"""
    try:
        while True:
            # 等待一段时间
            yield env.timeout(random.uniform(2, 8))

            # 获取当前位置
            current_pos = agent.get_state('position')

            # 生成随机移动
            new_pos = (
                current_pos[0] + random.uniform(-15, 15),
                current_pos[1] + random.uniform(-15, 15),
                max(5, current_pos[2] + random.uniform(-5, 5))  # 保持最小高度
            )

            # 更新位置（会自动更新空域管理器中的位置）
            agent.update_state('position', new_pos)

    except simpy.Interrupt:
        pass


def create_sensing_agents(env, count=2):
    """创建感知代理"""
    sensing_agents = []

    for i in range(count):
        # 随机位置
        position = (
            random.uniform(-50, 50),
            random.uniform(-50, 50),
            random.uniform(5, 20)
        )

        # 创建感知代理
        agent = SensingAgent(
            env=env,
            agent_name=f"sensing_agent_{i}",
            properties={
                'position': position,
                'sensor_range': random.uniform(30, 70),
                'position_accuracy': random.uniform(1, 3),
                'classification_error_rate': random.uniform(0.05, 0.15),
                'battery_level': 50.0,  # 初始电池电量设置为50%
                'energy_factor': 5.0,   # 大幅增加能量消耗因子，使电池消耗更明显
                'base_energy_consumption': 2.0,  # 大幅增加基础能量消耗
                'sensing_interval': 1.0  # 缩短感知间隔，增加感知频率
            }
        )

        # 注册到环境（会自动注册到空域管理器）
        env.register_agent(agent)

        # 启动随机移动进程
        env.process(random_movement(env, agent))

        sensing_agents.append(agent)

    return sensing_agents


def run_object_sensing_simulation():
    """运行物理对象感知仿真"""
    # 创建环境（会自动创建空域管理器）
    env = Environment()

    # 创建无人机
    logger.info("创建无人机...")
    drones = create_random_drones(env, count=8)

    # 创建感知代理
    logger.info("创建感知代理...")
    sensing_agents = create_sensing_agents(env, count=3)

    # 打印初始状态
    logger.info(f"初始化了 {len(drones)} 个无人机和 {len(sensing_agents)} 个感知代理")

    # 运行仿真
    logger.info("=== 开始仿真 ===")
    simulation_time = 300  # 仿真300秒

    # 每60秒打印一次电池状态
    for checkpoint in range(60, simulation_time, 60):
        env.run(until=checkpoint)
        logger.info(f"=== 仿真检查点 (时间: {checkpoint}秒) ===")
        for agent in sensing_agents:
            battery_level = agent.get_state('battery_level', 0.0)
            logger.info(f"{agent.id} 当前电池电量: {battery_level:.1f}%")

    # 运行到最终时间
    env.run(until=simulation_time)
    logger.info(f"=== 仿真结束 (时间: {simulation_time}秒) ===")

    # 打印最终状态
    for agent in sensing_agents:
        nearby_objects = agent.get_nearby_objects()
        battery_level = agent.get_state('battery_level', 0.0)
        logger.info(f"{agent.id} 最终感知到 {len(nearby_objects)} 个对象，电池电量: {battery_level:.1f}%")

        # 获取最近的无人机
        nearest_drone = agent.get_nearest_object('drone')
        if nearest_drone:
            logger.info(f"  最近的无人机: {nearest_drone['id']}, 距离: {nearest_drone['distance']:.2f}米")


if __name__ == "__main__":
    run_object_sensing_simulation()
