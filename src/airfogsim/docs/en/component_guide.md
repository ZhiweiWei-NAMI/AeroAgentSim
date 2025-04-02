
## AirFogSim Component Developer Documentation

This document provides guidance for developers looking to create custom `Component` subclasses within the AirFogSim framework. It explains the role of Components, their interaction with Agents and Tasks, resource management, and the steps required for implementation.

### 1. Overview of the `Component` Class

A `Component` represents a functional unit or capability attached to an `Agent`. Its primary responsibility is to **execute `Task` objects** assigned to it by its parent `Agent`. Components manage the lifecycle of task execution, including:

1.  **Resource Acquisition:** Requesting necessary simulation resources (like CPU, memory, bandwidth, or specialized resources like airspace) from the `ResourceManager`.
2.  **Task Logic Execution:** Running the core logic defined within the `Task` object itself (`task.execute(...)`).
3.  **Performance Modeling:** Calculating performance metrics (e.g., processing speed, energy consumption, movement speed) based on the acquired resources.
4.  **Event Emission:** Triggering events on the parent `Agent` to signal task progress (started, completed, failed, canceled) and changes in performance metrics.
5.  **Resource Cleanup:** Releasing acquired resources back to the `ResourceManager` upon task completion or failure.

Components are passive in the sense that they don't decide *what* task to run; they simply execute tasks given to them by the `Agent`.

### 2. Core Concepts

#### 2.1 Task Execution Lifecycle (`execute_task`, `_execute_task_wrapper`)

*   **Initiation:** The `Agent` calls the component's `execute_task(task)` method.
*   **Validation:** `execute_task` first checks if it `can_execute(task)` (typically by matching `task.component_name` to `self.name`). If not, it returns a SimPy process that immediately fails the task.
*   **Wrapper Process:** If valid, `execute_task` starts and returns a SimPy process managed by the `_execute_task_wrapper(task)` method. This wrapper orchestrates the entire execution flow.
*   **Wrapper Steps:** The `_execute_task_wrapper` performs the following sequence within a `try...finally` block:
    1.  Triggers a `ComponentName.task_started` event on the Agent.
    2.  Calls `self.get_resource_requirements(task)` (abstract method) to determine resource needs.
    3.  Requests these resources from `self.env.resource_manager`. This is a blocking step (`yield`).
    4.  Stores acquired `ResourceWrapper` objects.
    5.  Sets up callbacks (`_on_resource_update`) on the acquired resources.
    6.  Calls `self._calculate_performance_metrics(...)` (abstract method) with acquired resources to get initial performance values.
    7.  Triggers a `ComponentName.metric_changed` event with the initial metrics.
    8.  Executes the task's specific logic by yielding `task.execute(self.env, initial_metrics)`. The `Task` object is responsible for its own behavior using the provided metrics.
    9.  Once `task.execute` finishes, determines the final `task.status`.
    10. Triggers the appropriate final event (`ComponentName.task_completed`, `ComponentName.task_failed`, or `ComponentName.task_canceled`) on the Agent, including the result from `task.execute`.
    11. **Cleanup (in `finally`):** Removes resource callbacks, releases resources via `self.env.resource_manager.release_resources()`, and cleans up internal component state (`active_tasks`, `task_processes`, `task_resources`).
*   **Error Handling:** The wrapper catches exceptions and `simpy.Interrupt`, updates the task status accordingly (fail/cancel), triggers the corresponding event, and ensures cleanup occurs.

#### 2.2 Resource Management

*   **Requirement Specification (`get_resource_requirements`):** This **abstract method must be implemented by subclasses**. It receives the `task` object and must return a `List[Dict]`, where each dictionary describes a required resource for the `ResourceManager`. These specifications can be simple (e.g., `{'type': 'cpu', 'amount': 1}`) or use templates defined in the `ResourceManager` (as seen in `MoveToComponent` requesting `'airspace'` with a `'flight_corridor'` template and parameters).
*   **Acquisition/Release:** The `_execute_task_wrapper` handles requesting resources from and releasing them back to the central `env.resource_manager`.
*   **Resource Updates (`_on_resource_update`):** If an acquired resource's properties change dynamically (managed by the `ResourceWrapper`), the `_on_resource_update` callback is triggered. This callback typically recalculates performance metrics using `_calculate_performance_metrics` and triggers a `ComponentName.metric_changed` event, allowing the running `Task` (if it's listening) to potentially adapt its behavior.

#### 2.3 Performance Metrics

*   **`PRODUCED_METRICS` (Class Variable):** A class-level list of strings defining the names of the performance metrics this component type is expected to calculate and provide (e.g., `['speed', 'energy_consumption']`). Primarily for documentation and validation.
*   **`_calculate_performance_metrics` (Abstract Method):** This **abstract method must be implemented by subclasses**. It receives a list of the currently acquired `ResourceWrapper` objects for a task (Note: the base implementation passes a list of lists, which might need flattening or adjustment based on how resources are tracked). It must return a dictionary where keys are metric names (matching `PRODUCED_METRICS`) and values are the calculated performance values based on the resources' attributes.
*   **Usage:** The metrics dictionary returned by `_calculate_performance_metrics` is passed to `task.execute()` and emitted via the `ComponentName.metric_changed` event.

#### 2.4 Event Handling

*   **Namespaced Events:** Components trigger events on their parent `Agent`, but the event names are automatically namespaced with the component's name (e.g., `MoveTo.task_started`, `CPU.metric_changed`).
*   **Standard Events:** The base class automatically registers common events like `task_started`, `task_completed`, `task_failed`, `task_canceled`, `state_changed`, `metric_changed`.
*   **Custom Events:** Subclasses can define additional `supported_events` in their `__init__` (e.g., `MoveToComponent` adds `'position_changed'`).
*   **Triggering:** Use `self.trigger_event('event_name', value)` within the component to fire a namespaced event on the agent.

### 3. Implementing a Custom Component Subclass

Follow these steps to create your own component type (referencing `MoveToComponent`):

1.  **Define the Class:**
    *   Inherit from `airfogsim.core.component.Component`.
    ```python
    from airfogsim.core.component import Component
    from airfogsim.core import ResourceWrapper # Often needed for type hints
    from typing import List, Dict, Any

    class MyCustomComponent(Component):
        # ... implementation ...
    ```

2.  **Define `PRODUCED_METRICS`:**
    *   Declare the metrics this component calculates as a class variable.
    ```python
    class MyCustomComponent(Component):
        PRODUCED_METRICS = ['processing_rate', 'latency', 'custom_metric']
        # ...
    ```

3.  **Implement `__init__`:**
    *   Call `super().__init__(env, agent, name, supported_events)`. Provide a default name if desired. List any custom events this component might trigger in `supported_events`.
    *   Store any component-specific configuration or state (e.g., `speed_factor` in `MoveToComponent`).
    ```python
    def __init__(self, env, agent, name: Optional[str] = None, processing_factor: float = 1.0, custom_events: List[str] = None):
        supported = ['custom_event_1'] + (custom_events or [])
        super().__init__(env, agent, name or "MyCustom", supported_events=supported)
        self.processing_factor = processing_factor
        # Initialize other custom state if needed
    ```

4.  **Implement `get_resource_requirements`:**
    *   This method receives the `task` object.
    *   Analyze the `task.properties` or other task attributes to determine what resources are needed.
    *   Return a list of resource specification dictionaries compatible with `ResourceManager.request_resources`. Use templates if applicable.
    ```python
    def get_resource_requirements(self, task) -> List[Dict]:
        # Example: Read required CPU units from task properties
        required_cpu = task.properties.get('cpu_units', 1)
        required_mem = task.properties.get('memory_mb', 256)

        specs = [
            {'type': 'cpu', 'amount': required_cpu, 'request_id': f'req_cpu_{task.id}'},
            {'type': 'memory', 'amount': required_mem, 'request_id': f'req_mem_{task.id}'}
        ]
        # Maybe add network based on task type?
        if task.properties.get('needs_network', False):
            specs.append({'type': 'network', 'template': 'low_latency', 'request_id': f'req_net_{task.id}'})

        return specs
    ```

5.  **Implement `_calculate_performance_metrics`:**
    *   This method receives the list of acquired `ResourceWrapper` objects.
    *   Iterate through the resources, access their attributes (e.g., `resource.get_attribute('speed', 0)`).
    *   Calculate the performance metrics defined in `PRODUCED_METRICS`.
    *   Return the results as a dictionary.
    ```python
    def _calculate_performance_metrics(self, acquired_resources: List[ResourceWrapper]) -> Dict[str, Any]:
        total_cpu_power = 0
        min_latency = float('inf')

        # Note: Base class passes list of lists sometimes, ensure you handle it or flatten
        flat_resources = []
        for res_list in acquired_resources: # Adjust if base class changes structure
             if isinstance(res_list, list): flat_resources.extend(res_list)
             else: flat_resources.append(res_list)


        for resource in flat_resources:
            if resource.type == 'cpu':
                total_cpu_power += resource.get_attribute('power', 0) # Assuming 'power' attribute
            elif resource.type == 'network':
                min_latency = min(min_latency, resource.get_attribute('latency', float('inf')))

        processing_rate = total_cpu_power * self.processing_factor
        latency = min_latency if min_latency != float('inf') else 0

        return {
            'processing_rate': processing_rate,
            'latency': latency,
            'custom_metric': 42 # Example static metric
        }

    ```

6.  **(Optional) Add Custom Logic:** Include any helper methods needed for the component's operation.

### 4. Key Takeaways & Best Practices

*   **Focus:** Components execute tasks using resources. They don't plan.
*   **Abstract Methods:** `get_resource_requirements` and `_calculate_performance_metrics` are mandatory implementations defining the component's core behavior regarding resources and performance.
*   **Wrapper Pattern:** The `_execute_task_wrapper` handles the complex lifecycle (resource req/release, events, error handling). Subclasses primarily focus on the two abstract methods.
*   **Resource Manager:** Components rely heavily on `env.resource_manager` for resource allocation. Understand the resource types and templates available in your simulation setup.
*   **Task Interaction:** The component provides performance metrics *to* the `Task` via `task.execute()`. The `Task` object contains the actual execution logic.
*   **Events:** Use namespaced events (`self.trigger_event`) for communication back to the Agent and potentially other listeners.

By implementing these methods correctly, your custom component will integrate seamlessly into the AirFogSim agent-component-task execution model.
