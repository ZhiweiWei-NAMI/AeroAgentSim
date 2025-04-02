from airfogsim.core.enums import TaskStatus
from typing import Dict, List, Any, Optional, Type
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
    @classmethod
    def register_state_template(cls, key, **kwargs):
        AgentMeta.register_template(cls, key, **kwargs)
        return cls
        
    @classmethod
    def get_description(cls):
        """获取代理类型的描述"""
        return cls.__doc__ or f"{cls.__name__} 代理"

    def __init__(self, env, agent_name: str, properties: Optional[Dict] = None):
        self.id = 'agent_' + str(uuid.uuid4())
        self.name = agent_name
        self.env = env
        self.task_manager = env.task_manager
        self.properties = properties or {}
        self.state: Dict[str, Any] = {}
        self.llm_client = properties.get('llm_client', None)
        
        from airfogsim.core import Component, TaskProof
        self.components: Dict[str, Component] = {}

        self._initialization_level = type(self)

        # Task Management (Agent manages its own tasks)
        # task_id -> {'task': Task, 'process': SimPy Process}
        self.managed_tasks: Dict[str, Dict[str, Any]] = {}

        if not hasattr(self.env, 'event_registry'): raise RuntimeError("Environment missing EventRegistry.")
        if not hasattr(self.env, 'workflow_manager'): warnings.warn("Environment missing WorkflowManager.")
        # Task proof management
        self.task_proofs: Dict[str, TaskProof] = {} 

        # Standard Agent Events
        self.register_event('state_changed')
        self.register_event('task_started')   # Agent initiated a task
        self.register_event('task_finished')  # Agent observed task finish (comp/fail/canc)
        self.register_event('proof_created')
        self.register_event('proof_updated') 
        self.register_event('proof_transferred')
        

        # Agent's core behavior process
        self.agent_process = self.env.process(self.live())

    # --- State Management (Mostly Unchanged) ---
    def initialize_states(self, level_class=None, **states):
        target_class = level_class or self._initialization_level
        for key, value in states.items(): self.update_state(key, value)
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
        
        return active_workflows
    @classmethod
    def get_state_templates(cls):
        return getattr(cls, '_all_state_templates', {})
    def get_current_states(self):
        return dict(self.state)
    def get_state(self, key, default=None): return self.state.get(key, default)
    def set_state(self, key, value): return self.update_state(key, value)
    def has_state(self, key): return key in self.state
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
        # 检查component.MONITORED_STATES是否在自身的template
        for state_key in component.MONITORED_STATES:
            if state_key not in self.get_state_templates():
                warnings.warn(f"Agent {self.id} missing required state template for component: {state_key}")
        return self
    
    from airfogsim.core.component import Component
    def get_component(self, component_name: str) -> Optional[Component]:
        return self.components.get(component_name)    
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
        return self.env.event_registry.trigger_event(self.id, event_name, value)
    def subscribe(self, source_id, event_name, callback, listener_id=None): # Simplified subscribe
        sid = listener_id or f"agent_{self.id}_sub_{uuid.uuid4().hex[:6]}"
        return self.env.event_registry.subscribe(source_id, event_name, sid, callback)
    def unsubscribe(self, source_id, event_name, listener_id):
        return self.env.event_registry.unsubscribe(source_id, event_name, listener_id)
    def unsubscribe_all(self): # Convenience method
         return self.env.event_registry.unsubscribe_all(self.id)

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
                    workflow_id: Optional[str] = None, proof_id: Optional[str] = None):
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
            self.trigger_event('task_finished', {
                'task_name': task_name, # No task ID created
                'status': TaskStatus.FAILED.name, 'result': result, 'time': self.env.now
            })
            return None  # Return None for failure

        # Create the task instance
        task = self.task_manager.create_task(task_class, self, component_name, 
                                             task_name, workflow_id, 
                                             proof_id=proof_id, target_state=target_state,
                                             properties=properties)
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
             self.trigger_event('task_finished', {
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