Workflow Development Guide
==========================

This guide covers how to create custom workflows that coordinate high-level processes in AeroAgentSim.

Overview
--------

Workflows represent higher-level goals or processes that coordinate multiple tasks and components. They use state machines to monitor conditions and automatically suggest appropriate tasks to agents based on their current state and workflow progress.

Key Concepts
------------

Workflow Architecture
~~~~~~~~~~~~~~~~~~~~~

Every workflow in AeroAgentSim consists of:

- **State Machine**: Manages internal states and transitions
- **Trigger System**: Monitors conditions and drives state transitions
- **Task Suggestions**: Provides task recommendations based on current state
- **Event Integration**: Responds to simulation events and agent state changes

Base Workflow Class
~~~~~~~~~~~~~~~~~~~

All workflows inherit from the base ``Workflow`` class:

.. code-block:: python

   from aeroagentsim.core.workflow import Workflow
   
   class CustomWorkflow(Workflow):
       """Custom workflow implementation."""
       
       def __init__(self, env, name, owner, properties=None):
           super().__init__(env, name, owner, properties)
           
       def _setup_transitions(self):
           """Define state machine transitions."""
           # Implement transition logic

Creating a Custom Workflow
---------------------------

Step 1: Define Workflow Class
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.workflow import Workflow
   from aeroagentsim.core.trigger import TriggerOperator
   
   class MaintenanceWorkflow(Workflow):
       """Workflow for equipment maintenance operations."""
       
       def __init__(self, env, name, owner, properties=None):
           super().__init__(env, name, owner, properties)
           self.maintenance_points = properties.get('maintenance_points', [])
           self.current_point_index = 0
           self.maintenance_duration = properties.get('maintenance_duration', 60)
           
       @classmethod
       def register_property_template(cls, env):
           """Register property templates for this workflow."""
           return {
               'maintenance_points': {
                   'type': list,
                   'required': True,
                   'description': 'List of maintenance locations'
               },
               'maintenance_duration': {
                   'type': float,
                   'required': False,
                   'default': 60.0,
                   'description': 'Duration for each maintenance task'
               }
           }

Step 2: Setup State Machine Transitions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _setup_transitions(self):
       """Define the maintenance workflow state machine."""
       sm = self.status_machine
       
       # Set initial state
       sm.set_start_transition('moving_to_point')
       
       # Transition: Arrive at maintenance point
       sm.add_transition(
           state='moving_to_point',
           next_status='performing_maintenance',
           agent_state={
               'agent_id': self.owner.id,
               'state_key': 'position',
               'operator': TriggerOperator.CUSTOM,
               'target_value': self._at_maintenance_point
           },
           description="Arrived at maintenance point"
       )
       
       # Transition: Complete maintenance
       sm.add_transition(
           state='performing_maintenance',
           next_status='moving_to_point',
           time_trigger={'delay': self.maintenance_duration},
           callback=self._advance_to_next_point,
           description="Maintenance completed, move to next point"
       )
       
       # Transition: All points completed
       sm.add_transition(
           state='performing_maintenance',
           next_status='completed',
           event_trigger={
               'source_id': self.owner.id,
               'event_name': 'task_completed',
               'operator': TriggerOperator.CUSTOM,
               'target_value': self._all_points_completed
           },
           description="All maintenance points completed"
       )

Step 3: Implement Helper Methods
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _at_maintenance_point(self, position):
       """Check if agent is at current maintenance point."""
       if self.current_point_index < len(self.maintenance_points):
           target = self.maintenance_points[self.current_point_index]
           distance = self._calculate_distance(position[:2], target[:2])
           return distance < 5.0  # Within 5 meters
       return False
   
   def _advance_to_next_point(self, context):
       """Advance to next maintenance point."""
       self.current_point_index += 1
   
   def _all_points_completed(self, event_data):
       """Check if all maintenance points have been completed."""
       return self.current_point_index >= len(self.maintenance_points)
   
   def _calculate_distance(self, pos1, pos2):
       """Calculate distance between two points."""
       return ((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)**0.5

Step 4: Implement Task Suggestions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def get_current_suggested_task(self):
       """Get current suggested task based on workflow state."""
       current_state = self.status_machine.current_status
       
       if current_state == 'moving_to_point':
           # Suggest movement task to current maintenance point
           if self.current_point_index < len(self.maintenance_points):
               target = self.maintenance_points[self.current_point_index]
               return {
                   'task_class': 'MoveToTask',
                   'params': {
                       'target_position': target,
                       'precision': 5.0
                   }
               }
               
       elif current_state == 'performing_maintenance':
           # Suggest maintenance task
           return {
               'task_class': 'MaintenanceTask',
               'params': {
                   'duration': self.maintenance_duration,
                   'maintenance_type': 'routine_inspection'
               }
           }
       
       return None

Step 5: Add Event Handling
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def _register_workflow_events(self):
       """Register for workflow-specific events."""
       super()._register_workflow_events()
       
       # Listen for maintenance completion events
       self.event_names.extend([
           'maintenance_completed',
           'equipment_failure',
           'weather_alert'
       ])
   
   def handle_event(self, event_name, event_data):
       """Handle workflow-specific events."""
       super().handle_event(event_name, event_data)
       
       if event_name == 'equipment_failure':
           # Handle equipment failure during maintenance
           self._handle_equipment_failure(event_data)
           
       elif event_name == 'weather_alert':
           # Handle severe weather conditions
           self._handle_weather_alert(event_data)
   
   def _handle_equipment_failure(self, event_data):
       """Handle equipment failure event."""
       self.logger.warning(f"Equipment failure detected: {event_data}")
       # Transition to error state or emergency procedures
       self.status_machine.force_transition('emergency_maintenance')
   
   def _handle_weather_alert(self, event_data):
       """Handle severe weather alert."""
       alert_level = event_data.get('alert_level', 0.0)
       if alert_level > 0.8:
           # Suspend operations due to severe weather
           self.status_machine.force_transition('weather_hold')

Advanced Workflow Features
--------------------------

Conditional Transitions
~~~~~~~~~~~~~~~~~~~~~~~

Create transitions that depend on multiple conditions:

.. code-block:: python

   from aeroagentsim.core.trigger import CompositeTrigger, TriggerOperator
   
   def _setup_conditional_transitions(self):
       """Setup transitions with multiple conditions."""
       sm = self.status_machine
       
       # Transition only when both battery is sufficient AND weather is good
       sm.add_transition(
           state='waiting',
           next_status='active',
           composite_trigger={
               'operator': 'AND',
               'triggers': [
                   {
                       'type': 'agent_state',
                       'agent_id': self.owner.id,
                       'state_key': 'battery_level',
                       'operator': TriggerOperator.GREATER_THAN,
                       'target_value': 50.0
                   },
                   {
                       'type': 'event',
                       'source_id': 'weather_provider',
                       'event_name': 'weather_clear'
                   }
               ]
           },
           description="Ready to start when battery > 50% and weather is clear"
       )

Repeating Workflows
~~~~~~~~~~~~~~~~~~~

Create workflows that repeat automatically:

.. code-block:: python

   def _setup_repeating_workflow(self):
       """Setup workflow that repeats automatically."""
       sm = self.status_machine
       
       # After completion, return to initial state for next cycle
       sm.add_transition(
           state='completed',
           next_status='idle',
           time_trigger={'delay': 300},  # Wait 5 minutes before next cycle
           callback=self._reset_workflow_state,
           description="Reset for next maintenance cycle"
       )
   
   def _reset_workflow_state(self, context):
       """Reset workflow state for next cycle."""
       self.current_point_index = 0
       self.logger.info("Maintenance workflow reset for next cycle")

Workflow Integration
--------------------

Using Your Custom Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from aeroagentsim.core.environment import Environment
   from aeroagentsim.agent.drone import DroneAgent
   from aeroagentsim.component.mobility import MoveToComponent
   from your_module import MaintenanceWorkflow
   
   # Create environment and agent
   env = Environment()
   drone = DroneAgent(env, "maintenance_drone", properties={
       'position': (0, 0, 100),
       'battery_level': 100
   })
   drone.add_component(MoveToComponent(env, drone))
   env.register_agent(drone)
   
   # Define maintenance points
   maintenance_points = [
       (50, 50, 100),   # Point 1
       (150, 50, 100),  # Point 2
       (150, 150, 100), # Point 3
       (50, 150, 100)   # Point 4
   ]
   
   # Create workflow with trigger-based startup
   from aeroagentsim.core.trigger import TimeTrigger
   
   workflow = env.create_workflow(
       MaintenanceWorkflow,
       name="routine_maintenance",
       owner=drone,
       properties={
           'maintenance_points': maintenance_points,
           'maintenance_duration': 120.0
       },
       start_trigger=TimeTrigger(env, trigger_time=100),
       max_starts=1
   )
   
   # Run simulation
   env.run(until=2000)

Workflow Coordination
~~~~~~~~~~~~~~~~~~~~~

Coordinate multiple workflows:

.. code-block:: python

   # Create multiple agents with different workflows
   agents = []
   for i in range(3):
       agent = DroneAgent(env, f"drone_{i}", properties={
           'position': (i*100, 0, 100),
           'battery_level': 100
       })
       agent.add_component(MoveToComponent(env, agent))
       env.register_agent(agent)
       agents.append(agent)
       
       # Stagger workflow start times
       workflow = env.create_workflow(
           MaintenanceWorkflow,
           name=f"maintenance_{i}",
           owner=agent,
           properties={'maintenance_points': maintenance_points},
           start_trigger=TimeTrigger(env, trigger_time=i*200),
           max_starts=1
       )

Best Practices
--------------

State Machine Design
~~~~~~~~~~~~~~~~~~~~

1. **Keep states simple** and focused on specific workflow phases
2. **Use descriptive state names** that clearly indicate workflow progress
3. **Design for error recovery** with appropriate error states
4. **Test all transition paths** thoroughly

Task Coordination
~~~~~~~~~~~~~~~~~

1. **Provide clear task suggestions** based on current workflow state
2. **Handle task failures gracefully** with appropriate fallback strategies
3. **Monitor task progress** and adjust workflow accordingly
4. **Coordinate with agent capabilities** through component interfaces

Event Integration
~~~~~~~~~~~~~~~~~

1. **Listen for relevant events** that affect workflow execution
2. **Handle external events** (weather, failures) appropriately
3. **Trigger workflow events** to communicate with other system components
4. **Use event filtering** to avoid unnecessary processing

Performance
~~~~~~~~~~~

1. **Optimize trigger conditions** for efficient evaluation
2. **Use appropriate timeout values** for time-based transitions
3. **Minimize state machine complexity** for better performance
4. **Monitor workflow execution** for bottlenecks

Testing
~~~~~~~

1. **Test all state transitions** under various conditions
2. **Verify task suggestions** are appropriate for each state
3. **Test error conditions** and recovery mechanisms
4. **Validate workflow completion** criteria

For more information, see:

- :doc:`../api/workflow` - Complete workflow API reference
- :doc:`agent_development` - Creating agents that use workflows
- :doc:`component_development` - Building components for workflow tasks
