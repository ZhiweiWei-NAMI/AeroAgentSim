## AeroAgentSim Component Developer Documentation

This document provides guidance for developers looking to create custom `Component` subclasses within the AeroAgentSim framework. It explains the role of Components, their interaction with Agents and Tasks, performance metric calculation based on Agent state, and the steps required for implementation.

### 1. Overview of the `Component` Class

A `Component` represents a functional unit or capability attached to an `Agent`. Its primary responsibility is to **execute `Task` objects** assigned to it by its parent `Agent`. Components manage the lifecycle of task execution, including:

1.  **Task Logic Execution:** Running the core logic defined within the `Task` object itself (`task.execute(...)`).
2.  **Performance Modeling:** Calculating performance metrics (e.g., processing speed, energy consumption, movement speed) based on the **parent Agent's current state** and component properties.
3.  **Agent State Monitoring:** Reacting to changes in relevant Agent states (`MONITORED_STATES`) by recalculating performance metrics.
4.  **Event Emission:** Triggering namespaced events on the parent `Agent` to signal task progress (started, completed, failed, canceled) and changes in performance metrics (`metric_changed`).
5.  **Task Lifecycle Management:** Orchestrating the setup, execution, and cleanup phases of a task.

Components are passive in the sense that they don't decide *what* task to run; they simply execute tasks given to them by the `Agent`, adapting their performance based on the Agent's state. Resource acquisition and management are **not** directly handled by the base `Component` wrapper; this logic should be implemented within specific `Component` subclasses or potentially within the `Task` logic itself, often influenced by the Agent's state or possessed objects.

### 2. Core Concepts

#### 2.1 Task Execution Lifecycle (`execute_task`, `_execute_task_wrapper`)

*   **Initiation:** The `Agent` calls the component's `execute_task(task)` method.
*   **Validation:** `execute_task` first checks if it `can_execute(task)` (typically by matching `task.component_name` to `self.name`). If not, it returns a SimPy process that immediately fails the task. It also checks if the task is already running.
*   **Wrapper Process:** If valid, `execute_task` starts and returns a SimPy process managed by the `_execute_task_wrapper(task)` method. This wrapper orchestrates the entire execution flow.
*   **Wrapper Steps:** The `_execute_task_wrapper` performs the following sequence within a `try...finally` block:
    1.  Triggers a `ComponentName.task_started` event on the Agent.
    2.  Calls `self._calculate_performance_metrics()` (abstract method) to get initial performance values based on the current Agent state. Validates these metrics against `PRODUCED_METRICS`. Updates `self.current_metrics`.
    3.  Triggers a `ComponentName.metric_changed` event with the initial metrics.
    4.  **Registers an Agent state listener:** Subscribes to the parent Agent's `state_changed` event using `self.env.event_registry.subscribe`, linking it to the `_on_agent_state_changed` callback.
    5.  Executes the task's specific logic by yielding `task.execute(self.env, initial_metrics)`. The `Task` object uses these metrics to perform its work.
    6.  Once `task.execute` finishes, determines the final `task.status`.
    7.  Triggers the appropriate final event (`ComponentName.task_completed`, `ComponentName.task_failed`, or `ComponentName.task_canceled`) on the Agent, including the result from `task.execute`.
    8.  **Cleanup (in `finally`):**
        *   Unsubscribes the Agent state listener using `self.env.event_registry.unsubscribe`.
        *   Removes the task from the component's internal tracking (`active_tasks`, `task_processes`).
*   **Error Handling:** The wrapper catches `simpy.Interrupt` (cancels the task and triggers `task_canceled`) and other `Exception`s (fails the task and triggers `task_failed`), ensuring cleanup occurs.

#### 2.2 Performance Metrics (`PRODUCED_METRICS`, `_calculate_performance_metrics`, `current_metrics`)

*   **`PRODUCED_METRICS` (Class Variable):** A **mandatory** class-level list of strings defining the names of the performance metrics this component type calculates and provides to the `Task` (e.g., `['speed', 'energy_consumption']`). Used for validation and initializing `current_metrics`.
*   **`_calculate_performance_metrics()` (Abstract Method):** This **abstract method must be implemented by subclasses**.
    *   **Input:** Implicitly uses `self.agent.get_state()` or specific agent states defined in `MONITORED_STATES`, and potentially component `self.properties`.
    *   **Action:** Calculates the current performance metrics based on the relevant Agent state(s) and component configuration.
    *   **Output:** Returns a dictionary where keys are metric names (must be present in `PRODUCED_METRICS`) and values are the calculated performance values.
*   **`current_metrics` (Instance Variable):** A dictionary holding the last calculated values for the metrics defined in `PRODUCED_METRICS`.
*   **`metric_changed` Event:** Triggered by `_execute_task_wrapper` initially and by `_on_agent_state_changed` whenever calculated metrics change, providing the updated metrics dictionary to the running `Task` and other listeners.
*   **`_validate_metrics(metrics)`:** Internal helper to ensure calculated metrics match `PRODUCED_METRICS`.

#### 2.3 Agent State Monitoring (`MONITORED_STATES`, `_on_agent_state_changed`)

*   **`MONITORED_STATES` (Class Variable):** An **optional** class-level list of Agent state keys (strings) that this component specifically cares about. If a state listed here changes, the component will recalculate its metrics. If empty or not defined, the component might react to *any* agent state change (depending on the implementation of `_on_agent_state_changed`). The base implementation checks if the changed state `key` is in `MONITORED_STATES` (if `MONITORED_STATES` is not empty).
*   **`_validate_agent_states()`:** Called during `__init__` to ensure that states listed in `MONITORED_STATES` (excluding composite keys like 'object.state') are actually defined in the agent's state templates.
*   **`_on_agent_state_changed(event_data)`:** The callback function triggered when the parent Agent's `state_changed` event occurs.
    *   Checks if the changed state key (`event_data['key']`) is relevant (i.e., in `MONITORED_STATES` if the list is defined and non-empty).
    *   If relevant, it calls `_calculate_performance_metrics()` to get updated metrics.
    *   It compares the new metrics to `self.current_metrics`. **If any metric value has changed**, it updates `self.current_metrics` and triggers the `ComponentName.metric_changed` event with the new metrics dictionary. This allows the running `Task` to adapt its behavior based on the Agent's updated state.

#### 2.4 Event Handling (`_register_component_events`, `trigger_event`)

*   **Namespaced Events:** Components trigger events on their parent `Agent`, but the event names are automatically namespaced with the component's name (e.g., `MobilityComponent.task_started`, `ComputeComponent.metric_changed`). This is handled by `trigger_event`.
*   **Standard Events:** The base class automatically registers common events via `_register_component_events`: `task_started`, `task_completed`, `task_failed`, `task_canceled`, `state_changed` (often triggered *by* the Task, but the component *can* trigger it), `metric_changed`.
*   **Custom Events:** Subclasses can define additional `supported_events` in their `__init__` and trigger them using `self.trigger_event('my_custom_event', value)`.
*   **Triggering:** Use `self.trigger_event('event_name', value)` within the component to fire a namespaced event on the agent.

### 3. Implementing a Custom Component Subclass

Follow these steps to create your own component type:

1.  **Define the Class:**
    *   Inherit from `aeroagentsim.core.component.Component`.
    ```python
    from aeroagentsim.core.component import Component
    from typing import List, Dict, Any, Optional

    class MyCustomComponent(Component):
        # ... implementation ...
    ```

2.  **Define `PRODUCED_METRICS` (Mandatory):**
    *   Declare the metrics this component calculates as a class variable.
    ```python
    class MyCustomComponent(Component):
        PRODUCED_METRICS = ['processing_rate', 'latency']
        # ...
    ```

3.  **Define `MONITORED_STATES` (Optional but Recommended):**
    *   Declare the Agent states that influence this component's metrics.
    ```python
    class MyComputeComponent(Component):
        PRODUCED_METRICS = ['processing_power']
        MONITORED_STATES = ['cpu_load', 'power_mode'] # React only to these state changes
        # ...
    ```

4.  **Implement `__init__`:**
    *   Call `super().__init__(env, agent, name, supported_events, properties)`. Provide a default name if desired. List any custom events.
    *   Store any component-specific configuration from `properties` or arguments.
    *   Initialize any internal state for the component.
    ```python
    def __init__(self, env, agent, name: Optional[str] = None, processing_factor: float = 1.0, custom_events: List[str] = None, properties=None):
        supported = ['custom_event_1'] + (custom_events or [])
        super().__init__(env, agent, name or "MyCustom", supported_events=supported, properties=properties)
        self.processing_factor = processing_factor
        # Initialize other custom state if needed
    ```

5.  **Implement `_calculate_performance_metrics` (Mandatory):**
    *   Access relevant Agent state(s) using `self.agent.get_state('state_key', default_value)`.
    *   Use component properties (`self.properties`, `self.processing_factor`, etc.).
    *   Calculate the performance metrics defined in `PRODUCED_METRICS`.
    *   Return the results as a dictionary.
    ```python
    def _calculate_performance_metrics(self) -> Dict[str, Any]:
        # Example: Calculate based on agent's power mode and component factor
        power_mode = self.agent.get_state('power_mode', 'normal')
        base_rate = self.properties.get('base_processing_rate', 100)

        if power_mode == 'low':
            rate = base_rate * 0.5
            latency = 20
        elif power_mode == 'high':
            rate = base_rate * 1.5
            latency = 5
        else: # normal
            rate = base_rate
            latency = 10

        processing_rate = rate * self.processing_factor

        # Return dictionary matching PRODUCED_METRICS
        return {
            'processing_rate': processing_rate,
            'latency': latency
        }
    ```

6.  **(Optional) Override `can_execute` or `is_available`:** Add custom logic if the default behavior (match name, check active tasks) is insufficient.

7.  **(Optional) Add Custom Logic/Events:** Include helper methods or trigger custom events as needed for the component's specific function.

### 4. Key Takeaways & Best Practices

*   **Focus:** Components execute tasks, adapting their performance based on the parent Agent's state. They act as the capability interface for the Agent.
*   **Abstract Method:** `_calculate_performance_metrics` is the **mandatory** implementation defining how the component's performance is derived from the Agent's state and component configuration.
*   **State-Driven Metrics:** Performance metrics are primarily driven by the Agent's state (`MONITORED_STATES`) and component properties, not directly by acquired resources in the base class.
*   **Wrapper Pattern:** The `_execute_task_wrapper` handles the complex lifecycle (events, state listening, task execution, error handling, cleanup). Subclasses primarily focus on `_calculate_performance_metrics`.
*   **Task Interaction:** The component provides performance metrics *to* the `Task` via `task.execute()` and the `metric_changed` event. The `Task` object contains the actual execution logic that consumes these metrics.
*   **Events:** Use namespaced events (`self.trigger_event`) for communication back to the Agent and potentially other listeners (like the running Task).
*   **Resource Management:** Explicit resource acquisition/release logic (e.g., interacting with Managers) is **not** part of the base `Component` wrapper and needs to be implemented in subclasses or handled within the `Task` logic, often triggered by Agent state or possessed objects.

By implementing these methods correctly, your custom component will integrate seamlessly into the AeroAgentSim agent-component-task execution model, reacting dynamically to the state of its parent Agent.
