"""
触发器系统示例
=============

这个示例展示了如何使用AirFogSim的触发器系统来创建和管理工作流。
"""

import simpy
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
from airfogsim.component.charging import ChargingComponent
from airfogsim.core.enums import WorkflowStatus, TriggerOperator
from airfogsim.manager.workflow import Workflow
from airfogsim.core.utils import calculate_distance
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

def run_trigger_example():
    # 创建环境
    env = Environment()
    
    # 创建代理
    drone = env.create_agent(DroneAgent, "Drone-1", properties={
        'max_speed': 10,  # m/s
        'battery_capacity': 5000.0,  # mAh
    })
    
    # 添加组件
    drone.add_component(ChargingComponent(env, drone, name="charging", properties={"charging_factor": 1.2}))

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
        operator=TriggerOperator.LESS_THAN,
        target_value=20,  # 电池电量低于20%时触发
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
            # 创建事件触发器
            location_change_trigger = self.env.create_event_trigger(
                source_id=agent_id,
                event_name='state_changed',
                value_key='key',
                operator=TriggerOperator.EQUALS,
                target_value='location',
                name="位置变更到提货点触发器"
            )
            # 添加回调来检查位置
            location_change_trigger.add_callback(lambda ctx: self._check_pickup_location(ctx))
            
            # 添加状态转换
            sm.add_transition(
                state='moving_to_pickup',
                next_status='picking_up_package',
                trigger=location_change_trigger
            )
            
            # 创建任务完成触发器
            pickup_complete_trigger = self.env.create_event_trigger(
                source_id=agent_id,
                event_name='task_completed',
                value_key='task.name',
                operator=TriggerOperator.EQUALS,
                target_value='pickup_task',
                name="提货任务完成触发器"
            )
            
            # 添加状态转换
            sm.add_transition(
                state='picking_up_package',
                next_status='moving_to_delivery',
                trigger=pickup_complete_trigger
            )
            
            # 创建事件触发器
            delivery_location_trigger = self.env.create_event_trigger(
                source_id=agent_id,
                event_name='state_changed',
                value_key='key',
                operator=TriggerOperator.EQUALS,
                target_value='location',
                name="位置变更到交付点触发器"
            )
            # 添加回调来检查位置
            delivery_location_trigger.add_callback(lambda ctx: self._check_delivery_location(ctx))
            
            # 添加状态转换
            sm.add_transition(
                state='moving_to_delivery',
                next_status='delivering_package',
                trigger=delivery_location_trigger
            )
            
            # 创建任务完成触发器
            delivery_complete_trigger = self.env.create_event_trigger(
                source_id=agent_id,
                event_name='task_completed',
                value_key='task.name',
                operator=TriggerOperator.EQUALS,
                target_value='delivery_task',
                name="交付任务完成触发器"
            )
            
            # 添加状态转换
            sm.add_transition(
                state='delivering_package',
                next_status='completed',
                trigger=delivery_complete_trigger
            )
        
        def _is_at_location(self, current_location, target_location):
            if not current_location or not target_location:
                return False
            distance = calculate_distance(current_location, target_location)
            return distance < 1.0
            
        def _check_pickup_location(self, context):
            """检查是否到达提货点"""
            if context.get('key') != 'location':
                return False
            current_location = context.get('new_value')
            return self._is_at_location(current_location, self.pickup_location)
            
        def _check_delivery_location(self, context):
            """检查是否到达交付点"""
            if context.get('key') != 'location':
                return False
            current_location = context.get('new_value')
            return self._is_at_location(current_location, self.delivery_location)
    
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
                logger.info(f"时间 {env.now}: 电池电量: {drone.get_state('battery_level')}%")
            yield env.timeout(10)
    
    env.process(battery_drain())
    
    # 运行模拟
    logger.info("开始模拟...")
    env.run(until=100)
    logger.info("模拟结束")

if __name__ == "__main__":
    run_trigger_example()