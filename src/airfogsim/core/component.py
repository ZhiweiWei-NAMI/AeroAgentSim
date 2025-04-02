from airfogsim.core.enums import TaskStatus
import warnings
from typing import List, Dict, Any, Optional, Tuple
import simpy
from typing import Any, Dict, List, Set
from abc import ABC, abstractmethod

class Component:
    """执行任务的组件，使用分配的资源。"""
    PRODUCED_METRICS = []  # 子类应当重写此属性，定义组件产生的性能指标
    MONITORED_STATES = []  # 子类应当重写此属性，定义组件关心的代理状态
    
    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = []):
        self.name = name or self.__class__.__name__
        self.env = env
        self.agent = agent
        self.agent_id = agent.id
        self.supported_events = list(supported_events) # 组件特定事件
        from airfogsim.core import Task
        self.active_tasks: Dict[str, Task] = {}  # task_id -> Task对象
        self.task_processes: Dict[str, simpy.Process] = {} # task_id -> SimPy进程（包装器）
        self.task_resource_allocations: Dict[str, List[Tuple]] = {} # task_id -> 资源分配信息列表
        
        # 状态监听器的唯一ID
        self.state_listener_id = f'{self.agent_id}_{self.name}_state_listener'

        self._register_component_events()
        if not self.PRODUCED_METRICS or not all(isinstance(m, str) for m in self.PRODUCED_METRICS):
            warnings.warn(f"组件 {self.name} 没有定义PRODUCED_METRICS或格式不正确。")

    def _register_component_events(self):
        """向Agent注册组件的标准和特定事件。"""
        # 组件触发的关于任务的标准事件
        common_events = [
            'task_started', 'task_completed', 'task_failed', 'task_canceled', # 最终结果
            'state_changed', # 来自任务逻辑的中间状态更新
            'metric_changed' # 来自任务逻辑的性能指标更新
        ]
        all_events = set(common_events).union(self.supported_events)
        for event_name in all_events:
            full_event_name = f"{self.name}.{event_name}"
            if not self.agent.has_event(full_event_name):
                self.agent.register_event(full_event_name)

    def trigger_event(self, event_name: str, event_value: Any = None):
        """触发组件命名空间下的事件。"""
        full_event_name = f"{self.name}.{event_name}"
        return self.agent.trigger_event(full_event_name, event_value)

    def can_execute(self, task) -> bool:
        """检查组件名称是否匹配。"""
        return task.component_name == self.name

    def execute_task(self, task) -> simpy.Process:
        """
        开始执行任务。返回包含完整生命周期的SimPy进程。
        由Agent调用。
        """
        if task.id in self.task_processes and self.task_processes[task.id].is_alive:
             warnings.warn(f"组件 {self.name} 已经在执行任务 {task.id}")
             return self.task_processes[task.id] # 返回现有进程

        if not self.can_execute(task):
            return self.env.process(self._fail_immediately(task, f"组件 {self.name} 无法执行任务"))

        # 启动包装进程
        wrapper_proc = self.env.process(self._execute_task_wrapper(task))
        self.active_tasks[task.id] = task
        self.task_processes[task.id] = wrapper_proc
        return wrapper_proc

    def _fail_immediately(self, task, reason: str):
        """立即使任务失败的生成器函数。"""
        yield self.env.timeout(0)
        task.fail(reason)
        # 触发组件的失败事件
        self.trigger_event('task_failed', {
            'task_id': task.id, 'task_name': task.name,
            'reason': reason, 'time': self.env.now,
            'result': task.result
        })
        return task.result # 返回失败字典

    def _execute_task_wrapper(self, task):
        """包装资源获取、任务执行和清理。"""
        task_id = task.id
        resource_allocations = []
        
        try:
            # 1. 触发任务开始事件
            self.trigger_event('task_started', {'task_id': task_id, 'task_name': task.name, 'time': self.env.now})

            # 2. 获取资源需求并分配资源 - 由子类实现
            resource_allocations = self._allocate_task_resources(task)
            
            # 保存分配信息
            self.task_resource_allocations[task_id] = resource_allocations
            
            # 3. 计算初始指标
            initial_metrics = self._calculate_performance_metrics()
            # 向组件的监听器提供初始指标
            self.trigger_event('metric_changed', initial_metrics)

            # 4. 注册状态变化监听器
            # 使用event_registry的标准接口订阅事件
            self.env.event_registry.subscribe(
                self.agent_id, 
                'state_changed', 
                self.state_listener_id, 
                self._on_agent_state_changed
            )

            # 5. 执行任务逻辑
            task_logic_proc = self.env.process(task.execute(self.env, initial_metrics))
            result = yield task_logic_proc

            # 6. 触发最终结果事件（基于任务的最终状态）
            if task.status == TaskStatus.COMPLETED:
                self.trigger_event('task_completed', {
                    'task_id': task_id, 'task_name': task.name, 'time': task.end_time, 'result': result
                })
            elif task.status == TaskStatus.FAILED:
                 self.trigger_event('task_failed', {
                    'task_id': task_id, 'task_name': task.name, 'time': task.end_time,
                    'reason': task.failure_reason, 'result': result
                 })
            elif task.status == TaskStatus.CANCELED:
                 self.trigger_event('task_canceled', {
                     'task_id': task_id, 'task_name': task.name, 'time': task.end_time,
                     'reason': task.failure_reason, 'result': result # 使用failure_reason作为取消原因
                 })

            return result # 返回task.execute的结果

        except simpy.Interrupt as i:
            reason = f"执行被中断: {i.cause}"
            task_logic_proc.interrupt(reason)
            if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                 task.cancel(reason, self.env.now) # 假设中断时取消
            # 触发取消事件
            self.trigger_event('task_canceled', {
                 'task_id': task_id, 'task_name': task.name, 'time': self.env.now, 'reason': reason, 'result': task.result
            })
            return task.result

        except Exception as e:
            print(f"时间 {self.env.now}: 组件 {self.name} 在任务 {task_id} 的包装器中出错: {str(e)}")
            import traceback; traceback.print_exc()
            reason = f"组件执行错误: {str(e)}"
            if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
                 task.fail(reason)
            # 触发失败事件
            self.trigger_event('task_failed', {
                'task_id': task_id, 'task_name': task.name, 'time': self.env.now, 'reason': reason, 'result': task.result
            })
            return task.result
        finally:
            # 7. 清理
            # 取消状态监听 - 使用正确的EventRegistry方法
            self.env.event_registry.unsubscribe(self.agent_id, 'state_changed', self.state_listener_id)
            
            # 释放资源 - 由子类实现的方法
            self._release_task_resources(task_id, resource_allocations)

            # 清理组件状态
            if task_id in self.active_tasks: del self.active_tasks[task_id]
            if task_id in self.task_processes: del self.task_processes[task_id]
            if task_id in self.task_resource_allocations: del self.task_resource_allocations[task_id]


    def _on_agent_state_changed(self, event_data):
        """
        当代理状态改变时的回调。
        
        Args:
            event_data: 包含状态变化信息的字典，格式为
                {'key': 状态键, 'old_value': 旧值, 'new_value': 新值, 'time': 时间}
        """
        # 检查事件来源是否是我们关心的代理
        if 'key' not in event_data:
            return
            
        # 获取变化的状态键
        key = event_data['key']
            
        # 如果状态变化是我们监控的状态，或者我们监控所有状态（空列表）
        if not self.MONITORED_STATES or key in self.MONITORED_STATES:
            # 重新计算性能指标并触发事件
            current_metrics = self._calculate_performance_metrics()
            self.trigger_event('metric_changed', current_metrics)

    def _allocate_task_resources(self, task) -> List[Tuple]:
        """
        为任务分配所需资源。
        返回格式为 [(resource_type, allocation_id), ...] 的列表
        子类应该实现此方法来处理特定的资源分配逻辑
        """
        # 默认实现 - 获取资源需求但不做任何分配
        required_resources = self.get_resource_requirements(task)
        # 如果子类没有实现特定的分配逻辑，返回空列表
        return []
    
    def _release_task_resources(self, task_id: str, allocations: List[Tuple]):
        """
        释放任务使用的资源。
        子类应该实现此方法来处理特定的资源释放逻辑
        
        Args:
            task_id: 任务ID
            allocations: 由_allocate_task_resources返回的资源分配列表
        """
        # 默认实现 - 不做任何操作
        pass

    # --- 子类需要实现的方法 ---
    def get_resource_requirements(self, task) -> List[Dict]:
        """返回任务所需资源规格的字典列表。"""
        # 示例: return [{'type': 'airspace', 'airspace_id': 'main_airspace'}]
        raise NotImplementedError("子类必须实现get_resource_requirements")
    
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """基于当前代理状态和已分配资源计算组件指标。"""
        # 示例: return {'processing_speed': self.agent.get_state('cpu_usage') * factor}
        raise NotImplementedError("子类必须实现_calculate_performance_metrics")