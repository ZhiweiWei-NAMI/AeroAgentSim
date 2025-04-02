# Trigger System Guide

The AirFogSim trigger system provides a flexible way to respond to events and state changes in the simulation. This guide explains how to use the trigger system effectively.

## Trigger Types

AirFogSim supports several types of triggers:

1. **Event Triggers**: Respond to specific events emitted by simulation entities
2. **State Triggers**: Monitor agent state changes
3. **Time Triggers**: Activate at specific times or intervals
4. **Composite Triggers**: Combine multiple triggers with logical operators

## Basic Usage

### Creating a Simple Event Trigger

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

# Create a trigger that activates when a drone's battery level drops below 20%
trigger = EventTrigger(
    env,                          # Simulation environment
    source_id="drone_1",          # Entity emitting the event
    event_name="state_changed",   # Event to monitor
    value_key="battery_level",    # Key to check in the event data
    operator=TriggerOperator.LESS_THAN,  # Comparison operator
    target_value=20,              # Value to compare against
    name="low_battery_trigger"    # Optional name for the trigger
)

# Add a callback function to execute when the trigger activates
trigger.add_callback(lambda ctx: print(f"Low battery alert: {ctx['event_value']}%"))

# Activate the trigger
trigger.activate()
```

### Creating a Time Trigger

```python
from airfogsim.core.trigger import TimeTrigger

# Create a trigger that activates every 10 simulation time units
trigger = TimeTrigger(
    env,
    interval=10,
    name="periodic_trigger"
)

# Add a callback
trigger.add_callback(lambda ctx: print(f"Time: {env.now} - Periodic event triggered"))

# Activate the trigger
trigger.activate()
```

### Creating a State Trigger

```python
from airfogsim.core.trigger import StateTrigger
from airfogsim.core.enums import TriggerOperator

# Create a trigger that activates when an agent's position changes
trigger = StateTrigger(
    env,
    agent_id="drone_1",
    state_key="position",
    operator=TriggerOperator.NOT_EQUALS,
    target_value=None,  # Will trigger on any position change
    name="position_change_trigger"
)

# Add a callback
trigger.add_callback(lambda ctx: print(f"Position changed to: {ctx['new_value']}"))

# Activate the trigger
trigger.activate()
```

### Creating a Composite Trigger

```python
from airfogsim.core.trigger import CompositeTrigger, EventTrigger, StateTrigger
from airfogsim.core.enums import TriggerOperator

# Create individual triggers
battery_trigger = EventTrigger(
    env, "drone_1", "state_changed", "battery_level", 
    TriggerOperator.LESS_THAN, 10
)

position_trigger = StateTrigger(
    env, "drone_1", "position", 
    TriggerOperator.EQUALS, [0, 0, 0]
)

# Combine triggers with AND operator (both conditions must be met)
composite = CompositeTrigger(
    env,
    triggers=[battery_trigger, position_trigger],
    operator=TriggerOperator.AND,
    name="low_battery_at_home_trigger"
)

# Add a callback
composite.add_callback(lambda ctx: print("Drone is at home with low battery"))

# Activate the composite trigger
composite.activate()
```

## Trigger Operators

The following operators are available for comparing values:

- `TriggerOperator.EQUALS`: Equal to
- `TriggerOperator.NOT_EQUALS`: Not equal to
- `TriggerOperator.GREATER_THAN`: Greater than
- `TriggerOperator.LESS_THAN`: Less than
- `TriggerOperator.GREATER_EQUAL`: Greater than or equal to
- `TriggerOperator.LESS_EQUAL`: Less than or equal to
- `TriggerOperator.CONTAINS`: Contains (for collections)
- `TriggerOperator.NOT_CONTAINS`: Does not contain (for collections)
- `TriggerOperator.AND`: Logical AND (for composite triggers)
- `TriggerOperator.OR`: Logical OR (for composite triggers)
- `TriggerOperator.CUSTOM`: Custom function (advanced usage)

## Using Triggers in Workflows

Workflows in AirFogSim use triggers to manage state transitions. Here's an example of setting up a workflow with triggers:

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator

class MyWorkflow(Workflow):
    def _setup_transitions(self):
        # Set initial state
        self.status_machine.set_start_transition('waiting')
        
        # Add a transition based on agent state
        self.status_machine.add_transition(
            'waiting',           # Current state
            'moving',            # Next state
            agent_state={        # Agent state trigger configuration
                'agent_id': self.owner.id,
                'state_key': 'is_moving',
                'operator': TriggerOperator.EQUALS,
                'target_value': True
            }
        )
        
        # Add a transition based on time
        self.status_machine.add_transition(
            'moving',
            'timeout',
            time_trigger={
                'interval': 60  # Transition after 60 time units
            }
        )
        
        # Add a transition based on event
        self.status_machine.add_transition(
            'moving',
            'completed',
            event_trigger={
                'source_id': self.owner.id,
                'event_name': 'arrived',
                'value_key': 'location',
                'operator': TriggerOperator.EQUALS,
                'target_value': 'destination'
            }
        )
```

## Advanced Usage

### Custom Operators

For complex conditions, you can use the `CUSTOM` operator with a custom function:

```python
from airfogsim.core.trigger import EventTrigger
from airfogsim.core.enums import TriggerOperator

# Create a trigger with a custom condition function
trigger = EventTrigger(
    env,
    source_id="drone_1",
    event_name="position_updated",
    value_key="position",
    operator=TriggerOperator.CUSTOM,
    target_value=lambda pos: pos[0]**2 + pos[1]**2 < 100  # Activate when within a circle of radius 10
)
```

### Workflow Offloadability Analysis

The trigger system enables workflows to analyze whether they can be offloaded to other agents:

```python
# Check if a workflow can be offloaded
offload_analysis = workflow.analyze_offloadability()

if offload_analysis['offloadable']:
    print(f"Workflow can be offloaded. Offloadable states: {offload_analysis['states']}")
else:
    print("Workflow cannot be offloaded")
```

## Best Practices

1. **Use descriptive names**: Give your triggers meaningful names to make debugging easier
2. **Deactivate unused triggers**: Call `trigger.deactivate()` when a trigger is no longer needed
3. **Keep callbacks lightweight**: Trigger callbacks should be fast and avoid blocking operations
4. **Use composite triggers**: For complex conditions, use composite triggers instead of complex custom functions
5. **Handle exceptions in callbacks**: Wrap callback code in try-except blocks to prevent crashes

## Example: Inspection Workflow

Here's a complete example of an inspection workflow using the trigger system:

```python
from airfogsim.core import Workflow
from airfogsim.core.enums import TriggerOperator

class InspectionWorkflow(Workflow):
    def _setup_transitions(self):
        # Set initial state
        self.status_machine.set_start_transition('inspecting_point_1')
        
        # For each inspection point
        for i, point in enumerate(self.inspection_points):
            current_state = f'inspecting_point_{i+1}'
            
            # Determine next state
            if i+1 < len(self.inspection_points):
                next_state = f'inspecting_point_{i+2}'
            else:
                next_state = 'completed'
            
            # Add transition using event trigger
            self.status_machine.add_transition(
                current_state, 
                next_state,
                event_trigger={
                    'source_id': self.proof_id,
                    'event_name': 'proof_updated',
                    'value_key': 'data.position',
                    'operator': TriggerOperator.CUSTOM,
                    'target_value': lambda position, point=point: 
                        all([abs(position[i] - point[i]) < 1e-6 for i in range(3)])
                }
            )
