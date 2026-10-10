## AeroAgentSim Workflow-Agent-Task Framework Documentation

This document explains the Workflow-Agent-Task framework in AeroAgentSim, focusing on the `Workflow` and `WorkflowStatusMachine` classes and how they enable modeling complex mission scenarios through the composition of reusable components.

### 1. Overview

At the heart of AeroAgentSim is the workflow-agent-task framework, which enables the modeling of complex mission scenarios through the composition of reusable components. While Tasks represent atomic execution units, Workflows define higher-level processes, objectives, or sequences of operations.

*   **`Workflow`:** The main object representing a higher-level goal or process. It holds overall status (Pending, Running, Completed, etc.), references the owner `Agent`, and contains workflow-specific properties or goals defined via `WorkflowPropertyTemplate`. It defines *what* the goal is and manages its overall lifecycle.

*   **`WorkflowStatusMachine`:** Each Workflow instance encapsulates a dedicated state machine, represented by the WorkflowStatusMachine class. This state machine manages the workflow's progression through various stages (e.g., 'idle', 'picking_up', 'transporting'). It listens for specific events (using various `Trigger` types) and transitions between internal states based on predefined rules. It defines *how* the workflow progresses internally based on events.

*   **`Agent`:** Autonomous decision-making entities that own components, execute tasks, and participate in workflows. Agents maintain internal state and make decisions based on their state, assigned workflows, and environmental perception.

*   **`Task`:** Atomic execution units that encapsulate the logic for specific actions. Tasks define how work is performed, what resources are needed, what metrics are consumed, and what agent states are produced.

**Key Concept:** A critical aspect of this model is that state transitions are not predetermined sequences but are dynamically driven by simulation Triggers. These triggers actively monitor the simulation environment for specific conditions to be met before allowing a transition from one state to the next.

**Relationship:** A `Workflow` object *has a* `WorkflowStatusMachine`. The `Workflow`'s overall status (e.g., `WorkflowStatus.RUNNING`) is managed by the `Workflow` itself (often triggered by the state machine), while the `WorkflowStatusMachine` manages the detailed internal progression by reacting to fine-grained simulation events.

### 2. `WorkflowStatusMachine` Class

This class implements the event-driven state transition logic for a workflow.

#### 2.1 Core Concepts

*   **States:** The machine tracks its `current_status` (a string representing the internal state, e.g., `'waiting_for_drone'`, `'processing_data'`).
*   **Transitions (`add_transition`):** The core logic is defined by adding transitions. A transition specifies:
    *   `state`: The state(s) from which this transition is valid (string or list/tuple of strings). `"*"` acts as a wildcard, valid from any state.
    *   `next_status`: The internal state the machine should transition *to* if the trigger activates.
    *   **Trigger Specification:** One of the following must be provided to define *when* the transition occurs:
        *   `agent_state` (Dict): Configuration for a `StateTrigger` (e.g., `{'agent_id': ..., 'state_key': ..., 'operator': ..., 'target_value': ...}`).
        *   `time_trigger` (Dict): Configuration for a `TimeTrigger` (e.g., `{'interval': ..., 'trigger_time': ...}`).
        *   `event_trigger` (Dict): Configuration for an `EventTrigger` (e.g., `{'source_id': ..., 'event_name': ..., 'value_key': ..., 'operator': ..., 'target_value': ...}`).
        *   `trigger` (Trigger): A pre-configured `Trigger` instance (highest priority).
    *   `callback` (Optional[Callable]): A function to execute when the trigger activates *before* the state transition. The callback receives the trigger context (`event_data`).
    *   `description` (Optional[str]): A human-readable description of the transition.
*   **Event Monitoring (`_monitor_events`):** When the machine is active (`start()` is called), this SimPy process runs:
    1.  Determines the valid transitions from the `current_status` using `_get_current_transitions`.
    2.  **Deactivates** all previously active triggers (`_deactivate_all_triggers`).
    3.  **Activates** the triggers associated with the valid transitions for the *current* state. Each trigger's callback is set to `_on_trigger_activated`.
    4.  **Waits (`yield self.env.timeout(float('inf'))`)** indefinitely until interrupted.
    5.  The process is interrupted when a trigger's callback (`_on_trigger_activated`) successfully changes the state.
*   **State Change (`_on_trigger_activated`, `_change_status`):**
    *   `_on_trigger_activated` is called when an active trigger fires. It checks if the machine is still in a valid state.
    *   If valid, it calls `_change_status`, passing the `next_status` and details about the triggering event.
    *   `_change_status` updates the machine's internal `current_status`.
    *   **Crucially, `_change_status` notifies the parent `Workflow` object by calling `self.workflow._trigger_status_changed(...)`.** This allows the `Workflow` (and listening managers) to react to the internal state progression.
    *   If the state changes successfully, `_on_trigger_activated` interrupts the `_monitor_events` process to handle the new state.
*   **Start State (`set_start_transition`):** Defines the initial internal state the machine enters *after* the `Workflow`'s overall status becomes `RUNNING`.
*   **Terminal States:** States like `'completed'`, `'failed'`, `'canceled'` stop the monitoring process.

#### 2.2 Usage

Developers typically interact with the `WorkflowStatusMachine` *indirectly* through the `Workflow` subclass, primarily within the `_setup_transitions` method by calling `self.status_machine.add_transition(...)`.

### 3. `Workflow` Base Class

This class represents the overall workflow goal and orchestrates the `WorkflowStatusMachine`. It uses the `WorkflowMeta` metaclass for handling property template inheritance.

#### 3.1 Core Concepts

*   **Metaclass (`WorkflowMeta`):** Handles the aggregation of `WorkflowPropertyTemplate` definitions across the class hierarchy. Allows registering templates using `mcs.register_template` within the metaclass `__new__` method.
*   **Property Templates (`WorkflowPropertyTemplate`, `register_property_template`, `get_property_templates`):** Define the expected structure, types, and validation rules for the `properties` dictionary passed during workflow initialization. Ensures consistency and provides documentation. Use the `@classmethod register_property_template` decorator or `WorkflowMeta.register_template` for definition.
*   **Overall Status (`self.status`):** Tracks the high-level status using the `WorkflowStatus` enum (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`).
*   **State Machine (`self.status_machine`):** Holds the instance of `WorkflowStatusMachine` that drives internal progress.
*   **Initialization (`__init__`):** Sets up ID, name, owner, optional timeout. Validates provided `properties` against registered templates (`_validate_properties`). Creates the `WorkflowStatusMachine` with the specified `initial_status` (default is 'idle'). Calls the abstract `_setup_transitions`. Automatically registers workflow-level events ('sm_status_changed', 'workflow_status_changed'). Also automatically registers property templates for 'task_priority' and 'task_preemptive'.
*   **Transitions Setup (`_setup_transitions`) - Abstract Method:**
    *   **This is the primary method subclasses MUST implement.**
    *   Inside this method, you define the workflow's logic by adding transitions to `self.status_machine`.
    *   Transitions typically use triggers based on the `self.owner` Agent's state (`agent_state`), simulation time (`time_trigger`), or specific events (`event_trigger`).
*   **Starting (`start`):**
    *   Called externally (usually by `WorkflowManager`) when the workflow should begin execution.
    *   Changes the `Workflow.status` from `PENDING` to `RUNNING`.
    *   Triggers the `workflow_status_changed` event.
    *   Calls `self.status_machine.start()` to activate the event monitoring process.
*   **Signaling Internal Changes (`_trigger_status_changed`):**
    *   An **internal** method called *by the `WorkflowStatusMachine`* when *its* internal state changes.
    *   This method first updates the workflow's overall status (`self.status`) based on the state machine's new state using `update_status_from_state_machine`.
    *   It then triggers the `sm_status_changed` event to notify about the state machine's internal status change. This event includes the old and new internal SM states and details about the triggering event.
*   **Status Management (`update_status_from_state_machine`):**
    *   Automatically maps state machine states to the corresponding `WorkflowStatus` enum values:
        * State machine state `'completed'` → `WorkflowStatus.COMPLETED`
        * State machine state `'failed'` → `WorkflowStatus.FAILED`
        * State machine state `'canceled'` → `WorkflowStatus.CANCELED`
        * Other states generally maintain the current workflow status
    *   Updates `self.status`, `self.end_time`, and `self.completion_reason` when a terminal state is reached.
    *   The `completion_reason` is set based on the event details or a default message based on the state machine state.
    *   When the workflow's overall status changes, it triggers the `workflow_status_changed` event to notify external listeners (like `WorkflowManager`).
    *   If the workflow status changes to `RUNNING` and an owner is specified, it also triggers a 'workflow_assigned' event on the owner agent to notify it about the assignment.
*   **Reset Functionality (`reset`):**
    *   Resets the workflow to its initial `PENDING` state.
    *   Stops the current state machine process and creates a new `WorkflowStatusMachine` instance with the original initial status.
    *   Re-establishes all transitions by calling `_setup_transitions` again to set up the state machine logic.
    *   Clears start_time, end_time, and completion_reason.
    *   Triggers a 'workflow_status_changed' event with the reason 'workflow_reset'.
    *   Allows workflows to be restarted after completion or failure.
*   **Timeout (`_timeout_monitor`):** An optional process that automatically fails the workflow if it doesn't reach a terminal state machine state within the specified duration.
*   **Details (`get_details`):** Subclasses should override this to provide specific information about the workflow's parameters or current goal context (useful for LLM planning or UI display).
*   **Suggested Task (`get_current_suggested_task`):** Subclasses can override this to provide a dictionary describing the task an agent should likely perform based on the current state machine state. This aids in agent decision-making.

#### 3.2 Implementing a Custom Workflow Subclass

1.  **Inherit:** `class MyWorkflow(Workflow): ...` (Optionally specify a metaclass inheriting from `WorkflowMeta` if defining templates within the metaclass).
2.  **Register Property Templates (Optional):** Use the `@classmethod register_property_template` decorator on the class or `WorkflowMeta.register_template` in a custom metaclass to define expected `properties`.
    ```python
    @Workflow.register_property_template('target_location', tuple, True, description="Target 3D coordinates")
    @Workflow.register_property_template('speed_limit', float, False, validator=lambda s: s > 0)
    class MyNavigationWorkflow(Workflow):
        # ...
    ```
3.  **Implement `__init__` (Optional but common):**
    *   Call `super().__init__(...)`.
    *   Store any specific properties needed for this workflow type (e.g., target locations, data IDs) usually accessed from the validated `self.properties` dictionary.
    ```python
    class InspectionWorkflow(Workflow): # Assuming metaclass handles templates
        def __init__(self, env, name, owner, initial_status='waiting', **kwargs):
            super().__init__(env, name, owner, initial_status=initial_status, **kwargs)
            # Properties are validated by the metaclass/base init
            self.inspection_points = self.properties.get('inspection_points', [])
            self.current_point_index = 0
            # ... rest of init
    ```
4.  **Implement `_setup_transitions` (Mandatory):**
    *   Define the state machine logic using `self.status_machine.add_transition(...)`.
    *   Use the various trigger options (`agent_state`, `event_trigger`, `time_trigger`) to listen for relevant simulation occurrences.
    *   Use callbacks for actions needed upon trigger activation *before* the state change.
    ```python
    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id
        battery_threshold = self.properties.get('battery_threshold', 20)

        sm.set_start_transition('monitoring') # Initial state after RUNNING

        # Example: Transition based on Agent's state (low battery)
        sm.add_transition(
            state='monitoring',
            next_status='needs_charge',
            agent_state={
                'agent_id': agent_id,
                'state_key': 'battery_level',
                'operator': TriggerOperator.LESS_THAN,
                'target_value': battery_threshold
            },
            description="Battery level dropped below threshold"
        )

        # Example: Transition based on an event (task completion)
        # Assumes a task triggers 'charge_task_completed' on the agent
        sm.add_transition(
            state='charging',
            next_status='completed',
            event_trigger={
                'source_id': agent_id, # Event triggered by the agent
                'event_name': 'charge_task_completed', # Custom event name
                # Optionally check event data:
                # 'value_key': 'result.status',
                # 'operator': TriggerOperator.EQUALS,
                # 'target_value': 'SUCCESS'
            },
            description="Charging task finished successfully"
        )

        # Example: Transition based on time
        sm.add_transition(
            state='waiting_confirmation',
            next_status='failed',
            time_trigger={
                'interval': 60 # Timeout after 60 seconds
            },
            description="Confirmation timed out"
        )

        # Example: Wildcard transition for failure (e.g., agent status becomes 'ERROR')
        sm.add_transition(
            state='*', # From any state
            next_status='failed',
            agent_state={
                 'agent_id': agent_id,
                 'state_key': 'status',
                 'operator': TriggerOperator.EQUALS,
                 'target_value': 'ERROR'
            },
            description="Agent entered ERROR state"
        )
    ```
5.  **Implement `get_details` (Optional):**
    *   Return a dictionary with useful context about the workflow's goal.
    ```python
    def get_details(self):
        details = super().get_details()
        details.update({
            'goal_type': 'inspection',
            'total_points': len(self.inspection_points),
            'current_target_index': self.current_point_index, # Needs logic to update based on SM state
            'points': self.inspection_points
        })
        return details
    ```
6.  **Implement `get_current_suggested_task` (Optional):**
    *   Return a dictionary describing the task the agent should perform based on `self.status_machine.state`.
    ```python
    def get_current_suggested_task(self):
        if not self.owner or self.status != WorkflowStatus.RUNNING: return None
        current_sm_state = self.status_machine.state

        if current_sm_state == 'needs_charge':
            # Find nearest charging station (example logic)
            station = self.env.resource_manager.find_nearest('charging_station', self.owner.get_state('position'))
            if station:
                return {
                    'component': 'MoveTo', # Component name
                    'task_class': 'MoveToTask', # Task class name (string)
                    'task_name': f'Move to Charging Station {station.id}',
                    'workflow_id': self.id,
                    'properties': {'target_position': station.location},
                    'target_state': {'position': station.location} # Optional expected agent state after task
                }
        elif current_sm_state == 'charging':
             return {
                 'component': 'ChargingComponent',
                 'task_class': 'ChargeBatteryTask',
                 'task_name': 'Charge Battery',
                 'workflow_id': self.id,
                 'properties': {'target_level': self.properties.get('target_charge_level', 95)},
                 'target_state': {'battery_level': self.properties.get('target_charge_level', 95)}
             }
        # ... other states
        return None # No suggestion for the current state
    ```

### 4. Key Takeaways & Best Practices

*   `Workflow` represents the high-level goal and manages overall status; `WorkflowStatusMachine` drives the internal step-by-step progression based on triggers and events.
*   Subclasses **must** implement `_setup_transitions` to define the state machine logic using `add_transition`.
*   Use the appropriate trigger type (`agent_state`, `event_trigger`, `time_trigger`, or custom `trigger`) for each transition.
*   Use `properties` validated by `WorkflowPropertyTemplate` for workflow-specific parameters.
*   Implement `get_details` and `get_current_suggested_task` for better integration with agent decision-making (especially LLMs) and monitoring.
*   The `WorkflowStatusMachine`'s internal state (`current_status`) is distinct from the `Workflow`'s overall status (`status`). The workflow updates its status based on the state machine's progression.
*   Listen to `workflow_status_changed` (for overall status) or `sm_status_changed` (for internal SM state changes) events for monitoring. These events are automatically registered during workflow initialization.
*   The `reset()` method allows workflows to be reused or restarted.
*   Keep transition logic clear and focused. Use callbacks for actions directly related to the trigger activation, but complex logic should ideally be handled by the Agent based on the new state or suggested task.
*   The workflow automatically registers property templates for 'task_priority' and 'task_preemptive' which are used to control task execution behavior when tasks are suggested by the workflow.

### 5. Diagram Generation

Workflows can be visualized using PlantUML or Mermaid diagrams to help understand and debug the workflow logic:

*   `workflow.to_uml_activity_diagram()`: Generates a PlantUML activity diagram string.
    * The diagram shows all states and transitions defined in the workflow's state machine.
    * Terminal states (completed, failed, canceled) are represented with stop nodes.
    * Transitions are labeled with their descriptions and trigger types.
    * Wildcard transitions (those that apply to any state) are shown separately with a note.

*   `workflow.to_mermaid_diagram()`: Generates a Mermaid state diagram string.
    * Similar to the UML diagram but in Mermaid syntax for integration with Markdown documents.
    * Shows states, transitions, and includes notes for wildcard transitions.
    * Terminal states are connected to the end node.

Example usage:
```python
# Generate and save a UML diagram
uml_diagram = workflow.to_uml_activity_diagram()
with open('workflow_diagram.puml', 'w') as f:
    f.write(uml_diagram)

# Generate and save a Mermaid diagram
mermaid_diagram = workflow.to_mermaid_diagram()
with open('workflow_diagram.md', 'w') as f:
    f.write(mermaid_diagram)
```

These diagrams are particularly useful for complex workflows with many states and transitions, as they provide a visual representation of the workflow's logic.
