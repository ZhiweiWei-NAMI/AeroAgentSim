#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim意外事件集成示例

该示例展示了如何使用AirFogSim的意外事件集成功能，包括意外事件的生成、处理和对代理的影响。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import argparse
import random
import time
import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from airfogsim.core.environment import Environment
from airfogsim.core.agent import Agent
from airfogsim.dataprovider.weather_integration import WeatherIntegration
from airfogsim.dataprovider.accident_integration import AccidentIntegration
from airfogsim.statistics import StatsCollector


def setup_environment(visual_interval=1.0, random_seed=None):
    """
    设置仿真环境
    
    Args:
        visual_interval: 可视化更新间隔
        random_seed: 随机种子
        
    Returns:
        Environment: 仿真环境
    """
    # 设置随机种子
    if random_seed is not None:
        random.seed(random_seed)
    
    # 创建仿真环境
    env = Environment(visual_interval=visual_interval)
    
    return env


def create_agents(env, num_agents=5):
    """
    创建代理
    
    Args:
        env: 仿真环境
        num_agents: 代理数量
        
    Returns:
        List[Agent]: 代理列表
    """
    agents = []
    
    # 创建代理
    for i in range(num_agents):
        # 随机位置
        x = random.uniform(-500, 500)
        y = random.uniform(-500, 500)
        z = random.uniform(50, 100)
        
        # 创建代理
        agent = Agent(env, position=(x, y, z), battery_level=100.0)
        
        # 添加到列表
        agents.append(agent)
    
    return agents


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AirFogSim意外事件集成示例')
    parser.add_argument('--duration', type=int, default=3600, help='仿真时长（秒），默认3600秒')
    parser.add_argument('--num-drones', type=int, default=5, help='无人机数量，默认5个')
    parser.add_argument('--visual-interval', type=float, default=1.0, help='可视化更新间隔（秒），默认1.0秒')
    parser.add_argument('--random-seed', type=int, default=None, help='随机种子，默认None')
    parser.add_argument('--output-dir', type=str, default='./output', help='输出目录，默认./output')
    parser.add_argument('--collect-stats', action='store_true', help='收集统计数据')
    args = parser.parse_args()
    
    # 设置环境
    env = setup_environment(visual_interval=args.visual_interval, random_seed=args.random_seed)
    
    # 创建代理
    agents = create_agents(env, args.num_drones)
    
    # 创建天气集成
    WeatherIntegration(env, {'use_mock_data': True})
    print(f"天气系统已启用")
    
    # 创建意外事件集成
    AccidentIntegration(env, {
        'check_interval': 60,  # 每分钟检查一次
        'base_probability': 0.1,  # 提高基础概率，便于演示
        'min_duration': 120,  # 最小持续时间2分钟
        'max_duration': 300,  # 最大持续时间5分钟
    })
    print(f"意外事件系统已启用")
    
    # 创建统计数据收集器
    if args.collect_stats:
        stats_collector = StatsCollector(env, output_dir=args.output_dir,
                                         agent_collector_config={'listen_visual_update': True})
        print(f"统计数据收集器已创建，输出目录: {args.output_dir}")
    
    # 运行仿真
    print(f"开始运行仿真，时长: {args.duration} 秒 ({args.duration/60:.1f} 分钟)")
    start_time = time.time()
    env.run(until=args.duration)
    end_time = time.time()
    
    print(f"仿真完成，耗时: {end_time - start_time:.2f} 秒")
    
    # 导出统计数据
    if args.collect_stats:
        output_files = stats_collector.export_data()
        print(f"统计数据已导出到: {output_files['output_dir']}")
    
    return {"duration": args.duration, "real_time": end_time - start_time}


if __name__ == "__main__":
    main()
