Agent Classes
=============

This section documents all agent implementations in AirFogSim. Agents are autonomous entities that make decisions and interact with the simulation environment.

Base Agent
----------

.. autoclass:: airfogsim.core.agent.Agent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Drone Agents
------------

DroneAgent
~~~~~~~~~~

.. autoclass:: airfogsim.agent.drone.DroneAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DeliveryDroneAgent
~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.delivery_drone.DeliveryDroneAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DeliveryAgent
~~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.delivery_agent.DeliveryAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Ground Agents
-------------

TerminalAgent
~~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.terminal.TerminalAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DeliveryStation
~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.delivery_station.DeliveryStation
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

InspectionStation
~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.inspection_station.InspectionStation
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Specialized Agents
------------------

SensingAgent
~~~~~~~~~~~~

.. autoclass:: airfogsim.agent.sensing_agent.SensingAgent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Agent Development Guide
-----------------------

Creating Custom Agents
~~~~~~~~~~~~~~~~~~~~~~~

To create a custom agent, inherit from the base Agent class and implement the required methods:

.. code-block:: python

   from airfogsim.core.agent import Agent
   from airfogsim.core.enums import AgentStatus
   
   class CustomAgent(Agent):
       """Custom agent implementation."""
       
       def __init__(self, env, agent_name, **kwargs):
           super().__init__(env, agent_name, **kwargs)
           
           # Register custom state templates
           self.register_state_template('custom_property', 
                                       value_type=float, 
                                       required=True)
           
           # Initialize states
           self.initialize_states(
               status='idle',
               position=[0, 0, 0],
               custom_property=0.0
           )
           
           # Add components
           self._setup_components()
       
       def _setup_components(self):
           """Setup agent-specific components."""
           # Add required components
           pass
       
       def _process_custom_logic(self):
           """Implement agent-specific behavior."""
           # Custom decision logic
           pass

State Management
~~~~~~~~~~~~~~~~

Agents maintain state through a structured system:

.. code-block:: python

   # Register state templates (typically in __init__)
   self.register_state_template('battery_level', 
                               value_type=float, 
                               required=True,
                               validator=lambda x: 0 <= x <= 100)
   
   # Initialize states
   self.initialize_states(battery_level=100.0)
   
   # Update states (triggers events)
   self.update_state('battery_level', 85.0)
   
   # Get states (supports composite keys)
   battery = self.get_state('battery_level')
   station_status = self.get_state('charging_station.status')

Component Integration
~~~~~~~~~~~~~~~~~~~~~

Agents gain capabilities through components:

.. code-block:: python

   from airfogsim.component import MoveToComponent, ChargingComponent
   
   # Add components in __init__ or _setup_components
   self.add_component(MoveToComponent(env, self))
   self.add_component(ChargingComponent(env, self))

Event Handling
~~~~~~~~~~~~~~

Agents communicate through events:

.. code-block:: python

   def register_event_listeners(self):
       """Register custom event listeners."""
       listeners = super().register_event_listeners()
       
       # Add custom listeners
       listeners.extend([
           {
               'source_id': 'environment',
               'event_name': 'weather_changed',
               'callback': self._handle_weather_change
           }
       ])
       
       return listeners
   
   def _handle_weather_change(self, event_data):
       """Handle weather change events."""
       # React to weather changes
       pass

Workflow Integration
~~~~~~~~~~~~~~~~~~~~

Agents can be assigned workflows for high-level coordination:

.. code-block:: python

   def _process_custom_logic(self):
       """Process workflow suggestions and make decisions."""
       # Get active workflows
       workflows = self.get_active_workflows()
       
       for workflow in workflows:
           # Get suggested task from workflow
           suggested_task = workflow.get_current_suggested_task()
           
           if suggested_task and self._should_execute_task(suggested_task):
               # Execute the suggested task
               self.execute_task(suggested_task['task_class'], 
                               **suggested_task.get('params', {}))

Best Practices
~~~~~~~~~~~~~~

1. **State Templates**: Always register state templates for validation and documentation
2. **Component Composition**: Use components for reusable capabilities
3. **Event-Driven**: Leverage events for loose coupling and reactivity
4. **Hook Methods**: Override hook methods rather than the main `live()` method

For more detailed examples, see the agent development guide in the main documentation.
