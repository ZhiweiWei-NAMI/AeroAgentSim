"""
AirFogSim代理(Agent)核心模块

该模块定义了仿真系统中代理(Agent)的基础类和相关功能。代理是仿真中的主要实体，
可以执行任务、管理状态、与其他代理交互，并通过组件扩展功能。核心功能包括：
1. 状态管理：通过StateTemplate和AgentMeta实现状态定义、验证和继承
2. 组件管理：代理可以添加多个组件，并与组件交互
3. 事件处理：注册、触发和订阅事件系统
4. 任务执行：执行和监控任务的生命周期

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import functools # Added for partial
from airfogsim.core.enums import TaskStatus, TriggerType
from typing import Dict, List, Any, Optional, get_origin, get_args
import uuid
import warnings
import simpy
from airfogsim.core.trigger import TimeTrigger
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class StateTemplate:
    """状态模板定义"""

    def __init__(self, key, value_type=None, required=False, validator=None, description=None):
        """
        定义状态属性的模板

        参数:
            key: 状态键名
            value_type: 值的类型(如int, float, str等)，None表示任意类型
            required: 是否为必需状态
            validator: 可选的验证函数，接收值并返回布尔值
            description: 状态属性的描述
        """
        self.key = key
        self.value_type = value_type
        self.required = required
        self.validator = validator
        self.description = description

    def validate(self, value):
        """验证值是否符合模板要求"""
        # 类型检查
        if self.value_type is not None:
            origin_type = get_origin(self.value_type)
            if origin_type is not None:  # 处理泛型类型
                if not isinstance(value, origin_type):
                    return False, f"值类型应为 {origin_type.__name__}，而非 {type(value).__name__}"

                # 检查泛型参数类型（如果列表不为空）
                type_args = get_args(self.value_type)
                if type_args and isinstance(value, (list, tuple)) and len(value) > 0:
                    for item in value:
                        if not isinstance(item, type_args[0]):
                            return False, f"列表元素类型应为 {type_args[0].__name__}，而非 {type(item).__name__}"
            else:  # 普通类型检查
                if not isinstance(value, self.value_type):
                    return False, f"值类型应为 {self.value_type.__name__}，而非 {type(value).__name__}"

        # 使用自定义验证器
        if self.validator is not None:
            try:
                if not self.validator(value):
                    return False, f"值 '{value}' 未通过自定义验证"
            except Exception as e:
                return False, f"验证时发生错误: {str(e)}"

        return True, None

class AgentMeta(type):
    """Agent元类，用于处理状态模板继承"""

    def __new__(mcs, name, bases, attrs):
        # 确保每个类都有自己独立的模板字典
        # 不要直接从父类继承引用
        attrs['_own_state_templates'] = {}

        # 创建类
        cls = super().__new__(mcs, name, bases, attrs)

        # 初始化聚合的模板字典
        cls._all_state_templates = {}

        # 收集所有父类的模板
        for base in bases:
            if hasattr(base, '_all_state_templates'):
                cls._all_state_templates.update(base._all_state_templates)

        return cls

    @classmethod
    def register_template(mcs, cls, key, value_type=None, required=False, validator=None, description=None):
        """注册状态模板到指定类"""
        from airfogsim.core.agent import StateTemplate

        # 确保类有自己的模板存储
        if not hasattr(cls, '_own_state_templates'):
            cls._own_state_templates = {}

        # 创建模板
        template = StateTemplate(key, value_type, required, validator, description)

        # 存储到当前类自己的模板中
        cls._own_state_templates[key] = template

        # 同时更新聚合的模板集合
        if not hasattr(cls, '_all_state_templates'):
            cls._all_state_templates = {}

        cls._all_state_templates[key] = template

        return template

class Agent(metaclass=AgentMeta):
    """
    Agent 类是 AirFogSim 中所有代理的基类。

    Agent 提供了以下核心功能：
    1. 状态管理：管理代理的状态属性
    2. 组件管理：管理代理的组件
    3. 任务管理：管理代理的任务队列和执行
    4. 工作流管理：处理分配给代理的工作流
    5. 事件管理：注册、触发和处理事件
    6. 对象持有管理：管理代理拥有的外部对象
    7. 合约管理：创建和管理代理间的合约

    子类可以通过重写以下钩子方法来添加自定义行为：
    - register_event_listeners(): 注册自定义事件监听器
    - _before_event_wait(): 在等待事件之前执行
    - _check_agent_status(): 检查代理状态并决定是否继续处理
    - _process_custom_logic(): 执行代理特定的逻辑
    """
    # Register standard agent states here using the decorator-like pattern
    # (or ensure they are registered in base classes if using inheritance heavily)
    # Example: Agent.register_state_template('status', value_type=AgentStatus, required=True)
    # Agent.register_state_template('position', value_type=List[float], required=False)

    @classmethod
    def __init_subclass__(cls, **kwargs):
        """Initialize subclass with core status template"""
        super().__init_subclass__(**kwargs)
        # Register the core status template for all Agent subclasses
        cls.register_state_template('status', value_type=str, required=True,
                                   validator=lambda s: s in ['active', 'error', 'idle'],
                                   description="代理核心状态，可选值: active, error, idle")

    @classmethod
    def register_state_template(cls, key, **kwargs):
        """Registers a state template for this Agent class."""
        AgentMeta.register_template(cls, key, **kwargs)
        return cls # Return cls to allow chaining if needed

    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return cls.__doc__ or f"{cls.__name__} 代理"

    def __init__(self, env, agent_name: str, properties: Optional[Dict] = None):
        self.id = 'agent_' + str(uuid.uuid4().hex[:8])
        self.name = agent_name
        self.env = env
        self.task_manager = env.task_manager
        self.properties = properties or {}
        self.state: Dict[str, Any] = {}
        self.llm_client = self.properties.get('llm_client', None)

        from airfogsim.core import Component
        self.components: Dict[str, Component] = {}

        # 管理代理拥有的外部对象（如充电站、着陆点等）
        self.possessing_objects: Dict[str, Any] = {}

        self._initialization_level = type(self)

        # Task Management (Agent manages its own tasks)
        # task_id -> {'task': Task, 'process': SimPy Process}
        self.managed_tasks: Dict[str, Dict[str, Any]] = {}

        # 任务队列，按优先级排序
        self.task_queue = []

        # 任务调度间隔（秒）
        self.scheduling_interval = 5

        # 任务调度触发器
        self.task_schedule_trigger = None

        # Standard Agent Events
        self.get_event('state_changed')

        # Agent's core behavior process
        self.agent_process = self.env.process(self.live())

    # --- State Management (Mostly Unchanged) ---
    def initialize_states(self, level_class=None, **states):
        target_class = level_class or self._initialization_level
        # Set provided states first
        for key, value in states.items():
            self.update_state(key, value) # Use update_state for validation and event triggering

        # Ensure default for external_force if still not set
        if 'external_force' not in self.state:
             self.state['external_force'] = [0.0, 0.0, 0.0]

        self._validate_required_templates(target_class)
        return self
    def _validate_required_templates(self, cls): # Simplified validation
        templates = cls.get_state_templates()
        missing = [k for k,t in templates.items() if t.required and k not in self.state]
        if missing:
            warnings.warn(f"Agent {self.id} missing required states: {missing}")

    def _cancel_all_tasks(self):
        """取消所有正在执行的任务"""
        tasks_to_cancel = [task_id for task_id, task_info in self.managed_tasks.items()
                          if task_info['status'] == 'running']
        for task_id in tasks_to_cancel:
            self.cancel_task(task_id)
        return len(tasks_to_cancel)

    @classmethod
    def get_state_templates(cls):
        return getattr(cls, '_all_state_templates', {})
    def get_current_states(self):
        return dict(self.state)

    def _get_attribute(self, obj, attr, default=None):
        """
        统一获取对象属性或字典键的辅助方法

        Args:
            obj: 对象或字典
            attr: 属性或键名
            default: 如果属性/键不存在，返回的默认值

        Returns:
            属性/键值或默认值
        """
        # 如果是字典，尝试使用键访问
        if isinstance(obj, dict) and attr in obj:
            return obj[attr]
        # 如果是对象，尝试使用属性访问
        elif hasattr(obj, attr):
            return getattr(obj, attr)
        # 都不适用，返回默认值
        return default

    def _has_attribute(self, obj, attr):
        """
        统一检查对象属性或字典键是否存在的辅助方法

        Args:
            obj: 对象或字典
            attr: 属性或键名

        Returns:
            布尔值，表示属性/键是否存在
        """
        return (isinstance(obj, dict) and attr in obj) or hasattr(obj, attr)

    def get_state(self, key, default=None):
        """
        获取状态值，支持复合状态访问（如 'charging_station.status'）

        Args:
            key: 状态键，可以是简单键或复合键（用点分隔）
            default: 如果状态不存在，返回的默认值

        Returns:
            状态值或默认值
        """
        # 检查是否是复合键（包含点）
        if '.' in key:
            parts = key.split('.')
            obj_name = parts[0]
            attr_name = '.'.join(parts[1:])  # 支持多级属性

            # 检查是否是代理拥有的对象
            if obj_name in self.possessing_objects:
                obj = self.possessing_objects[obj_name]
                # 递归获取嵌套属性
                try:
                    for part in attr_name.split('.'):
                        obj = self._get_attribute(obj, part)
                        if obj is None:
                            return default
                    return obj
                except Exception as e:
                    logger.error(f"获取对象 {obj_name} 的属性 {attr_name} 时出错: {e}")
                    return default
            return default
        # 普通状态键
        return self.state.get(key, default)

    def set_state(self, key, value): return self.update_state(key, value)

    def has_state(self, key):
        """
        检查状态是否存在，支持复合状态

        Args:
            key: 状态键，可以是简单键或复合键（用点分隔）

        Returns:
            布尔值，表示状态是否存在
        """
        if '.' in key:
            parts = key.split('.')
            obj_name = parts[0]
            attr_name = '.'.join(parts[1:])

            if obj_name in self.possessing_objects:
                obj = self.possessing_objects[obj_name]
                try:
                    for part in attr_name.split('.'):
                        if not self._has_attribute(obj, part):
                            return False
                        obj = self._get_attribute(obj, part)
                    return True
                except:
                    return False

        return key in self.state
    def update_states(self, state_dict):
        for key, value in state_dict.items(): self.update_state(key, value)
    def update_state(self, key, value):
        old_value = self.state.get(key)
        # Basic validation (can add strictness)
        template = self.get_state_templates().get(key)
        if template:
            valid, msg = template.validate(value)
            if not valid:
                 warnings.warn(f"Agent {self.id} state '{key}' validation failed: {msg}")
                 # Optionally raise error or prevent update based on policy
                 # return False # Prevent update on validation failure
        elif self.properties.get('strict_state_keys', False):
             warnings.warn(f"Agent {self.id} setting unknown state '{key}'")
             # Optionally raise error
             # return False

        if old_value != value:
             self.state[key] = value
             self.trigger_event('state_changed', {'key': key, 'old_value': old_value, 'new_value': value, 'time': self.env.now})
             return True
        return False

    # --- Component Management (Unchanged) ---
    def add_component(self, component):
        self.components[component.name] = component
        component.agent = self; component.agent_id = self.id
        tmp = self.get_state_templates()
        # 检查component.MONITORED_STATES是否在自身的template
        for state_key in component.MONITORED_STATES:
            if '.' in state_key:
                continue # 跳过复合状态
            if state_key not in tmp:
                warnings.warn(f"Agent {self.id} missing required state template for component: {state_key}")
        return self

    from airfogsim.core.component import Component
    def get_component(self, component_name: str) -> Optional[Component]:
        component_name_lower = component_name
        return self.components.get(component_name_lower)
    def get_components(self):
        return list(self.components.values())
    def get_component_names(self) -> List[str]:
        return list(self.components.keys())

    def get_details(self):
        """获取代理详细信息"""
        details = {
            'id': self.id,
            'name': self.name,
            'type': self.__class__.__name__,
            'components': self.get_component_names(),
            'states': self.get_current_states()
        }
        return details

    def cancel_task(self, task_id):
        """
        取消指定的任务。在组件级别查找任务进程。

        Args:
            task_id: 要取消的任务ID

        Returns:
            bool: 如果任务成功取消则返回True，否则返回False
        """
        if task_id not in self.managed_tasks:
            logger.warning(f"时间 {self.env.now}: 警告! {self.id} 尝试取消不存在的任务 {task_id}")
            return False

        task_info = self.managed_tasks[task_id]
        if task_info['status'] != 'running':
            logger.warning(f"时间 {self.env.now}: 任务 {task_id} 不是运行状态，当前状态: {task_info['status']}")
            return False

        component_name = task_info['component']
        component = self.get_component(component_name)

        if not component:
            logger.error(f"时间 {self.env.now}: 无法找到任务 {task_id} 对应的组件 {component_name}")
            return False

        try:
            # 首先检查组件的task_processes中是否有该任务
            if task_id in component.task_processes:
                process = component.task_processes[task_id]
                if process and process.is_alive:
                    reason = f"任务被用户取消于时间 {self.env.now}"
                    process.interrupt(cause=reason)

                    # 更新任务状态
                    task_info['status'] = 'canceled'
                    task_info['end_time'] = self.env.now
                    task_info['failure_reason'] = reason

                    logger.info(f"时间 {self.env.now}: {self.id} 成功取消任务 {task_id} ({task_info['task_name']})")

                    # 触发任务取消事件
                    event_name = f"{component_name}.task_canceled"

                    self.trigger_event(event_name, {
                        'task_id': task_id,
                        'task_name': task_info['task_name'],
                        'time': self.env.now,
                        'reason': reason
                    })

                    return True
                else:
                    logger.warning(f"时间 {self.env.now}: 组件 {component_name} 中的任务 {task_id} 没有有效的运行进程")
            else:
                logger.warning(f"时间 {self.env.now}: 组件 {component_name} 中找不到任务 {task_id} 的进程")

            # 如果在组件中找不到进程，尝试使用agent管理的监控进程
            monitor_process = task_info.get('process')
            if monitor_process and monitor_process.is_alive:
                reason = f"任务被用户取消于时间 {self.env.now}"
                monitor_process.interrupt(cause=reason)

                # 更新任务状态
                task_info['status'] = 'canceled'
                task_info['end_time'] = self.env.now
                task_info['failure_reason'] = reason

                logger.info(f"时间 {self.env.now}: {self.id} 通过监控进程取消任务 {task_id} ({task_info['task_name']})")

                # 触发任务取消事件
                event_name = f"{component_name}.task_canceled"

                self.trigger_event(event_name, {
                    'task_id': task_id,
                    'task_name': task_info['task_name'],
                    'time': self.env.now,
                    'reason': reason
                })

                return True

            logger.warning(f"时间 {self.env.now}: 无法找到任务 {task_id} 的有效运行进程")
            return False

        except Exception as e:
            logger.error(f"时间 {self.env.now}: 取消任务 {task_id} 时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    # --- Event Handling (Unchanged) ---
    def has_event(self, event_name):
        return self.env.event_registry.has_event(self.id, event_name)
    def get_event(self, event_name):
        return self.env.event_registry.get_event(self.id, event_name)
    def trigger_event(self, event_name, value=None):
        # logger.debug(f"Agent {self.id} trigger: {event_name}")
        value = value or {}
        value['agent_id'] = self.id
        return self.env.event_registry.trigger_event(self.id, event_name, value)
    def subscribe(self, source_id, event_name, callback, listener_id=None): # Simplified subscribe
        sid = listener_id or f"agent_{self.id}_sub_{uuid.uuid4().hex[:4]}"
        return self.env.event_registry.subscribe(source_id, event_name, sid, callback)
    def unsubscribe(self, source_id, event_name, listener_id):
        return self.env.event_registry.unsubscribe(source_id, event_name, listener_id)
    def unsubscribe_all(self): # Convenience method
         # Note: This might need adjustment if listener IDs aren't based on self.id
         return self.env.event_registry.unsubscribe_all(self.id)


    # --- Event Handling ---
    def register_event_listeners(self):
        """
        注册代理需要监听的事件

        子类可以重写此方法来注册自定义事件监听器

        Returns:
            List[Dict]: 事件监听器列表，每个监听器包含以下字段：
                - source_id: 事件源ID，如代理ID
                - event_name: 事件名称，如 'workflow_assigned'、'task_completed' 等
                - callback: 事件处理函数，接收事件数据作为参数
        """
        # 默认监听工作流分配事件、环境更新事件和状态错误事件
        return [
            {
                'source_id': self.id,
                'event_name': 'workflow_assigned',
                'callback': self._handle_workflow_assigned
            },
            {
                'source_id': self.env.id,
                'event_name': 'visual_update',
                'callback': self._handle_visual_update
            },
            {
                'source_id': self.id,
                'event_name': 'state_changed',
                'callback': self._handle_status_error
            }
        ]

    def _handle_workflow_assigned(self, _):
        """处理工作流分配事件"""
        self._process_workflow_tasks()

    def _handle_visual_update(self, _):
        """处理环境更新事件"""
        self._update_agent_status()

    def _handle_status_error(self, event_data):
        """处理状态变为error的事件"""
        # 检查是否是status状态变化
        if event_data.get('key') == 'status' and event_data.get('new_value') == 'error':
            # 获取事件数据中的组件名称
            component_name = event_data.get('component_name')
            if component_name and component_name in self.components:
                # 禁用该组件
                self.components[component_name].disable()
                logger.warning(f"时间 {self.env.now}: 代理 {self.id} 禁用了组件 {component_name} 因为状态变为error")

    def _update_agent_status(self):
        """更新代理状态"""
        # 检查所有组件是否有任何一个处于错误状态
        if any(component.is_error for component in self.components.values()):
            self.update_state('status', 'error')
            return

        # 检查所有组件是否有任何一个正在执行任务
        if any(component.is_busy for component in self.components.values()):
            self.update_state('status', 'active')
            return

        # 如果没有活跃任务，将状态设为空闲
        self.update_state('status', 'idle')

    def _before_event_wait(self):
        """
        在等待事件之前执行的逻辑。

        该钩子方法在每次等待事件之前被调用。
        子类可以重写此方法来添加自定义行为。
        """
        pass

    def _check_agent_status(self):
        """
        检查代理状态并决定是否继续处理。

        该钩子方法在每次事件循环中被调用，用于决定是否继续处理事件。
        子类可以重写此方法来添加自定义状态检查逻辑。

        Returns:
            bool: 如果代理状态允许继续处理，则返回 True，否则返回 False
        """
        return True

    def _process_custom_logic(self):
        """
        执行代理特定的逻辑。

        该钩子方法在每次事件触发后被调用。
        子类应该重写此方法来添加自定义行为，而不是重写 live() 方法。

        注意：该方法应该专注于代理特定的逻辑，而不是通用的任务执行逻辑。
        任务执行逻辑已由 Agent 基类的 _task_scheduler 和相关方法处理。

        典型用例包括：
        - 代理间协作（如合约管理）
        - LLM 集成（如分析、规划）
        - 代理特定的状态更新
        - 特殊资源管理
        """
        pass

    def cleanup(self):
        """清理代理资源，包括取消事件监听和触发器"""
        # 取消所有事件监听
        if hasattr(self, 'event_listeners'):
            for listener_id, (source_id, event_name) in self.event_listeners.items():
                self.env.event_registry.unsubscribe(source_id, event_name, listener_id)

        # 停用任务调度触发器
        if hasattr(self, 'task_schedule_trigger') and self.task_schedule_trigger:
            self.task_schedule_trigger.deactivate()

    # --- Workflow Task Processing ---
    def _process_workflow_tasks(self):
        """处理工作流建议的任务"""
        # 获取活跃的工作流
        active_workflows = self.get_active_workflows()
        if not active_workflows:
            return

        # 从每个工作流获取建议任务
        tasks_to_execute = []
        for workflow in active_workflows:
            # 获取工作流ID
            workflow_id = workflow.id

            # 获取工作流建议的任务
            suggested_task = workflow.get_current_suggested_task()
            if suggested_task:
                # 确保任务有工作流ID
                if 'workflow_id' not in suggested_task:
                    suggested_task['workflow_id'] = workflow_id
                tasks_to_execute.append(suggested_task)

        # 将任务添加到队列
        for task_info in tasks_to_execute:
            # 检查是否已经有相同的任务在队列中或正在执行
            if not (self._is_task_in_queue(task_info) or self._is_task_being_executed(task_info)):
                self.add_task_to_queue(
                    priority=task_info.get('priority'),
                    preemptive=task_info.get('preemptive', False),
                    component_name=task_info['component'],
                    task_name=task_info['task_name'],
                    task_class=task_info['task_class'],
                    target_state=task_info.get('target_state'),
                    properties=task_info.get('properties'),
                    workflow_id=task_info.get('workflow_id')
                )

    def _is_task_in_queue(self, task_info):
        """检查任务是否已经在队列中"""
        for queued_task in self.task_queue:
            # 检查关键属性是否相同
            if (queued_task['component_name'] == task_info['component'] and
                queued_task['task_class'] == task_info['task_class'] and
                queued_task['workflow_id'] == task_info.get('workflow_id')):
                # 可以根据需要添加更多的属性比较
                return True
        return False

    def _is_task_being_executed(self, task_info):
        """
        检查是否已经有相同的任务正在执行

        Args:
            task_info: 任务信息字典

        Returns:
            bool: 如果已经有相同的任务正在执行，则返回True，否则返回False
        """
        for _, managed_task in self.managed_tasks.items():
            # 检查关键属性是否相同
            if (managed_task['component'] == task_info['component'] and
                managed_task['task_name'] == task_info['task_name'] and
                managed_task.get('task').workflow_id == task_info.get('workflow_id')):
                return True
        return False

    def get_active_workflows(self):
        """获取代理的活跃工作流"""
        if not hasattr(self.env, 'workflow_manager'):
            return []

        # 获取与该代理相关的所有工作流
        agent_workflows = self.env.workflow_manager.get_agent_workflows(self.id)

        # 过滤出活跃的工作流
        active_workflows = []
        for workflow in agent_workflows:
            if workflow.is_active():
                active_workflows.append(workflow)

        return active_workflows

    # --- Task Queue Management ---
    def add_task_to_queue(self, component_name: str, task_name: str, task_class: str,
                         priority=None, preemptive=False,
                         target_state: Optional[Dict] = None, properties: Optional[Dict] = None,
                         workflow_id: Optional[str] = None):
        """
        添加任务到队列

        Args:
            component_name: 组件名称
            task_name: 任务名称
            task_class: 任务类名
            priority: 任务优先级，如果为None，则使用properties中的priority
            preemptive: 是否可抢占
            target_state: 目标状态
            properties: 任务属性
            workflow_id: 工作流ID
        """
        # 确保属性不为空
        properties = properties or {}

        # 添加优先级和抢占属性
        if priority is not None:
            properties['priority'] = priority.name.lower() if hasattr(priority, 'name') else priority
        properties['preemptive'] = preemptive

        # 创建任务信息
        task_info = {
            'component_name': component_name,
            'task_name': task_name,
            'task_class': task_class,
            'target_state': target_state or {},
            'properties': properties,
            'workflow_id': workflow_id,
            'added_time': self.env.now
        }

        # 添加到队列
        self.task_queue.append(task_info)

        # 按优先级排序（优先级高的在前面）
        self._sort_task_queue()

        logger.info(f"时间 {self.env.now}: 代理 {self.id} 添加任务 '{task_name}' 到队列")

    def _sort_task_queue(self):
        """
        按优先级和工作流开始时间排序任务队列

        排序规则：
        1. 首先按任务优先级排序（高优先级在前）
        2. 在优先级相同的情况下，按工作流开始时间排序（先开始的工作流先执行）
        """
        from airfogsim.core.enums import TaskPriority

        # 如果队列为空，直接返回
        if not self.task_queue:
            return

        # 获取工作流开始时间的字典
        workflow_start_times = {}
        if hasattr(self.env, 'workflow_manager'):
            for workflow_id, workflow in self.env.workflow_manager.workflows.items():
                workflow_start_times[workflow_id] = workflow.start_time or float('inf')

        # 为每个任务计算优先级和工作流开始时间
        task_sort_info = []
        for task_info in self.task_queue:
            # 获取任务优先级
            priority_str = task_info.get('properties', {}).get('priority', 'normal')

            # 将优先级字符串转换为数值
            priority_value = 0  # 默认优先级为0（最低）
            if isinstance(priority_str, str):
                # 使用TaskPriority的from_string方法转换
                priority_enum = TaskPriority.from_string(priority_str)
                priority_value = priority_enum.value
            elif isinstance(priority_str, TaskPriority):
                # 如果已经是枚举对象，直接获取值
                priority_value = priority_str.value
            elif isinstance(priority_str, int):
                # 如果是整数，直接使用
                priority_value = priority_str

            # 获取工作流开始时间
            workflow_id = task_info.get('workflow_id')
            workflow_start_time = workflow_start_times.get(workflow_id, task_info.get('added_time', float('inf')))

            # 将任务信息、优先级和工作流开始时间一起保存
            task_sort_info.append((task_info, priority_value, workflow_start_time, workflow_id))

        # 按优先级和工作流开始时间排序
        # 先按优先级排序（高优先级在前），然后按工作流开始时间排序（先开始的在前），最后按工作流ID排序
        task_sort_info.sort(key=lambda x: (-x[1], x[2], x[3]))

        # 更新任务队列
        self.task_queue = [task_info for task_info, _, _, _ in task_sort_info]

    def _process_task_queue(self):
        """处理任务队列"""
        # 如果没有任务，直接返回
        if not self.task_queue:
            return
        to_execute_tasks = []
        queuing_tasks = []
        if self.llm_client and self.llm_client.is_available():
            to_execute_tasks, queuing_tasks = self.llm_client.analyze_agent_tasks(self)
        else:
            to_execute_tasks, queuing_tasks = self._default_policy()

        self.task_queue = queuing_tasks
        # 遍历任务队列（已按优先级排序）
        i = 0
        while i < len(to_execute_tasks):
            task_info = to_execute_tasks[i]
            component_name = task_info['component_name']
            component = self.get_component(component_name)

            # 如果组件不存在，移除任务
            if not component:
                logger.warning(f"\n时间 {self.env.now}: 组件 {component_name} 不存在，移除任务 {task_info['task_name']}")
                to_execute_tasks.pop(i)
                continue

            # 检查组件是否可用
            if component.is_available():
                # 组件可用，执行任务
                self._execute_queued_task(task_info)
                # 释放一下进程，让is_available()可以被更新
                self.env.timeout(0)
                to_execute_tasks.pop(i)
            else:
                # 组件不可用，检查是否可以抢占
                preemptive = task_info['properties'].get('preemptive', False)
                if preemptive:
                    # 尝试抢占
                    if self._try_preempt_task(task_info):
                        # 抢占成功，移除任务
                        to_execute_tasks.pop(i)
                    else:
                        # 抢占失败，检查下一个任务
                        i += 1
                else:
                    # 不可抢占，检查下一个任务
                    i += 1
        self._sort_task_queue()

    def _default_policy(self):
        # 获取任务队列中与正在执行的任务同属于同一工作流的任务
        workflow_id = self.task_queue[0]['workflow_id']
        # 先判断是否有preemptive任务，如果有，先执行
        is_preemptive = False
        for task_info in self.task_queue:
            if task_info['properties'].get('preemptive', False):
                workflow_id = task_info['workflow_id']
                is_preemptive = True
                break
        if len(self.managed_tasks) > 0 and not is_preemptive:
            workflow_id = list(self.managed_tasks.values())[0]['task'].workflow_id
        to_execute_tasks = [task for task in self.task_queue if task['workflow_id'] == workflow_id]
        queuing_tasks = [task for task in self.task_queue if task not in to_execute_tasks]
        return to_execute_tasks, queuing_tasks

    def _execute_queued_task(self, task_info):
        """执行队列中的任务"""
        self.execute_task(
            component_name=task_info['component_name'],
            task_name=task_info['task_name'],
            task_class=task_info['task_class'],
            target_state=task_info['target_state'],
            properties=task_info['properties'],
            workflow_id=task_info['workflow_id']
        )

    def _try_preempt_task(self, task_info):
        """尝试抢占任务"""
        component_name = task_info['component_name']

        # 获取组件当前正在执行的任务
        current_tasks = self.get_component_tasks(component_name)
        if not current_tasks:
            return False

        # 创建新任务实例，但不执行
        new_task = self.task_manager.create_task(
            task_info['task_class'],
            self,
            component_name,
            task_info['task_name'],
            task_info['workflow_id'],
            target_state=task_info['target_state'],
            properties=task_info['properties']
        )

        # 获取当前任务
        for current_task_info in current_tasks:
            current_task_id = current_task_info['id']
            current_task = self.managed_tasks[current_task_id]['task']

            # 检查是否可以抢占
            if new_task.can_preempt(current_task):
                # 可以抢占，取消当前任务
                self.cancel_task(current_task_id)

                # 如果当前任务有on_preempted方法，调用它
                if hasattr(current_task, 'on_preempted'):
                    current_task.on_preempted()

                # 执行新任务
                self._execute_queued_task(task_info)

                # logger.info(f"时间 {self.env.now}: 任务 {new_task.name} 抢占任务 {current_task.name}")
                return True

        return False

    def get_component_tasks(self, component_name):
        """
        获取指定组件正在执行的任务

        Args:
            component_name: 组件名称

        Returns:
            List: 任务信息列表
        """
        tasks = []
        for _, task_info in self.managed_tasks.items():
            if task_info['component'] == component_name and task_info['status'] == 'running':
                task = task_info['task']
                task_data = {
                    'id': task.id,
                    'name': task.name,
                    'status': task.status.name,
                    'start_time': task.start_time,
                    'progress': task.progress,
                    'priority': task.priority.name if hasattr(task, 'priority') else 'NORMAL',
                    'priority_value': task.priority.value if hasattr(task, 'priority') else 1,
                    'preemptive': task.preemptive if hasattr(task, 'preemptive') else False
                }
                tasks.append(task_data)
        return tasks

    def _task_scheduler(self):
        """任务调度器"""
        # 从属性中获取任务调度触发器，如果没有则创建默认的TimeTrigger
        trigger_config = self.properties.get('task_schedule_trigger', None)

        if trigger_config:
            # 如果在属性中指定了触发器，使用指定的触发器
            # 这里假设触发器已经创建好并传入属性
            self.task_schedule_trigger = trigger_config
            self.task_schedule_trigger.not_to_deactive = True
        else:
            # 如果没有指定触发器，创建默认的TimeTrigger
            self.task_schedule_trigger = TimeTrigger(
                self.env,
                interval=self.scheduling_interval,  # 使用默认的调度间隔
                name=f"{self.id}_task_scheduler_trigger"
            )

        # 添加回调函数，当触发器触发时处理任务队列
        self.task_schedule_trigger.add_callback(lambda _: self._process_task_queue())

        # 激活触发器
        self.task_schedule_trigger.activate()

        # 等待直到被中断
        try:
            while True:
                yield self.env.timeout(float('inf'))
        except simpy.Interrupt:
            # 如果被中断，停用触发器
            self.task_schedule_trigger.deactivate()

    # --- Core Agent Logic ---
    def live(self):
        """
        代理的主要行为逻辑。

        该方法实现了基本的事件监听和任务处理逻辑，包括：
        1. 初始化任务调度器
        2. 注册事件监听器
        3. 监听注册的事件
        4. 执行代理特定的行为

        子类可以通过重写以下方法来添加自定义行为：
        - register_event_listeners(): 注册自定义事件监听器
        - _before_event_wait(): 在等待事件之前执行
        - _check_agent_status(): 检查代理状态并决定是否继续处理
        - _process_custom_logic(): 执行代理特定的逻辑

        注意：子类通常不应该重写此方法，而是通过重写上述钩子方法来添加自定义行为。
        这样可以确保代理的核心行为逻辑保持一致。
        """
        # 初始化任务调度器
        self.env.process(self._task_scheduler())

        # 注册事件监听器
        event_listeners = self.register_event_listeners()

        # 注册到事件注册表
        self.event_listeners = {}  # 存储监听器ID，用于后续清理
        for listener_info in event_listeners:
            source_id = listener_info['source_id']
            event_name = listener_info['event_name']
            callback = listener_info['callback']

            # 生成唯一的监听器ID
            listener_id = f"{self.id}_{source_id}_{event_name}_listener"

            # 注册到事件注册表
            _ = self.env.event_registry.subscribe(
                source_id, event_name, listener_id, callback
            )

            # 存储监听器ID
            self.event_listeners[listener_id] = (source_id, event_name)

        # 封装获取事件的函数
        def get_events_to_listen():
            # 初始化事件列表
            events = []

            # 获取默认事件
            visual_update_event = self.env.event_registry.get_event(self.env.id, 'visual_update')
            workflow_assigned_event = self.env.event_registry.get_event('workflow_assigned', self.id)

            # 添加默认事件
            if visual_update_event:
                events.append(visual_update_event)
            if workflow_assigned_event:
                events.append(workflow_assigned_event)

            # 获取所有注册的事件
            for _, (source_id, event_name) in self.event_listeners.items():
                # 跳过默认事件，避免重复
                if (source_id == self.env.id and event_name == 'visual_update') or \
                   (source_id == 'workflow_assigned' and event_name == self.id):
                    continue

                event = self.env.event_registry.get_event(source_id, event_name)
                if event and event not in events:
                    events.append(event)

            return events

        # 监听事件
        while True:
            # 执行事件等待前的逻辑
            self._before_event_wait()

            # 检查代理状态，决定是否继续处理
            if not self._check_agent_status():
                # 短暂等待后再次检查
                yield self.env.timeout(1)
                continue

            # 获取要监听的事件
            events_to_listen = get_events_to_listen()

            # 等待任一事件触发
            if events_to_listen:
                try:
                    yield self.env.any_of(events_to_listen)
                except Exception as e:
                    logger.error(f"代理 {self.id} 等待事件时出错: {str(e)}")
                    # 出错时等待一段时间
                    yield self.env.timeout(self.scheduling_interval)
            else:
                # 如果没有事件要监听，等待一段时间
                yield self.env.timeout(self.scheduling_interval)

            self._process_workflow_tasks()
            # 执行代理特定的逻辑
            self._process_custom_logic()

    # --- Task Execution Management ---
    from .task import Task
    def execute_task(self, component_name: str, task_name: str, task_class: str,
                    target_state: Optional[Dict] = None, properties: Optional[Dict] = None,
                    workflow_id: Optional[str] = None, task_id: Optional[str] = None) -> Optional[Task]:
        """
        Creates and executes a task using a specified component.
        Non-blocking - returns task object immediately.
        """
        component = self.get_component(component_name)
        if not component:
            reason = f"Component '{component_name}' not found"
            logger.error(f"时间 {self.env.now}: Agent {self.id} cannot execute '{task_name}': {reason}")
            result = {"status": "failed", "reason": reason, "time": self.env.now}
            # Trigger agent's task finished event even if component not found
            self.trigger_event('task_completed', {
                'task_name': task_name, # No task ID created
                'status': TaskStatus.FAILED.name, 'result': result, 'time': self.env.now
            })
            return None  # Return None for failure

        # Create the task instance
        task = self.task_manager.create_task(task_class, self, component_name,
                                             task_name, workflow_id, target_state=target_state,
                                             properties=properties, task_id=task_id)
        task_id = task.id

        self.trigger_event('task_started', {'task_id': task_id, 'task_name': task_name, 'time': self.env.now})

        # Start component execution and monitor it
        component_exec_proc = component.execute_task(task)
        monitor_proc = self.env.process(self._monitor_task_execution(task, component_exec_proc))

        self.managed_tasks[task_id] = {
            'task': task,
            'process': monitor_proc,
            'component': component_name,
            'task_name': task_name,
            'status': 'running',
            'start_time': self.env.now
        }

        # Return the task object immediately, not waiting for completion
        return task


    def _monitor_task_execution(self, task: Task, component_exec_proc: simpy.Process):
        """Internal process to wait for component execution and handle outcome."""
        task_id = task.id
        final_status = TaskStatus.FAILED # Default to failed unless success confirmed
        final_result = None

        try:
            # print(f"DEBUG Agent {self.id}: Monitoring component execution for task {task_id}")
            # Wait for the process returned by component.execute_task()
            final_result = yield component_exec_proc
            # print(f"DEBUG Agent {self.id}: Component execution finished for {task_id}. Raw result: {final_result}")

            # Status should be set within the Task object by component/task logic
            final_status = task.status
            # Ensure result dict matches status
            if isinstance(final_result, dict):
                 # Trust task.status primarily
                 final_result['status'] = final_status.name.lower()
            else:
                 # Create result dict if component returned something else
                 final_result = task.result or {'status': final_status.name.lower(), 'reason': task.failure_reason, 'time': task.end_time}


        except simpy.Interrupt as i:
             logger.warning(f"时间 {self.env.now}: Agent {self.id} monitoring task {task_id} interrupted: {i.cause}")
             reason = f"Agent monitoring interrupted: {i.cause}"
             if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                 task.cancel(reason, self.env.now)
             final_status = task.status
             final_result = task.result

        except Exception as e:
             logger.error(f"时间 {self.env.now}: Agent {self.id} error monitoring task {task_id}: {str(e)}")
             import traceback
             logger.error(traceback.format_exc())
             reason = f"Agent monitoring error: {str(e)}"
             if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                  task.fail(reason)
             final_status = task.status
             final_result = task.result

        finally:
             # print(f"时间 {self.env.now}: Agent {self.id} finished monitoring task {task_id}. Final status: {final_status.name}")
             # Trigger Agent's own event about task completion
             self.trigger_event('task_completed', {
                  'task_id': task_id,
                  'task_name': task.name,
                  'status': final_status.name,
                  'result': final_result,
                  'time': self.env.now # Use current time for event trigger
             })

             # Clean up managed task entry
             if task_id in self.managed_tasks:
                 del self.managed_tasks[task_id]

             # Return the final result obtained from the component execution
             return final_result

   # --- Possessing Objects Management ---
    def add_possessing_object(self, object_name: str, obj: Any) -> bool:
        """
        添加代理拥有的对象，并监听其状态变化事件

        Args:
            object_name: 对象名称，用于在复合状态中引用
            obj: 对象实例

        Returns:
            是否成功添加
        """
        if object_name in self.possessing_objects:
            # 如果已存在，先移除旧对象及其监听器
            self.remove_possessing_object(object_name)
            logger.warning(f"警告: 代理 {self.id} 已拥有名为 {object_name} 的对象，将被覆盖")

        # 存储对象
        self.possessing_objects[object_name] = obj

        # 创建监听器ID
        listener_id = f"{self.id}_{object_name}_state_listener"

        # 定义状态变化回调函数
        def on_object_state_changed(event_data):
            # 将对象状态变化转发为代理的状态变化事件
            if isinstance(event_data, dict):
                event_data['object_name'] = object_name  # 添加对象名称以便识别
                event_data['key'] = f"{object_name}.{event_data['key']}"  # 更新键名
                self.trigger_event('state_changed', event_data)

        if self._has_attribute(obj, 'id'):
            try:
                obj_id = self._get_attribute(obj, 'id')
                self.subscribe(obj_id, 'state_changed', on_object_state_changed, listener_id)
                # print(f"代理 {self.id} 成功订阅对象 {object_name} (ID: {obj_id}) 的状态变化事件")
            except Exception as e:
                logger.error(f"订阅对象 {object_name} 状态变化事件失败: {e}")

        self.trigger_event('possessing_object_added', {
            'object_name': object_name,
            'object_id': self._get_attribute(obj, 'id'),
            'agent_id': self.id,
            'time': self.env.now
        })

        return True

    def remove_possessing_object(self, object_name: str) -> bool:
        """
        移除代理拥有的对象，并取消相关事件订阅

        Args:
            object_name: 对象名称

        Returns:
            是否成功移除
        """
        if object_name in self.possessing_objects:
            obj = self.possessing_objects[object_name]

            # 取消对象状态变化的监听
            if self._has_attribute(obj, 'id'):
                listener_id = f"{self.id}_{object_name}_state_listener"
                try:
                    obj_id = self._get_attribute(obj, 'id')
                    self.unsubscribe(obj_id, 'state_changed', listener_id)
                    # print(f"代理 {self.id} 已取消订阅对象 {object_name} (ID: {obj_id}) 的状态变化事件")
                except Exception as e:
                    logger.error(f"取消订阅对象 {object_name} 状态变化事件失败: {e}")

            # 从字典中移除对象
            del self.possessing_objects[object_name]
            self.trigger_event('possessing_object_removed', {
                'object_name': object_name,
                'object_id': self._get_attribute(obj, 'id', None),
                'agent_id': self.id,
                'time': self.env.now
            })
            return True
        return False

    def get_possessing_object(self, object_name: str) -> Optional[Any]:
       """
       获取代理拥有的对象

       Args:
           object_name: 对象名称

       Returns:
           对象实例，如果不存在则返回None
       """
       return self.possessing_objects.get(object_name)

    def get_possessing_object_names(self) -> List[str]:
       """
       获取代理拥有的所有对象名称

       Returns:
           对象名称列表
       """
       return list(self.possessing_objects.keys())

    # --- 合约管理接口 ---
    def create_contract(self, task_info, target_agent_ids, reward, penalty=0,
                       deadline=None, description=''):
        """
        创建任务卸载合约

        Args:
            task_info: 任务信息字典，必须包含id字段
            target_agent_ids: 目标代理ID列表
            reward: 完成任务的奖励
            penalty: 未完成任务的惩罚
            deadline: 截止时间，默认为当前时间+30分钟
            description: 任务描述

        Returns:
            str: 合约ID，如果创建失败则返回None
        """
        # 检查环境中是否有合约管理器
        if not hasattr(self.env, 'contract_manager'):
            logger.error(f"时间 {self.env.now}: 代理 {self.id} 无法创建合约，环境中没有合约管理器")
            return None

        # 检查任务信息是否有效
        if not task_info or 'id' not in task_info:
            logger.error(f"时间 {self.env.now}: 代理 {self.id} 无法创建合约，任务信息无效")
            return None

        # 设置默认截止时间
        if deadline is None:
            deadline = self.env.now + 30*60  # 默认30分钟后截止

        # 创建合约
        contract_id = self.env.contract_manager.create_contract(
            issuer_agent_id=self.id,
            task_info=task_info,
            reward=reward,
            penalty=penalty,
            deadline=deadline,
            appointed_agent_ids=target_agent_ids,
            description=description
        )

        if contract_id:
            logger.info(f"时间 {self.env.now}: 代理 {self.id} 创建合约 {contract_id} 针对任务 {task_info['id']}")

        return contract_id

    def accept_contract(self, contract_id):
        """
        接受合约

        Args:
            contract_id: 合约ID

        Returns:
            bool: 是否成功接受合约
        """
        # 检查环境中是否有合约管理器
        if not hasattr(self.env, 'contract_manager'):
            logger.error(f"时间 {self.env.now}: 代理 {self.id} 无法接受合约，环境中没有合约管理器")
            return False

        # 接受合约
        result = self.env.contract_manager.accept_contract(contract_id, self.id)

        if result:
            logger.info(f"时间 {self.env.now}: 代理 {self.id} 接受合约 {contract_id}")

        return result

    def get_available_contracts(self):
        """
        获取可接受的合约列表

        Returns:
            list: 可接受的合约列表
        """
        # 检查环境中是否有合约管理器
        if not hasattr(self.env, 'contract_manager'):
            return []

        # 获取所有待处理的合约
        pending_contracts = self.env.contract_manager.get_pending_contracts()

        # 筛选出当前代理可以接受的合约
        available_contracts = []
        for contract in pending_contracts:
            if self.id in contract['appointed_agent_ids']:
                available_contracts.append(contract)

        return available_contracts

    def get_agent_contracts(self, role=None, status=None):
        """
        获取代理相关的合约

        Args:
            role: 角色，'issuer'或'executor'
            status: 合约状态

        Returns:
            list: 合约列表
        """
        # 检查环境中是否有合约管理器
        if not hasattr(self.env, 'contract_manager'):
            return []

        # 获取代理相关的合约
        return self.env.contract_manager.get_agent_contracts(self.id, role, status)