"""
AirFogSim物流工作流示例

该示例展示了如何使用物流工作流模拟无人机的取件、运输和交付过程。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.environment import Environment
from airfogsim.agent.delivery_drone import DeliveryDroneAgent
from airfogsim.component.logistics import LogisticsComponent
from airfogsim.component.mobility import MoveToComponent
from airfogsim.component.charging import ChargingComponent
from airfogsim.workflow.logistics import LogisticsWorkflow, create_logistics_workflow
from airfogsim.workflow.charging import ChargingWorkflow, create_charging_workflow
from airfogsim.core.trigger import TimeTrigger

def run_logistics_simulation():
    """运行物流工作流示例"""
    # 创建仿真环境
    env = Environment()
    
    # 创建物流无人机
    drone = DeliveryDroneAgent(
        env, 
        "物流无人机1",
        properties={
            'position': [0, 0, 10],  # 初始位置
            'battery_level': 90.0,   # 初始电量
        },
        agent_id="logistics_drone_1"
    )
    
    env.register_agent(drone)

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
    
    # 定义取件点和交付点
    pickup_location = [10, 10, 0]
    delivery_location = [50, 50, 0]
    
    # 定义货物信息
    payloads = [
        {
            'id': 'package_001',
            'weight': 2.5,  # kg
            'dimensions': [0.3, 0.2, 0.15],  # m
            'description': '电子产品'
        }
    ]
    
    # 创建物流工作流
    logistics_workflow = create_logistics_workflow(
        env,
        drone,
        pickup_location,
        delivery_location,
        payloads
    )
    
    # 创建充电工作流（作为备用）
    charging_workflow = create_charging_workflow(
        env,
        drone,
        battery_threshold=20.0,  # 电量低于20%时触发充电
        target_charge_level=90.0  # 充电目标电量
    )
    
    # 设置仿真结束时间
    end_time = 1000  # 仿真1000分钟
    
    # 运行仿真
    print(f"开始物流仿真...")
    env.run(until=end_time)
    print(f"仿真结束，总时长: {env.now} 分钟")
    
    # 打印仿真结果
    print("\n物流仿真结果:")
    print(f"无人机最终位置: {drone.get_state('position')}")
    print(f"无人机最终电量: {drone.get_state('battery_level'):.1f}%")
    print(f"无人机最终状态: {drone.get_state('status')}")
    print(f"物流工作流最终状态: {logistics_workflow.status_machine.state}")
    
    return env, drone, logistics_workflow

if __name__ == "__main__":
    run_logistics_simulation()