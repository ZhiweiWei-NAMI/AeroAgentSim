"""
AirFogSim物流工作流示例

该示例展示了如何使用订单执行工作流和物流工作流模拟快递站和无人机的配送过程。
流程：时间触发器生成4个orderexecution工作流->station agent处理工作流->两个drone派送

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.agent.delivery_drone import DeliveryDroneAgent
from airfogsim.agent.delivery_station import DeliveryStation
from airfogsim.component.logistics import LogisticsComponent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent
from airfogsim.workflow.logistics import LogisticsWorkflow
from airfogsim.workflow.order_execution import create_order_execution_workflow
from airfogsim.workflow.charging import create_charging_workflow
from airfogsim.core.trigger import TimeTrigger
import random
from tqdm import tqdm
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def run_logistics_simulation():
    """运行物流工作流示例"""
    # 创建仿真环境
    env = Environment(visual_interval=100)
    
    # 创建两个快递站
    station1 = DeliveryStation(
        env,
        "中心快递站",
        properties={
            'position': [0, 0, 0],  # 快递站位置
            'storage_capacity': 200,  # 存储容量
            'service_radius': 100.0,  # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 5.5,    # kg
                    'dimensions': [0.3, 0.2, 0.15],  # m
                    'description': '电子产品'
                }
            }
        }
    )
    station2 = DeliveryStation(
        env,
        "分拣快递站",
        properties={
            'position': [50, 50, 0],  # 快递站位置
            'storage_capacity': 100,  # 存储容量
            'service_radius': 50.0,   # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 1.5,    # kg
                    'dimensions': [0.2, 0.15, 0.1],  # m
                    'description': '食品'
                }
            }
        }
    )
    station3 = DeliveryStation(
        env,
        "居民快递站",
        properties={
            'position': [30, 30, 0],  # 快递站位置
            'storage_capacity': 50,  # 存储容量
            'service_radius': 30.0,   # 服务半径
            'payload_generation_model': {
                'properties': {
                    'weight': 1.0,    # kg
                    'dimensions': [0.2, 0.1, 0.05],  # m
                    'description': '日用品'
                }
            }
        }
    )
    # 三个快递站之间互相配送
    env.register_agent(station1).register_agent(station2).register_agent(station3)

    drones = []

    for i in tqdm(range(100), desc="创建无人机", unit="无人机"):
        drone_i = DeliveryDroneAgent(
            env,
            f"物流无人机{i+1}",
            properties={
                'position': [0, 0, 10],  # 初始位置
                'battery_level': 90.0-random.random()*30,   # 初始电量
                'max_payload_weight':5.0
            },
        )
        drones.append(drone_i)

        env.register_agent(drone_i)

    # 为无人机添加组件
    for drone in drones:
        # 添加移动组件
        drone.add_component(
            MoveToComponent(
                env,
                drone,
                properties={
                    'speed': 10.0,  # km/h
                    'energy_consumption_rate': 0.5  # %/km
                }
            )
        )
        
        # 添加物流组件
        drone.add_component(
            LogisticsComponent(
                env,
                drone,
                properties={
                    'pickup_speed': 2.0,  # 每分钟取件数
                    'handover_speed': 1.5,  # 每分钟交付数
                    'max_payload_weight': 5.0  # kg
                }
            )
        )
        
        # 添加充电组件
        drone.add_component(
            ChargingComponent(
                env,
                drone,
                properties={
                    'charging_rate': 5.0  # %/min
                }
            )
        )
        
        # 将无人机注册到快递站
        station1.register_drone(drone.id)
        station2.register_drone(drone.id)
        station3.register_drone(drone.id)
    
    
    # 创建4个订单执行工作流，使用时间触发器依次生成
    # 所有订单必须有目的agent,所有物流订单的目的agent必须是快递站
    order_workflows = []
    
    # 定义各站点之间的配送关系
    delivery_pairs = [
        (station1, station3),  # 中心快递站 -> 分拣快递站
        (station2, station3),  # 分拣快递站 -> 居民快递站
    ] * 200  # 重复100次以生成200个订单
    
    # 为每个配送关系创建订单执行工作流
    for i, (source_station, target_station) in enumerate(delivery_pairs):
        # 创建物品属性，每个订单的物品属性略有不同
        payload_properties = source_station.get_state('payload_generation_model')\
            .get('properties', {}).copy()
        payload_properties['description'] = f"订单{i+1}物品"
        payload_properties['weight'] = payload_properties['weight'] * random.uniform(0.8, 1.2)  # 随机化物品重量

        # 创建订单执行工作流
        order_workflow = create_order_execution_workflow(
            env,
            source_station,
            payload_properties,
            target_station.get_state('position'),
            target_station.id,  # 指定目标代理ID为目标快递站
            start_trigger = TimeTrigger(env, name=f'第{i}个订单到达',interval=50+i*10),  # 每10分钟触发一次
        )
        
        order_workflows.append(order_workflow)
        logger.info(f"创建订单工作流 {order_workflow.id}，从{source_station.id}到{target_station.id}，交付位置: {target_station.get_state('position')}")
    
    # 为无人机创建充电工作流（作为备用）
    for drone in drones:
        charging_workflow = create_charging_workflow(
            env,
            drone,
            battery_threshold=30.0,  # 电量低于20%时触发充电
            target_charge_level=90.0  # 充电目标电量
        )
    
    # 设置仿真结束时间
    end_time = 2000  # 仿真1000分钟
    
    # 运行仿真
    logger.info(f"开始物流仿真...")
    env.run(until=end_time)
    logger.info(f"仿真结束，总时长: {env.now} 分钟")
    
    # 打印仿真结果
    logger.info("\n物流仿真结果:")
    
    # 打印快递站状态
    for i, station in enumerate([station1, station2, station3]):
        logger.info(f"\n快递站{i+1} ({station.id}) 最终状态:")
        logger.info(f"位置: {station.get_state('position')}")
        logger.info(f"当前存储: {station.get_state('current_storage')}/{station.get_state('storage_capacity')}")
        logger.info(f"注册的无人机数量: {len(station.get_state('registered_logistics_drones'))}")
    
    # 打印无人机状态
    for i, drone in enumerate(drones[:5]):
        logger.info(f"\n无人机{i+1} ({drone.id}) 最终状态:")
        logger.info(f"位置: {drone.get_state('position')}")
        logger.info(f"电量: {drone.get_state('battery_level'):.1f}%")
        logger.info(f"状态: {drone.get_state('status')}")
        logger.info(f"是否携带货物: {drone.get_state('carrying_payload')}")
        if drone.get_state('carrying_payload'):
            logger.info(f"货物ID: {drone.get_state('payload_id')}")
    
    # 打印订单工作流状态
    logger.info("\n订单工作流状态:")
    for i, workflow in enumerate(order_workflows):
        source_station_id = workflow.owner.id
        target_agent_id = workflow.target_agent_id
        logger.info(f"订单{i+1} ({workflow.id}) 状态: {workflow.status_machine.state}")
        logger.info(f"  从 {source_station_id} 到 {target_agent_id}")
        logger.info(f"  物品ID: {workflow.payload_id}")
        logger.info(f"  分配的无人机: {workflow.assigned_drone}")
    
    # 打印货物状态
    logger.info("\n货物状态:")
    payloads = env.payload_manager.get_all_payloads()
    for payload_id, payload_info in payloads.items():
        # create time  pickup time  delivery time
        create_time = payload_info.get('create_time', '未知')
        pickup_time = payload_info.get('pickup_time', '未知')
        delivery_time = payload_info.get('delivery_time', '未知')
        source_agent_id = payload_info['source_agent_id']
        target_agent_id = payload_info['target_agent_id']
        logger.info(f"货物ID: {payload_id}, 创建时间: {create_time}, 取件时间: {pickup_time}, 投递时间: {delivery_time}, {source_agent_id} -> {target_agent_id}")
        # 打印货物的properties
        properties = payload_info.get('properties', {})
        logger.info(f"  货物属性: {properties}")

    return env, [station1, station2, station3], drones, order_workflows


if __name__ == "__main__":
    run_logistics_simulation()