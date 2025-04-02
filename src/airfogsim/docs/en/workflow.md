
## AirFogSim Workflow & State Machine Developer Documentation

This document explains the `Workflow` and `WorkflowStatusMachine` classes in AirFogSim, guiding developers on how to implement custom workflow logic based on simulation events, particularly Agent state changes and Task outcomes.

### 1. Overview

Workflows in AirFogSim represent higher-level goals, processes, or sequences of actions that an `Agent` might undertake. They are driven by a dedicated state machine (`WorkflowStatusMachine`) that reacts to events occurring within the simulation.

*   **`Workflow`:** The main object representing the goal. It holds overall status (Pending, Running, Completed, etc.), references the owner `Agent`, and contains workflow-specific properties or goals. It defines *what* the goal is.
*   **`WorkflowStatusMachine`:** The engine contained within a `Workflow`. It manages the internal states of the workflow's progression. It listens for specific events (like Agent state changes, task proof updates, component events) and transitions between internal states based on predefined rules. It defines *how* the workflow progresses internally based on events.

**Relationship:** A `Workflow` object *has a* `WorkflowStatusMachine`. The `Workflow`'s overall status (e.g., `WorkflowStatus.RUNNING`) is managed externally (usually by a `WorkflowManager`), while the `WorkflowStatusMachine` manages the detailed internal progression (e.g., `inspecting_point_1`, `moving_to_target`, `awaiting_confirmation`) by reacting to fine-grained simulation events.

### 2. `WorkflowStatusMachine` Class

This class implements the event-driven state transition logic for a workflow.

#### 2.1 Core Concepts

*   **States:** The machine tracks its `current_status` (a string representing the internal state, e.g., `'waiting_for_drone'`, `'processing_data'`).
*   **Transitions (`add_transition`):** The core logic is defined by adding transitions. A transition specifies:
    *   `state`: The state(s) from which this transition is valid (string or list/tuple of strings). `"*"` acts as a wildcard, valid from any state.
    *   `source_id`: The ID of the entity expected to trigger the event (e.g., `self.workflow.owner.id` for agent events, a specific `Task.id`, or even the `env.id`).
    *   `event_name`: The name of the event to listen for (e.g., `'state_changed'`, `'proof_updated'`, `f'{component_name}.task_completed'`).
    *   `next_status`: The internal state the machine should transition *to* if the event occurs and the condition is met.
    *   `condition_func` (Optional): A function `Callable[[Any], bool]` that takes the `event_value` as input and returns `True` if the transition should proceed, `False` otherwise. This allows fine-grained control (e.g., checking if a specific Agent state key changed to a particular value).
*   **Event Monitoring (`_monitor_events`):** When the machine is active (`start()` is called), this SimPy process runs:
    1.  Determines the valid transitions from the `current_status` using `_get_current_transitions`.
    2.  **Subscribes** to all required events specified in the valid transitions using `env.event_registry.subscribe`.
    3.  **Waits (`yield env.any_of(...)`)** for any of the subscribed events to trigger.
    4.  When an event triggers, the callback `_on_event_triggered` is invoked.
    5.  Before waiting again (or if the state changes), it clears old subscriptions using `_clear_subscriptions`.
*   **State Change (`_on_event_triggered`, `_change_status`):**
    *   `_on_event_triggered` checks if the received event matches a valid transition from the current state and if its `condition_func` passes.
    *   If valid, `_change_status` is called. It updates the machine's internal `current_status`.
    *   **Crucially, `_change_status` notifies the parent `Workflow` object by calling `self.workflow._trigger_status_changed(...)`.** This allows the `Workflow` (and listening managers) to react to the internal state progression.
*   **Start State (`set_start_transition`):** Defines the initial internal state the machine enters *after* the `Workflow`'s overall status becomes `RUNNING`.
*   **Terminal States:** States like `'completed'`, `'failed'`, `'canceled'` stop the monitoring process.

#### 2.2 Usage

Developers typically interact with the `WorkflowStatusMachine` *indirectly* through the `Workflow` subclass, primarily within the `_setup_transitions` method by calling `self.status_machine.add_transition(...)`.

### 3. `Workflow` Base Class

This class represents the overall workflow goal and orchestrates the `WorkflowStatusMachine`.

#### 3.1 Core Concepts

*   **Overall Status (`self.status`):** Tracks the high-level status using the `WorkflowStatus` enum (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`). This is typically updated by a `WorkflowManager` listening to the workflow's `status_changed` event.
*   **State Machine (`self.status_machine`):** Holds the instance of `WorkflowStatusMachine` that drives internal progress.
*   **Initialization (`__init__`):** Sets up ID, name, owner, optional timeout. Creates the `WorkflowStatusMachine`. Calls the abstract `_setup_transitions`. Registers workflow-level events (like `status_changed`).
*   **Transitions Setup (`_setup_transitions`) - Abstract Method:**
    *   **This is the primary method subclasses MUST implement.**
    *   Inside this method, you define the workflow's logic by adding transitions to `self.status_machine`.
    *   Transitions typically listen for events related to the `self.owner` Agent (e.g., `self.owner.id`, `'state_changed'`), Task proofs (`self.owner.id`, `'proof_updated'`), or specific Task/Component completion events.
    *   Condition functions (`condition_func`) are essential here to check specific details of the event (e.g., which state key changed, proof data content).
*   **Starting (`start`):**
    *   Called externally (usually by `WorkflowManager`) when the workflow should begin execution.
    *   Changes the `Workflow.status` from `PENDING` to `RUNNING`.
    *   Triggers the initial `status_changed` event.
    *   Calls `self.status_machine.start()` to activate the event monitoring process.
* **Signaling Internal Changes (`_trigger_status_changed`):**
    * An **internal** method called *by the `WorkflowStatusMachine`* when *its* internal state changes.
    * This method first updates the workflow's status through `update_status_from_state_machine` based on the state machine's new state.
    * It then triggers the `sm_status_changed` event to notify about the state machine's status change. This event includes the old and new internal SM states and details about the triggering event.
    * **The workflow itself manages its own status** based on the state machine's state transitions.

* **Status Management (`update_status_from_state_machine`):**
    * Automatically maps state machine states to appropriate workflow statuses.
    * Common mappings include: `'completed'` → `WorkflowStatus.COMPLETED`, `'failed'` → `WorkflowStatus.FAILED`, etc.
    * When the workflow status changes, it triggers a `workflow_status_changed` event to notify external listeners.
    * **External listeners (like `WorkflowManager`) now subscribe to the workflow's `workflow_status_changed` event** to react to changes in the workflow's overall status.

* **Reset Functionality (`reset`):**
    * Resets the workflow to its initial `PENDING` state.
    * Cleans up the existing state machine process and creates a new state machine instance.
    * Re-establishes all transitions through `_setup_transitions`.
    * Allows workflows to be restarted after completion.
*   **Timeout (`_timeout_monitor`):** An optional process that automatically fails the workflow if it doesn't reach a terminal state machine state within the specified duration.
*   **Details (`get_details`):** Subclasses can override this to provide specific information about the workflow's parameters or current goal context (useful for LLM planning).

#### 3.2 Implementing a Custom Workflow Subclass

1.  **Inherit:** `class MyWorkflow(Workflow): ...`
2.  **Implement `__init__` (Optional but common):**
    *   Call `super().__init__(...)`.
    *   Store any specific properties needed for this workflow type (e.g., target locations, data IDs) usually passed via the `properties` dictionary.
    ```python
    class InspectionWorkflow(Workflow):
        def __init__(self, env, name, owner, inspection_points: List[Tuple], **kwargs):
            properties = kwargs.pop('properties', {})
            properties['inspection_points'] = inspection_points # Store specific goal
            super().__init__(env, name, owner, properties=properties, **kwargs)
            self.inspection_points = inspection_points
            self.current_point_index = 0
    ```
3.  **Implement `_setup_transitions` (Mandatory):**
    *   Define the state machine logic using `self.status_machine.add_transition(...)`.
    *   Listen to relevant events, often from `self.owner.id`.
    *   Use condition functions to check event details precisely.
    ```python
    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id
        proof_id = self.properties.get('proof_id') # Assuming proof_id is needed

        sm.set_start_transition('moving_to_point_1') # Initial state after RUNNING

        # Example: Transition from moving to inspecting based on Agent's position
        sm.add_transition(
            state='moving_to_point_1',
            source_id=agent_id,
            event_name='state_changed', # Listen for any agent state change
            next_status='inspecting_point_1',
            condition_func=lambda ev: (
                ev.get('key') == 'position' and
                self._is_at_point(ev.get('new_value'), 0) # Check if new position is near point 0
            )
        )

        # Example: Transition from inspecting to moving to next point based on Task Proof
        sm.add_transition(
            state='inspecting_point_1',
            source_id=agent_id, # Agent triggers proof update
            event_name='proof_updated',
            next_status='moving_to_point_2',
            condition_func=lambda ev: (
                ev.get('proof_id') == proof_id and # Check it's the right proof
                ev.get('data', {}).get('inspection_status') == 'complete' # Check proof data
            )
        )

        # Example: Transition to completed state
        sm.add_transition(
            state='moving_to_final_point', # Example final move state
            source_id=agent_id,
            event_name='state_changed',
            next_status='completed', # Terminal state
            condition_func=lambda ev: (
                 ev.get('key') == 'position' and
                 self._is_at_point(ev.get('new_value'), -1) # Check if at last point
            )
        )

        # Example: Wildcard transition for failure (e.g., low battery)
        sm.add_transition(
            state='*', # From any state
            source_id=agent_id,
            event_name='state_changed',
            next_status='failed', # Terminal state
            condition_func=lambda ev: (
                 ev.get('key') == 'battery_level' and ev.get('new_value', 100) < 5
            )
        )
    # Helper method for condition_func
    def _is_at_point(self, current_pos, point_index):
        if not current_pos: return False
        target_index = point_index if point_index >= 0 else len(self.inspection_points) - 1
        if 0 <= target_index < len(self.inspection_points):
            target_pos = self.inspection_points[target_index]
            # Simple distance check (implement distance calculation)
            distance = calculate_distance(current_pos, target_pos)
            return distance < 1.0 # Tolerance
        return False

    # (Need to define calculate_distance helper function)

    ```
4.  **Implement `get_details` (Optional):**
    *   Return a dictionary with useful context about the workflow's goal.
    ```python
    def get_details(self):
        details = super().get_details()
        details.update({
            'goal_type': 'inspection',
            'total_points': len(self.inspection_points),
            'current_target_index': self.current_point_index, # Need to update this index based on SM state
            'points': self.inspection_points,
            'proof_id': self.properties.get('proof_id')
        })
        return details
    ```

### 4. Key Takeaways

* `Workflow` represents the goal; `WorkflowStatusMachine` drives internal progress based on events.
* Subclasses **must** implement `_setup_transitions` to define the state machine logic using `add_transition`.
* Transitions listen for specific events (often from the owner `Agent`) and use condition functions to check event details.
* The `WorkflowStatusMachine`'s internal state (`current_status`) is distinct from the `Workflow`'s overall status (`status`).
* The `Workflow` maintains its own status by automatically mapping state machine states to appropriate workflow statuses via `update_status_from_state_machine`.
* There are now two distinct events: `sm_status_changed` (for internal state machine changes) and `workflow_status_changed` (for overall workflow status changes).
* External systems (like `WorkflowManager`) now subscribe to `workflow_status_changed` to react to workflow status changes.
* The `reset()` method allows workflows to be restarted after completion, which is useful for repeatable workflows.
* Condition functions (`condition_func`) are critical for precise control over state transitions based on event data.

