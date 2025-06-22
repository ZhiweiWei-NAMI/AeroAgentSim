"""
AirFogSim物流无人机代理模块

该模块定义了物流无人机代理类，结合了无人机代理和物流代理的功能，实现了物流无人机的行为和状态管理。
主要功能包括：
1. 物流无人机状态管理
2. 货物携带和运输
3. 飞行和充电管理
4. 物流工作流集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.agent.drone import DroneAgent, DroneAgentMeta
from airfogsim.agent.delivery_agent import DeliveryAgent, DeliveryAgentMeta
from airfogsim.workflow.logistics import LogisticsWorkflow
from airfogsim.workflow.charging import ChargingWorkflow
from typing import Dict, List
import uuid
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class DeliveryDroneAgentMeta(DroneAgentMeta, DeliveryAgentMeta):
    """物流无人机代理元类，合并了无人机和物流代理的元类"""
    pass

class DeliveryDroneAgent(DroneAgent, DeliveryAgent, metaclass=DeliveryDroneAgentMeta):
    """物流无人机代理，能够执行物流任务和工作流，结合了无人机和物流代理的功能"""

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "物流无人机代理 - 能够执行物流任务，支持取件、运输和交付工作流"

    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        # 确保properties是一个字典
        properties = properties or {}

        # 生成一个唯一的agent_id
        agent_id = agent_id or f"agent_delivery_drone_{uuid.uuid4().hex[:8]}"

        # 调用DeliveryAgent的初始化方法，但传入相同的agent_id
        super().__init__(env, agent_name, properties, agent_id)

        # 确保必需的状态被设置
        if 'moving_status' not in self.state or self.state['moving_status'] is None:
            self.update_state('moving_status', 'idle')

        if 'battery_level' not in self.state or self.state['battery_level'] is None:
            self.update_state('battery_level', properties.get('battery_level', 100.0))

        if 'status' not in self.state or self.state['status'] is None:
            self.update_state('status', 'idle')

        # 确保DeliveryAgent的状态也被设置
        if 'delivery_status' not in self.state or self.state['delivery_status'] is None:
            self.update_state('delivery_status', 'idle')

        # 打印初始化后的状态，用于调试
        logger.info(f"初始化 {self.id} 完成，状态: moving_status={self.get_state('moving_status')}, battery_level={self.get_state('battery_level')}")

        # 物流无人机初始化完成

    def _on_payload_added(self, event_data):
        """响应货物添加事件"""
        # 调用DeliveryAgent的方法
        DeliveryAgent._on_payload_added(self, event_data)

        # 更新移动状态
        if self.get_state('delivery_status') == 'transporting':
            self.update_state('moving_status', 'flying')

    def _on_payload_removed(self, event_data):
        """响应货物移除事件"""
        # 调用DeliveryAgent的方法
        DeliveryAgent._on_payload_removed(self, event_data)

        # 如果没有剩余货物，更新移动状态
        if not self.get_state('payload_ids'):
            # 检查是否有活跃的工作流
            active_workflows = self.get_active_workflows()
            if not active_workflows:
                self.update_state('moving_status', 'idle')

    def register_event_listeners(self):
        """注册物流无人机需要监听的事件"""
        # 获取DroneAgent注册的事件监听器
        listeners = DroneAgent.register_event_listeners(self)

        # 添加DeliveryAgent特有的事件监听器
        delivery_listeners = [
            {
                'source_id': self.id,
                'event_name': 'possessing_object_added',
                'callback': self._on_payload_added
            },
            {
                'source_id': self.id,
                'event_name': 'possessing_object_removed',
                'callback': self._on_payload_removed
            }
        ]

        # 合并监听器列表，确保没有重复
        for listener in delivery_listeners:
            if listener not in listeners:
                listeners.append(listener)

        return listeners

    def _process_custom_logic(self):
        """执行物流无人机特定的逻辑"""
        # 获取当前活跃的工作流
        active_workflows = self.get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            self.update_state('moving_status', 'idle')
            self.update_state('delivery_status', 'idle')
            self.update_state('status', 'idle')
            return

        # 优先级排序：充电 > 物流 > 其他
        charging_workflow = None
        logistics_workflow = None

        for workflow in active_workflows:
            if isinstance(workflow, LogisticsWorkflow):
                logistics_workflow = workflow
            elif isinstance(workflow, ChargingWorkflow):
                charging_workflow = workflow

        # 检查是否需要优先处理充电
        charging_needed = False
        if charging_workflow and charging_workflow.status_machine.state in ['seeking_charger', 'charging']:
            charging_needed = True

        # 如果需要优先处理充电
        if charging_needed and charging_workflow:
            # 如果当前在充电，更新无人机状态
            if charging_workflow.status_machine.state == 'charging':
                self.update_state('moving_status', 'idle')
                self.update_state('status', 'active')

        # 如果不需要优先充电，处理物流工作流
        elif logistics_workflow:
            # 根据物流工作流状态更新无人机状态
            current_state = logistics_workflow.status_machine.state
            if current_state == 'picking_up':
                self.update_state('moving_status', 'flying')
                self.update_state('delivery_status', 'picking_up')
                self.update_state('status', 'active')
            elif current_state == 'transporting':
                self.update_state('moving_status', 'flying')
                self.update_state('delivery_status', 'transporting')
                self.update_state('status', 'active')
            elif current_state == 'delivering':
                self.update_state('moving_status', 'flying')
                self.update_state('delivery_status', 'delivering')
                self.update_state('status', 'active')

    def get_details(self) -> Dict:
        """获取代理详细信息，添加物流无人机相关信息"""
        # 获取DroneAgent的详细信息
        drone_details = DroneAgent.get_details(self)
        # 获取DeliveryAgent的详细信息
        delivery_details = DeliveryAgent.get_details(self)

        # 合并详细信息
        details = drone_details

        # 添加物流相关信息
        if 'payload_info' in delivery_details:
            details['payload_info'] = delivery_details['payload_info']

        # 添加物流工作流信息
        if 'logistics_workflows' in delivery_details:
            details['logistics_workflows'] = delivery_details['logistics_workflows']

        # 添加物流无人机特有信息
        details['type'] = 'DeliveryDroneAgent'
        details['combined_status'] = {
            'core_status': self.get_state('status'),
            'moving_status': self.get_state('moving_status'),
            'delivery_status': self.get_state('delivery_status')
        }

        return details