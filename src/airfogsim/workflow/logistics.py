"""
AirFogSim物流工作流模块

该模块定义了无人机物流工作流及其元类，实现了无人机取件、运输和交付货物的过程管理。
主要功能包括：
1. 货物(Payload)信息管理
2. 取件点和交付点管理
3. 取件、运输和交付状态转换
4. 状态机转换和事件触发
5. 动态任务生成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus
import uuid
from typing import List, Tuple, Any, Dict
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class LogisticsWorkflowMeta(WorkflowMeta):
    """物流工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册物流工作流专用的属性模板
        mcs.register_template(cls, 'pickup_location', (list, tuple), True,
                          lambda loc: len(loc) == 3 and all(isinstance(c, (int, float)) for c in loc),
                          "取件点坐标 (x, y, z)")

        mcs.register_template(cls, 'delivery_location', (list, tuple), True,
                          lambda loc: len(loc) == 3 and all(isinstance(c, (int, float)) for c in loc),
                          "交付点坐标 (x, y, z)")

        mcs.register_template(cls, 'payloads', list, True,
                          lambda payloads: all(isinstance(p, dict) and 'id' in p for p in payloads),
                          "货物列表，每个货物为字典，包含id和其他属性")

        mcs.register_template(cls, 'source_agent_id', str, True,
                          lambda agent_id: isinstance(agent_id, str),
                          "源代理ID，负责提供货物的代理")

        mcs.register_template(cls, 'target_agent_id', str, True,
                          lambda agent_id: isinstance(agent_id, str),
                          "目标代理ID，负责接收货物的代理")

        return cls

class LogisticsWorkflow(Workflow, metaclass=LogisticsWorkflowMeta):
    """
    物流工作流，管理无人机取件、运输和交付货物的过程。

    工作流状态流转：
    picking_up -> transporting -> delivering -> completed
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "物流工作流 - 管理无人机取件、运输和交付货物的过程"

    def __init__(self, env, name, owner, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 取件点坐标
        self.pickup_location = properties.get('pickup_location', [0, 0, 0])
        # 交付点坐标
        self.delivery_location = properties.get('delivery_location', [0, 0, 0])
        # 货物列表
        self.payloads = properties.get('payloads', [])
        # 源代理ID
        self.source_agent_id = properties.get('source_agent_id', 'unknown')
        # 目标代理ID
        self.target_agent_id = properties.get('target_agent_id', 'unknown')

        # 工作流的事件
        event_names = ['pickup_started', 'pickup_completed', 'transport_started',
                      'transport_completed', 'delivery_started', 'delivery_completed']

        super().__init__(
            env=env,
            name=name,
            owner=owner,
            timeout=timeout,
            event_names=event_names,
            initial_status=initial_status,
            callback=callback,
            properties=properties or {}
        )

        # 当前处理的货物ID
        self.current_payload_id = None

    def get_details(self) -> Dict:
        """获取工作流详细信息"""
        details = super().get_details()
        details['pickup_location'] = self.pickup_location
        details['delivery_location'] = self.delivery_location
        details['payloads'] = self.payloads
        details['source_agent_id'] = self.source_agent_id
        details['target_agent_id'] = self.target_agent_id
        details['description'] = f"物流工作流 - 从{self.source_agent_id}取件，交付到{self.target_agent_id}"
        return details

    def _is_at_pickup_location(self, position):
        """检查是否到达取件位置"""
        if not position or not self.pickup_location:
            return False

        # 检查是否在取件点附近（允许一定误差）
        tolerance = 1.0  # 1米误差范围
        return all(abs(position[i] - self.pickup_location[i]) <= tolerance for i in range(3))

    def _is_at_delivery_location(self, position):
        """检查是否到达交付位置"""
        if not position or not self.delivery_location:
            return False

        # 检查是否在交付点附近（允许一定误差）
        tolerance = 1.0  # 1米误差范围
        return all(abs(position[i] - self.delivery_location[i]) <= tolerance for i in range(3))

    def get_current_suggested_task(self):
        """
        获取当前状态下建议执行的任务

        根据当前状态机状态，动态生成任务信息。

        返回:
            Dict: 任务信息字典
            None: 如果没有找到匹配的任务
        """
        if not self.owner or self.status != WorkflowStatus.RUNNING:
            return None

        current_state = self.status_machine.state
        current_position = self.owner.get_state('position')

        # 根据当前状态生成对应的任务
        if current_state == 'picking_up':
            # 首先检查是否已经到达取件位置
            if not self._is_at_pickup_location(current_position):
                # 如果还没到达取件位置，需要先移动到取件位置
                return {
                    'component': 'MoveTo',
                    'task_class': 'MoveToTask',
                    'task_name': '移动到取件点',
                    'workflow_id': self.id,
                    'target_state': {'position': self.pickup_location},
                    'properties': {
                        'movement_type': 'path_following',
                        'target_position': self.pickup_location,
                        'priority': 'high'
                    }
                }
            else:
                # 如果已经到达取件位置，创建取件任务
                return {
                    'component': 'Logistics',
                    'task_class': 'PickupTask',
                    'task_name': '取件任务',
                    'workflow_id': self.id,
                    'target_state': {},
                    'properties': {
                        'payload_id': self.current_payload_id or self.payloads[0]['id'],
                        'pickup_location': self.pickup_location,
                        'source_agent_id': self.source_agent_id
                    }
                }
        elif current_state == 'transporting':
            # 创建运输任务
            return {
                'component': 'MoveTo',
                'task_class': 'MoveToTask',
                'task_name': '运输货物',
                'workflow_id': self.id,
                'target_state': {'position': self.delivery_location},
                'properties': {
                    'movement_type': 'path_following',
                    'target_position': self.delivery_location,
                    'priority': 'high'
                }
            }
        elif current_state == 'delivering':
            # 创建交付任务
            return {
                'component': 'Logistics',
                'task_class': 'HandoverTask',
                'task_name': '交付货物',
                'workflow_id': self.id,
                'target_state': {},
                'properties': {
                    'payload_id': self.current_payload_id,
                    'delivery_location': self.delivery_location,
                    'target_agent_id': self.target_agent_id
                }
            }

        return None

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，进入取件状态
        self.status_machine.set_start_transition('picking_up')

        # 添加从取件到运输的转换 - 使用payload_id作为key
        self.status_machine.add_transition(
            'picking_up',
            'transporting',
            event_trigger={
                'source_id': self.owner.id,
                'event_name': 'possessing_object_added',
                'value_key': 'object_name',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda obj_name: obj_name in [p['id'] for p in self.payloads]
            },
            callback=lambda context: setattr(
                self, 'current_payload_id', context['event_value']['object_name']
            )
        )

        # 添加从运输到交付的转换
        self.status_machine.add_transition(
            'transporting',
            'delivering',
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'position',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda pos: self._is_at_delivery_location(pos)
            }
        )

        # 添加从交付到完成的转换 - 使用payload_id作为key
        self.status_machine.add_transition(
            'delivering',
            'completed',
            event_trigger={
                'source_id': self.owner.id,
                'event_name': 'possessing_object_removed',
                'value_key': 'object_name',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda obj_name: obj_name == self.current_payload_id
            }
        )

        # 失败处理
        self.status_machine.add_transition(
            '*',
            'failed',
            event_trigger={
                'source_id': self.id,
                'event_name': 'status_changed',
                'value_key': 'new_status',
                'operator': TriggerOperator.EQUALS,
                'target_value': WorkflowStatus.FAILED
            }
        )

        # 添加状态回调
        self._add_state_callbacks()

    def _add_state_callbacks(self):
        """添加状态转换回调函数"""
        # 取件开始回调
        def on_pickup_started(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 开始取件，目标位置: {self.pickup_location}")
            # 触发取件开始事件
            self.env.event_registry.trigger_event(
                self.id, 'pickup_started',
                {
                    'agent_id': self.owner.id,
                    'pickup_location': self.pickup_location,
                    'time': self.env.now
                }
            )

        # 运输开始回调
        def on_transport_started(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 取件完成，开始运输货物 {self.current_payload_id}")
            # 触发取件完成和运输开始事件
            self.env.event_registry.trigger_event(
                self.id, 'pickup_completed',
                {
                    'agent_id': self.owner.id,
                    'payload_id': self.current_payload_id,
                    'time': self.env.now
                }
            )
            self.env.event_registry.trigger_event(
                self.id, 'transport_started',
                {
                    'agent_id': self.owner.id,
                    'payload_id': self.current_payload_id,
                    'time': self.env.now
                }
            )

        # 交付开始回调
        def on_delivery_started(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 到达交付点，开始交付货物 {self.current_payload_id}")
            # 触发运输完成和交付开始事件
            self.env.event_registry.trigger_event(
                self.id, 'transport_completed',
                {
                    'agent_id': self.owner.id,
                    'payload_id': self.current_payload_id,
                    'delivery_location': self.delivery_location,
                    'time': self.env.now
                }
            )
            self.env.event_registry.trigger_event(
                self.id, 'delivery_started',
                {
                    'agent_id': self.owner.id,
                    'payload_id': self.current_payload_id,
                    'time': self.env.now
                }
            )

        # 交付完成回调
        def on_delivery_completed(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 完成货物 {self.current_payload_id} 的交付")
            # 触发交付完成事件
            self.env.event_registry.trigger_event(
                self.id, 'delivery_completed',
                {
                    'agent_id': self.owner.id,
                    'payload_id': self.current_payload_id,
                    'time': self.env.now
                }
            )

        # 获取当前触发器
        transitions = self.status_machine.state_transitions

        # 为状态添加回调
        for state, trans_list in transitions.items():
            for trigger, next_state, desc in trans_list:
                if state == 'picking_up' and next_state == 'transporting':
                    trigger.add_callback(on_transport_started)
                elif state == 'transporting' and next_state == 'delivering':
                    trigger.add_callback(on_delivery_started)
                elif state == 'delivering' and next_state == 'completed':
                    trigger.add_callback(on_delivery_completed)


# 使用示例
def create_logistics_workflow(env, agent, pickup_location, delivery_location, payloads, source_agent_id, target_agent_id):
    """
    创建物流工作流

    Args:
        env: 仿真环境
        agent: 执行物流任务的代理
        pickup_location: 取件位置坐标
        delivery_location: 交付位置坐标
        payloads: 货物列表
        source_agent_id: 源代理ID，提供货物的代理
        target_agent_id: 目标代理ID，接收货物的代理

    Returns:
        LogisticsWorkflow: 创建的物流工作流
    """
    from airfogsim.core.trigger import TimeTrigger

    # 确保每个payload都有唯一ID
    for payload in payloads:
        if 'id' not in payload:
            payload['id'] = f"payload_{uuid.uuid4().hex[:8]}"

    workflow = env.create_workflow(
        LogisticsWorkflow,
        name=f"Logistics of {agent.id}",
        owner=agent,
        properties={
            'pickup_location': pickup_location,
            'delivery_location': delivery_location,
            'payloads': payloads,
            'source_agent_id': source_agent_id,
            'target_agent_id': target_agent_id
        },
        start_trigger=TimeTrigger(env, trigger_time=1+env.now, name='延迟启动派送工作流'),  # 1秒后启动
        max_starts=1  # 只启动一次
    )

    return workflow