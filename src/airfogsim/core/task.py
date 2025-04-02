import uuid
from airfogsim.core.enums import TaskStatus
from typing import Dict, Optional, List, Type
import simpy
from abc import ABC, abstractmethod
from .enums import TaskProofType
# TaskProof class to add
class TaskProof:
    def __init__(self, env, workflow_id: str, proof_id: str, owner,
                 data: Dict = None, handover_workflow_class=None):
        self.id = proof_id or f"proof_{uuid.uuid4().hex}"
        self.env = env
        self.workflow_id = workflow_id
        self.data = data or {}
        self.creation_time = None
        self.last_updated = None
        self.owner = owner
        self.owner_id = owner.id  # Current agent ID that owns this proof
        self.updated_history: List[Dict] = []  # Log of updates
        self.handover_workflow_class = handover_workflow_class
        self.type = TaskProofType.GENERIC
        
    def update(self, data: Dict, agent_id):
        self.data.update(data)
        self.last_updated = self.env.now
        self.updated_history.append({'time': self.last_updated, 'data': data, 'agent_id': agent_id})
        return self
        
    def handover(self, target_agent):
        """Called when proof is transferred between agents"""
        if not self.handover_workflow_class:
            self.owner = target_agent
            self.owner_id = target_agent.id
            return self
        
        # 创建工作流（所有用于handover的workflow，必须要能识别target_agent作为自身的属性）
        workflow = self.env.create_workflow(self.handover_workflow_class, name=f"proof_handover_{self.id}",
                                            owner=self.owner_id, properties={'target_agent': target_agent})
        
        if not hasattr(workflow, 'target_agent'):
            raise ValueError(f"Handover workflow {self.handover_workflow_class.__name__} must have 'target_agent' property.")

        # 创建一个SimPy事件来等待工作流完成
        workflow_complete = self.env.event()
        
        # 订阅工作流状态变更事件
        def on_workflow_status_changed(event_value):
            new_status = event_value.get('new_status')
            if new_status in ('completed', 'failed', 'canceled'):
                # 终止状态 - 触发我们的完成事件
                details = event_value.get('event_details', {})
                workflow_complete.succeed({
                    'status': new_status, 
                    'details': details,
                    'time': event_value.get('time', self.env.now)
                })
        
        # 注册监听器
        listener_id = f"handover_{self.id}_{self.env.now}"
        self.env.event_registry.subscribe(
            workflow.id, 'status_changed', listener_id, on_workflow_status_changed
        )
        
        # 启动工作流
        workflow.start()
        
        # 等待工作流达到终止状态
        try:
            result = yield workflow_complete
            
            # 取消订阅避免内存泄漏
            self.env.event_registry.unsubscribe(workflow.id, 'status_changed', listener_id)
            
            # 根据结果决定是否转移所有权
            if result['status'] == 'completed':
                self.env.event_registry.trigger_event(self.workflow_id, 'proof_handover', {
                    'proof_id': self.id,
                    'source_agent_id': self.owner_id,
                    'target_agent_id': target_agent.id,
                    'time': self.env.now
                })
                self.owner_id = target_agent.id
            else:
                print(f"时间 {self.env.now}: Proof {self.id} handover failed: {result.get('details', {}).get('reason', 'unknown reason')}")
        
        except Exception as e:
            print(f"时间 {self.env.now}: Error during proof handover: {e}")
            # 确保取消订阅
            self.env.event_registry.unsubscribe(workflow.id, 'status_changed', listener_id)
            
        return self
    
class Task:
    PROOF_CLASS = None  # Proof type for tasks
    NECESSARY_METRICS = [] # Metrics required for task execution
    PRODUCED_STATES = [] # States produced by task execution
    # **************************

    """Represents a specific action performed by a component, initiated by an agent."""
    def __init__(self, env, agent, component_name: str, task_name: str,
                 workflow_id: Optional[str] = None, # ID of the workflow this task belongs to
                 proof_id: Optional[str] = None, # ID for the expected proof outcome
                 target_state: Optional[Dict] = None, # Optional: Describe desired outcome
                 properties: Optional[Dict] = None): # Generic properties (e.g., duration, demand)
        self.id = 'task_'+str(uuid.uuid4())
        self.name = task_name
        self.agent_id = agent.id
        self.agent = agent
        self.component_name = component_name
        self.workflow_id = workflow_id # Link to the workflow this task belongs to
        self.proof_id = proof_id # Link to the proof this task affects/creates
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
        self.proof: Optional[TaskProof] = None
        self._proof_handover_workflow_class = None # Class to use for proof handover，这个是由子类定义的，不可外部更改，并且需要把proof从agent到target存储好

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

    def handover_proof(self, target_task):
        """移交当前任务的证明到目标任务
    
        Args:
            target_task: 目标任务，将接收证明
        """
        if not self.proof:
            print(f"时间 {self.env.now}: 任务 {self.id} 没有证明可以移交")
            return False
    
        # 将证明转移到目标任务的代理
        target_agent = target_task.agent
        yield self.env.process(self.proof.handover(target_agent))
        
        # 更新目标任务的证明引用
        target_task.proof = self.proof
        self.proof = None  # 清除当前任务的证明引用
        return True

    @classmethod
    def get_proof_class(cls):
        """Return the type of proof this task produces (if any)"""
        return getattr(cls, 'PROOF_CLASS', None)
    
    def create_proof(self, data: Dict = None):
        """Create a proof for this task"""
        if not self.get_proof_class() or not self.workflow_id:
            return None
            
        self.proof = self.get_proof_class()(
            env=self.env,
            owner=self.agent,
            workflow_id=self.workflow_id,            
            proof_id=self.proof_id,
            data=data or {},
            handover_workflow_class=self._proof_handover_workflow_class
        )
        self.proof.owner_id = self.agent_id
        return self.proof
    
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
                completion_timeout = env.timeout(remaining_time)
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
                self._trigger_workflow_events()
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
        # **Subclasses should implement specific progress logic here**
        # Example: Simple time-based progress
        # elapsed_time = self.env.now - self.last_update_time
        # for metric in self.NECESSARY_METRICS:
        #     if metric not in performance_metrics:
        #         self.fail(f"Missing required metric '{metric}' for task execution.")
        #         return
        # total_time = self.estimate_total_time(performance_metrics)
        # if total_time > 0:
        #      increment = elapsed_time / total_time
        #      self.progress = min(1.0, self.progress + increment)
        # elif elapsed_time > 0: # If total time is 0 or less, complete instantly
        #     self.progress = 1.0
        raise NotImplementedError("Subclasses must implement this method.")

    def _trigger_workflow_events(self):
        raise NotImplementedError("Subclasses must implement this method.")

    def estimate_remaining_time(self, performance_metrics) -> float:
        raise NotImplementedError("Subclasses must implement this method.")

    def _get_current_task_state_repr(self) -> Dict:
        """Return a dictionary representing the task's current state for events."""
        current_state = {
            'task_id': self.id,
            'task_name': self.name,
            'status': self.status.name,
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

    # --- Status Update Methods ---
    def complete(self):
        if self.status == TaskStatus.COMPLETED: return
        self.status = TaskStatus.COMPLETED
        self.end_time = self.env.now
        self.progress = 1.0
        self.result = {"status": "completed", "time": self.end_time}
        # print(f"时间 {timestamp}: Task {self.id} ({self.name}) completed.")
        # Create proof if applicable
        if self.get_proof_class() and self.workflow_id and not self.proof:
            self.create_proof(self._get_current_task_state_repr())
        elif self.proof:
            self.proof.update(self._get_current_task_state_repr(), self.agent_id)
        self._update_task_state(self.current_metrics) # Ensure final state update
        self._trigger_workflow_events()
        
    def fail(self, reason: str):
        if self.status == TaskStatus.FAILED: return
        self.status = TaskStatus.FAILED
        self.end_time = self.env.now
        self.failure_reason = reason
        self.result = {"status": "failed", "reason": reason, "time": self.end_time}
        # print(f"时间 {timestamp}: Task {self.id} ({self.name}) failed: {reason}")

    def cancel(self, reason: str, timestamp: float):
         if self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED): return
         self.status = TaskStatus.CANCELED
         self.end_time = timestamp
         self.failure_reason = reason # Use failure_reason for cancel reason too?
         self.result = {"status": "canceled", "reason": reason, "time": self.end_time}
         # print(f"时间 {timestamp}: Task {self.id} ({self.name}) canceled: {reason}")
