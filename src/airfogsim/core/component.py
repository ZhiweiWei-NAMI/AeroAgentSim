"""
AirFogSim组件(Component)核心模块

该模块定义了代理(Agent)的组件基类，组件是代理功能的实际执行者。
每个组件负责特定类型的任务执行，并管理任务所需的资源。核心功能包括：
1. 任务执行：包装和管理任务的完整生命周期
2. 资源管理：分配和释放任务所需的资源
3. 事件处理：触发与任务执行相关的事件
4. 性能监控：计算和更新组件的性能指标

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

from airfogsim.core.enums import TaskStatus
import warnings
from typing import List, Dict, Any, Optional, Tuple
import simpy
from typing import Any, Dict, List, Set
from abc import ABC, abstractmethod
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class Component:
    """执行任务的组件，使用分配的资源。"""
    PRODUCED_METRICS = []  # 子类应当重写此属性，定义组件产生的性能指标
    MONITORED_STATES = []  # 子类应当重写此属性，定义组件关心的代理状态

    def __init__(self, env, agent, name: Optional[str] = None,
                 supported_events: List[str] = [], properties=None):
        self.name = name or self.__class__.__name__
        self.env = env
        self.agent = agent
        self.agent_id = agent.id
        self.supported_events = list(supported_events) # 组件特定事件
        from airfogsim.core import Task
        self.active_tasks: Dict[str, Task] = {}  # task_id -> Task对象
        self.task_processes: Dict[str, simpy.Process] = {} # task_id -> SimPy进程（包装器）
        self.properties = properties or {} # 组件属性
        self.is_error = False  # 组件是否处于错误状态

        # 状态监听器的唯一ID
        self.state_listener_id = f'{self.agent_id}_{self.name}_state_listener'

        self._register_component_events()
        if not self.PRODUCED_METRICS or not all(isinstance(m, str) for m in self.PRODUCED_METRICS):
            warnings.warn(f"组件 {self.name} 没有定义PRODUCED_METRICS或格式不正确。")

        # 把produced_metrics 以 self.current_metrics的形式保存
        self.current_metrics = {metric: None for metric in self.PRODUCED_METRICS}
        self._validate_agent_states()

    def _validate_agent_states(self):
        """验证代理状态是否符合要求。"""
        # 判断是否都在self.MONITORED_STATES中
        agent_states = self.agent.get_state_templates()
        tmp_states = self.MONITORED_STATES.copy()
        # 移除tmp_states中带.的state
        tmp_states = [state for state in tmp_states if '.' not in state]
        if not all(state in agent_states for state in tmp_states):
            raise ValueError(f"组件 {self.name} 监控的状态 {tmp_states} 不符合要求，必须包含在代理的状态中")

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
            # 确保事件存在
            self.agent.get_event(full_event_name)

    def trigger_event(self, event_name: str, event_value: Any = None):
        """触发组件命名空间下的事件。"""
        full_event_name = f"{self.name}.{event_name}"
        return self.agent.trigger_event(full_event_name, event_value)

    def can_execute(self, task) -> bool:
        """检查组件名称是否匹配"""
        return task.component_name == self.name

    @property
    def is_busy(self) -> bool:
        """检查组件是否正在执行任务"""
        return len(self.active_tasks) > 0

    def is_available(self) -> bool:
        """检查组件是否可用"""
        # 如果组件处于错误状态，则不可用
        if self.is_error:
            return False
        # 如果组件正在执行任务，则不可用
        return not self.is_busy

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

    def _validate_metrics(self, metrics: Dict[str, Any]):
        """验证性能指标是否符合要求。"""
        # 判断是否都在self.current_metrics中
        if not all(metric in self.current_metrics for metric in metrics):
            raise ValueError(f"性能指标 {metrics} 不符合要求，必须包含 {self.current_metrics}")

    def _execute_task_wrapper(self, task):
        """包装资源获取、任务执行和清理。"""
        task_id = task.id

        try:
            # 1. 触发任务开始事件
            self.trigger_event('task_started', {'task_id': task_id, 'task_name': task.name, 'time': self.env.now})

            # 2. 计算初始指标
            initial_metrics = self._calculate_performance_metrics()
            self._validate_metrics(initial_metrics)
            self.current_metrics.update(initial_metrics)

            # 3. 向组件的监听器提供初始指标
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
            logger.error(f"时间 {self.env.now}: 组件 {self.name} 在任务 {task_id} 的包装器中出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
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

            # 清理组件状态
            if task_id in self.active_tasks: del self.active_tasks[task_id]
            if task_id in self.task_processes: del self.task_processes[task_id]


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
            self._validate_metrics(current_metrics)
            # 判断current_metrics和self.current_metrics是否相同
            if all(
                current_metrics.get(metric) == self.current_metrics.get(metric)
                for metric in self.PRODUCED_METRICS
            ):
                return
            # 更新当前指标
            self.current_metrics.update(current_metrics)
            self.trigger_event('metric_changed', current_metrics)

    def disable(self):
        """禁用组件，将其设置为错误状态并取消所有正在执行的任务"""
        self.is_error = True

        # 取消所有正在执行的任务
        for task_id, process in list(self.task_processes.items()):
            if process.is_alive:
                reason = f"组件 {self.name} 被禁用"
                process.interrupt(cause=reason)

        # 触发组件禁用事件
        self.trigger_event('disabled', {
            'component_name': self.name,
            'time': self.env.now,
            'reason': "组件处于错误状态"
        })

    def enable(self):
        """启用组件，将其从错误状态恢复"""
        self.is_error = False
        # 触发组件启用事件
        self.trigger_event('enabled', {
            'component_name': self.name,
            'time': self.env.now
        })

    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        """基于当前代理状态和已分配资源计算组件指标。"""
        # 示例: return {'processing_speed': self.agent.get_state('cpu_usage') * factor}
        raise NotImplementedError("子类必须实现_calculate_performance_metrics")