from airfogsim.core import Workflow, WorkflowStatus, WorkflowMeta
from airfogsim.core.enums import TriggerOperator
import uuid
from typing import List, Tuple, Any

class ChargingWorkflowMeta(WorkflowMeta):
    """充电工作流元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 注册充电工作流专用的属性模板
        mcs.register_template(cls, 'charging_station', (tuple, list), True,
                            lambda pos: len(pos) == 3 and all(isinstance(x, (int, float)) for x in pos),
                            "充电站的3D位置坐标 (x, y, z)")
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
    """
    
    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return "充电工作流 - 监控无人机电池电量并在需要时引导其前往充电站充电"
    def __init__(self, env, name, owner, timeout=None, proof_id=None, 
                 event_names=[], initial_status='idle', callback=None, properties=None):
        # 充电站位置
        self.charging_station = properties.get('charging_station', [0, 0, 0])
        # 电池电量阈值
        self.battery_threshold = properties.get('battery_threshold', 50)  # 默认50%
        # 充电目标电量
        self.target_charge_level = properties.get('target_charge_level', 90)  # 默认90%
        
        # 工作流的事件
        event_names = ['charging_needed', 'charging_started', 'charging_completed']
        
        super().__init__(
            env=env,
            name=name,
            owner=owner,
            timeout=timeout,
            proof_id=proof_id or f"charging_proof_{uuid.uuid4().hex}",
            event_names=event_names,
            initial_status=initial_status,
            callback=callback,
            properties=properties or {}
        )
        
    def get_details(self):
        """获取工作流详细信息"""
        details = super().get_details()
        details['charging_station'] = self.charging_station
        details['battery_threshold'] = self.battery_threshold
        details['target_charge_level'] = self.target_charge_level
        details['proof_id'] = self.proof_id
        details['description'] = f"Monitor battery level and trigger charging when below {self.battery_threshold}%"
        return details
        
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
        
        # 根据当前状态生成对应的任务
        if current_state == 'seeking_charger':
            # 创建移动到充电站的任务
            return {
                'component': 'MoveTo',
                'task_class': 'MoveToTask',
                'task_name': '移动到充电站',
                'workflow_id': self.id,
                'proof_id': self.proof_id,
                'target_state': {'position': self.charging_station},
                'properties': {
                    'movement_type': 'direct',
                    'target_position': self.charging_station,
                    'priority': 'high'  # 电量低，高优先级
                }
            }
        elif current_state == 'charging':
            # 创建充电任务
            return {
                'component': 'Charging',
                'task_class': 'ChargingTask',
                'task_name': '电池充电',
                'workflow_id': self.id,
                'proof_id': self.proof_id,
                'target_state': {'battery_level': self.target_charge_level},
                'properties': {
                    'charging_type': 'normal',
                    'current_battery_level': self.owner.get_state('battery_level'),
                    'priority': 'normal'
                }
            }
            
        return None

    def _setup_transitions(self):
        """设置状态机转换规则"""
        # 工作流启动时，进入监控电量状态
        self.status_machine.set_start_transition('monitoring_battery')
        
        # 添加从监控到寻找充电站的转换
        self.status_machine.add_transition(
            'monitoring_battery',
            'seeking_charger',
            agent_state={
                'agent_id': self.owner.id,
                'state_key': 'battery_level',
                'operator': TriggerOperator.LESS_THAN,
                'target_value': self.battery_threshold
            }
        )
        
        # 添加从寻找充电站到充电状态的转换
        self.status_machine.add_transition(
            'seeking_charger',
            'charging',
            event_trigger={
                'source_id': self.proof_id,
                'event_name': 'proof_updated',
                'value_key': 'data.position',
                'operator': TriggerOperator.CUSTOM,
                'target_value': lambda position, station=self.charging_station: 
                    all([abs(position[i] - station[i]) < 1e-6 for i in range(3)]) if position else False
            }
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
            print(f"时间 {self.env.now}: 开始监控 {self.owner.id} 的电池电量")
            
        # 寻找充电站回调
        def on_seeking_charger(context):
            print(f"时间 {self.env.now}: {self.owner.id} 电量低于 {self.battery_threshold}%，开始寻找充电站")
            # 触发充电需求事件
            self.env.event_registry.trigger_event(
                self.id, 'charging_needed',
                {
                    'agent_id': self.owner.id,
                    'battery_level': self.owner.get_state('battery_level'),
                    'charging_station': self.charging_station,
                    'time': self.env.now
                }
            )
            
        # 充电中回调
        def on_charging_started(context):
            print(f"时间 {self.env.now}: {self.owner.id} 到达充电站，开始充电")
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
            print(f"时间 {self.env.now}: {self.owner.id} 充电完成，电量达到 {self.target_charge_level:.1f}%")
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
            for trigger, next_state in trans_list:
                if state == 'monitoring_battery' and next_state == 'seeking_charger':
                    trigger.add_callback(on_seeking_charger)
                elif state == 'seeking_charger' and next_state == 'charging':
                    trigger.add_callback(on_charging_started)
                elif state == 'charging' and next_state == 'completed':
                    trigger.add_callback(on_charging_completed)
                elif state == 'completed' and next_state == 'monitoring_battery':
                    trigger.add_callback(on_monitoring)


# 使用示例
def create_charging_workflow(env, agent, charging_station=None, battery_threshold=50, target_charge_level=90):
    """创建充电工作流"""
    from airfogsim.core.trigger import StateTrigger
    proof_id = f"charging_{uuid.uuid4().hex}"
    
    # 如果未指定充电站，使用默认位置
    if charging_station is None:
        charging_station = [0, 0, 0]  # 默认充电站位置
    
    workflow = env.create_workflow(
        ChargingWorkflow,
        name=f"Charging of {agent.id}",
        owner=agent,
        proof_id=proof_id,
        properties={
            'charging_station': charging_station,
            'battery_threshold': battery_threshold,
            'target_charge_level': target_charge_level
        },
        # 使用电池电量触发器作为启动条件
        start_trigger=StateTrigger(
            env, 
            agent_id=agent.id, 
            state_key='battery_level', 
            operator=TriggerOperator.LESS_THAN, 
            target_value=battery_threshold
        ),
        max_starts=None  # 允许无限次触发
    )
    
    return workflow