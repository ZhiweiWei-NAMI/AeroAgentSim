"""
AirFogSim物流代理模块

该模块定义了物流代理类及其元类，实现了物流任务的行为和状态管理。
主要功能包括：
1. 物流代理状态模板定义和管理
2. 货物携带状态管理
3. 物流任务执行逻辑
4. 物流工作流集成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.agent.terminal import TerminalAgent, TerminalAgentMeta
from airfogsim.workflow.logistics import LogisticsWorkflow
from typing import Dict, List
import uuid
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class DeliveryAgentMeta(TerminalAgentMeta):
    """物流代理元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册物流代理专用的状态模板
        mcs.register_template(cls, 'payload_ids', List[str], False, None,
                            "代理当前携带的货物ID")

        # 负载重量状态
        mcs.register_template(cls, 'current_payload_weight', (float, int), True, None,
                            "代理当前负载重量 (kg)")
        mcs.register_template(cls, 'max_payload_weight', (float, int), True, None,
                            "代理最大负载重量 (kg)")

        # 负载容积状态
        mcs.register_template(cls, 'current_payload_volume', (float, int), True, None,
                            "代理当前负载容积 (m³)")
        mcs.register_template(cls, 'max_payload_volume', (float, int), True, None,
                            "代理最大负载容积 (m³)")

        # 物流状态
        mcs.register_template(cls, 'delivery_status', str, True,
                            lambda s: s in ['idle', 'picking_up', 'transporting', 'delivering', 'pickup_completed', 'delivery_completed'],
                            "物流代理当前状态")

        return cls

class DeliveryAgent(TerminalAgent, metaclass=DeliveryAgentMeta):
    """物流代理，能够执行物流任务和工作流"""

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return "物流代理 - 能够执行物流任务，支持取件、运输和交付工作流"

    def __init__(self, env, agent_name: str, properties=None, agent_id=None):
        super().__init__(env, agent_name, properties)
        self.id = agent_id or f"agent_delivery_{uuid.uuid4().hex[:8]}"

        # 初始化物流相关状态
        self.update_state('payload_ids', [])
        self.update_state('current_payload_weight', 0.0)
        self.update_state('max_payload_weight', properties.get('max_payload_weight', 5.0))
        self.update_state('current_payload_volume', 0.0)
        self.update_state('max_payload_volume', properties.get('max_payload_volume', 0.125))  # 默认0.125立方米
        self.update_state('delivery_status', 'idle')
        self.update_state('status', 'idle')

        # 物流代理初始化完成

    def _on_payload_added(self, event_data):
        """响应货物添加事件"""
        if event_data.get('object_name').startswith('payload_'):
            payload_id = event_data.get('object_id')
            payload = self.get_possessing_object(payload_id)

            if payload:
                # 更新payload_ids状态
                self.update_state('payload_ids', self.get_state('payload_ids', []) + [payload_id])
                properties = payload.get('properties', {})
                # 更新负载重量
                payload_weight = properties.get('weight', 0.0)
                current_weight = self.get_state('current_payload_weight', 0.0)
                self.update_state('current_payload_weight', current_weight + payload_weight)

                # 计算并更新负载容积
                payload_volume = 0.0
                if 'dimensions' in properties:
                    # 假设dimensions是[长, 宽, 高]格式，单位为米
                    dimensions = properties['dimensions']
                    if len(dimensions) == 3:
                        payload_volume = dimensions[0] * dimensions[1] * dimensions[2]

                current_volume = self.get_state('current_payload_volume', 0.0)
                self.update_state('current_payload_volume', current_volume + payload_volume)

                # 更新状态为运输中
                self.update_state('delivery_status', 'transporting')
                self.update_state('status', 'active')

                logger.info(f"时间 {self.env.now}: {self.id} 开始携带货物 {payload_id}, 当前货物ids: {self.get_state('payload_ids')}")
                logger.info(f"\t\t {self.id} 当前负载重量: {self.get_state('current_payload_weight')}kg, 负载容积: {self.get_state('current_payload_volume')}m³")

    def _on_payload_removed(self, event_data):
        """响应货物移除事件"""
        if event_data.get('object_name').startswith('payload_'):
            payload_id = event_data.get('object_id')

            # 在移除前获取货物信息，用于更新重量和容积
            payload = None
            for pid in self.get_state('payload_ids', []):
                if pid == payload_id:
                    # 尝试从环境的payload_manager获取货物信息
                    if hasattr(self.env, 'payload_manager'):
                        payload = self.env.payload_manager.get_payload(payload_id)
                    break

            # 更新payload_ids状态
            self.update_state('payload_ids',
                              [pid for pid in self.get_state('payload_ids') if pid != payload_id])

            # 更新负载重量和容积
            if payload:
                properties = payload.get('properties', {})
                # 减去货物重量
                payload_weight = properties.get('weight', 0.0)
                current_weight = self.get_state('current_payload_weight', 0.0)
                new_weight = max(0.0, current_weight - payload_weight)
                self.update_state('current_payload_weight', new_weight)

                # 减去货物容积
                payload_volume = 0.0
                if 'dimensions' in properties:
                    dimensions = properties['dimensions']
                    if len(dimensions) == 3:
                        payload_volume = dimensions[0] * dimensions[1] * dimensions[2]

                current_volume = self.get_state('current_payload_volume', 0.0)
                new_volume = max(0.0, current_volume - payload_volume)
                self.update_state('current_payload_volume', new_volume)

            # 如果没有剩余货物，将重量和容积设为0
            if not self.get_state('payload_ids'):
                self.update_state('current_payload_weight', 0.0)
                self.update_state('current_payload_volume', 0.0)

            # 如果当前状态是delivering，更新为idle
            if self.get_state('delivery_status') == 'delivering':
                self.update_state('delivery_status', 'idle')
                self.update_state('status', 'idle')

            logger.info(f"时间 {self.env.now}: {self.id} 不再携带货物 {payload_id}")
            logger.info(f"\t\t {self.id} 当前负载重量: {self.get_state('current_payload_weight')}kg, 负载容积: {self.get_state('current_payload_volume')}m³")

    def register_event_listeners(self):
        """注册物流代理需要监听的事件"""
        # 获取基类注册的事件监听器
        listeners = super().register_event_listeners()

        # 添加物流代理特有的事件监听器
        listeners.extend([
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
        ])

        return listeners

    def _process_custom_logic(self):
        """执行物流代理特定的逻辑"""
        # 获取当前活跃的工作流
        active_workflows = self.get_active_workflows()
        if not active_workflows:
            # 如果没有活跃的工作流，则简单地保持空闲状态
            if self.get_state('delivery_status') not in ['transporting', 'delivering',
                                                'picking_up', 'pickup_completed', 'delivery_completed']:
                self.update_state('delivery_status', 'idle')
                self.update_state('status', 'idle')
            return

        # 检查是否有物流工作流
        logistics_workflow = None

        for workflow in active_workflows:
            if isinstance(workflow, LogisticsWorkflow):
                logistics_workflow = workflow
                break

        # 如果有物流工作流，处理物流工作流
        if logistics_workflow:
            # 根据物流工作流状态更新代理状态
            current_state = logistics_workflow.status_machine.state
            if current_state == 'picking_up':
                self.update_state('delivery_status', 'picking_up')
                self.update_state('status', 'active')
            elif current_state == 'transporting':
                self.update_state('delivery_status', 'transporting')
                self.update_state('status', 'active')
            elif current_state == 'delivering':
                self.update_state('delivery_status', 'delivering')
                self.update_state('status', 'active')

    def get_details(self) -> Dict:
        """获取代理详细信息，添加物流相关信息"""
        details = super().get_details()

        # 添加物流相关信息
        payload_ids = self.get_state('payload_ids', [])
        if payload_ids:
            # 获取所有携带的货物信息
            payloads = []
            for payload_id in payload_ids:
                payload = self.get_possessing_object(payload_id)
                if payload:
                    payloads.append(payload)

            details['payload_info'] = {
                'ids': payload_ids,
                'count': len(payload_ids),
                'payloads': payloads,
                'current_weight': self.get_state('current_payload_weight', 0.0),
                'max_weight': self.get_state('max_payload_weight', 5.0),
                'current_volume': self.get_state('current_payload_volume', 0.0),
                'max_volume': self.get_state('max_payload_volume', 0.125)
            }

        # 添加物流工作流信息
        logistics_workflows = []
        for workflow in self.get_active_workflows():
            if isinstance(workflow, LogisticsWorkflow):
                logistics_workflows.append({
                    'id': workflow.id,
                    'name': workflow.name,
                    'state': workflow.status_machine.state,
                    'pickup_location': workflow.pickup_location,
                    'delivery_location': workflow.delivery_location
                })

        if logistics_workflows:
            details['logistics_workflows'] = logistics_workflows

        return details
