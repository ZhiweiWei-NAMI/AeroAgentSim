Workflow Classes
===============

Workflows coordinate high-level processes and goals in AirFogSim. They use state machines to track progress and suggest tasks to agents.

Base Workflow
-------------

.. autoclass:: airfogsim.core.workflow.Workflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Workflow State Machine
----------------------

.. autoclass:: airfogsim.core.workflow.WorkflowStatusMachine
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Inspection Workflows
--------------------

InspectionWorkflow
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.inspection.InspectionWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Logistics Workflows
-------------------

LogisticsWorkflow
~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.logistics.LogisticsWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

OrderExecutionWorkflow
~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.order_execution.OrderExecutionWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Charging Workflows
------------------

ChargingWorkflow
~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.charging.ChargingWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Processing Workflows
--------------------

ImageProcessingWorkflow
~~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.image_processing.ImageProcessingWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Contract Workflows
------------------

ContractWorkflow
~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.workflow.contract.ContractWorkflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Workflow Development Guide
--------------------------

Creating Custom Workflows
~~~~~~~~~~~~~~~~~~~~~~~~~

To create a custom workflow, inherit from the base Workflow class and define the state machine:

.. code-block:: python

   from airfogsim.core.workflow import Workflow
   from airfogsim.core.trigger import StateTrigger, TimeTrigger
   
   class CustomWorkflow(Workflow):
       """Custom workflow implementation."""
       
       def __init__(self, env, name, owner, **kwargs):
           super().__init__(env, name, owner, **kwargs)
           
           # Register workflow properties
           self.register_property_template('target_location', 
                                         value_type=list, 
                                         required=True)
           self.register_property_template('timeout_duration', 
                                         value_type=float, 
                                         required=False)
       
       def _setup_transitions(self):
           """Define state machine transitions."""
           sm = self.status_machine

           # Set initial state after workflow starts
           sm.set_start_transition('moving')

           # Transition: Arrive at target location
           sm.add_transition(
               state='moving',
               next_status='working',
               agent_state={
                   'agent_id': self.owner.id,
                   'state_key': 'position',
                   'operator': TriggerOperator.CUSTOM,
                   'target_value': self._at_target_location
               },
               description="Arrived at target location"
           )

           # Transition: Complete work after time delay
           sm.add_transition(
               state='working',
               next_status='completed',
               time_trigger={'delay': 300},  # 5 minutes
               description="Work completed"
           )
       
       def _at_target_location(self, position):
           """Check if agent is at target location."""
           target = self.properties['target_location']
           distance = self._calculate_distance(position, target)
           return distance < 5.0  # Within 5 meters

State Machine Design
~~~~~~~~~~~~~~~~~~~~

Workflows use state machines to track progress:

.. code-block:: python

   def _setup_transitions(self):
       """Define the workflow state machine."""
       sm = self.status_machine

       # Set initial state
       sm.set_start_transition('preparing')

       # Transition 1: Begin execution (triggered by agent state)
       sm.add_transition(
           state='preparing',
           next_status='executing',
           agent_state={
               'agent_id': self.owner.id,
               'state_key': 'preparation_status',
               'operator': TriggerOperator.EQUALS,
               'target_value': 'ready'
           },
           description="Preparation completed, begin execution"
       )

       # Transition 2: Complete workflow (time-based)
       sm.add_transition(
           state='executing',
           next_status='completed',
           time_trigger={'delay': self.properties.get('duration', 600)},
           description="Execution completed after timeout"
       )

       # Transition 3: Handle failures (event-based)
       sm.add_transition(
           state='*',  # From any state
           next_status='failed',
           event_trigger={
               'source_id': self.owner.id,
               'event_name': 'critical_error'
           },
           description="Critical error occurred"
       )

Task Suggestions
~~~~~~~~~~~~~~~~

Workflows suggest tasks to agents through transitions:

.. code-block:: python

   # Simple task suggestion
   task_suggestion = {
       'task_class': 'MoveToTask',
       'params': {
           'target_position': [100, 100, 50],
           'speed': 10.0
       }
   }
   
   # Conditional task suggestion
   def get_task_suggestion(self):
       """Dynamic task suggestion based on current state."""
       if self.owner.get_state('battery_level') < 20:
           return {
               'task_class': 'ChargingTask',
               'params': {'target_charge': 80}
           }
       else:
           return {
               'task_class': 'MoveToTask', 
               'params': {'target_position': self.properties['waypoint']}
           }

Trigger System
~~~~~~~~~~~~~~

Workflows use triggers to monitor conditions and drive state transitions:

.. code-block:: python

   from airfogsim.core.trigger import TriggerOperator

   def _setup_transitions(self):
       sm = self.status_machine

       # State-based trigger (battery level)
       sm.add_transition(
           state='monitoring',
           next_status='charging',
           agent_state={
               'agent_id': self.owner.id,
               'state_key': 'battery_level',
               'operator': TriggerOperator.LESS_THAN,
               'target_value': 20.0
           },
           description="Battery low, need charging"
       )

       # Event-based trigger (task completion)
       sm.add_transition(
           state='working',
           next_status='completed',
           event_trigger={
               'source_id': self.owner.id,
               'event_name': 'task_completed',
               'value_key': 'task_name',
               'operator': TriggerOperator.EQUALS,
               'target_value': 'ChargingTask'
           },
           description="Charging task completed"
       )

       # Time-based trigger (timeout)
       sm.add_transition(
           state='waiting',
           next_status='timeout',
           time_trigger={'delay': 300},  # 5 minutes
           description="Timeout after 5 minutes"
       )

Property Templates
~~~~~~~~~~~~~~~~~~

Define workflow properties for validation and documentation:

.. code-block:: python

   def __init__(self, env, name, owner, **kwargs):
       super().__init__(env, name, owner, **kwargs)
       
       # Register property templates
       self.register_property_template(
           'waypoints', 
           value_type=list, 
           required=True,
           description="List of waypoints to visit"
       )
       
       self.register_property_template(
           'inspection_duration',
           value_type=float,
           required=False,
           default=300.0,
           description="Duration to spend at each waypoint (seconds)"
       )
       
       self.register_property_template(
           'return_home',
           value_type=bool,
           required=False, 
           default=True,
           description="Whether to return to starting position"
       )

Workflow Lifecycle
~~~~~~~~~~~~~~~~~~

Workflows have a defined lifecycle:

.. code-block:: python

   # 1. Creation
   workflow = CustomWorkflow(env, "mission1", agent,
                           properties={'waypoints': [(0,0,100), (100,100,100)]})

   # 2. Assignment to agent
   env.workflow_manager.assign_workflow(agent, workflow)

   # 3. Activation
   workflow.start()

   # 4. Execution (automatic state transitions)
   # Agent queries workflow.get_current_suggested_task()
   # Triggers monitor conditions and drive transitions

   # 5. Completion or Reset
   # Workflow reaches terminal state (completed/failed/canceled)
   # Can be reset for reuse: workflow.reset()

Event Integration
~~~~~~~~~~~~~~~~~

Workflows integrate with the event system through triggers and event registration:

.. code-block:: python

   def __init__(self, env, name, owner, **kwargs):
       super().__init__(env, name, owner, **kwargs)

       # Register workflow-specific events
       self.event_names = ['battery_low', 'severe_weather', 'task_completed']
       self._register_workflow_events()

   def _setup_transitions(self):
       """Setup state machine with event-based transitions."""
       sm = self.status_machine

       # Listen for battery low events
       sm.add_transition(
           state='*',  # From any state
           next_status='emergency_charging',
           event_trigger={
               'source_id': self.owner.id,
               'event_name': 'battery_low'
           },
           description="Emergency charging due to low battery"
       )

       # Listen for severe weather events
       sm.add_transition(
           state='*',  # From any state
           next_status='failed',
           event_trigger={
               'source_id': 'weather_provider',
               'event_name': 'severe_weather'
           },
           description="Workflow cancelled due to severe weather"
       )

       # Listen for task completion events with specific conditions
       sm.add_transition(
           state='executing',
           next_status='completed',
           event_trigger={
               'source_id': self.owner.id,
               'event_name': 'task_completed',
               'value_key': 'task_type',
               'operator': TriggerOperator.EQUALS,
               'target_value': 'inspection'
           },
           description="Inspection task completed successfully"
       )

Best Practices
~~~~~~~~~~~~~~

1. **Clear States**: Define clear, meaningful state names
2. **Robust Triggers**: Use appropriate trigger types for different conditions
3. **Error Handling**: Include error states and recovery transitions
4. **Property Validation**: Use property templates for configuration validation
5. **Event Integration**: Leverage events for external condition monitoring
6. **Documentation**: Provide clear descriptions for states and transitions

Example: Multi-Stage Inspection Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class MultiStageInspectionWorkflow(Workflow):
       """Workflow for multi-stage inspection missions."""
       
       def __init__(self, env, name, owner, **kwargs):
           super().__init__(env, name, owner, **kwargs)
           
           # Register properties
           self.register_property_template('inspection_points', value_type=list, required=True)
           self.register_property_template('inspection_altitude', value_type=float, required=False, default=100.0)
           self.register_property_template('inspection_duration', value_type=float, required=False, default=60.0)
           
           self.current_point_index = 0
       
       def _setup_transitions(self):
           """Setup inspection workflow transitions."""
           sm = self.status_machine

           # Set initial state
           sm.set_start_transition('moving_to_point')

           # Arrive at inspection point
           sm.add_transition(
               state='moving_to_point',
               next_status='inspecting',
               agent_state={
                   'agent_id': self.owner.id,
                   'state_key': 'position',
                   'operator': TriggerOperator.CUSTOM,
                   'target_value': self._at_current_point
               },
               description="Arrived at inspection point"
           )

           # Complete inspection at current point
           sm.add_transition(
               state='inspecting',
               next_status='moving_to_point',
               event_trigger={
                   'source_id': self.owner.id,
                   'event_name': 'task_completed',
                   'value_key': 'task_name',
                   'operator': TriggerOperator.EQUALS,
                   'target_value': 'InspectionTask'
               },
               callback=self._advance_to_next_point,
               description="Inspection completed, move to next point"
           )

           # All points completed
           sm.add_transition(
               state='inspecting',
               next_status='completed',
               event_trigger={
                   'source_id': self.owner.id,
                   'event_name': 'task_completed',
                   'operator': TriggerOperator.CUSTOM,
                   'target_value': self._all_points_inspected
               },
               description="All inspection points completed"
           )
       
       def _advance_to_next_point(self, context):
           """Advance to next inspection point."""
           self.current_point_index += 1

       def _at_current_point(self, position):
           """Check if at current inspection point."""
           points = self.properties['inspection_points']
           if self.current_point_index < len(points):
               target = points[self.current_point_index]
               distance = self._calculate_distance(position[:2], target)
               return distance < 10.0
           return False

       def _all_points_inspected(self, event_data):
           """Check if all inspection points completed."""
           return self.current_point_index >= len(self.properties['inspection_points'])

       def _calculate_distance(self, pos1, pos2):
           """Calculate distance between two points."""
           return ((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)**0.5

       def get_current_suggested_task(self):
           """Get current suggested task based on state."""
           current_state = self.status_machine.current_status

           if current_state == 'moving_to_point':
               points = self.properties['inspection_points']
               if self.current_point_index < len(points):
                   point = points[self.current_point_index]
                   return {
                       'task_class': 'MoveToTask',
                       'params': {
                           'target_position': point + [self.properties['inspection_altitude']],
                           'precision': 5.0
                       }
                   }
           elif current_state == 'inspecting':
               return {
                   'task_class': 'InspectionTask',
                   'params': {'duration': self.properties['inspection_duration']}
               }

           return None

For more workflow examples and patterns, see the workflow development guide.
