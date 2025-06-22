"""
AirFogSim充电工作流模块

该模块定义了无人机充电工作流及其元类，实现了无人机电池电量监控和充电过程管理。
主要功能包括：
1. 电池电量监控和阈值触发
2. 充电站资源请求和分配
3. 充电站寻找和导航
4. 充电过程管理
5. 状态机转换和事件触发
6. 动态任务生成

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core import Workflow, WorkflowMeta
from airfogsim.core.enums import TriggerOperator, WorkflowStatus, TaskPriority
import uuid
from typing import List, Tuple, Any, Dict
from airfogsim.utils.logging_config import get_logger

logger = get_logger(__name__)

class ChargingWorkflowMeta(WorkflowMeta):
    """充电工作流元类"""

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)

        # 注册充电工作流专用的属性模板
        mcs.register_template(cls, 'battery_threshold', (float, int), True,
                            lambda lvl: 0 <= lvl <= 100,
                            "触发充电的电池电量阈值 (0-100)")
        mcs.register_template(cls, 'target_charge_level', (float, int), True,
                            lambda lvl: 0 <= lvl <= 100,
                            "充电目标电量 (0-100)")

        return cls

class ChargingWorkflow(Workflow, metaclass=ChargingWorkflowMeta):
    """
    充电工作流，监控代理的电池电量并在电量低时引导代理前往充电站充电。
    通过监听代理的 battery_level 状态来触发充电行为。

    工作流状态流转：
    monitoring_battery -> requesting_charger -> seeking_charger -> charging -> completed -> monitoring_battery
    """

    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "充电工作流 - 监控无人机电池电量并在需要时引导其前往充电站充电"

    def __init__(self, env, name, owner, timeout=None,
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 电池电量阈值
        self.battery_threshold = properties.get('battery_threshold', 50)  # 默认50%
        # 充电目标电量
        self.target_charge_level = properties.get('target_charge_level', 90)  # 默认90%

        # 工作流的事件
        event_names = ['charging_station_requested', 'charging_started', 'charging_completed']

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

        self.charging_station_id = None

    def get_details(self) -> Dict:
        """获取工作流详细信息"""
        details = super().get_details()
        details['battery_threshold'] = self.battery_threshold
        details['target_charge_level'] = self.target_charge_level
        details['description'] = f"监控电池电量，在低于 {self.battery_threshold}% 时触发充电"
        return details

    def get_current_suggested_task(self):
        """
        获取当前状态下建议执行的任务

        根据当前状态机状态，动态生成任务信息。
        任务会继承工作流的优先级和抢占属性。

        返回:
            Dict: 任务信息字典
            None: 如果没有找到匹配的任务
        """
        if not self.owner or self.status != WorkflowStatus.RUNNING:
            return None

        current_state = self.status_machine.state
        task_dict = None

        # 根据当前状态生成对应的任务
        if current_state == 'requesting_charger':
            charging_station = self.env.landing_manager.find_nearest_landing_spot(
                        x=self.owner.get_state('position')[0],
                        y=self.owner.get_state('position')[1],
                        require_charging=True
                    )
            if not charging_station:
                return None
            # 创建请求充电站的任务
            task_dict = {
                'component': 'Charging',
                'task_class': 'RequestChargingStationTask',
                'task_name': '请求充电站',
                'workflow_id': self.id,
                'target_state': {},
                'properties': {
                    'charging_station_id': charging_station.id,
                    'refresh_interval': 1,
                    'charging_efficiency': 0.95  # 充电效率
                }
            }
        elif current_state == 'seeking_charger':
            # 创建移动到充电站的任务
            task_dict = {
                'component': 'MoveTo',
                'task_class': 'MoveToTask',
                'task_name': '移动到充电站',
                'workflow_id': self.id,
                'target_state': {'position': self.env.landing_manager.get_landing_spot(self.charging_station_id).location},
                'properties': {}
            }
        elif current_state == 'charging':
            # 创建充电任务
            task_dict = {
                'component': 'Charging',
                'task_class': 'ChargingTask',
                'task_name': '电池充电',
                'workflow_id': self.id,
                'target_state': {'battery_level': self.target_charge_level},
                'properties': {
                    'charging_efficiency': 0.95  # 充电效率
                }
            }

        # 添加优先级和抢占属性
        return self._add_priority_to_task(task_dict)

    def _add_priority_to_task(self, task_dict: Dict) -> Dict:
        """为任务添加优先级和抢占属性"""
        if task_dict is None:
            return None
        task_priority = TaskPriority.CRITICAL  # 充电任务默认使用最高优先级
        task_dict['priority'] = task_priority.name.lower()
        task_dict['preemptive'] = True
        return task_dict

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，进入监控电量状态
        self.status_machine.set_start_transition('monitoring_battery')

        # 添加从监控到请求充电站的转换
        self.status_machine.add_transition(
            'monitoring_battery',
            'requesting_charger',
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'battery_level',
                'operator': TriggerOperator.LESS_THAN,
                'target_value': self.battery_threshold
            }
        )

        # 添加从请求充电站到寻找充电站的转换
        self.status_machine.add_transition(
            'requesting_charger',
            'seeking_charger',
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'charging_station.current_allocations',
                'operator': TriggerOperator.CONTAINS,
                'target_value': self.owner.id
            },
            callback=lambda context: setattr(
                self, 'charging_station_id', self.owner.get_possessing_object('charging_station').id
            )
        )

        # 添加从寻找充电站到充电状态的转换
        self.status_machine.add_transition(
            'seeking_charger',
            'charging',
            # state_changed
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'position',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda pos: self.env.landing_manager.get_landing_spot(
                    self.charging_station_id).is_within_range(pos[0], pos[1], pos[2])
            },
        )

        # 添加从充电到完成的转换
        self.status_machine.add_transition(
            'charging',
            'completed',
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'battery_level',
                'operator': TriggerOperator.GREATER_EQUAL,
                'target_value': self.target_charge_level
            }
        )

        # 添加充电完成后回到监控状态的转换（循环监控）
        self.status_machine.add_transition(
            'completed',
            'monitoring_battery',
            time_trigger={
                'interval': 10  # 10秒后回到监控状态
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
        # 监控状态回调
        def on_monitoring(context):
            logger.info(f"时间 {self.env.now}: 开始监控 {self.owner.id} 的电池电量")

        # 请求充电站回调
        def on_requesting_charger(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 电量低于 {self.battery_threshold}%，开始请求充电站")
            # 触发充电需求事件
            self.env.event_registry.trigger_event(
                self.id, 'charging_station_requested',
                {
                    'agent_id': self.owner.id,
                    'battery_level': self.owner.get_state('battery_level'),
                    'charging_station_id': self.charging_station_id,
                    'time': self.env.now
                }
            )


        # 充电中回调
        def on_charging_started(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 到达充电站，开始充电")
            # 触发充电开始事件
            self.env.event_registry.trigger_event(
                self.id, 'charging_started',
                {
                    'agent_id': self.owner.id,
                    'battery_level': self.owner.get_state('battery_level'),
                    'time': self.env.now
                }
            )

        # 充电完成回调
        def on_charging_completed(context):
            logger.info(f"时间 {self.env.now}: {self.owner.id} 充电完成，电量达到 {self.target_charge_level:.1f}%")
            # 触发充电完成事件
            self.env.event_registry.trigger_event(
                self.id, 'charging_completed',
                {
                    'agent_id': self.owner.id,
                    'battery_level': self.owner.get_state('battery_level'),
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
                if state == 'monitoring_battery' and next_state == 'requesting_charger':
                    trigger.add_callback(on_requesting_charger)
                elif state == 'seeking_charger' and next_state == 'charging':
                    trigger.add_callback(on_charging_started)
                elif state == 'charging' and next_state == 'completed':
                    trigger.add_callback(on_charging_completed)
                elif state == 'completed' and next_state == 'monitoring_battery':
                    trigger.add_callback(on_monitoring)


# 使用示例
def create_charging_workflow(env, agent, charging_station=None, battery_threshold=50, target_charge_level=90,
                          task_priority=None, task_preemptive=True):
    """
    创建充电工作流

    Args:
        env: 价格环境
        agent: 需要充电的代理
        charging_station: 充电站位置
        battery_threshold: 电量阈值，低于该值时触发充电
        target_charge_level: 目标电量水平
        task_priority: 任务优先级，可以是TaskPriority枚举或字符串
        task_preemptive: 任务是否可抢占

    Returns:
        创建的充电工作流
    """
    from airfogsim.core.trigger import StateTrigger
    from airfogsim.core.enums import TaskPriority

    # 如果没有指定优先级，使用默认值
    if task_priority is None:
        task_priority = TaskPriority.CRITICAL  # 充电任务默认使用最高优先级

    # 如果未指定充电站，使用默认位置
    if charging_station is None:
        charging_station = [0, 0, 0]  # 默认充电站位置

    workflow = env.create_workflow(
        ChargingWorkflow,
        name=f"Charging of {agent.id}",
        owner=agent,
        properties={
            'charging_station': charging_station,
            'battery_threshold': battery_threshold,
            'target_charge_level': target_charge_level,
            'charging_station_id': f"charging_station_{uuid.uuid4().hex[:8]}",
            'task_priority': task_priority,
            'task_preemptive': task_preemptive
        },
        # 使用电池电量触发器作为启动条件
        start_trigger=StateTrigger(
            env,
            agent_id=agent.id,
            state_key='battery_level',
            operator=TriggerOperator.LESS_THAN,
            target_value=battery_threshold,
            name='battery_level_trigger',
        ),
        max_starts=None  # 允许无限次触发
    )

    return workflow