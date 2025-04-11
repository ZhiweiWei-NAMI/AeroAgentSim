# AirFogSim Trigger System Guide

The AirFogSim trigger system provides a flexible way to react to various conditions and events within the simulation. Triggers are fundamental components for driving `WorkflowStatusMachine` transitions and automating agent behavior. This guide explains how to define and use different types of triggers.

## 1. Core Concepts

*   **Trigger:** An object that monitors a specific condition (event occurrence, state change, time passage) and executes registered callback functions when the condition is met.
*   **Activation/Deactivation:** Triggers must be `activate()`d to start monitoring. They can be `deactivate()`d to stop monitoring. Triggers automatically deactivate after firing once to prevent immediate re-triggering within the same simulation step, unless they are part of a `CompositeTrigger` or designed for periodic firing (like interval-based `TimeTrigger`). Workflows typically reactivate necessary triggers when entering a new state.
*   **Callbacks:** Functions added via `add_callback()` that are executed when the trigger fires. Callbacks receive a context dictionary with details about the trigger event.
*   **Context:** The dictionary passed to callbacks, containing information like `trigger_id`, `trigger_name`, `trigger_type`, `time`, and type-specific details (e.g., `event_value`, `new_state`).

## 2. Trigger Types

AirFogSim provides several built-in trigger types:

### 2.1 `EventTrigger`

*   **Purpose:** Reacts to specific named events published via the `env.event_registry`.
*   **Key Parameters:**
    *   `source_id` (str): The ID of the entity expected to publish the event.
    *   `event_name` (str): The name of the event to listen for.
    *   `value_key` (Optional[str]): A dot-separated path to extract a specific value from the event data dictionary (e.g., `'data.position'`). If `None`, the entire event data dictionary is used for comparison.
    *   `operator` (Optional[TriggerOperator]): The comparison operator to use (e.g., `EQUALS`, `LESS_THAN`, `CONTAINS`, `CUSTOM`). If `None`, the trigger fires whenever the event occurs, regardless of its value.
    *   `target_value` (Any): The value to compare the extracted event data against. If `operator` is `CUSTOM`, this should be a callable function `(value) -> bool`.
*   **Context Keys:** `source_id`, `event_name`, `event_value`, `value_key`, `operator`, `target_value`.

**Example:** Trigger when `drone_1` emits `task_completed` and the `result.status` key in the event data equals `'SUCCESS'`.

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

task_success_trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="task_completed",
    value_key="result.status", # Check nested key
    operator=TriggerOperator.EQUALS,
    target_value="SUCCESS",
    name="drone1_task_success"
)

def on_task_success(context):
    print(f"Time {context['time']}: Drone 1 task succeeded! Event data: {context['event_value']}")

task_success_trigger.add_callback(on_task_success)
task_success_trigger.activate()
```

### 2.2 `StateTrigger`

*   **Purpose:** Monitors changes in a specific state variable of an `Agent`.
*   **Key Parameters:**
    *   `agent_id` (str): The ID of the agent whose state is being monitored.
    *   `state_key` (str): The name of the state variable to monitor (must exist in the agent's state templates).
    *   `operator` (TriggerOperator): The comparison operator.
    *   `target_value` (Any): The value to compare the state against. If `operator` is `CUSTOM`, this should be a callable function `(value) -> bool`.
*   **Behavior:** Listens for the agent's `state_changed` event. When the specified `state_key` changes, it compares the *new* value against the `target_value` using the `operator`.
*   **Context Keys:** `agent_id`, `state_key`, `old_value`, `new_value`, `operator`, `target_value`.

**Example:** Trigger when `drone_1`'s `battery_level` drops below 20.

```python
from airfogsim.core.trigger import StateTrigger
from airfogsim.core.enums import TriggerOperator

low_battery_trigger = StateTrigger(
    env,
    agent_id="drone_1",
    state_key="battery_level",
    operator=TriggerOperator.LESS_THAN,
    target_value=20,
    name="drone1_low_battery"
)

def on_low_battery(context):
    print(f"Time {context['time']}: Drone 1 low battery alert! Level: {context['new_value']:.1f}%")

low_battery_trigger.add_callback(on_low_battery)
low_battery_trigger.activate()
```

### 2.3 `TimeTrigger`

*   **Purpose:** Activates based on simulation time.
*   **Key Parameters (Choose One):**
    *   `trigger_time` (float): Activates once at the specified absolute simulation time.
    *   `interval` (float): Activates repeatedly at the specified interval after activation.
    *   `cron_expr` (str): *Currently simplified:* Interpreted as an interval for repeated triggering (intended for future full cron support).
*   **Context Keys:** `trigger_mode` ('one_time', 'interval', 'cron').

**Example:** Trigger every 60 simulation time units.

```python
from airfogsim.core.trigger import TimeTrigger

periodic_trigger = TimeTrigger(
    env,
    interval=60,
    name="hourly_check"
)

def periodic_check(context):
    print(f"Time {context['time']}: Performing periodic check.")

periodic_trigger.add_callback(periodic_check)
periodic_trigger.activate()
```

### 2.4 `CompositeTrigger`

*   **Purpose:** Combines multiple triggers using logical AND or OR.
*   **Key Parameters:**
    *   `triggers` (List[Trigger]): A list of trigger instances to combine.
    *   `operator` (TriggerOperator.AND | TriggerOperator.OR): The logical operator to combine the triggers.
*   **Behavior:**
    *   **AND:** Fires only when *all* sub-triggers have fired since the last composite trigger activation or reset.
    *   **OR:** Fires when *any* of the sub-triggers fire.
*   **Activation/Deactivation:** Activating/deactivating the composite trigger also activates/deactivates all its sub-triggers.
*   **State Reset:** After the composite trigger fires, the internal states of which sub-triggers have fired are reset.
*   **Context Keys:**
    *   **AND:** `subtriggers` (Dict[str, bool] showing which sub-triggers fired).
    *   **OR:** `subtrigger_id` (ID of the sub-trigger that fired), `subtrigger_context` (context from the sub-trigger).

**Example:** Trigger if `drone_1` has low battery (`StateTrigger`) AND a specific `task_failed` event occurs (`EventTrigger`).

```python
from airfogsim.core.trigger import CompositeTrigger, StateTrigger, EventTrigger
from airfogsim.core.enums import TriggerOperator

# Assumes low_battery_trigger is defined as above
task_fail_trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="task_failed",
    name="drone1_task_fail"
)

critical_condition_trigger = CompositeTrigger(
    env,
    triggers=[low_battery_trigger, task_fail_trigger],
    operator=TriggerOperator.AND,
    name="drone1_critical_condition"
)

def on_critical_condition(context):
    print(f"Time {context['time']}: CRITICAL - Drone 1 has low battery AND a task failed!")
    # context['subtriggers'] will show both triggers as True

critical_condition_trigger.add_callback(on_critical_condition)
critical_condition_trigger.activate()
```

## 3. Trigger Operators

The following operators (`airfogsim.core.enums.TriggerOperator`) are available for `EventTrigger` and `StateTrigger`:

*   `EQUALS`: Equal to (`==`)
*   `NOT_EQUALS`: Not equal to (`!=`)
*   `GREATER_THAN`: Greater than (`>`)
*   `LESS_THAN`: Less than (`<`)
*   `GREATER_EQUAL`: Greater than or equal to (`>=`)
*   `LESS_EQUAL`: Less than or equal to (`<=`)
*   `CONTAINS`: Checks if `target_value` is in the event/state value (e.g., `item in list`).
*   `NOT_CONTAINS`: Checks if `target_value` is not in the event/state value.
*   `CUSTOM`: Uses a custom function provided as `target_value`. The function receives the extracted value and should return `True` or `False`.
*   `AND` / `OR`: Used only for `CompositeTrigger`.

## 4. Using Triggers in Workflows

The primary use case for triggers is defining transitions within a `WorkflowStatusMachine`. The `add_transition` method simplifies trigger creation:

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator, WorkflowStatus

class MyWorkflow(Workflow):
    # ... (init, property templates etc.)

    def _setup_transitions(self):
        sm = self.status_machine
        agent_id = self.owner.id

        sm.set_start_transition('waiting_for_task')

        # Transition based on agent state change
        sm.add_transition(
            state='waiting_for_task',
            next_status='processing',
            agent_state={ # Creates a StateTrigger internally
                'agent_id': agent_id,
                'state_key': 'current_task_status',
                'operator': TriggerOperator.EQUALS,
                'target_value': 'RECEIVED'
            },
            description="Agent received the task"
        )

        # Transition based on a specific event
        sm.add_transition(
            state='processing',
            next_status='completed',
            event_trigger={ # Creates an EventTrigger internally
                'source_id': agent_id,
                'event_name': 'processing_finished',
                'value_key': 'result_code', # Check event data
                'operator': TriggerOperator.EQUALS,
                'target_value': 0 # Success code
            },
            description="Processing finished successfully"
        )

        # Transition based on time (timeout)
        sm.add_transition(
            state='processing',
            next_status='failed',
            time_trigger={ # Creates a TimeTrigger internally
                'interval': 120 # Fail if processing takes longer than 120s
            },
            description="Processing timed out"
        )

        # Transition using a pre-defined trigger instance
        # custom_trigger = SomeCustomTrigger(...)
        # sm.add_transition(
        #     state='some_state',
        #     next_status='other_state',
        #     trigger=custom_trigger,
        #     description="Custom condition met"
        # )
```

## 5. Best Practices

1.  **Descriptive Names:** Give triggers meaningful names for easier debugging.
2.  **Specific Conditions:** Use `value_key` and appropriate `operator`s for `EventTrigger` and `StateTrigger` to avoid triggering on irrelevant changes.
3.  **Use `CUSTOM` Sparingly:** Prefer built-in operators for clarity. Use `CUSTOM` for complex logic that cannot be expressed otherwise. Ensure custom functions are robust.
4.  **Manage Activation:** Ensure triggers are activated when needed (e.g., when a workflow enters a state) and deactivated when no longer relevant (often handled automatically by the workflow state machine).
5.  **Lightweight Callbacks:** Keep trigger callbacks fast and non-blocking. Complex actions should typically be initiated by the agent or workflow based on the state change triggered by the callback.
6.  **Composite Triggers:** Use `CompositeTrigger` for complex AND/OR conditions instead of nesting logic deeply within callbacks or custom functions.
