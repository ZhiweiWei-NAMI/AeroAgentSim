## AirFogSim Task Developer Documentation

This document guides developers in creating custom `Task` subclasses within the AirFogSim framework. It explains the `Task` object's role as an atomic execution unit in the Workflow-Agent-Task framework, its execution lifecycle managed by a `Component`, interaction with Agent states, and performance metrics.

### 1. Overview

*   **`Task`:** Represents an atomic execution unit that encapsulates the logic for specific actions. Tasks define how work is performed, what resources are needed, what metrics are consumed, and what agent states are produced. Tasks are initiated by an `Agent` but are executed *by* a `Component`.

*   **Priority and Preemption:** Tasks can have priority levels (e.g., `TaskPriority.CRITICAL`, `TaskPriority.HIGH`) and preemption flags, allowing the system to model urgent tasks that can interrupt lower-priority ones.

**Relationship in the Workflow-Agent-Task Framework:**
`Workflow` (defines higher-level goal) -> `Agent` (decides *what* & *when*) -> `Component` (provides *capability* & *resources*) -> `Task` (defines *how* the action is done).

### 2. `Task` Base Class

The `Task` class is the abstract base for all specific actions. Subclasses define the actual behavior.

#### 2.1 Core Concepts

*   **Initialization (`__init__`):**
    *   Assigns unique ID, name, links to Agent, Component, and optional Workflow ID.
    *   Stores `target_state` (desired outcome) and `properties` (task-specific parameters like duration, target position).
    *   Initializes status (`PENDING`), times, result, etc.
    *   Sets priority and preemption flags if provided, allowing for task prioritization and interruption.
    *   **Crucially, validates `PRODUCED_STATES` and `NECESSARY_METRICS` (see below).** It checks that `PRODUCED_STATES` are defined and exist in the agent's state templates, and that `NECESSARY_METRICS` are defined.
*   **Execution Context (`execute` method):**
    *   The `execute` method contains the core task logic as a **SimPy generator function**.
    *   **Important:** This method is called and run *by the `Component`* within its `_execute_task_wrapper`.
    *   It receives `initial_metrics` from the component.
    *   **Execution Loop:**
        1.  Sets status to `RUNNING`, records `start_time`.
        2.  Enters a `while self.progress < 1.0` loop.
        3.  Calculates `estimate_remaining_time(self.current_metrics)`.
        4.  **Waits (`yield`)** for the *earliest* of:
            *   `completion_timeout`: Time passage equal to the estimated remaining time.
            *   `metric_change_event`: An event triggered *by the owning Component* (e.g., `ComponentName.metric_changed`) when its performance metrics change.
            *   `visual_update_event`: (Optional) Global event for visual updates.
        5.  If `metric_change_event` occurs, updates `self.current_metrics` with the new values from the event data.
        6.  Calls `self._update_task_state(self.current_metrics)` (abstract) to update progress and internal state based on elapsed time and current metrics.
        7.  Updates `self.last_update_time`.
        8.  Triggers a task-specific `state_changed` event (`self.id`, 'state_changed') with the task's current state representation (`_get_current_task_state_repr`).
        9.  Updates the **Agent's state** using `self.agent.update_states(self._get_task_specific_state_repr())` (abstract method provides the state dictionary).
        10. Loop continues until `self.progress >= 1.0`.
    *   **Completion/Failure:** Calls `self.complete()` or `self.fail()` based on the outcome. Handles `simpy.Interrupt`.
    *   Returns the final `self.result` dictionary.
*   **Status Management (`complete`, `fail`, `cancel`):** Methods to set the final task status, end time, result dictionary, and failure reason. These methods also call corresponding `_possessing_object_on_...` hooks.
*   **Possessing Object Hooks (`_possessing_object_on_complete`, `_possessing_object_on_fail`, `_possessing_object_on_cancel`):**
    *   These methods are called automatically when the task completes, fails, or is canceled, respectively.
    *   Subclasses can override these methods to implement logic for managing objects possessed by the agent (e.g., releasing a charging station resource upon completion or failure). The base implementations do nothing.

#### 2.2 Mandatory Subclass Definitions

When creating a `Task` subclass, you **must** define/implement the following:

1.  **Class Attributes:**
    *   `NECESSARY_METRICS: List[str]`: A list of metric names (strings) that this task *requires* from the executing `Component`'s `_calculate_performance_metrics` output. The base `__init__` checks if this list is defined and non-empty. Example: `['speed', 'processing_power']`.
    *   `PRODUCED_STATES: List[str]`: A list of *Agent* state variable names (strings) that this task is expected to modify via `_get_task_specific_state_repr`. The base `__init__` checks that this list is defined, non-empty, and that all listed state keys exist in the `Agent`'s state templates (`agent.get_state_templates()`). Example: `['position', 'battery_level', 'status']`.

2.  **Abstract Methods Implementation:**
    *   `_update_task_state(self, performance_metrics: Dict)`:
        *   **Input:** Dictionary of current performance metrics from the component.
        *   **Action:** This is the core progress logic. Update the task's internal state (e.g., distance covered, data processed) based on `performance_metrics` and the time elapsed since `self.last_update_time` (`self.env.now - self.last_update_time`). **Crucially, update `self.progress` (must be between 0.0 and 1.0).**
        *   **Example (`MoveToTask`):** Calculates distance moved using `speed` metric, updates `self.current_position` along the path, and updates `self.progress` based on distance traveled vs total distance. Also updates internal `self.battery_level` based on distance moved.
    *   `estimate_remaining_time(self, performance_metrics: Dict) -> float`:
        *   **Input:** Dictionary of current performance metrics from the component.
        *   **Output:** Estimated *remaining* time required to complete the task *from the current progress* given the *current* metrics. Return `float('inf')` if completion is impossible with current metrics. Return `0` if already complete.
        *   **Example (`MoveToTask`):** Calculates based on remaining distance (`self.total_distance - self.distance_traveled`) and current `speed`.
    *   `_get_task_specific_state_repr(self) -> Dict`:
        *   **Output:** A dictionary containing the *Agent* state updates produced by this task at its current progress. Keys **must** be a subset of `PRODUCED_STATES`.
        *   **Action:** This dictionary is passed to `self.agent.update_states()` during the `execute` loop.
        *   **Example (`MoveToTask`):** Returns `{'position': self.current_position, 'direction': self.direction, 'distance_traveled': self.distance_traveled, 'altitude': ..., 'battery_level': self.battery_level}`.

#### 2.3 Optional Overrides & Attributes

*   `_get_current_task_state_repr(self) -> Dict`: Override to add more task-specific details to the `state_changed` event triggered by the task itself. The base implementation includes basic info like ID, name, status, and progress, plus the content from `_get_task_specific_state_repr`.
*   `_possessing_object_on_complete(self)`: Override to add logic when the task completes successfully (e.g., release a resource).
*   `_possessing_object_on_fail(self)`: Override to add logic when the task fails (e.g., release a resource).
*   `_possessing_object_on_cancel(self)`: Override to add logic when the task is canceled (e.g., release a resource).

### 3. Implementing a Custom Task Subclass (Example: `MoveToTask`)

1.  **Inherit:** `class MyTask(Task): ...`
2.  **Define Class Attributes:**
    ```python
    class ComputeDataTask(Task):
        NECESSARY_METRICS = ['processing_power'] # Needs CPU power metric from component
        PRODUCED_STATES = ['computation_load', 'result_data', 'status'] # Updates agent's load, stores results, and sets status
    ```
3.  **Implement `__init__`:**
    ```python
    def __init__(self, env, agent, component_name, task_name, ..., properties=None):
        super().__init__(...)
        # Store task-specific parameters from properties
        self.data_size = properties.get('data_size', 100) # e.g., MB
        self.complexity = properties.get('complexity', 1.0) # Factor affecting time
        # Initialize internal task state variables
        self.processed_data = 0.0
        # Other initial setup
    ```
4.  **Implement `estimate_remaining_time`:**
    ```python
    def estimate_remaining_time(self, performance_metrics: Dict) -> float:
        power = performance_metrics.get('processing_power', 0) # e.g., MIPS
        if power <= 0:
            return float('inf') # Cannot complete without power

        total_ops = self.data_size * self.complexity * 1e6 # Example calculation
        ops_done = self.processed_data * self.complexity * 1e6
        remaining_ops = total_ops - ops_done

        if remaining_ops <= 0:
            return 0.0

        # Remaining Time = Remaining Operations / Power
        return remaining_ops / power
    ```
5.  **Implement `_update_task_state`:**
    ```python
    def _update_task_state(self, performance_metrics: Dict):
        power = performance_metrics.get('processing_power', 0)
        elapsed_time = self.env.now - self.last_update_time

        # Calculate work done in this step
        ops_done_this_step = power * elapsed_time
        data_processed_this_step = ops_done_this_step / (self.complexity * 1e6) # Reverse calculation
        self.processed_data += data_processed_this_step

        # Calculate total progress
        total_ops = self.data_size * self.complexity * 1e6
        current_total_ops_done = self.processed_data * self.complexity * 1e6

        if total_ops > 0:
             self.progress = min(1.0, current_total_ops_done / total_ops)
        else:
             self.progress = 1.0 # No work to do, instantly complete
    ```
6.  **Implement `_get_task_specific_state_repr`:**
    ```python
    def _get_task_specific_state_repr(self) -> Dict:
        # Calculate current load based on whether task is running
        load = self.current_metrics.get('processing_power', 0) if self.status == TaskStatus.RUNNING else 0
        # Only provide final result state upon completion
        result = {'final_result': 'some_value'} if self.progress >= 1.0 else None

        # Return dictionary matching PRODUCED_STATES
        return {
            'computation_load': load,
            'result_data': result,
            'status': 'computing' if self.status == TaskStatus.RUNNING else self.agent.get_state('status') # Update agent status
        }
    ```
7.  **Implement Possessing Object Hooks (Optional):**
    ```python
    def _possessing_object_on_complete(self):
        # Example: Release a compute resource lock if held
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"Time {self.env.now}: Task {self.id} completed, releasing compute lock.")
            self.agent.remove_possessing_object('my_compute_lock')

    def _possessing_object_on_fail(self):
        # Example: Release lock on failure too
        compute_resource = self.agent.get_possessing_object('my_compute_lock')
        if compute_resource:
            print(f"Time {self.env.now}: Task {self.id} failed, releasing compute lock.")
            self.agent.remove_possessing_object('my_compute_lock')
    ```

### 4. Task Priority and Preemption

AirFogSim supports task prioritization and preemption, allowing for modeling of urgent tasks that can interrupt lower-priority ones:

*   **Priority Levels:** Tasks can be assigned priority levels using the `TaskPriority` enum:
    *   `TaskPriority.CRITICAL`: Highest priority, for emergency or safety-critical tasks
    *   `TaskPriority.HIGH`: Important tasks that should be executed promptly
    *   `TaskPriority.NORMAL`: Default priority for most tasks
    *   `TaskPriority.LOW`: Background or non-urgent tasks

*   **Preemption:** Tasks can be marked as preemptive, allowing them to interrupt lower-priority tasks:
    *   Set `preemptive=True` when creating a task to allow it to interrupt other tasks
    *   When a preemptive task is executed, the Component will check if it should interrupt a currently running task
    *   Interrupted tasks are placed back in the queue and can be resumed later

*   **Example Usage:**
    ```python
    # Create a critical, preemptive charging task when battery is low
    charging_task = agent.execute_task(
        component_name="ChargingComponent",
        task_class="ChargeBatteryTask",
        task_name="Emergency Charging",
        properties={"target_level": 90.0},
        priority=TaskPriority.CRITICAL,
        preemptive=True
    )
    ```

### 5. Key Takeaways

*   Tasks represent atomic execution units in the Workflow-Agent-Task framework.
*   Tasks contain the *logic*, Components provide the *execution environment and resources*.
*   Subclasses **must** define `NECESSARY_METRICS`, `PRODUCED_STATES`, and implement `estimate_remaining_time`, `_update_task_state`, and `_get_task_specific_state_repr`.
*   The `execute` method's core loop waits for time passage *or* component metric updates.
*   Tasks update the owning Agent's state via `_get_task_specific_state_repr`.
*   Tasks can have priority and preemption properties, allowing agents to manage task execution based on importance and urgency.
*   Use the `_possessing_object_on_...` hooks to manage agent-possessed resources tied to the task lifecycle.
