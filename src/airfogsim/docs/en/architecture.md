
## AirFogSim: System Architecture Documentation

### 1. Introduction

AirFogSim is a discrete-event simulation framework built on SimPy, designed for modeling complex systems involving autonomous agents, dynamic resources, task execution, and goal-oriented workflows. This document outlines the core architectural components and their interactions, providing a high-level understanding of the system's design.

### 2. Core Philosophy

*   **Agent-Centric Autonomy:** `Agents` are the primary actors, possessing internal state and making autonomous decisions about actions (Tasks) based on their state, goals (Workflows), and environmental perception.
*   **Event-Driven Interaction:** Communication and synchronization between components primarily occur through a centralized `EventRegistry`. State changes, task completions, resource updates, and workflow progressions trigger events that other components can subscribe and react to.
*   **Separation of Concerns:** Each core component has a distinct responsibility:
    *   **Agent:** State maintenance, decision-making, task initiation.
    *   **Component:** Capability abstraction, task execution environment, resource interaction, metrics calculation.
    *   **Task:** Encapsulation of action logic, state production, metric consumption.
    *   **Workflow:** Goal representation, monitoring agent/task progress via events.
    *   **Resource Layer:** Modeling resource availability, attributes, dynamics (contention, fluctuation).
*   **Modularity and Extensibility:** The architecture is designed for extension through subclassing of core components (Agents, Components, Tasks, Workflows, Resource Pools).

### 3. Key Components and Interactions


1.  **Simulation Environment (`env`)**
    *   **Core:** Based on `simpy.Environment` for discrete-event scheduling.
    *   **Event Registry (`env.event_registry`):** Central bus for publishing and subscribing to named events across all simulation entities. Enables decoupled communication.
    *   **Resource Manager (`env.resource_manager`):** Central point for requesting and releasing resources. It delegates requests to the appropriate `SpecificResourcePool`.
    *   **Workflow Manager (`env.workflow_manager`):** Manages the lifecycle (creation, starting, tracking status) of `Workflow` instances. Listens to workflow status change events.
    *   **(Conceptual) MCP Server (`env.mcp_server`):** An optional centralized service assisting agents with complex task planning, especially involving LLMs. Gathers context, interacts with the LLM, parses results into task plans.

2.  **Agent (`airfogsim.core.Agent`)**
    *   **Role:** The autonomous decision-maker and state-maintaining entity. Represents simulated entities like drones, ground stations, users.
    *   **State:** Maintains internal state (`self.state`) defined and validated by `StateTemplate`s. State represents the Agent's current condition (position, battery, status, etc.).
    *   **Decision Logic (`live` method):** A SimPy process defining the agent's behavior loop. It perceives its state, assigned Workflows, and potentially consults the `MCPServer` or uses internal logic to decide which Tasks to execute.
    *   **Component Ownership:** Owns `Component` instances, representing its capabilities.
    *   **Task Initiation:** Calls `Component.execute_task(task)` to start actions. Manages the lifecycle of initiated tasks (`self.managed_tasks`).
    *   **Event Source/Sink:** Triggers events about its own state changes (`state_changed`) and task lifecycle (`task_started`, `task_finished`). Subscribes to events from its components, workflows, or other entities.

3.  **Component (`airfogsim.core.Component`)**
    *   **Role:** Abstracts a specific capability (e.g., mobility, computation, sensing). Provides the execution environment for Tasks. Acts as an interface between the Agent/Task and the underlying Resources.
    *   **Task Execution:** Implements `execute_task(task)`, which manages resource acquisition (via `ResourceManager`), runs the `Task.execute()` logic, handles metrics, and manages cleanup/resource release.
    *   **Resource Interaction:** Requests resources via `ResourceManager` based on Task requirements (`get_resource_requirements`). Receives `ResourceWrapper` instances.
    *   **Metrics Calculation:** **Crucially, converts Resource *attributes* (from `ResourceWrapper`) into Task *metrics* (`_calculate_performance_metrics`).** These metrics (e.g., effective speed, processing rate) quantify the capability based on current resource state and contention.
    *   **Event Source:** Triggers namespaced events on the Agent (e.g., `ComponentName.task_started`, `ComponentName.metric_changed`) signaling task progress and performance updates.

4.  **Task (`airfogsim.core.Task`)**
    *   **Role:** Encapsulates the *logic* for a specific action. Defines *how* work is done. Represents the result of Agent planning.
    *   **Execution Logic (`execute` method):** A SimPy generator run *by the Component*. Consumes performance `metrics` provided by the Component. Its core loop typically waits for time passage or metric updates.
    *   **State Production:** Updates its internal progress and calculates *Agent state changes* based on its logic and consumed metrics. Communicates these changes back to the Agent via `_get_task_specific_state_repr()`. Subclasses declare `PRODUCED_STATES`.
    *   **Metric Consumption:** Declares `NECESSARY_METRICS` required from the Component.
    *   **Proof Handling:** Optionally creates/updates a `TaskProof` object to record verifiable outcomes. Triggers proof-related events (`_trigger_workflow_events`).

5.  **Workflow (`airfogsim.core.Workflow`)**
    *   **Role:** Represents a higher-level goal or process. Primarily acts as a **monitor and coordinator** based on events.
    *   **State Machine (`WorkflowStatusMachine`):** Contains the logic for internal state transitions driven by simulation events.
    *   **Event Monitoring:** The state machine subscribes to specific events (often `Agent.state_changed`, `Agent.proof_updated`, `Component.task_completed`) defined in `_setup_transitions`. Condition functions check event details.
    *   **Passive Nature:** Workflows themselves don't *execute* tasks directly. They *monitor* the progress made by the Agent (via state changes and proofs resulting from Task execution) and transition their internal state accordingly. The current internal state might influence the Agent's *next* planning decision.
    *   **Event Source:** Triggers `status_changed` events when its internal state machine transitions, allowing `WorkflowManager` or other components to track its progress.

6.  **Resource Layer (`airfogsim.core.resource`)**
    *   **`SpecificResourcePool` (ABC):** Manages all resources of a specific type (e.g., `CpuPool`, `AirspacePool`). Handles creation/deletion, contention modeling (`_adjust_attributes_for_contention`), and fluctuation simulation (`_generate_attribute_fluctuations`). Defines request templates.
    *   **`ResourceWrapper`:** Represents a single resource instance with dynamic `attributes` (e.g., capacity, speed, latency, status). Notifies subscribers (Components) of attribute changes via callbacks.
    *   **Dynamics:** Models resource performance changes due to pool-wide usage (contention) and inherent randomness (fluctuations). Can be integrated with external data sources to drive these dynamics.
    *   **Attribute Source:** Provides the fundamental *attributes* that Components use to calculate *metrics*.

### 4. Typical Interaction Flow (Simplified)

1.  A `Workflow` is created and assigned to an `Agent` (managed by `WorkflowManager`).
2.  The `Agent`'s `live()` loop runs. It identifies the active `Workflow` goal.
3.  The Agent gathers context (its state, workflow details, component capabilities) and requests a `Task` plan (potentially via `MCPServer` -> LLM).
4.  The Agent receives a plan (list of `Task` specifications).
5.  For a Task, the Agent calls `component.execute_task(task_instance)`.
6.  The `Component` requests necessary `Resources` from `ResourceManager` -> `SpecificResourcePool`.
7.  The `Pool` creates/allocates a `ResourceWrapper` instance and returns it. Pool may update attributes based on current contention.
8.  The `Component` calculates initial `metrics` from the `ResourceWrapper`'s `attributes`.
9.  The `Component` calls `task.execute(metrics)`, running the Task logic.
10. Inside `task.execute()`:
    *   The Task waits for time or metric updates (if the `ResourceWrapper` attributes change due to pool contention/fluctuation, the wrapper notifies the Component via callback, which triggers `ComponentName.metric_changed`, waking the Task).
    *   The Task updates its progress.
    *   The Task calculates required Agent `state` changes (`_get_task_specific_state_repr`).
    *   The Task calls `agent.update_states(...)` -> Agent's `state_changed` event fires.
    *   The Task optionally updates its `TaskProof` -> triggers `proof_updated` event.
11. The `WorkflowStatusMachine` (listening for `state_changed` or `proof_updated` events with specific conditions) might transition its internal state. It triggers the `Workflow.status_changed` event.
12. `WorkflowManager` (listening to `Workflow.status_changed`) might update the overall `Workflow.status` (e.g., to COMPLETED).
13. When `task.execute()` finishes, it returns a result to the `Component`.
14. The `Component` triggers `ComponentName.task_completed/failed`, releases the `ResourceWrapper` back to the `Pool`.
15. The `Agent` receives the `agent.task_finished` event.
16. The Agent's `live()` loop continues, potentially planning the next Task based on the updated Agent state and Workflow state.

### 5. Data Flow Summary

*   **State:** Maintained by `Agent`, updated primarily by `Task` execution. Monitored by `Workflow`. Used for `Agent` decisions.
*   **Attributes:** Fundamental properties of `ResourceWrapper` instances. Managed and updated by `SpecificResourcePool` (contention, fluctuation).
*   **Metrics:** Performance characteristics derived from resource `attributes`. Calculated by `Component`, consumed by `Task`.
*   **Events:** Primary communication mechanism linking state changes, task lifecycle, resource updates, and workflow progression.
*   **Proofs:** Data artifacts generated/updated by `Task`, potentially monitored by `Workflow`.

### 6. Extensibility

New functionalities are added by creating subclasses of:
*   `Agent`: For different types of autonomous entities.
*   `Component`: For new capabilities.
*   `Task`: For new specific actions.
*   `Workflow`: For new high-level goals and monitoring logic.
*   `SpecificResourcePool`: For new types of dynamic resources.
