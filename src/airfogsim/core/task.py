"""
AirFogSim任务(Task)核心模块

该模块定义了仿真系统中的任务类。任务是代理通过组件执行的具体动作，
可以改变代理的状态和拥有的对象。主要内容包括：
1. Task类：任务基类，定义了任务的生命周期、执行逻辑和状态管理
2. 任务状态管理：包括进度跟踪、完成、失败和取消等状态转换
3. 代理对象操作：任务可以在完成、失败或取消时操作代理拥有的对象

@author: zhiwei wei
@email: 2311769@tongji.edu.cn
"""

import uuid
from airfogsim.core.enums import TaskStatus, TaskPriority
from typing import Dict, Optional, List, Type, Any
import simpy
from abc import ABC, abstractmethod
from airfogsim.utils.logging_config import get_logger

# 获取logger
logger = get_logger(__name__)

class Task:
    NECESSARY_METRICS = [] # Metrics required for task execution
    PRODUCED_STATES = [] # States produced by task execution
    # **************************

    """Represents a specific action performed by a component, initiated by an agent."""
    def __init__(self, env, agent, component_name: str, task_name: str,
                 workflow_id: Optional[str] = None, # ID of the workflow this task belongs to
                 target_state: Optional[Dict] = None, # Optional: Describe desired outcome
                 properties: Optional[Dict] = None): # Generic properties (e.g., duration, demand)
        self.id = 'task_'+str(uuid.uuid4().hex[:8])
        self.name = task_name
        self.agent_id = agent.id
        self.agent = agent
        self.component_name = component_name
        self.workflow_id = workflow_id # Link to the workflow this task belongs to
        self.target_state = target_state or {}
        self.properties = properties or {}
        self.refresh_interval = self.properties.get('refresh_interval', env.visual_interval) # s

        # 任务优先级和抢占属性
        priority_str = self.properties.get('priority', 'normal')
        self.priority = TaskPriority.from_string(priority_str) if hasattr(TaskPriority, 'from_string') else TaskPriority.NORMAL
        self.preemptive = self.properties.get('preemptive', False)
        self.preemption_count = 0  # 记录任务被抢占的次数

        self.status: TaskStatus = TaskStatus.PENDING
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.result: Optional[Dict] = None
        self.failure_reason: Optional[str] = None

        self.env = env
        self.event_registry = env.event_registry # Use env's registry

        # Execution state (managed during component execution)
        self.current_metrics: Dict = {} # Performance metrics from component
        self.last_update_time: Optional[float] = None
        self.progress: float = 0.0

        # 判断self的class的PRODUCE_STATES是否为空
        if not self.PRODUCED_STATES or not all(isinstance(state, str) for state in self.PRODUCED_STATES):
            raise ValueError("Task subclass must define PRODUCED_STATES as a list of state names.")

        templates = agent.get_state_templates()
        template_keys = list(templates.keys())
        # 确认self的PRODUCED_STATES都在agent的state_templates中
        if not all(state in template_keys for state in self.PRODUCED_STATES):
            raise ValueError(f"Task subclass PRODUCED_STATES must match agent state template keys. \nGot: {self.PRODUCED_STATES}\nAvailable: {template_keys}")

        # 判断self的class的NECESSARY_METRICS是否为空
        if not self.NECESSARY_METRICS or not all(isinstance(metric, str) for metric in self.NECESSARY_METRICS):
            raise ValueError("Task subclass must define NECESSARY_METRICS as a list of metric names.")

        # 判断task的PRODUCED_STATES是否和agent的component的MONITORED_STATES有重叠，如果有，则会导致循环触发事件
        # 这里的self.agent.get_component是一个字典，key是component_name，value是component对象
        component = self.agent.get_component(component_name)
        if not component:
            raise ValueError(f"Agent {self.agent_id} does not have component '{component_name}'")

    # --- Task Logic (to be run by component) ---
    def _on_metric_changed(self, event_data):
        """
        处理组件的metric_changed事件

        Args:
            event_data: 事件数据，包含新的性能指标
        """
        if isinstance(event_data, dict):
            self.current_metrics.update(event_data)
        else:
            logger.warning(f"时间 {self.env.now}: Task {self.id} received invalid metrics: {event_data}")

    def execute(self, env, initial_metrics: Dict):
        """
        Core task execution logic. Should be a generator function (SimPy process).
        Run within the component's execution context.
        """
        self.current_metrics = initial_metrics.copy()
        self.last_update_time = env.now
        self.start_time = env.now
        self.status = TaskStatus.RUNNING

        # 订阅组件的metric_changed事件
        metric_listener_id = f"{self.id}_metric_listener"
        self.event_registry.subscribe(
            source_id=self.agent_id,
            event_name=f'{self.component_name}.metric_changed',
            listener_id=metric_listener_id,
            callback=self._on_metric_changed
        )

        try:
            # --- Main Execution Loop ---
            while self.progress < 1.0:
                remaining_time = self.estimate_remaining_time(self.current_metrics)
                # print(f"DEBUG Task {self.id} progress {self.progress:.2f}, est remaining: {remaining_time:.2f}")

                if remaining_time <= 1e-9: # Epsilon for float comparison
                     self.progress = 1.0 # Force completion if time is negligible
                     break

                # Wait for time passage
                completion_timeout = env.timeout(remaining_time + 1e-9) # Epsilon for float comparison
                visual_update_event = self.event_registry.get_event(env.id, 'visual_update')
                refresh_interval = env.timeout(self.refresh_interval)

                # Wait for the timeout or visual update event
                yield env.any_of([completion_timeout, visual_update_event, refresh_interval])

                current_time = env.now

                # Update internal state/progress based on elapsed time and current metrics
                self._update_task_state(self.current_metrics)
                self.last_update_time = current_time

                # Trigger component state change (visuals or internal logic)
                self.event_registry.trigger_event(self.id, 'state_changed',
                                                  self._get_current_task_state_repr())
                self.agent.update_states(self._get_task_specific_state_repr())

                # Check if finished after update
                if self.progress >= 1.0:
                     break

            # --- Completion ---
            if self.progress >= 1.0:
                 self.complete()
            else:
                 # Should not happen if logic is correct
                 self.fail("Execution loop finished unexpectedly before completion.")

            # 取消订阅metric_changed事件
            self._unsubscribe_metric_changed()

            return self.result # Return final result dict

        except simpy.Interrupt as i:
            logger.info(f"时间 {env.now}: Task {self.id} ({self.name}) interrupted: {i.cause}")
            self.fail(f"Interrupted: {i.cause}")

            # 取消订阅metric_changed事件
            self._unsubscribe_metric_changed()

            return self.result
        except Exception as e:
            logger.error(f"时间 {env.now}: Task {self.id} ({self.name}) execution error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.fail(f"Execution error: {str(e)}")

            # 取消订阅metric_changed事件
            self._unsubscribe_metric_changed()

            return self.result


    # --- Abstract/Helper Methods for Task Logic ---
    def _update_task_state(self, performance_metrics: Dict):
        """Update task progress and internal state. Called within execute loop."""
        raise NotImplementedError("Subclasses must implement this method.")

    def estimate_remaining_time(self, performance_metrics) -> float:
        raise NotImplementedError("Subclasses must implement this method.")

    def _get_current_task_state_repr(self) -> Dict:
        """Return a dictionary representing the task's current state for events."""
        current_state = {
            'task_id': self.id,
            'task_name': self.name,
            'task_status': self.status.name,
            'progress': self.progress,
            # Add other relevant state parts
        }
        task_specific_state = self._get_task_specific_state_repr()
        for key in task_specific_state:
            # 确认task_specific_state的key都是self.PRODUCED_STATES的子集
            if key not in self.PRODUCED_STATES:
                raise ValueError(f"Task-specific state key '{key}' not in PRODUCED_STATES.")
        current_state.update(task_specific_state)
        return current_state

    def _get_task_specific_state_repr(self) -> Dict:
        """Return a dictionary with task-specific state details. Subclasses override."""
        raise NotImplementedError("Subclasses must implement this method.")

    # --- Priority and Preemption Methods ---
    def can_preempt(self, other_task) -> bool:
        """
        检查当前任务是否可以抢占另一个任务

        Args:
            other_task: 另一个任务

        Returns:
            bool: 如果当前任务可以抢占另一个任务，则返回True
        """
        # 如果当前任务不可抢占，则返回False
        if not self.preemptive:
            return False

        # 如果当前任务优先级高于另一个任务，则可以抢占
        return self.priority.value > other_task.priority.value

    def on_preempted(self):
        """当任务被抢占时调用"""
        self.preemption_count += 1
        logger.info(f"时间 {self.env.now}: 任务 {self.id} ({self.name}) 被抢占，已被抢占 {self.preemption_count} 次")

    def get_priority_info(self) -> Dict:
        """
        获取任务优先级信息

        Returns:
            Dict: 包含优先级和抢占信息的字典
        """
        return {
            'priority': self.priority.name,
            'priority_value': self.priority.value,
            'preemptive': self.preemptive,
            'preemption_count': self.preemption_count
        }

    # --- Possessing Object Management Methods ---
    def _possessing_object_on_complete(self):
        """
        处理任务完成时对代理拥有对象的操作。
        子类应该覆盖此方法以实现特定的对象操作逻辑。
        默认实现不执行任何操作。
        """
        pass

    def _possessing_object_on_fail(self):
        """
        处理任务失败时对代理拥有对象的操作。
        子类应该覆盖此方法以实现特定的对象操作逻辑。
        默认实现不执行任何操作。
        """
        pass

    def _possessing_object_on_cancel(self):
        """
        处理任务取消时对代理拥有对象的操作。
        子类应该覆盖此方法以实现特定的对象操作逻辑。
        默认实现不执行任何操作。
        """
        pass

    def _unsubscribe_metric_changed(self):
        """
        取消订阅metric_changed事件
        """
        metric_listener_id = f"{self.id}_metric_listener"
        try:
            self.event_registry.unsubscribe(
                source_id=self.agent_id,
                event_name=f'{self.component_name}.metric_changed',
                listener_id=metric_listener_id
            )
        except Exception as unsubscribe_error:
            logger.error(f"时间 {self.env.now}: 取消订阅失败: {unsubscribe_error}")

    # --- Status Update Methods ---
    def complete(self):
        if self.status == TaskStatus.COMPLETED: return
        self.status = TaskStatus.COMPLETED
        self.end_time = self.env.now
        self.progress = 1.0
        self.result = {"status": "completed", "time": self.end_time}
        # print(f"时间 {timestamp}: Task {self.id} ({self.name}) completed.")

        # 调用对象操作方法
        self._possessing_object_on_complete()

        self._update_task_state(self.current_metrics) # Ensure final state update

    def fail(self, reason: str):
        if self.status == TaskStatus.FAILED: return
        self.status = TaskStatus.FAILED
        self.end_time = self.env.now
        self.failure_reason = reason
        self.result = {"status": "failed", "reason": reason, "time": self.end_time}
        # print(f"时间 {timestamp}: Task {self.id} ({self.name}) failed: {reason}")

        # 调用对象操作方法
        self._possessing_object_on_fail()

    def cancel(self, reason: str, timestamp: float):
        if self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED): return
        self.status = TaskStatus.CANCELED
        self.end_time = timestamp
        self.failure_reason = reason # Use failure_reason for cancel reason too?
        self.result = {"status": "canceled", "reason": reason, "time": self.end_time}
        # print(f"时间 {timestamp}: Task {self.id} ({self.name}) canceled: {reason}")

        # 调用对象操作方法
        self._possessing_object_on_cancel()
