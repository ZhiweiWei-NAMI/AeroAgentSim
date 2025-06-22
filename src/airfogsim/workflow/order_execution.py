"""
AirFogSim订单执行工作流模块

该模块定义了订单执行工作流，负责管理物流订单的生成、分配和监控整个配送过程。
主要功能包括：
1. 物品(Payload)创建
2. 无人机分配
3. 创建物流工作流
4. 监控配送进度

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus
import uuid
from typing import List, Tuple, Any, Dict, Optional
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class OrderExecutionWorkflowMeta(WorkflowMeta):
    """订单执行工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册订单执行工作流专用的属性模板
        mcs.register_template(cls, 'payload_properties', dict, True,
                           lambda props: isinstance(props, dict),
                           "物品属性，包含物品的基本信息")

        mcs.register_template(cls, 'delivery_location', (list, tuple), True,
                           lambda loc: len(loc) == 3 and all(isinstance(c, (int, float)) for c in loc),
                           "交付点坐标 (x, y, z)")

        mcs.register_template(cls, 'assigned_drone', str, False,
                           lambda drone_id: isinstance(drone_id, str),
                           "分配的无人机ID")

        mcs.register_template(cls, 'target_agent_id', str, False,
                           lambda agent_id: isinstance(agent_id, str),
                           "目标代理ID")

        mcs.register_template(cls, 'logistics_workflow_id', str, False,
                           lambda wf_id: isinstance(wf_id, str),
                           "关联的物流工作流ID")

        return cls

class OrderExecutionWorkflow(Workflow, metaclass=OrderExecutionWorkflowMeta):
    """
    订单执行工作流，管理物流订单的生成、分配和监控整个配送过程。

    工作流状态流转：
    creating_payload -> assigning_drone -> in_delivery -> completed
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "订单执行工作流 - 管理物流订单的生成、分配和监控整个配送过程"

    def __init__(self, env, name, owner, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 确保属性不为空
        properties = properties or {}

        # 物品属性
        self.payload_properties = properties.get('payload_properties', {})
        # 交付点坐标
        self.delivery_location = properties.get('delivery_location', [0, 0, 0])
        # 分配的无人机ID
        self.assigned_drone = properties.get('assigned_drone', None)
        # 目标代理ID
        self.target_agent_id = properties.get('target_agent_id', 'unknown')
        # 关联的物流工作流ID
        self.logistics_workflow_id = properties.get('logistics_workflow_id', None)
        # 生成的物品ID
        self.payload_id = None

        # 工作流的事件
        event_names = ['payload_created', 'drone_assigned', 'delivery_started',
                      'delivery_completed', 'order_failed']

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

    def get_details(self) -> Dict:
        """获取工作流详细信息"""
        details = super().get_details()
        details['payload_properties'] = self.payload_properties
        details['delivery_location'] = self.delivery_location
        details['assigned_drone'] = self.assigned_drone
        details['target_agent_id'] = self.target_agent_id
        details['logistics_workflow_id'] = self.logistics_workflow_id
        details['payload_id'] = self.payload_id
        details['description'] = f"订单执行工作流 - 交付到{self.delivery_location}"
        return details

    def get_current_suggested_task(self):
        """
        order execution不需要建议的任务
        """
        return None

    def _check_payload_match(self, object_name):
        """
        检查payload是否符合当前订单的payload_properties

        Args:
            object_name: 对象名称，应该是payload_id

        Returns:
            bool: 是否匹配
        """
        # 检查对象名称是否以payload_开头
        if not object_name.startswith('payload_'):
            return False

        # 获取payload信息
        payload_info = self.owner.get_possessing_object(object_name)

        # 检查payload_info和self.payload_properties是否匹配
        if not payload_info or 'properties' not in payload_info:
            return False

        payload_properties = payload_info['properties']

        # 检查payload_properties是否包含当前订单的payload_properties
        for key, value in self.payload_properties.items():
            if key not in payload_properties or payload_properties[key] != value:
                return False

        # 如果匹配成功，设置payload_id
        self.payload_id = object_name
        return True

    def _check_logistics_workflow(self, workflow_info):
        """
        检查是否为当前订单对应的物流工作流

        Args:
            workflow_info: 工作流信息

        Returns:
            bool: 是否匹配
        """
        if not workflow_info or 'workflow_id' not in workflow_info:
            return False

        workflow_id = workflow_info['workflow_id']

        # 从工作流管理器获取工作流
        workflow = self.env.workflow_manager.get_workflow(workflow_id)
        if not workflow:
            return False

        # 检查是否为物流工作流
        if workflow.__class__.__name__ != 'LogisticsWorkflow':
            return False

        # 检查物流工作流的payload是否包含当前订单的payload_id
        if not hasattr(workflow, 'payloads') or not self.payload_id:
            return False

        # 检查payloads中是否包含当前payload_id
        for payload in workflow.payloads:
            if payload.get('id') == self.payload_id:
                # 设置物流工作流ID和分配的无人机
                self.logistics_workflow_id = workflow_id
                self.assigned_drone = workflow.owner.id if workflow.owner else None

                # 更新工作流属性
                self.properties['logistics_workflow_id'] = self.logistics_workflow_id
                self.properties['assigned_drone'] = self.assigned_drone

                # 触发无人机分配事件
                self.env.event_registry.trigger_event(
                    self.id, 'drone_assigned',
                    {
                        'drone_id': self.assigned_drone,
                        'logistics_workflow_id': self.logistics_workflow_id,
                        'time': self.env.now
                    }
                )

                return True

        return False

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，进入创建物品状态
        self.status_machine.set_start_transition('creating_payload')

        # 添加从创建物品到分配无人机的转换 - 监听agent的possessing_object_added事件
        self.status_machine.add_transition(
            'creating_payload',
            'assigning_drone',
            event_trigger={
                'source_id': self.owner.id,
                'event_name': 'possessing_object_added',
                'value_key': 'object_name',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda event_value: self._check_payload_match(event_value)
            },
            description="检测快递站生成的物品是否符合当前订单的物品属性",
            callback=lambda context: setattr(
                self, 'payload_id', context['event_value']['object_name']
            )
        )

        # 添加从分配无人机到配送中的转换 - 监听workflow_manager的workflow_registered事件
        self.status_machine.add_transition(
            'assigning_drone',
            'in_delivery',
            event_trigger={
                'source_id': self.env.workflow_manager.manager_id,
                'event_name': 'workflow_registered',
                'value_key': None,
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda event_value: self._check_logistics_workflow(event_value)
            },
            description="检测物流工作流的创建"
        )

        # 添加从配送中到完成的转换
        self.status_machine.add_transition(
            'in_delivery',
            'completed',
            event_trigger={
                'source_id': self.env.workflow_manager.manager_id,
                'event_name': 'workflow_completed',
                'value_key': 'workflow_id',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda wf_id: wf_id == self.logistics_workflow_id
            }
        )

        # 失败处理
        self.status_machine.add_transition(
            '*',
            'failed',
            event_trigger={
                'source_id': self.id,
                'event_name': 'order_failed',
                'value_key': 'reason',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda value: value is not None
            }
        )

        # 添加状态回调
        self._add_state_callbacks()

    def _handle_drone_assigned(self, context):
        """处理无人机分配完成事件"""
        event_value = context['event_value']
        self.assigned_drone = event_value['drone_id']
        self.logistics_workflow_id = event_value['logistics_workflow_id']

        # 更新工作流属性
        self.properties['assigned_drone'] = self.assigned_drone
        self.properties['logistics_workflow_id'] = self.logistics_workflow_id

    def _add_state_callbacks(self):
        """添加状态转换回调函数"""
        # 创建物品完成回调
        def on_payload_created(context):
            logger.info(f"时间 {self.env.now}: 订单 {self.id} 创建物品完成，物品ID: {self.payload_id}")

        # 分配无人机完成回调
        def on_drone_assigned(context):
            logger.info(f"时间 {self.env.now}: 订单 {self.id} 分配无人机完成，无人机ID: {self.assigned_drone}，物流工作流ID: {self.logistics_workflow_id}")
            # 触发配送开始事件
            self.env.event_registry.trigger_event(
                self.id, 'delivery_started',
                {
                    'payload_id': self.payload_id,
                    'drone_id': self.assigned_drone,
                    'logistics_workflow_id': self.logistics_workflow_id,
                    'time': self.env.now
                }
            )

        # 配送完成回调
        def on_delivery_completed(context):
            logger.info(f"时间 {self.env.now}: 订单 {self.id} 配送完成")
            # 触发配送完成事件
            self.env.event_registry.trigger_event(
                self.id, 'delivery_completed',
                {
                    'payload_id': self.payload_id,
                    'drone_id': self.assigned_drone,
                    'time': self.env.now
                }
            )

        # 获取当前触发器
        transitions = self.status_machine.state_transitions

        # 为状态添加回调
        for state, trans_list in transitions.items():
            for transition in trans_list:
                # 解包转换元组，可能是二元组或三元组
                if len(transition) == 2:
                    trigger, next_state = transition
                    description = None
                elif len(transition) == 3:
                    trigger, next_state, description = transition
                else:
                    raise ValueError("Invalid transition format")
                    continue

                # 添加回调
                if state == 'creating_payload' and next_state == 'assigning_drone':
                    trigger.add_callback(on_payload_created)
                elif state == 'assigning_drone' and next_state == 'in_delivery':
                    trigger.add_callback(on_drone_assigned)
                elif state == 'in_delivery' and next_state == 'completed':
                    trigger.add_callback(on_delivery_completed)


# 使用示例
def create_order_execution_workflow(env, station, payload_properties, delivery_location, target_agent_id, start_trigger=None):
    """
    创建订单执行工作流

    Args:
        env: 仿真环境
        station: 快递站代理
        payload_properties: 物品属性
        delivery_location: 交付位置坐标
        target_agent_id: 目标代理ID，如果为None则不指定目标代理
        start_trigger: 工作流启动触发器，默认为None

    Returns:
        OrderExecutionWorkflow: 创建的订单执行工作流
    """
    from airfogsim.core.trigger import TimeTrigger

    properties = {
        'payload_properties': payload_properties,
        'delivery_location': delivery_location
    }

    # 如果指定了目标代理ID，添加到属性中
    if target_agent_id:
        properties['target_agent_id'] = target_agent_id

    workflow = env.create_workflow(
        OrderExecutionWorkflow,
        name=f"Order of {station.id}",
        owner=station,
        properties=properties,
        start_trigger=TimeTrigger(env, interval=5) if start_trigger is None else start_trigger,
        max_starts=1  # 只启动一次
    )

    return workflow