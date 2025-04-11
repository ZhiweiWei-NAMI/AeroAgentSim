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
from airfogsim.core.enums import TaskStatus
from typing import Dict, List, Any, Optional, Type, get_origin, get_args
import uuid
import warnings
import simpy
from abc import ABC, abstractmethod

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
    # Register standard agent states here using the decorator-like pattern
    # (or ensure they are registered in base classes if using inheritance heavily)
    # Example: Agent.register_state_template('status', value_type=AgentStatus, required=True)
    # Agent.register_state_template('position', value_type=List[float], required=False)

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
        self.llm_client = properties.get('llm_client', None)
        
        from airfogsim.core import Component
        self.components: Dict[str, Component] = {}
        
        # 管理代理拥有的外部对象（如充电站、着陆点等）
        self.possessing_objects: Dict[str, Any] = {}

        self._initialization_level = type(self)

        # Task Management (Agent manages its own tasks)
        # task_id -> {'task': Task, 'process': SimPy Process}
        self.managed_tasks: Dict[str, Dict[str, Any]] = {}

        if not hasattr(self.env, 'event_registry'): raise RuntimeError("Environment missing EventRegistry.")
        if not hasattr(self.env, 'workflow_manager'): warnings.warn("Environment missing WorkflowManager.")

        # Standard Agent Events
        self.register_event('state_changed')
        

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
        if missing: warnings.warn(f"Agent {self.id} missing required states: {missing}")

    

    def _cancel_all_tasks(self):
        """取消所有正在执行的任务"""
        tasks_to_cancel = [task_id for task_id, task_info in self.managed_tasks.items() 
                          if task_info['status'] == 'running']
        for task_id in tasks_to_cancel:
            self.cancel_task(task_id)
        return len(tasks_to_cancel)

    def _get_active_workflows(self):
        """获取分配给此代理的活跃工作流"""
        from airfogsim.core.enums import WorkflowStatus
        active_workflows = []
        
        if hasattr(self.env, 'workflow_manager'):
            all_workflows = self.env.workflow_manager.get_all_workflows()
            for workflow in all_workflows:
                if (workflow.owner and workflow.owner.id == self.id and 
                    workflow.status == WorkflowStatus.RUNNING):
                    active_workflows.append(workflow)
                elif hasattr(workflow, 'executor_agent_id') and workflow.executor_agent_id == self.id:
                    active_workflows.append(workflow)
        
        return active_workflows
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
                    print(f"获取对象 {obj_name} 的属性 {attr_name} 时出错: {e}")
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
            print(f"时间 {self.env.now}: 警告! {self.id} 尝试取消不存在的任务 {task_id}")
            return False
            
        task_info = self.managed_tasks[task_id]
        if task_info['status'] != 'running':
            print(f"时间 {self.env.now}: 任务 {task_id} 不是运行状态，当前状态: {task_info['status']}")
            return False
        
        component_name = task_info['component']
        component = self.get_component(component_name)
        
        if not component:
            print(f"时间 {self.env.now}: 无法找到任务 {task_id} 对应的组件 {component_name}")
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
                    
                    print(f"时间 {self.env.now}: {self.id} 成功取消任务 {task_id} ({task_info['task_name']})")
                    
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
                    print(f"时间 {self.env.now}: 组件 {component_name} 中的任务 {task_id} 没有有效的运行进程")
            else:
                print(f"时间 {self.env.now}: 组件 {component_name} 中找不到任务 {task_id} 的进程")
                
            # 如果在组件中找不到进程，尝试使用agent管理的监控进程
            monitor_process = task_info.get('process')
            if monitor_process and monitor_process.is_alive:
                reason = f"任务被用户取消于时间 {self.env.now}"
                monitor_process.interrupt(cause=reason)
                
                # 更新任务状态
                task_info['status'] = 'canceled'
                task_info['end_time'] = self.env.now
                task_info['failure_reason'] = reason
                
                print(f"时间 {self.env.now}: {self.id} 通过监控进程取消任务 {task_id} ({task_info['task_name']})")
                
                # 触发任务取消事件
                event_name = f"{component_name}.task_canceled"
                
                self.trigger_event(event_name, {
                    'task_id': task_id,
                    'task_name': task_info['task_name'],
                    'time': self.env.now,
                    'reason': reason
                })
                
                return True
            
            print(f"时间 {self.env.now}: 无法找到任务 {task_id} 的有效运行进程")
            return False
                
        except Exception as e:
            print(f"时间 {self.env.now}: 取消任务 {task_id} 时出错: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    # --- Event Handling (Unchanged) ---
    def register_event(self, event_name):
        return self.env.event_registry.register_event(self.id, event_name)
    def has_event(self, event_name):
        return self.env.event_registry.has_event(self.id, event_name)
    def get_event(self, event_name):
        return self.env.event_registry.get_event(self.id, event_name)
    def trigger_event(self, event_name, value=None):
        # print(f"DEBUG Agent {self.id} trigger: {event_name}")
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

    def _setup_data_provider_subscriptions(self):
        """Sets up subscriptions to events from registered DataProviders."""
        # Import locally if needed to break cycles, or ensure WeatherDataProvider is imported at top
        from airfogsim.dataprovider.weather import WeatherDataProvider # Assuming weather.py exists

        weather_provider = self.env.get_data_provider('weather')
        if weather_provider and isinstance(weather_provider, WeatherDataProvider):
            try:
                # Use functools.partial to bind 'self' (the agent instance) to the callback
                bound_callback = functools.partial(weather_provider.on_weather_changed, self)
                listener_id = f"{self.id}_weather_listener" # Unique listener ID
                self.env.event_registry.subscribe(
                    event_name=WeatherDataProvider.EVENT_WEATHER_CHANGED, # Use constant from provider
                    callback=bound_callback,
                    listener_id=listener_id,
                    # No source_id filter needed, listen to all weather changes
                )
                # print(f"DEBUG Agent {self.id} subscribed to {WeatherDataProvider.EVENT_WEATHER_CHANGED}")
            except Exception as e:
                print(f"ERROR Agent {self.id} failed to subscribe to WeatherChanged: {e}")

        # Add subscriptions for other data providers (e.g., traffic, accident) here
        # traffic_provider = self.env.get_data_provider('traffic')
        # if traffic_provider and isinstance(traffic_provider, TrafficDataProvider):
        #     bound_callback = functools.partial(traffic_provider.on_traffic_changed, self)
        #     listener_id = f"{self.id}_traffic_listener"
        #     self.env.event_registry.subscribe(...)

    # --- Core Agent Logic ---
    def live(self):
        """
        **Must be implemented by subclasses.**
        The main behavior loop of the agent (SimPy process).
        Decides what actions (Tasks) to perform and when.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement the 'live' method.")
        yield # Required to make it a generator

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
            print(f"时间 {self.env.now}: Agent {self.id} cannot execute '{task_name}': {reason}")
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
             print(f"时间 {self.env.now}: Agent {self.id} monitoring task {task_id} interrupted: {i.cause}")
             reason = f"Agent monitoring interrupted: {i.cause}"
             if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                 task.cancel(reason, self.env.now)
             final_status = task.status
             final_result = task.result

        except Exception as e:
             print(f"时间 {self.env.now}: Agent {self.id} error monitoring task {task_id}: {str(e)}")
             import traceback; traceback.print_exc()
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
            print(f"警告: 代理 {self.id} 已拥有名为 {object_name} 的对象，将被覆盖")
        
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
                print(f"订阅对象 {object_name} 状态变化事件失败: {e}")

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
                    print(f"取消订阅对象 {object_name} 状态变化事件失败: {e}")
            
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
            print(f"时间 {self.env.now}: 代理 {self.id} 无法创建合约，环境中没有合约管理器")
            return None
        
        # 检查任务信息是否有效
        if not task_info or 'id' not in task_info:
            print(f"时间 {self.env.now}: 代理 {self.id} 无法创建合约，任务信息无效")
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
            print(f"时间 {self.env.now}: 代理 {self.id} 创建合约 {contract_id} 针对任务 {task_info['id']}")
        
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
            print(f"时间 {self.env.now}: 代理 {self.id} 无法接受合约，环境中没有合约管理器")
            return False
        
        # 接受合约
        result = self.env.contract_manager.accept_contract(contract_id, self.id)
        
        if result:
            print(f"时间 {self.env.now}: 代理 {self.id} 接受合约 {contract_id}")
        
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