
## AirFogSim Task & TaskProof Developer Documentation

This document guides developers in creating custom `Task` subclasses within the AirFogSim framework. It explains the `Task` object's role, its execution lifecycle managed by a `Component`, interaction with Agent states and performance metrics, and the related `TaskProof` concept.

### 1. Overview

*   **`Task`:** Represents a specific, executable action or piece of work. It contains the *logic* for performing the action. Tasks are initiated by an `Agent` but are executed *by* a `Component`.
*   **`TaskProof`:** An optional object associated with a `Task` and `Workflow`. It serves as evidence or a record of the task's outcome or progress, potentially transferable between agents involved in a workflow.

**Relationship:**
`Agent` (decides *what* & *when*) -> `Component` (provides *capability* & *resources*) -> `Task` (defines *how* the action is done) -> `TaskProof` (records *outcome/evidence*).

### 2. `TaskProof` Class

The `TaskProof` class provides a mechanism to track verifiable outcomes or data associated with tasks within a workflow context.

*   **Purpose:** To store data (`self.data`) generated or verified by a task, link it to a `Workflow` (`self.workflow_id`), track its ownership (`self.owner_id`), and log its history (`self.updated_history`).
*   **Creation:** Usually created by a `Task` upon completion (or during execution) via `task.create_proof()`, requiring the `Task` subclass to define a `PROOF_CLASS`.
*   **Updating:** Use `proof.update(data, agent_id)` to add or modify data. This logs the update time, data, and the agent responsible.
*   **Handover (`task.handover_proof(target_task)`):**
    *   Transfers ownership of the task's proof, typically when a workflow progresses to a task managed by a different agent.
    *   **Internal Implementation:** The method extracts the target agent from the target task (`target_agent = target_task.agent`), then calls `proof.handover(target_agent)` to perform the actual handover.
    *   **SimPy Generator:** This is a SimPy generator function and must be called using `yield env.process(task.handover_proof(target_task))`.
    *   **Optional Handover Workflow:** If `task._proof_handover_workflow_class` is set, the handover triggers a separate, dedicated workflow to manage the transfer process itself (e.g., for verification steps). The `handover_proof` method will wait for this workflow to complete before finalizing the ownership transfer. If no handover workflow is defined, ownership transfers immediately.
    *   **Task Proof References:** After handover is complete, the target task's `proof` attribute is updated to this proof, while the source task's `proof` attribute is set to `None`.
*   **Example (`LocationProof`):** A simple proof storing positional data, used by `MoveToTask`.

### 3. `Task` Base Class

The `Task` class is the abstract base for all specific actions. Subclasses define the actual behavior.

#### 3.1 Core Concepts

*   **Initialization (`__init__`):**
    *   Assigns unique ID, name, links to Agent, Component, Workflow, and Proof ID.
    *   Stores `target_state` (desired outcome) and `properties` (task-specific parameters like duration, target position).
    *   Initializes status (`PENDING`), times, result, etc.
    *   **Crucially, validates `PRODUCED_STATES` and `NECESSARY_METRICS` (see below).**
*   **Execution Context (`execute` method):**
    *   The `execute` method contains the core task logic as a **SimPy generator function**.
    *   **Important:** This method is called and run *by the `Component`* within its `_execute_task_wrapper`.
    *   It receives `initial_metrics` from the component.
    *   **Execution Loop:**
        1.  Sets status to `RUNNING`, records `start_time`.
        2.  Enters a `while self.progress < 1.0` loop.
        3.  Calculates `estimate_remaining_time()`.
        4.  **Waits (`yield`)** for the *earliest* of:
            *   `completion_timeout`: Time passage equal to the estimated remaining time.
            *   `metric_change_event`: An event triggered *by the owning Component* when its performance metrics change.
            *   `visual_update_event`: (Optional) Global event for visual updates.
        5.  If `metric_change_event` occurs, updates `self.current_metrics`.
        6.  Calls `self._update_task_state(self.current_metrics)` (abstract) to update progress and internal state based on elapsed time and current metrics.
        7.  Calls `self._trigger_workflow_events()` (abstract) to interact with the workflow.
        8.  Updates `self.last_update_time`.
        9.  Triggers a task-specific `state_changed` event.
        10. Updates the **Agent's state** using `self.agent.update_states(self._get_task_specific_state_repr())` (abstract method provides the state dictionary).
        11. Loop continues until `self.progress >= 1.0`.
    *   **Completion/Failure:** Calls `self.complete()` or `self.fail()` based on the outcome. Handles `simpy.Interrupt`.
    *   Returns the final `self.result` dictionary.
*   **Status Management (`complete`, `fail`, `cancel`):** Methods to set the final task status, end time, result dictionary, and failure reason. `complete` also handles `TaskProof` creation or updates.

#### 3.2 Mandatory Subclass Definitions

When creating a `Task` subclass, you **must** define/implement the following:

1.  **Class Attributes:**
    *   `NECESSARY_METRICS: List[str]`: A list of metric names (strings) that this task *requires* from the executing `Component`'s `_calculate_performance_metrics` output. The base `__init__` checks if this list is defined and non-empty. Example: `['speed', 'processing_power']`.
    *   `PRODUCED_STATES: List[str]`: A list of *Agent* state variable names (strings) that this task is expected to modify via `_get_task_specific_state_repr`. The base `__init__` checks that this list is defined, non-empty, and that all listed state keys exist in the `Agent`'s state templates (`agent.get_state_templates()`). Example: `['position', 'battery_level']`.
    *   `PROOF_CLASS: Type[TaskProof] | None`: (Optional) The specific `TaskProof` subclass (e.g., `LocationProof`) that this task creates or updates upon completion. If `None`, no proof is handled automatically.

2.  **Abstract Methods Implementation:**
    *   `estimate_total_time(self, performance_metrics: Dict) -> float`:
        *   **Input:** Dictionary of current performance metrics from the component.
        *   **Output:** Estimated *total* time required to complete the task from the *beginning* given the *current* metrics. Return `float('inf')` if completion is impossible with current metrics.
        *   **Example (`MoveToTask`):** Calculates based on total distance and current speed.
    *   `_update_task_state(self, performance_metrics: Dict)`:
        *   **Input:** Dictionary of current performance metrics.
        *   **Action:** This is the core progress logic. Update the task's internal state (e.g., distance covered, data processed) based on `performance_metrics` and the time elapsed since `self.last_update_time` (`self.env.now - self.last_update_time`). **Crucially, update `self.progress` (must be between 0.0 and 1.0).**
        *   **Example (`MoveToTask`):** Calculates distance moved using `speed` metric, updates `self.current_position` along the path, and updates `self.progress` based on distance traveled vs total distance.
    *   `_get_task_specific_state_repr(self) -> Dict`:
        *   **Output:** A dictionary containing the *Agent* state updates produced by this task at its current progress. Keys **must** be a subset of `PRODUCED_STATES`.
        *   **Action:** This dictionary is passed to `self.agent.update_states()` during the `execute` loop.
        *   **Example (`MoveToTask`):** Returns `{'position': self.current_position, 'direction': self.direction, ...}`.
    *   `_trigger_workflow_events(self)`:
        *   **Action:** Implement logic here to interact with the associated workflow, if necessary. This often involves updating the `TaskProof` (`self.proof.update(...)`) and triggering specific events on the Agent (e.g., `agent.trigger_event('proof_updated', ...)`).
        *   **Example (`MoveToTask`):** Updates the `LocationProof` with the current position and triggers `proof_updated` and `position_changed` events.

#### 3.3 Optional Overrides & Attributes

*   `estimate_remaining_time(self) -> float`: The base implementation calculates this based on `progress` and `estimate_total_time`. Override if a more sophisticated calculation is needed.
*   `_proof_handover_workflow_class: Type[Workflow] | None`: Set this attribute (typically in `__init__`) to a `Workflow` class if you want the proof handover process to be managed by a dedicated workflow.

### 4. Implementing a Custom Task Subclass (Example: `MoveToTask`)

1.  **Inherit:** `class MyTask(Task): ...`
2.  **Define Class Attributes:**
    ```python
    class ComputeDataTask(Task):
        NECESSARY_METRICS = ['processing_power'] # Needs CPU power metric
        PRODUCED_STATES = ['computation_load', 'result_data'] # Updates agent's load and stores results
        PROOF_CLASS = None # Doesn't produce a standard proof
    ```
3.  **Implement `__init__`:**
    ```python
    def __init__(self, env, agent, component_name, task_name, ..., properties=None):
        super().__init__(...)
        self.data_size = properties.get('data_size', 100) # e.g., MB
        self.complexity = properties.get('complexity', 1.0) # Factor affecting time
        self.processed_data = 0.0
        # Other initial setup
    ```
4.  **Implement `estimate_total_time`:**
    ```python
    def estimate_total_time(self, performance_metrics: Dict) -> float:
        power = performance_metrics.get('processing_power', 0) # e.g., MIPS
        if power <= 0:
            return float('inf')
        # Total operations = size * complexity
        # Time = Total operations / power
        total_ops = self.data_size * self.complexity * 1e6 # Example calculation
        return total_ops / power
    ```
5.  **Implement `_update_task_state`:**
    ```python
    def _update_task_state(self, performance_metrics: Dict):
        power = performance_metrics.get('processing_power', 0)
        elapsed_time = self.env.now - self.last_update_time
        ops_done_this_step = power * elapsed_time
        data_processed_this_step = ops_done_this_step / (self.complexity * 1e6) # Reverse calculation
        self.processed_data += data_processed_this_step

        total_ops = self.data_size * self.complexity * 1e6
        current_total_ops_done = self.processed_data * self.complexity * 1e6

        if total_ops > 0:
             self.progress = min(1.0, current_total_ops_done / total_ops)
        else:
             self.progress = 1.0
    ```
6.  **Implement `_get_task_specific_state_repr`:**
    ```python
    def _get_task_specific_state_repr(self) -> Dict:
        load = self.current_metrics.get('processing_power', 0) if self.status == TaskStatus.RUNNING else 0
        result = {'final_result': 'some_value'} if self.progress >= 1.0 else None # Only provide final result state on completion
        return {
            'computation_load': load,
            'result_data': result
            # Ensure 'result_data' key is in PRODUCED_STATES
        }
    ```
7.  **Implement `_trigger_workflow_events`:**
    ```python
    def _trigger_workflow_events(self):
        # Example: Trigger event when 50% complete
        if self.progress >= 0.5 and not hasattr(self, '_halfway_triggered'):
             self.agent.trigger_event('compute_halfway', {'task_id': self.id, 'time': self.env.now})
             self._halfway_triggered = True
        # Update proof if applicable
        # if self.proof: self.proof.update(...)
    ```

### 5. Key Takeaways

*   Tasks contain the *logic*, Components provide the *execution environment and resources*.
*   Subclasses **must** define `NECESSARY_METRICS`, `PRODUCED_STATES`, and implement `estimate_total_time`, `_update_task_state`, `_get_task_specific_state_repr`, and `_trigger_workflow_events`.
*   The `execute` method's core loop waits for time passage *or* component metric updates.
*   Tasks update the owning Agent's state via `_get_task_specific_state_repr`.
*   `TaskProof` provides an optional mechanism for outcome tracking and handover within workflows.
