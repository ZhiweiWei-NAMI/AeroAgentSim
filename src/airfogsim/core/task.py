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
from airfogsim.core.enums import TaskStatus
from typing import Dict, Optional, List, Type, Any
import simpy
from abc import ABC, abstractmethod

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
    def execute(self, env, initial_metrics: Dict):
        """
        Core task execution logic. Should be a generator function (SimPy process).
        Run within the component's execution context.
        """
        self.current_metrics = initial_metrics.copy()
        self.last_update_time = env.now
        self.start_time = env.now
        self.status = TaskStatus.RUNNING

        try:
            # --- Main Execution Loop ---
            while self.progress < 1.0:
                remaining_time = self.estimate_remaining_time(self.current_metrics)
                # print(f"DEBUG Task {self.id} progress {self.progress:.2f}, est remaining: {remaining_time:.2f}")

                if remaining_time <= 1e-9: # Epsilon for float comparison
                     self.progress = 1.0 # Force completion if time is negligible
                     break

                # Wait for time passage OR metric update event from the *component*
                completion_timeout = env.timeout(remaining_time + 1e-9) # Epsilon for float comparison
                # Listen for metric changes specific to this component instance
                metric_change_event = self.event_registry.get_event(self.agent_id, f'{self.component_name}.metric_changed')
                visual_update_event = self.event_registry.get_event(env.id, 'visual_update')

                # Wait for the first event
                triggered = yield env.any_of([completion_timeout, metric_change_event, visual_update_event])

                current_time = env.now

                if metric_change_event in triggered:
                    new_metrics = triggered[metric_change_event]
                    # print(f"DEBUG Task {self.id} received metrics: {new_metrics}")
                    if isinstance(new_metrics, dict):
                        self.current_metrics.update(new_metrics)
                    else:
                        print(f"时间 {current_time}: Task {self.id} received invalid metrics: {new_metrics}")
                    # Re-fetch event for next wait  
                    metric_change_event = self.event_registry.get_event(self.agent_id, f'{self.component_name}.metric_changed')

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

            return self.result # Return final result dict

        except simpy.Interrupt as i:
            print(f"时间 {env.now}: Task {self.id} ({self.name}) interrupted: {i.cause}")
            self.fail(f"Interrupted: {i.cause}")
            return self.result
        except Exception as e:
            print(f"时间 {env.now}: Task {self.id} ({self.name}) execution error: {str(e)}")
            import traceback; traceback.print_exc()
            self.fail(f"Execution error: {str(e)}")
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
