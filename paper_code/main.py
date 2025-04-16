#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim多工作流基准测试主程序

该程序是AirFogSim多工作流基准测试的入口点，负责解析命令行参数、
设置环境、创建代理、运行仿真等。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import argparse
import random
import time

# 导入自定义模块
from environment_setup import setup_environment
from agent_factory import create_agents
from workflow_manager import WorkflowGenerator
from airfogsim.dataprovider.weather_integration import WeatherIntegration
from airfogsim.statistics import StatsCollector, StatsAnalyzer, StatsVisualizer

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AirFogSim多工作流基准测试示例')
    parser.add_argument('--num-drones', type=int, default=50, help='无人机数量')
    parser.add_argument('--duration', type=int, default=1000, help='仿真时长（秒），默认1小时')
    parser.add_argument('--visual-interval', type=int, default=10, help='可视化更新间隔（秒），默认10秒')
    parser.add_argument('--output-dir', type=str, default='./stats_data', help='输出目录')
    parser.add_argument('--random-seed', type=int, default=42, help='随机种子')
    parser.add_argument('--scenario', type=str, default='mixed', choices=['inspection', 'delivery', 'charging', 'mixed'],
                        help='仿真场景类型')
    parser.add_argument('--enable-weather', action='store_true', help='启用天气系统')
    parser.add_argument('--station-interval', type=int, default=60, help='站点生成工作流的间隔（秒），默认60秒')
    parser.add_argument('--collect-stats', action='store_true', help='收集统计数据')
    args = parser.parse_args()

    # 设置环境
    env = setup_environment(visual_interval=args.visual_interval, random_seed=args.random_seed)

    # 创建代理
    agents = create_agents(env, args.num_drones)

    # 创建工作流生成器
    workflow_generator = WorkflowGenerator(env)

    # 创建天气集成
    if args.enable_weather:
        WeatherIntegration(env)
        print(f"天气系统已启用")

    # 创建统计数据收集器
    if args.collect_stats:
        stats_collector = StatsCollector(env, output_dir=args.output_dir,
                                         agent_collector_config={'listen_visual_update': True})
        print(f"统计数据收集器已创建，输出目录: {args.output_dir}")


    # 设置基于站点的工作流生成
    setup_station_workflow_generation(env, workflow_generator, agents, args.scenario, args.station_interval)

    # 运行仿真
    print(f"开始运行仿真，时长: {args.duration} 秒 ({args.duration/3600:.1f} 小时)")
    start_time = time.time()
    env.run(until=args.duration)
    end_time = time.time()

    print(f"仿真完成，耗时: {end_time - start_time:.2f} 秒")

    # 导出统计数据
    if args.collect_stats:

        # 导出统计数据
        output_files = stats_collector.export_data()

        # 分析统计数据
        stats_dir = output_files["output_dir"]
        stats_analyzer = StatsAnalyzer(stats_dir)
        report_file = stats_analyzer.save_report()

        # 生成可视化图表
        stats_visualizer = StatsVisualizer(stats_dir, report_file)
        stats_visualizer.visualize_all()

        print(f"统计数据分析和可视化完成，输出目录: {stats_dir}")

    return {"duration": args.duration, "real_time": end_time - start_time}


def setup_station_workflow_generation(env, workflow_generator, agents, scenario, interval):
    """
    设置基于站点的工作流生成

    Args:
        env: 仿真环境
        workflow_generator: 工作流生成器
        agents: 代理字典
        scenario: 场景类型
        interval: 生成间隔（秒）
    """
    drones = agents['drones']
    if not drones:
        print("没有可用的无人机代理")
        return

    # 获取快递站和巡检站
    delivery_stations = agents['delivery_stations']
    inspection_stations = agents['inspection_stations']

    if not delivery_stations:
        print("没有可用的快递站代理")
        return

    if not inspection_stations:
        print("没有可用的巡检站代理")
        return

    # 不需要导入模块，因为我们使用工作流生成器的方法

    # 根据场景类型设置工作流生成
    if not (scenario == 'inspection' or scenario == 'mixed'):
        # 巡检是通过agent发布的，所以如果不需要巡检，则设置inspection_generation_interval为inf
        for station in inspection_stations:
            station.update_state('inspection_generation_interval', float('inf'))

    if scenario == 'delivery' or scenario == 'mixed':
        # 使用工作流生成器创建站点之间的快递工作流
        order_workflows = workflow_generator.create_station_to_station_workflows(delivery_stations, interval)
        print(f"时间 {env.now}: 已创建 {len(order_workflows)} 个站点间快递工作流")

    if scenario == 'charging' or scenario == 'mixed':
        # 显式创建充电工作流
        charging_workflows = []
        for drone in drones:
            # 为每个无人机创建充电工作流
            charging_workflow = workflow_generator.create_charging_workflow(
                agent=drone,
                battery_threshold=30.0,  # 电量低于30%时触发充电
                target_charge_level=90.0  # 充电目标电量
            )
            charging_workflows.append(charging_workflow)

        print(f"时间 {env.now}: 已创建 {len(charging_workflows)} 个充电工作流")

if __name__ == "__main__":
    main()
