"""
触发器系统示例
=============

这个示例展示了如何使用AirFogSim的触发器系统来创建和管理工作流。
"""

import simpy
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.charging import ChargingComponent
from airfogsim.core.enums import WorkflowStatus
from airfogsim.manager.workflow import Workflow
from airfogsim.core.utils import calculate_distance

def run_trigger_example():
    # 创建环境
    env = Environment()
    
    # 创建代理
    drone = env.create_agent(DroneAgent, "Drone-1", properties={
        'max_speed': 10,  # m/s
        'battery_capacity': 5000.0,  # mAh
    })
    
    # 添加组件
    drone.add_component(ChargingComponent(env, drone, name="charging", charging_factor=1.2))

    # 初始化状态
    drone.initialize_states(
        location=(0, 0, 0),  # x, y, z coordinates
        status="idle",
        battery_level=100.0,  # percentage
        payload={}
    )
    
    # 创建基于事件的触发器
    event_trigger = env.create_event_trigger(
        source_id="agent_manager",
        event_name="agent_registered",
        name="新代理注册触发器"
    )
    
    # 创建基于状态的触发器
    battery_low_trigger = env.create_state_trigger(
        agent_id=drone.id,
        state_key="battery_level",
        condition_func=lambda value: value < 20,  # 电池电量低于20%时触发
        check_interval=1.0,  # 每1秒检查一次
        name="电池电量低触发器"
    )
    
    # 创建基于时间的触发器
    maintenance_trigger = env.create_time_trigger(
        interval=60,  # 每60秒触发一次
        name="定期维护触发器"
    )
    
    # 创建组合触发器
    composite_trigger = env.create_composite_trigger(
        triggers=[battery_low_trigger, maintenance_trigger],
        operator_type="or",  # 任一触发器触发即可
        name="电池低或维护时间触发器"
    )
    
    # 定义工作流类
    class DeliveryWorkflow(Workflow):
        def __init__(self, env, name, owner, **kwargs):
            super().__init__(env, name, owner, **kwargs)
            self.pickup_location = kwargs.get('properties', {}).get('pickup_location')
            self.delivery_location = kwargs.get('properties', {}).get('delivery_location')
            self.package_id = kwargs.get('properties', {}).get('package_id')
            self.priority = kwargs.get('properties', {}).get('priority')
        
        def _setup_transitions(self):
            sm = self.status_machine
            agent_id = self.owner.id
            
            sm.set_start_transition('moving_to_pickup')
            
            sm.add_transition(
                state='moving_to_pickup',
                source_id=agent_id,
                event_name='state_changed',
                next_status='picking_up_package',
                condition_func=lambda ev: (
                    ev.get('key') == 'location' and
                    self._is_at_location(ev.get('new_value'), self.pickup_location)
                )
            )
            
            sm.add_transition(
                state='picking_up_package',
                source_id=agent_id,
                event_name='task_completed',
                next_status='moving_to_delivery',
                condition_func=lambda ev: ev.get('task', {}).get('name') == 'pickup_task'
            )
            
            sm.add_transition(
                state='moving_to_delivery',
                source_id=agent_id,
                event_name='state_changed',
                next_status='delivering_package',
                condition_func=lambda ev: (
                    ev.get('key') == 'location' and
                    self._is_at_location(ev.get('new_value'), self.delivery_location)
                )
            )
            
            sm.add_transition(
                state='delivering_package',
                source_id=agent_id,
                event_name='task_completed',
                next_status='completed',
                condition_func=lambda ev: ev.get('task', {}).get('name') == 'delivery_task'
            )
        
        def _is_at_location(self, current_location, target_location):
            if not current_location or not target_location:
                return False
            distance = calculate_distance(current_location, target_location)
            return distance < 1.0
    
    # 创建工作流并使用触发器启动
    delivery_workflow = env.create_workflow(
        workflow_class=DeliveryWorkflow,
        name="Package Delivery",
        owner=drone,
        start_trigger=composite_trigger,  # 使用组合触发器启动工作流
        properties={
            'pickup_location': (10, 10, 0),
            'delivery_location': (20, 20, 0),
            'package_id': 'PKG-001',
            'priority': 'high'
        }
    )

        
    env.task_manager.find_compatible_tasks(drone, workflow=delivery_workflow)
    
    # 模拟电池电量下降
    def battery_drain():
        while True:
            current_level = drone.get_state('battery_level')
            if current_level > 0:
                drone.update_state('battery_level', current_level - 5)
                print(f"时间 {env.now}: 电池电量: {drone.get_state('battery_level')}%")
            yield env.timeout(10)
    
    env.process(battery_drain())
    
    # 运行模拟
    print("开始模拟...")
    env.run(until=100)
    print("模拟结束")

if __name__ == "__main__":
    run_trigger_example()