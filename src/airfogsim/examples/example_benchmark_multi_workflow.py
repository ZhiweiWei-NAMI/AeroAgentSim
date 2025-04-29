#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AirFogSim多工作流基准测试示例

该示例展示了如何使用AirFogSim进行多工作流基准测试，包括无人机巡检、物流配送和充电工作流。
该示例是为JOSS (Journal of Open Source Software) 论文准备的基准测试代码。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import argparse
import random
import time
import os
import sys

# 添加项目根目录到路径，以便导入paper_code模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

# 导入AirFogSim模块
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.agent.delivery_drone import DeliveryDroneAgent
from airfogsim.agent.delivery_station import DeliveryStation
from airfogsim.agent.inspection_station import InspectionStation
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.img_sensor import ImageSensingComponent
from airfogsim.component.computation import ComputationComponent
from airfogsim.component.communication import CommunicationComponent
from airfogsim.component.charging import ChargingComponent
from airfogsim.component.logistics import LogisticsComponent
from airfogsim.core.trigger import TimeTrigger, StateTrigger, TriggerOperator
from airfogsim.core.enums import TaskPriority
from airfogsim.workflow.inspection import InspectionWorkflow
from airfogsim.workflow.charging import ChargingWorkflow
from airfogsim.workflow.order_execution import OrderExecutionWorkflow
from airfogsim.dataprovider.weather_integration import WeatherIntegration
from airfogsim.statistics import StatsCollector, StatsAnalyzer, StatsVisualizer
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)
class WorkflowGenerator:
    """
    工作流生成器

    负责统一管理工作流的生成和分配，支持两种工作流生成方式：
    1. 基于站点的工作流生成（由station类的agent持续生成并赋予其他agent）
    2. 基于状态变化的工作流生成（由agent状态变化触发的工作流）
    """

    def __init__(self, env):
        """
        初始化工作流生成器

        Args:
            env: 仿真环境
        """
        self.env = env
        self.workflows = {}

    def create_inspection_workflow(self, agent, inspection_points, start_time=10):
        """
        创建巡检工作流

        Args:
            agent: 代理
            inspection_points: 巡检点列表
            interval: 工作流创建间隔（秒）

        Returns:
            创建的工作流
        """
        # 创建巡检工作流
        workflow = self.env.create_workflow(
            InspectionWorkflow,
            name=f"Inspection of {agent.id}",
            owner=agent,
            properties={
                'inspection_points': inspection_points,
                'task_priority': TaskPriority.NORMAL,
                'task_preemptive': False
            },
            start_trigger=TimeTrigger(self.env, trigger_time=start_time),
            max_starts=1
        )
        self.workflows[workflow.id] = workflow
        logger.info(f"时间 {self.env.now}: 为代理 {agent.id} 创建巡检工作流 {workflow.id}")
        return workflow

    def create_charging_workflow(self, agent, battery_threshold=30.0, target_charge_level=90.0):
        """
        创建充电工作流

        Args:
            agent: 代理
            battery_threshold: 电池电量阈值
            target_charge_level: 目标充电电量

        Returns:
            创建的工作流
        """
        # 创建充电工作流
        workflow = self.env.create_workflow(
            ChargingWorkflow,
            name=f"Charging of {agent.id}",
            owner=agent,
            properties={
                'battery_threshold': battery_threshold,
                'target_charge_level': target_charge_level,
                # 添加任务优先级和抢占属性
                'task_priority': TaskPriority.CRITICAL,  # 最高优先级
                'task_preemptive': True  # 允许抢占
            },
            start_trigger=StateTrigger(
                self.env,
                agent_id=agent.id,
                state_key='battery_level',
                operator=TriggerOperator.LESS_THAN,
                target_value=battery_threshold
            ),
            max_starts=None
        )
        self.workflows[workflow.id] = workflow
        logger.info(f"时间 {self.env.now}: 为代理 {agent.id} 创建充电工作流 {workflow.id}")
        logger.info(f"  - 当前电量: {agent.get_state('battery_level'):.1f}%, 触发阈值: {battery_threshold}%")
        logger.info(f"  - 优先级: 关键 (CRITICAL), 可抢占: 是")

        return workflow

    def create_delivery_station_to_station_workflows(self, delivery_stations, interval=10):
        """
        创建站点之间的快递工作流

        Args:
            delivery_stations: 快递站列表
            interval: 工作流创建间隔（秒）

        Returns:
            创建的工作流列表
        """
        # 定义各站点之间的配送关系
        delivery_pairs = []

        # 如果有多个快递站，创建它们之间的配送关系
        if len(delivery_stations) >= 2:
            for i in range(len(delivery_stations)):
                for j in range(len(delivery_stations)):
                    if i != j:  # 不在同一个站点之间配送
                        delivery_pairs.append((delivery_stations[i], delivery_stations[j]))
        delivery_pairs *= 10  # 重复10次以生成10个订单

        # 为每个配送关系创建订单执行工作流
        order_workflows = []
        for i, (source_station, target_station) in enumerate(delivery_pairs):
            # 创建物品属性，每个订单的物品属性略有不同
            payload_properties = source_station.get_state('payload_generation_model').get('properties', {}).copy()
            payload_properties['description'] = f"订单{i+1}物品"
            payload_properties['weight'] = payload_properties['weight'] * random.uniform(0.8, 1.2)  # 随机化物品重量

            # 创建订单执行工作流
            order_workflow = self.env.create_workflow(
                OrderExecutionWorkflow,
                name=f"Order of {source_station.id}",
                owner=source_station,
                properties={
                    'payload_properties': payload_properties,
                    'delivery_location': target_station.get_state('position'),
                    'target_agent_id': target_station.id
                },
                start_trigger=TimeTrigger(self.env, trigger_time=self.env.now + interval * i),
                max_starts=1 # 只启动一次
            )
            self.workflows[order_workflow.id] = order_workflow
            payload_properties['order_id'] = order_workflow.id
            order_workflows.append(order_workflow)
            logger.info(f"创建订单工作流 {order_workflow.id}，从{source_station.id}到{target_station.id}，交付位置: {target_station.get_state('position')}")

        logger.info(f"时间 {self.env.now}: 已创建 {len(order_workflows)} 个站点间快递工作流")

        return order_workflows


def setup_environment(visual_interval=10, random_seed=42):
    """
    设置仿真环境

    Args:
        visual_interval: 可视化更新间隔（秒）
        random_seed: 随机种子

    Returns:
        Environment: 创建的仿真环境
    """
    # 设置随机种子
    random.seed(random_seed)

    # 创建环境
    env = Environment(visual_interval=visual_interval)

    # 创建着陆点
    create_landing_spots(env)

    logger.info(f"环境初始化完成，可视化更新间隔: {visual_interval} 秒")

    return env


def create_landing_spots(env):
    """
    创建着陆点

    Args:
        env: 仿真环境
        
    Returns:
        Dict: 创建的着陆点ID字典
    """
    # 创建主基地
    main_base_id = env.landing_manager.create_base_station(
        location=(0, 0, 0),
        radius=50.0,
        max_capacity=10,
        charging_power=500.0,
        data_transfer_rate=100.0,
        name='主基地'
    )

    # 创建工业区着陆点
    industrial_landing_id = env.landing_manager.create_base_station(
        location=(1000, 1000, 0),
        radius=30.0,
        max_capacity=10,
        charging_power=300.0,
        data_transfer_rate=50.0,
        name='工业区着陆点'
    )

    # 创建商业区着陆点
    commercial_landing_id = env.landing_manager.create_base_station(
        location=(800, 800, 0),
        radius=20.0,
        max_capacity=10,
        charging_power=200.0,
        data_transfer_rate=30.0,
        name='商业区着陆点'
    )

    logger.info(f"创建了 3 个着陆点")

    return {
        'main_base_id': main_base_id,
        'industrial_landing_id': industrial_landing_id,
        'commercial_landing_id': commercial_landing_id
    }


def create_agents(env, num_drones=10):
    """
    创建代理

    Args:
        env: 仿真环境
        num_drones: 无人机数量

    Returns:
        Dict: 创建的代理字典
    """
    agents = {
        'drones': [],
        'inspection_stations': [],
        'delivery_stations': []
    }

    # 创建巡检站
    inspection_station = create_inspection_station(env)
    agents['inspection_stations'].append(inspection_station)

    # 创建多个快递站
    delivery_stations = create_delivery_stations(env)
    agents['delivery_stations'].extend(delivery_stations)

    # 创建无人机
    for i in range(num_drones):
        drone = create_drone(env, i+1)

        # 注册到巡检站和快递站
        inspection_station.register_drone(drone.id)

        # 注册到所有快递站
        for station in delivery_stations:
            station.register_drone(drone.id)

        # 添加到列表
        agents['drones'].append(drone)

    logger.info(f"创建了 {len(agents['drones'])} 架无人机、{len(agents['inspection_stations'])} 个巡检站和 {len(agents['delivery_stations'])} 个快递站")

    return agents


def create_inspection_station(env):
    """
    创建巡检站

    Args:
        env: 仿真环境

    Returns:
        InspectionStation: 创建的巡检站
    """
    # 创建巡检站
    inspection_station = InspectionStation(
        env,
        "main_inspection_station",
        properties={
            'position': [0, 0, 10],
            'service_radius': 2000.0,
            'inspection_generation_interval': 10,  # 每10秒生成一次巡检任务
            'inspection_areas': [
                {
                    'center': [500, 500, 0],
                    'radius': 300.0,
                    'min_altitude': 50,
                    'max_altitude': 150,
                    'name': '工业区巡检区域'
                }
            ]
        }
    )

    # 重写选择无人机的方法，使用轮转策略而非随机选择
    last_selected_index = [0]  # 使用列表存储上次选择的索引，以便在闭包中修改

    def new_select_drone_for_inspection():
        available_drones = inspection_station.get_available_drones()
        if not available_drones:
            return None

        # 轮转选择无人机
        index = last_selected_index[0] % len(available_drones)
        drone_id = available_drones[index]
        last_selected_index[0] = (index + 1) % len(available_drones)

        logger.info(f"时间 {env.now}: 巡检站选择无人机 {drone_id} (轮转策略)")
        return drone_id
    
    inspection_station.select_drone_for_inspection = new_select_drone_for_inspection
    env.register_agent(inspection_station)

    return inspection_station


def create_delivery_stations(env):
    """
    创建多个快递站

    Args:
        env: 仿真环境

    Returns:
        list: 创建的快递站列表
    """
    # 1. 中心快递站
    center_station = DeliveryStation(
        env,
        "center_station",
        properties={
            'position': [0, 0, 0],  # 中心快递站位置
            'storage_capacity': 200,  # 存储容量
            'service_radius': 2000.0,   # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 2.0,    # kg
                    'dimensions': [0.3, 0.2, 0.15],  # m
                    'description': '中心快递物品'
                }
            }
        }
    )

    # 2. 工业区快递站
    industrial_station = DeliveryStation(
        env,
        "industrial_station",
        properties={
            'position': [1000, 1000, 0],  # 工业区快递站位置
            'storage_capacity': 150,  # 存储容量
            'service_radius': 2000.0,   # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 3.0,    # kg
                    'dimensions': [0.4, 0.3, 0.2],  # m
                    'description': '工业物品'
                }
            }
        }
    )

    # 3. 居民区快递站
    residential_station = DeliveryStation(
        env,
        "residential_station",
        properties={
            'position': [800, 800, 0],  # 居民区快递站位置
            'storage_capacity': 100,  # 存储容量
            'service_radius': 2000.0,   # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 1.0,    # kg
                    'dimensions': [0.2, 0.15, 0.1],  # m
                    'description': '居民物品'
                }
            }
        }
    )

    # 为每个快递站添加方法并注册到环境
    stations = []

    for station in [center_station, industrial_station, residential_station]:
        # 重写选择无人机的方法，使用负载均衡策略
        station_last_selected = {}  # 记录每个无人机上次被选择的时间

        def new_select_drone_for_delivery(station=station, last_selected=station_last_selected):
            available_drones = station.get_available_drones()
            if not available_drones:
                return None

            # 选择最长时间没有被分配任务的无人机
            current_time = env.now
            least_recently_used = None
            longest_idle_time = -1

            for drone_id in available_drones:
                last_time = last_selected.get(drone_id, 0)
                idle_time = current_time - last_time

                if idle_time > longest_idle_time:
                    longest_idle_time = idle_time
                    least_recently_used = drone_id

            # 更新选择时间
            if least_recently_used:
                last_selected[least_recently_used] = current_time

            return least_recently_used
        # 设置方法
        station.select_drone_for_delivery = new_select_drone_for_delivery

        # 注册到环境
        env.register_agent(station)

        # 添加到列表
        stations.append(station)

    # 返回创建的快递站列表
    return stations


def create_drone(env, index):
    """
    创建无人机

    Args:
        env: 仿真环境
        index: 无人机索引

    Returns:
        DroneAgent: 创建的无人机
    """
    # 随机生成初始位置
    x = random.uniform(0, 2000)
    y = random.uniform(0, 2000)
    z = random.uniform(100, 150)

    # 创建无人机
    drone = DeliveryDroneAgent(
        env,
        f"drone_{index}",
        properties={
            'position': (x, y, z),
            'battery_level': random.uniform(70, 90),
            'max_speed': random.uniform(10, 15),  # m/s
            'weight': random.uniform(1.0, 2.0),  # kg
            'max_payload_weight': random.uniform(3.0, 5.0)  # kg
        }
    )

    # 添加组件
    drone.add_component(MoveToComponent(env, drone))
    drone.add_component(ImageSensingComponent(env, drone))
    drone.add_component(ComputationComponent(env, drone))
    drone.add_component(CommunicationComponent(env, drone))
    drone.add_component(ChargingComponent(env, drone))
    drone.add_component(LogisticsComponent(env, drone))

    # 注册到环境
    env.register_agent(drone)

    return drone


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
        logger.warning("没有可用的无人机代理")
        return

    # 获取快递站和巡检站
    delivery_stations = agents['delivery_stations']
    inspection_stations = agents['inspection_stations']

    if not delivery_stations:
        logger.warning("没有可用的快递站代理")
        return

    if not inspection_stations:
        logger.warning("没有可用的巡检站代理")
        return

    # 根据场景类型设置工作流生成
    if not (scenario == 'inspection' or scenario == 'mixed'):
        # 巡检是通过agent发布的，所以如果不需要巡检，则设置inspection_generation_interval为inf
        for station in inspection_stations:
            station.update_state('inspection_generation_interval', float('inf'))

    if scenario == 'delivery' or scenario == 'mixed':
        # 使用工作流生成器创建站点之间的快递工作流
        order_workflows = workflow_generator.create_delivery_station_to_station_workflows(delivery_stations, interval)
        logger.info(f"时间 {env.now}: 已创建 {len(order_workflows)} 个站点间快递工作流")

    if scenario == 'charging' or scenario == 'mixed':
        # 显式创建充电工作流
        charging_workflows = []
        for drone in drones:
            # 为每个无人机创建充电工作流
            charging_workflow = workflow_generator.create_charging_workflow(
                agent=drone,
                battery_threshold=50.0,  # 电量低于50%时触发充电
                target_charge_level=90.0  # 充电目标电量
            )
            charging_workflows.append(charging_workflow)

        logger.info(f"时间 {env.now}: 已创建 {len(charging_workflows)} 个充电工作流")


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AirFogSim多工作流基准测试示例')
    parser.add_argument('--num-drones', type=int, default=10, help='无人机数量')
    parser.add_argument('--duration', type=int, default=600, help='仿真时长（秒），默认10分钟')
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
        logger.info(f"天气系统已启用")

    # 创建统计数据收集器
    if args.collect_stats:
        stats_collector = StatsCollector(env, output_dir=args.output_dir,
                                         agent_collector_config={'listen_visual_update': True})
        logger.info(f"统计数据收集器已创建，输出目录: {args.output_dir}")

    # 设置基于站点的工作流生成
    setup_station_workflow_generation(env, workflow_generator, agents, args.scenario, args.station_interval)

    # 运行仿真
    logger.info(f"开始运行仿真，时长: {args.duration} 秒 ({args.duration/60:.1f} 分钟)")
    start_time = time.time()
    env.run(until=args.duration)
    end_time = time.time()

    logger.info(f"仿真完成，耗时: {end_time - start_time:.2f} 秒")

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

        logger.info(f"统计数据分析和可视化完成，输出目录: {stats_dir}")

    return {"duration": args.duration, "real_time": end_time - start_time}


if __name__ == "__main__":
    main()
