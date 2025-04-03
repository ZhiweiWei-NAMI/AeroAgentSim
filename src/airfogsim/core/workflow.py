"""
AirFogSim工作流(Workflow)核心模块

该模块定义了仿真系统中的工作流框架，工作流是代理执行的一系列任务的集合，
通过状态机实现状态转换和任务调度。主要内容包括：
1. Workflow类：工作流基类，定义了工作流的生命周期和状态管理
2. WorkflowStatusMachine类：工作流状态机，基于各种触发器实现状态转换
3. WorkflowPropertyTemplate类：工作流属性模板，用于验证和管理属性
4. WorkflowMeta元类：处理属性模板和状态转换任务的继承

工作流系统支持基于事件、状态、时间和组合条件的触发机制，实现了复杂的
自动化任务调度和状态转换。

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.enums import WorkflowStatus, TaskStatus, TriggerOperator, TriggerType
from airfogsim.core.trigger import StateTrigger, EventTrigger, TimeTrigger, CompositeTrigger, Trigger
from collections import defaultdict, deque
from typing import List, Optional, Dict, Tuple, Callable, Set, Any, Union, Type
import uuid
import simpy
import warnings
from abc import abstractmethod, ABC


class WorkflowPropertyTemplate:
    """工作流属性模板定义"""
    
    def __init__(self, key, value_type=None, required=False, validator=None, description=None):
        """
        定义工作流属性的模板
        
        参数:
            key: 属性键名
            value_type: 值的类型(如int, float, str等)，None表示任意类型
            required: 是否为必需属性
            validator: 可选的验证函数，接收值并返回布尔值
            description: 属性的描述
        """
        self.key = key
        self.value_type = value_type
        self.required = required
        self.validator = validator
        self.description = description
    
    def validate(self, value):
        """验证值是否符合模板要求"""
        # 类型检查
        if self.value_type is not None and not isinstance(value, self.value_type):
            return False, f"值类型应为 {self.value_type.__name__}，而非 {type(value).__name__}"
        
        # 使用自定义验证器
        if self.validator is not None:
            try:
                if not self.validator(value):
                    return False, f"值 '{value}' 未通过自定义验证"
            except Exception as e:
                return False, f"验证时发生错误: {str(e)}"
        
        return True, None

class WorkflowMeta(type):
    """Workflow元类，用于处理属性模板和状态转换任务继承"""
    
    def __new__(mcs, name, bases, attrs):
        # 确保每个类都有自己独立的模板字典和状态转换任务字典
        # 不要直接从父类继承引用
        attrs['_own_property_templates'] = {}
        attrs['_own_transition_tasks'] = {}
        
        # 创建类
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 初始化聚合的模板字典和状态转换任务字典
        cls._all_property_templates = {}
        cls._all_transition_tasks = {}
        
        # 收集所有父类的模板和状态转换任务
        for base in bases:
            if hasattr(base, '_all_property_templates'):
                cls._all_property_templates.update(base._all_property_templates)
            if hasattr(base, '_all_transition_tasks'):
                cls._all_transition_tasks.update(base._all_transition_tasks)
        
        return cls
    
    @classmethod
    def register_template(mcs, cls, key, value_type=None, required=False, validator=None, description=None):
        """注册属性模板到指定类"""
        
        # 确保类有自己的模板存储
        if not hasattr(cls, '_own_property_templates'):
            cls._own_property_templates = {}
        
        # 创建模板
        template = WorkflowPropertyTemplate(key, value_type, required, validator, description)
        
        # 存储到当前类自己的模板中
        cls._own_property_templates[key] = template
        
        # 同时更新聚合的模板集合
        if not hasattr(cls, '_all_property_templates'):
            cls._all_property_templates = {}
            
        cls._all_property_templates[key] = template
        
        return template
        

class WorkflowStatusMachine:
    """Drives state transitions based on external events and triggers."""
    def __init__(self, workflow: 'Workflow', initial_status='idle'):
        self.workflow = workflow # The Workflow object holding this SM
        self.env = workflow.env
        self.current_status = initial_status
        self.start_status = initial_status
        self.state_transitions: Dict[str, List[Tuple[Trigger, str]]] = defaultdict(list)
        self.terminal_status = {'completed', 'failed', 'canceled'} 
        self.process: Optional[simpy.Process] = None
        self.wildcard_transitions: List[Tuple[Trigger, str]] = []
        self.active_triggers: Dict[str, Trigger] = {}  # trigger_id -> Trigger
        self._monitor_process_active = False
        
    def add_transition(self, state, next_status: str, 
                      agent_state: Optional[Dict] = None,
                      time_trigger: Optional[Dict] = None,
                      event_trigger: Optional[Dict] = None,
                      trigger: Optional[Trigger] = None, callback: Optional[Callable] = None):
        """
        添加状态转换，支持多种触发方式
        
        Args:
            state: 起始状态(字符串或状态列表)
            next_status: 目标状态
            agent_state: 代理状态触发配置 {agent_id, state_key, operator, target_value}
            time_trigger: 时间触发配置 {trigger_time, interval, cron_expr}
            event_trigger: 事件触发配置 {source_id, event_name, value_key, operator, target_value}
            trigger: 自定义触发器（优先级最高）
            callback: 触发器回调函数（可选）
        """
        # 创建触发器
        if trigger:
            # 使用提供的自定义触发器
            t = trigger
        elif agent_state:
            # 创建代理状态触发器
            agent_id = agent_state.get('agent_id')
            state_key = agent_state.get('state_key')
            operator = agent_state.get('operator', TriggerOperator.EQUALS)
            target_value = agent_state.get('target_value')
            
            if not agent_id or not state_key:
                raise ValueError("agent_state 必须包含 agent_id 和 state_key")
                
            t = StateTrigger(
                self.env,
                agent_id=agent_id,
                state_key=state_key,
                operator=operator,
                target_value=target_value,
                name=f"wf_{self.workflow.id}_agent_{state}_{next_status}"
            )
        elif time_trigger:
            # 创建时间触发器
            trigger_time = time_trigger.get('trigger_time')
            interval = time_trigger.get('interval')
            cron_expr = time_trigger.get('cron_expr')
            
            t = TimeTrigger(
                self.env,
                trigger_time=trigger_time,
                interval=interval,
                cron_expr=cron_expr,
                name=f"wf_{self.workflow.id}_time_{state}_{next_status}"
            )
        elif event_trigger:
            # 创建事件触发器
            source_id = event_trigger.get('source_id')
            event_name = event_trigger.get('event_name')
            value_key = event_trigger.get('value_key')
            operator = event_trigger.get('operator')
            target_value = event_trigger.get('target_value')
            
            if not source_id or not event_name:
                raise ValueError("event_trigger 必须包含 source_id 和 event_name")
                
            t = EventTrigger(
                self.env,
                source_id=source_id,
                event_name=event_name,
                value_key=value_key,
                operator=operator,
                target_value=target_value,
                name=f"wf_{self.workflow.id}_event_{state}_{next_status}"
            )
        else:
            raise ValueError("必须提供至少一种触发方式")
            
        # 设置回调
        if callback:
            t.add_callback(callback)
        # 添加转换
        transition = (t, next_status)
        if state == "*":
            self.wildcard_transitions.append(transition)
        elif isinstance(state, str):
            self.state_transitions[state].append(transition)
        elif isinstance(state, (list, tuple)):
            for s in state:
                self.state_transitions[s].append(transition)
                
        return self

    def set_start_transition(self, start_status: str):
        """默认当workflow running后，直接进入的状态"""
        self.start_status = start_status

    def start(self):
        self._change_status(self.start_status)
        if self.process is None or not self.process.is_alive:
            if not self._monitor_process_active:
                self._monitor_process_active = True
                self.process = self.env.process(self._monitor_events())
                self.process.callbacks.append(self._monitor_finished) # Use SimPy callback
                self.workflow.sm_process = self.process # Link back to workflow
        return self.process

    def _monitor_finished(self, event):
        self._monitor_process_active = False
        # If SM process fails unexpectedly, ensure workflow status reflects it
        if not event.ok and self.workflow.status not in (WorkflowStatus.FAILED, WorkflowStatus.CANCELED):
             print(f"时间 {self.env.now}: Workflow {self.workflow.id} state machine process failed unexpectedly: {event.cause}")
             self._change_status('failed', {'reason': f"State machine process failed: {event.cause}"})

    def _monitor_events(self):
        try:
            while not self.is_in_terminal_status():                    
                transitions = self._get_current_transitions()

                if not transitions:
                    if not self.is_in_terminal_status():
                        print(f"时间 {self.env.now}: 工作流 {self.workflow.id} 状态机在状态 {self.current_status} 没有转换规则，标记为失败。")
                        self._change_status('failed', {"reason": f"No transitions defined from state {self.current_status}"})
                    break

                # 停用所有活动的触发器
                self._deactivate_all_triggers()
                
                # 激活当前状态的触发器
                for trigger, next_status in transitions:
                    trigger_id = trigger.id
                    if trigger_id not in self.active_triggers:
                        # 设置触发器回调
                        trigger.add_callback(
                            lambda ctx, t=trigger, ns=next_status: self._on_trigger_activated(t, ns, ctx)
                        )
                        # 激活触发器
                        trigger.activate()
                        # 记录活动的触发器
                        self.active_triggers[trigger_id] = trigger
                
                if not self.active_triggers:
                    if not self.is_in_terminal_status():
                        print(f"时间 {self.env.now}: 工作流 {self.workflow.id} 状态机在状态 {self.current_status} 没有可激活的触发器，标记为失败。")
                        self._change_status('failed', {"reason": f"No triggers to activate in state {self.current_status}"})
                    break

                # 等待直到被中断（触发器回调会中断此进程）
                try:
                    yield self.env.timeout(float('inf'))
                except simpy.Interrupt as i:
                    # 触发器被激活，状态变更已在回调中处理
                    pass

        except simpy.Interrupt as i:
             print(f"时间 {self.env.now}: 工作流 {self.workflow.id} 状态机被中断: {i.cause}")
             reason = i.cause.get('reason', str(i.cause)) if isinstance(i.cause, dict) else str(i.cause)
             action = i.cause.get('action', 'fail') if isinstance(i.cause, dict) else 'fail'
             next_state = 'canceled' if action == 'cancel' else 'failed'
             if not self.is_in_terminal_status():
                 self._change_status(next_state, {'reason': f"State machine interrupted: {reason}"})
        except Exception as e:
            print(f"时间 {self.env.now}: 工作流 {self.workflow.id} 状态机监控错误: {type(e).__name__} - {str(e)}")
            import traceback; traceback.print_exc()
            if not self.is_in_terminal_status():
                self._change_status('failed', {'reason': f"State machine exception: {str(e)}"})
        finally:
            self._deactivate_all_triggers()

    def _on_trigger_activated(self, trigger: Trigger, next_status: str, context: Dict[str, Any]):
        """触发器被激活时的回调"""
        # 检查当前状态是否仍然有效
        if self.is_in_terminal_status():
            return False
            
        # 执行状态变更
        details = {
            'trigger_id': trigger.id,
            'trigger_name': trigger.name,
            'trigger_type': trigger.type.value,
            'context': context
        }
        changed = self._change_status(next_status, details)
        
        # 如果状态已变更，中断监控进程以处理新状态
        if changed and self.process and self.process.is_alive:
            self.process.interrupt({'reason': 'State changed by trigger', 'next_status': next_status})
            
        return changed

    def _deactivate_all_triggers(self):
        """停用所有活动的触发器"""
        for trigger in self.active_triggers.values():
            trigger.deactivate()
        self.active_triggers.clear()

    def _get_current_transitions(self):
        """获取当前状态的所有可能转换"""
        transitions = list(self.state_transitions.get(self.current_status, []))
        transitions.extend(self.wildcard_transitions)
        return transitions

    def _change_status(self, new_status: str, event_details: Optional[Dict] = None):
        """变更状态机状态"""
        if self.is_in_terminal_status(): return False
        if self.current_status == new_status: return False

        old_status = self.current_status
        self.current_status = new_status
        timestamp = self.env.now
        print(f"时间 {timestamp}: SM {self.workflow.id}: State changed {old_status} -> {new_status}")

        # 触发工作流的状态变更事件
        self.workflow._trigger_status_changed(old_status, new_status, event_details, timestamp)

        return True

    def is_in_terminal_status(self):
        """检查是否处于终止状态"""
        return self.current_status in self.terminal_status

    @property
    def state(self):
        """获取当前状态"""
        return self.current_status
        


class Workflow(metaclass=WorkflowMeta):
    """Represents a process/goal tracked by a state machine based on Agent state."""
    from .agent import Agent
    
    @classmethod
    def register_property_template(cls, key, **kwargs):
        """注册工作流属性模板"""
        WorkflowMeta.register_template(cls, key, **kwargs)
        return cls
    
    @classmethod
    def get_property_templates(cls):
        """获取所有属性模板"""
        return getattr(cls, '_all_property_templates', {})
    
    @classmethod
    def get_transition_tasks(cls):
        """获取所有状态转换任务"""
        return getattr(cls, '_all_transition_tasks', {})
    
    @classmethod
    def get_description(cls):
        """获取工作流类型的描述"""
        return cls.__doc__ or f"{cls.__name__} 工作流"
    
    def __init__(self, env, name: str, owner: Optional['Agent'], timeout: Optional[float] = None, 
                 event_names = [], initial_status='idle', callback: Optional[Callable] = None, properties: Optional[Dict] = None):
        self.id = f'workflow'+str(uuid.uuid4())
        self.env = env
        self.name = name
        self.owner = owner

        self.status: WorkflowStatus = WorkflowStatus.PENDING
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.completion_reason: Optional[str] = None

        self.callback = callback
        self.properties = properties or {} # Store workflow-specific goals, locations etc.
        
        # 验证属性是否符合模板要求
        self._validate_properties()

        self.status_machine = WorkflowStatusMachine(self, initial_status)
        self.sm_process: Optional[simpy.Process] = None

        self.event_names = event_names + ['sm_status_changed', 'workflow_status_changed']
        self._register_workflow_events()
        if timeout:
            self.env.process(self._timeout_monitor(timeout))
        self._setup_transitions()
    
    def _validate_properties(self):
        """验证属性是否符合模板要求"""
        templates = self.get_property_templates()
        
        # 检查必需属性
        for key, template in templates.items():
            if template.required and key not in self.properties:
                error_msg = f"工作流 {self.id} 缺少必需属性: {key} ({template.description})"
                warnings.warn(error_msg)
                if self.properties.get('strict_property_validation', False):
                    raise ValueError(error_msg)
            elif key in self.properties:
                # 验证属性值
                value = self.properties[key]
                valid, msg = template.validate(value)
                if not valid:
                    error_msg = f"工作流 {self.id} 属性 '{key}' 验证失败: {msg}"
                    warnings.warn(error_msg)
                    if self.properties.get('strict_property_validation', False):
                        raise ValueError(error_msg)
    
    def get_details(self):
        """获取工作流详细信息，子类应该重写此方法提供更多详细信息"""
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status.name,
            'sm_state': self.status_machine.state,
            'owner': self.owner.id if self.owner else None,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'completion_reason': self.completion_reason
        }
        
    def get_current_suggested_task(self) -> Optional[Dict]:
        """
        获取当前状态下建议执行的任务
        
        根据当前状态机状态和可能的转换，返回预先定义的任务信息。
        这个方法可以被代理用来自动规划任务，而不需要复杂的决策逻辑。
        
        返回:
            Dict: 任务信息字典，包含组件名称、任务类、任务名称等信息
            None: 如果没有找到匹配的任务
        """
        return None
    
    def _timeout_monitor(self, timeout_sec):
        """监控工作流超时。如果工作流在指定时间内未完成，则将其标记为失败。"""
        try:
            # 等待超时时间
            yield self.env.timeout(timeout_sec)
            
            # 检查工作流当前状态
            if self.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELED):
                print(f"时间 {self.env.now}: 工作流 {self.id} ({self.name}) 超时 ({timeout_sec}秒)")
                
                # 构建超时详情
                timeout_details = {
                    'reason': f'timeout after {timeout_sec} seconds',
                    'workflow_id': self.id,
                    'timeout_value': timeout_sec,
                    'time': self.env.now
                }
                
                # 触发状态更改事件
                self._trigger_status_changed(
                    self.status_machine.state,  # 当前SM状态
                    'failed',                  # 新SM状态 
                    timeout_details,           # 事件详情
                    self.env.now               # 时间戳
                )
                
                # 如果状态机进程仍在运行，中断它
                if self.sm_process and self.sm_process.is_alive:
                    self.sm_process.interrupt({'action': 'fail', 'reason': f'Timeout after {timeout_sec} seconds'})
        
        except simpy.Interrupt:
            # 如果超时监控器被中断（例如工作流提前结束），则静默退出
            pass

    def _register_workflow_events(self):
        for name in self.event_names:
             self.env.event_registry.register_event(self.id, name)

    def _setup_transitions(self):
        """
        **Must be implemented by subclasses.**
        Defines SM transitions using triggers for agent states,
        time events, or custom events.
        """
        raise NotImplementedError("Subclasses must implement _setup_transitions()")

    def start(self) -> Optional[simpy.Process]:
        """Starts the workflow's state machine (if PENDING). Called by WM."""
        if self.status != WorkflowStatus.PENDING: return None
        
        # 更新状态，但不触发状态机的状态变更事件
        old_status = self.status
        self.start_time = self.env.now
        self.status = WorkflowStatus.RUNNING
        
        # 触发工作流状态变更事件
        self.env.event_registry.trigger_event(self.id, 'workflow_status_changed', {
            'workflow_id': self.id,
            'old_status': old_status.name,
            'new_status': self.status.name,
            'sm_state': self.status_machine.state,  # 当前状态机状态
            'event_details': None,
            'time': self.start_time
        })
        
        # 启动状态机
        sm_proc = self.status_machine.start()
        if not sm_proc:
            print(f"时间 {self.env.now}: Workflow {self.id} state machine failed to start during start() call.")
            # 更新为失败状态
            self.status = WorkflowStatus.FAILED
            self.completion_reason = "State machine failed to start"
            self.end_time = self.env.now
            
            # 触发工作流状态变更事件
            self.env.event_registry.trigger_event(self.id, 'workflow_status_changed', {
                'workflow_id': self.id,
                'old_status': WorkflowStatus.RUNNING.name,
                'new_status': self.status.name,
                'sm_state': self.status_machine.state,
                'event_details': {'reason': "State machine failed to start"},
                'time': self.env.now
            })
            return None
            
        self.sm_process = sm_proc
        
        # 通知所有者工作流已启动
        if self.owner:
            self.env.event_registry.trigger_event(
                self.owner.id, 'workflow_assigned',
                {'workflow_id': self.id, 'agent_id': self.owner.id, 'time': self.start_time}
            )
            
        return sm_proc

    def _trigger_status_changed(self, old_sm_status: str, new_sm_status: str, event_details: Optional[Dict], timestamp: float):
        """Internal: Called by SM to trigger workflow's state machine status_changed event."""
        # 先根据状态机的状态更新工作流的状态
        self.update_status_from_state_machine(new_sm_status, event_details, timestamp)
        
        # 触发状态机状态变更事件
        self.env.event_registry.trigger_event(self.id, 'sm_status_changed', {
            'workflow_id': self.id,
            'old_sm_status': old_sm_status,
            'new_sm_status': new_sm_status,
            'event_details': event_details,
            'time': timestamp
        })
    
    def update_status_from_state_machine(self, new_sm_state: str, event_details: Optional[Dict] = None, timestamp: Optional[float] = None):
        """根据状态机状态更新工作流状态"""
        if timestamp is None:
            timestamp = self.env.now
            
        old_status = self.status
        new_status = self.status
        
        # 根据状态机状态确定工作流状态
        if new_sm_state == 'completed':
            new_status = WorkflowStatus.COMPLETED
            self.completion_reason = event_details.get('reason', f"SM reached '{new_sm_state}'") if isinstance(event_details, dict) else str(event_details) if event_details else "Completed"
            self.end_time = timestamp
        elif new_sm_state == 'failed':
            new_status = WorkflowStatus.FAILED
            self.completion_reason = event_details.get('reason', f"SM reached '{new_sm_state}'") if isinstance(event_details, dict) else str(event_details) if event_details else "Failed"
            self.end_time = timestamp
        elif new_sm_state == 'canceled':
            new_status = WorkflowStatus.CANCELED
            self.completion_reason = event_details.get('reason', f"SM reached '{new_sm_state}'") if isinstance(event_details, dict) else str(event_details) if event_details else "Canceled"
            self.end_time = timestamp
        elif self.status == WorkflowStatus.PENDING and new_sm_state not in ('idle', 'pending'):
            new_status = WorkflowStatus.RUNNING
            self.start_time = self.start_time or timestamp
                
        # 如果状态有变化，触发工作流状态变更事件
        if new_status != old_status:
            self.status = new_status
            print(f"时间 {timestamp}: Workflow {self.id}: Status changed {old_status.name} -> {new_status.name} (SM: {new_sm_state})")
            
            self.env.event_registry.trigger_event(self.id, 'workflow_status_changed', {
                'workflow_id': self.id,
                'old_status': old_status.name,
                'new_status': new_status.name,
                'sm_state': new_sm_state,
                'event_details': event_details,
                'time': timestamp
            })
            
            # 如果工作流刚开始运行，通知所有者
            if new_status == WorkflowStatus.RUNNING and self.owner:
                self.env.event_registry.trigger_event(
                    self.owner.id, 'workflow_assigned',
                    {'workflow_id': self.id, 'agent_id': self.owner.id, 'time': timestamp}
                )
        
    def reset(self):
        """重置工作流状态，包括状态机"""
        old_status = self.status
        
        # 停止当前的状态机进程
        if self.sm_process and self.sm_process.is_alive:
            self.sm_process.interrupt({'action': 'cancel', 'reason': 'workflow_reset'})
        self.sm_process = None
        
        # 重置工作流状态
        self.status = WorkflowStatus.PENDING
        self.start_time = None
        self.end_time = None
        self.completion_reason = None
        
        # 重置状态机
        initial_status = self.status_machine.start_status
        self.status_machine = WorkflowStatusMachine(self, initial_status)
        self._setup_transitions()  # 重新设置转换规则
        
        # 触发工作流状态变更事件
        self.env.event_registry.trigger_event(self.id, 'workflow_status_changed', {
            'workflow_id': self.id,
            'old_status': old_status.name,
            'new_status': self.status.name,
            'sm_state': initial_status,
            'event_details': {'reason': 'workflow_reset'},
            'time': self.env.now
        })
        
        print(f"时间 {self.env.now}: Workflow {self.id} has been reset to PENDING state")
        return self
