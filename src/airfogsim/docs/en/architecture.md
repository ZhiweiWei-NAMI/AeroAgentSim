## AirFogSim: System Architecture Documentation

### 1. Introduction

AirFogSim is a discrete-event simulation framework built upon the SimPy library, designed for benchmarking collaborative intelligence in UAV-integrated fog computing environments. It provides a comprehensive platform for modeling complex interactions between heterogeneous aerial and terrestrial nodes, with a focus on realistic communication, computation, energy, and mobility modeling. This document outlines the core architectural components and their interactions, providing a comprehensive understanding of the system's design.

### 2. Core Philosophy

*   **High-Performance Event-Driven Simulation Core:** AirFogSim employs an optimized event-driven simulation engine, achieving sub-O(n log n) computational complexity for critical operations. This enables efficient simulation of large-scale scenarios involving hundreds of heterogeneous UAVs and thousands of concurrent tasks spanning multiple temporal scales.

*   **Workflow-Based Task Composition Framework:** The framework provides a flexible and modular workflow-driven task model, explicitly capturing task dependencies, resource constraints, and collaborative interactions among heterogeneous aerial and terrestrial nodes. This facilitates realistic modeling of complex, multi-stage UAV missions.

*   **Standards-Compliant Realistic Modeling:** AirFogSim integrates comprehensive models grounded in established standards, including 3GPP-compliant communication channel models, empirically validated energy consumption profiles, realistic computation capabilities, and physics-based mobility patterns. Additionally, a modular data provider system allows seamless integration of real-world datasets for enhanced realism.

*   **Agent-Centric Autonomy:** `Agent` instances are the primary actors. They possess internal state, own functional `Component`s, and make autonomous decisions about executing `Task`s based on their state, assigned `Workflow`s (goals), and perception of the environment through events.

*   **Event-Driven Interaction:** Communication and synchronization rely heavily on a centralized `EventRegistry`. State changes, task lifecycle events, resource updates, workflow progressions, and trigger activations are published as events, allowing decoupled components to subscribe and react.

*   **Component-Based Capabilities:** Agents utilize `Component`s to encapsulate specific functionalities (e.g., mobility, computation, sensing). Components manage the execution environment for `Task`s and interact with underlying resources.

*   **Task Encapsulation:** `Task` objects encapsulate the logic for specific actions, defining *how* work is performed, what resources are needed (implicitly via the Component), what metrics are consumed, and what agent states are produced.

*   **Workflow-Driven Goals:** `Workflow`s represent higher-level goals or processes. They utilize a `WorkflowStatusMachine` driven by `Trigger`s to monitor simulation events (agent state, task completion, time) and coordinate the overall process without directly executing tasks.

*   **Trigger-Based Reactivity:** The `Trigger` system provides a flexible mechanism for reacting to various conditions (events, state changes, time). Triggers are fundamental for driving `WorkflowStatusMachine` transitions and enabling automated responses.

*   **Managed Resources:** Simulation resources (e.g., landing spots, CPU, airspace, spectrum) are managed by dedicated `Manager` classes, handling registration, allocation, contention, and dynamic attribute changes.

*   **Modularity and Extensibility:** The architecture promotes extension through subclassing core entities (`Agent`, `Component`, `Task`, `Workflow`, `Trigger`, `Resource`, and specific `Manager`s).

### 3. Key Components and Interactions

#### 3.1 Simulation Environment (`airfogsim.core.environment.Environment`)

*   **Core:** Extends `simpy.Environment` for discrete-event scheduling, providing the foundation for the simulation's temporal progression.
*   **Central Hub:** Acts as a central registry and access point for core managers and services, facilitating coordinated access to simulation resources and services.
*   **Key Managers (typically attributes of `env`):**
    *   **`EventRegistry` (`airfogsim.core.event.EventRegistry`):** Central bus for publishing and subscribing to named events across all simulation entities. Enables decoupled communication through a publish-subscribe pattern, supporting wildcards for both source IDs and event names.
    *   **`TaskManager` (`airfogsim.manager.task_manager.TaskManager`):** Manages the creation and tracking of `Task` instances. Provides a central point to access task information, including task status, ownership, and execution metrics.
    *   **`WorkflowManager` (`airfogsim.manager.workflow.WorkflowManager`):** Manages the lifecycle (creation, starting, tracking status) of `Workflow` instances. Listens to workflow status change events and provides methods for workflow creation, starting, and status tracking.
    *   **`TriggerManager` (`airfogsim.manager.trigger.TriggerManager`):** Manages the lifecycle and lookup of `Trigger` instances, providing centralized activation, deactivation, and callback management.
    *   **Resource Managers:**
        *   **`LandingManager` (`airfogsim.manager.landing.LandingManager`):** Manages landing spots and charging stations, handling resource allocation for landing, takeoff, and charging operations.
        *   **`AirspaceManager` (`airfogsim.manager.airspace.AirspaceManager`):** Manages spatial queries and airspace resource allocation/deconfliction using an octree-based spatial index for efficient collision detection and proximity queries.
        *   **`FrequencyManager` (`airfogsim.manager.frequency.FrequencyManager`):** Manages spectrum resources with 3GPP-compliant channel models, handling frequency allocation, interference modeling, and signal quality calculations.
    *   **`ContractManager` (`airfogsim.manager.contract.ContractManager`):** Manages task offloading contracts between agents, facilitating negotiation, agreement, and execution of collaborative tasks.
    *   **Data Providers:**
        *   **`WeatherIntegration` (`airfogsim.dataprovider.weather_integration.WeatherIntegration`):** Provides real-time or simulated weather data, affecting flight conditions, energy consumption, and communication quality.
        *   **`TrafficIntegration` (`airfogsim.dataprovider.traffic_integration.TrafficIntegration`):** Integrates with traffic simulation tools like SUMO to provide realistic ground traffic patterns for UAV-vehicular coordination scenarios.
        *   **`SensorDataProvider` (`airfogsim.dataprovider.sensor.SensorDataProvider`):** Provides simulated sensor data for environmental monitoring, surveillance, and perception tasks.

#### 3.2 Agent (`airfogsim.core.agent.Agent`)

*   **Role:** The autonomous decision-maker and state-maintaining entity. Represents simulated actors like drones, ground stations, etc.
*   **Source Code:** `airfogsim/core/agent.py`
*   **Key Features:**
    *   **State Management:** Maintains internal state (`self.state`) defined and validated by `StateTemplate`s using the `AgentMeta` metaclass. Tracks properties like position, status, battery level. Can also access state of possessed objects via composite keys.
    *   **Decision Logic (`live` method):** A required SimPy process defining the agent's behavior loop. It perceives its state, assigned `Workflow`s (checking `workflow.get_current_suggested_task()`), incoming events, and decides which `Task`s to execute. Can integrate with LLMs for planning.
    *   **Component Ownership:** Owns `Component` instances (`self.add_component()`), representing its capabilities (e.g., mobility, sensing).
    *   **Task Initiation:** Initiates tasks by calling `self.execute_task(...)`, which delegates execution to the appropriate `Component`. Monitors initiated tasks (`self.managed_tasks`).
    *   **Event Handling:** Triggers events about its own state changes (`state_changed`), task lifecycle (`task_started`, `task_completed`), and possessed objects. Subscribes to events from components, workflows, triggers, or other entities via `EventRegistry`.
    *   **Possessing Objects:** Can hold references to other simulation entities (e.g., a specific `LandingResource`) using `add_possessing_object`, allowing interaction and state monitoring.
*   **Subclass Examples:** `airfogsim.agent.drone.DroneAgent`, `airfogsim.agent.delivery_drone.DeliveryDroneAgent`, `airfogsim.agent.delivery_station.DeliveryStationAgent`.

#### 3.3 Component (`airfogsim.core.component.Component`)

*   **Role:** Abstracts a specific capability (e.g., mobility, computation, charging). Provides the execution environment for `Task`s and acts as an interface between the Agent/Task and underlying Resources/Managers.
*   **Source Code:** `airfogsim/core/component.py`
*   **Key Features:**
    *   **Task Execution (`execute_task`, `_execute_task_wrapper`):** Manages the lifecycle of a task execution request from the Agent. Validates if it can execute the task, orchestrates resource acquisition, runs the task logic, handles metrics, and manages cleanup.
    *   **Resource Interaction:** Implements `get_resource_requirements(task)` (abstract) to define resource needs. Requests resources via the appropriate `Manager` (e.g., `env.landing_manager`). Handles resource updates via callbacks (`_on_resource_update`).
    *   **Metrics Calculation (`_calculate_performance_metrics` - abstract):** Calculates performance metrics (e.g., speed, processing rate, energy consumption) based on the attributes of acquired resources. Declares `PRODUCED_METRICS`.
    *   **Event Emission:** Triggers namespaced events on the parent `Agent` (e.g., `ComponentName.task_started`, `ComponentName.metric_changed`, `ComponentName.task_completed`) via `self.trigger_event()`.
*   **Subclass Examples:** `airfogsim.component.mobility.MobilityComponent`, `airfogsim.component.computation.ComputeComponent`, `airfogsim.component.charging.ChargingComponent`, `airfogsim.component.sensing.SensingComponent`.

#### 3.4 Task (`airfogsim.core.task.Task`)

*   **Role:** Encapsulates the *logic* for a specific action. Defines *how* work is done based on provided metrics. Represents the atomic execution units in the simulation.
*   **Source Code:** `airfogsim/core/task.py`
*   **Key Features:**
    *   **Execution Logic (`execute` method):** A SimPy generator run *by the Component*. Consumes performance `metrics` provided by the Component. Its core loop typically waits for time passage or metric updates (`ComponentName.metric_changed` event).
    *   **Metric Consumption:** Declares `NECESSARY_METRICS` required from the executing Component, ensuring that tasks only execute when all required resources and capabilities are available.
    *   **State Production:** Implements `_update_task_state(metrics)` (abstract) to update internal progress. Implements `_get_task_specific_state_repr()` (abstract) to calculate *Agent* state changes based on its logic and progress. Declares `PRODUCED_STATES`. Updates Agent state via `self.agent.update_states()`.
    *   **Lifecycle:** Manages its own status (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`), with support for priority and preemption properties allowing agents to manage task execution based on importance and urgency.
    *   **Possessing Object Hooks:** Provides `_possessing_object_on_complete/fail/cancel` methods for subclasses to manage agent-possessed objects tied to the task lifecycle.
    *   **Priority and Preemption:** Tasks can have priority levels (e.g., `TaskPriority.CRITICAL`, `TaskPriority.HIGH`) and preemption flags, allowing the system to model urgent tasks that can interrupt lower-priority ones.
*   **Subclass Examples:** `airfogsim.task.mobility.MoveToTask`, `airfogsim.task.compute.ComputeTask`, `airfogsim.task.charging.ChargeBatteryTask`, `airfogsim.task.logistics.LoadCargoTask`, `airfogsim.task.sensing.SensingTask`.

#### 3.5 Workflow-Agent-Task Framework

*   **Role:** At the heart of AirFogSim is the workflow-agent-task framework, which enables the modeling of complex mission scenarios through the composition of reusable components.
*   **Workflow (`airfogsim.core.workflow.Workflow`):**
    *   **Role:** Represents a higher-level goal or process. Primarily acts as a **monitor and coordinator** based on simulation events, driven by its internal `WorkflowStatusMachine`.
    *   **Source Code:** `airfogsim/core/workflow.py`
    *   **Key Features:**
        *   **Overall Status:** Manages high-level status (`WorkflowStatus` enum).
        *   **Properties:** Defines expected parameters using `WorkflowPropertyTemplate` (managed by `WorkflowMeta`).
        *   **State Machine (`self.status_machine`):** Contains the `WorkflowStatusMachine` instance that manages the workflow's progression through various stages.
        *   **Transitions Setup (`_setup_transitions` - abstract):** Subclasses MUST implement this to define the state machine logic using `self.status_machine.add_transition()`.
        *   **Starting/Resetting:** `start()` initiates the workflow and state machine; `reset()` allows restarting.
        *   **Context/Guidance:** `get_details()` provides workflow context; `get_current_suggested_task()` suggests the next likely task for the agent based on the current internal state.
*   **WorkflowStatusMachine:**
    *   **Internal States:** Tracks `current_status` (string), representing the current stage of the workflow.
    *   **Event-Driven Transitions:** Uses `add_transition` to define rules based on `Trigger` activations (listening for agent state, events, or time).
    *   **Trigger Management:** Activates/deactivates relevant triggers based on the current state, ensuring that only relevant conditions are monitored.
    *   **Notification:** Notifies the parent `Workflow` upon internal state changes (`workflow._trigger_status_changed`).
*   **Key Concept:** State transitions are not predetermined sequences but are dynamically driven by simulation Triggers. These triggers actively monitor the simulation environment for specific conditions to be met before allowing a transition from one state to the next.
*   **Subclass Examples:** `airfogsim.workflow.inspection.InspectionWorkflow`, `airfogsim.workflow.charging.ChargingWorkflow`, `airfogsim.workflow.contract.ContractWorkflow`, `airfogsim.workflow.image_processing.ImageProcessingWorkflow`, `airfogsim.workflow.order_execution.OrderExecutionWorkflow`.

#### 3.6 Trigger (`airfogsim.core.trigger.Trigger`)

*   **Role:** Monitors specific simulation conditions and executes callbacks when met. Drives `WorkflowStatusMachine` transitions.
*   **Source Code:** `airfogsim/core/trigger.py`
*   **Key Features:**
    *   **Condition Monitoring:** Checks for event occurrences, agent state changes, or time passage.
    *   **Activation/Deactivation:** Must be activated (`activate()`) to monitor; can be stopped (`deactivate()`). Often managed by the `WorkflowStatusMachine`.
    *   **Callbacks:** Executes registered functions (`add_callback()`) upon firing, passing context.
*   **Subclass Types:**
    *   `EventTrigger`: Reacts to `EventRegistry` events based on source, name, and value conditions.
    *   `StateTrigger`: Reacts to `Agent.state_changed` events based on agent ID, state key, and value conditions.
    *   `TimeTrigger`: Fires based on absolute time, intervals, or cron-like schedules (currently simplified to intervals).
    *   `CompositeTrigger`: Combines multiple triggers using logical AND or OR.

#### 3.7 Resource Management

*   **Role:** Resources in AirFogSim represent limited shared assets that components require to produce metrics. These include physical resources like airspace, landing spots, and spectrum.
*   **Resource Framework:**
    *   **`Resource` Base Class (`airfogsim.core.resource.Resource`):** Serves as the basic representation for any individual resource instance. It maintains a unique ID, a dictionary of specific attributes, an operational status (e.g., `ResourceStatus.AVAILABLE`, `ResourceStatus.FULLY_ALLOCATED`), and tracks its current allocations.
    *   **Subclass Examples:** `airfogsim.resource.landing.LandingResource`, `airfogsim.resource.frequency.FrequencyResource`, `airfogsim.resource.computation.ComputationResource`.
*   **Resource Management:**
    *   **`ResourceManager<R>` Base Class (`airfogsim.core.resource.ResourceManager`):** The core of the framework is the generic `ResourceManager<R>` base class, designed to manage a collection of resources of a specific type R. Its primary responsibilities include resource lifecycle management, allocation management, and state tracking.
    *   **Specific Managers:**
        *   **`LandingManager` (`airfogsim.manager.landing.LandingManager`):** Manages landing spots and charging stations, handling resource allocation for landing, takeoff, and charging operations.
        *   **`FrequencyManager` (`airfogsim.manager.frequency.FrequencyManager`):** Manages spectrum resources with 3GPP-compliant channel models, handling frequency allocation, interference modeling, and signal quality calculations.
        *   **`ComputationManager` (`airfogsim.manager.computation.ComputationManager`):** Manages computational resources, handling task allocation, processing power distribution, and computational load balancing.
    *   **Resource Allocation Process:**
        1. Components identify resource requirements for tasks via `get_resource_requirements(task)`
        2. Components request resources from the appropriate manager (e.g., `env.landing_manager.request_resource(...)`)
        3. Managers find suitable resources based on requirements and current availability
        4. Managers allocate resources and notify components
        5. Components calculate performance metrics based on allocated resources
        6. Upon task completion, components release resources back to managers
    *   **Contention Handling:** Resource managers implement strategies for handling resource contention, including queuing, priority-based allocation, and preemption mechanisms.

### 4. Typical Interaction Flow (Simplified)

1.  A `Workflow` is created and assigned to an `Agent` (managed by `WorkflowManager`). `WorkflowManager.start_workflow()` is called.
2.  The `Workflow` status changes to `RUNNING`. It starts its `WorkflowStatusMachine`.
3.  The `WorkflowStatusMachine` enters its initial state and activates the relevant `Trigger`s for that state (e.g., a `StateTrigger` monitoring `Agent.status`, an `EventTrigger` listening for `task_completed`).
4.  The `Agent`'s `live()` loop runs. It checks its active `Workflow`s, potentially calling `workflow.get_current_suggested_task()` to get guidance.
5.  Based on its internal logic and workflow guidance, the `Agent` decides to execute a `Task`. It calls `agent.execute_task(component_name, task_class, properties, ...)`.
6.  The `Agent` finds the specified `Component`.
7.  The `Agent` uses `TaskManager` to create the `Task` instance.
8.  The `Agent` calls `component.execute_task(task_instance)`.
9.  The `Component`'s `_execute_task_wrapper` starts:
    a.  Triggers `ComponentName.task_started` event.
    b.  Calls `component.get_resource_requirements(task)`.
    c.  Requests resources from the relevant `Manager` (e.g., `env.landing_manager.request_resource(...)`). This might involve waiting if resources are unavailable.
    d.  Once resources are acquired, calculates initial `metrics` using `component._calculate_performance_metrics(resources)`.
    e.  Triggers `ComponentName.metric_changed` event.
    f.  Calls and yields `task.execute(initial_metrics)`.
10. Inside `task.execute()`:
    a.  Task enters its main loop, waiting for time passage or `ComponentName.metric_changed` events.
    b.  If metrics change, the task updates its internal state.
    c.  Calls `task._update_task_state(metrics)` to update progress.
    d.  Calls `task._get_task_specific_state_repr()` to get agent state updates.
    e.  Calls `agent.update_states(...)` -> `Agent.state_changed` event fires.
11. A `Trigger` activated by the `WorkflowStatusMachine` might fire:
    a.  **Example 1:** A `StateTrigger` listening for the `Agent.state_changed` event (from step 10e) detects a condition matching its criteria.
    b.  **Example 2:** An `EventTrigger` listening for `ComponentName.task_completed` fires when the task finishes.
12. The activated `Trigger` calls its callback, which is `WorkflowStatusMachine._on_trigger_activated`.
13. `_on_trigger_activated` calls `_change_status`, updating the machine's `current_status`.
14. `_change_status` calls `workflow._trigger_status_changed`.
15. `workflow._trigger_status_changed` updates the `Workflow`'s overall status if needed (e.g., maps internal 'completed' to `WorkflowStatus.COMPLETED`) and triggers `sm_status_changed` and potentially `workflow_status_changed` events.
16. The `WorkflowStatusMachine`'s monitor process is interrupted, restarts, deactivates old triggers, and activates triggers for the *new* internal state.
17. When `task.execute()` finishes, it returns a result to the `Component`.
18. The `Component` triggers `ComponentName.task_completed/failed`, releases resources back to the `Manager`, and cleans up.
19. The `Agent` receives the `agent.task_completed` event (if subscribed).
20. The Agent's `live()` loop continues, observing the updated Agent state and potentially new `Workflow` state/suggestions to plan the next action.

### 5. Data Flow Summary

*   **State:** Maintained by `Agent`, updated primarily by `Task` execution. Monitored by `StateTrigger`s (used by `Workflow`). Used for `Agent` decisions.
*   **Attributes:** Properties of `Resource` instances. Managed and updated by specific `Manager`s (modeling contention, fluctuation).
*   **Metrics:** Performance characteristics derived from resource `attributes`. Calculated by `Component`, consumed by `Task`.
*   **Events:** Primary communication mechanism linking state changes, task lifecycle, resource updates, trigger activations, and workflow progression. Managed by `EventRegistry`.
*   **Triggers:** Monitor Events and State to drive `WorkflowStatusMachine` transitions.
*   **Workflow Properties:** Configuration and goal parameters for a `Workflow`.
*   **Task Properties:** Configuration parameters for a specific `Task` instance.

### 6. Extensibility

New functionalities are typically added by creating subclasses of:

*   `Agent`: For different types of autonomous entities.
*   `Component`: For new capabilities.
*   `Task`: For new specific actions.
*   `Workflow`: For new high-level goals and state machine logic.
*   `Trigger`: For custom condition monitoring (less common).
*   `Resource`: For new types of simulation resources.
*   Specific `Manager`: For managing new resource types or providing new central services.
